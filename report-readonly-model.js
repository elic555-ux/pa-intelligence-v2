(function(root,factory){'use strict';const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.PAReadonlyReport=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION='9.0.2';
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
  return {VERSION,SECTIONS,esc,object,number,money,unitMoney,percent,url,identity,matches,fresh,pendingRequest,recordedSales,rentSource,rehabSource,metrics,financing};
});
