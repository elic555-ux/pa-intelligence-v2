'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const api=require('./secondary_sources.js');
const row={id:'PA-MLS-1001',docket_id:'MLS-1001',source_type:'mls',address:'12 First St',city:'Pittsburgh',
    state:'PA',county:'Allegheny',zip:'15216',url:'https://www.redfin.com/x/home/1'};
const source='https://www.coldwellbankerhomes.com/pa/pittsburgh/12-first-st/pid_12345/';
function receipt(){
    return {schema:1,property_id:row.id,listing_id:'1001',identity:api.identity(row),county:'Allegheny',
        inventory_source_url:row.url,photos:[1,2,3].map(n=>({
            url:'https://m1.cbhomes.com/p/716/1001/image'+n+'.jpg',provider:'coldwellbanker',
            source_url:source,listing_id:'1001',source_name:'Coldwell Banker / MLS',
            checked_at:'2026-10-18T13:00:00Z',verification:{status:'image_verified',content_type:'image/jpeg',bytes:200,sha256:'a'.repeat(64)}
        })),attempts:[{provider:'coldwellbanker',status:'available'}]};
}
test('independently bound and checked fallback images accepted',()=>{
    assert.equal(api.fallbackPhotos(row,receipt()).length,3);
});
test('wrong source, listing, current identity, credentials, HTML and unverified images rejected',()=>{
    for(const mutate of [r=>r.identity[0]='14 FIRST ST',r=>r.inventory_source_url+='wrong',
        r=>r.photos[0].listing_id='9999',r=>r.photos[0].url='https://evil.test/a.jpg',
        r=>r.photos[0].source_url='https://evil.test/a',r=>r.photos[0].verification.status='candidate',
        r=>r.photos[0].verification.content_type='text/html',r=>r.photos[0].url='https://user:pass@m1.cbhomes.com/a.jpg']) {
        const r=receipt();mutate(r);assert.ok(api.fallbackPhotos(row,r).length<3);
    }
    assert.equal(api.fallbackPhotos({...row,_idAmbiguous:true},receipt()).length,0);
    assert.equal(api.fallbackPhotos({...row,address:'12 First St #2'},receipt()).length,0);
});
test('receipt reports attempted sources without claiming scan failure or browser load success',()=>{
    const r=receipt();r.photos=[];r.sources_exhausted=true;
    assert.match(api.fallbackNotice(row,r),/Coldwell Banker/);
    assert.match(api.fallbackNotice(row,r),/הסריקה ממשיכה/);
    assert.doesNotMatch(api.fallbackNotice(row,r),/הסריקה נכשלה/);
    assert.doesNotMatch(api.fallbackNotice(row,receipt()),/תמונות שנטענו בדפדפן/);
});
