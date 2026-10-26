"""Bounded public report gallery, independent of paid APIs and photo inference."""
from __future__ import annotations
import argparse,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import property_sources as registry
import secondary_listing_source as core
import tarasa_listing_source as source

VERSION='report-gallery-1'
MAX_PHOTOS=60
CACHE_DAYS=7

def read(path):
    try:
        value=registry.read_json(path)
        return value if isinstance(value,dict) else None
    except (OSError,ValueError,TypeError):return None

def collect(p,repo=Path('.'),allow_public=True,reader=None,now=None):
    repo=Path(repo);now=now or datetime.now(timezone.utc)
    result={'version':VERSION,'property_id':str(p.get('id','')),'identity':core.identity4(p),
        'observed_at':now.isoformat(),'status':'not_collected','photo_count':0,
        'source_network_requests':0,'rentcast_calls':0,'gemini_calls':0}
    if not allow_public:return result|{'status':'lookup_disabled'}
    try:
        row=core.eligible(repo,str(p['id']),include_review=True)
        if (core.identity4(row)!=core.identity4(p) or (p.get('county') and
                registry.identity(p)['county']!=registry.identity(row)['county'])):raise ValueError('subject_mismatch')
        if registry.identity(row)['county']!='Allegheny':
            return result|{'status':'unsupported_county'}
    except (ValueError,OSError,KeyError):
        return result|{'status':'subject_mismatch'}
    key=source.cache_key(row['id'])
    path=repo/source.ROOT/(key+'.json')
    receipt_path=repo/'COMPS_REPORTS/report_galleries'/(key+'.json')
    old=read(path);old=old if source.bound(row,old) else None
    prior=read(receipt_path) or {}
    date=source.parse_date((old or {}).get('retrieved_at'))
    if (old and old.get('report_gallery_version')==VERSION and date and
            timedelta(0)<=now-date<timedelta(days=CACHE_DAYS)):
        return result|{'status':'cache_used','photo_count':len(old.get('photos',[])),
            'source_url':old['source_url'],'observed_at':old['retrieved_at']}
    state=read(repo/source.SOURCE_STATE) or {}
    same=prior.get('property_id')==str(row['id']) and prior.get('identity')==core.identity4(row)
    deadlines=[source.parse_date(state.get('retry_after_at')),
        source.parse_date((old or {}).get('last_attempt',{}).get('retry_after_at')),
        source.parse_date(prior.get('retry_after_at')) if same else None]
    until=max((d for d in deadlines if d),default=None)
    if until and until>now:return result|{'status':'source_cooldown','retry_after_at':until.isoformat()}
    reader=reader or source.PublicReader(max_requests=2)
    before=reader.requests
    url=(old or {}).get('source_url') or source.target_url(row)
    try:
        reader.initialize()
        html=reader.get(url)
        record=source.parse_detail(html,row,url,now.isoformat(),photo_limit=MAX_PHOTOS)
        if not record.get('photos'):raise source.StopSource('no_source_photos')
        record['report_gallery_version']=VERSION
        registry.atomic_json(path,record)
        result.update(status='published',photo_count=len(record['photos']),source_url=record['source_url'])
    except (source.StopSource,ValueError) as error:
        status=error.status if isinstance(error,source.StopSource) else 'source_identity_mismatch'
        retry=source.cooldown_until(getattr(error,'retry_after',None),now)
        result.update(status=status,retry_after_at=retry,http_status=getattr(error,'http_status',None))
        # Keep successful facts/photos and their original observation date intact.
        if status in source.GLOBAL_BLOCKS:
            registry.atomic_json(repo/source.SOURCE_STATE,{'provider':'tarasa','status':status,
                'checked_at':now.isoformat(),'retry_after_at':retry,
                'http_status':getattr(error,'http_status',None)})
    result['source_network_requests']=reader.requests-before
    registry.atomic_json(receipt_path,result)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--property-id',required=True)
    args=parser.parse_args()
    row=core.eligible(Path('.'),args.property_id,include_review=True)
    result=collect(row)
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status'] in {'published','cache_used'} else 1)

if __name__=='__main__':main()
