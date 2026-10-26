"""Read an exact Allegheny county annual bill; never a total expense or forecast.

Two bounded unauthenticated GETs (robots + bill), no redirects/cookies/payment.
Only the current-year face bill is accepted, never payment-history amounts.
"""
from __future__ import annotations
import argparse,json,re
from datetime import datetime,timedelta,timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,urlsplit
from urllib.robotparser import RobotFileParser
import internal_report_store as store

VERSION='allegheny-county-bill-1'
ORIGIN='https://realestate.alleghenycounty.us'
UA='PAIntelligenceReport/1.0'
CACHE_DAYS=30
MAX_BYTES=1_500_000

class SourceStop(ValueError):pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class BillFields(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.values={};self.active=[]
    def handle_starttag(self,tag,attrs):
        key=dict(attrs).get('id')
        if tag=='span' and key and key.startswith('MainContent_'):
            if key in self.values:raise SourceStop('duplicate_bill_field')
            self.values[key]=[];self.active.append(key)
        elif tag=='br':self.handle_data(' ')
    def handle_endtag(self,tag):
        if tag=='span' and self.active:self.active.pop()
    def handle_data(self,text):
        for key in self.active:self.values[key].append(text)
    def text(self,name):return ' '.join(''.join(self.values.get('MainContent_'+name,[])).split())

def source_url(parcel):return ORIGIN+'/Tax?'+urlencode({'ID':parcel})
def parcel_id(county,p):
    if (county.get('status')!='matched' or county.get('county')!='Allegheny' or
            not store.same_address(p,county.get('subject',{})) or str(p.get('state') or 'PA').upper()!='PA'):
        raise SourceStop('subject_mismatch')
    parcel=county.get('parcel_id')
    if not isinstance(parcel,str) or not re.fullmatch(r'[0-9A-Z]{10,20}',parcel):raise SourceStop('parcel_missing')
    return parcel

def parse_bill(raw,p,county,now):
    parcel=parcel_id(county,p);f=BillFields();f.feed(raw)
    clean=lambda x:re.sub(r'[^0-9A-Z]','',x.upper())
    if f.text('parcelID')!=parcel or clean(f.text('InfoPane_parcelIDLbl').removeprefix('Parcel ID:'))!=parcel:
        raise SourceStop('parcel_mismatch')
    # City is validated by its exact known suffix, never by a guessed split of the street.
    expected='Address: '+str(p['address'])+' '+str(p['city'])+', '+str(p.get('state') or 'PA')+' '+str(p['zip'])[:5]
    if store.norm(f.text('InfoPane_addressLbl'))!=store.norm(expected):raise SourceStop('address_mismatch')
    def due(name):
        try:return datetime.strptime(f.text(name),'Net Tax Due %B %d, %Y' if name=='netTaxDue' else 'Gross Tax Due %B %d, %Y').date()
        except ValueError:raise SourceStop('bill_date_missing')
    net_date=due('netTaxDue');gross_date=due('grossTaxDue')
    if net_date.year!=now.year or gross_date.year!=now.year or net_date>gross_date:raise SourceStop('bill_year_mismatch')
    def amount(name):
        text=f.text(name)
        if not re.fullmatch(r'\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\$\d+(?:\.\d{2})?',text):raise SourceStop('bill_amount_missing')
        return float(text.replace('$','').replace(',',''))
    face=amount('grossTax');discount=amount('netTax');base=amount('taxableValue')
    if face<=0 or not 0<discount<=face or base<=0:raise SourceStop('bill_amount_invalid')
    rate=f.text('milageRate')
    if not re.fullmatch(r'\d+(?:\.\d+)?',rate) or not 0<float(rate)<100:raise SourceStop('millage_missing')
    homestead=f.text('lblHomesteadText')
    return {'version':VERSION,'status':'partial','property_id':str(p['id']),'property':dict(p),
        'parcel_id':parcel,'tax_year':now.year,'observed_at':now.isoformat(),
        'source_name':'Allegheny County Treasurer / Real Estate Portal','source_url':source_url(parcel),
        'basis':'current_owner_annual_county_bill','currency':'USD','county_face_amount':face,
        'county_discount_amount':discount,'face_due_date':gross_date.isoformat(),'discount_due_date':net_date.isoformat(),
        'county_millage':float(rate),'county_taxable_value':base,
        'homestead_exclusion_applied':True if homestead=='*Homestead exclusion applied' else None,
        'forecast_for_new_owner':False,'certified_closing_balance':False,
        'total_property_tax':None,'complete':False,
        'missing_components':['municipal','school','other_local_levies'],
        'operating_expenses_complete':False}

class PublicReader:
    def __init__(self):self.requests=0;self.opener=build_opener(NoRedirect())
    def get(self,url,robots=False):
        part=urlsplit(url)
        if part.scheme!='https' or part.netloc!='realestate.alleghenycounty.us' or part.path not in {'/Tax','/robots.txt'}:
            raise SourceStop('unapproved_source')
        if self.requests>=2:raise SourceStop('request_limit')
        self.requests+=1
        try:
            with self.opener.open(Request(url,headers={'User-Agent':UA,'Accept':'text/html,text/plain'}),timeout=20) as response:
                if response.url!=url:raise SourceStop('source_redirect')
                raw=response.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:raise SourceStop('source_too_large')
                return raw.decode('utf-8-sig')
        except HTTPError as exc:
            if robots and exc.code==404:return '' # Standard absent robots file, not a bypass.
            raise SourceStop('source_http_'+str(exc.code)) from None
        except (URLError,TimeoutError,UnicodeError):raise SourceStop('source_transport_unavailable') from None
    def read_bill(self,url):
        rules=self.get(ORIGIN+'/robots.txt',robots=True)
        if '<html' in rules.lower():raise SourceStop('robots_unavailable')
        robot=RobotFileParser();robot.parse(rules.splitlines())
        if not robot.can_fetch(UA,url):raise SourceStop('robots_disallowed')
        return self.get(url)

def collect(p,county,root=Path('COMPS_REPORTS'),allow_public=True,reader=None,now=None):
    now=now or datetime.now(timezone.utc);root=Path(root)
    empty={'version':VERSION,'status':'not_collected','property_id':str(p['id']),'property':dict(p),
        'observed_at':now.isoformat(),'complete':False,'total_property_tax':None,
        'forecast_for_new_owner':False,'source_network_requests':0}
    if not allow_public:return empty|{'status':'lookup_disabled'}
    try:parcel=parcel_id(county,p)
    except SourceStop as exc:return empty|{'status':str(exc)}
    path=root/'property_taxes'/(store.key(p['id'])+'.json')
    prior=store.read(path) or {};same=(prior.get('version')==VERSION and prior.get('property_id')==str(p['id']) and
        prior.get('parcel_id')==parcel and store.same_address(p,prior.get('property',{})))
    def dt(value):
        try:
            value=datetime.fromisoformat(value)
            return value if value.tzinfo else None
        except (TypeError,ValueError):return None
    observed=dt(prior.get('observed_at'))
    if same and prior.get('status')=='partial' and prior.get('tax_year')==now.year and observed and timedelta(0)<=now-observed<timedelta(days=CACHE_DAYS):
        return dict(prior,source_network_requests=0,source_mode='cache')
    global_path=root/'property_taxes/source_state.json';global_state=store.read(global_path) or {}
    for deadline in [dt(prior.get('retry_after_at')) if same else None,dt(global_state.get('retry_after_at'))]:
        if deadline and deadline>now:return empty|{'status':'source_cooldown','parcel_id':parcel,'retry_after_at':deadline.isoformat()}
    reader=reader or PublicReader();before=reader.requests
    try:
        result=parse_bill(reader.read_bill(source_url(parcel)),p,county,now)
    except (SourceStop,OSError) as exc:
        reason=str(exc) if isinstance(exc,SourceStop) else 'source_unavailable'
        result=empty|{'status':reason,'parcel_id':parcel,'source_url':source_url(parcel),'retry_after_at':(now+timedelta(days=1)).isoformat()}
        if reason.startswith(('source_http_','robots_')):
            store.write(global_path,{'status':reason,'checked_at':now.isoformat(),'retry_after_at':result['retry_after_at']})
    result['source_network_requests']=reader.requests-before
    store.write(path,result);return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--property-id',required=True);args=parser.parse_args()
    # Reuse the exact matched county record and existing report; no provider call.
    path=Path('COMPS_REPORTS/expanded_reports')/(store.key(args.property_id)+'.json');report=store.read(path) or {}
    p=report.get('property') or {};county=(report.get('internal_data') or {}).get('county_record') or {}
    if str(p.get('id'))!=args.property_id or report.get('property_id')!=args.property_id:raise SystemExit('Existing matched report required')
    result=collect(p,county)
    if result.get('status')=='partial':
        report.setdefault('report_analysis',{})['property_taxes']=result
        # Preserve original analysis, rent receipt and all per-source dates.
        report['last_tax_enrichment_at']=result['observed_at'];store.write(path,report)
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result.get('status')=='partial' else 1)

if __name__=='__main__':main()
