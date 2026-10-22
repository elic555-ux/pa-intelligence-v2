'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'report.html'),'utf8');
const script=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(x=>x[1]).find(x=>x.includes('async function loadReport()'));
const property={id:'qa-report-collection',address:'101 Example St',city:'Pittsburgh',state:'PA',zip:'15203',county:'Allegheny',property_type:'Single Family',beds:3,baths:1,sqft:1444,price:120000,url:'https://example.test/listing',last_source_check:new Date().toISOString()};
const stamp=new Date().toISOString();
const today=new Date(),start=new Date(Date.UTC(today.getUTCFullYear(),today.getUTCMonth(),1)).toISOString().slice(0,10),end=new Date(Date.UTC(today.getUTCFullYear(),today.getUTCMonth()+1,1)).toISOString().slice(0,10);
const quota={billing_cycle_verified:true,cycle_start:start,next_reset:end,used:3};
const copy=x=>JSON.parse(JSON.stringify(x));
function harness(){
    const elements=new Map(),storage=new Map([['pa_github_token','offline-fake-token']]),requests=[],statuses=[],saved=[],committed=[];
    const el=id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',value:id==='rentcast-mode'?'both':'',checked:false,hidden:false,disabled:false,focus(){this.focused=true;}});return elements.get(id);};
    const context=vm.createContext({console,URL,URLSearchParams,document:{getElementById:el},window:{location:{}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},crypto:{randomUUID:()=> 'offline-test-request-2026-10-22'},fetch:async(url,options)=>{requests.push({url,options});return {status:204};}});
    vm.runInContext(script,context);
    vm.runInContext('currentDeal='+JSON.stringify(property)+'; cloudAvailable=true; reportReady=true; lastUsage='+JSON.stringify(quota)+';',context);
    context.setEditability=()=>{};context.statusText=(id,message)=>statuses.push({id,message});context.renderReport=()=>{};context.pollReceipt=async()=>{};
    context.commitCurrentDeal=async next=>{committed.push(copy(next));vm.runInContext('currentDeal='+JSON.stringify(next),context);};context.saveReportToHistory=async()=>saved.push(true);
    return {context,el,storage,requests,statuses,saved,committed,value:code=>vm.runInContext(code,context)};
}
function receipt({profile=false,status='completed',accepted=false}={}){
    const report={property_id:property.id,property:copy(property),request_id:'offline-test-request-2026-10-22',status,profile_only:profile,sources:{},errors:{}};
    if(accepted)report.sources.rent={status:'accepted',observed_at:stamp,subject:{addressLine1:property.address,city:property.city,state:'PA',zipCode:property.zip,propertyType:'Single Family',bedrooms:3,bathrooms:1,squareFootage:1444},estimate:{value:1500,range_low:1400,range_high:1600},quality:{minimum_comparables:3},comparables:[{},{},{}]};
    return report;
}
const tests=[];const test=(name,fn)=>tests.push({name,fn});
test('A verified request dispatches both estimate kinds with an exact subject',async()=>{const h=harness();await h.context.requestRentCastReport();assert.equal(h.requests.length,1);const request=JSON.parse(JSON.parse(h.requests[0].options.body).inputs.payload);assert.equal(request.mode,'both');const {price,url,last_source_check,...subject}=property;assert.deepEqual(request.property,subject);});
test('Expired cycle blocks dispatch locally instead of silently reporting success',async()=>{const h=harness();h.value('lastUsage.next_reset="2000-01-01"');await h.context.requestRentCastReport();assert.equal(h.requests.length,0);assert.ok(h.statuses.some(x=>x.message.includes('מחזור אחר')));assert.equal(h.storage.has('pa_expanded_report_pending_'+property.id),false);});
test('Unverified provider usage blocks estimate requests without spending quota',async()=>{const h=harness();h.value('lastUsage.billing_cycle_verified=false');await h.context.requestRentCastReport();assert.equal(h.requests.length,0);assert.ok(h.statuses.some(x=>x.message.includes('טרם אומת')));});
test('Two-call request cannot cross 45 while a one-call request can use the final safe slot',async()=>{const h=harness();h.value('lastUsage.used=44');await h.context.requestRentCastReport();assert.equal(h.requests.length,0);h.el('rentcast-mode').value='rent';await h.context.requestRentCastReport();assert.equal(h.requests.length,1);});
test('Free county profile remains available while financial collection is blocked',async()=>{const h=harness();h.value('lastUsage=null');await h.context.requestRentCastReport('profile');assert.equal(h.requests.length,1);assert.equal(JSON.parse(JSON.parse(h.requests[0].options.body).inputs.payload).mode,'profile');});
test('Pending estimate prevents duplicate dispatches and repeated spending',async()=>{const h=harness();await h.context.requestRentCastReport();await h.context.requestRentCastReport();assert.equal(h.requests.length,1);assert.ok(h.statuses.some(x=>x.message.includes('עדיין לא נבדקה')));});
test('Historical snapshots cannot dispatch any research',async()=>{const h=harness();h.value('currentSavedReportId="historical"');await h.context.requestRentCastReport();assert.equal(h.requests.length,0);});
test('County-only completion updates facts but never auto-saves an empty analysis report',async()=>{const h=harness();await h.context.applySourceReport(receipt({profile:true}),true);assert.equal(h.committed.length,1);assert.equal(h.saved.length,0);});
test('Blocked or rejected results do not create automatic empty report snapshots',async()=>{const h=harness();const r=receipt({status:'partial'});r.errors={rent:'insufficient_comparables'};await h.context.applySourceReport(r,true);assert.equal(h.committed.length,1);assert.equal(h.saved.length,0);});
test('Accepted rent source persists actual financial evidence and produces gross yield',async()=>{const h=harness();await h.context.applySourceReport(receipt({accepted:true}),true);assert.equal(h.saved.length,1);assert.equal(h.committed[0].financial_evidence.rent.value,1500);assert.equal(h.value('financialSnapshot(currentDeal).yield'),15);assert.equal(h.value('financialSnapshot(currentDeal).arv'),null);});
test('Mode controls distinguish county-only facts from estimate collection',()=>{const h=harness();h.el('rentcast-mode').value='profile';h.context.updateRequestMode();assert.match(h.el('request-report').textContent,/בלבד/);assert.match(h.el('request-mode-note').textContent,/אינה אוספת שווי/);h.el('rentcast-mode').value='both';h.context.updateRequestMode();assert.match(h.el('request-report').textContent,/אומדני שווי ושכירות/);});
test('Displayed quota does not advertise an expired counter as available calls',()=>{const h=harness();h.context.showUsage({...quota,next_reset:'2000-01-01'});assert.match(h.el('quota-status').textContent,/מחזור אחר/);assert.doesNotMatch(h.el('quota-status').textContent,/זמינות עד עצירה/);});
test('Configured GitHub connection opens collection controls rather than asking for the token again',()=>{const h=harness();h.context.switchTab=()=>{};h.context.configureRentCast();assert.equal(h.el('source-settings').open,true);assert.equal(h.el('request-report').focused,true);assert.notEqual(h.el('github-token').focused,true);});
test('Historical report collection action opens its current property instead of changing the snapshot',()=>{const h=harness();h.value('currentSavedReportId="historical"');h.context.configureRentCast();assert.equal(h.context.window.location.href,'report.html?id='+encodeURIComponent(property.id));assert.equal(h.requests.length,0);});
test('Successful profile job explicitly says it did not collect financial estimates',()=>{const h=harness();assert.match(h.context.reportCollectionMessage(receipt({profile:true})),/לא נאספו אומדני שווי/);});
test('A completed job with no accepted sources cannot claim successful estimate collection',()=>{const h=harness();assert.match(h.context.reportCollectionMessage(receipt()),/לא התקבל אומדן/);});
test('Accepted financial collection names exactly the estimate kinds actually available',()=>{const h=harness();const message=h.context.reportCollectionMessage(receipt({accepted:true}));assert.match(message,/נטענו אומדני שכירות/);assert.doesNotMatch(message,/שווי שוק/);});
(async()=>{let passed=0;for(const {name,fn} of tests){try{await fn();passed++;console.log('PASS '+name);}catch(error){console.error('FAIL '+name,error);process.exitCode=1;}}console.log(passed+'/'+tests.length+' report collection checks passed; no external calls.');})();
