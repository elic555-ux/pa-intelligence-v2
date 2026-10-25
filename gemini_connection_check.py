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

ROOT=Path('COMPS_REPORTS')

def persist_reservation(repo):
    """Persist only the attempt ledger/cache; a failure prevents the AI call."""
    if os.environ.get('GITHUB_ACTIONS')!='true':raise ValueError('reservation_persistence_requires_actions')
    def git(*args,check=True):
        return subprocess.run(['git',*args],cwd=repo,check=check,capture_output=True,text=True)
    git('config','user.name','github-actions[bot]')
    git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    git('add','COMPS_REPORTS/gemini_findings')
    diff=git('diff','--cached','--quiet',check=False)
    if diff.returncode not in (0,1):raise ValueError('reservation_diff_failed')
    if diff.returncode==1:
        git('commit','-m','Reserve explicit Gemini photo check before API request')
        try:git('pull','--rebase','origin','main')
        except subprocess.SubprocessError:
            git('rebase','--abort',check=False)
            raise
        git('push','origin','HEAD:main')

def check(property_id,repo=Path('.'),persist=False):
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
        'read_timeout_seconds':result.get('read_timeout_seconds'),
        'findings_count':len(result.get('items') or []),
        'renovation_budget_complete':False,'rentcast_calls':0}
    store.write(repo/ROOT/'gemini_connection_check.json',summary)
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--property-id',required=True)
    args=parser.parse_args()
    summary=check(args.property_id,persist=os.environ.get('GITHUB_ACTIONS')=='true')
    print(json.dumps(summary,ensure_ascii=True))
    return 0 if summary['connection_verified'] else 1

if __name__=='__main__':raise SystemExit(main())
