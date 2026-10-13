/* Local persistence, namespace isolation and network restrictions. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const{webcrypto}=require('node:crypto');
const items=new Map([['pa_github_token','production-secret'],['pa_viewed_deals','["production"]'],['unrelated','keep']]);
const storage={getItem:k=>items.get(k)??null,setItem:(k,v)=>items.set(k,String(v)),removeItem:k=>items.delete(k),key:i=>[...items.keys()][i]??null,get length(){return items.size;}};
const nativeRequests=[];
const root={location:{href:'https://example.test/preview/index.html',reload(){}},localStorage:storage,crypto:webcrypto,
 fetch:async(url,options)=>{nativeRequests.push([url,options]);return new Response(JSON.stringify({message:'שלום',value:1}),{status:200});}};
const context=vm.createContext({window:root,document:{currentScript:{src:'https://example.test/preview/preview-runtime.js'}},
 URL,Response,Date,Set,Object,JSON,String,Array,Promise,Error,encodeURIComponent,unescape,btoa:x=>Buffer.from(x,'binary').toString('base64')});
vm.runInContext(fs.readFileSync('preview/preview-runtime.js','utf8'),context);
(async()=>{
 const db=root.supabase.createClient('https://production.supabase.co','secret');
 assert.equal((await db.auth.getUser()).data.user.id,'preview-owner');
 assert.equal(root.localStorage.getItem('pa_github_token'),'preview-only');
 assert.equal(root.localStorage.getItem('pa_viewed_deals'),null);
 root.localStorage.setItem('pa_viewed_deals','["preview"]');
 assert.equal(items.get('pa_viewed_deals'),'["production"]');
 assert.equal(root.localStorage.getItem('pa_viewed_deals'),'["preview"]');
 await db.from('user_watchlist').upsert({property_id:'test'});
 await db.from('user_watchlist').upsert({property_id:'test',flag:1});
 assert.equal((await db.from('user_watchlist').select('*')).data.length,1);
 assert.equal((await db.from('user_watchlist').select('*').eq('property_id','test')).data[0].flag,1);
 await db.from('pipeline_deals').upsert({property_id:'test',deal_data:{id:'test'}});
 await db.from('saved_reports').insert({address:'Test',report_data:{id:'test'}});
 assert.equal((await db.from('saved_reports').select('*')).data.length,1);
 await db.from('user_watchlist').delete().eq('property_id','test');
 assert.equal((await db.from('user_watchlist').select('*')).data.length,0);
 assert.ok((await db.from('production_table').select('*')).error);
 for(const[url,options]of [
  ['https://api.github.com/repos/owner/repo/contents/config.json',{method:'PUT'}],
  ['https://api.github.com/repos/owner/repo/actions/workflows/job/dispatches',{method:'POST'}],
  ['https://production.supabase.co/rest/v1/pipeline_deals',{method:'GET'}],
  ['https://api.rentcast.io/v1/avm/rent/long-term',{method:'GET'}],
  ['https://example.test/properties.json',{}],
  ['https://api.github.com/repos/owner/repo/contents/%2e%2e/secrets',{}]
 ])await assert.rejects(()=>root.fetch(url,options));
 assert.equal(nativeRequests.length,0);
 const response=await root.fetch('https://api.github.com/repos/owner/repo/contents/data.json?ref=main',{headers:{Authorization:'Bearer real-token'}});
 const payload=await response.json();
 assert.equal(JSON.parse(Buffer.from(payload.content,'base64').toString('utf8')).message,'שלום');
 assert.equal(nativeRequests[0][0],'https://example.test/preview/data.json');
 assert.equal(nativeRequests[0][1].credentials,'omit');
 assert.equal(nativeRequests[0][1].headers,undefined);
 await root.fetch('properties.json?t=1');
 assert.equal(nativeRequests[1][0],'https://example.test/preview/properties.json?t=1');
 root.localStorage.clear();
 assert.equal(items.get('pa_github_token'),'production-secret');
 assert.equal(items.get('pa_viewed_deals'),'["production"]');
 assert.equal(items.get('unrelated'),'keep');
 assert.equal(root.localStorage.length,0);
 for(const name of ['index.html','deals.html','report.html','map.html']){
  const html=fs.readFileSync('preview/'+name,'utf8');
  assert.match(html,/Content-Security-Policy/);
  assert.match(html,/connect-src 'self'/);
  assert.match(html,/preview-runtime\.js/);
  assert.match(html,/preview-hooks\.js/);
  assert.doesNotMatch(html,/\.supabase\.co|@supabase\/supabase-js/);
 }
 console.log('PASS preview isolation, local saves, production-key preservation, blocked writes, fixture routing and CSP');
})().catch(error=>{console.error(error);process.exit(1);});
