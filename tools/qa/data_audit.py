"""Read-only inventory audit. No source/network requests, cloud or inventory writes."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'qa-results'
OUT.mkdir(exist_ok=True)
def read(name, default=None):
    path=ROOT/name
    return json.loads(path.read_text()) if path.exists() else default
def number(value):
    if value is None or isinstance(value,bool): return None
    try:
        result=float(str(value).replace(',','').replace('$','').strip())
        return result if math.isfinite(result) else None
    except (ValueError,TypeError): return None
rows=read('properties.json',[])
sheriff=read('sheriff_listings.json',[])
checks={}
date_formats=Counter()
checks['properties_is_array']=isinstance(rows,list)
checks['sheriff_is_array']=isinstance(sheriff,list)
if not all(checks.values()): raise SystemExit('Inventory must contain arrays')
all_rows=rows+sheriff
valid=[r for r in all_rows if isinstance(r,dict)]
ids=defaultdict(list)
for r in valid: ids[str(r.get('id') or '')].append(r)
issues=defaultdict(list)
for row in valid:
    identifier=str(row.get('id') or '')
    if not identifier: issues['missing_id'].append(identifier)
    for field in ('price','beds','baths','sqft'):
        n=number(row.get(field))
        if n is not None and n<0: issues['negative_'+field].append(identifier)
    url=row.get('url')
    if url and urlsplit(str(url)).scheme.lower() not in {'http','https'}:
        issues['unsafe_inventory_url'].append(identifier)
    if '<' in str(row.get('baths','')) or '<' in str(row.get('beds','')):
        issues['markup_in_room_fields'].append(identifier)
    if row.get('source_type')=='mls':
        if number(row.get('sqft')) is None or number(row.get('sqft'))<=0: issues['mls_missing_living_area'].append(identifier)
        if number(row.get('beds')) is None or number(row.get('beds'))<=0: issues['mls_missing_beds'].append(identifier)
    if not str(row.get('address') or '').strip(): issues['missing_address'].append(identifier)
    text=str(row.get('listed_date') or '')
    if text:
        try:
            if '/' in text:
                day=datetime.strptime(text,'%d/%m/%Y').date()
                date_formats['day/month/year']+=1
            else:
                day=datetime.fromisoformat(text.replace('Z','+00:00')).date()
                date_formats['iso']+=1
            if day>datetime.now(timezone.utc).date(): issues['future_listing_date'].append(identifier)
        except ValueError: issues['invalid_listing_date'].append(identifier)
photos=[]
for path in (ROOT/'COMPS_REPORTS/listing_photos').glob('*.json'):
    record=json.loads(path.read_text())
    if record.get('property_id'):
        photos.append({'property_id':record['property_id'],'status':record.get('status'),
                       'verified_files':sum(p.get('verification',{}).get('status')=='image_verified' for p in record.get('photos',[]))})
status=read('COMPS_REPORTS/automatic_listing_sources/status.json',{})
profile=read('scan_config.json',{})
report={
 'checked_at':datetime.now(timezone.utc).isoformat(),
 'source_sha256':hashlib.sha256((ROOT/'properties.json').read_bytes()).hexdigest(),
 'properties_bytes':(ROOT/'properties.json').stat().st_size,
 'inventory_rows':len(rows),'sheriff_rows':len(sheriff),'non_object_rows':len(all_rows)-len(valid),
 'sources':dict(Counter(str(r.get('source_type') or 'unknown') for r in valid)),
 'counties':dict(Counter(str(r.get('county') or 'unknown') for r in valid)),
 'duplicate_id_groups':sum(len(v)>1 for v in ids.values()),
 'duplicate_id_rows':sum(len(v) for v in ids.values() if len(v)>1),
 'issues':{k:{'count':len(v),'example_ids':v[:5]} for k,v in issues.items()},
 'photo_receipts':photos,
 'listing_date_formats':dict(date_formats),
 'completion':{k:status.get(k) for k in ('version','checked_at','status','eligible_properties','pending','photo_pending','review_required','new_rentcast_calls')},
 'completion_settings':profile.get('completionSettings'),
 'checks':checks,
 'scope':'Saved public repository data only; missing fields and duplicate IDs require interpretation, not automatic deletion.'
}
(OUT/'data-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('QA_DATA='+json.dumps(report,ensure_ascii=False))
