"""Recorded Allegheny sales, joined to parcel geometry and building facts.

No listing prices or AVM comparables enter this module. Pending county review
is retained as a status, never treated as an arm's-length validation.
"""
from __future__ import annotations
import json,math,re,statistics
from datetime import datetime,timezone,timedelta,date
from urllib.parse import urlencode
import internal_report_store as store

SALES='5bbe6c55-bce6-4edb-9d04-68edeb6bf7b1'
ANNUAL_SALES={2026:'0bd13902-f2ee-4340-b7c8-c54ebd0f6bd0'}
GEO='3fab7152-3f11-4788-8372-4c33f86ea813'
ASSESS='65855e14-549e-4992-b5be-d629afc676fa'
SOURCE='https://data.wprdc.org/dataset/real-estate-sales'
GEO_SOURCE='https://data.wprdc.org/dataset/parcel-centroids-in-allegheny-county-with-geographic-identifiers'
RADIUS_MILES=1.0
LOOKBACK_DAYS=180
MAX_ROWS=500

def clock():return datetime.now(timezone.utc)
def finite(v):
    if isinstance(v,bool):return None
    try:
        n=float(v);return n if math.isfinite(n) else None
    except (TypeError,ValueError):return None
def distance(lat,lng,other_lat,other_lng):
    points=[finite(v) for v in (lat,lng,other_lat,other_lng)]
    if any(v is None for v in points):return None
    a,b,c,d=map(math.radians,points)
    x=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 3958.7613*2*math.atan2(math.sqrt(x),math.sqrt(max(0,1-x)))
def get_json(action,params):
    result=json.loads(store.public_get('https://data.wprdc.org/api/3/action/'+action+'?'+urlencode(params)))
    if result.get('success') is not True:raise ValueError('County API rejected query')
    return result.get('result',{})

def parcel_point(parcel):
    if not re.fullmatch(r'[A-Z0-9]{16}',str(parcel)):return None
    rows=get_json('datastore_search',{'resource_id':GEO,'filters':json.dumps({'PIN':parcel}),'limit':2}).get('records',[])
    if len(rows)!=1 or rows[0].get('PIN')!=parcel:return None
    r=rows[0];lat,lng=finite(r.get('LAT')),finite(r.get('LONG'))
    if lat is None or lng is None or not (39<lat<43 and -81<lng<-74):return None
    return {'parcel_id':parcel,'lat':lat,'lng':lng,'neighborhood':r.get('CITY_NEIGHBORHOOD'),
            'tract':str(r.get('FIPS_TRACT') or '').zfill(6),'geoid':str(r.get('GEOID') or ''),
            'municipality':r.get('MUNI_LABEL'),'source_url':GEO_SOURCE,'source_as_of':'2025-03'}

def use_kind(value):
    s=str(value or '').upper()
    if s in {'SINGLE FAMILY','ROWHOUSE','TOWNHOUSE'}:return 'Single Family'
    if 'TWO FAMILY' in s or 'THREE FAMILY' in s or 'FOUR FAMILY' in s:return 'Multi-Family'
    if 'CONDO' in s:return 'Condo'
    return None

def normalize_rows(rows,p,point,today=None):
    today=today or clock().date();earliest=today-timedelta(days=LOOKBACK_DAYS)
    sqft=finite(p.get('sqft'));beds=finite(p.get('beds'));kind=p.get('property_type')
    if kind=='Townhouse':kind='Single Family'
    accepted=[];rejected={};seen=set()
    def reject(reason):rejected[reason]=rejected.get(reason,0)+1
    for r in rows:
        try:day=date.fromisoformat(str(r.get('SALEDATE'))[:10])
        except ValueError:reject('invalid_sale_date');continue
        price=finite(r.get('PRICE'));area=finite(r.get('FINISHEDLIVINGAREA'))
        d=distance(point['lat'],point['lng'],r.get('LAT'),r.get('LONG'))
        if not earliest<=day<=today:reject('outside_dates');continue
        if str(r.get('PARID'))==point['parcel_id']:reject('subject_itself');continue
        if not price or price<1000:reject('invalid_price');continue
        if d is None or d>RADIUS_MILES:reject('outside_radius');continue
        if use_kind(r.get('USEDESC'))!=kind:reject('different_property_type');continue
        if not sqft or not area or not .75<=area/sqft<=1.25:reject('different_or_missing_size');continue
        other_beds=finite(r.get('BEDROOMS'))
        if beds is not None and (other_beds is None or abs(other_beds-beds)>1):reject('different_or_missing_beds');continue
        code=str(r.get('SALECODE') or '').strip();desc=str(r.get('SALEDESC') or '').strip()
        validity='county_validated' if code in {'0','U','UR'} and desc in {'VALID SALE','OTHER VALID'} else 'pending_county_review' if code=='AA' and desc=='SALE NOT ANALYZED' else None
        if not validity:reject('non_market_or_unknown_transfer');continue
        ref=(str(r.get('PARID')),str(r.get('DEEDBOOK')),str(r.get('DEEDPAGE')),day.isoformat())
        if ref in seen:reject('duplicate');continue
        seen.add(ref)
        baths,halves=finite(r.get('FULLBATHS')),finite(r.get('HALFBATHS'))
        accepted.append({'kind':'recorded_sale','address':r.get('FULL_ADDRESS'),'parcel_id':r.get('PARID'),
            'amount':price,'sale_date':day.isoformat(),'recorded_date':str(r.get('RECORDDATE') or '')[:10],
            'sqft':area,'beds':other_beds,'baths':baths+halves/2 if baths is not None and halves is not None else None,
            'year_built':finite(r.get('YEARBLT')),'county_property_use':r.get('USEDESC'),
            'distance_miles':round(d,3),'neighborhood':r.get('CITY_NEIGHBORHOOD'),
            'same_named_neighborhood':bool(point.get('neighborhood') and r.get('CITY_NEIGHBORHOOD')==point['neighborhood']),
            'neighborhood_class':None,'neighborhood_class_match':None,'validation':validity,
            'sale_code':code,'sale_description':desc,'deed_book':r.get('DEEDBOOK'),'deed_page':r.get('DEEDPAGE'),
            'price_per_sqft':round(price/area,2),'source_name':'Allegheny County / WPRDC','source_url':SOURCE})
    accepted.sort(key=lambda r:(not r['same_named_neighborhood'],r['distance_miles'],-date.fromisoformat(r['sale_date']).toordinal()))
    return accepted[:20],rejected

