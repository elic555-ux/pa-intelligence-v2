(function(root,factory){'use strict';const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.PAReadonlyReport=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION='10.2.0';
  const SECTIONS=Object.freeze([
    ['property',1,'פרטי הנכס והמחיר המבוקש','overview'],['photos',2,'תמונות הנכס','overview'],
    ['county',3,'רישום המחוז ופרטי החלקה','overview'],['area',4,'שכונה ואזור','overview'],
    ['analysis',5,'תחשיבי העסקה','financials'],['comparisons',6,'השוואות מכירה ושכירות','financials'],
    ['history',7,'היסטוריית מחירי הנכס','financials'],['financing',8,'מקורות ועלויות מימון','financials'],
    ['summary',9,'סיכום ומידע להשלמה','overview']
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
  function metrics(p,a,report,financials,now=Date.now()){
    const f=financials?.financialSnapshot(p,now)||{},rent=rentSource(report,p,now);
    const price=number(f.price),rentValue=rent?.value??number(f.rent);
    const annualRent=rentValue===null?null:rentValue*12,expenses=number(f.operatingExpenses),vacancy=number(f.vacancyLoss);
    const noi=[annualRent,expenses,vacancy].every(x=>x!==null)?annualRent-expenses-vacancy:null;
    return {price,rent:rentValue,annualRent,
      grossYield:rentValue!==null&&price>0?rentValue*1200/price:null,
      noi,capRate:noi!==null&&price>0?noi*100/price:null,
      arv:null,rehab:null,rehabLow:null,purchaseAndRehab:null,flipProfit:null,maoFlip:null,maoRental:null};
  }
  function propertyConflicts(p,county){
    if(county?.status!=='matched'||!matches({property:county.subject,property_id:String(p.id)},p))return [];
    return [['beds','מספר חדרים'],['baths','חדרי רחצה'],['sqft','שטח SqFt'],['year_built','שנת בנייה']].flatMap(([key,label])=>{
      const listing=number(p[key]),recorded=number(county.fields?.[key]);
      return listing!==null&&recorded!==null&&listing!==recorded?[label+': במודעה '+listing+', במחוז '+recorded+' — דורש אימות']:[];
    });
  }
  function priceHistory(p,now=Date.now()){
    return (Array.isArray(p.price_history)?p.price_history:[])
      .filter(r=>number(r.price)>0&&Number.isFinite(Date.parse(r.date))&&Date.parse(r.date)<=now)
      .sort((a,b)=>Date.parse(b.date)-Date.parse(a.date));
  }
  function currentValue(a,report,p,now=Date.now()){
    const s=matches(report,p)?report.sources?.value:null;
    if(s?.status==='accepted'&&s.estimate?.basis==='current_market_value'&&number(s.estimate.value)>0&&fresh(s.observed_at,60,now))
      return {value:Number(s.estimate.value),low:number(s.estimate.range_low),high:number(s.estimate.range_high),source:s.source_name,date:s.observed_at,url:url(s.source_url)};
    const sales=matches(a,p)?a.closed_sales:null,estimate=sales?.market_estimate;
    if(estimate?.is_arv===false&&estimate.count>=3&&number(estimate.value)>0&&recordedSales(a,p,now).filter(x=>x.validation==='county_validated').length>=3)
      return {value:Number(estimate.value),low:null,high:null,source:sales.source_name,date:sales.observed_at,url:url(sales.source_url)};
    return null;
  }
  function officialArea(a,p,now=Date.now()){
    const area=matches(a,p)?a.area:null;
    if(area?.version!=='official-area-1'||!/^42003\d{6}$/.test(area.geoid||'')||
      !url(area.source_url)||new URL(area.source_url).hostname!=='data.census.gov'||
      !fresh(area.observed_at,550,now)||!Array.isArray(area.metrics)||
      !area.metrics.some(r=>number(r?.value)!==null&&number(r.value)>=0))return null;
    const point=a.closed_sales?.subject_location||a.public_context?.parcel_location;
    const geoid=String(point?.geoid||'').slice(0,11)||
      (point?.parcel_id&&/^\d{6}$/.test(String(point.tract))?'42003'+point.tract:'');
    if(geoid!==area.geoid)return null;
    const t=new Date(now),quarter=t.getUTCFullYear()+'-Q'+(Math.floor(t.getUTCMonth()/3)+1);
    return {...area,status:area.checked_quarter===quarter?area.status:'cached_stale'};
  }
  function financing(a,p,now=Date.now()){
    if(!matches(a,p)||!fresh(a.financing?.observed_at,7,now))return [];
    return (a.financing.products||[]).filter(r=>r.status==='published_terms'&&r.is_personal_quote===false&&url(r.source_url)&&fresh(r.observed_at,7,now));
  }
  return {VERSION,SECTIONS,propertyConflicts,currentValue,priceHistory,officialArea,esc,object,number,money,unitMoney,percent,url,identity,matches,fresh,pendingRequest,recordedSales,rentSource,rehabSource,metrics,financing,propertyTaxes,photoCoverage,photoReview,interiorAssessments};
});
