"""Current production HTML in real browsers, with every service/write isolated."""
import base64
import copy
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote, urlsplit
import urllib.request

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'qa-results'
OUT.mkdir(exist_ok=True)
HTML=(ROOT/'index.html').read_text()
OWNER=re.search(r"const PA_OWNER_ID\s*=\s*'([^']+)'",HTML).group(1)
INVENTORY=json.loads((ROOT/'properties.json').read_text())
RESULTS=[]
METRICS={}
ASSETS={}
for key,url in [('tailwind','https://cdn.tailwindcss.com'),('lucide','https://unpkg.com/lucide@latest')]:
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'PA-isolated-QA/1.0'}),timeout=40) as response:
        raw=response.read(4_000_001)
        if len(raw)>4_000_000: raise RuntimeError('Static QA dependency exceeds bound')
        ASSETS[key]=raw
        METRICS[key+'_sha256']=hashlib.sha256(raw).hexdigest()

SDK=r"""
window.__qaWrites=[];window.__qaDb={};
if(!sessionStorage.getItem('qa-auth-mode'))sessionStorage.setItem('qa-auth-mode',__MODE__);
const qaOwner=__OWNER__;
let qaAuthCallback=null;
const qaResult=()=>({data:{user:sessionStorage.getItem('qa-auth-mode')==='owner'?{id:qaOwner,email:'qa@example.test'}:sessionStorage.getItem('qa-auth-mode')==='wrong'?{id:'qa-other-user',email:'other@example.test'}:null},error:null});
window.supabase={createClient:()=>({
 auth:{getUser:async()=>qaResult(),onAuthStateChange:fn=>{qaAuthCallback=fn;return{data:{subscription:{unsubscribe(){}}}};},
 signInWithPassword:async data=>({error:{message:'QA invalid credentials'}}),
 signOut:async()=>{sessionStorage.setItem('qa-auth-mode','none');return{error:null};}},
 from:table=>({
 select:async()=>({data:window.__qaDb[table]||[],error:__CLOUD_FAIL__?{message:'QA cloud unavailable'}:null}),
 upsert:async data=>{window.__qaWrites.push({table,operation:'upsert',data});return{error:null};},
 delete:()=>({eq:async(key,value)=>{window.__qaWrites.push({table,operation:'delete',key,value});return{error:null};}})
 })
})};
"""

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,*args):pass

