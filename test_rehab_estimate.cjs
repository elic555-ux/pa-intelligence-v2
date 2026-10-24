'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const E=require('./rehab-estimate.js'),R=require('./rehab-report.js'),A=require('./deal-analysis.js');
const p={id:'qa-rehab',address:'10 QA Ave',city:'Pittsburgh',state:'PA',zip:'15203'};
const quantity=()=>({pricing_mode:'quantity',quantity:12.5,unit:'lf',unit_low:100.1,unit_high:200.2,price_basis:'manual'});
function plan(){const x=R.normalizePlan(null,p.id);x.strategy='rental';x.updated_at='2026-10-24T12:00:00Z';for(const e of Object.values(x.items))e.condition='not_applicable';x.items.kitchen={...quantity(),condition:'repair',scope:'QA replacement',finding:'QA observation'};return x;}
const tests=[];const test=(name,run)=>tests.push([name,run]);
test('Quantities use cent rounding, a real lower range and the upper planning amount',()=>{
  assert.deepEqual(E.estimate(quantity()),{cost:2502.5,low:1251.25,high:2502.5,mode:'quantity',errors:[]});
  const x=plan();x.contingency_pct=10;const sum=R.summarize(x);assert.equal(sum.low,1376.38);assert.equal(sum.total,2752.75);assert.equal(sum.high,sum.total);assert.equal(sum.groups[0].subtotal,2502.5);
});
test('Missing, zero quantity, inverted, over-precision and overflowing ranges cannot become budgets',()=>{
  for(const patch of [{quantity:null},{quantity:0},{unit_low:300},{unit_high:null},{quantity:-2},{unit_low:'1e3'},{unit_low:'4.333'},{quantity:1000000,unit_high:100000000}])assert.equal(E.estimate({...quantity(),...patch}).cost,null,JSON.stringify(patch));
  assert.equal(E.estimate({...quantity(),unit_low:0,unit_high:0}).cost,0);
});
test('A documented price basis requires a source, real calendar date and reference in either mode',()=>{
  for(const pricing_mode of ['total','quantity']){
    let e={...quantity(),pricing_mode,cost:50,price_basis:'quote'};assert.ok(E.normalizeEntry(e).errors.length);
    e={...e,price_source:'QA contractor',price_date:'2026-02-30',price_reference:'QA quote 3'};assert.ok(E.normalizeEntry(e).errors.length);
    e.price_date='2026-10-24';assert.equal(E.normalizeEntry(e).errors.length,0);
  }
});
test('Legacy plans migrate without losing zero, scope, exclusion, or reserve',()=>{
  const x=R.normalizePlan({version:1,property_id:p.id,items:{roof:{cost:0,condition:'good',scope:'Prior inspection'},kitchen:{cost:5000,condition:'not_applicable'}},contingency_pct:10},p.id);
  assert.equal(x.version,2);assert.equal(x.items.roof.cost,0);assert.equal(x.items.roof.scope,'Prior inspection');assert.equal(x.items.kitchen.cost,5000);assert.equal(x.items.roof.pricing_mode,'total');assert.equal(R.summarize(x).total,0);assert.equal(x.strategy,'unspecified');
});
test('Pricing completion differs from reviewed, scoped and saved readiness',()=>{
  const x=plan();assert.equal(R.summarize(x).ready_for_scenario,true);
  for(const patch of [{updated_at:null},{strategy:'unspecified'}])assert.equal(R.summarize({...x,...patch}).ready_for_scenario,false);
  x.items.kitchen.condition='unassessed';assert.equal(R.summarize(x).complete,true);assert.equal(R.summarize(x).ready_for_scenario,false);
  x.items.kitchen.condition='repair';x.items.kitchen.finding='';x.items.kitchen.scope='';assert.equal(R.summarize(x).reviewed_count,0);
});
test('Exclusion and total budget bounds never inflate a complete estimate',()=>{
  const x=plan();x.items.roof.cost=99999999;assert.equal(R.summarize(x).total,2502.5);
  x.items.roof.condition='good';x.items.roof.scope='QA review';assert.equal(R.summarize(x).ready_for_scenario,false);assert.ok(R.summarize(x).validation_errors.total);
});
test('Copy uses a saved matching strategy, preserves other costs and identifies its snapshot',()=>{
  const s=A.normalize(null,p);s.inputs.flipRehab=9000;const b=R.summarize(plan());const next=A.applyRehabBudget(p,s,b,'rental');
  assert.equal(next.inputs.rentalRehab,2502.5);assert.equal(next.inputs.flipRehab,9000);assert.equal(next.rehab_links.rentalRehab.low,1251.25);
  const roundtrip=A.serialize(next,p);assert.deepEqual(roundtrip.rehab_links,next.rehab_links);assert.equal(A.calculate(p,next).origins.rentalRehab,'rehab_plan');
  assert.equal(s.inputs.rentalRehab,null);
});
test('Foreign, unsaved, dirty, unreviewed and wrong-strategy budgets are rejected',()=>{
  const s=A.normalize(null,p),b=R.summarize(plan());
  for(const patch of [{property_id:'foreign'},{updated_at:null},{ready_for_scenario:false},{dirty:true}])assert.throws(()=>A.applyRehabBudget(p,s,{...b,...patch},'rental'));
  assert.throws(()=>A.applyRehabBudget(p,s,b,'flip'));
});
test('Changed budget keeps the copied amount and clearly marks the scenario as outdated',()=>{
  const x=plan(),b=R.summarize(x),s=A.applyRehabBudget(p,A.normalize(null,p),b,'rental');
  assert.doesNotMatch(A.rehabStatusHTML(s,b),/da-budget-stale/);
  x.items.kitchen.finding='A later review with the same amount';const changed=R.summarize(x);assert.notEqual(changed.revision,b.revision);
  assert.match(A.rehabStatusHTML(s,changed),/da-budget-stale/);assert.equal(A.calculate(p,s).inputs.rentalRehab,2502.5);
});
test('Edited amounts cannot retain an old budget source; document preset clears links',()=>{
  const s=A.applyRehabBudget(p,A.normalize(null,p),R.summarize(plan()),'rental');s.inputs.rentalRehab=8000;
  assert.deepEqual(A.serialize(s,p).rehab_links,{});assert.equal(A.calculate(p,s).origins.rentalRehab,'assumption');
  assert.deepEqual(A.documentPreset({...p,sqft:1000},s).rehab_links,{});
});
test('Evidence, references and photo labels are escaped; a photo selection makes no Vision claim',()=>{
  const x=plan(),attack='<script>alert(1)</script>';x.items.kitchen.finding=attack;x.items.kitchen.price_source=attack;x.items.kitchen.photo_ref=attack;
  const html=R.renderHTML({property:p,plan:x,photos:[{id:attack,name:attack}]});assert.ok(!html.includes('<script>'));assert.match(html,/&lt;script&gt;/);assert.match(html,/אינו פענוח תמונות אוטומטי/);
  const readonly=R.renderHTML({property:p,plan:x,readOnly:true});assert.ok(!/<(?:input|select|textarea)\b/.test(readonly));assert.match(readonly,/12.5/);
});
function node(){return{textContent:'',innerHTML:'',disabled:false,classList:{toggle(){}},setAttribute(){}};}
function host(){const root=node(),nodes=new Map();root.listeners={};root.addEventListener=(k,v)=>root.listeners[k]=v;root.removeEventListener=k=>delete root.listeners[k];root.querySelectorAll=()=>[];root.querySelector=k=>{if(!nodes.has(k)){const n=node();n.querySelector=()=>node();nodes.set(k,n);}return nodes.get(k);};root.edit=(id,field,value)=>root.listeners.input({target:{value,getAttribute:()=>field,closest:()=>({getAttribute:()=>id})}});root.save=()=>root.listeners.click({target:{closest:()=>root.querySelector('.rh-save-button')}});return root;}
test('Reload restores raw invalid drafts without silently converting them to missing values',()=>{
  const storage=new Map();storage.setItem=(k,v)=>storage.set(k,v);storage.getItem=k=>storage.get(k);storage.removeItem=k=>storage.delete(k);
  const options={property:p,plan:plan(),storage,onSave:async()=>{}};const one=host();R.mount(one,options);one.edit('kitchen','quantity','oops');
  const two=host(),instance=R.mount(two,options);assert.equal(instance.isDirty(),true);assert.match(two.innerHTML,/תקינים/);
  const other=R.mount(host(),{...options,property:{...p,address:'Different unit'}});assert.equal(other.isDirty(),false);
});
test('Save roundtrip retains range and source fields; failed save retains a local draft',async()=>{
  const root=host();let saved;const options={property:p,plan:plan(),onSave:async x=>{saved=x;}};const instance=R.mount(root,options);
  root.edit('kitchen','quantity','20');root.edit('kitchen','price_source','QA manual estimate');await root.save();assert.equal(saved.items.kitchen.cost,4004);assert.equal(saved.items.kitchen.unit_low,100.1);assert.equal(saved.items.kitchen.price_source,'QA manual estimate');assert.equal(instance.isDirty(),false);
  instance.update({...options,plan:saved,onSave:async()=>{throw Error('offline');}});root.edit('kitchen','quantity','21');await root.save();assert.equal(instance.isDirty(),true);assert.match(root.querySelector('.rh-save-status').textContent,/offline/);
});
test('Concurrent cloud rehabilitation edits are rejected before any write',async()=>{
  const html=fs.readFileSync(__dirname+'/report.html','utf8');const start=html.indexOf('async function commitCurrentDeal('),end=html.indexOf('\nasync function saveInspectionFindings',start);
  const old={...p,rehab_plan:{version:1}},latest={...old,rehab_plan:{version:2}};let writes=0;
  const context=vm.createContext({cloudAvailable:true,currentSavedReportId:null,reportSavePending:false,currentDeal:old,setEditability(){},latestCloudDeal:async()=>latest,addressNorm:x=>x,copy:x=>JSON.parse(JSON.stringify(x)),applyFinancialSnapshot(){},supabaseClient:{from:()=>({upsert:async()=>{writes++;return {};}})},renderReport(){},cacheLocally(){}});
  vm.runInContext(html.slice(start,end),context);await assert.rejects(()=>context.commitCurrentDeal({...old,rehab_plan:{version:3}}),/עודכן במכשיר אחר/);assert.equal(writes,0);
});
(async()=>{for(const [name,run]of tests){try{await run();console.log('PASS '+name);}catch(e){process.exitCode=1;console.error('FAIL '+name,e);}}console.log(tests.length+' rehabilitation estimate checks, offline only.');})();
