/* Exercise the real controller with local-only source/DOM stubs, no browser or API. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const model=require('./report-readonly-model.js'),financials=require('./report-financials.js');
const controller=fs.readFileSync(__dirname+'/report-readonly.js','utf8');
async function simulate(shared,{collect=false,ready=false,storageFailure=false}={}){
 const property={id:'qa-1',address:'18 Main St',city:'Pittsburgh',state:'PA',zip:'15203',county:'Allegheny',property_type:'Single Family',price:135000,url:'https://example.com/listing',last_source_check:new Date().toISOString()};
 const elements=new Map(),posts=[],timers=[];
 function element(id){if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',hidden:false,dataset:{},classList:{toggle(){}},addEventListener(){},querySelectorAll(){return[];},scrollIntoView(){},insertAdjacentHTML(){}});return elements.get(id);}
 const location=new URL('http://localhost/report.html?id=qa-1'+(collect?'&collect=1':''));
 const storage={getItem:key=>key==='pa_github_token'?'test-pat-not-real':shared.get(key)||null,setItem(key,value){if(storageFailure)throw Error('quota');shared.set(key,value);},removeItem:key=>shared.delete(key)};
 const context={URL,URLSearchParams,Uint8Array,TextDecoder,TextEncoder,Date,crypto:crypto.webcrypto,
   atob:text=>Buffer.from(text,'base64').toString('binary'),localStorage:storage,location,
   history:{replaceState(_,__,value){location.href=new URL(value,location).href;}},
   document:{getElementById:element,querySelectorAll(){return[];}},
   PAReadonlyReport:model,PAReportFinancials:financials,
   SecondarySourceEvidence:{originalPhotos(){return[];},fallbackPhotos(){return[];},combinedPhotos(){return[];}},
   supabase:{createClient(){return {from(){return {select(){return {eq:async()=>({data:[{deal_data:property}],error:null})};}};}};}},
   setTimeout:fn=>timers.push(fn),addEventListener(){},
   fetch:async(url,options={})=>{
     if(options.method==='POST'){posts.push({url,body:JSON.parse(options.body)});return new Response(null,{status:204});}
     if(ready&&url.includes('/expanded_receipts/')){
       const receipt={property_id:property.id,property,generated_at:new Date().toISOString(),sources:{}};
       return new Response(JSON.stringify({encoding:'base64',content:Buffer.from(JSON.stringify(receipt)).toString('base64')}),{status:200});
     }
     return new Response('{}',{status:404});
   }};
 context.window=context;
 vm.runInNewContext(controller,context);
 for(let i=0;i<150;i++){
   await new Promise(resolve=>setTimeout(resolve,1));
   const text=element('report-status').textContent;
   if(timers.length||text.includes('האיסוף הסתיים')||text.includes('בקשת איסוף חדשה לא נשלחה'))return{posts,timers,shared,status:text};
 }
 throw Error('Controller did not reach a settled collection state: '+element('report-status').textContent);
}
(async()=>{
 const shared=new Map();
 let result=await simulate(shared,{collect:true});assert.equal(result.posts.length,1);assert.equal(result.timers.length,1);
 const first=JSON.parse(result.posts[0].body.inputs.payload).request_id;
 assert.ok([...shared.values()].some(v=>v.includes(first)));console.log('PASS Persist request identity before the first dispatch');
 result=await simulate(shared);assert.equal(result.posts.length,0);assert.equal(result.timers.length,1);console.log('PASS Reload resumes polling without another workflow dispatch');
 result=await simulate(shared,{ready:true});assert.equal(result.posts.length,0);assert.equal(shared.size,0);console.log('PASS Matching receipt clears only the completed pending request');
 result=await simulate(new Map(),{collect:true,storageFailure:true});assert.equal(result.posts.length,0);console.log('PASS Failed local reservation prevents a workflow dispatch');
})().catch(error=>{console.error(error);process.exitCode=1;});

