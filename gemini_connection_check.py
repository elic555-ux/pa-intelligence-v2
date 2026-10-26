"""Run one explicit Gemini connection check against a real inventory property.

No RentCast import, cloud/deal writes, generated test photo, or key logging.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import report_gemini as gemini
import internal_report_store as store
from report_provider_reservation import persist_gemini_reservation as persist_reservation

ROOT=Path('COMPS_REPORTS')

def check(property_id,repo=Path('.'),persist=False,verify_sources=False):
    repo=Path(repo)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(property_id)):
        raise ValueError('invalid_property_id')
    inventory=store.read(repo/'properties.json')
    rows=[r for r in inventory if isinstance(r,dict) and str(r.get('id'))==str(property_id)] if isinstance(inventory,list) else []
    if len(rows)!=1:raise ValueError('property_identity_not_unique')
    result=gemini.collect(rows[0],repo/ROOT,before_send=(lambda:persist_reservation(repo)) if persist else None)
    success=result.get('status')=='partial' and result.get('reason')=='pricing_sources_not_connected'
    summary={
        'checked_at':store.stamp(),'property_id':str(property_id),'model':gemini.MODEL,
        'connection_verified':success,'status':result.get('status'),
        'reason':result.get('reason'),'photo_count':result.get('photo_count',0),
        'input_photo_count':result.get('input_photo_count',result.get('photo_count',0)),
        'http_status':result.get('http_status'),'failure_stage':result.get('failure_stage'),
        'error_code':result.get('error_code'),'response_seconds':result.get('response_seconds'),
        'provider_status':result.get('provider_status'),'provider_hint':result.get('provider_hint'),
        'read_timeout_seconds':result.get('read_timeout_seconds'),
        'findings_count':len(result.get('items') or []),
        'interior_assessments_count':len(result.get('interior_assessments') or []),
        'renovation_budget_complete':False,'rentcast_calls':0}
    if success and verify_sources:
        import report_source_audit
        audit=report_source_audit.collect(rows[0],result,repo)
        summary['report_sources_verified']=audit['sources_verified']
        summary['report_source_checks']=audit['checks']
        summary['reason']='renovation_scope_and_quantities_missing' if audit['sources_verified'] else 'report_sources_partial'
        if persist:persist_reservation(repo,'Save expanded-report source verification')
    store.write(repo/ROOT/'gemini_connection_check.json',summary)
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--property-id',required=True)
    args=parser.parse_args()
    summary=check(args.property_id,persist=os.environ.get('GITHUB_ACTIONS')=='true',verify_sources=True)
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary['connection_verified'] and summary.get('report_sources_verified',True) else 1

if __name__=='__main__':raise SystemExit(main())
