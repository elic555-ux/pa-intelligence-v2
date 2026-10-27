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
