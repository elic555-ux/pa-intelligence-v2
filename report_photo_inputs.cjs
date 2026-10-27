/* Reuse the report gallery's identity/source checks; never guess image URLs. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const S=require('./secondary_sources.js');
const MAX_PHOTOS=60;
function read(file){try{return JSON.parse(fs.readFileSync(file,'utf8'));}catch(_){return null;}}
function collect(p,repo){
  const rows=read(path.join(repo,'properties.json'));
  const matches=(Array.isArray(rows)?rows:[]).filter(r=>String(r.id)===String(p.id));
  if(matches.length!==1||JSON.stringify(S.identity(matches[0]))!==JSON.stringify(S.identity(p)))return {status:'subject_mismatch',photos:[],facts:{}};
  const subject=matches[0],key=crypto.createHash('sha256').update(String(p.id)).digest('hex').slice(0,32),root=path.join(repo,'COMPS_REPORTS');
  const details=read(path.join(root,'listing_details',key+'.json'));
  const additional=['howardhanna','eriemoves','tarasa','clearchoice'].map(x=>read(path.join(root,'additional_sources',x,key+'.json'))).filter(Boolean);
  const photos=S.reportPhotos(subject,{details,receipt:read(path.join(root,'listing_photos',key+'.json')),additional},Infinity);
  const seen=new Set(),facts={};
  for(const name of ['roof_type','heating','cooling','construction','basement','stories','water','sewer','hvac_type']){
    const fact=S.bestFact(subject,additional,name);if(fact)facts[name]=fact;
  }
  const unique=photos.filter(r=>!seen.has(r.url)&&seen.add(r.url));
  return {status:'verified',property_id:String(p.id),identity:S.identity(subject),facts,
    available_photo_count:unique.length,photos:unique.slice(0,MAX_PHOTOS)};
}
module.exports={collect,MAX_PHOTOS};
if(require.main===module){
  const p=JSON.parse(fs.readFileSync(0,'utf8'));
  process.stdout.write(JSON.stringify(collect(p,path.resolve(process.argv[2]||'.'))));
}
