'use strict';
const assert=require('node:assert/strict'),m=require('./report-readonly-model.js');
const r={photo_count:4,photos:[1,2,3,4].map(i=>({id:'photo_'+i})),photo_coverage:[{photo_id:'photo_1',scene:'exterior'},{photo_id:'photo_2',scene:'kitchen'},{photo_id:'photo_3',scene:'kitchen'},{photo_id:'photo_4',scene:'unclear'}]};
let c=m.photoCoverage(r);assert.equal(c.interior,2);assert.equal(c.exterior,1);assert.equal(c.unclear,1);assert.deepEqual(c.scenes,['kitchen']);assert.deepEqual(c.missing,['bathroom','bedroom','living_dining']);
assert.equal(m.photoCoverage({...r,photo_coverage:r.photo_coverage.slice(0,3)}),null);
assert.equal(m.photoCoverage({...r,photo_coverage:[r.photo_coverage[0],r.photo_coverage[1],r.photo_coverage[2],r.photo_coverage[2]]}),null);
assert.equal(m.photoCoverage({...r,photo_coverage:r.photo_coverage.map((x,i)=>i===3?{...x,photo_id:'foreign'}:x)}),null);
assert.equal(m.photoCoverage({...r,photo_coverage:r.photo_coverage.map((x,i)=>i===3?{...x,scene:'invented-room'}:x)}),null);
assert.equal(m.photoCoverage({photo_count:12}),null);
console.log('PASS Photo coverage distinguishes interior/exterior/unclear images and never counts angles as rooms');

const assessed={...r,interior_assessments:['flooring','walls_ceilings','doors_windows','cabinets_counters','fixtures'].map(component=>({scene:'kitchen',component,condition:'not_assessable',finding:'לא נראה בבירור',scope:'נדרשת בדיקה לפני קביעת עבודה',rationale:'הזווית אינה מציגה מספיק פרטים',photo_ids:['photo_2']}))};
assert.equal(m.interiorAssessments(assessed).length,5);
assert.equal(m.interiorAssessments({...assessed,interior_assessments:assessed.interior_assessments.slice(1)}).length,0);
assert.equal(m.interiorAssessments({...assessed,interior_assessments:assessed.interior_assessments.map((x,i)=>i?x:{...x,photo_ids:['photo_1']})}).length,0);
assert.equal(m.interiorAssessments({...assessed,interior_assessments:assessed.interior_assessments.map((x,i)=>i?x:{...x,condition:'fully_working'})}).length,0);
assert.equal(m.interiorAssessments(r).length,0);
for(const condition of ['visible_damage','cosmetic_optional','no_obvious_damage_in_view','not_assessable','visible_wear']){
 const rows=assessed.interior_assessments.map(x=>({...x,condition}));
 const normalized=m.interiorAssessments({...assessed,interior_assessments:rows});
 assert.equal(normalized.length,5);assert.ok(normalized.every(x=>x.condition===condition));
}
console.log('PASS Every photographed interior area needs all component assessments grounded in its own photos');

const reviewed={...r,photo_coverage:r.photo_coverage.map(x=>({...x,condition:x.scene==='unclear'?'not_assessable':'no_obvious_damage_in_view',finding:'ממצא מהתמונה'})),photo_intake:{available_links:7,candidate_links:6,prepared_photos:4,unavailable:1,duplicate_bytes:1,excluded_by_byte_limit:0,not_attempted:0}};
let review=m.photoReview(reviewed);
assert.equal(review.available,7);assert.equal(review.sent,4);assert.equal(review.reviewed,4);
assert.equal(review.unassessable,1);assert.equal(review.omitted,2);assert.equal(review.duplicates,1);assert.equal(review.complete,false);
assert.equal(m.photoReview(r).reviewed,0);assert.equal(m.photoReview(r).complete,false);
const full={photo_count:22,photos:Array.from({length:22},(_,i)=>({id:'photo_'+(i+1)})),photo_coverage:Array.from({length:22},(_,i)=>({photo_id:'photo_'+(i+1),scene:'exterior',condition:'visible_damage',finding:'שבר נראה'})),photo_intake:{available_links:22,candidate_links:22,prepared_photos:22,unavailable:0,duplicate_bytes:0,excluded_by_byte_limit:0,not_attempted:0}};
assert.equal(m.photoReview(full).complete,true);assert.equal(m.photoReview(full).rows.length,22);
assert.equal(m.photoReview({...full,photo_coverage:full.photo_coverage.slice(0,21)}).complete,false);
assert.equal(m.photoReview({...full,photo_coverage:full.photo_coverage.map((x,i)=>i===21?{...x,finding:''}:x)}).reviewed,21);
assert.equal(m.photoReview({...full,photo_intake:{...full.photo_intake,available_links:70}}).omitted,48);
assert.equal(m.photoReview({...full,photo_intake:{...full.photo_intake,unavailable:9}}).available,null);
console.log('PASS Per-photo review distinguishes sent, assessed, unassessable, omitted and duplicate images; legacy room labels do not count as condition reviews');

const outside={...r,exterior_assessments:['roof_surface','exterior_walls','doors_windows','gutters_downspouts','steps_railings','yard_paving'].map(component=>({component,condition:'not_assessable',finding:'לא ניתן להעריך',scope:'בדיקה לפני החלטה',rationale:'זווית הצילום אינה מספקת',photo_ids:['photo_1']}))};
assert.equal(m.exteriorAssessments(outside).length,6);
assert.equal(m.exteriorAssessments(r).length,0);
assert.equal(m.exteriorAssessments({...outside,exterior_assessments:outside.exterior_assessments.slice(1)}).length,0);
assert.equal(m.exteriorAssessments({...outside,exterior_assessments:outside.exterior_assessments.map((x,i)=>i?x:{...x,photo_ids:['photo_2']})}).length,0);
assert.equal(m.exteriorAssessments({...outside,exterior_assessments:outside.exterior_assessments.map((x,i)=>i?x:{...x,component:'yard_paving'})}).length,0);
assert.equal(m.exteriorAssessments({...outside,exterior_assessments:outside.exterior_assessments.map((x,i)=>i?x:{...x,condition:'fully_working'})}).length,0);
console.log('PASS Exterior detail requires all six components grounded in exterior views; legacy and incomplete records do not pass');
