const assert=require('node:assert/strict');
const fs=require('node:fs');
const G=require('./report-area-grades.js');
const now=Date.parse('2026-11-01T12:00:00Z');
const county=(name='Penn Hills',c='Allegheny')=>({status:'matched',parcel_id:'0035N00138000000',county:c,fields:{municipality:name}});
assert.equal(G.resolve(county(),null,now).overall,'B');
assert.equal(G.resolve(county('Verona'),null,now).safety,'B+');
assert.equal(G.resolve(county('Verona','Erie'),null,now),null);
assert.equal(G.resolve(county('Fairview','Erie'),null,now),null); // Township and CDP are distinct.
assert.equal(G.resolve(county('Girard','Erie'),null,now).overall,'A-');
assert.equal(G.resolve(county('Girard Township','Erie'),null,now).overall,'B');
assert.equal(G.resolve({status:'unmatched',city:'Verona'},null,now),null);
assert.equal(G.resolve(county(),null,Date.parse('2026-10-01')),null);
assert.equal(G.resolve(county(),null,now+401*86400000),null);
const c=county('19th Ward - PITTSBURGH');
const point={parcel_id:c.parcel_id,municipality:'Pittsburgh',neighborhood:'Beechview',source_url:'https://data.wprdc.org/dataset/parcel-centroids-in-allegheny-county-with-geographic-identifiers'};
assert.equal(G.resolve(c,point,now).name,'Beechview');
assert.equal(G.resolve(c,{...point,parcel_id:'other'},now).name,'Pittsburgh');
assert.equal(G.resolve(c,{...point,source_url:'https://example.com'},now).name,'Pittsburgh');
assert.equal(G.resolve(c,{...point,municipality:'Verona'},now).name,'Pittsburgh');
const html=G.render(c,point,now);
assert.match(html,/>A<\/strong>/);
assert.match(html,/>—<\/strong>/); // Never borrow the city's crime grade for a neighborhood.
assert.match(html,/n\/beechview-pittsburgh-pa/);
assert.ok(!/פרימיום|סיכון גבוה|משפחות יציב/.test(html));
assert.equal((G.render(null,null,now).match(/>—<\/strong>/g)||[]).length,2);
for(const r of G.records()){
 assert.ok(/^[A-D][+-]?$/.test(r.overall));
 assert.ok(r.safety===null||/^[A-D][+-]?$/.test(r.safety));
 assert.equal(new URL(r.url).hostname,'www.niche.com');
}
const page=fs.readFileSync(__dirname+'/report.html','utf8');
assert.ok(page.indexOf('src="report-area-grades.js')<page.indexOf('src="report-readonly.js'));
console.log('External grades: geography, missing values, freshness, provenance and integration passed');
