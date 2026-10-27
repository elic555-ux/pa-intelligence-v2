"""Sourced renovation unit rates; images never provide dollars by themselves."""
from __future__ import annotations
import copy,json,math,re
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit

PATH=Path(__file__).with_name('rehab_pricebook.json')
UNITS={'floor_sqft','surface_sqft','roof_sqft','item','system'}

def numeric(x):return type(x) in (int,float) and math.isfinite(x) and x>0

def catalog(path=PATH,today=None):
    today=today or datetime.now(timezone.utc).date()
    raw=json.loads(Path(path).read_text(encoding='utf-8'))
    seen=set()
    if raw.get('currency')!='USD' or raw.get('geography')!='US_national' or raw.get('local_adjustment_applied') is not False:
        raise ValueError('unsupported_catalog_basis')
    observed=date.fromisoformat(raw['observed_at']);due=date.fromisoformat(raw['review_due_at'])
    if observed>today or due<=observed or (due-observed).days>90:raise ValueError('invalid_catalog_dates')
    for row in raw.get('entries',[]):
        u=urlsplit(row['source_url'])
        checked=date.fromisoformat(row.get('observed_at',raw['observed_at']))
        if checked>today or checked>=due:raise ValueError('invalid_entry_observation_date')
        if (row['code'] in seen or row['unit'] not in UNITS or not numeric(row['low']) or not numeric(row['high']) or row['high']<row['low']
            or u.scheme!='https' or u.username or u.password or u.hostname not in {'www.homewyse.com','homewyse.com','www.homedepot.com'}):
            raise ValueError('invalid_pricebook_entry')
        seen.add(row['code'])
    return {**raw,'status':'current' if today<due else 'review_due'}

def attach(analysis,p,book=None):
    """Price only explicit documented measurements/counts, never absent work.

    The separate visual analysis may identify a task even when its physical
    quantity is unknown. Such a task receives the reference rate and a missing
    quantity reason, with no fabricated project total.
    """
    result=copy.deepcopy(analysis);book=book or catalog()
    result['pricebook']=book
    if result.get('status') not in {'available','partial'}:return result
    entries={x['code']:x for x in book['entries']};priced=[];unpriced=[]
    for row in result.get('items',[]):
        row.update(cost_low=None,cost_high=None)
        rate=entries.get(row.get('task_code'))
        if not rate:row['pricing_reason']='task_not_in_pricebook';unpriced.append(row);continue
        row.update(unit_low=rate['low'],unit_high=rate['high'],unit=rate['unit'],
            price_source=rate['source_name'],price_source_url=rate['source_url'],price_observed_at=rate.get('observed_at',book['observed_at']),
            price_geography=book['geography'],price_scope=rate['scope'],price_exclusions=rate['exclusions'])
        if book['status']!='current':row['pricing_reason']='pricebook_review_due';unpriced.append(row);continue
        if rate.get('reference_only'):row['pricing_reason']='technical_scope_not_confirmed';unpriced.append(row);continue
        if row.get('status')=='not_visible':row['pricing_reason']='not_visible';unpriced.append(row);continue
        quantity=row.get('quantity_evidence') or {}
        if not isinstance(quantity,dict):quantity={}
        value=quantity.get('value');source=quantity.get('source_url') or ''
        try:u=urlsplit(source)
        except ValueError:u=urlsplit('')
        try:
            observed=date.fromisoformat(str(quantity.get('observed_at',''))[:10])
            age=(datetime.now(timezone.utc).date()-observed).days
        except ValueError:age=-1
        if (not numeric(value) or not 0<=age<=90 or quantity.get('unit')!=rate['unit'] or quantity.get('property_id')!=str(p['id'])
            or quantity.get('status')!='documented_measurement' or u.scheme!='https' or u.username or u.password
            or not quantity.get('source_name') or not quantity.get('observed_at')):
            row['pricing_reason']='quantity_not_documented';unpriced.append(row);continue
        if rate['unit'] in {'item','system'} and value!=int(value):
            row['pricing_reason']='fractional_item_count';unpriced.append(row);continue
        row.update(quantity=value,cost_low=round(value*rate['low'],2),cost_high=round(value*rate['high'],2),pricing_reason='documented_quantity_times_reference_rate')
        priced.append(row)
    result.update(priced_items_count=len(priced),unpriced_items_count=len(unpriced),
        total_low=round(sum(x['cost_low'] for x in priced),2) if priced else None,
        total_high=round(sum(x['cost_high'] for x in priced),2) if priced else None,
        complete=False,status='partial',reason='photo_scope_and_reference_prices_only')
    # A sum of visible work is never presented as a full inspection or whole-home budget.
    return result


def refresh_saved(property_id,repo=Path('.'),book=None):
    """Refresh a matched saved report from the local catalog, without providers.

    Source observation dates, rent receipts, taxes and photo assessments are
    retained. No photo inference, external source collection or API key is used.
    """
    import internal_report_store as store
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(property_id)):
        raise ValueError('invalid_property_id')
    repo=Path(repo);inventory=store.read(repo/'properties.json')
    rows=[x for x in inventory if isinstance(x,dict) and str(x.get('id'))==str(property_id)] if isinstance(inventory,list) else []
    if len(rows)!=1:raise ValueError('property_identity_not_unique')
    p=rows[0];path=repo/'COMPS_REPORTS'/'expanded_reports'/(store.key(property_id)+'.json')
    receipt=store.read(path)
    if not isinstance(receipt,dict):raise ValueError('report_missing')
    analysis=receipt.get('report_analysis');rehab=analysis.get('rehab') if isinstance(analysis,dict) else None
    if (receipt.get('property_id')!=str(property_id) or not store.same_address(p,receipt.get('property') or {})
        or not isinstance(analysis,dict) or analysis.get('property_id')!=str(property_id)
        or not store.same_address(p,analysis.get('property') or {})
        or not isinstance(rehab,dict) or rehab.get('property_id')!=str(property_id)):
        raise ValueError('report_subject_mismatch')
    updated=attach(rehab,p,book)
    changed=updated!=rehab
    if changed:
        receipt=copy.deepcopy(receipt);receipt['report_analysis']['rehab']=updated
        receipt['last_pricebook_enrichment_at']=store.stamp();store.write(path,receipt)
    return {'property_id':str(property_id),'changed':changed,'pricebook_version':updated['pricebook']['version'],
        'reference_count':len(updated['pricebook']['entries']),'provider_calls':0,'renovation_budget_complete':False}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Refresh a saved renovation pricebook without provider calls')
    parser.add_argument('--property-id',required=True)
    args=parser.parse_args()
    print(json.dumps(refresh_saved(args.property_id),ensure_ascii=True))
