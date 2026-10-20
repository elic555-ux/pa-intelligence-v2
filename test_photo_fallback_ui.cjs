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
test('public MLS gallery requires the current MLS route and its specific media path',()=>{
    const r=receipt();
    for(const p of r.photos) Object.assign(p,{provider:'preferredrealty',source_url:'https://www.thepreferredrealty.com/real-estate/1001/',
        url:'https://api.cotality.com/trestle/Media/Property/PHOTO-Jpeg/1193411277/'+(r.photos.indexOf(p)+1)+'/encoded/encoded/signature'});
    assert.equal(api.fallbackPhotos(row,r).length,3);
    for(const mutate of [p=>p.source_url=p.source_url.replace('1001','9999'),p=>p.url+='?token=x',
        p=>p.url=p.url.replace('api.cotality.com','api.cotality.com.evil.test'),p=>p.url='https://api.cotality.com/a.jpg',
        p=>p.verification.status='candidate',p=>p.provider='weichert']) {
        const wrong=structuredClone(r);mutate(wrong.photos[0]);assert.equal(api.fallbackPhotos(row,wrong).length,2);
    }
});
test('original Redfin photos require the current address, listing and source binding',()=>{
    const p={...row,url:'https://www.redfin.com/PA/Pittsburgh/12-First-St-15216/home/1'};
    p.listing_source_details={status:'published',identity:api.identity(p),listing_id:'1001',source_url:p.url};
    p.listing_photos=[{url:'https://ssl.cdn-redfin.com/photo/162/bigphoto/001/1001_0.jpg',source_url:p.url,source:'Redfin / MLS'}];
    assert.equal(api.originalPhotos(p).length,1);
    for(const change of [q=>q.address='14 First St',q=>q._idAmbiguous=true,q=>q.docket_id='MLS-9999',
        q=>q.listing_source_details.status='source_blocked',q=>q.listing_photos[0].source_url+='wrong',
        q=>q.listing_photos[0].url='https://evil.test/photo/a.jpg',q=>q.listing_photos[0].url+='?token=x']) {
        const invalid=structuredClone(p);change(invalid);assert.equal(api.originalPhotos(invalid).length,0);
    }
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

