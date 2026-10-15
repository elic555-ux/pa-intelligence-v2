/* Saved selections must never attach to a different property sharing an ID. Entirely offline. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
function actual(name) {
    let start = html.indexOf('function ' + name + '(');
    if (html.slice(start - 6, start) === 'async ') start -= 6;
    const end = html.indexOf('\n        }', start);
    assert.ok(start >= 0 && end > start, name);
    return html.slice(start, end + '\n        }'.length);
}
const functions = ['numericData', 'identityText', 'propertyIdentity', 'uiPropertyKey',
    'parseDate', 'propertyCheckedTime', 'propertySourceFamily', 'propertyMlsListingKey', 'preparePropertyRecords',
    'savedPropertyMatches', 'isPipelineProperty', 'isWatchlistedProperty',
    'cloudActionAllowed', 'moveToPipelineFromModal', 'archiveFromModal',
    'getMarketAskingPrice', 'loadProperties'].map(actual).join('\n');
const first = {id:'PA-MLS-1772710', source_type:'mls', address:'5201 Glenwood Ave', city:'Pittsburgh',
    county:'Allegheny', zip:'15207', price:110000, beds:3, sqft:1200};
const second = {...first, address:'5201 Glenwwod Ave', price:100000};
function setup(rows = [first]) {
    const calls = [], alerts = [], elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {textContent:'', innerHTML:'', hidden:false, value:''});
        return elements.get(id);
    };
    const context = vm.createContext({URL, Date, Map, Set,
        document:{getElementById:element, querySelectorAll:()=>[]},
        fetch:async path => ({ok:true, json:async()=>path.startsWith('properties.json') ? rows : []}),
        loadScannerStatus:async()=>{}, archiveDeals:{}, cloudReady:{archive:true,pipeline:true,watchlist:true}, cloudErrors:[],
        pipelineDeals:[], userWatchlist:new Set(), isTaxProperty:()=>false,
        hasRequiredPropertyDetails:()=>true, applyFinancialSnapshot:()=>{}, recordedPriceDrops:()=>[],
        populateCityDropdown:()=>{}, updateCounts:()=>{}, renderCards:()=>{},
        updateWatchlistCountDisplay:()=>{}, renderDataHealth:()=>{}, closePropertyModal:()=>{},
        alert:message=>alerts.push(message),
        supabaseClient:{from:table=>({
            upsert:async data=>{calls.push({table,operation:'upsert',data});return {error:null};},
            delete:()=>({eq:async(field,value)=>{calls.push({table, field, value}); return {error:null};}})
        })}
    });
    vm.runInContext(functions, context);
    return {context, calls, alerts};
}
let passed = 0;
async function check(name, test) { await test(); passed++; console.log('PASS', name); }
(async()=>{
    await check('saved pipeline snapshot cannot hide another address with the same current unique ID', ()=>{
        const {context:c} = setup();
        c.pipelineDeals = [first];
        const current = c.preparePropertyRecords([second]).records[0];
        assert.equal(current._idAmbiguous, false);
        assert.equal(c.isPipelineProperty(current), false);
    });
    await check('removing one ambiguous sibling does not transfer a saved selection to the other', ()=>{
        const {context:c} = setup();
        const both = c.preparePropertyRecords([first, second]).records;
        assert.equal(both.every(p=>p._idAmbiguous), true);
        c.pipelineDeals = [{...first, _identityKey:c.propertyIdentity(first)}];
        const remaining = c.preparePropertyRecords([second]).records[0];
        assert.equal(c.isPipelineProperty(remaining), false);
    });
    await check('same property still matches by identity across record IDs and normalized address spelling', ()=>{
        const {context:c} = setup();
        c.pipelineDeals = [{...first, id:'legacy-id', address:' 5201 GLENWOOD AVE. '}];
        assert.equal(c.isPipelineProperty(c.preparePropertyRecords([first]).records[0]), true);
    });
    await check('a saved explicit key or address conflict cannot fall back to ID', ()=>{
        const {context:c} = setup();
        const current = c.preparePropertyRecords([first]).records[0];
        assert.equal(c.savedPropertyMatches(current,{id:first.id,_identityKey:c.propertyIdentity(second)}),false);
        assert.equal(c.savedPropertyMatches(current,{...second,_identityKey:current._identityKey}),false);
        assert.equal(c.savedPropertyMatches(current,{id:first.id,identity_key:current._identityKey,
            _identityKey:c.propertyIdentity(second)}),false);
    });
    await check('legacy ID-only selections work only with a nonempty unambiguous current ID', ()=>{
        const {context:c} = setup();
        const current = c.preparePropertyRecords([first]).records[0];
        assert.equal(c.savedPropertyMatches(current,{id:first.id}),true);
        assert.equal(c.savedPropertyMatches({...current,_idAmbiguous:true},{id:first.id}),false);
        assert.equal(c.savedPropertyMatches({...current,id:''},{}),false);
        assert.equal(c.savedPropertyMatches(current,null),false);
    });
    await check('explicit identity selects its own ambiguous sibling; ID-only watchlist remains blocked', ()=>{
        const {context:c} = setup();
        const [a,b] = c.preparePropertyRecords([first,second]).records;
        c.pipelineDeals=[first]; c.userWatchlist.add(first.id);
        assert.equal(c.isPipelineProperty(a),true);
        assert.equal(c.isPipelineProperty(b),false);
        assert.equal(c.isWatchlistedProperty(a),false);
        assert.equal(c.isWatchlistedProperty(b),false);
    });
    await check('a mismatched saved archive neither hides a different property nor consumes the old archive', async()=>{
        const {context:c,calls} = setup([second]);
        const saved={price:110000,identity_key:c.propertyIdentity(first),reason:'user_manual'};
        c.archiveDeals[first.id]=saved;
        await c.loadProperties();
        assert.equal(c.databaseProperties[0].is_archived,false);
        assert.equal(c.archiveDeals[first.id],saved);
        assert.equal(calls.length,0);
        assert.equal(c.databaseProperties[0].price_drop_history,undefined);
    });
    await check('matching archive remains archived when its own price does not drop', async()=>{
        const {context:c,calls} = setup();
        c.archiveDeals[first.id]={price:110000,identity_key:c.propertyIdentity(first)};
        await c.loadProperties();
        assert.equal(c.databaseProperties[0].is_archived,true);
        assert.equal(calls.length,0);
    });
    await check('matching archive still restores its own property after a price drop', async()=>{
        const {context:c,calls} = setup([{...first,price:100000}]);
        c.archiveDeals[first.id]={price:110000,identity_key:c.propertyIdentity(first)};
        await c.loadProperties();
        assert.equal(c.databaseProperties[0].is_archived,false);
        assert.equal(c.archiveDeals[first.id],undefined);
        assert.equal(calls.length,1);
        assert.equal(c.databaseProperties[0].price_drop_history[0].previous_price,110000);
    });
    await check('legacy archive retains ID-only behavior for a unique property but not ambiguous siblings', async()=>{
        const {context:c,calls} = setup();
        c.archiveDeals[first.id]={price:110000};
        await c.loadProperties();
        assert.equal(c.databaseProperties[0].is_archived,true);
        assert.equal(calls.length,0);
        const ambiguous=setup([first,second]);
        ambiguous.context.archiveDeals[first.id]={price:110000};
        await ambiguous.context.loadProperties();
        assert.equal(ambiguous.context.databaseProperties.every(p=>!p.is_archived),true);
        assert.equal(ambiguous.calls.length,0);
    });
    await check('pipeline and archive actions cannot overwrite conflicting saved identities', async()=>{
        const {context:c,calls,alerts} = setup([second]);
        c.currentSelectedProperty=c.preparePropertyRecords([second]).records[0];
        const savedDeal={...first,_identityKey:c.propertyIdentity(first)};
        const savedArchive={price:110000,identity_key:c.propertyIdentity(first)};
        c.pipelineDeals=[savedDeal]; c.archiveDeals[first.id]=savedArchive;
        await c.moveToPipelineFromModal();
        await c.archiveFromModal();
        assert.equal(calls.length,0);
        assert.equal(c.pipelineDeals[0],savedDeal);
        assert.equal(c.archiveDeals[first.id],savedArchive);
        assert.equal(alerts.length,2);
        assert.equal(alerts.every(message=>message.includes('בעל זהות אחרת')),true);
    });
    await check('matching saved identities allow archive updates and pipeline access', async()=>{
        const {context:c,calls} = setup();
        c.currentSelectedProperty=c.preparePropertyRecords([first]).records[0];
        c.pipelineDeals=[first];
        c.archiveDeals[first.id]={price:110000,identity_key:c.propertyIdentity(first)};
        assert.equal(c.cloudActionAllowed(c.currentSelectedProperty,'pipeline'),true);
        assert.equal(c.cloudActionAllowed(c.currentSelectedProperty,'archive'),true);
        await c.archiveFromModal();
        assert.equal(calls[0].operation,'upsert');
        assert.equal(calls[0].data.archive_data.identity_key,c.propertyIdentity(first));
    });
    await check('legacy ID-only pipeline selection remains viewable without duplicating or overwriting it', async()=>{
        const {context:c,calls} = setup();
        c.currentSelectedProperty=c.preparePropertyRecords([first]).records[0];
        const legacy={id:first.id}; c.pipelineDeals=[legacy];
        assert.equal(c.isPipelineProperty(c.currentSelectedProperty),true);
        await c.moveToPipelineFromModal();
        assert.equal(calls.length,0);
        assert.equal(c.pipelineDeals[0],legacy);
    });
    await check('new additions and legacy archive updates remain available with a unique current ID', async()=>{
        const {context:c,calls} = setup();
        c.currentSelectedProperty=c.preparePropertyRecords([first]).records[0];
        await c.moveToPipelineFromModal();
        assert.equal(calls[0].table,'pipeline_deals');
        assert.equal(calls[0].operation,'upsert');
        c.archiveDeals[first.id]={price:110000};
        await c.archiveFromModal();
        assert.equal(calls[1].table,'archive_deals');
        assert.equal(calls[1].operation,'upsert');
    });
    await check('missing ready flags and missing saved datasets pause actions without writes', async()=>{
        for (const kind of ['pipeline','archive']) {
            const {context:c,calls} = setup();
            c.currentSelectedProperty=c.preparePropertyRecords([first]).records[0];
            for (const readiness of [{},null]) {
                c.cloudReady=readiness;
                assert.equal(c.cloudActionAllowed(c.currentSelectedProperty,kind),false);
            }
            c.cloudReady={pipeline:true,archive:true};
            if (kind==='pipeline') c.pipelineDeals=null; else c.archiveDeals=undefined;
            assert.equal(c.cloudActionAllowed(c.currentSelectedProperty,kind),false);
            if (kind==='pipeline') {
                assert.equal(c.isPipelineProperty(c.currentSelectedProperty),false);
                await c.moveToPipelineFromModal();
            } else await c.archiveFromModal();
            assert.equal(calls.length,0);
        }
    });
    await check('newer MLS listing owns the displayed ID, source details and history for the same Redfin home', ()=>{
        const {context:c} = setup();
        const older={...first,id:'PA-MLS-1001',docket_id:'MLS-1001',
            url:'https://www.redfin.com/PA/Pittsburgh/12-First-St-15216/home/77',
            last_source_check:'2026-10-13T10:00:00Z',beds:4,sqft:1500,
            price_history:[{price:150000}],price_drop_history:[{previous_price:160000,current_price:150000}],
            technical_facts:{roof_type:{value:'Asphalt',listing_id:'1001'}},
            listing_source_details:{listing_id:'1001'},listing_photos:[{url:'old.jpg'}],
            county_property_data:{year_built:1900},verified_arv:220000};
        const newer={...first,id:'PA-MLS-1002',docket_id:'MLS-1002',url:older.url,
            last_source_check:'2026-10-15T10:00:00Z',price:140000,beds:null,sqft:null,
            price_history:[{price:140000}],source:'Current MLS'};
        const before=JSON.stringify([older,newer]);
        for (const rows of [[older,newer],[newer,older]]) {
            const result=c.preparePropertyRecords(rows);
            assert.equal(result.records.length,1);
            const current=result.records[0];
            assert.equal(current.id,newer.id);
            assert.equal(current.docket_id,newer.docket_id);
            assert.equal(current.source,'Current MLS');
            assert.deepEqual(JSON.parse(JSON.stringify(current.price_history)),newer.price_history);
            assert.equal(current.beds,null);
            assert.equal(current.sqft,null);
            for (const field of ['price_drop_history','technical_facts','listing_source_details',
                'listing_photos','county_property_data','verified_arv']) assert.equal(current[field],undefined,field);
        }
        assert.equal(JSON.stringify([older,newer]),before);
    });
    await check('legacy MLS rows with no source_type still separate listing provenance at the display boundary', ()=>{
        const {context:c} = setup();
        const older={...first,source_type:undefined,id:'PA-MLS-1001',docket_id:'MLS-1001',
            last_seen:'2026-10-13T10:00:00Z',technical_facts:{roof_type:{value:'Old roof'}}};
        const newer={...first,id:'PA-MLS-1002',docket_id:'MLS-1002',last_seen:'2026-10-15T10:00:00Z'};
        const current=c.preparePropertyRecords([older,newer]).records[0];
        assert.equal(current.id,newer.id);
        assert.equal(current.docket_id,newer.docket_id);
        assert.equal(current.technical_facts,undefined);
    });
    await check('same MLS docket with different legacy row IDs retains primary ID and same-listing facts', ()=>{
        const {context:c} = setup();
        const older={...first,id:'legacy-row-id',docket_id:'MLS-1001',
            last_source_check:'2026-10-13T10:00:00Z',technical_facts:{roof_type:{value:'Asphalt'}}};
        const newer={...first,id:'PA-MLS-1001',docket_id:'MLS-1001',
            last_source_check:'2026-10-15T10:00:00Z',price:100000,beds:null};
        const current=c.preparePropertyRecords([older,newer]).records[0];
        assert.equal(current.id,older.id);
        assert.equal(current.price,100000);
        assert.equal(current.beds,first.beds);
        assert.equal(current.technical_facts.roof_type.value,'Asphalt');
        assert.deepEqual(Array.from(current._sourceIds),[older.id,newer.id]);
    });
    await check('ordinary updates of the same MLS retain their existing history and null-field fallback behavior', ()=>{
        const {context:c} = setup();
        const older={...first,docket_id:'MLS-1772710',last_seen:'2026-10-13T10:00:00Z',
            technical_facts:{roof_type:{value:'Asphalt'}},price_history:[{price:110000}]};
        const newer={...first,docket_id:'MLS-1772710',last_seen:'2026-10-15T10:00:00Z',beds:null,
            price:100000,price_history:[{price:110000},{price:100000}]};
        const current=c.preparePropertyRecords([older,newer]).records[0];
        assert.equal(current.id,first.id);
        assert.equal(current.beds,first.beds);
        assert.equal(current.technical_facts.roof_type.value,'Asphalt');
        assert.deepEqual(JSON.parse(JSON.stringify(current.price_history)),newer.price_history);
    });
    await check('listing key follows registry field precedence and keeps unverified IDs separate', ()=>{
        const {context:c} = setup();
        assert.equal(c.propertyMlsListingKey({...first,listing_id:'1001',docket_id:'MLS-9999'}),'mls-1001');
        assert.equal(c.propertyMlsListingKey({...first,id:'legacy',docket_id:'MLS-1001'}),'mls-1001');
        assert.equal(c.propertyMlsListingKey({...first,id:'first',docket_id:'unknown'}),'id-first');
        assert.equal(c.propertyMlsListingKey({...first,id:'second',docket_id:'unknown'}),'id-second');
        assert.equal(c.propertyMlsListingKey({...first,source_type:'reo',deal_type:'MLS'}),null);
        assert.equal(c.propertyMlsListingKey({...first,source_type:undefined}), 'mls-1772710');
        const older={...first,id:'legacy-first',docket_id:'unknown',last_seen:'2026-10-13T10:00:00Z',listing_photos:['old']};
        const newer={...first,id:'legacy-second',docket_id:'unknown',last_seen:'2026-10-15T10:00:00Z'};
        const current=c.preparePropertyRecords([older,newer]).records[0];
        assert.equal(current.id,newer.id);
        assert.equal(current.listing_photos,undefined);
    });
    await check('cross-source rows sharing an address display one complete preferred source without mixing facts or history', ()=>{
        const {context:c} = setup();
        const mls={...first,docket_id:'MLS-1772710',last_seen:'2026-10-13T10:00:00Z',
            price_history:[{price:110000}],technical_facts:{roof_type:{value:'MLS roof'}},
            source_listing_description:'Old MLS description',county_property_data:{year_built:1900}};
        const hud={...first,id:'HUD-123',source_type:'hud',docket_id:'HUD-123',
            last_seen:'2026-10-15T10:00:00Z',price:100000,beds:null,sqft:null,
            price_history:[{price:100000}],source:'HUD'};
        assert.equal(c.propertyIdentity(mls),c.propertyIdentity(hud));
        const before=JSON.stringify([mls,hud]);
        for (const rows of [[mls,hud],[hud,mls]]) {
            const result=c.preparePropertyRecords(rows);
            assert.equal(result.records.length,1);
            const current=result.records[0];
            assert.equal(current.id,hud.id);
            assert.equal(current.source_type,'hud');
            assert.equal(current.docket_id,hud.docket_id);
            assert.deepEqual(JSON.parse(JSON.stringify(current.price_history)),hud.price_history);
            assert.equal(current.beds,null);
            assert.equal(current.sqft,null);
            for (const field of ['technical_facts','source_listing_description','county_property_data'])
                assert.equal(current[field],undefined,field);
        }
        assert.equal(JSON.stringify([mls,hud]),before);
    });
    await check('source families use explicit source types and legacy ID prefixes without guessing an unknown source', ()=>{
        const {context:c} = setup();
        assert.equal(c.propertySourceFamily({source_type:' HUD ',id:'PA-MLS-1001'}),'hud');
        for (const [id,family] of [['PA-MLS-1','mls'],['pa-reo-1','reo'],[' PA-SHERIFF-1 ','sheriff'],
            ['PA-TAX-1','tax'],['tax-erie-1','tax']]) assert.equal(c.propertySourceFamily({id}),family);
        assert.equal(c.propertySourceFamily({id:'legacy',docket_id:'MLS-1001',url:'https://www.redfin.com/x/home/1'}),'unknown:legacy');
        assert.equal(c.propertyMlsListingKey({id:'legacy',docket_id:'MLS-1001'}),null);
        assert.equal(c.propertySourceFamily({address:first.address}),null);
        const older={...first,source_type:undefined,id:'unknown-old',last_seen:'2026-10-13T10:00:00Z',
            technical_facts:{roof_type:{value:'Old'}}};
        const newer={...first,source_type:undefined,id:'unknown-new',last_seen:'2026-10-15T10:00:00Z'};
        const current=c.preparePropertyRecords([older,newer]).records[0];
        assert.equal(current.id,newer.id);
        assert.equal(current.technical_facts,undefined);
    });
    console.log(`${passed} saved property identity checks passed; no live network or database access.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
