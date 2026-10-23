(function(root,factory){
  'use strict';
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.PADealAnalysis=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION=1,MAX=100000000;
  const FIELDS={
    purchase:['מחיר הרכישה לבדיקה · $',MAX],arv:['שווי לאחר שיפוץ לבדיקה · $',MAX],rent:['שכירות חודשית · $',1000000],
    flipRehab:['שיפוץ לפליפ · $',MAX],rentalRehab:['שיפוץ להשכרה · $',MAX],acquisition:['עלויות רכישה וסגירה · $',MAX],
    operatingAnnual:['הוצאות תפעול ואי־תפוסה · $ לשנה',MAX],operatingPct:['או הוצאות מתוך השכירות · %',100],
    selling:['עלויות מכירה · $',MAX],holding:['עלויות החזקה לפליפ · $',MAX],financing:['עלויות מימון לפליפ · $',MAX],
    targetProfit:['יעד רווח לפליפ · $',MAX],targetYield:['יעד תשואה על עלות ההשכרה · %',100],rulePct:['מקדם כלל האצבע לפליפ · %',100],
    ltv:['מימון מתוך מחיר הרכישה · %',100],apr:['ריבית שנתית להלוואה · %',100],years:['תקופת הלוואה · שנים',50],
    loanFees:['עלויות הקמת הלוואה להשכרה · $',MAX]
  };
  const DEFAULTS={targetYield:10,rulePct:70,ltv:75,years:30};
  const SOURCE_KEYS={purchase:'price',arv:'arv',rent:'rent',flipRehab:'flipRehab',rentalRehab:'rentalRehab',acquisition:'acquisitionCosts',operatingAnnual:null,selling:'sellingCosts',holding:'holdingCosts',financing:'financingCosts',targetProfit:'targetProfit'};
  const instances=new WeakMap();
  const clone=v=>JSON.parse(JSON.stringify(v));
  const obj=v=>v&&typeof v==='object'&&!Array.isArray(v)?v:{};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const norm=v=>String(v??'').trim().toUpperCase().replace(/\s+/g,' ');
  const identity=p=>[String(p.id??''),norm(p.address),norm(p.city),norm(p.state||'PA'),String(p.zip??'').slice(0,5)];
  const matches=(s,p)=>s?.version===VERSION&&String(s.property_id)===String(p.id)&&JSON.stringify(s.identity)===JSON.stringify(identity(p));
  function number(v,max=MAX){
    if(v===null||v===undefined||v==='')return {value:null,valid:true};
    if(typeof v!=='number'&&typeof v!=='string')return {value:null,valid:false};
    if(typeof v==='string'&&!/^\d+(?:\.\d{1,2})?$/.test(v.trim()))return {value:null,valid:false};
    const n=Number(v);return {value:Number.isFinite(n)&&n>=0&&n<=max?n:null,valid:Number.isFinite(n)&&n>=0&&n<=max};
  }
  function normalize(s,p){
    const source=matches(s,p)?s:{},inputs={},errors={};
    for(const [key,[label,max]]of Object.entries(FIELDS)){
      const n=number(obj(source.inputs)[key],max);inputs[key]=n.value;
      if(!n.valid)errors[key]=label+': יש להזין מספר שאינו שלילי, עד שתי ספרות אחרי הנקודה.';
      if(n.value===0&&['purchase','arv','rent','targetYield','rulePct','years'].includes(key))errors[key]=label+': הערך חייב להיות גדול מאפס.';
    }
    return {version:VERSION,property_id:String(p.id??''),identity:identity(p),inputs,
      updated_at:typeof source.updated_at==='string'&&Number.isFinite(Date.parse(source.updated_at))?source.updated_at:null,errors};
  }
  function serialize(s,p){const n=normalize(s,p);if(Object.keys(n.errors).length)throw Error('יש לתקן את השדות המסומנים לפני שמירה.');delete n.errors;return n;}
  const all=values=>values.every(n=>typeof n==='number'&&Number.isFinite(n));
  const sum=(...values)=>all(values)?values.reduce((a,b)=>a+b,0):null;
  const pct=(a,b)=>all([a,b])&&b>0?a/b*100:null;
  function payment(principal,apr,years){
    if(principal===0)return 0;
    if(!all([principal,apr,years])||principal<0||apr<0||years<=0)return null;
    const n=years*12,r=apr/1200;
    return r===0?principal/n:principal*r/(1-Math.pow(1+r,-n));
  }
  function calculate(p,s,source={}){
    const n=normalize(s,p),values={},origins={};
    for(const key of Object.keys(FIELDS)){
      if(n.errors[key]){values[key]=null;origins[key]='invalid';continue;}
      if(n.inputs[key]!==null){values[key]=n.inputs[key];origins[key]='assumption';continue;}
      let v=source[SOURCE_KEYS[key]];
      if(key==='operatingAnnual')v=sum(source.operatingExpenses,source.vacancyLoss);
      if(key==='targetYield')v=source.targetCap;
      const parsed=number(v,FIELDS[key][1]);
      values[key]=parsed.valid&&parsed.value!==null?parsed.value:DEFAULTS[key]??null;
      origins[key]=parsed.valid&&parsed.value!==null?'source':key in DEFAULTS?'setting':'missing';
    }
    // An explicitly selected expense percentage replaces the complete source budget.
    if(n.inputs.operatingPct!==null&&n.inputs.operatingAnnual===null){values.operatingAnnual=null;origins.operatingAnnual='overridden';}
    const v=values,annualRent=v.rent===null?null:v.rent*12;
    const operating=v.operatingAnnual!==null?v.operatingAnnual:all([annualRent,v.operatingPct])?annualRent*v.operatingPct/100:null;
    const noi=all([annualRent,operating])?annualRent-operating:null;
    const rentalAllIn=sum(v.purchase,v.rentalRehab,v.acquisition);
    const rentalMao=all([noi,v.targetYield,v.rentalRehab,v.acquisition])&&v.targetYield>0?noi/(v.targetYield/100)-v.rentalRehab-v.acquisition:null;
    const flipAllIn=sum(v.purchase,v.flipRehab,v.acquisition,v.holding,v.financing);
    const flipProfit=all([v.arv,flipAllIn,v.selling])?v.arv-flipAllIn-v.selling:null;
    const flipMao=all([v.arv,v.flipRehab,v.acquisition,v.holding,v.financing,v.selling,v.targetProfit])?v.arv-v.flipRehab-v.acquisition-v.holding-v.financing-v.selling-v.targetProfit:null;
    const ruleMao=all([v.arv,v.rulePct,v.flipRehab])?v.arv*v.rulePct/100-v.flipRehab:null;
    const loan=all([v.purchase,v.ltv])?v.purchase*v.ltv/100:null;
    const monthlyDebt=payment(loan,v.apr,v.years);
    const cashRequired=all([rentalAllIn,loan,v.loanFees])?rentalAllIn-loan+v.loanFees:null;
    const annualCashFlow=all([noi,monthlyDebt])?noi-monthlyDebt*12:null;
    const result={version:VERSION,inputs:v,origins,errors:n.errors,annualRent,operating,noi,rentalAllIn,rentalMao,
      capRate:pct(noi,v.purchase),yieldOnCost:pct(noi,rentalAllIn),grossYield:pct(annualRent,v.purchase),
      flipAllIn,flipProfit,flipMao,ruleMao,flipRoi:pct(flipProfit,flipAllIn),loan,monthlyDebt,cashRequired,
      monthlyCashFlow:annualCashFlow===null?null:annualCashFlow/12,cashOnCash:pct(annualCashFlow,cashRequired),
      rentalGap:all([rentalMao,v.purchase])?v.purchase-rentalMao:null,
      flipGap:all([flipMao,v.purchase])?v.purchase-flipMao:null};
    return result;
  }
  function documentPreset(p,s){
    const n=serialize(s,p),sqft=number(p.sqft).value;
    n.inputs.operatingAnnual=null;n.inputs.operatingPct=26;
    if(n.inputs.acquisition===null)n.inputs.acquisition=5000;
    if(sqft>0){n.inputs.flipRehab=Math.round(sqft*45*100)/100;n.inputs.rentalRehab=Math.round(sqft*27*100)/100;}
    return n;
  }
  const money=n=>n===null?'נדרש נתון':n.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0});
  const percent=n=>n===null?'נדרש נתון':n.toFixed(1)+'%';
  function summary(p,r){
    const lines=['ניתוח תרחיש עבור '+String(p.address||'הנכס')+'. התוצאות תלויות במקורות ובהנחות המוצגים להלן.'];
    if(r.inputs.purchase!==null)lines.push('מחיר הרכישה שנבדק: '+money(r.inputs.purchase)+'.');
    if(r.rentalAllIn!==null)lines.push('עלות רכישה, שיפוץ וסגירה להשכרה: '+money(r.rentalAllIn)+'.');
    if(r.noi!==null)lines.push('הכנסה תפעולית שנתית לפני מימון ומס הכנסה: '+money(r.noi)+'.');
    if(r.rentalMao!==null)lines.push('תקרת מחיר הרכישה לפי יעד תשואה על העלות של '+r.inputs.targetYield+'%: '+money(r.rentalMao)+(r.rentalMao<=0?' — היעד אינו מאפשר מחיר רכישה חיובי.':'.'));
    if(r.monthlyCashFlow!==null)lines.push('תזרים חודשי לאחר החזר ההלוואה: '+money(r.monthlyCashFlow)+'.');
    if(r.flipProfit!==null)lines.push('רווח פליפ לפני מס לפי העלויות שהוזנו: '+money(r.flipProfit)+'.');
    if(r.flipMao!==null)lines.push('תקרת רכישה לפליפ לפי יעד הרווח: '+money(r.flipMao)+'.');
    if(r.inputs.arv===null)lines.push('חסר שווי לאחר שיפוץ; אומדן שווי שוק ומחירי מודעות אינם ARV מאומת.');
    if(r.noi===null)lines.push('להשלמת ניתוח ההשכרה נדרשים שכירות ותקציב תפעול הכולל אי־תפוסה.');
    return lines.join(' ');
  }
  function metricsHTML(r){
    const metric=(label,value,format=money)=>'<div class="da-metric"><span>'+label+'</span><strong dir="ltr">'+format(value)+'</strong></div>';
    return '<div class="da-strategies"><section><h3>השכרה</h3><div class="da-metrics">'+
      metric('עלות רכישה + שיפוץ + סגירה',r.rentalAllIn)+metric('NOI שנתי · לפני מימון',r.noi)+metric('תשואה על מחיר הרכישה · Cap Rate',r.capRate,percent)+metric('תשואה על העלות הכוללת',r.yieldOnCost,percent)+metric('תקרת רכישה לפי יעד התשואה על העלות',r.rentalMao)+metric('מחיר הבדיקה פחות תקרת הרכישה',r.rentalGap)+
      '</div></section><section><h3>פליפ</h3><div class="da-metrics">'+
      metric('השקעה כולל החזקה ומימון',r.flipAllIn)+metric('רווח צפוי לפני מס',r.flipProfit)+metric('תשואה על ההשקעה · אינה שנתית',r.flipRoi,percent)+metric('תקרת רכישה לפי יעד הרווח',r.flipMao)+metric('מחיר הבדיקה פחות תקרת הרכישה',r.flipGap)+metric('תקרה ראשונית לפי כלל '+r.inputs.rulePct+'%',r.ruleMao)+
      '</div><p>כלל האצבע: ARV × '+r.inputs.rulePct+'% פחות שיפוץ. הוא אינו תחשיב רווח ואינו מחליף את תקרת הרכישה לפי העלויות המלאות.</p></section></div><section class="da-debt"><h3>מימון להשכרה · תרחיש הלוואה בתשלום קבוע</h3><div class="da-metrics">'+
      metric('סכום הלוואה · מימון מחיר הרכישה בלבד',r.loan)+metric('הון מזומן לרכישה, שיפוץ, סגירה והקמת הלוואה',r.cashRequired)+metric('החזר הלוואה חודשי · קרן וריבית',r.monthlyDebt)+metric('תזרים חודשי לאחר החזר הלוואה',r.monthlyCashFlow)+metric('תשואה שנתית על ההון המזומן',r.cashOnCash,percent)+
      '</div><p>הוצאות התפעול צריכות לכלול מסי נכס, ביטוח, תחזוקה, ניהול ואי־תפוסה. מודל המימון אינו הצעת מלווה ואינו כולל מחזור הלוואה.</p></section>';
  }
  function inputsTable(r){return '<table><thead><tr><th>נתון</th><th>ערך בתרחיש</th><th>בסיס</th></tr></thead><tbody>'+Object.entries(FIELDS).map(([key,[label]])=>'<tr><td>'+esc(label)+'</td><td dir="ltr">'+(r.inputs[key]===null?'חסר':esc(r.inputs[key]))+'</td><td>'+({source:'נתון מהמקורות / יעד שנשמר',assumption:'הנחה שהוזנה בתרחיש',setting:'הגדרת מודל מוצעת',missing:'חסר נתון',invalid:'הזנה לא תקינה',overridden:'מוחלף באחוז ההוצאות שהוזן'}[r.origins[key]])+'</td></tr>').join('')+'</tbody></table>';}
  function reportHTML(p,s,source){const r=calculate(p,s,source);return '<h2>מחשבון עסקה · הנחות ותרחיש</h2><p>'+esc(summary(p,r))+'</p>'+metricsHTML(r)+'<h3>הנתונים ששימשו לחישוב</h3>'+inputsTable(r);}
  function mount(container,config){
    if(!container)return null;
    let state=instances.get(container),subject=JSON.stringify(identity(config.property));
    if(state&&state.subject===subject){state.config=config;state.refresh();return state;}
    if(state)state.destroy();
    let cfg=config,s=normalize(cfg.scenario,cfg.property),dirty=false,revision=0,timer=null,saving=null,status='';
    const draftKey='pa_deal_analysis_draft_v1:'+subject;
    const storage=cfg.storage;
    let draft=null;
    try{draft=JSON.parse(storage?.getItem(draftKey)||'null');}catch(_){}
    if(!cfg.readOnly&&matches(draft?.scenario,cfg.property)&&draft.base===JSON.stringify(cfg.scenario||null)){
      s=draft.scenario;dirty=true;status='שוחזרה טיוטה מקומית שטרם נשמרה בענן. לחץ שמירה כדי לאשר אותה.';
    }
    const groups=[['מחיר, שווי ושכירות',['purchase','arv','rent']],['שיפוץ ורכישה',['flipRehab','rentalRehab','acquisition']],['הוצאות ויעד להשכרה',['operatingAnnual','operatingPct','targetYield']],['עלויות ויעד לפליפ',['selling','holding','financing','targetProfit','rulePct']],['מימון להשכרה',['ltv','apr','years','loanFees']]];
    container.innerHTML='<div class="da-heading"><div><span class="da-kicker">בדיקת כדאיות · תרחיש שמור לנכס</span><h2>מחשבון פליפ והשכרה</h2></div><a href="#source-settings" class="no-print">איסוף נתונים מהמקורות</a></div><p>נתון ממקור מוצע כבסיס. כל ערך שתזין כאן הוא הנחת עבודה בתרחיש ואינו משנה את המקור. שדה ריק משתמש במקור, אם קיים; אפס מחייב הזנה מפורשת.</p><div class="da-tools no-print"><button type="button" data-da-action="preset">טען הנחות עבודה מהמסמך</button><button type="button" data-da-action="rehab">העתק תקציב שיפוץ ידני לפליפ</button></div><p class="da-preset-note no-print">הנחות המסמך: $45 לרגל רבועה לפליפ, $27 להשכרה, הוצאות כולל אי־תפוסה 26% מהשכירות, וסגירה $5,000 אם טרם הוזנה. אלו הנחות לבדיקה; העלויות בפועל תלויות בנכס. יעדי המודל המוצעים: תשואה על העלות 10%, כלל פליפ 70%, מימון 75% ותקופה 30 שנה. ריבית ויתר העלויות דורשות הזנה.</p><details class="da-editor no-print"><summary>עריכת הנחות העבודה</summary>'+groups.map(([title,keys])=>'<fieldset><legend>'+title+'</legend><div class="da-inputs">'+keys.map(key=>'<label>'+esc(FIELDS[key][0])+'<input inputmode="decimal" dir="ltr" type="text" autocomplete="off" data-da-field="'+key+'" aria-describedby="da-note-'+key+'"><small id="da-note-'+key+'"></small></label>').join('')+'</div></fieldset>').join('')+'<div class="da-tools"><button type="button" data-da-action="save">שמור תרחיש</button><button type="button" data-da-action="reset">חזור למקורות ללא ההנחות</button></div></details><p class="da-status no-print" role="status" aria-live="polite"></p><div class="da-results"></div><details class="da-basis"><summary>הנתונים ששימשו לחישוב</summary><div class="da-input-table"></div></details>';
    const statusNode=container.querySelector('.da-status'),results=container.querySelector('.da-results'),basis=container.querySelector('.da-input-table');
    function show(text){status=text;statusNode.textContent=text;}
    function draftSave(){try{storage?.setItem(draftKey,JSON.stringify({base:JSON.stringify(cfg.scenario||null),scenario:s}));}catch(_){show('הטיוטה לא נשמרה בדפדפן. שמור לענן לפני יציאה.');}}
    function renderFields(){const r=calculate(cfg.property,s,cfg.source);container.querySelectorAll('[data-da-field]').forEach(input=>{const key=input.dataset.daField;input.value=s.inputs[key]??'';input.placeholder=r.origins[key]==='source'||r.origins[key]==='setting'?String(r.inputs[key]??''):'נדרש נתון';});}
    function refresh(){
      cfg=state.config;
      if(!dirty&&!saving){s=normalize(cfg.scenario,cfg.property);renderFields();}
      const r=calculate(cfg.property,s,cfg.source);
      results.innerHTML='<p class="da-summary">'+esc(summary(cfg.property,r))+'</p>'+metricsHTML(r);
      basis.innerHTML=inputsTable(r);
      container.querySelectorAll('[data-da-field]').forEach(input=>{const key=input.dataset.daField, note=container.querySelector('#da-note-'+key);input.placeholder=r.origins[key]==='source'||r.origins[key]==='setting'?String(r.inputs[key]??''):'נדרש נתון';input.disabled=!!cfg.readOnly;input.setAttribute('aria-invalid',r.errors[key]?'true':'false');note.textContent=r.errors[key]||({source:'בסיס מהמקורות / יעד שנשמר',assumption:'הנחה בתרחיש',setting:'הגדרת מודל מוצעת',missing:'חסר נתון',invalid:'הזנה לא תקינה',overridden:'מוחלף באחוז ההוצאות שהוזן'}[r.origins[key]]);});
      container.querySelectorAll('button').forEach(b=>b.disabled=!!cfg.readOnly||!!saving||!!cfg.busy);
      if(cfg.readOnly)show('צפייה בתרחיש שמור. עריכת הנחות זמינה בדוח הנכס העדכני.');else statusNode.textContent=status;
    }
    function schedule(){clearTimeout(timer);timer=setTimeout(()=>save().catch(()=>{}),1400);}
    function changed(){dirty=true;revision++;s.updated_at=new Date().toISOString();draftSave();show('טיוטה מקומית · ממתין לשמירה אוטומטית…');refresh();if(!Object.keys(normalize(s,cfg.property).errors).length)schedule();else show('הטיוטה נשמרה בדפדפן. יש לתקן את השדות המסומנים כדי לשמור לענן.');}
    async function save(){
      clearTimeout(timer);if(cfg.readOnly)return;if(saving){await saving;if(dirty)return save();return;}
      if(!dirty)return;
      if(cfg.busy){show('ממתין לסיום שמירה אחרת בדוח…');schedule();return;}
      const next=serialize(s,cfg.property),version=revision;
      show('שומר תרחיש לענן…');
      saving=(async()=>{try{
        await cfg.onSave(next);cfg=state.config;
        if(revision===version){dirty=false;s=normalize(next,cfg.property);try{storage?.removeItem(draftKey);}catch(_){}show('התרחיש נשמר לענן. פתיחה חוזרת תטען אותו ללא איסוף נוסף.');}
        else{draftSave();schedule();}
      }catch(error){show('התרחיש לא נשמר לענן: '+error.message+'. הטיוטה נשארה בדפדפן.');throw error;}finally{saving=null;refresh();}})();
      refresh();return saving;
    }
    function input(event){const key=event.target?.dataset?.daField;if(!FIELDS[key]||cfg.readOnly)return;s.inputs[key]=event.target.value;changed();}
    async function click(event){const action=event.target.closest('[data-da-action]')?.dataset.daAction;if(!action||cfg.readOnly)return;
      try{
        if(action==='save'){await save();return;}
        if(action==='reset')s=normalize(null,cfg.property);
        if(action==='preset'){s=documentPreset(cfg.property,s);s=normalize(s,cfg.property);}
        if(action==='rehab'){
          const budget=cfg.rehabBudget;if(!budget?.complete)throw Error('יש לתמחר את כל סעיפי השיפוץ הרלוונטיים לפני העתקת התקציב');
          s.inputs.flipRehab=budget.total;
        }
        changed();renderFields();
      }catch(error){show(error.message);}
    }
    container.addEventListener('input',input);container.addEventListener('click',click);
    state={subject,config,refresh,save,get dirty(){return dirty;},get pending(){return !!saving;},get scenario(){return serialize(s,cfg.property);},
      destroy(){clearTimeout(timer);container.removeEventListener('input',input);container.removeEventListener('click',click);}};
    instances.set(container,state);renderFields();refresh();return state;
  }
  return {VERSION,FIELDS,identity,matches,normalize,serialize,calculate,payment,documentPreset,summary,reportHTML,mount};
});
