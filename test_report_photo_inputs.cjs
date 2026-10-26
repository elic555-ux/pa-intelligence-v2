'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {collect}=require('./report_photo_inputs.cjs'),S=require('./secondary_sources.js');
const originalRead=fs.readFileSync,reportPhotos=S.reportPhotos;
let rows=[],checked=0;
fs.readFileSync=function(file,...args){if(String(file).endsWith('properties.json'))return JSON.stringify(rows);return originalRead.call(this,file,...args);};
const p={id:'qa-1',address:'18 Main St',city:'Pittsburgh',state:'PA',zip:'15203'};
try{
  assert.equal(collect(p,'offline').status,'subject_mismatch');checked++;
  rows=[{...p,address:'22 Other St'}];assert.equal(collect(p,'offline').status,'subject_mismatch');checked++;
  rows=[p,p];assert.equal(collect(p,'offline').status,'subject_mismatch');checked++;
  rows=[p];assert.deepEqual(collect(p,'offline').photos,[]);checked++;
  S.reportPhotos=()=>[{url:'https://ssl.cdn-redfin.com/photo/verified.jpg',source_url:'https://www.redfin.com/PA/Pittsburgh/x/home/1'}];
  assert.equal(collect(p,'offline').photos.length,1);checked++;
  S.reportPhotos=()=>Array.from({length:70},(_,i)=>({url:'https://ssl.cdn-redfin.com/photo/'+i+'.jpg',source_url:'https://www.redfin.com/PA/Pittsburgh/x/home/1'}));
  const wide=collect(p,'offline');assert.equal(wide.photos.length,60);assert.ok(wide.photos[59].url.endsWith('/59.jpg'));checked++;
  console.log(checked+' photo input identity checks passed');
}finally{fs.readFileSync=originalRead;S.reportPhotos=reportPhotos;}
