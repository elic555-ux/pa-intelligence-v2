"""Verify public report sources using the just-checked Gemini result, no RentCast."""
from pathlib import Path
import internal_report_store as store
import report_enrichment

def collect(p,rehab_result,repo=Path('.')):
    repo=Path(repo);root=repo/'COMPS_REPORTS'
    fields=['id','address','city','county','zip','property_type','beds','baths','sqft']
    subject={key:p.get(key) for key in fields};subject['state']=p.get('state') or 'PA'
    profile=store.profile(subject,root,allow_public=True)
    analysis=report_enrichment.collect(subject,profile,root,rehab_result=rehab_result)
    closed=analysis.get('closed_sales',{});rehab=analysis.get('rehab',{})
    checks={'county':(profile.get('county_record') or {}).get('status')=='matched',
        'area':bool(closed.get('subject_location')),
        'closed_sales':closed.get('status') in {'available','no_matching_sales'},
        'financing':len(analysis.get('financing',{}).get('products',[]))>0,
        'photo_analysis':rehab.get('status')=='partial' and bool(rehab.get('items') or rehab.get('interior_assessments') or rehab.get('exterior_assessments')),
        'pricebook':rehab.get('pricebook',{}).get('status')=='current'}
    audit={'property_id':str(p['id']),'checked_at':store.stamp(),'sources_verified':all(checks.values()),
        'checks':checks,'analysis':analysis,'rentcast_calls':0,
        'remaining_data':['after_repair_valuation','documented_operating_and_transaction_costs',
            'renovation_scope_and_quantities','verified_neighborhood_class']}
    path=root/'expanded_reports'/(store.key(p['id'])+'.json')
    prior=store.read(path)
    if (isinstance(prior,dict) and prior.get('property_id')==str(p['id']) and store.same_address(subject,prior.get('property',{}))):
        # Adding an enrichment never changes original per-source/receipt dates.
        updated=dict(prior);updated['report_analysis']=analysis;updated['last_enrichment_at']=store.stamp()
        store.write(path,updated);audit['existing_rent_receipt_preserved']=True
    else:audit['existing_rent_receipt_preserved']=False
    store.write(root/'gemini_findings/report_source_audit.json',audit)
    return audit
