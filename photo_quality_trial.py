"""One real, checksum-bound source-image trial; never update the live report.

The provider is real. Only the collector's image intake is supplied with bytes
already fetched and verified by the normal image validator. No expected answer
or human-review wording is sent to the provider.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import internal_report_store as store
import report_gemini as gemini
from report_provider_reservation import persist_gemini_reservation

PROPERTY_ID='PA-MLS-1779799'
IMAGE_SHA='b9602aa3dc1454106fe18534cfaba474da0ab47dcc48edcc6ed5c4a5107a83e9'
IMAGE_URL='https://api.cotality.com/trestle/Media/Property/PHOTO-Jpeg/1193396285/1/NTEyNy8zOTYyLzIw/MjAvMTAwMzYvMTc5MjQxMzk3Nw/Z3QsojgQVqBf6CizHidgCApv5qh6j1sZ8PqEewKz6t4'


def evaluate(result):
    rows=result.get('exterior_assessments') or []
    by_component={r['component']:r for r in rows}
    checks={
        'one_real_image':result.get('photo_count')==1 and len(result.get('photos') or [])==1 and result['photos'][0].get('sha256')==IMAGE_SHA,
        'photo_assessed':len(result.get('photo_coverage') or [])==1,
        'six_exterior_components':len(rows)==6 and set(by_component)==set(gemini.EXTERIOR_COMPONENTS),
        'facade_damage_identified':by_component.get('exterior_walls',{}).get('condition')=='visible_damage',
        'roof_not_certified':by_component.get('roof_surface',{}).get('condition')=='not_assessable',
        'no_invented_interior':result.get('interior_assessments')==[],
        'no_invented_budget':result.get('complete') is False and result.get('total_low') is None and result.get('total_high') is None,
    }
    return {'passed':all(checks.values()),'checks':checks}


def saved_image(path):
    body=Path(path).read_bytes()
    if len(body)>gemini.MAX_IMAGE_BYTES or not body.startswith(b'\xff\xd8\xff') or hashlib.sha256(body).hexdigest()!=IMAGE_SHA:
        raise ValueError('saved_image_changed_since_verification')
    return 'image/jpeg',body,IMAGE_SHA


def run(repo,trigger,persist=False,image=None):
    repo=Path(repo)
    if trigger.get('free_tier_confirmed') is not True or trigger.get('property_id')!=PROPERTY_ID or trigger.get('image_sha256')!=IMAGE_SHA:
        raise ValueError('trial_not_authorized_or_source_changed')
    if os.environ.get('GEMINI_REPORT_FREE_TIER_ENABLED')!='true':
        raise ValueError('free_tier_not_confirmed')
    properties=store.read(repo/'properties.json')
    matches=[p for p in properties if str(p.get('id'))==PROPERTY_ID]
    if len(matches)!=1:raise ValueError('property_identity_not_unique')
    subject=matches[0]
    verified=gemini.verified_inputs(subject,repo)
    candidates=[p for p in verified['photos'] if p.get('url')==IMAGE_URL]
    if len(candidates)!=1:raise ValueError('trial_image_not_in_verified_sources')
    source=dict(candidates[0],verification={'sha256':IMAGE_SHA})
    # A saved, independently reviewed copy can be supplied explicitly. Reusing
    # verified existing bytes makes no request to either blocked image host.
    prepared=saved_image(image) if image is not None else gemini.fetch_image(source)
    inputs=dict(verified,photos=[source],available_photo_count=1)
    before_send=(lambda:persist_gemini_reservation(repo)) if persist else None
    with patch.object(gemini,'verified_inputs',return_value=inputs),patch.object(gemini,'fetch_image',return_value=prepared):
        result=gemini.collect(subject,repo/'COMPS_REPORTS',before_send=before_send)
    outcome=evaluate(result)
    summary={'property_id':PROPERTY_ID,'checked_at':store.stamp(),'model':gemini.MODEL,
        'version':gemini.VERSION,'image_sha256':IMAGE_SHA,'trial_id':trigger['trial_id'],
        'status':result.get('status'),'http_status':result.get('http_status'),
        'error_code':result.get('error_code'),'reason':result.get('reason'),
        'provider_hint':result.get('provider_hint'),'response_seconds':result.get('response_seconds'),
        'text_validation_diagnostics':result.get('text_validation_diagnostics'),
        'image_intake':'saved_verified_bytes' if image is not None else 'verified_source_fetch',
        'evaluation':outcome,'live_report_updated':False,'rentcast_calls':0}
    target=repo/'COMPS_REPORTS/gemini_findings/quality_trials'/trigger['trial_id']
    store.write(target.with_suffix('.json'),{'summary':summary,'result':result})
    if persist:persist_gemini_reservation(repo,'Record explicit facade photo quality trial')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--trigger',type=Path,required=True)
    parser.add_argument('--image',type=Path)
    args=parser.parse_args()
    trigger=json.loads(args.trigger.read_text(encoding='utf-8'))
    # Never permit a trigger to select an output path outside the trial folder.
    import re
    if not re.fullmatch(r'[a-z0-9-]{1,80}',trigger.get('trial_id','')):raise ValueError('invalid_trial_id')
    summary=run(args.repo,trigger,persist=os.environ.get('GITHUB_ACTIONS')=='true',image=args.image)
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary['evaluation']['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
