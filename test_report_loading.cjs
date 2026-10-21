'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'report.html'),'utf8');
const script=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(s=>s.includes('async function loadReport()'));
assert.ok(script);
const copy=x=>JSON.parse(JSON.stringify(x));
const property={id:'qa-property',address:'101 Example St',city:'Erie',state:'PA',zip:'16501',price:120000,beds:3,baths:1};
function harness({search='?id=qa-property',rows=[{property_id:property.id,deal_data:property}],error=null,cache=[],saved=[],renderError=false,refreshError=false}={}){
    const elements=new Map(),storage=new Map([['pa_pipeline_deals',JSON.stringify(cache)]]),calls=[],statuses=[];
    const el=id=>{if(!elements.has(id))elements.set(id,{hidden:id==='report-content',textContent:'',value:'',disabled:true,classList:{add(){}},innerHTML:''});return elements.get(id);};
    const document={getElementById:el,querySelectorAll:()=>[]};
    const supabase={createClient:()=>({from:table=>({select:()=>({eq:async(field,value)=>{calls.push({table,field,value});return {data:table==='saved_reports'?saved:rows,error};}})})})};
    const ctx=vm.createContext({console,URL,URLSearchParams,document,window:{supabase,location:{search}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}});
    vm.runInContext(script,ctx);
    ctx.statusText=(id,text)=>statuses.push({id,text});
    let rendered=0;
    ctx.renderReport=()=>{rendered++;if(renderError)throw Error('Rendering failed');};
    ctx.renderRehabPlan=()=>{};
    ctx.updateEvidenceForm=()=>{};
    ctx.refreshRepositoryData=async()=>{if(refreshError)throw Error('Supplement unavailable');};
    ctx.showUsage=()=>{};
    return {ctx,el,calls,statuses,storage,get rendered(){return rendered;},value:expression=>vm.runInContext(expression,ctx),async load(){await ctx.loadReport();}};
}
const tests=[];
function test(name,fn){tests.push({name,fn});}
test('Cloud property renders before content and PDF controls become available',async()=>{
    const h=harness();await h.load();assert.equal(h.rendered,1);assert.equal(h.value('reportReady'),true);assert.equal(h.el('report-content').hidden,false);assert.equal(h.el('loading').hidden,true);assert.equal(h.el('btn-download-pdf').disabled,false);assert.equal(h.el('save-report').disabled,false);assert.equal(h.value('currentDeal.address'),property.address);
});
test('An authoritative missing cloud row cannot revive a cached deleted property',async()=>{
    const h=harness({rows:[],cache:[property]});await h.load();assert.equal(h.value('reportReady'),false);assert.equal(h.rendered,0);assert.equal(h.el('loading').hidden,false);assert.match(h.el('loading').textContent,/לא נמצא/);assert.equal(h.el('btn-download-pdf').disabled,true);
});
test('Unavailable cloud permits an exact cached property for read-only viewing',async()=>{
    const h=harness({error:{message:'offline'},cache:[property]});await h.load();assert.equal(h.value('reportReady'),true);assert.equal(h.value('cloudAvailable'),false);assert.equal(h.el('save-report').disabled,true);assert.equal(h.el('btn-download-pdf').disabled,false);assert.ok(h.statuses.some(x=>x.text.includes('עותק מקומי')));
});
test('An unrelated cached property is never substituted after cloud failure',async()=>{
    const h=harness({error:{message:'offline'},cache:[{...property,id:'another'}]});await h.load();assert.equal(h.value('reportReady'),false);assert.equal(h.rendered,0);assert.equal(h.el('report-content').hidden,true);
});
test('Mismatched cloud data cannot enable export or display another property',async()=>{
    const h=harness({rows:[{property_id:property.id,deal_data:{...property,id:'another'}}]});await h.load();assert.equal(h.value('reportReady'),false);assert.equal(h.rendered,0);assert.match(h.el('loading').textContent,/אינו תואם/);assert.equal(h.el('btn-download-pdf').disabled,true);
});
test('Historical reports use only their snapshot and keep editing and research disabled',async()=>{
    const h=harness({search:'?report=saved-1',saved:[{id:'saved-1',report_data:{date:'2026-10-09T10:00:00Z',deal_data:{...property,price:99000}}}],rows:[{property_id:property.id,deal_data:{...property,price:150000}}],refreshError:true});await h.load();assert.equal(h.value('currentDeal.price'),99000);assert.equal(h.value('reportReady'),true);assert.equal(h.el('save-report').disabled,true);assert.equal(h.el('request-report').disabled,true);assert.equal(h.el('btn-download-pdf').disabled,false);assert.deepEqual(h.calls.map(x=>x.table),['saved_reports']);
});
test('Legacy saved rows without a full snapshot explain the problem visibly',async()=>{
    const h=harness({search:'?report=legacy',saved:[{id:'legacy',report_data:{address:property.address}}]});await h.load();assert.equal(h.value('reportReady'),false);assert.equal(h.el('legacy-report-note').hidden,false);assert.match(h.el('loading').textContent,/לא ניתן לשחזר/);
});
test('A saved empty object cannot produce a blank report with export enabled',async()=>{
    const h=harness({search:'?report=empty',saved:[{id:'empty',report_data:{date:'2026-10-09T10:00:00Z',deal_data:{}}}]});await h.load();assert.equal(h.value('reportReady'),false);assert.equal(h.rendered,0);assert.match(h.el('loading').textContent,/חסרה מזהה או כתובת/);assert.equal(h.el('btn-download-pdf').disabled,true);
});
test('Rendering failure leaves an explicit visible error and no blank export',async()=>{
    const h=harness({renderError:true});await h.load();assert.equal(h.value('reportReady'),false);assert.equal(h.el('loading').hidden,false);assert.equal(h.el('report-content').hidden,true);assert.match(h.el('loading').textContent,/Rendering failed/);assert.equal(h.el('btn-download-pdf').disabled,true);
});
test('Supplemental source failure retains the populated report with a visible warning',async()=>{
    const h=harness({refreshError:true});await h.load();assert.equal(h.value('reportReady'),true);assert.equal(h.el('report-content').hidden,false);assert.equal(h.el('btn-download-pdf').disabled,false);assert.ok(h.statuses.some(x=>x.text.includes('Supplement unavailable')));
});
test('A report link without an identifier gives an actionable error',async()=>{
    const h=harness({search:''});await h.load();assert.equal(h.value('reportReady'),false);assert.match(h.el('loading').textContent,/לא נבחר נכס/);assert.equal(h.calls.length,0);
});
test('Empty financial records explain missing values without inventing numbers',async()=>{
    const h=harness();await h.load();h.ctx.renderAvailability=undefined;
    // Reload just this real declaration after the isolated render stub.
    const start=script.indexOf('function renderAvailability(){'),end=script.indexOf('function renderComparables(',start);
    vm.runInContext(script.slice(start,end),h.ctx);h.ctx.renderAvailability();
    assert.match(h.el('report-data-summary').textContent,/אין נתונים כספיים/);assert.match(h.el('report-data-details').innerHTML,/לא נשמרה תוצאה/);assert.equal(h.value('financialSnapshot(currentDeal).maoFlip'),null);
});
test('Rejected rent estimate shows its actual comparable shortage',async()=>{
    const report={property_id:property.id,property:{...property},sources:{rent:{status:'insufficient_comparables',observed_at:'2026-10-08',comparables:[{}]}}};
    const h=harness({rows:[{property_id:property.id,deal_data:{...property,expanded_source_report:report}}]});await h.load();
    const start=script.indexOf('function renderAvailability(){'),end=script.indexOf('function renderComparables(',start);vm.runInContext(script.slice(start,end),h.ctx);h.ctx.renderAvailability();
    assert.match(h.el('report-data-details').innerHTML,/לא התקבלו מספיק/);assert.match(h.el('report-data-details').innerHTML,/1 מודעות/);assert.equal(h.value('financialSnapshot(currentDeal).rent'),null);
});
(async()=>{let passed=0;for(const {name,fn}of tests){try{await fn();console.log('PASS '+name);passed++;}catch(error){console.error('FAIL '+name,error);process.exitCode=1;}}console.log(passed+'/'+tests.length+' report loading checks passed; no network or credentials.');})();
