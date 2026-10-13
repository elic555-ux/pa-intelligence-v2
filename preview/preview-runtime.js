/* Isolated preview: local data only, no production clients or server writes. */
(function(root){
'use strict';
const base=new URL('./',document.currentScript?.src||root.location.href);
const prefix='pa-preview-v1:'+base.pathname+':';
const nativeStorage=root.localStorage;
const nativeFetch=root.fetch.bind(root);
const tables=new Set(['pipeline_deals','user_watchlist','archive_deals','saved_reports']);
const copy=x=>JSON.parse(JSON.stringify(x));
const telemetry={localWrites:0,blockedRequests:0};
const storage={
 getItem(key){return key==='pa_github_token'?'preview-only':nativeStorage.getItem(prefix+key);},
 setItem(key,value){if(key!=='pa_github_token')nativeStorage.setItem(prefix+key,String(value));},
 removeItem(key){nativeStorage.removeItem(prefix+key);},
 clear(){for(let i=nativeStorage.length-1;i>=0;i--){const k=nativeStorage.key(i);if(k?.startsWith(prefix))nativeStorage.removeItem(k);}},
 key(i){const keys=[];for(let n=0;n<nativeStorage.length;n++){const k=nativeStorage.key(n);if(k?.startsWith(prefix))keys.push(k.slice(prefix.length));}return keys[i]||null;},
 get length(){let n=0;for(let i=0;i<nativeStorage.length;i++)if(nativeStorage.key(i)?.startsWith(prefix))n++;return n;}
};
Object.defineProperty(root,'localStorage',{value:storage,configurable:false});
const loadTable=name=>{try{const r=JSON.parse(storage.getItem('table:'+name)||'[]');return Array.isArray(r)?r:[];}catch(_){return [];}};
const storeTable=(name,rows)=>{storage.setItem('table:'+name,JSON.stringify(rows));telemetry.localWrites++;};
class Query {
 constructor(table){this.table=table;this.mode='select';this.filters=[];}
 select(){return this;}
 eq(field,value){this.filters.push(r=>String(r[field])===String(value));return this;}
 neq(field,value){this.filters.push(r=>String(r[field])!==String(value));return this;}
 order(field,options={}){this.sort=[field,options.ascending!==false];return this;}
 insert(value){this.mode='insert';this.value=value;return this;}
 upsert(value,options={}){this.mode='upsert';this.value=value;this.conflict=options.onConflict||'property_id';return this;}
 update(value){this.mode='update';this.value=value;return this;}
 delete(){this.mode='delete';return this;}
 single(){this.one=true;return this;}
 maybeSingle(){this.one=true;this.optional=true;return this;}
 async execute(){
  if(!tables.has(this.table))return{data:null,error:{message:'Unknown preview table'}};
  let rows=loadTable(this.table),result=[];
  const match=row=>this.filters.every(fn=>fn(row));
  if(this.mode==='insert'||this.mode==='upsert'){
   for(const item of Array.isArray(this.value)?this.value:[this.value]){
    const value={id:root.crypto.randomUUID(),created_at:new Date().toISOString(),updated_at:new Date().toISOString(),...copy(item)};
    const index=this.mode==='upsert'?rows.findIndex(r=>String(r[this.conflict])===String(value[this.conflict])):-1;
    if(index>=0)rows[index]={...rows[index],...value,id:rows[index].id};
    else rows.push(value);
    result.push(copy(index>=0?rows[index]:value));
   }
   storeTable(this.table,rows);
  }else if(this.mode==='update'){
   rows=rows.map(row=>{if(!match(row))return row;const next={...row,...copy(this.value),updated_at:new Date().toISOString()};result.push(next);return next;});
   storeTable(this.table,rows);
  }else if(this.mode==='delete'){
   result=rows.filter(match);rows=rows.filter(r=>!match(r));storeTable(this.table,rows);
  }else result=rows.filter(match);
  if(this.sort){const[field,ascending]=this.sort;result.sort((a,b)=>String(a[field]||'').localeCompare(String(b[field]||''))*(ascending?1:-1));}
  return{data:this.one?(copy(result[0]||null)):copy(result),error:this.one&&!this.optional&&!result.length?{message:'No preview record'}:null};
 }
 then(a,b){if(!this.promise)this.promise=this.execute();return this.promise.then(a,b);}
}
const user={id:'preview-owner',email:'preview@example.invalid'};
root.supabase={createClient(){return{
 from:table=>new Query(table),
 auth:{
  getUser:async()=>({data:{user},error:null}),getSession:async()=>({data:{session:{user}},error:null}),
  signInWithPassword:async()=>({data:{user},error:null}),signOut:async()=>({error:null}),
  onAuthStateChange:()=>({data:{subscription:{unsubscribe(){}}}})
 }
};}};
function denied(){telemetry.blockedRequests++;throw new Error('בסביבת הבדיקה אין פנייה לשירותים פעילים. נתוני הניסוי נשמרים בדפדפן בלבד.');}
function asset(path){
 if(!path||path.includes('\\')||path.split('/').includes('..')||path.startsWith('/')||path.includes('\0'))return null;
 const url=new URL(path,base);
 return url.origin===base.origin&&url.pathname.startsWith(base.pathname)?url:null;
}
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json; charset=utf-8'}});
root.fetch=async function(input,options={}){
 const method=String(options.method||input?.method||'GET').toUpperCase();if(method!=='GET')return denied();
 const url=new URL(typeof input==='string'||input instanceof URL?String(input):input.url,root.location.href);
 if(url.hostname==='api.github.com'){
  const match=url.pathname.match(/^\/repos\/[^/]+\/[^/]+\/contents\/(.+)$/);
  if(match){
   const target=asset(decodeURIComponent(match[1]));if(!target)return denied();
   const response=await nativeFetch(target.href,{method:'GET',credentials:'omit',cache:'no-store'});
   if(!response.ok)return json({message:'Preview fixture unavailable'},404);
   const text=await response.text();
   return json({encoding:'base64',content:btoa(unescape(encodeURIComponent(text))),sha:'0000000000000000000000000000000000000001',path:match[1]});
  }
  if(/\/actions\/workflows\/[^/]+$/.test(url.pathname))return json({state:'disabled',path:'.github/workflows/preview.yml'});
  if(/\/actions\/workflows\/[^/]+\/runs$/.test(url.pathname))return json({workflow_runs:[]});
  return denied();
 }
 if(url.origin!==base.origin||!url.pathname.startsWith(base.pathname))return denied();
 return nativeFetch(url.href,{method:'GET',credentials:'omit',cache:'no-store'});
};
root.PaPreview=Object.freeze({
 prefix,telemetry,reset(){storage.clear();root.location.reload();},
 message(text='זו סביבת בדיקה: השמירה מקומית, וסריקות ושירותים חיצוניים כבויים.'){
  let n=document.getElementById('pa-preview-message');
  if(!n){n=document.createElement('div');n.id='pa-preview-message';n.setAttribute('role','status');n.style.cssText='position:fixed;bottom:20px;right:20px;left:20px;z-index:100000;background:#78350f;color:white;border:1px solid #fbbf24;border-radius:12px;padding:16px;font:700 16px sans-serif;direction:rtl';document.body.appendChild(n);}
  n.textContent=text;
 }
});
})(window);
