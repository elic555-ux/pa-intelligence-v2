'use strict';
const assert=require('node:assert/strict');
const A=require('./deal-analysis.js');
const fs=require('node:fs'),vm=require('node:vm');
const p={id:'subject-1',address:'101 Example St',city:'Pittsburgh',state:'PA',zip:'15203',sqft:1600};
const scenario=inputs=>({...A.normalize(null,p),inputs});
const full={purchase:100000,arv:240000,rent:1800,flipRehab:50000,rentalRehab:30000,acquisition:5000,
    operatingAnnual:6000,selling:14400,holding:3000,financing:2000,targetProfit:30000,targetYield:10,rulePct:70,ltv:75,apr:6,years:30,loanFees:2000};
const tests=[];function test(name,fn){tests.push([name,fn]);}
function near(actual,expected){assert.ok(Math.abs(actual-expected)<.02,`${actual} ≈ ${expected}`);}
test('Empty data never derives ARV, rent or neighborhood from asking price',()=>{
    const r=A.calculate({...p,price:59000,beds:3},null,{});assert.equal(r.inputs.arv,null);assert.equal(r.inputs.rent,null);assert.equal(r.ruleMao,null);assert.equal(r.noi,null);assert.equal(r.rentalMao,null);
});
test('Source values initialize the scenario and explicit assumptions override only the scenario',()=>{
    const source={price:135000,rent:1820,arv:null,operatingExpenses:4000,vacancyLoss:1000};
    const before=JSON.stringify(source);let r=A.calculate(p,scenario({purchase:110000}),source);
    assert.equal(r.inputs.purchase,110000);assert.equal(r.inputs.rent,1820);assert.equal(r.origins.purchase,'assumption');assert.equal(r.origins.rent,'source');assert.equal(r.noi,16840);assert.equal(JSON.stringify(source),before);
});
test('Separate rental and flip rehabilitation feed exact ceilings and profits',()=>{
    const r=A.calculate(p,scenario(full));assert.equal(r.rentalAllIn,135000);assert.equal(r.noi,15600);assert.equal(r.rentalMao,121000);
    assert.equal(r.flipAllIn,160000);assert.equal(r.flipProfit,65600);assert.equal(r.flipMao,135600);assert.equal(r.ruleMao,118000);
    near(r.capRate,15.6);near(r.yieldOnCost,11.555555);near(r.flipRoi,41);assert.equal(r.rentalGap,-21000);assert.equal(r.flipGap,-35600);
});
test('Amortization, cash required and cash-on-cash use principal and interest without double counting NOI',()=>{
    const r=A.calculate(p,scenario(full));assert.equal(r.loan,75000);assert.equal(r.cashRequired,62000);near(r.monthlyDebt,449.6629);near(r.monthlyCashFlow,850.3371);near(r.cashOnCash,16.4581);
});
test('Explicit zero APR amortizes principal and cash purchase requires no interest rate',()=>{
    near(A.payment(120000,0,10),1000);let r=A.calculate(p,scenario({...full,ltv:0,apr:null,years:null}));assert.equal(r.monthlyDebt,0);assert.equal(r.cashRequired,137000);assert.equal(r.monthlyCashFlow,1300);
});
test('Missing costs never silently become zero or a complete profit',()=>{
    const r=A.calculate(p,scenario({...full,financing:null,loanFees:null}));assert.equal(r.flipAllIn,null);assert.equal(r.flipProfit,null);assert.equal(r.flipMao,null);assert.equal(r.cashRequired,null);assert.equal(r.cashOnCash,null);
});
test('Negative ceilings and loss-making deals remain visible and are not screened out',()=>{
    const r=A.calculate(p,scenario({...full,arv:50000,rentalRehab:300000}));assert.ok(r.flipMao<0);assert.ok(r.rentalMao<0);assert.ok(r.flipProfit<0);assert.match(A.summary(p,r),/אינו מאפשר מחיר רכישה חיובי/);
});
test('Known annual expenses take precedence over percentage; selected percentage replaces source budget',()=>{
    let r=A.calculate(p,scenario({...full,operatingPct:26}));assert.equal(r.operating,6000);
    r=A.calculate(p,scenario({...full,operatingAnnual:null,operatingPct:26}),{operatingExpenses:4000,vacancyLoss:1000});assert.equal(r.operating,5616);assert.equal(r.noi,15984);
});
test('Incomplete expense sources cannot turn missing vacancy into zero',()=>{
    const r=A.calculate(p,scenario({rent:1000}),{operatingExpenses:2000});assert.equal(r.noi,null);
});
test('Invalid values block save and their dependent calculations without falling back to source',()=>{
    for(const bad of [-1,NaN,Infinity,true,'abc','10.123','-2']){const s=scenario({...full,rent:bad});assert.throws(()=>A.serialize(s,p));assert.equal(A.calculate(p,s,{rent:2000}).noi,null);}
    for(const [key,value]of [['ltv',101],['apr',-1],['years',0],['targetYield',0],['arv',0]])assert.throws(()=>A.serialize(scenario({...full,[key]:value}),p));
});
test('Another property, city, unit or snapshot version cannot supply scenario assumptions',()=>{
    const s=scenario(full);for(const q of [{...p,id:'other'},{...p,address:'101 Example St UNIT 2'},{...p,city:'Erie'}])assert.equal(A.calculate(q,s,{}).inputs.purchase,null);
    assert.equal(A.calculate(p,{...s,version:999},{}).inputs.purchase,null);
});
test('Document preset requires explicit application and never fills ARV or rent',()=>{
    const s=A.documentPreset(p,scenario({purchase:80000}));assert.equal(s.inputs.flipRehab,72000);assert.equal(s.inputs.rentalRehab,43200);assert.equal(s.inputs.operatingPct,26);assert.equal(s.inputs.acquisition,5000);assert.equal(s.inputs.arv,null);assert.equal(s.inputs.rent,null);
    assert.equal(A.documentPreset({...p,sqft:null},null).inputs.flipRehab,null);
});
test('Printed analysis is static, escapes property content and labels assumptions',()=>{
    const q={...p,address:'<img src=x onerror=alert(1)>'},s={...scenario(full),identity:A.identity(q)};
    const html=A.reportHTML(q,s,{});assert.ok(!html.includes('<img'));assert.match(html,/&lt;img/);assert.match(html,/הנחה שהוזנה בתרחיש/);assert.match(html,/תזרים חודשי/);assert.ok(!html.includes('<input'));assert.ok(!html.includes('<button'));
});
test('Report persistence and export use dedicated scenario fields without replacing evidence',()=>{
    const html=fs.readFileSync(__dirname+'/report.html','utf8');assert.match(html,/'investment_scenario','report_listing_media'/);assert.match(html,/investment_scenario_snapshot/);assert.match(html,/await flushDealAnalysis\(\)/);assert.match(html,/התרחיש עודכן במכשיר אחר/);
    const adapter=fs.readFileSync(__dirname+'/deal-analysis-adapter.js','utf8');new vm.Script(adapter);assert.match(adapter,/source:financialSnapshot/);assert.match(adapter,/readOnly:!reportReady\|\|!!currentSavedReportId\|\|!cloudAvailable/);assert.ok(!adapter.includes('workflow_dispatch'));
});
test('Report gallery accepts only matching verified receipts and freezes historical media',()=>{
    const API=require('./secondary_sources.js');
    const subject={id:'PA-MLS-1234567',address:'101 Example St',city:'Pittsburgh',state:'PA',zip:'15203',county:'Allegheny',source_type:'mls',url:'https://www.redfin.com/PA/Pittsburgh/101-Example-St-15203/home/1234'};
    const photo={url:'https://api.cotality.com/trestle/Media/Property/PHOTO-Jpeg/12345/1/abc/def/ghi',source_url:'https://www.thepreferredrealty.com/real-estate/1234567/',listing_id:'1234567',provider:'preferredrealty',source_name:'MLS source',checked_at:'2026-10-20T12:00:00Z',verification:{status:'image_verified',bytes:30000,content_type:'image/jpeg',sha256:'a'.repeat(64)}};
    const receipt={schema:1,property_id:subject.id,listing_id:'1234567',identity:API.identity(subject),county:'Allegheny',inventory_source_url:subject.url,photos:[photo]};
    const media={property_id:subject.id,identity:API.identity(subject),receipt,additional:[]};
    const context=vm.createContext({window:{SecondarySourceEvidence:API},currentDeal:subject,currentSavedReportId:null});
    vm.runInContext(fs.readFileSync(__dirname+'/deal-analysis-adapter.js','utf8'),context);
    vm.runInContext('reportListingMedia='+JSON.stringify(media),context);assert.equal(context.reportOriginalPhotos().length,1);
    vm.runInContext('reportListingMedia.receipt.identity[0]="999 WRONG ST"',context);assert.equal(context.reportOriginalPhotos().length,0);
    vm.runInContext('reportListingMedia='+JSON.stringify(media),context);context.currentSavedReportId='old-report';assert.equal(context.reportOriginalPhotos().length,0);
    context.currentDeal={...subject,report_listing_media:media};assert.equal(context.reportOriginalPhotos().length,1);
    context.currentDeal={...subject,report_listing_media:{...media,property_id:'another'}};assert.equal(context.reportOriginalPhotos().length,0);
});
(async()=>{for(const [name,fn]of tests){try{await fn();console.log('PASS '+name);}catch(error){process.exitCode=1;console.error('FAIL '+name,error);}}console.log(tests.length+' scenario checks, offline with explicit fixtures.');})();
