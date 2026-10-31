'use strict';

const assert=require('node:assert/strict'),fs=require('node:fs');

const m=require('./report-readonly-model.js'),f=require('./report-financials.js');

const now=Date.parse('2026-10-25T12:00:00Z');

let passed=0;function test(name,run){run();passed++;console.log('PASS '+name);}

const p={id:'qa-1',address:'18 Main St',city:'Pittsburgh',state:'PA',zip:'15203',price:100000,last_source_check:'2026-10-24T10:00:00Z',url:'https://example.com/listing'};

const report={property:p,property_id:p.id,generated_at:'2026-10-25T10:00:00Z',sources:{rent:{status:'accepted',observed_at:'2026-10-24T10:00:00Z',source_name:'RentCast',estimate:{status:'provider_estimate',value:1000,range_low:900,range_high:1100},comparables:[]}}};

test('Nine unique sections are numbered in two tabs',()=>{assert.deepEqual(m.SECTIONS.map(s=>s.number),[1,2,3,4,5,6,7,8,9]);assert.equal(new Set(m.SECTIONS.map(s=>s.id)).size,9);assert.equal(m.SECTIONS.filter(s=>s.tab==='overview').length,5);assert.equal(m.SECTIONS.filter(s=>s.tab==='financials').length,4);});

test('Report HTML has two tabs, print and official refresh controls and no editing or collection form',()=>{const html=fs.readFileSync(__dirname+'/report.html','utf8');assert.equal((html.match(/<button\b/g)||[]).length,4);assert.ok(!/<(?:input|select|textarea|form)\b/.test(html));assert.ok(!/report-sections-nav|mחשבון|btn-download-pdf|data-da-field/.test(html));for(const s of m.SECTIONS)assert.equal((html.match(new RegExp('id="section-'+s.id+'"','g'))||[]).length,1);});

test('Missing values and numeric zero stay distinct',()=>{assert.equal(m.number(null),null);assert.equal(m.number(false),null);assert.equal(m.number(''),null);assert.equal(m.number('0'),0);assert.equal(m.money(null),'חסר נתון');});

test('Foreign property reports and expired estimates cannot enter calculations',()=>{assert.equal(m.rentSource({...report,property_id:'other'},p,now),null);assert.equal(m.rentSource({...report,sources:{rent:{...report.sources.rent,observed_at:'2020-01-01'}}},p,now),null);assert.equal(m.rentSource(report,p,now).value,1000);});

test('Source rent and asking produce gross yield without invented net expenses',()=>{const r=m.metrics(p,null,report,f,now);assert.equal(r.grossYield,12);assert.equal(r.annualRent,12000);assert.equal(r.noi,null);assert.equal(r.flipProfit,null);assert.equal(r.purchaseAndRehab,null);});

test('Manual scenario fields never become automatic report assumptions',()=>{const r=m.metrics({...p,investment_scenario:{inputs:{arv:500000,flipRehab:1,operatingPct:0}}},null,report,f,now);assert.equal(r.arv,null);assert.equal(r.flipProfit,null);});

test('Legacy AI estimates never supply report totals',()=>{const a={...report,rehab:{property_id:p.id,observed_at:'2026-10-24',status:'available',complete:true,total_low:1000,total_high:2000}};assert.equal(m.metrics(p,a,report,f,now).rehab,null);assert.equal(m.metrics(p,a,report,f,now).purchaseAndRehab,null);});

test('Sales listing rows are rejected from the recorded sales table',()=>{const a={...report,closed_sales:{property_id:p.id,source_name:'Allegheny County / WPRDC',observed_at:report.generated_at,policy:{radius_miles:1},comparables:[{kind:'sale_listing',amount:100000,sale_date:'2026-10-01',recorded_date:'2026-10-03',distance_miles:.1,validation:'county_validated',source_url:'https://data.wprdc.org/'}]}};assert.equal(m.recordedSales(a,p,now).length,0);a.closed_sales.comparables[0].kind='recorded_sale';assert.equal(m.recordedSales(a,p,now).length,1);});

test('Source links and text reject executable input',()=>{assert.equal(m.url('javascript:alert(1)'),null);assert.equal(m.url('https://name:pass@example.com'),null);assert.equal(m.esc('<script>'), '&lt;script&gt;');});

test('Read-only client has no deal mutation, scenario write or image upload path',()=>{const text=fs.readFileSync(__dirname+'/report-readonly.js','utf8');assert.ok(!/\.upsert\(|\.insert\(|\.update\(|commitCurrentDeal|uploadCustomImage/.test(text));assert.ok(/mode:type==='Multi-Family'\?'profile':'rent'/.test(text));assert.ok(/params.delete\('collect'\)/.test(text));});

test('Unit prices retain cents rather than rounding national reference rates',()=>{assert.equal(m.unitMoney(3.8),'$3.80');assert.equal(m.unitMoney(4.79),'$4.79');assert.equal(m.unitMoney(null),'חסר נתון');});

test('Pending collection survives reload only for the same property and bounded time',()=>{

 const raw={request_id:'12345678-1234-4234-8234-123456789abc',identity:JSON.stringify(m.identity(p)),created_at:now-60000};

 assert.equal(m.pendingRequest(JSON.stringify(raw),p,now).request_id,raw.request_id);

 assert.equal(m.pendingRequest(raw,{...p,address:'22 Other St'},now),null);

 assert.equal(m.pendingRequest({...raw,created_at:now+1000},p,now),null);

 assert.equal(m.pendingRequest({...raw,created_at:now-31*60000},p,now),null);

 assert.equal(m.pendingRequest({...raw,request_id:'../../other'},p,now),null);

});

console.log(passed+' read-only report checks passed');



 test('Net metrics use the displayed source rent and explicit expenses consistently',()=>{
   const fake={financialSnapshot:()=>({price:100000,rent:1000,operatingExpenses:1800,vacancyLoss:200,noi:10000,capRate:10})};
   const r=m.metrics(p,null,{...report,sources:{rent:{...report.sources.rent,estimate:{status:'provider_estimate',value:1500}}}},fake,now);
   assert.equal(r.annualRent,18000);assert.equal(r.noi,16000);assert.equal(r.capRate,16);
   assert.equal(m.metrics(p,null,report,{financialSnapshot:()=>({price:100000,operatingExpenses:0,vacancyLoss:null})},now).noi,null);
   assert.equal(m.metrics(p,null,report,{financialSnapshot:()=>({price:100000,operatingExpenses:0,vacancyLoss:0})},now).noi,12000);
 });

test('Actual listing/county disagreements are flagged without changing source records',()=>{
 const p2={...p,beds:3},c={status:'matched',subject:p2,fields:{beds:2}};
 assert.ok(m.propertyConflicts(p2,c)[0].includes('במודעה 3, במחוז 2'));
 assert.equal(p2.beds,3);assert.equal(c.fields.beds,2);
 assert.deepEqual(m.propertyConflicts(p2,{...c,subject:{...p2,address:'Other'}}),[]);
});

test('Expanded report has no manual calculators and retains compact expandable area detail',()=>{
 const html=fs.readFileSync(__dirname+'/report.html','utf8'),controller=fs.readFileSync(__dirname+'/report-readonly.js','utf8');
 assert.ok(!/report-scenarios|deal-analysis|scenario-view/.test(html));
 assert.ok(!/PAReportScenarios|investment_scenario|\.mount\(/.test(controller));
 assert.ok(controller.includes('<details class="area-details"><summary>פירוט נתוני האזור והמקורות</summary>'));
 assert.ok(!controller.includes('<details class="area-details" open'));
});
