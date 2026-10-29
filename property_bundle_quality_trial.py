"""Explicit free-tier multi-image trial; saved real pixels, real provider, no live report write."""
import argparse,hashlib,json,os,re,subprocess
from pathlib import Path
from unittest.mock import patch
import report_gemini as gemini
import internal_report_store as store
from report_provider_reservation import persist_gemini_reservation

PROPERTY_ID='PA-MLS-1770125'
CASE_ID='school-multiview-v1'
MANIFEST_SHA='858ec7b958afecd0d29d123e3e78f1b5e0cfd931cf0f7d2c02b2d5eec2d551b3'
SOURCE='https://www.compass.com/homedetails/2414-School-St-Pittsburgh-PA-15235/17CA7K_pid/'
IDENTITY=['2414 School St','Pittsburgh','PA','15235']
FIXTURE=Path(__file__).parent/'test_fixtures/school_bundle'

def norm(value):return re.sub(r'[^A-Z0-9]','',str(value).upper())

def load_fixture(root=FIXTURE):
    root=Path(root);raw=(root/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA:raise ValueError('fixture_manifest_changed')
    data=json.loads(raw)
    if data.get('property_id')!=PROPERTY_ID or data.get('source_url')!=SOURCE or data.get('identity')!=IDENTITY:
        raise ValueError('fixture_subject_changed')
    rows=data.get('photos',[])
    if len(rows)!=7:raise ValueError('fixture_photo_count')
    prepared={}
    for row in rows:
        if not re.fullmatch(r'S-\d{3}\.jpg',row.get('file','')) or row.get('source_url')!=SOURCE:
            raise ValueError('fixture_source_changed')
        if not re.fullmatch(r'https://www\.compass\.com/m/[a-f0-9]+/origin\.jpg',row.get('url','')):
            raise ValueError('fixture_image_url_changed')
        body=(root/row['file']).read_bytes();sha=hashlib.sha256(body).hexdigest()
        if sha!=row['sha256'] or not body.startswith(b'\xff\xd8\xff') or not 100<len(body)<=gemini.MAX_IMAGE_BYTES:
            raise ValueError('fixture_image_changed')
        if row['url'] in prepared:raise ValueError('duplicate_fixture_url')
        prepared[row['url']]=('image/jpeg',body,sha)
    if len({x[2] for x in prepared.values()})!=7:raise ValueError('duplicate_fixture_bytes')
    return data,prepared

def evaluate(result,fixture):
    if result.get('status')!='partial':
        return {'contract_passed':False,'visual_quality':'not_evaluated','checks':{},
                'reason':result.get('error_code') or result.get('reason')}
    expected=[r['sha256'] for r in fixture['photos']]
    actual=result.get('photos') or []
    checks={'all_seven_real_images_received':result.get('photo_count')==7 and [r.get('sha256') for r in actual]==expected,
            'every_image_has_coverage':len(result.get('photo_coverage') or [])==7,
            'no_invented_budget':result.get('complete') is False and result.get('total_low') is None and result.get('total_high') is None}
    return {'contract_passed':all(checks.values()),'visual_quality':'pending_independent_review',
            'checks':checks,'general_accuracy_measured':False}

def run(repo,trigger,persist=False):
    repo=Path(repo)
    if (trigger.get('free_tier_confirmed') is not True or trigger.get('case_id')!=CASE_ID
        or trigger.get('manifest_sha256')!=MANIFEST_SHA or trigger.get('model')!='gemini-3.5-flash-lite'
        or not re.fullmatch(r'[a-z0-9-]{1,80}',trigger.get('trial_id',''))):
        raise ValueError('trial_not_authorized')
    if os.environ.get('GEMINI_REPORT_FREE_TIER_ENABLED')!='true':raise ValueError('free_tier_not_confirmed')
    if gemini.MODEL!=trigger['model']:raise ValueError('unexpected_production_model')
    if persist:
        if repo.resolve()==Path(__file__).parent.resolve():raise ValueError('separate_data_checkout_required')
        branch=subprocess.run(['git','branch','--show-current'],cwd=repo,check=True,capture_output=True,text=True).stdout.strip()
        if branch!='main':raise ValueError('data_checkout_must_be_main')
    fixture,prepared=load_fixture()
    props=store.read(repo/'properties.json') or []
    matches=[p for p in props if str(p.get('id'))==PROPERTY_ID]
    if len(matches)!=1:raise ValueError('property_identity_not_unique')
    subject=matches[0]
    identity=[subject.get('address'),subject.get('city'),subject.get('state') or 'PA',str(subject.get('zip',''))]
    if [norm(x) for x in identity]!=[norm(x) for x in IDENTITY]:raise ValueError('property_identity_mismatch')
    inputs={'property_id':PROPERTY_ID,'identity':IDENTITY,'facts':{},'available_photo_count':7,
            'photos':[dict(r,verification={'sha256':r['sha256']}) for r in fixture['photos']]}
    def image(photo):
        if photo['source_url']!=SOURCE or photo['url'] not in prepared:raise ValueError('unverified_fixture_image')
        return prepared[photo['url']]
    before=(lambda:persist_gemini_reservation(repo)) if persist else None
    # Only intake uses pinned, previously acquired real bytes. collect sends a
    # real provider request and retains its ordinary cache, quota and validation.
    # Owner labels and expected work scopes are never sent with the images.
    with patch.object(gemini,'verified_inputs',return_value=inputs),patch.object(gemini,'fetch_image',side_effect=image):
        result=gemini.collect(subject,repo/'COMPS_REPORTS',before_send=before)
    evaluation=evaluate(result,fixture)
    summary={'trial_id':trigger['trial_id'],'property_id':PROPERTY_ID,'model':gemini.MODEL,'version':gemini.VERSION,
             'checked_at':store.stamp(),'status':result.get('status'),'http_status':result.get('http_status'),
             'reason':result.get('reason'),'error_code':result.get('error_code'),'provider_hint':result.get('provider_hint'),
             'input_photo_count':result.get('input_photo_count'),'response_seconds':result.get('response_seconds'),
             'text_validation_diagnostics':result.get('text_validation_diagnostics'),
             'exterior_validation_diagnostics':result.get('exterior_validation_diagnostics'),
             'interior_validation_diagnostics':result.get('interior_validation_diagnostics'),
             'manifest_sha256':MANIFEST_SHA,'evaluation':evaluation,'live_report_updated':False,'rentcast_calls':0}
    store.write(repo/'COMPS_REPORTS/gemini_findings/quality_trials'/(trigger['trial_id']+'.json'),{'summary':summary,'result':result})
    if persist:persist_gemini_reservation(repo,'Record explicit seven-photo property bundle trial')
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--trigger',type=Path,required=True);args=p.parse_args()
    summary=run(args.repo,json.loads(args.trigger.read_text(encoding='utf-8')),persist=os.environ.get('GITHUB_ACTIONS')=='true')
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary['evaluation']['contract_passed'] else 1

if __name__=='__main__':raise SystemExit(main())
