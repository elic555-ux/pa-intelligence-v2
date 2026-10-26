"""Bounded reads of lenders' published product terms; never a personal quote."""
from __future__ import annotations
import html,json,re
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlsplit

PRODUCTS=(
 {'id':'new-silver-flip','lender':'New Silver','product':'Fix and Flip','strategy':'flip','url':'https://newsilver.com/the-lender/fix-and-flip-loans/'},
 {'id':'new-silver-dscr','lender':'New Silver','product':'DSCR Rental','strategy':'rental','url':'https://newsilver.com/dscr-loan/'},
 {'id':'kiavi-bridge','lender':'Kiavi','product':'Bridge','strategy':'flip','url':'https://www.kiavi.com/loans/bridge-loans'},
)

def visible_text(raw):
    raw=re.sub(r'<(script|style|nav)\b[^>]*>.*?</\1>',' ',raw,flags=re.S|re.I)
    return ' '.join(html.unescape(re.sub('<[^>]+>',' ',raw)).split())

def get_html(url):
    host=urlsplit(url).hostname
    if host not in {'newsilver.com','www.kiavi.com'} or urlsplit(url).scheme!='https':raise ValueError('Unapproved lender host')
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 (PA property report source check)'}),timeout=20) as response:
        if urlsplit(response.url).hostname!=host:raise ValueError('Unexpected lender redirect')
        raw=response.read(1_500_001)
        if len(raw)>1_500_000:raise ValueError('Lender page too large')
        return raw.decode('utf-8')

def parse_terms(product,raw):
    text=visible_text(raw);out={**product,'source_url':product['url'],'source_name':product['lender'],
        'observed_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'status':'published_terms',
        'is_personal_quote':False,'eligibility_confirmed':False,'currency':'USD'}
    def match(pattern):
        m=re.search(pattern,text,re.I);return m.group(1) if m else None
    if product['lender']=='New Silver':
        # Product-detail labels, rather than menus, blog examples or recently funded loans.
        start=text.find('Current as of')
        if start<0:raise ValueError('Product terms not located')
        text=text[start:start+3000]
        out.update(rate_text=match(r'Interest Rate\s+((?:from\s+)?\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*%)'),
            origination_text=match(r'Origination fee\s+(\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*%)'),
            ltc_text=match(r'Loan To Cost\s*\(LTC\)\s+(up to\s+\d+\s*%)'),
            ltv_text=match(r'Loan To Value\s+(up to\s+\d+\s*%)'),
            larv_text=match(r'Loan To ARV\s+(up to\s+\d+\s*%)'),
            purchase_text=match(r'Loan To Purchase Price\s+(up to\s+\d+\s*%)'),
            term_text=match(r'\bTerm\s+((?:up to\s+)?\d+\s*(?:months|[-–]yr fixed rate))'),
            minimum_loan=match(r'Minimum Loan Amount\s+(\$[\d,]+)'),
            maximum_loan=match(r'Maximum Loan Amount\s+(\$[\d,]+)'),
            fico_text=match(r'Minimum FICO\s+(\d+)'))
    else:
        out.update(rate_text=match(r'Rates as low as\s+(\d+(?:\.\d+)?\s*%)'),
            purchase_text=match(r'(Up to\s+\d+\s*%\s+of purchase price)'),
            larv_text=match(r'(Up to\s+\d+\s*%\s+of after-repair value)'),
            term_text=match(r'(12,\s*18\s*\+\s*24 month terms)'),
            origination_text=None,minimum_loan=match(r'Loans from\s+(\$\d+K)'),
            maximum_loan=match(r'Loans from\s+\$\d+K to\s+(\$\d+MM)'))
    if not out.get('rate_text') or not any(out.get(k) for k in ['ltv_text','ltc_text','purchase_text','larv_text']):raise ValueError('Required published terms missing')
    return out

def collect(root=Path('COMPS_REPORTS'),refresh=False):
    now=datetime.now(timezone.utc);path=Path(root)/'financing/current.json'
    try:
        old=json.loads(path.read_text(encoding='utf-8'));age=(now-datetime.fromisoformat(old['observed_at'])).total_seconds()
        if not refresh and 0<=age<7*86400:return old
    except (OSError,ValueError,KeyError,TypeError):pass
    result={'version':'published-lender-terms-v1','observed_at':now.isoformat(timespec='seconds'),'products':[],'errors':[]}
    for product in PRODUCTS:
        try:result['products'].append(parse_terms(product,get_html(product['url'])))
        except Exception as e:result['errors'].append({'product_id':product['id'],'reason':type(e).__name__})
    result['status']='available' if result['products'] else 'source_unavailable'
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
