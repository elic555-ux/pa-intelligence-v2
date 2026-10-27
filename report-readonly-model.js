(function(root,factory){'use strict';const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.PAReadonlyReport=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION='9.0.11';
  const SECTIONS=Object.freeze([
    ['property',1,'פרטי הנכס והמחיר המבוקש','overview'],['photos',2,'תמונות הנכס','overview'],
    ['county',3,'רישום המחוז ופרטי החלקה','overview'],['area',4,'שכונה ואזור','overview'],
    ['analysis',5,'תחשיבי העסקה','financials'],['comparisons',6,'השוואות מכירה ושכירות','financials'],
    ['history',7,'היסטוריית מחירי הנכס','financials'],['financing',8,'מקורות ועלויות מימון','financials'],
    ['rehab',9,'ניתוח הנכס ואומדן השיפוץ','rehab']
  ].map(([id,number,title,tab])=>Object.freeze({id,number,title,tab})));
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const object=x=>x&&typeof x==='object'&&!Array.isArray(x)?x:{};
  const number=x=>!['number','string'].includes(typeof x)||(typeof x==='string'&&!/^-?\d+(?:\.\d+)?$/.test(x.trim()))?null:Number.isFinite(Number(x))?Number(x):null;
  const money=x=>number(x)===null?'חסר נתון':Number(x).toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0});
  const unitMoney=x=>number(x)===null?'חסר נתון':Number(x).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2});
  const percent=x=>number(x)===null?'חסר נתון':Number(x).toFixed(1)+'%';
  function url(x){try{const u=new URL(x);return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;}catch(_){return null;}}
  function identity(p){return [String(p.id||''),String(p.address||'').trim().toLowerCase(),String(p.city||'').trim().toLowerCase(),String(p.state||p.source_state||'PA').toUpperCase(),String(p.zip||'').slice(0,5)];}
  function matches(a,p){return !!a&&JSON.stringify(identity({...a.property,id:a.property_id||a.property?.id}))===JSON.stringify(identity(p));}
  function fresh(value,days,now=Date.now()){const t=Date.parse(value);return Number.isFinite(t)&&t<=now&&now-t<=days*86400000;}
  function pendingRequest(raw,p,now=Date.now()){
    try{const x=typeof raw==='string'?JSON.parse(raw):raw;
      return x&&/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(x.request_id)&&
        x.identity===JSON.stringify(identity(p))&&typeof x.created_at==='number'&&Number.isFinite(x.created_at)&&
        x.created_at<=now&&now-x.created_at<30*60000?x:null;
    }catch(_){return null;}
  }
  function recordedSales(a,p,now=Date.now()){
    if(!matches(a,p)||!fresh(a.generated_at,7,now))return [];
    const source=a.closed_sales;if(source?.property_id!==String(p.id)||source.source_name!=='Allegheny County / WPRDC'||!fresh(source.observed_at,7,now))return [];
    return (Array.isArray(source.comparables)?source.comparables:[]).filter(r=>r.kind==='recorded_sale'&&number(r.amount)>0&&fresh(r.sale_date,181,now)&&fresh(r.recorded_date,366,now)&&number(r.distance_miles)!==null&&r.distance_miles>=0&&r.distance_miles<=source.policy?.radius_miles&&url(r.source_url)&&['county_validated','pending_county_review'].includes(r.validation));
  }
  function rentSource(report,p,now=Date.now()){
    if(!matches(report,p))return null;
    const s=report.sources?.rent;
    if(s?.status!=='accepted'||s.estimate?.status!=='provider_estimate'||number(s.estimate.value)<=0||!fresh(s.observed_at,30,now))return null;
    return {value:Number(s.estimate.value),low:number(s.estimate.range_low),high:number(s.estimate.range_high),source:s.source_name,date:s.observed_at,comparables:(s.comparables||[]).filter(r=>r.kind==='rental_listing'&&number(r.amount)>0&&fresh(r.date,91,now)),url:url(s.source_url)};
  }
  function rehabSource(a,p,now=Date.now()){
    const r=matches(a,p)?a.rehab:null;
    if(!r||r.property_id!==String(p.id)||!fresh(r.observed_at,30,now))return null;
    if(!['available','partial','provider_not_configured','photos_unavailable','source_unavailable','quota_exhausted'].includes(r.status))return null;
    return r;
  }
  function propertyTaxes(a,p,county,now=Date.now()){
    const t=matches(a,p)?a.property_taxes:null;
    if(!t||!matches(t,p)||county?.status!=='matched'||county.county!=='Allegheny'||
       !matches({property:county.subject,property_id:String(p.id)},p)||county.parcel_id!==t.parcel_id||
       t.version!=='allegheny-county-bill-1'||t.status!=='partial'||
       t.basis!=='current_owner_annual_county_bill'||t.currency!=='USD'||
       t.forecast_for_new_owner!==false||t.complete!==false||t.total_property_tax!==null||
       !fresh(t.observed_at,30,now)||t.tax_year!==new Date(now).getUTCFullYear()||number(t.county_face_amount)<=0)return null;
    try{const u=new URL(t.source_url);if(u.origin!=='https://realestate.alleghenycounty.us'||u.pathname!=='/Tax'||u.searchParams.get('ID')!==t.parcel_id||u.username||u.password)return null;}catch(_){return null;}
    return t;
  }
  function photoCoverage(r){
    const scenes=['kitchen','bathroom','bedroom','living_dining','hall_stairs','basement_utility','attic','garage','exterior','other','unclear'];
    const photos=Array.isArray(r?.photos)?r.photos:[],rows=Array.isArray(r?.photo_coverage)?r.photo_coverage:[];
    const ids=new Set(photos.map(x=>x.id));
    if(!ids.size||ids.size!==number(r?.photo_count)||rows.length!==ids.size||new Set(rows.map(x=>x?.photo_id)).size!==ids.size||
      rows.some(x=>!ids.has(x?.photo_id)||!scenes.includes(x?.scene)))return null;
    const counts=Object.fromEntries(scenes.map(s=>[s,rows.filter(x=>x.scene===s).length]));
    return {interior:scenes.slice(0,8).reduce((sum,key)=>sum+counts[key],0),exterior:counts.exterior,
      unclear:counts.other+counts.unclear,scenes:scenes.slice(0,8).filter(s=>counts[s]>0),
      missing:['kitchen','bathroom','bedroom','living_dining'].filter(s=>counts[s]===0)};
  }
  function photoReview(r){
    const coverage=photoCoverage(r),photos=Array.isArray(r?.photos)?r.photos:[];
    const rows=Array.isArray(r?.photo_coverage)?r.photo_coverage:[];
    const conditions=['visible_damage','cosmetic_optional','no_obvious_damage_in_view','not_assessable'];
    const valid=coverage?rows.filter(x=>conditions.includes(x.condition)&&typeof x.finding==='string'&&x.finding.trim()):[];
    const intake=r?.photo_intake;
    const keys=['available_links','candidate_links','prepared_photos','unavailable','duplicate_bytes','excluded_by_byte_limit','not_attempted'];
    const accounted=!!intake&&keys.every(k=>Number.isInteger(intake[k])&&intake[k]>=0)&&
      intake.prepared_photos===photos.length&&intake.candidate_links<=intake.available_links&&
      intake.prepared_photos+intake.unavailable+intake.duplicate_bytes+intake.excluded_by_byte_limit+intake.not_attempted===intake.candidate_links;
    const available=accounted?intake.available_links:null;
    const omitted=accounted?intake.unavailable+intake.excluded_by_byte_limit+intake.not_attempted+intake.available_links-intake.candidate_links:null;
    return {available,sent:photos.length,reviewed:valid.length,unassessable:valid.filter(x=>x.condition==='not_assessable'||['other','unclear'].includes(x.scene)).length,
      omitted,duplicates:accounted?intake.duplicate_bytes:null,intake:accounted?intake:null,
      complete:!!coverage&&accounted&&omitted===0&&valid.length===photos.length&&valid.every(x=>x.condition!=='not_assessable'&&!['other','unclear'].includes(x.scene)),
      rows:valid.map(x=>({...x,photo:photos.find(p=>p.id===x.photo_id)}))};
  }
  function interiorAssessments(r){
    const coverage=photoCoverage(r),rows=Array.isArray(r?.interior_assessments)?r.interior_assessments:[];
    if(!coverage)return [];
    const components=scene=>['flooring','walls_ceilings','doors_windows',...(scene==='kitchen'?['cabinets_counters','fixtures']:scene==='bathroom'?['fixtures']:[])];
    const expected=new Set(coverage.scenes.flatMap(scene=>components(scene).map(component=>scene+':'+component)));
    const seen=new Set();
    if(rows.length!==expected.size)return [];
    for(const row of rows){
      const key=row?.scene+':'+row?.component;
      if(!expected.has(key)||seen.has(key)||!['visible_damage','cosmetic_optional','visible_wear','no_obvious_damage_in_view','not_assessable'].includes(row?.condition)||
        !['finding','scope','rationale'].every(k=>typeof row[k]==='string'&&row[k].trim())||
        !Array.isArray(row.photo_ids)||!row.photo_ids.length||row.photo_ids.some(id=>!r.photo_coverage.some(c=>c.photo_id===id&&c.scene===row.scene)))return [];
      seen.add(key);
    }
    return rows;
  }
  function exteriorAssessments(r){
    if(!photoCoverage(r)?.exterior)return [];
    const codes=['roof_surface','exterior_walls','doors_windows','gutters_downspouts','steps_railings','yard_paving'];
    const rows=Array.isArray(r?.exterior_assessments)?r.exterior_assessments:[],seen=new Set();
    if(rows.length!==codes.length)return [];
    for(const row of rows){
      if(!codes.includes(row?.component)||seen.has(row.component)||
        !['visible_damage','cosmetic_optional','no_obvious_damage_in_view','not_assessable'].includes(row.condition)||
        !['finding','scope','rationale'].every(k=>typeof row[k]==='string'&&row[k].trim())||
        !Array.isArray(row.photo_ids)||!row.photo_ids.length||
        row.photo_ids.some(id=>!r.photo_coverage.some(c=>c.photo_id===id&&c.scene==='exterior')))return [];
      seen.add(row.component);
    }
    return rows;
  }
  function metrics(p,a,report,financials,now=Date.now()){
    const f=financials?.financialSnapshot(p,now)||{},rent=rentSource(report,p,now),rehab=rehabSource(a,p,now);
    const price=number(f.price),rentValue=rent?.value??number(f.rent);
    const budget=rehab?.status==='available'&&rehab.complete===true&&number(rehab.total_high)>=0?Number(rehab.total_high):null;
    return {price,rent:rentValue,annualRent:rentValue===null?null:rentValue*12,
      grossYield:rentValue!==null&&price>0?rentValue*1200/price:null,
      rehab:budget,rehabLow:budget!==null?number(rehab.total_low):null,
      purchaseAndRehab:price!==null&&budget!==null?price+budget:null,
      arv:number(f.arv),noi:number(f.noi),flipProfit:number(f.flipProfit),capRate:number(f.capRate),
      maoFlip:number(f.maoFlip),maoRental:number(f.maoRental),rentalRehab:number(f.rentalRehab),flipRehab:number(f.flipRehab)};
  }
  function financing(a,p,now=Date.now()){
    if(!matches(a,p)||!fresh(a.financing?.observed_at,7,now))return [];
    return (a.financing.products||[]).filter(r=>r.status==='published_terms'&&r.is_personal_quote===false&&url(r.source_url)&&fresh(r.observed_at,7,now));
  }
  return {VERSION,SECTIONS,esc,object,number,money,unitMoney,percent,url,identity,matches,fresh,pendingRequest,recordedSales,rentSource,rehabSource,metrics,financing,propertyTaxes,photoCoverage,photoReview,interiorAssessments,exteriorAssessments};
});
