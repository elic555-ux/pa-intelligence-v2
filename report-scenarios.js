(function(root,factory){
  'use strict';
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.PAReportScenarios=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION=2,MAX=100000000;
  const FIELDS={
    purchase:['מחיר רכישה · $',MAX],rent:['שכירות חודשית · $',1000000],
    arv:['מחיר מכירה בתרחיש · $',MAX],flipRehab:['תקציב שיפוץ לפליפ · $',MAX],
    rentalRehab:['תקציב שיפוץ להשכרה · $',MAX],acquisition:['עלויות רכישה וסגירה · $',MAX],
    operatingAnnual:['תפעול ואי־תפוסה · $ לשנה',MAX],selling:['עלויות מכירה · $',MAX],
    holding:['עלויות החזקה לפליפ · $',MAX],financing:['עלויות מימון לפליפ · $',MAX],
    targetProfit:['יעד רווח לפליפ · $',MAX],targetYield:['יעד תשואה על עלות ההשכרה · %',100],
    ltv:['מימון מתוך מחיר הרכישה · %',100],apr:['ריבית שנתית להלוואה · %',100],
    years:['תקופת ההלוואה · שנים',50],loanFees:['עלויות הקמת הלוואה להשכרה · $',MAX],
    initialCash:['כל ההון העצמי שהושקע עד המחזור · $',MAX],
    debtPayoff:['סכום פירעון החוב בעת המחזור · $',MAX],
    refinanceValue:['שווי הנכס לצורך המחזור · $',MAX],
    refinanceLtv:['מימון במחזור מתוך השווי · %',100],
    refinanceApr:['ריבית שנתית במחזור · %',100],refinanceYears:['תקופת הלוואת המחזור · שנים',50],
    refinanceCosts:['כל עלויות המחזור · $',MAX],
    stressRentPct:['שינוי שכירות לכל כיוון · %',100],
    stressSalePct:['שינוי מחיר מכירה לכל כיוון · %',100],
    stressRehabPct:['שינוי תקציב שיפוץ לכל כיוון · %',100],
    stressOperatingPct:['שינוי הוצאות תפעול לכל כיוון · %',100],
    stressRatePoints:['שינוי ריבית לכל כיוון · נקודות אחוז',100]
  };
  const GROUPS=[
    ['נתוני העסקה',['purchase','rent','arv','flipRehab','rentalRehab','acquisition','operatingAnnual','selling','holding','financing','targetProfit','targetYield']],
    ['מימון הרכישה',['ltv','apr','years','loanFees']],
    ['BRRRR — הנחות המחזור',['initialCash','debtPayoff','refinanceValue','refinanceLtv','refinanceApr','refinanceYears','refinanceCosts']],
    ['רגישות — שינויים לבדיקה לפי בחירתך',['stressRentPct','stressSalePct','stressRehabPct','stressOperatingPct','stressRatePoints']]
  ];
  const SOURCE_KEYS={purchase:'price',rent:'rent',acquisition:'acquisitionCosts',selling:'sellingCosts',holding:'holdingCosts',financing:'financingCosts'};
  const POSITIVE=new Set(['purchase','rent','arv','years','targetYield','refinanceValue','refinanceYears']);
  const obj=x=>x&&typeof x==='object'&&!Array.isArray(x)?x:{};
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const norm=x=>String(x??'').trim().toUpperCase().replace(/\s+/g,' ');
  const identity=p=>[String(p.id??''),norm(p.address),norm(p.city),norm(p.state||'PA'),String(p.zip??'').slice(0,5)];
  const matches=(s,p)=>[VERSION,1].includes(s?.version)&&String(s?.property_id)===String(p.id)&&JSON.stringify(s?.identity)===JSON.stringify(identity(p));
  const number=(x,max=MAX)=>{
    if(x===null||x===undefined||(typeof x==='string'&&!x.trim()))return {value:null,valid:true};
    if(!['number','string'].includes(typeof x)||(typeof x==='string'&&!/^\d+(?:\.\d{1,2})?$/.test(x.trim())))return {value:null,valid:false};
    const n=Number(x);return {value:Number.isFinite(n)&&n>=0&&n<=max?n:null,valid:Number.isFinite(n)&&n>=0&&n<=max};
  };
  const all=xs=>xs.every(x=>typeof x==='number'&&Number.isFinite(x));
  const sum=(...xs)=>all(xs)?xs.reduce((a,b)=>a+b,0):null;
  const ratio=(a,b)=>all([a,b])&&b>0?a/b*100:null;
  function normalize(s,p){
    const valid=matches(s,p),raw=valid?obj(s.inputs):{},inputs={},errors={};
    for(const [key,[label,max]]of Object.entries(FIELDS)){
      // Old plan-linked budgets are never silently relabeled as manual inputs.
      const linked=['flipRehab','rentalRehab'].includes(key)&&Object.hasOwn(obj(s?.rehab_links),key);
      const v=number(linked?null:raw[key],max);
      inputs[key]=v.value;
      if(!v.valid||(POSITIVE.has(key)&&v.value===0))errors[key]=label+': נדרש מספר תקין'+(POSITIVE.has(key)?' גדול מאפס':' שאינו שלילי');
    }
    return {version:VERSION,property_id:String(p.id??''),identity:identity(p),inputs,errors,
      updated_at:valid&&typeof s.updated_at==='string'&&Number.isFinite(Date.parse(s.updated_at))?s.updated_at:null};
  }
  function serialize(s,p){
    const n=normalize(s,p);if(Object.keys(n.errors).length)throw Error('יש לתקן את הנתונים המסומנים לפני שמירה.');
    delete n.errors;return n;
  }
  function payment(principal,apr,years){
    if(principal===0)return 0;
    if(!all([principal,apr,years])||principal<0||apr<0||years<=0)return null;
    const months=years*12,rate=apr/1200;
    return rate===0?principal/months:principal*rate/(1-Math.pow(1+rate,-months));
  }
  function values(p,s,source){
    const n=normalize(s,p),inputs={},origins={};
    for(const [key,[,max]]of Object.entries(FIELDS)){
      if(n.errors[key]){inputs[key]=null;origins[key]='invalid';continue;}
      if(n.inputs[key]!==null){inputs[key]=n.inputs[key];origins[key]='manual';continue;}
      let v=SOURCE_KEYS[key]?source[SOURCE_KEYS[key]]:null;
      if(key==='operatingAnnual')v=sum(number(source.operatingExpenses).value,number(source.vacancyLoss).value);
      const parsed=number(v,max);
      inputs[key]=parsed.valid&&!(POSITIVE.has(key)&&parsed.value===0)?parsed.value:null;
      origins[key]=inputs[key]===null?'missing':'source';
    }
    return {inputs,origins,errors:n.errors,updated_at:n.updated_at};
  }
  function compute(v){
    const annualRent=v.rent===null?null:v.rent*12;
    const noi=all([annualRent,v.operatingAnnual])?annualRent-v.operatingAnnual:null;
    const loan=all([v.purchase,v.ltv])?v.purchase*v.ltv/100:null;
    const monthlyDebt=payment(loan,v.apr,v.years);
    const rentalAllIn=sum(v.purchase,v.rentalRehab,v.acquisition);
    const cashRequired=all([rentalAllIn,loan,v.loanFees])?rentalAllIn-loan+v.loanFees:null;
    const monthlyCashFlow=all([noi,monthlyDebt])?noi/12-monthlyDebt:null;
    const flipAllIn=sum(v.purchase,v.flipRehab,v.acquisition,v.holding,v.financing);
    const flipProfit=all([v.arv,flipAllIn,v.selling])?v.arv-flipAllIn-v.selling:null;
    const flipBreakEven=sum(flipAllIn,v.selling);
    const flipMao=all([v.arv,v.flipRehab,v.acquisition,v.holding,v.financing,v.selling,v.targetProfit])?v.arv-v.flipRehab-v.acquisition-v.holding-v.financing-v.selling-v.targetProfit:null;
    const rentalMao=all([noi,v.targetYield,v.rentalRehab,v.acquisition])&&v.targetYield>0?noi/(v.targetYield/100)-v.rentalRehab-v.acquisition:null;
    const refinanceLoan=all([v.refinanceValue,v.refinanceLtv])?v.refinanceValue*v.refinanceLtv/100:null;
    const refinancePayment=payment(refinanceLoan,v.refinanceApr,v.refinanceYears);
    const netCashReturned=all([refinanceLoan,v.debtPayoff,v.refinanceCosts])?refinanceLoan-v.debtPayoff-v.refinanceCosts:null;
    const capitalRemaining=all([v.initialCash,netCashReturned])?v.initialCash-netCashReturned:null;
    const refinanceCashFlow=all([noi,refinancePayment])?noi/12-refinancePayment:null;
    const equity=all([v.refinanceValue,refinanceLoan])?v.refinanceValue-refinanceLoan:null;
    return {annualRent,noi,loan,monthlyDebt,rentalAllIn,cashRequired,monthlyCashFlow,
      cashOnCash:ratio(monthlyCashFlow===null?null:monthlyCashFlow*12,cashRequired),
      capRate:ratio(noi,v.purchase),flipAllIn,flipProfit,flipBreakEven,flipMao,rentalMao,
      refinanceLoan,refinancePayment,netCashReturned,capitalRemaining,refinanceCashFlow,equity,
      refinanceCashOnCash:ratio(refinanceCashFlow===null?null:refinanceCashFlow*12,capitalRemaining)};
  }
  function calculate(p,s,source={}){
    const resolved=values(p,s,obj(source)),v=resolved.inputs,base=compute(v);
    const stressKeys=['stressRentPct','stressSalePct','stressRehabPct','stressOperatingPct','stressRatePoints'];
    const sensitivityReady=stressKeys.every(k=>v[k]!==null);
    const shifted=direction=>{
      const x={...v};
      const change=(key,pct,sign)=>{if(x[key]!==null)x[key]*=1+sign*pct/100;};
      change('rent',v.stressRentPct,direction);change('arv',v.stressSalePct,direction);
      for(const key of ['flipRehab','rentalRehab'])change(key,v.stressRehabPct,-direction);
      change('operatingAnnual',v.stressOperatingPct,-direction);
      if(x.apr!==null)x.apr=Math.max(0,x.apr-direction*v.stressRatePoints);
      return {inputs:x,...compute(x)};
    };
    return {...resolved,...base,sensitivityReady,sensitivity:sensitivityReady?
      {conservative:shifted(-1),base:{inputs:{...v},...base},optimistic:shifted(1)}:null};
  }
  const money=x=>x===null?'נדרש נתון':x.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0});
  const percent=x=>x===null?'לא ניתן לחשב':x.toFixed(1)+'%';
  const metric=(label,value,format=money)=>'<div class="metric"><span>'+esc(label)+'</span><strong'+(value===null?' class="missing"':'')+'>'+esc(format(value))+'</strong></div>';
  function render(result){
    const r=result;
    const cards=rows=>{const available=rows.filter(([,value])=>value!==null);return available.length?'<div class="metric-grid">'+available.map(([label,value,format])=>metric(label,value,format)).join('')+'</div>':'';};
    const dealCards=cards([['תזרים חודשי',r.monthlyCashFlow],['Cash-on-Cash',r.cashOnCash,percent],['מחיר מכירה לאיזון בפליפ',r.flipBreakEven],['רווח פליפ לפני מס',r.flipProfit],['תקרת רכישה להשכרה',r.rentalMao],['תקרת רכישה לפליפ',r.flipMao]]);
    let html=dealCards||'<p class="missing">טרם הוזנו מספיק נתונים לתחשיב עסקה מלא.</p>';
    const refinanceCards=cards([['הלוואה חדשה',r.refinanceLoan],['החזר חודשי לאחר המחזור',r.refinancePayment],['מזומן נטו המתקבל במחזור',r.netCashReturned],['הון עצמי שנותר מושקע',r.capitalRemaining],['תזרים חודשי לאחר המחזור',r.refinanceCashFlow],['תשואה על ההון שנותר',r.refinanceCashOnCash,percent]]);
    html+='<details'+(refinanceCards?' open':'')+'><summary>BRRRR — מחזור לפי הנחות ידניות</summary>'+(refinanceCards||'<p class="missing">להערכת מחזור יש להזין את הנחות השווי, המימון, פירעון החוב וההון שהושקע.</p>');
    html+='<p class="source-note">המזומן נטו הוא הלוואה חדשה פחות פירעון החוב וכל עלויות המחזור. ההון שנותר הוא כל ההון שהושקע פחות המזומן נטו. יתרה שלילית משמעה שהוחזר יותר מההון שהוזן; אינה רווח מכירה. אין תשואה באחוזים כשההון שנותר אפס או שלילי.</p></details>';
    html+='<details'+(r.sensitivityReady?' open':'')+'><summary>השוואת רגישות</summary>';
    if(r.sensitivityReady){
      html+='<div class="table-scroll"><table><thead><tr><th>מדד</th><th>שמרני</th><th>בסיסי</th><th>אופטימי</th></tr></thead><tbody>'+
        [['שכירות חודשית',x=>x.inputs.rent],['מחיר מכירה',x=>x.inputs.arv],['שיפוץ לפליפ',x=>x.inputs.flipRehab],['תפעול שנתי',x=>x.inputs.operatingAnnual],['תזרים חודשי',x=>x.monthlyCashFlow],['רווח פליפ לפני מס',x=>x.flipProfit]].map(([label,get])=>'<tr><td>'+esc(label)+'</td>'+['conservative','base','optimistic'].map(k=>'<td>'+esc(money(get(r.sensitivity[k])))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div><p class="source-note">שינויים שנבחרו: שכירות ±'+esc(r.inputs.stressRentPct)+'%, מכירה ±'+esc(r.inputs.stressSalePct)+'%, שיפוץ ±'+esc(r.inputs.stressRehabPct)+'%, תפעול ±'+esc(r.inputs.stressOperatingPct)+'%, ריבית ±'+esc(r.inputs.stressRatePoints)+' נקודות אחוז. ריבית בתרחיש האופטימי אינה יורדת מתחת לאפס. אלה בדיקות רגישות, ללא הסתברות או תחזית.</p>';
    }else html+='<p class="missing">להשוואה יש להזין את חמשת השינויים לבדיקה. אפשר להזין 0 כדי להשאיר מדד קבוע.</p>';
    html+='</details>';
    const used=Object.entries(FIELDS).filter(([k])=>r.inputs[k]!==null||r.origins[k]==='invalid');
    html+='<details><summary>הנחות ומקורות החישוב</summary>'+(used.length?'<div class="table-scroll"><table><thead><tr><th>נתון</th><th>ערך</th><th>בסיס</th></tr></thead><tbody>'+used.map(([k,[label]])=>'<tr><td>'+esc(label)+'</td><td>'+esc(r.inputs[k]??'נדרש נתון')+'</td><td>'+esc(({manual:'הנחה ידנית',source:'מקור מתועד',missing:'חסר',invalid:'לא תקין'})[r.origins[k]])+'</td></tr>').join('')+'</tbody></table></div>':'<p class="missing">לא הוזנו הנחות ולא התקבלו נתונים מתועדים לחישוב.</p>')+'</details>';
    return html;
  }
  function storageKey(p){return 'pa_report_scenarios_v2:'+JSON.stringify(identity(p));}
  function mount(container,cfg){
    if(!container||!cfg?.property)return null;
    const p=cfg.property,readOnly=cfg.readOnly===true;
    function draftFrom(s){
      const draft=normalize(s,p);
      // Keep invalid raw entries while editing so an error cannot turn into a
      // blank field that silently falls back to a documented source on rerender.
      if(matches(s,p))for(const key of Object.keys(draft.errors))draft.inputs[key]=obj(s.inputs)[key];
      return draft;
    }
    let draft=draftFrom(cfg.scenario),stored=false,storage=cfg.storage;
    if(storage===undefined){try{storage=globalThis.localStorage;}catch(_){storage=null;}}
    if(!readOnly&&storage){try{const saved=JSON.parse(storage.getItem(storageKey(p))||'null');if(saved?.version===VERSION&&matches(saved,p)){draft=draftFrom(saved);stored=true;}}catch(_){/* A malformed or inaccessible local draft never replaces source facts. */}}
    container.innerHTML='<h3>תרחישי עסקה, BRRRR ורגישות</h3><p class="source-note">הנחות לבדיקה, ללא אומדן אוטומטי למחיר מכירה או לשיפוץ. שדה ריק משתמש במקור מתועד רק אם הוא זמין; יתר הנתונים נשארים חסרים. סכום אפס מחייב הזנה מפורשת.</p>'+
      (readOnly?'':'<details class="no-print scenario-editor"><summary>הזנת הנחות ידניות</summary>'+GROUPS.map(([title,keys])=>'<fieldset><legend>'+esc(title)+'</legend><div class="facts">'+keys.map(k=>'<label>'+esc(FIELDS[k][0])+'<input type="text" inputmode="decimal" autocomplete="off" dir="ltr" data-rs-field="'+k+'" style="display:block;width:100%;max-width:220px"><small data-rs-error="'+k+'"></small></label>').join('')+'</div></fieldset>').join('')+'<p class="source-note">כל ההון שהושקע כולל רכישה, שיפוץ, החזקה ומימון ששולמו מכספך עד המחזור. סכום פירעון החוב חייב לכלול את כל החובות המוחלפים. הסכומים אינם נגזרים ממחיר הנכס.</p><button type="button" data-rs-action="save">שמירת ההנחות במחשב זה</button> <button type="button" data-rs-action="reset">איפוס ההנחות המקומיות</button></details>')+
      '<p class="source-note" data-rs-status role="status"></p><div data-rs-results></div>';
    const status=container.querySelector('[data-rs-status]');
    function refresh(message){
      const result=calculate(p,draft,cfg.source);
      container.querySelector('[data-rs-results]').innerHTML=render(result);
      container.querySelectorAll('[data-rs-field]').forEach(input=>{const key=input.dataset.rsField;const error=result.errors[key];input.setAttribute('aria-invalid',String(!!error));input.placeholder=result.origins[key]==='source'?String(result.inputs[key]):'נדרש נתון';container.querySelector('[data-rs-error="'+key+'"]').textContent=error||'';});
      if(message!==undefined)status.textContent=message;
      cfg.onChange?.(result);return result;
    }
    container.querySelectorAll('[data-rs-field]').forEach(input=>{
      input.value=draft.inputs[input.dataset.rsField]??'';
      input.addEventListener('input',()=>{draft.inputs[input.dataset.rsField]=input.value;refresh('שינויים בתצוגה — טרם נשמרו במחשב.');});
    });
    container.querySelector('[data-rs-action="save"]')?.addEventListener('click',()=>{
      try{const saved=serialize(draft,p);saved.updated_at=new Date().toISOString();if(!storage)throw Error('Storage unavailable');storage.setItem(storageKey(p),JSON.stringify(saved));draft=normalize(saved,p);refresh('הנחות נשמרו במחשב זה בלבד · '+saved.updated_at.slice(0,10));}
      catch(_){refresh('ההנחות לא נשמרו. יש לבדוק את הנתונים ואת זמינות האחסון המקומי.');}
    });
    container.querySelector('[data-rs-action="reset"]')?.addEventListener('click',()=>{
      try{storage?.removeItem(storageKey(p));}catch(_){status.textContent='האיפוס המקומי לא נשמר; ההנחות הקודמות עשויות לחזור בפתיחה הבאה.';return;}
      draft=draftFrom(cfg.scenario);container.querySelectorAll('[data-rs-field]').forEach(input=>input.value=draft.inputs[input.dataset.rsField]??'');refresh('ההנחות המקומיות אופסו; מוצג התרחיש השמור לנכס, אם קיים.');
    });
    refresh((stored?'הנחות מקומיות במחשב זה':readOnly?'תרחיש בעותק הדוח השמור':'תרחיש שמור לנכס / מקורות מתועדים')+(draft.updated_at?' · '+draft.updated_at.slice(0,10):''));
    return {calculate:()=>calculate(p,draft,cfg.source),getScenario:()=>serialize(draft,p),
      getDraft:()=>JSON.parse(JSON.stringify(draft)),
      updateSource:nextSource=>{cfg.source=obj(nextSource);return refresh();}};
  }
  return {VERSION,FIELDS,identity,matches,number,normalize,serialize,payment,calculate,render,mount,storageKey};
});
