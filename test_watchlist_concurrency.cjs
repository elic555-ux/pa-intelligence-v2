/* Real watchlist handlers with delayed fake server responses. No network or live database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2] || path.join(__dirname, 'index.html'), 'utf8');
function actual(name) {
    let start = html.indexOf('function ' + name + '(');
    if (html.slice(start - 6, start) === 'async ') start -= 6;
    const firstLine = html.slice(start, html.indexOf('\n', start));
    if (/\{.*\}\s*$/.test(firstLine)) return firstLine;
    const end = html.indexOf('\n        }', start);
    assert.ok(start >= 0 && end > start, name);
    return html.slice(start, end + '\n        }'.length);
}
const functions = ['uiPropertyKey', 'isWatchlistedProperty', 'cloudActionAllowed',
    'toggleWatchlist', 'toggleWatchlistFromModal', 'updateModalWatchlistState', 'renderCards'].map(actual).join('\n');
const a = {id:'A', _uiKey:'ui-A', address:'1 First St'};
const b = {id:'B', _uiKey:'ui-B', address:'2 Second St'};
function setup({selected=a, watched=[], view='table', applyOnRequest=true}={}) {
    const requests = [], server = new Set(watched), alerts = [], elements = new Map();
    function element(id) {
        if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', disabled:false, value:'',
            attributes:{}, setAttribute(name,value){ this.attributes[name] = value; }});
        return elements.get(id);
    }
    const c = vm.createContext({Map, Set, Date, URL,
        rawProperties:[a,b], currentSelectedProperty:selected, userWatchlist:new Set(watched),
        watchlistPendingIds:new Set(), cloudReady:{watchlist:true}, pipelineDeals:[], archiveDeals:{},
        currentViewMode:view, latestNewFilterActive:false, viewedDeals:new Set(), activeCategory:'all',
        document:{getElementById:element}, lucide:{createIcons(){}}, alert:message=>alerts.push(message),
        updateWatchlistCountDisplay(){}, getFilteredProperties:()=>c.rawProperties.slice(),
        updateResultSummary(){}, parseDate:()=>0, getMarketAskingPrice:()=>100000,
        financialSnapshot:()=>({arvMethod:'none',maoFlip:null}), hasRecordedPriceDrop:()=>false,
        propertyCityDisplay:()=>'', escapeHtml:value=>String(value),
        propertyStatusDisplayHtml:()=>'', propertyPriceDisplayHtml:()=>'', priceDropDetailsHtml:()=>'',
        livingAreaDisplayHtml:()=>'', featureDisplayHtml:()=>'', financialDisplayHtml:()=>'',
        propertyDateDisplayHtml:()=>'', neighborhoodBadgeHtml:()=>'', analysisMoneyData:()=>'',
        financialExplanationHtml:()=>'',
        supabaseClient:{from:table=>{
            assert.equal(table,'user_watchlist');
            function request(kind,id) {
                let resolve, reject;
                const promise = new Promise((yes,no)=>{resolve=yes;reject=no;});
                const item = {kind,id,done:false,applied:false,
                    apply(){ if (!this.applied) { kind === 'add' ? server.add(id) : server.delete(id); this.applied=true; } },
                    respond(error=null){ this.done=true; resolve({error}); },
                    reject(){ this.done=true; reject(new Error('simulated network failure')); }};
                requests.push(item);
                if (applyOnRequest) item.apply();
                return promise;
            }
            return {upsert:data=>request('add',data.property_id),
                delete:()=>({eq:(field,id)=>{assert.equal(field,'property_id');return request('remove',id);}})};
        }}
    });
    vm.runInContext(functions,c);
    return {c,requests,server,alerts,element};
}
function watchButton(harness,key) {
    const tags = harness.element('properties-container').innerHTML.match(/<button\b[^>]*>/g) || [];
    const tag = tags.find(text=>text.includes("toggleWatchlist('" + key + "'"));
    assert.ok(tag, 'rendered watchlist control exists');
    return tag;
}
let passed=0;
async function check(name, run) { await run(); passed++; console.log('PASS',name); }
(async()=>{
    // Compile every inline script too: extraction must not hide syntax regressions.
    for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
        if (!/\bsrc=/.test(match[1])) new vm.Script(match[2]);
    }
    await check('late duplicate add cannot undo a completed remove in the UI',async()=>{
        const h=setup(), first=h.c.toggleWatchlist('ui-A'), duplicate=h.c.toggleWatchlist('ui-A');
        const initialAdds=h.requests.filter(r=>r.kind==='add');
        h.requests[0].respond(); await first;
        const remove=h.c.toggleWatchlist('ui-A');
        const removeRequest=h.requests.find(r=>r.kind==='remove');
        assert.ok(removeRequest);
        removeRequest.respond(); await remove;
        // The unguarded baseline has a second add whose server commit happened
        // before the delete, but whose response arrives after it.
        for (const late of initialAdds.slice(1)) late.respond();
        await duplicate;
        assert.equal(h.server.has('A'),false);
        assert.equal(h.c.userWatchlist.has('A'),false,'UI must match the already deleted server row');
        assert.equal(initialAdds.length,1,'one request per property while a response is pending');
    });
    await check('table, card and modal pending state clears after success',async()=>{
        for (const view of ['table','grid']) {
            const h=setup({view});
            const pending=h.c.toggleWatchlist('ui-A');
            assert.match(watchButton(h,'ui-A'),/\sdisabled(?:\s|>)/);
            assert.match(watchButton(h,'ui-A'),/aria-busy="true"/);
            assert.doesNotMatch(watchButton(h,'ui-B'),/\sdisabled(?:\s|>)/);
            assert.equal(h.element('modal-watchlist-btn').disabled,true);
            assert.equal(h.element('modal-watchlist-btn').attributes['aria-busy'],'true');
            h.requests[0].respond(); await pending;
            assert.doesNotMatch(watchButton(h,'ui-A'),/\sdisabled(?:\s|>)/);
            assert.match(watchButton(h,'ui-A'),/aria-busy="false"/);
            assert.equal(h.element('modal-watchlist-btn').disabled,false);
            assert.equal(h.element('modal-watchlist-btn').attributes['aria-busy'],'false');
            assert.equal(h.c.watchlistPendingIds.size,0);
        }
    });
    await check('different properties can save concurrently without blocking each other',async()=>{
        const h=setup();
        const first=h.c.toggleWatchlist('ui-A'), other=h.c.toggleWatchlist('ui-B');
        assert.equal(h.requests.length,2);
        assert.deepEqual(h.requests.map(r=>r.id),['A','B']);
        h.requests[1].respond(); await other;
        assert.equal(h.c.watchlistPendingIds.has('A'),true);
        assert.equal(h.c.watchlistPendingIds.has('B'),false);
        h.requests[0].respond(); await first;
        assert.deepEqual([...h.c.userWatchlist].sort(),['A','B']);
    });
    await check('server error releases the lock and preserves the previous selection',async()=>{
        for (const watched of [[],['A']]) {
            const h=setup({watched,applyOnRequest:false});
            const failed=h.c.toggleWatchlist('ui-A');
            h.requests[0].respond({message:'simulated rejection'}); await failed;
            assert.equal(h.c.userWatchlist.has('A'),watched.includes('A'));
            assert.equal(h.c.watchlistPendingIds.size,0);
            assert.equal(h.element('modal-watchlist-btn').disabled,false);
            assert.equal(h.alerts.length,1);
            const retry=h.c.toggleWatchlist('ui-A');
            assert.equal(h.requests.length,2);
            h.requests[1].apply(); h.requests[1].respond(); await retry;
            assert.equal(h.c.userWatchlist.has('A'),!watched.includes('A'));
            assert.equal(h.server.has('A'),!watched.includes('A'));
        }
    });
    await check('network rejection and synchronous provider exception also release the lock',async()=>{
        const h=setup({applyOnRequest:false});
        const rejected=h.c.toggleWatchlist('ui-A');
        h.requests[0].reject(); await rejected;
        assert.equal(h.c.watchlistPendingIds.size,0);
        const provider=h.c.supabaseClient;
        h.c.supabaseClient={from(){throw new Error('simulated provider initialization failure');}};
        await h.c.toggleWatchlist('ui-A');
        assert.equal(h.c.watchlistPendingIds.size,0);
        h.c.supabaseClient=provider;
        const retry=h.c.toggleWatchlist('ui-A');
        h.requests[1].apply(); h.requests[1].respond(); await retry;
        assert.equal(h.c.userWatchlist.has('A'),true);
        assert.equal(h.alerts.length,2);
    });
    await check('modal and row clicks share the same property lock',async()=>{
        const h=setup();
        h.c.toggleWatchlistFromModal();
        const repeat=h.c.toggleWatchlist('ui-A');
        await repeat;
        assert.equal(h.requests.length,1);
        h.requests[0].respond();
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(h.c.watchlistPendingIds.size,0);
        assert.equal(h.c.userWatchlist.has('A'),true);
    });
    await check('switching modal properties during a save keeps the new property usable',async()=>{
        const h=setup();
        const pending=h.c.toggleWatchlist('ui-A');
        h.c.currentSelectedProperty=b;
        h.c.updateModalWatchlistState();
        assert.equal(h.element('modal-watchlist-btn').disabled,false);
        assert.equal(h.element('modal-watchlist-btn').attributes['aria-busy'],'false');
        h.requests[0].respond(); await pending;
        assert.equal(h.c.currentSelectedProperty,b);
        assert.equal(h.element('modal-watchlist-btn').disabled,false);
        assert.equal(h.c.userWatchlist.has('B'),false);
    });
    await check('unavailable and ambiguous records still cause no mutations',async()=>{
        const h=setup();
        h.c.cloudReady.watchlist=false;
        await h.c.toggleWatchlist('ui-A');
        h.c.cloudReady.watchlist=true;
        h.c.rawProperties=[{...a,_idAmbiguous:true},b];
        await h.c.toggleWatchlist('ui-A');
        assert.equal(h.requests.length,0);
        assert.equal(h.c.watchlistPendingIds.size,0);
    });
    console.log(passed+' watchlist concurrency checks passed; no live requests.');
})().catch(error=>{console.error(error);process.exitCode=1;});

