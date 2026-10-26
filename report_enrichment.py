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
import report_gemini
import rehab_pricebook
import internal_report_store as store

VERSION='readonly-report-sources-v1'
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
    result['area']={'status':'available' if point else 'not_collected','class':None,'class_verified':False,
        'neighborhood':point.get('neighborhood'),'tract':point.get('tract'),
        'source_name':'WPRDC parcel geography','source_url':closed_sales.GEO_SOURCE,
        'observed_at':now,'source_as_of':point.get('source_as_of')}
    if allow_public and rehab_result is None:
        try:
            import report_gallery
            result['gallery']=report_gallery.collect(p,Path(root).parent)
        except Exception as exc:
            result['gallery']={'status':'source_unavailable','property_id':property_id}
            result['errors']['gallery']=type(exc).__name__
    # Gemini returns observed findings only. Costs remain missing until a sourced
    # price book is connected; a model response cannot supply dollar estimates.
    try:
        if rehab_result is not None:
            if rehab_result.get('property_id')!=property_id:raise ValueError('rehab_subject_mismatch')
            result['rehab']=dict(rehab_result)
        else:result['rehab']=report_gemini.collect(p,Path(root),before_send=before_gemini_send) if allow_public else report_gemini.empty(
            p,'provider_not_configured','collection_disabled')
    except Exception as exc:
        result['rehab']=report_gemini.empty(p,'source_unavailable','analysis_failed')
        result['errors']['rehab']=type(exc).__name__
    try:result['rehab']=rehab_pricebook.attach(result['rehab'],p)
    except (ValueError,OSError,KeyError):result['errors']['rehab_pricebook']='catalog_unavailable'
    result['status']='partial' if result['errors'] or result['rehab']['status']!='available' else 'available'
    return result