server=ThreadingHTTPServer(('127.0.0.1',8765),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
LOCAL='http://127.0.0.1:8765/'

def fixture_rows():
    rows=[]
    for i,(word,price,kind) in enumerate([('Alpha',90000,'Single Family'),('Beta',120000,'Multi-Family'),('Gamma',160000,'Single Family'),('Unknown',None,'Single Family'),('Background',180000,'Single Family')],1):
        rows.append({'id':'QA-'+str(i),'source_type':'mls','address':str(i*100)+' QA '+word+' St',
          'city':'Pittsburgh','county':'Allegheny','state':'PA','zip':'15235','price':price,
          'beds':2,'baths':1,'sqft':1000,'type':kind,'listed_date':'2026-10-'+str(18-i).zfill(2),
          'market_status':'active','url':'https://example.invalid/listing/qa-'+str(i),
          'summary':'Synthetic isolated QA property','docket_id':'MLS-'+str(1000+i)})
    return rows

def record(name,fn,severity='P2'):
    try:
        detail=fn()
        if detail is False: raise AssertionError('Expected condition was false')
        RESULTS.append({'name':name,'status':'pass','detail':detail})
    except Exception as exc:
        RESULTS.append({'name':name,'status':'fail','severity':severity,'detail':str(exc)[:700]})

def yes(value,message):
    if not value:raise AssertionError(message)
    return True

def scenario(browser,rows=None,mode='owner',mobile=False,cloud_fail=False,inventory_fail=False,images_fail=False,api_limit=None):
    context=browser.new_context(viewport={'width':390 if mobile else 1440,'height':844 if mobile else 1000},
                                is_mobile=mobile,has_touch=mobile,service_workers='block')
    errors=[];traffic={'github_reads':0,'remote_write_attempts':0,'unexpected_remote':[],'inventory_reads':0}
    def route(req):
        request=req.request
        url=request.url
        parsed=urlsplit(url)
        if request.method not in {'GET','HEAD'}:
            traffic['remote_write_attempts']+=1
            req.fulfill(status=403,body='QA blocks all network writes');return
        if url.startswith(LOCAL):
            if parsed.path=='/properties.json':
                traffic['inventory_reads']+=1
                if inventory_fail:req.fulfill(status=503,body='QA unavailable');return
                if rows is not None:req.fulfill(content_type='application/json',body=json.dumps(rows));return
            if parsed.path=='/sheriff_listings.json' and rows is not None:
                req.fulfill(content_type='application/json',body='[]');return
            req.continue_();return
        if parsed.hostname=='api.github.com':
            traffic['github_reads']+=1
            if api_limit is not None and traffic['github_reads']>api_limit:
                req.fulfill(status=403,headers={'Access-Control-Allow-Origin':'*'},content_type='application/json',body='{"message":"QA simulated public API rate limit"}');return
            prefix='/repos/elic555-ux/pa-intelligence-v2/contents/'
            if not parsed.path.startswith(prefix):
                req.fulfill(status=404,body='{}');return
            relative=unquote(parsed.path[len(prefix):])
            path=(ROOT/relative).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                req.fulfill(status=404,headers={'Access-Control-Allow-Origin':'*'},content_type='application/json',body='{"message":"QA missing file"}');return
            raw=path.read_bytes()
            req.fulfill(headers={'Access-Control-Allow-Origin':'*'},content_type='application/json',body=json.dumps({'encoding':'base64','content':base64.b64encode(raw).decode(),'sha':hashlib.sha1(raw).hexdigest()}));return
        if 'supabase-js' in url:
            body=SDK.replace('__MODE__',json.dumps(mode)).replace('__OWNER__',json.dumps(OWNER)).replace('__CLOUD_FAIL__','true' if cloud_fail else 'false')
            req.fulfill(content_type='application/javascript',body=body);return
        if parsed.hostname=='cdn.tailwindcss.com':
            req.fulfill(content_type='application/javascript',body=ASSETS['tailwind']);return
        if parsed.hostname=='unpkg.com' and 'lucide' in url:
            req.fulfill(content_type='application/javascript',body=ASSETS['lucide']);return
        if parsed.hostname in {'fonts.googleapis.com','fonts.gstatic.com'}:
            req.fulfill(content_type='text/css',body='');return
        if request.resource_type=='image':
            if images_fail:req.fulfill(status=404,body='QA unavailable image')
            else:req.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jrXcAAAAASUVORK5CYII='))
            return
        traffic['unexpected_remote'].append(parsed.hostname)
        req.abort()
    context.route('**/*',route)
    page=context.new_page()
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('dialog',lambda dialog:dialog.accept())
    started=time.monotonic()
    page.goto(LOCAL+'index.html',wait_until='load')
    if mode=='owner':
        page.wait_for_function("!document.getElementById('pa-app-shell').hidden",timeout=45000)
    else:
        page.wait_for_function("!document.getElementById('pa-login-form').hidden",timeout=15000)
    METRICS.setdefault('initial_load_ms',[]).append({'mobile':mobile,'actual_inventory':rows is None,'ms':round((time.monotonic()-started)*1000)})
    return context,page,errors,traffic

def value(page,code):return page.evaluate(code)
def open_first(page):
    page.evaluate("openPropertyModal(uiPropertyKey(rawProperties[0]))")
    page.wait_for_function("!document.getElementById('property-modal').classList.contains('hidden')")

with sync_playwright() as p:
    browser=p.chromium.launch()
    ctx,page,errors,traffic=scenario(browser,fixture_rows(),mode='none')
    record('Unauthenticated session remains at login',lambda:yes(page.locator('#pa-app-shell').is_hidden(),'App shell exposed'))
    record('Unauthenticated session does not load inventory',lambda:yes(traffic['inventory_reads']==0,'Inventory fetched before authentication'),'P1')
    page.locator('#pa-login-email').fill('qa@example.test');page.locator('#pa-login-password').fill('bad-qa-password')
    page.locator('#pa-login-submit').click()
    record('Rejected login clears password',lambda:yes(page.locator('#pa-login-password').input_value()=='','Password retained'))
    ctx.close()
    ctx,page,errors,traffic=scenario(browser,fixture_rows(),mode='wrong')
    record('Wrong-account session is refused',lambda:yes(page.locator('#pa-app-shell').is_hidden() and traffic['inventory_reads']==0,'Wrong account loaded app'),'P1')
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,fixture_rows())
    record('Owner fixture boot has no JavaScript exceptions',lambda:yes(not errors,str(errors)),'P1')
    record('Desktop document uses RTL Hebrew',lambda:yes(value(page,"document.documentElement.dir==='rtl'&&document.documentElement.lang==='he'"),'Missing RTL metadata'))
    record('Default board respects configured ceiling',lambda:yes(value(page,"!getFilteredProperties().some(p=>p.price===180000)"),'Above-budget listing shown'))
    page.locator('#filter-max-price').fill('120000')
    record('Typed maximum is formatted and filters inclusively',lambda:yes(page.locator('#filter-max-price').input_value()=='120,000' and value(page,"getFilteredProperties().some(p=>p.id==='QA-2')&&!getFilteredProperties().some(p=>p.id==='QA-3')"),'Typed maximum incorrect'))
    page.locator('#filter-unknown-price').uncheck()
    record('Unknown-price toggle excludes unknown prices',lambda:yes(value(page,"getFilteredProperties().every(p=>getMarketAskingPrice(p)!==null)"),'Unknown price not excluded'))
    page.locator('#filter-min-price').fill('150000')
    record('Reversed price range displays an error and zero results',lambda:yes('המינימום' in page.locator('#active-price-range').inner_text() and value(page,"getFilteredProperties().length===0"),'Invalid range accepted'))
    page.locator('#filter-min-price').fill('50000');page.locator('#filter-max-price').fill('170000')
    page.locator('#filter-type').select_option('Multi-Family')
    record('Property type filters the board',lambda:yes(value(page,"getFilteredProperties().length===1&&getFilteredProperties()[0].id==='QA-2'"),'Wrong type result'))
    page.locator('#filter-type').select_option('all')
    page.locator('#sort-by').select_option('price_asc')
    record('Price sorting orders rendered rows',lambda:yes('QA Alpha' in page.locator('#properties-container tbody tr').first.inner_text(),'Price sorting incorrect'))
    page.locator('#property-search').fill('Alpha | Gamma')
    record('Search OR finds both requested properties',lambda:yes(value(page,"getFilteredProperties().length===2"),'OR search incorrect'))
    page.locator('#property-search').fill('QA Alpha')
    record('Search AND matches address words',lambda:yes(value(page,"getFilteredProperties().length===1&&getFilteredProperties()[0].id==='QA-1'"),'AND search incorrect'))
    page.locator('#property-search').fill('Background')
    record('All-inventory search labels out-of-budget history',lambda:yes('מחוץ לטווח' in page.locator('#properties-container').inner_text(),'Background-price result missing label'))
    page.evaluate('clearPropertySearch()')
    page.locator('#filter-latest-new').click()
    record('Latest-new button toggles and describes its state',lambda:yes(page.locator('#filter-latest-new').get_attribute('aria-pressed')=='true','New filter state absent'))
    page.locator('#filter-latest-new').click()
    page.locator('#properties-container tbody tr').first.click()
    record('Native row click opens the correct property',lambda:yes('QA Alpha' in page.locator('#modal-address').inner_text(),'Wrong property opened'))
    record('Missing financial evidence is not displayed as a calculated yield',lambda:yes('אין נתון מבוסס' in page.locator('#modal-yield').inner_text(),'Unverified yield displayed'),'P1')
    record('Map link points to Google Maps over HTTPS',lambda:yes(page.locator('#modal-property-map-link').get_attribute('href').startswith('https://www.google.com/maps/'),'Map destination incorrect'))
    record('Main modal exposes dialog semantics',lambda:yes(page.locator('#property-modal').get_attribute('role')=='dialog','Property modal lacks role=dialog and accessible dialog semantics'))
    page.keyboard.press('Escape')
    record('Escape closes the property dialog',lambda:yes(page.locator('#property-modal').is_hidden(),'Escape does not close property modal'))
    page.evaluate('closePropertyModal()')
    page.locator('#btn-view-grid').click()
    record('Grid and table preserve the filtered result count',lambda:yes(value(page,"document.querySelectorAll('#properties-container>div>div').length===getFilteredProperties().length"),'View switch count differs'))
    page.screenshot(path=str(OUT/'desktop-grid.png'))
    record('Desktop page avoids document-level horizontal overflow',lambda:yes(value(page,"document.documentElement.scrollWidth<=innerWidth+2"),'Desktop document overflows'))
    record('Read-only browsing makes no network write requests',lambda:yes(traffic['remote_write_attempts']==0,'Network writes attempted'),'P1')
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,None)
    METRICS['actual_data_summary']=value(page,"({loaded:rawProperties.length,database:databaseProperties.length,summary:dataLoadSummary,displayed:getFilteredProperties().length})")
    record('Complete current saved inventory renders',lambda:yes(value(page,"rawProperties.length>0&&databaseProperties.length>=rawProperties.length"),'Saved inventory not rendered'),'P1')
    record('Complete saved inventory causes no JavaScript exception',lambda:yes(not errors,str(errors)),'P1')
    METRICS['public_api_reads_on_boot']=traffic['github_reads']
    page.screenshot(path=str(OUT/'desktop-current-inventory.png'))
    eligible=value(page,"rawProperties.filter(p=>!p._idAmbiguous).slice(0,14).map(p=>uiPropertyKey(p))")
    before=traffic['github_reads']
    for key in eligible:
        page.evaluate("(key)=>openPropertyModal(key)",key)
        page.wait_for_timeout(150)
        page.evaluate('closePropertyModal()')
    METRICS['github_reads_for_14_distinct_property_opens']=traffic['github_reads']-before
    METRICS['requests_scope']='All GitHub reads above were fulfilled from checkout fixtures; no real GitHub request was sent by the browser.'
    ctx.close()

    for engine in ('chromium','webkit'):
        mobile_browser=browser if engine=='chromium' else p.webkit.launch()
        ctx,page,errors,traffic=scenario(mobile_browser,fixture_rows(),mobile=True)
        record(engine+' mobile can load owner board',lambda:yes(page.locator('#pa-app-shell').is_visible(),'Mobile board hidden'),'P1')
        record(engine+' mobile has no document-level horizontal overflow',lambda:yes(value(page,"document.documentElement.scrollWidth<=innerWidth+2"),'Mobile document horizontally overflows'))
        page.locator('#btn-view-grid').click()
        page.screenshot(path=str(OUT/(engine+'-mobile-grid.png')))
        record(engine+' mobile cards have no document-level horizontal overflow',lambda:yes(value(page,"document.documentElement.scrollWidth<=innerWidth+2"),'Mobile cards overflow document'))
        open_first(page)
        record(engine+' mobile can open property details',lambda:yes(page.locator('#modal-address').is_visible(),'Modal address offscreen'))
        page.screenshot(path=str(OUT/(engine+'-mobile-modal.png')))
        record(engine+' mobile modal stays within viewport width',lambda:yes(value(page,"document.getElementById('property-modal').getBoundingClientRect().width<=innerWidth+2"),'Mobile modal exceeds viewport'))
        record(engine+' mobile has no JavaScript exception',lambda:yes(not errors,str(errors)),'P1')
        ctx.close()
        if engine=='webkit':mobile_browser.close()

    ctx,page,errors,traffic=scenario(browser,fixture_rows(),cloud_fail=True)
    open_first(page)
    record('Unavailable cloud disables watchlist writes',lambda:yes(page.locator('#modal-watchlist-btn').is_disabled(),'Watchlist enabled after failed cloud read'),'P1')
    record('Unavailable cloud disables deal-room transfer',lambda:yes(page.locator('#modal-pipeline-btn').is_disabled(),'Pipeline transfer enabled after failed cloud read'),'P1')
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,fixture_rows(),inventory_fail=True)
    record('Inventory fetch failure displays a readable error',lambda:yes('לא נטענו' in page.locator('#properties-container').inner_text(),'Inventory failure hidden'),'P1')
    ctx.close()

    probe=fixture_rows()[:1]
    probe[0]['address']='<img src="/qa-missing-address" onerror="window.__qaAddressMarkup=true"> QA Safe St'
    ctx,page,errors,traffic=scenario(browser,probe)
    page.locator('#btn-view-grid').click();page.wait_for_timeout(150)
    record('Address markup is displayed as text without execution',lambda:yes(not value(page,"Boolean(window.__qaAddressMarkup)"),'Address markup executed'),'P1')
    ctx.close()

    probe=fixture_rows()[:1]
    probe[0]['baths']='<img src="/qa-missing-bath" onerror="window.__qaBathMarkup=true">'
    ctx,page,errors,traffic=scenario(browser,probe)
    page.locator('#btn-view-grid').click();page.wait_for_timeout(200)
    record('Imported bathroom value cannot execute HTML in cards',lambda:yes(not value(page,"Boolean(window.__qaBathMarkup)"),'Harmless bathroom-field HTML canary executed in grid rendering'),'P1')
    ctx.close()
    browser.close()

server.shutdown()
report={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
 'scope':'Current index.html and real saved inventory; browser authentication/database are mocks, remote writes and providers are blocked.',
 'live_authentication_or_rls_tested':False,'live_photo_or_maps_availability_tested':False,
 'product_code_changed':False,'results':RESULTS,'metrics':METRICS,
 'summary':{'checks':len(RESULTS),'passed':sum(r['status']=='pass' for r in RESULTS),'failed':sum(r['status']=='fail' for r in RESULTS)}}
(OUT/'browser-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('QA_BROWSER='+json.dumps(report,ensure_ascii=False))
