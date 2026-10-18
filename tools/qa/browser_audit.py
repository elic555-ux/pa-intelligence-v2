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
PROFILE=json.loads((ROOT/'scan_config.json').read_text())
CEILING=float(PROFILE['maxPrice'])
BACKGROUND_PRICE=CEILING+5000
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
window.__qaWrites=[];window.__qaDb={};window.__qaWriteFail=false;
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
 upsert:async data=>{window.__qaWrites.push({table,operation:'upsert',data});return{error:window.__qaWriteFail?{message:'QA rejected save'}:null};},
 delete:()=>({eq:async(key,value)=>{window.__qaWrites.push({table,operation:'delete',key,value});return{error:window.__qaWriteFail?{message:'QA rejected save'}:null};}})
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
    for i,(word,price,kind) in enumerate([('Alpha',90000,'Single Family'),('Beta',120000,'Multi-Family'),('Gamma',160000,'Single Family'),('Unknown',None,'Single Family'),('Background',BACKGROUND_PRICE,'Single Family')],1):
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

def scenario(browser,rows=None,mode='owner',mobile=False,cloud_fail=False,inventory_fail=False,images_fail=False,api_limit=None,inventory_mode=None,sheriff_fail=False):
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
                if inventory_mode=='invalid_json':req.fulfill(content_type='application/json',body='{');return
                if inventory_mode=='not_array':req.fulfill(content_type='application/json',body='{"rows":[]}');return
                if rows is not None:req.fulfill(content_type='application/json',body=json.dumps(rows));return
            if parsed.path=='/sheriff_listings.json' and sheriff_fail:
                req.fulfill(status=503,body='QA sheriff unavailable');return
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
    record('Default board respects configured ceiling',lambda:yes(value(page,"!getFilteredProperties().some(p=>p.id==='QA-5')"),'Above-budget listing shown'))
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
    METRICS['map_destinations']={'street_view':page.locator('#modal-property-map-link').get_attribute('href'),'map':page.locator('#modal-footer-map-link').get_attribute('href')}
    record('Map footer points to Google Maps over HTTPS',lambda:yes(urlsplit(METRICS['map_destinations']['map']).scheme=='https' and urlsplit(METRICS['map_destinations']['map']).hostname=='www.google.com' and urlsplit(METRICS['map_destinations']['map']).path.startswith('/maps/'),'Map destination incorrect'))
    record('Street View uses the dedicated local page with property address',lambda:yes(urlsplit(METRICS['map_destinations']['street_view']).path=='street-view.html' and 'QA+Alpha' in METRICS['map_destinations']['street_view'],'Street View destination incorrect'))
    record('Main modal exposes dialog semantics',lambda:yes(page.locator('#property-modal').get_attribute('role')=='dialog' and page.locator('#property-modal').get_attribute('aria-modal')=='true' and page.locator('#property-modal').get_attribute('aria-labelledby')=='modal-address','Property modal lacks dialog semantics or accessible name'))
    record('Dialog opening places focus on the property title',lambda:yes(value(page,"document.activeElement.id==='modal-address'"),'Initial focus stays outside the dialog'))
    record('Dialog background is inert while property details are open',lambda:yes(value(page,"Array.from(document.getElementById('property-modal').parentElement.children).filter(n=>n.id!=='property-modal').every(n=>n.inert)"),'Background can still receive focus or interaction'))
    page.evaluate("propertyModalFocusables().at(-1).focus()")
    page.keyboard.press('Tab')
    record('Tab wraps from the last dialog control to the first',lambda:yes(value(page,"document.activeElement===propertyModalFocusables()[0]"),'Tab escaped the dialog'))
    page.keyboard.press('Shift+Tab')
    record('Shift-Tab wraps from the first dialog control to the last',lambda:yes(value(page,"document.activeElement===propertyModalFocusables().at(-1)"),'Shift-Tab escaped the dialog'))
    page.keyboard.press('Escape')
    record('Escape closes the property dialog',lambda:yes(page.locator('#property-modal').is_hidden(),'Escape does not close property modal'))
    record('Closing the dialog restores background interaction',lambda:yes(value(page,"!document.getElementById('app-header').inert"),'Background remains inert after close'))
    page.evaluate('closePropertyModal()')
    page.locator('#btn-view-grid').click()
    record('Grid and table preserve the filtered result count',lambda:yes(value(page,"document.querySelectorAll('#properties-container>div>div').length===getFilteredProperties().length"),'View switch count differs'))
    page.screenshot(path=str(OUT/'desktop-grid.png'))
    print('QA_VISUAL_DESKTOP='+base64.b64encode(page.screenshot(type='jpeg',quality=50)).decode())
    card_opener=page.locator('#properties-container [data-property-open]').first
    opener_key=card_opener.get_attribute('data-property-open')
    card_opener.focus()
    before_generation=value(page,'basicSourceGeneration')
    page.keyboard.press('Enter')
    record('Card property title opens details with Enter exactly once',lambda:yes(value(page,"!document.getElementById('property-modal').classList.contains('hidden')") and value(page,'basicSourceGeneration')==before_generation+1,'Card lacks a native keyboard opener or opens twice'))
    page.keyboard.press('Escape')
    record('Closing card details restores focus after cards rerender',lambda:yes(value(page,"document.activeElement.dataset.propertyOpen") == opener_key,'Focus did not return to the property opener'))
    record('Desktop page avoids document-level horizontal overflow',lambda:yes(value(page,"document.documentElement.scrollWidth<=innerWidth+2"),'Desktop document overflows'))
    record('Read-only browsing makes no network write requests',lambda:yes(traffic['remote_write_attempts']==0,'Network writes attempted'),'P1')
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,None)
    METRICS['actual_data_summary']=value(page,"({loaded:rawProperties.length,database:databaseProperties.length,summary:dataLoadSummary,displayed:getFilteredProperties().length})")
    record('Complete current saved inventory renders',lambda:yes(value(page,"rawProperties.length>0&&databaseProperties.length>=rawProperties.length"),'Saved inventory not rendered'),'P1')
    record('Complete saved inventory causes no JavaScript exception',lambda:yes(not errors,str(errors)),'P1')
    METRICS['public_api_reads_on_boot']=traffic['github_reads']
    METRICS['board_scan_price_range']=value(page,'boardScanPriceRange')
    METRICS['fixture_background_price']=BACKGROUND_PRICE
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
        if engine=='webkit':print('QA_VISUAL_MOBILE='+base64.b64encode(page.screenshot(type='jpeg',quality=60)).decode())
        record(engine+' mobile modal stays within viewport width',lambda:yes(value(page,"document.getElementById('property-modal').getBoundingClientRect().width<=innerWidth+2"),'Mobile modal exceeds viewport'))
        page.keyboard.press('Escape')
        record(engine+' mobile Escape closes property details',lambda:yes(page.locator('#property-modal').is_hidden(),'Mobile dialog did not close'))
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


    # All SDK writes below remain in memory; every actual POST/DELETE is blocked.
    ctx,page,errors,traffic=scenario(browser,fixture_rows())
    page.evaluate("async()=>{await toggleWatchlist(uiPropertyKey(rawProperties[0]));}")
    record('Watchlist add updates the in-memory UI only after acknowledgment',lambda:yes(value(page,"userWatchlist.has('QA-1')&&window.__qaWrites[0].table==='user_watchlist'&&window.__qaWrites[0].operation==='upsert'"),'Watchlist add did not acknowledge'),'P1')
    page.evaluate("window.__qaWriteFail=true")
    page.evaluate("async()=>{await toggleWatchlist(uiPropertyKey(rawProperties[0]));}")
    record('Rejected watchlist removal preserves prior selection',lambda:yes(value(page,"userWatchlist.has('QA-1')"),'Failed save discarded watchlist selection'),'P1')
    page.evaluate("window.__qaWriteFail=false")
    page.evaluate("async()=>{await toggleWatchlist(uiPropertyKey(rawProperties[0]));}")
    record('Acknowledged watchlist removal updates the selection',lambda:yes(value(page,"!userWatchlist.has('QA-1')"),'Acknowledged removal not reflected'),'P1')
    record('Watchlist tests send no real write requests',lambda:yes(traffic['remote_write_attempts']==0,'Unexpected real network write'),'P1')
    ctx.close()

    # Simulated API quota still permits the existing local JSON fallback.
    ctx,page,errors,traffic=scenario(browser,None,api_limit=2)
    page.evaluate("openPropertyModal('PA-MLS-1774373')")
    page.wait_for_timeout(900)
    record('GitHub API 403 retains the board without JavaScript exceptions',lambda:yes(not errors and value(page,"rawProperties.length>0"),str(errors)),'P1')
    record('GitHub API 403 still loads saved photo links through local fallback',lambda:yes(page.locator('#modal-additional-source-photos img').count()==3,'Cached saved photo links unavailable during API limit'))
    METRICS['api_limit_fallback']={'github_reads':traffic['github_reads'],'gallery_images':page.locator('#modal-additional-source-photos img').count()}
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,None,images_fail=True)
    page.evaluate("openPropertyModal('PA-MLS-1774373')")
    page.wait_for_timeout(500)
    gallery=page.locator('#modal-additional-source-photos')
    if gallery.count():gallery.scroll_into_view_if_needed()
    page.wait_for_timeout(1200)
    record('Failed photo loads offer the source link and do not claim scanner failure',lambda:yes(page.locator('#modal-photo-load-status').count()==1 and 'התמונות לא נטענו' in page.locator('#modal-photo-load-status').inner_text() and 'הסריקה נכשלה' not in page.locator('#modal-source-coverage').inner_text(),'Image failure fallback text incorrect'))
    ctx.close()


    ctx,page,errors,traffic=scenario(browser,fixture_rows())
    open_first(page)
    page.evaluate("async()=>{sessionStorage.setItem('qa-auth-mode','none'); await verifyPaAccess();}")
    page.wait_for_function("!document.getElementById('pa-login-form').hidden")
    page.locator('#pa-login-email').focus()
    page.keyboard.press('Tab')
    record('Authentication expiry while a dialog was open keeps login keyboard usable',lambda:yes(value(page,"document.activeElement.id==='pa-login-password'") and not errors,'Hidden property dialog trapped login keyboard focus'),'P1')
    ctx.close()

    for payload_kind in ('invalid_json','not_array'):
        ctx,page,errors,traffic=scenario(browser,fixture_rows(),inventory_mode=payload_kind)
        record(payload_kind+' inventory is rejected with a readable error',lambda:yes('לא נטענו' in page.locator('#properties-container').inner_text() and not errors,'Malformed inventory was accepted or crashed the page'),'P1')
        ctx.close()

    ctx,page,errors,traffic=scenario(browser,fixture_rows(),sheriff_fail=True)
    record('Unavailable sheriff file retains the main property inventory',lambda:yes(value(page,"rawProperties.length===5&&dataLoadSummary.sheriffUnavailable===true") and not errors,'Sheriff failure discarded the main inventory'),'P1')
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,[])
    record('Empty inventory shows zero results without a false loading failure',lambda:yes(value(page,"rawProperties.length===0") and 'לא נטענו' not in page.locator('#properties-container').inner_text() and not errors,'Empty inventory treated as failed or non-empty'))
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,[None,'invalid-row',{},fixture_rows()[0]])
    record('Invalid rows do not prevent a valid property from rendering',lambda:yes(value(page,"rawProperties.length===1&&rawProperties[0].id==='QA-1'") and not errors,'Malformed rows interrupted valid inventory'),'P1')
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,fixture_rows())
    page.evaluate("""() => {
        const originalSetItem=Storage.prototype.setItem;
        Storage.prototype.setItem=function(key,value){
            if(key==='pa_viewed_deals')throw new DOMException('QA storage full','QuotaExceededError');
            return originalSetItem.call(this,key,value);
        };
    }""")
    def opens_when_storage_full():
        page.locator('#properties-container [data-property-open]').first.click()
        return yes(page.locator('#property-modal').is_visible() and not errors,'Local storage failure stopped property details: '+str(errors))
    record('Full local storage does not prevent opening property details',opens_when_storage_full)
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,fixture_rows())
    page.evaluate("localStorage.setItem('pa_viewed_deals','{not-json'); localStorage.setItem('pa_main_geo_selection_v1','{not-json')")
    page.reload(wait_until='load')
    page.wait_for_function("rawProperties.length===5")
    record('Corrupt viewed and geographic preferences do not prevent startup',lambda:yes(not errors and page.locator('#pa-app-shell').is_visible(),'Corrupt local preferences crashed startup'))
    ctx.close()

    probe=fixture_rows()[:1]
    probe[0]['url']='javascript:window.__qaBadSource=true'
    ctx,page,errors,traffic=scenario(browser,probe)
    open_first(page)
    record('Non-HTTPS source link is hidden in property details',lambda:yes(page.locator('#modal-generic-source-link').is_hidden() and not value(page,"Boolean(window.__qaBadSource)"),'Unsafe source URL remains actionable'),'P1')
    ctx.close()

    ctx,page,errors,traffic=scenario(browser,fixture_rows())
    page.evaluate("window.__qaWriteFail=true")
    page.evaluate("async()=>{await toggleWatchlist(uiPropertyKey(rawProperties[1]));}")
    record('Rejected watchlist addition is not shown as saved',lambda:yes(value(page,"!userWatchlist.has('QA-2')"),'Failed addition appeared saved'),'P1')
    open_first(page)
    page.evaluate("async()=>{await archiveFromModal();}")
    record('Rejected archive write leaves the property on the board',lambda:yes(value(page,"!rawProperties[0].is_archived&&!archiveDeals['QA-1']"),'Rejected archive write removed a property'),'P1')
    page.evaluate("window.__qaWriteFail=false")
    page.evaluate("async()=>{await archiveFromModal();}")
    record('Acknowledged archive records the property identity in memory',lambda:yes(value(page,"rawProperties[0].is_archived&&Boolean(archiveDeals['QA-1'].identity_key)"),'Archive did not retain a safe identity binding'),'P1')
    record('Archive tests send no real write requests',lambda:yes(traffic['remote_write_attempts']==0,'Archive test attempted a network write'),'P1')
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
 'application_source_scope':'Checked-out QA branch with candidate fixes; not deployed to production',
 'live_product_code_changed':False,'results':RESULTS,'metrics':METRICS,
 'summary':{'checks':len(RESULTS),'passed':sum(r['status']=='pass' for r in RESULTS),'failed':sum(r['status']=='fail' for r in RESULTS)}}
(OUT/'browser-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('QA_BROWSER='+json.dumps(report,ensure_ascii=False))
