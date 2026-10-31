'use strict';
const assert=require('node:assert/strict');
const S=require('./report-scenarios.js');
const p={id:'qa-1',address:'18 Main St',city:'Pittsburgh',state:'PA',zip:'15203'};
const seed=(inputs={})=>({...S.normalize(null,p),inputs});
let count=0;function test(name,fn){fn();count++;console.log('PASS '+name);}

test('No invented sale value, renovation, loan terms or stress assumptions',()=>{
  const r=S.calculate(p,null,{price:100000,rent:1000,arv:250000,flipRehab:1,rentalRehab:1});
  assert.equal(r.inputs.purchase,100000);assert.equal(r.inputs.rent,1000);
  for(const key of ['arv','flipRehab','rentalRehab','apr','ltv','years','refinanceValue','stressRentPct'])assert.equal(r.inputs[key],null);
  assert.equal(r.flipProfit,null);assert.equal(r.refinanceCashFlow,null);assert.equal(r.sensitivity,null);
});
test('Only exact property identity can seed a saved scenario',()=>{
  const s=seed({arv:200000});assert.equal(S.calculate(p,s).inputs.arv,200000);
  for(const different of [{...p,id:'other'},{...p,city:'Verona'},{...p,address:'18 Main St Apt 2'},{...p,zip:'15201'}])assert.equal(S.calculate(different,s).inputs.arv,null);
});
test('Old plan-linked renovation budgets are excluded even when link metadata is incomplete',()=>{
  const s={...seed({flipRehab:10000,rentalRehab:5000,arv:200000}),version:1,rehab_links:{flipRehab:{},rentalRehab:{}}};
  const r=S.calculate(p,s);assert.equal(r.inputs.flipRehab,null);assert.equal(r.inputs.rentalRehab,null);assert.equal(r.inputs.arv,200000);
});
test('Missing costs are not zero, explicit zero costs remain usable',()=>{
  const s=seed({purchase:100000,arv:120000,flipRehab:0,acquisition:0,selling:0,holding:0});
  assert.equal(S.calculate(p,s).flipProfit,null);
  s.inputs.financing=0;assert.equal(S.calculate(p,s).flipProfit,20000);
});
test('Invalid manual values block fallback and prevent serialization',()=>{
  for(const bad of [true,-1,'-1','1e3',Infinity,'nope']){
    const s=seed({purchase:bad}),r=S.calculate(p,s,{price:100000});
    assert.equal(r.inputs.purchase,null);assert.equal(r.origins.purchase,'invalid');assert.throws(()=>S.serialize(s,p));
  }
});
test('Full-cost flip loss and negative purchase ceiling remain visible',()=>{
  const r=S.calculate(p,seed({purchase:100000,arv:90000,flipRehab:10000,acquisition:5000,selling:5000,holding:2000,financing:1000,targetProfit:80000}));
  assert.equal(r.flipProfit,-33000);assert.equal(r.flipBreakEven,123000);assert.equal(r.flipMao,-13000);
});
test('Zero-interest amortization and cash purchase require no guessed APR',()=>{
  assert.equal(S.payment(120000,0,10),1000);assert.equal(S.payment(0,null,null),0);assert.equal(S.payment(120000,null,30),null);
  const r=S.calculate(p,seed({purchase:100000,rent:1000,rentalRehab:0,acquisition:0,operatingAnnual:2000,ltv:0,loanFees:0}));
  assert.equal(r.monthlyDebt,0);assert.equal(r.monthlyCashFlow,10000/12);assert.equal(r.cashRequired,100000);
});
test('BRRRR uses explicit cash contributions, debt payoff and all refinance costs',()=>{
  const r=S.calculate(p,seed({rent:1500,operatingAnnual:6000,initialCash:60000,debtPayoff:80000,refinanceValue:200000,refinanceLtv:75,refinanceApr:0,refinanceYears:25,refinanceCosts:5000}));
  assert.equal(r.refinanceLoan,150000);assert.equal(r.refinancePayment,500);
  assert.equal(r.netCashReturned,65000);assert.equal(r.capitalRemaining,-5000);
  assert.equal(r.refinanceCashFlow,500);assert.equal(r.equity,50000);assert.equal(r.refinanceCashOnCash,null);
});
test('Refinancing cash shortfall is negative cash returned, never clamped away',()=>{
  const r=S.calculate(p,seed({initialCash:20000,debtPayoff:160000,refinanceValue:200000,refinanceLtv:75,refinanceCosts:5000}));
  assert.equal(r.netCashReturned,-15000);assert.equal(r.capitalRemaining,35000);
});
test('Missing payoff or cash contribution blocks dependent BRRRR outputs',()=>{
  const r=S.calculate(p,seed({refinanceValue:200000,refinanceLtv:75,refinanceCosts:0}));
  assert.equal(r.netCashReturned,null);assert.equal(r.capitalRemaining,null);assert.equal(r.refinanceCashOnCash,null);
});
test('Sensitivity is opt-in and changes exactly the selected economic inputs',()=>{
  const inputs={purchase:100000,rent:1000,arv:160000,flipRehab:10000,rentalRehab:5000,acquisition:5000,selling:5000,holding:0,financing:0,operatingAnnual:3000,apr:3,stressRentPct:10,stressSalePct:5,stressRehabPct:20,stressOperatingPct:10};
  assert.equal(S.calculate(p,seed(inputs)).sensitivityReady,false);
  const r=S.calculate(p,seed({...inputs,stressRatePoints:5})),c=r.sensitivity.conservative,o=r.sensitivity.optimistic;
  assert.equal(c.inputs.rent,900);assert.equal(c.inputs.arv,152000);assert.equal(c.inputs.flipRehab,12000);assert.equal(c.inputs.operatingAnnual,3300.0000000000005);assert.equal(c.inputs.apr,8);
  assert.equal(o.inputs.rent,1100);assert.equal(o.inputs.flipRehab,8000);assert.equal(o.inputs.apr,0);
  assert.equal(c.inputs.purchase,100000);assert.equal(c.inputs.selling,5000);assert.equal(c.inputs.refinanceValue,null);
});
test('Zero sensitivity produces identical rows and null inputs stay missing',()=>{
  const inputs={rent:1000,stressRentPct:0,stressSalePct:0,stressRehabPct:0,stressOperatingPct:0,stressRatePoints:0};
  const r=S.calculate(p,seed(inputs));assert.ok(r.sensitivityReady);
  assert.equal(r.sensitivity.conservative.inputs.rent,1000);assert.equal(r.sensitivity.optimistic.inputs.arv,null);
  assert.equal(r.sensitivity.conservative.flipProfit,null);assert.equal(r.sensitivity.optimistic.monthlyCashFlow,null);
});
test('Rendered output labels manual refinancing, uncertainty and nonpositive capital',()=>{
  const html=S.render(S.calculate(p,seed({initialCash:0,debtPayoff:0,refinanceValue:100000,refinanceLtv:100,refinanceCosts:0})));
  assert.ok(html.includes('BRRRR'));assert.ok(html.includes('אפס או שלילי'));assert.ok(html.includes('טרם הוזנו מספיק נתונים'));assert.ok(!html.includes('Infinity'));assert.ok(!html.includes('NaN'));
});
test('Local persistence key isolates full property identity',()=>{
  assert.notEqual(S.storageKey(p),S.storageKey({...p,city:'Verona'}));assert.notEqual(S.storageKey(p),S.storageKey({...p,address:'18 Main St Apt 2'}));
});
function dom(){
  const nodes=new Map(),fields=Object.keys(S.FIELDS).map(key=>({dataset:{rsField:key},value:'',attributes:{},listeners:{},setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,v){this.listeners[k]=v;}}));
  const root={innerHTML:'',querySelectorAll(){return root.innerHTML.includes('scenario-editor')?fields:[];},querySelector(selector){
    if(selector.includes('data-rs-action')&&!root.innerHTML.includes('scenario-editor'))return null;
    if(!nodes.has(selector))nodes.set(selector,{textContent:'',innerHTML:'',listeners:{},addEventListener(k,v){this.listeners[k]=v;}});
    return nodes.get(selector);
  }};
  return {root,nodes,fields};
}
test('Historical mount never reads current local assumptions or offers edit controls',()=>{
  const d=dom();let reads=0,writes=0;
  const control=S.mount(d.root,{property:p,scenario:seed({purchase:100000}),readOnly:true,source:{},storage:{getItem(){reads++;return JSON.stringify(seed({purchase:1}));},setItem(){writes++;}}});
  assert.equal(reads,0);assert.equal(writes,0);assert.equal(control.calculate().inputs.purchase,100000);assert.ok(!d.root.innerHTML.includes('scenario-editor'));
  assert.ok(d.nodes.get('[data-rs-status]').textContent.includes('עותק הדוח השמור'));
});
test('Editor saves a separate local scenario, reports success only after storage succeeds',()=>{
  const d=dom(),saved=new Map(),storage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
  const control=S.mount(d.root,{property:p,scenario:seed({purchase:100000}),source:{rent:1500},storage});
  const field=d.fields.find(x=>x.dataset.rsField==='refinanceValue');field.value='200000';field.listeners.input();
  assert.equal(control.calculate().inputs.refinanceValue,200000);assert.equal(saved.size,0);
  d.nodes.get('[data-rs-action="save"]').listeners.click();
  assert.equal(saved.size,1);assert.equal(JSON.parse(saved.get(S.storageKey(p))).inputs.refinanceValue,200000);
  assert.ok(d.nodes.get('[data-rs-status]').textContent.includes('במחשב זה בלבד'));
  assert.equal(d.fields.find(x=>x.dataset.rsField==='rent').placeholder,'1500');
});
test('Denied local storage never claims persistence',()=>{
  const d=dom();S.mount(d.root,{property:p,scenario:seed({purchase:100000}),storage:{getItem(){return null;},setItem(){throw Error('denied');}}});
  d.nodes.get('[data-rs-action="save"]').listeners.click();assert.ok(d.nodes.get('[data-rs-status]').textContent.includes('לא נשמרו'));
});
test('Malformed stored manual values remain invalid instead of falling back to source',()=>{
  const d=dom(),stored={...seed({purchase:'oops'}),version:S.VERSION};
  const control=S.mount(d.root,{property:p,source:{price:100000},storage:{getItem(){return JSON.stringify(stored);}}});
  assert.equal(control.calculate().inputs.purchase,null);assert.equal(control.calculate().origins.purchase,'invalid');
  assert.equal(d.fields.find(x=>x.dataset.rsField==='purchase').attributes['aria-invalid'],'true');
});
test('Default result stays concise without empty metric cards or expanded optional sections',()=>{
  const html=S.render(S.calculate(p,null));assert.equal((html.match(/class="metric"/g)||[]).length,0);assert.equal((html.match(/<details open>/g)||[]).length,0);
  const ready=S.render(S.calculate(p,seed({refinanceValue:100000,refinanceLtv:75,stressRentPct:0,stressSalePct:0,stressRehabPct:0,stressOperatingPct:0,stressRatePoints:0})));
  assert.equal((ready.match(/<details open>/g)||[]).length,2);
});
test('Refreshing documented source values preserves unsaved manual draft and its status',()=>{
  const d=dom(),control=S.mount(d.root,{property:p,source:{price:100000,rent:1000},storage:null});
  const field=d.fields.find(x=>x.dataset.rsField==='refinanceValue');field.value='200000';field.listeners.input();
  const priorStatus=d.nodes.get('[data-rs-status]').textContent;
  const r=control.updateSource({price:110000,rent:1200});assert.equal(r.inputs.purchase,110000);assert.equal(r.inputs.rent,1200);assert.equal(r.inputs.refinanceValue,200000);
  assert.equal(control.getDraft().inputs.refinanceValue,'200000');assert.equal(d.nodes.get('[data-rs-status]').textContent,priorStatus);
});
console.log(count+' report scenario checks passed');
