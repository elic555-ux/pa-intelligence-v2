'use strict';
const assert=require('node:assert/strict'),S=require('./secondary_sources.js');
const p={id:'PA-MLS-1001',docket_id:'MLS-1001',source_type:'mls',address:'12 First St',city:'Pittsburgh',state:'PA',county:'Allegheny',zip:'15216',url:'https://www.redfin.com/PA/Pittsburgh/x/home/1001'};
const source='Tarasa / River Point Realty / MLS',url='https://www.tarasa.com/property-search/detail/56/1001/12-first-st-pittsburgh-pa-15216/';
const record={schema:1,provider:'tarasa',property_id:p.id,listing_id:'1001',identity:S.identity(p),inventory_source_url:p.url,source_url:url,source_name:source,status:'published',retrieved_at:'2026-10-26',
 subject:{street:'12 first st',unit:null,city:'pittsburgh',state:'PA',zip:'15216',county:'Allegheny',complete:true},
 photos:Array.from({length:80},(_,i)=>({url:'https://cdn.listingphotos.sierrastatic.com/pics1x/v123/56/56_1001_'+String(i+1).padStart(2,'0')+'.jpg',source,source_url:url,listing_id:'1001'}))};
assert.equal(S.photos(p,record).length,3);
assert.equal(S.combinedPhotos(p,[record]).length,3);
assert.equal(S.reportPhotos(p,{additional:[record]}).length,60);
const other=structuredClone(record);other.photos=other.photos.map(x=>({...x,url:x.url.replace('pics1x','pics3x').replace('v123','v456')}));
assert.equal(S.reportPhotos(p,{additional:[record,other]}).length,60);
assert.equal(S.photoIdentity(record.photos[0].url),S.photoIdentity(other.photos[0].url));
assert.notEqual(S.photoIdentity(record.photos[0].url),S.photoIdentity(record.photos[0].url.replace('_1001_','_9999_')));
assert.equal(S.reportPhotos({...p,address:'22 Other St'},{additional:[record]}).length,0);
const contaminated=structuredClone(record);contaminated.photos[3].url=contaminated.photos[3].url.replace('_1001_','_9999_');contaminated.photos[4].source='Unverified';
assert.ok(S.reportPhotos(p,{additional:[contaminated]}).every(x=>x.listing_id==='1001'&&!x.url.includes('_9999_')&&x.source===source));
console.log('8 report gallery checks passed');
