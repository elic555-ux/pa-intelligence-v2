(function(root,factory){
  'use strict';
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.PARehabEstimate=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const MAX=100000000;
  const UNITS={item:'יחידה',sqft:'רגל רבועה',lf:'רגל אורך',room:'חדר',system:'מערכת',project:'עבודה כוללת'};
  const BASES={manual:'הנחת משתמש',quote:'הצעת ספק / קבלן',pricebook:'מחירון מזוהה'};
  const CONFIDENCE={unknown:'לא נקבעה',low:'נמוכה',medium:'בינונית',high:'גבוהה'};
  const STRATEGIES={unspecified:'טרם נבחר ייעוד',rental:'הכנה להשכרה',flip:'שיפוץ למכירה'};
  const own=(v,k)=>Object.prototype.hasOwnProperty.call(v,k);
  const obj=v=>v&&typeof v==='object'&&!Array.isArray(v)?v:{};
  const round=v=>Math.round((v+Number.EPSILON)*100)/100;
  function amount(v,max=MAX){
    if(v==null||(typeof v==='string'&&v.trim()===''))return {value:null,valid:true};
    if(!['number','string'].includes(typeof v)||(typeof v==='string'&&!/^\d+(?:\.\d{1,2})?$/.test(v.trim())))return {value:null,valid:false};
    const n=Number(v),valid=Number.isFinite(n)&&n>=0&&n<=max&&Math.abs(n*100-Math.round(n*100))<0.000001;
    return {value:valid?round(n):null,valid};
  }
  function date(v){return typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&Number.isFinite(Date.parse(v+'T00:00:00Z'))&&new Date(v+'T00:00:00Z').toISOString().slice(0,10)===v?v:'';}
  function normalizeEntry(raw){
    const e=obj(raw),errors=[];
    const text=(key,max)=>{const v=typeof e[key]==='string'?e[key].trim():'';if(v.length>max)errors.push('השדה ארוך מדי: '+key);return v.slice(0,max);};
    const entry={pricing_mode:e.pricing_mode==='quantity'?'quantity':'total',cost:null,
      quantity:null,unit:own(UNITS,e.unit)?e.unit:'item',unit_low:null,unit_high:null,
      price_basis:own(BASES,e.price_basis)?e.price_basis:'manual',price_source:text('price_source',200),
      price_date:date(e.price_date),price_reference:text('price_reference',500),
      finding:text('finding',1000),photo_ref:text('photo_ref',2000),
      confidence:own(CONFIDENCE,e.confidence)?e.confidence:'unknown'};
    if(e.price_date&&!entry.price_date)errors.push('תאריך מקור המחיר אינו תקין.');
    if(entry.price_basis!=='manual'&&(!entry.price_source||!entry.price_date||!entry.price_reference))errors.push('להצעה או מחירון יש לציין מקור, תאריך ואסמכתה / גרסה.');
    for(const key of ['quantity','unit_low','unit_high']){
      const n=amount(e[key],key==='quantity'?1000000:MAX);entry[key]=n.value;
      if(entry.pricing_mode==='quantity'&&!n.valid)errors.push('יש להזין כמות ומחירי יחידה תקינים, עד שתי ספרות אחרי הנקודה.');
    }
    const total=amount(e.cost);
    if(entry.pricing_mode==='total'){
      entry.cost=total.value;if(!total.valid)errors.push('יש להזין סכום חיובי או אפס, עד שתי ספרות אחרי הנקודה.');
    }else{
      if(entry.quantity===0)errors.push('הכמות חייבת להיות גדולה מאפס.');
      if(entry.unit_low!==null&&entry.unit_high!==null&&entry.unit_high<entry.unit_low)errors.push('המחיר הגבוה אינו יכול להיות נמוך מהמחיר הנמוך.');
      const high=entry.quantity!==null&&entry.unit_high!==null?round(entry.quantity*entry.unit_high):null;
      if(high!==null&&high>MAX)errors.push('עלות הסעיף חורגת מהסכום המרבי.');
      if(!errors.length&&entry.quantity>0&&entry.unit_low!==null&&entry.unit_high!==null)entry.cost=high;
    }
    return {entry,errors};
  }
  function estimate(raw){
    const {entry,errors}=normalizeEntry(raw);
    return {cost:entry.cost,low:entry.cost===null?null:entry.pricing_mode==='quantity'?round(entry.quantity*entry.unit_low):entry.cost,
      high:entry.cost,mode:entry.pricing_mode,errors};
  }
  // A full canonical snapshot is used as the revision key; it is not an authentication claim.
  function revision(plan){
    const p=obj(plan);
    return JSON.stringify({version:p.version,property_id:p.property_id,strategy:p.strategy,updated_at:p.updated_at,contingency_pct:p.contingency_pct,items:p.items});
  }
  return {MAX,UNITS,BASES,CONFIDENCE,STRATEGIES,amount,date,normalizeEntry,estimate,revision};
});
