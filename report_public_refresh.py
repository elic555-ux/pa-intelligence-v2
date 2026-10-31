"""Refresh official context for known properties without paid estimates or AI."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
import closed_sales
import internal_report_store as store
import report_area
import report_public_context

def key(value): return hashlib.sha256(str(value).encode()).hexdigest()[:32]
def read(path):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): return None

def refresh(root=Path('COMPS_REPORTS'), property_ids=None):
    root=Path(root); records=read(root.parent/'properties.json')
    if not isinstance(records,list): raise ValueError('Inventory unavailable')
    ids=list(property_ids or [])
    if not ids:
        for path in sorted((root/'public_context').glob('*.json')):
            record=read(path)
            if isinstance(record,dict) and record.get('property_id'): ids.append(str(record['property_id']))
        for path in sorted((root/'expanded_reports').glob('*.json')):
            record=read(path)
            if isinstance(record,dict) and record.get('property_id'): ids.append(str(record['property_id']))
    ids=list(dict.fromkeys(ids))
    if len(ids)>100: raise ValueError('Refresh limited to 100 known properties')
    out=[]
    for property_id in ids:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',property_id): raise ValueError('Invalid property id')
        matches=[r for r in records if str(r.get('id'))==property_id]
        if len(matches)!=1:
            out.append({'property_id':property_id,'status':'ambiguous_or_missing_inventory'});continue
        p=matches[0]
        profile=store.profile(p,root,allow_public=True)
        county=profile.get('county_record') or {}
        if county.get('status')!='matched' or not store.same_address(p,county.get('subject',{})):
            out.append({'property_id':property_id,'status':'county_identity_unavailable'});continue
        try: point=closed_sales.parcel_point(county.get('parcel_id')) or {}
        except (OSError, ValueError, TypeError): point={}
        area=report_area.collect(point,root)
        context=report_public_context.collect(p,county,point,root,allow_public=True)
        context['county_record']=county
        target=root/'public_context'/(key(property_id)+'.json');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(context,ensure_ascii=False,indent=2),encoding='utf-8')
        # Area snapshots are written by report_area. Never rewrite historical reports.
        out.append({'property_id':property_id,'census':area.get('status'),
            'education':context.get('education',{}).get('status'),
            'crime':context.get('crime',{}).get('status')})
    return {'census_key_configured':bool(report_area.os.environ.get('CENSUS_API_KEY','').strip()),'properties':out}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--property-id',action='append');parser.add_argument('--request-file',type=Path)
    args=parser.parse_args();ids=args.property_id
    if args.request_file:
        request=read(args.request_file)
        if not isinstance(request,dict) or not isinstance(request.get('property_ids'),list) or len(request['property_ids'])>10:
            raise ValueError('Invalid public refresh request')
        ids=request['property_ids']
    print(json.dumps(refresh(property_ids=ids),ensure_ascii=False))
