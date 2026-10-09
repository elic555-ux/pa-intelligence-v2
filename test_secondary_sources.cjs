/* Identity, provenance and real basic-modal integration; entirely offline. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const {webcrypto,createHash}=require('node:crypto');
const api=require('./secondary_sources.js');
const row={id:'PA-MLS-1001',docket_id:'MLS-1001',source_type:'mls',address:'12 First St',city:'Pittsburgh',
    state:'PA',county:'Allegheny',zip:'15216',url:'https://www.redfin.com/PA/Pittsburgh/x/home/1001',
    beds:2,baths:1,sqft:900,lot_size:3201,technical_facts:{}};
const values={roof_type:'Composition',heating:'Forced Air, Gas',cooling:'Wall/Window Unit(s)',parking:'Off Street',
    parking_spaces:2,construction:'Frame',basement:'Full, Walk-Out Access',stories:1,water:'Public',sewer:'Public',
    beds:2,baths:1,sqft:900,year_built:1890,total_rooms:5,style:'Ranch',lot_area_acres:.07};
function record(provider='tarasa',date=new Date().toISOString()) {
    const source=provider==='tarasa'?'Tarasa / River Point Realty / MLS':'Clear Choice / MLS';
    const url=provider==='tarasa'?'https://www.tarasa.com/property-search/detail/56/1001/12-first-st-pittsburgh-pa-15216/':
        'https://www.clearchoiceenterprises.com/idx/12-first-st-pittsburgh-pa-15216/123456_spid/';
    return {schema:1,provider,property_id:row.id,listing_id:'1001',identity:api.identity(row),
        inventory_source_url:row.url,source_url:url,source_name:source,status:'published',retrieved_at:date,source_updated_at:'2026-10-08',
        subject:{street:'12 first st',unit:null,city:'pittsburgh',state:'PA',zip:'15216',county:'Allegheny',complete:true},
        facts:Object.fromEntries(Object.entries(values).map(([field,value])=>[field,{value,source,source_url:url,
            property_id:row.id,listing_id:'1001',status:'reported_by_source',checked_at:date,unit:field==='lot_area_acres'?'acre':null}])),
        photos:[1,2,3].map(n=>({url:'https://cdn.listingphotos.sierrastatic.com/pics3x/v123/56/56_1001_0'+n+'.jpg',
            source,source_url:url,listing_id:'1001',retrieved_at:date,capture_date:null}))};
}
const tarasa=record(),clear=record('clearchoice',new Date(Date.now()-3600000).toISOString());
let passed=0;
function check(name,callback){callback();passed++;console.log('PASS',name);}
check('both explicitly supported providers bind',()=>{assert.equal(api.matches(row,tarasa),true);assert.equal(api.matches(row,clear),true);});
check('provider and host cannot be swapped',()=>assert.equal(api.matches(row,{...tarasa,provider:'clearchoice'}),false));
check('same URL MLS is required independently',()=>assert.equal(api.matches(row,{...tarasa,source_url:tarasa.source_url.replace('/1001/','/9999/')}),false));
check('full address, unit, fraction, city, county and current listing must match',()=>{
    for(const edit of [{address:'14 First St'},{address:'12 First St #2'},{address:'12 1/2 First St'},
        {city:'Beechview'},{county:'Erie'},{docket_id:'MLS-1002'},{_idAmbiguous:true}]) assert.equal(api.matches({...row,...edit},tarasa),false);
});
check('changed inventory source cannot reuse snapshot',()=>assert.equal(api.matches({...row,url:'https://www.redfin.com/other'},tarasa),false));
check('field source provenance cannot be substituted',()=>{
    const bad=structuredClone(tarasa);bad.facts.roof_type.source='Clear Choice / MLS';assert.equal(api.fact(row,bad,'roof_type'),null);
    bad.facts.roof_type.source=tarasa.source_name;bad.facts.roof_type.listing_id='9999';assert.equal(api.fact(row,bad,'roof_type'),null);
});
check('unknown occupancy and roof condition are not fabricated',()=>{assert.equal(api.fact(row,tarasa,'occupancy'),null);assert.equal(api.fact(row,tarasa,'roof_condition'),null);});
check('three current photos with provider provenance',()=>{
    assert.equal(api.photos(row,tarasa).length,3);
    const bad=structuredClone(tarasa);bad.photos[0].url=bad.photos[0].url.replace('_1001_','_9999_');bad.photos[1].source=clear.source_name;
    assert.equal(api.photos(row,bad).length,1);
});
check('untrusted hosts, credentials, protocols and queries rejected',()=>{
    for(const url of ['javascript:alert(1)',tarasa.source_url.replace('.com','.com.evil.test'),
        tarasa.source_url.replace('https://','https://user:pass@'),tarasa.source_url+'?token=x']) assert.equal(api.sourceUrl(url),null);
});
check('latest valid snapshot wins while older provider remains available',()=>{
    const newest=structuredClone(tarasa);newest.facts.heating.value='Gas';
    assert.equal(api.bestFact(row,[clear,newest],'heating').value,'Gas');
    delete newest.facts.heating;assert.equal(api.bestFact(row,[clear,newest],'heating').source,clear.source_name);
});
check('rounded acreage keeps original unit without altering original lot',()=>{assert.equal(api.fact(row,tarasa,'lot_area_acres').unit,'acre');assert.equal(row.lot_size,3201);});

const elements=new Map();
class Element {
    constructor(tag='div'){this.tagName=tag;this.children=[];this.textContent='';this.hidden=false;this.disabled=false;
        this.classList={toggle(){},add(){},remove(){},contains(){return false;}};}
    set id(value){this._id=value;elements.set(value,this);} get id(){return this._id;}
    appendChild(n){this.children.push(n);n.parentNode=this;return n;} append(...ns){ns.forEach(n=>this.appendChild(n));}
    replaceChildren(...ns){this.children=[];this.append(...ns);} insertBefore(n){this.appendChild(n);}
    addEventListener(name,callback){this['on'+name]=callback;} getAttribute(){return null;}
    querySelectorAll(){return [];} querySelector(){return null;}
}
const dom={getElementById:id=>elements.get(id)||null,createElement:tag=>new Element(tag),
    createTextNode:text=>({textContent:text}),querySelectorAll:()=>[],addEventListener(){}};
for(const id of ['modal-listing-source-controls','modal-roof','modal-roof-source','modal-hvac','modal-hvac-source',
    'modal-parking','modal-parking-source','modal-occupancy','modal-occupancy-source','modal-listing-extra-facts']) {
    const e=new Element();e.id=id;e.parentNode=new Element();
}
const storage=new Map([['pa_github_token','test-token']]);let calls=[];
const ctx=vm.createContext({URL,TextEncoder,TextDecoder,Uint8Array,AbortController,crypto:webcrypto,console,Date,Set,Map,WeakMap,
    JSON,Number,String,Object,Array,Math,Promise,document:dom,
    window:{crypto:webcrypto,supabase:{createClient:()=>({from:()=>{throw Error('Unexpected cloud access');}})},addEventListener(){}},
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},navigator:{},lucide:{createIcons(){}},
    atob:x=>Buffer.from(x,'base64').toString('binary'),setTimeout:cb=>{queueMicrotask(cb);return 1;},clearTimeout(){},setInterval(){},clearInterval(){},
    fetch:async(url,options)=>{calls.push([url,options]);const r=url.includes('/tarasa/')?tarasa:clear;
        return {ok:true,json:async()=>({encoding:'base64',content:Buffer.from(JSON.stringify(r)).toString('base64')})};}});
const html=fs.readFileSync(path.join(__dirname,'index.html'),'utf8');
const script=html.match(/<script>\s*\/\/ Supabase Initialization([\s\S]*?)<\/script>/);
assert.ok(script,'The installed basic modal script must be present');
vm.runInContext('// Supabase Initialization'+script[1],ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,'secondary_sources.js'),'utf8'),ctx);
ctx.p=structuredClone(row);const before=JSON.stringify(ctx.p),run=c=>vm.runInContext(c,ctx);
(async()=>{
    assert.equal(await api.key(row.id,webcrypto),createHash('sha256').update(row.id).digest('hex').slice(0,32));
    passed++;console.log('PASS cache filename matches Python');
    run('currentSelectedProperty=p;basicSourceGeneration=1');
    await ctx.window.SecondarySourceEvidence.load(ctx.p,1);
    check('real basic modal renders technical facts and source labels',()=>{
        assert.equal(dom.getElementById('modal-roof').textContent,'גג מחומר מרוכב');
        assert.match(dom.getElementById('modal-hvac').textContent,/גז/);
        assert.match(dom.getElementById('modal-hvac').textContent,/קיר\/חלון/);
        assert.equal(dom.getElementById('modal-parking').textContent,'חניה מחוץ לרחוב');
        assert.match(dom.getElementById('modal-roof-source').children.map(n=>n.textContent).join(''),/Tarasa/);
    });
    check('two source columns and three real photo nodes rendered',()=>{
        const section=dom.getElementById('modal-additional-source');
        assert.equal(dom.getElementById('modal-additional-source-photos').children.length,3);
        assert.match(dom.getElementById('modal-additional-source-notice').textContent,/Tarasa/);
        assert.equal(section.children.filter(n=>n.tagName==='a').length,2);
    });
    check('all original property data and financial inputs remain unchanged',()=>assert.equal(JSON.stringify(ctx.p),before));
    check('normal opening only reads two GitHub snapshots',()=>{
        assert.equal(calls.length,2);assert.ok(calls.every(([url,options])=>url.startsWith('https://api.github.com/')&&!options?.method));
    });
    check('documented original fact still has precedence',()=>{
        ctx.p.technical_facts.roof_type={value:'Metal',source:'Contractor inspection'};
        assert.equal(run("propertyTechnicalFact(p,'roof_type').value"),'Metal');delete ctx.p.technical_facts.roof_type;
    });
    const resolvers=[];let started;
    const ready=new Promise(resolve=>{started=resolve;});
    ctx.fetch=()=>new Promise(resolve=>{resolvers.push(resolve);if(resolvers.length===2)started();});
    const pending=ctx.window.SecondarySourceEvidence.load(ctx.p,1);await ready;
    run('basicSourceGeneration=2;currentSelectedProperty=null');
    const prior=dom.getElementById('modal-additional-source-notice').textContent;
    resolvers.forEach(resolve=>resolve({ok:true,json:async()=>({encoding:'base64',content:Buffer.from(JSON.stringify(tarasa)).toString('base64')})}));
    await pending;
    check('closed modal ignores both late cache responses',()=>assert.equal(dom.getElementById('modal-additional-source-notice').textContent,prior));
    check('shared GitHub token never modified',()=>assert.equal(storage.get('pa_github_token'),'test-token'));
    console.log(passed+' source UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
