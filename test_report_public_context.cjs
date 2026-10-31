'use strict';
const assert=require('node:assert/strict'),m=require('./report-public-context.js');
const now=Date.parse('2026-10-31T10:00:00Z'),p={id:'qa',address:'1 Main St',city:'Verona',state:'PA',zip:'15147'};
const parcel='0012E00199000000',county={parcel_id:parcel};
const c={version:'official-public-context-1',property_id:p.id,property:p,parcel_id:parcel,generated_at:'2026-10-31T09:00:00Z',municipality:'Penn Hills',
 education:{parcel_id:parcel,district:'Penn Hills SD',district_match_basis:'county_parcel_school_district',district_source_url:'https://data.wprdc.org/dataset/property-assessments',source_url:'https://www.futurereadypa.org/Home/DataFiles',source_period:'2024-2025',observed_at:'2026-10-31T09:00:00Z',source_name:'PDE',schools:[{school_name:'<script>fake</script>',metrics:[{label:'Attendance',value:0,unit:'percent'}]}]},crime:{source_url:'https://www.ucr.pa.gov/PAUCRSPUBLIC'}};
assert.ok(m.valid(c,p,county,now));assert.equal(m.valid(c,{...p,address:'2 Main St'},county,now),false);
assert.equal(m.valid(c,p,{parcel_id:'DIFFERENT'},now),false);
const html=m.render(c,p,county,now);assert.ok(html.includes('Penn Hills SD'));assert.ok(html.includes('0.0%'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));assert.ok(html.includes('אינה אישור לשיבוץ'));
assert.ok(!m.render({...c,education:{...c.education,district_match_basis:'postal_city'}},p,county,now).includes('Penn Hills SD'));
assert.ok(!m.render({...c,education:{...c.education,source_url:'https://untrusted.example/'}},p,county,now).includes('0.0%'));
console.log('PASS Official context rejects foreign parcels, postal school inference and untrusted performance URLs; zero and escaped labels preserved');
