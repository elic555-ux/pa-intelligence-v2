"""Source results for the nine-section, read-only report.

This module does not modify the inventory, investment scenarios, or cloud deals.
External source failures are isolated and explicitly recorded.
"""
from __future__ import annotations
import os,json
from pathlib import Path
from datetime import datetime,timezone
import closed_sales
import report_financing
import report_property_taxes
import report_area
import report_public_context
import internal_report_store as store

VERSION='concise-report-sources-v3'
def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def base_property(p,profile):
    value=dict(p)
    if profile.get('inventory',{}).get('status')=='ambiguous_identity':return value
    record=profile.get('county_record') or {}
    if record.get('status')=='matched' and store.same_address(p,record.get('subject',{})):
        for key in ['sqft','beds','baths']:
            if record.get('fields',{}).get(key) is not None:value[key]=record['fields'][key]
        value['county']=record.get('county') or value.get('county')
    return value

def collect(p,profile,root=Path('COMPS_REPORTS'),allow_public=True,previous=None,before_gemini_send=None,rehab_result=None):
    now=stamp();property_id=str(p['id']);subject=base_property(p,profile)
    result={'version':VERSION,'property_id':property_id,'property':dict(p),'generated_at':now,'errors':{}}
    county=profile.get('county_record') or {}
    if profile.get('inventory',{}).get('status')=='ambiguous_identity':
        result.update(status='subject_mismatch',closed_sales={'status':'subject_mismatch','property_id':property_id,'comparables':[]})
        return result
    if allow_public:
        try:result['closed_sales']=closed_sales.collect(subject,county)
        except Exception as exc:
            result['closed_sales']={'status':'source_unavailable','property_id':property_id,'comparables':[],'source_name':'Allegheny County / WPRDC','source_url':closed_sales.SOURCE,'observed_at':now}
            result['errors']['closed_sales']=type(exc).__name__
        try:result['financing']=report_financing.collect(Path(root))
        except Exception as exc:
            result['financing']={'status':'source_unavailable','observed_at':now,'products':[]}
            result['errors']['financing']=type(exc).__name__
    else:
        result['closed_sales']={'status':'lookup_disabled','property_id':property_id,'comparables':[]}
        result['financing']={'status':'lookup_disabled','observed_at':now,'products':[]}
    try:result['property_taxes']=report_property_taxes.collect(p,county,Path(root),allow_public=allow_public)
    except Exception as exc:
        result['property_taxes']={'status':'source_unavailable','property_id':property_id,'complete':False,'total_property_tax':None}
        result['errors']['property_taxes']=type(exc).__name__
    point=result['closed_sales'].get('subject_location') or {}
    try:result['area']=report_area.collect(point,Path(root),allow_public=allow_public)
    except Exception as exc:
        result['area']={'status':'source_unavailable','metrics':[],'class':None,'class_verified':False}
        result['errors']['area']=type(exc).__name__
    try:result['public_context']=report_public_context.collect(p,county,point,Path(root),allow_public=allow_public)
    except Exception as exc:
        result['public_context']={'status':'source_unavailable','property_id':property_id}
        result['errors']['public_context']=type(exc).__name__
    if allow_public:
        try:
            import report_gallery
            result['gallery']=report_gallery.collect(p,Path(root).parent)
        except Exception as exc:
            result['gallery']={'status':'source_unavailable','property_id':property_id}
            result['errors']['gallery']=type(exc).__name__
    # Renovation inference and pricing are excluded from the expanded report.
    result['status']='partial' if result['errors'] or any(result.get(k,{}).get('status') not in {'available','partial'} for k in ('closed_sales','area','property_taxes','financing')) else 'available'
    return result