def collect(p,county_record):
    result={'version':'recorded-sales-v1','property_id':str(p.get('id')),'status':'unavailable',
        'source_name':'Allegheny County / WPRDC','source_url':SOURCE,'observed_at':clock().isoformat(timespec='seconds'),
        'policy':{'radius_miles':RADIUS_MILES,'lookback_days':LOOKBACK_DAYS,'size_ratio':[.75,1.25],'bed_difference':1,'minimum_recorded_price':1000},
        'comparables':[],'market_estimate':None,'valuation_is_after_repair_evidence':False}
    if county_record.get('county')!='Allegheny' or county_record.get('status')!='matched':
        result['reason']='county_or_identity_not_supported';return result
    point=parcel_point(county_record.get('parcel_id'))
    if not point:result['reason']='parcel_coordinates_missing';return result
    result['subject_location']=point
    lat,lng=point['lat'],point['lng'];lat_delta=RADIUS_MILES/69;lng_delta=RADIUS_MILES/(69*math.cos(math.radians(lat)))
    first=(clock().date()-timedelta(days=LOOKBACK_DAYS)).isoformat();last=clock().date().isoformat()
    columns=['PARID','FULL_ADDRESS','SALEDATE','RECORDDATE','PRICE','SALECODE','SALEDESC','DEEDBOOK','DEEDPAGE']
    facts=['FINISHEDLIVINGAREA','BEDROOMS','FULLBATHS','HALFBATHS','YEARBLT','USEDESC']
    sql='SELECT '+','.join('s."'+x+'"' for x in columns)+','+','.join('a."'+x+'"' for x in facts)+',g."LAT",g."LONG",g."CITY_NEIGHBORHOOD" '
    resource=ANNUAL_SALES.get(clock().year,SALES) if first[:4]==last[:4] else SALES
    sql+=f'FROM "{resource}" s JOIN "{GEO}" g ON g."PIN"=s."PARID" JOIN "{ASSESS}" a ON a."PARID"=s."PARID" '
    sql+=f'WHERE g."LAT" BETWEEN {lat-lat_delta:.7f} AND {lat+lat_delta:.7f} AND g."LONG" BETWEEN {lng-lng_delta:.7f} AND {lng+lng_delta:.7f} '
    sql+=f'AND s."SALEDATE">=\'{first}\' AND s."SALEDATE"<=\'{last}\' AND a."CLASS"=\'R\' '
    sql+=f'ORDER BY s."SALEDATE" DESC LIMIT {MAX_ROWS}'
    data=get_json('datastore_search_sql',{'sql':sql});raw=data.get('records',[])
    rows,rejected=normalize_rows(raw,p,point)
    valid=[r for r in rows if r['validation']=='county_validated']
    result.update(status='available' if rows else 'no_matching_sales',comparables=rows,rejected=rejected,
        returned_rows=len(raw),query_limit_reached=len(raw)>=MAX_ROWS,county_validated_count=len(valid),
        pending_county_review_count=len(rows)-len(valid))
    # A sale alone never establishes renovated condition or ARV.
    if len(valid)>=3:
        prices=[r['price_per_sqft'] for r in valid]
        result['market_estimate']={'value':round(statistics.median(prices)*float(p['sqft'])),
            'basis':'median_validated_closed_sale_price_per_sqft','count':len(valid),'is_arv':False}
    return result
