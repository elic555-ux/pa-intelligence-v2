/* Manually verified published grades. No scoring, ZIP matching, or live scraping. */
(function(root,factory){'use strict';if(typeof module==='object'&&module.exports)module.exports=factory();else root.PAReportAreaGrades=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const RECORDS=[
  {
    "county": "Allegheny",
    "name": "Penn Hills Township",
    "aliases": [
      "Penn Hills",
      "Penn Hills Township"
    ],
    "overall": "B",
    "safety": "C-",
    "url": "https://www.niche.com/places-to-live/penn-hills-township-allegheny-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Allegheny",
    "name": "Verona",
    "aliases": [
      "Verona",
      "Verona Borough"
    ],
    "overall": "A-",
    "safety": "B+",
    "url": "https://www.niche.com/places-to-live/verona-allegheny-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Allegheny",
    "name": "Pittsburgh",
    "aliases": [
      "Pittsburgh",
      "City of Pittsburgh"
    ],
    "overall": "A",
    "safety": "C",
    "url": "https://www.niche.com/places-to-live/pittsburgh-allegheny-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Erie",
    "aliases": [
      "Erie",
      "Erie City",
      "City of Erie"
    ],
    "overall": "B",
    "safety": "C-",
    "url": "https://www.niche.com/places-to-live/erie-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Millcreek Township",
    "aliases": [
      "Millcreek Township",
      "Millcreek"
    ],
    "overall": "A-",
    "safety": null,
    "url": "https://www.niche.com/places-to-live/millcreek-township-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Fairview Township",
    "aliases": [
      "Fairview Township"
    ],
    "overall": "B+",
    "safety": null,
    "url": "https://www.niche.com/places-to-live/fairview-township-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Harborcreek Township",
    "aliases": [
      "Harborcreek Township",
      "Harborcreek"
    ],
    "overall": "B+",
    "safety": null,
    "url": "https://www.niche.com/places-to-live/harborcreek-township-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Wesleyville",
    "aliases": [
      "Wesleyville",
      "Wesleyville Boro",
      "Wesleyville Borough"
    ],
    "overall": "B",
    "safety": "B+",
    "url": "https://www.niche.com/places-to-live/wesleyville-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Edinboro",
    "aliases": [
      "Edinboro",
      "Edinboro Borough"
    ],
    "overall": "A",
    "safety": "A-",
    "url": "https://www.niche.com/places-to-live/edinboro-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Girard Borough",
    "aliases": [
      "Girard",
      "Girard Borough",
      "Girard Boro"
    ],
    "overall": "A-",
    "safety": "B+",
    "url": "https://www.niche.com/places-to-live/girard-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Girard Township",
    "aliases": [
      "Girard Township"
    ],
    "overall": "B",
    "safety": null,
    "url": "https://www.niche.com/places-to-live/girard-township-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Corry",
    "aliases": [
      "Corry",
      "City of Corry"
    ],
    "overall": "C+",
    "safety": "B-",
    "url": "https://www.niche.com/places-to-live/corry-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Erie",
    "name": "Wattsburg",
    "aliases": [
      "Wattsburg",
      "Wattsburg Boro",
      "Wattsburg Borough"
    ],
    "overall": "C+",
    "safety": null,
    "url": "https://www.niche.com/places-to-live/wattsburg-erie-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Allegheny",
    "name": "Sheraden",
    "aliases": [
      "Sheraden"
    ],
    "overall": "A-",
    "safety": null,
    "url": "https://www.niche.com/places-to-live/n/sheraden-pittsburgh-pa/",
    "level": "neighborhood",
    "parent": "Pittsburgh",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Allegheny",
    "name": "Beechview",
    "aliases": [
      "Beechview"
    ],
    "overall": "A",
    "safety": null,
    "url": "https://www.niche.com/places-to-live/n/beechview-pittsburgh-pa/",
    "level": "neighborhood",
    "parent": "Pittsburgh",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Allegheny",
    "name": "Crafton",
    "aliases": [
      "Crafton",
      "Crafton Borough"
    ],
    "overall": "A-",
    "safety": "B",
    "url": "https://www.niche.com/places-to-live/crafton-allegheny-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  },
  {
    "county": "Allegheny",
    "name": "Plum",
    "aliases": [
      "Plum",
      "Plum Borough",
      "Plum Boro"
    ],
    "overall": "A",
    "safety": "B",
    "url": "https://www.niche.com/places-to-live/plum-allegheny-pa/",
    "level": "municipality",
    "checked_at": "2026-11-01",
    "edition": 2026
  }
];
  const norm=x=>String(x||'').trim().toLowerCase().replace(/\s+/g,' ');
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const municipality=x=>norm(x).replace(/^\d+(?:st|nd|rd|th) ward\s*-\s*pittsburgh$/,'pittsburgh');
  function resolve(county,point,now=Date.now()){
    if(county?.status!=='matched'||!county.parcel_id||!Number.isFinite(now))return null;
    const c=norm(county.county).replace(/ county$/,''),m=municipality(county.fields?.municipality);
    if(!m)return null;
    const fresh=r=>{const t=Date.parse(r.checked_at+'T00:00:00Z');return t<=now&&now-t<=400*86400000;};
    const candidates=RECORDS.filter(r=>norm(r.county)===c&&fresh(r));
    const parent=candidates.find(r=>r.level==='municipality'&&r.aliases.some(a=>municipality(a)===m));
    if(!parent)return null;
    // A neighborhood is trusted only from this parcel's official county centroid.
    const official=point?.parcel_id===county.parcel_id&&point?.source_url==='https://data.wprdc.org/dataset/parcel-centroids-in-allegheny-county-with-geographic-identifiers'&&municipality(point.municipality)===municipality(parent.name);
    const local=official?candidates.find(r=>r.level==='neighborhood'&&r.parent===parent.name&&r.aliases.some(a=>norm(a)===norm(point.neighborhood))):null;
    return local||parent;
  }
  function render(county,point,now=Date.now()){
    const r=resolve(county,point,now);
    const letter=x=>/^[A-D][+-]?$/.test(x||'')?x.replace('-','−'):'—';
    return '<div class="area-grades"><div><span>דירוג אזור</span><strong dir="ltr">'+letter(r?.overall)+'</strong></div><div><span>בטיחות</span><strong dir="ltr">'+letter(r?.safety)+'</strong></div></div>'+
      (r?'<p class="source-note"><bdi>'+esc(r.name)+'</bdi> · '+(r.level==='neighborhood'?'שכונה':'רשות מקומית')+' · <a href="'+esc(r.url)+'" target="_blank" rel="noopener noreferrer">Niche '+r.edition+'</a> · נבדק '+r.checked_at+'</p>':'');
  }
  return {resolve,render,records:()=>JSON.parse(JSON.stringify(RECORDS))};
});

