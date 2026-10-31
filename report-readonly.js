/* The expanded report reads source results. It never edits a deal or scenario. */
(function(root){
  'use strict';
  const M=root.PAReadonlyReport,F=root.PAReportFinancials,S=root.SecondarySourceEvidence;
  const REPO='https://api.github.com/repos/elic555-ux/pa-intelligence-v2';
  const client=root.supabase?.createClient('https://rccncvnybybastdgdoqe.supabase.co','sb_publishable_-OFzsbOGfsPATYLeD5Grnw_GpHYDAAC');
  const state={property:null,report:null,analysis:null,county:null,media:[],reference:Date.now(),historical:false};
  const $=id=>document.getElementById(id),e=M.esc,n=M.number,money=M.money;
  const date=x=>x?String(x).slice(0,10):'לא נשמר מועד';
  const list=x=>Array.isArray(x)?x:[];
  const source=(name,when,url)=>'<p class="source-note">'+e(name||'לא נשמר מקור')+' · '+e(date(when))+(M.url(url)?' · <a href="'+e(M.url(url))+'" target="_blank" rel="noopener noreferrer">מקור הנתון</a>':'')+'</p>';
  const missing=text=>'<p class="missing">'+e(text)+'</p>';
  const fact=(name,value)=>'<div class="fact"><span>'+e(name)+'</span><strong>'+e(value??'לא פורסם')+'</strong></div>';
  function metric(label,value,note='',format=money){return '<div class="metric"><span>'+e(label)+'</span><strong'+(value==null?' class="missing"':'')+'>'+e(value==null?'חסר נתון':format(value))+'</strong>'+(note?'<small>'+e(note)+'</small>':'')+'</div>';}
  function status(message,error=false){$('report-status').textContent=message;$('report-status').classList.toggle('error',error);}
  function tab(name){
    for(const id of ['overview','financials']){$('tab-'+id).hidden=id!==name;$('btn-'+id).setAttribute('aria-selected',String(id===name));}
  }
  function county(){
    const context=state.analysis?.public_context;
    const record=state.report?.internal_data?.county_record||
      (root.PAReportPublicContext?.valid(context,state.property,null,state.reference)?context.county_record:null);
    return record?.status==='matched'&&sameAddress(record.subject,state.property)?record:null;
  }
  function sameAddress(a,b){return !!a&&!!b&&JSON.stringify(M.identity({...a,id:b.id}))===JSON.stringify(M.identity(b));}
  function renderProperty(){
    const p=state.property,m=M.metrics(p,state.analysis,state.report,F,state.reference),c=state.county;
    $('report-address').textContent=p.address;$('report-location').textContent=[p.city,p.state||'PA',p.zip].filter(Boolean).join(' · ');
    document.title=p.address+' — דוח נכס מורחב';
    const fields=c?.fields||{};
    $('property-view').innerHTML='<div class="metric-grid">'+metric('מחיר מבוקש',m.price,'מחיר הפרסום שנשמר')+metric('מחיר ל־SqFt',m.price!==null&&n(fields.sqft??p.sqft)>0?m.price/n(fields.sqft??p.sqft):null,'לפי המחיר והשטח הרשומים',M.unitMoney)+metric('אומדן שכירות חודשי',m.rent,'אומדן או נתון שמור עם מקור')+metric('תשואה ברוטו',m.grossYield,'לפני הוצאות ומימון',M.percent)+'</div>'+
      '<p class="summary">'+e([p.address+', '+p.city,m.price!==null?'מחיר מבוקש '+money(m.price):'מחיר פרסום עדכני לא אומת',m.rent!==null?'שכירות לפי המקור '+money(m.rent)+' לחודש':'מקור שכירות עדיין חסר',m.grossYield!==null?'תשואה ברוטו '+M.percent(m.grossYield):null].filter(Boolean).join(' · '))+'.</p>'+
      '<div class="property-facts">'+[['חדרים',fields.beds??p.beds],['רחצה',fields.baths??p.baths],['שטח SqFt',fields.sqft??p.sqft],['שנת בנייה',fields.year_built??p.year_built],['סוג הנכס',p.property_type||p.type],['סטטוס בפרסום',p.listing_status||p.status]].map(([k,v])=>'<div><span>'+e(k)+'</span><b>'+e(v??'לא פורסם')+'</b></div>').join('')+'</div>'+
      '<div class="source-links">'+(M.url(p.url||p.source_url)?'<a href="'+e(M.url(p.url||p.source_url))+'" target="_blank" rel="noopener noreferrer">מודעת המקור</a>':'')+'<span>נבדק בפרסום: '+e(date(p.last_source_check))+'</span></div>';
  }
  function renderPhotos(){
    const photos=state.media,view=$('photos-view');
    if(!photos.length){view.innerHTML=missing('לא התקבלו תמונות מקור מאומתות לנכס הזה.');return;}
    view.innerHTML='<div class="gallery">'+photos.map((p,i)=>'<img '+(i?'loading="lazy" ':'')+'src="'+e(p.url)+'" alt="'+e('תמונת מקור '+(i+1)+' — '+state.property.address)+'" data-report-photo="'+i+'">').join('')+'</div><p id="photos-summary" class="source-note"></p><p id="photos-unavailable" class="missing" hidden></p>';
    const summary=$('photos-summary'),unavailable=$('photos-unavailable'),loaded=new Set(),failed=new Set();
    function update(){
      const pending=photos.length-loaded.size-failed.size;
      const sources=[...new Set([...loaded].map(i=>photos[i].source||photos[i].source_name||'מודעת הנכס'))];
      summary.textContent=(loaded.size?'מוצגות '+loaded.size+' תמונות מקור. '+sources.join(' · ')+'. ':pending?'ממתין לטעינת תמונות המקור. ':'לא נטענו תמונות מקור כרגע. ')+(pending?pending+' תמונות טרם נטענו. ':'')+'מועד האיסוף אינו בהכרח מועד הצילום.';
      unavailable.hidden=!failed.size;
      unavailable.textContent=failed.size+' קישורי תמונות נוספים אינם נטענים כרגע מהספק.';
    }
    view.querySelectorAll('img').forEach(img=>{
      const index=Number(img.dataset.reportPhoto);
      function success(){failed.delete(index);loaded.add(index);update();}
      function failure(){loaded.delete(index);failed.add(index);img.remove();update();}
      img.addEventListener('load',success,{once:true});
      img.addEventListener('error',failure,{once:true});
      if(img.complete){if(img.naturalWidth>0)success();else failure();}
    });
    update();
  }
  function renderCounty(){
    const c=state.county;
    if(!c){$('county-view').innerHTML=missing('לא התקבל רישום מחוז התואם בוודאות לכתובת הנכס.');return;}
    const f=c.fields||{};
    $('county-view').innerHTML='<div class="facts">'+[['מזהה חלקה',c.parcel_id],['מחוז',c.county],['רשות מקומית',f.municipality],['שימוש רשום',f.county_use],['מבנה רשום',f.style],['שטח מגרש SqFt',f.lot_size],['שטח מגורים SqFt',f.sqft],['שנת בנייה',f.year_built],['חדרים / רחצה',[f.beds,f.baths].map(x=>x??'—').join(' / ')]].map(([k,v])=>fact(k,v)).join('')+'</div>'+source(c.source_name,c.retrieved_at,c.source_url)+'<p class="muted">תאריך נתוני המחוז: '+e(c.source_as_of||'לא פורסם')+'. רישום המחוז מתאר מאפייני נכס; הוא אינו בדיקת מצב עדכנית.</p>';
  }
  function renderArea(){
    const a=M.matches(state.analysis,state.property)?state.analysis:null,point=a?.closed_sales?.subject_location||a?.public_context?.parcel_location;
    const area=M.officialArea(a,state.property,state.reference);
    let out='<div class="facts">'+fact('עיר',state.property.city)+fact('שכונה לפי המיפוי',point?.neighborhood)+fact('רשות מקומית',state.county?.fields?.municipality||point?.municipality)+fact('אזור סטטיסטי Census',point?.tract)+'</div>';
    if(area){
      const rows=list(area.metrics).filter(x=>n(x.value)!==null&&n(x.value)>=0);
      out+='<p class="source-note">'+e(area.geography_name)+' · תקופת הנתונים: '+e(area.source_period)+'</p>';
      if(area.status==='cached_stale')out+=missing('עדכון המקור לא הושלם; מוצגת הגרסה הקודמת.');
      out+='<div class="table-scroll"><table><thead><tr><th>מדד רשמי</th><th>אומדן שפורסם</th><th>טעות דגימה ±</th></tr></thead><tbody>'+rows.map(x=>'<tr><td>'+e(x.label)+'</td><td>'+e(x.unit?.startsWith('USD')?money(x.value):Number(x.value).toLocaleString('en-US'))+'</td><td>'+e(n(x.margin_of_error)===null?'לא זמין':x.margin_of_error)+'</td></tr>').join('')+'</tbody></table></div>'+source(area.source_name,area.observed_at,area.source_url);
    }else out+=missing('נתוני האזור הרשמיים טרם נאספו או שלא אומתה התאמה לאזור הנכס.');
    out+='<p class="source-note">נתוני Census מתייחסים לאזור סטטיסטי, לא לרחוב. אין בדוח ציון שכונה מחושב.</p>';
    if(root.PAReportPublicContext)out+=root.PAReportPublicContext.render(a?.public_context,state.property,state.county,state.reference);
    $('area-view').innerHTML=out;
  }
  function renderPropertyTaxes(){
    const t=M.propertyTaxes(state.analysis,state.property,state.county,state.reference);
    if(!t)return '<details class="pricebook"><summary>מסי נכס והוצאות תפעול · נדרש מידע נוסף</summary>'+missing('לא אומת חשבון מס עדכני התואם לנכס. מסי עירייה, בתי ספר והיטלים מקומיים, וכן שאר הוצאות התפעול, טרם הושלמו.')+'</details>';
    const discount=n(t.county_discount_amount),validDiscount=discount>0&&discount<=t.county_face_amount;
    return '<details class="pricebook"><summary>מסי נכס והוצאות תפעול · אומת מס המחוז בלבד</summary>'+
      '<p class="notice">חשבון מס מחוז לשנת '+e(t.tax_year)+' לפי רישום הבעלים הנוכחי. '+(t.homestead_exclusion_applied===true?'הרישום מציין הטבת Homestead. ':'')+'זכאות של רוכש חדש להטבות לא אומתה; החשבון אינו תחזית מס למשקיע או יתרה לתשלום.</p>'+
      '<div class="table-scroll"><table><thead><tr><th>רכיב מס שנתי</th><th>סכום מתועד</th><th>בסיס הנתון</th></tr></thead><tbody><tr><td>מחוז Allegheny</td><td>'+e(M.unitMoney(t.county_face_amount))+'</td><td>חיוב שנתי לפני הנחת תשלום מוקדם · '+e(date(t.face_due_date))+'</td></tr>'+
      '<tr><td>עירייה</td><td>טרם אומת</td><td>נדרש חשבון עירוני תואם</td></tr><tr><td>בתי ספר</td><td>טרם אומת</td><td>לא נכלל בחשבון המחוז</td></tr><tr><td>היטלים מקומיים נוספים</td><td>טרם אומתו</td><td>יש לבדוק בחשבון העירוני</td></tr></tbody></table></div>'+
      (validDiscount?'<p class="source-note">בחשבון פורסם גם סכום לאחר הנחת תשלום מוקדם: '+e(M.unitMoney(discount))+' עד '+e(date(t.discount_due_date))+'. הסכום אינו נוסף לחיוב השנתי.</p>':'')+
      missing('סך מסי הנכס ומלוא הוצאות התפעול עדיין חסרים. מס המחוז לבדו אינו משמש לחישוב NOI או תשואה נטו.')+
      source(t.source_name,t.observed_at,t.source_url)+'</details>';
  }
  function renderAnalysis(){
    const p=state.property,m=M.metrics(p,state.analysis,state.report,F,state.reference);
    let out='<div class="metric-grid">'+metric('שכירות שנתית ברוטו',m.annualRent)+metric('תשואה ברוטו',m.grossYield,'לפני הוצאות ומימון',M.percent)+metric('NOI שנתי',m.noi,'דורש הוצאות מלאות ומתועדות')+metric('Cap Rate',m.capRate,'לפני מימון',M.percent)+'</div>'+renderPropertyTaxes();
    $('analysis-view').innerHTML=out;
    const inputs={...F.financialSnapshot(p,state.reference),rent:m.rent};
    if(root.PAReportScenarios){
      if(state.scenarios)state.scenarios.updateSource(inputs);
      else state.scenarios=root.PAReportScenarios.mount($('scenario-view'),{property:p,scenario:p.investment_scenario,source:inputs,readOnly:state.historical});
    }
  }
  function renderSummary(){
    const p=state.property,m=M.metrics(p,state.analysis,state.report,F,state.reference),items=[];
    items.push(...M.propertyConflicts(p,state.county));
    if(m.price===null)items.push('מחיר מבוקש עדכני לא אומת');
    if(m.rent===null)items.push('אומדן שכירות חסר');
    if(!state.county)items.push('רישום חלקה תואם חסר');
    if(!M.recordedSales(state.analysis,p,state.reference).length)items.push('השוואות מכירה מתאימות חסרות');
    if(m.noi===null)items.push('הוצאות מלאות חסרות — לא ניתן לחשב נטו');
    if(!M.officialArea(state.analysis,p,state.reference))items.push('נתוני אזור רשמיים חסרים');
    $('summary-view').innerHTML=(items.length?'<ul>'+items.map(x=>'<li>'+e(x)+'</li>').join('')+'</ul>':'<p>נתוני הבסיס זמינים; יש לבדוק את ההנחות ומועד המקורות.</p>')+'<p class="source-note">בעלות, שעבודים והיתרים: נדרשת בדיקה משלימה. הדוח אינו אישור לזכויות או לתקינות הנכס.</p>';
  }
  function renderComparisons(){
    const a=state.analysis,p=state.property,rows=M.recordedSales(a,p,state.reference),rent=M.rentSource(state.report,p,state.reference);
    const value=M.currentValue(a,state.report,p,state.reference);
    let out='<article class="comparison-part"><h3>שווי נוכחי והשוואות מכירה</h3>';
    out+=value?'<div class="metric-grid">'+metric('שווי נוכחי משוער',value.value)+metric('קצה תחתון שפורסם',value.low)+metric('קצה עליון שפורסם',value.high)+'</div>'+source(value.source,value.date,value.url):missing('לא התקבל אומדן שווי נוכחי מבוסס.');
    if(rows.length){out+='<p class="muted">טווח חיפוש: '+e(a.closed_sales.policy.lookback_days)+' ימים · עד '+e(a.closed_sales.policy.radius_miles)+' מייל · שטח בטווח של 25% וסטייה של עד חדר שינה אחד. מוצגים מחירים ששולמו לפי הרישום.</p><div class="table-scroll"><table><thead><tr><th>כתובת</th><th>מחיר מכירה</th><th>תאריך מכירה / רישום</th><th>שטח SqFt</th><th>חדרים / רחצה</th><th>מרחק במייל</th><th>שכונה ובדיקת המחוז</th></tr></thead><tbody>'+rows.map(r=>'<tr><td class="address">'+e(r.address)+'</td><td>'+e(money(r.amount))+'</td><td>'+e(r.sale_date)+'<br><small>'+e(r.recorded_date)+'</small></td><td>'+e(r.sqft)+'</td><td>'+e(r.beds??'—')+' / '+e(r.baths??'—')+'</td><td>'+e(r.distance_miles)+'</td><td>'+e(r.neighborhood||'לא זוהתה')+'<br><span class="tag">'+(r.validation==='county_validated'?'המחוז סיווג כעסקת שוק':'טרם בדיקת תקפות במחוז')+'</span></td></tr>').join('')+'</tbody></table></div>'+source(a.closed_sales.source_name,a.closed_sales.observed_at,a.closed_sales.source_url)+'<p class="muted">עסקה שטרם נבדקה במחוז אינה נחשבת עסקת שוק מאומתת לצורך אומדן.</p>';
      if(a.closed_sales.query_limit_reached)out+=missing('החיפוש הגיע למגבלת הרשומות. ייתכן שקיימות עסקאות מתאימות נוספות.');
    }else out+=missing('לא נמצאו עסקאות מכירה רשומות ומתאימות בתוצאה השמורה. מחירי מודעות אינם מוצגים במקומן.');
    out+='</article><article class="comparison-part"><h3>אומדן שכירות והשוואות להשכרה</h3>';
    if(rent){out+='<div class="metric-grid">'+metric('אומדן שכירות חודשי',rent.value)+metric('קצה תחתון בטווח הספק',rent.low)+metric('קצה עליון בטווח הספק',rent.high)+'</div><p class="muted">אומדן הספק נשען על מודעות שכירות דומות. מחירי השכירות להלן הם מחירים מבוקשים, ללא אימות חוזים חתומים.</p><div class="table-scroll"><table><thead><tr><th>כתובת</th><th>שכירות במודעה</th><th>מועד במקור</th><th>שטח SqFt</th><th>חדרים / רחצה</th><th>מרחק במייל</th></tr></thead><tbody>'+rent.comparables.map(r=>'<tr><td class="address">'+e(r.address)+'</td><td>'+e(money(r.amount))+'</td><td>'+e(date(r.date))+'</td><td>'+e(r.sqft??'—')+'</td><td>'+e(r.beds??'—')+' / '+e(r.baths??'—')+'</td><td>'+e(r.distance??'—')+'</td></tr>').join('')+'</tbody></table></div>'+source(rent.source,rent.date,rent.url);
    }else out+=missing('לא התקבל אומדן שכירות עדכני שעבר בדיקת התאמה לנכס.');
    $('comparisons-view').innerHTML=out+'</article>';
  }
  function renderHistory(){
    const rows=M.priceHistory(state.property,state.reference);
    $('history-view').innerHTML=rows.length?'<div class="table-scroll"><table><thead><tr><th>מועד תצפית</th><th>מחיר בפרסום</th><th>מקור</th></tr></thead><tbody>'+rows.map(r=>'<tr><td>'+e(date(r.date))+'</td><td>'+e(money(r.price))+'</td><td>'+e(r.source||'לא נשמר')+'</td></tr>').join('')+'</tbody></table></div><p class="source-note">מחירים שנצפו ונשמרו בסריקות; פערים אינם מוכיחים שהמחיר נשאר קבוע. עסקאות מכירה בסעיף 06.</p>':missing('טרם הצטברה היסטוריית מחירים. התיעוד מתחיל בתצפית הראשונה שנשמרה.');
  }
  function renderFinancing(){
    const rows=M.financing(state.analysis,state.property,state.reference);
    $('financing-view').innerHTML=rows.length?'<p class="notice">אלה תנאי מוצר שפורסמו באתרי המלווים, ולא הצעות אישיות לנכס או אישור זכאות. תושבות, ניסיון, שווי ומצב הנכס עשויים לשנות את התנאים. ריבית אינה העלות הכוללת של ההלוואה.</p><div class="table-scroll"><table><thead><tr><th>מלווה ומסלול</th><th>ריבית מפורסמת</th><th>תקרות מימון מפורסמות</th><th>עמלת פתיחה</th><th>תקופה ומגבלות</th><th>מקור ותאריך</th></tr></thead><tbody>'+rows.map(r=>'<tr><td>'+e(r.lender)+'<br>'+e(r.product)+'</td><td>'+e(r.rate_text)+'</td><td>'+[r.purchase_text&&'רכישה: '+r.purchase_text,r.ltv_text&&'LTV: '+r.ltv_text,r.ltc_text&&'LTC: '+r.ltc_text,r.larv_text&&'ARV: '+r.larv_text].filter(Boolean).map(e).join('<br>')+'</td><td>'+e(r.origination_text||'לא פורסם שיעור בעמוד; נדרשת הצעה')+'</td><td>'+e(r.term_text||'נדרשת הצעה')+'<br><small>מינימום הלוואה: '+e(r.minimum_loan||'לא פורסם')+(r.fico_text?' · FICO '+e(r.fico_text):'')+'</small></td><td><a href="'+e(M.url(r.source_url))+'" target="_blank" rel="noopener noreferrer">'+e(r.source_name)+'</a><br>'+e(date(r.observed_at))+'</td></tr>').join('')+'</tbody></table></div><p class="source-note">LTV — יחס לשווי הנכס; LTC — יחס לעלות הפרויקט; ARV — יחס לשווי לאחר שיפוץ. תנאי מינימום ועמלות נוספות דורשים בדיקת מלווה. נתונים אלה אינם מוכנסים אוטומטית כתנאי הלוואה מאושרים לתחשיב בסעיף 05.</p>':missing('לא התקבלו תנאי מימון עדכניים ממקורות המלווים.');
  }
  function render(){state.county=county();renderProperty();renderPhotos();renderCounty();renderArea();renderAnalysis();renderComparisons();renderHistory();renderFinancing();renderSummary();$('report-date').textContent='גרסה '+M.VERSION+' · ריכוז מקורות: '+date(state.analysis?.generated_at||state.report?.generated_at||state.analysis?.public_context?.generated_at);$('report-content').hidden=false;}
  function token(){try{return localStorage.getItem('pa_github_token')||'';}catch(_){return '';}}
  const pendingKey=p=>'pa_report_pending:'+JSON.stringify(M.identity(p));
  function readPending(p){try{return M.pendingRequest(localStorage.getItem(pendingKey(p)),p);}catch(_){return null;}}
  function clearPending(p,id){try{if(readPending(p)?.request_id===id)localStorage.removeItem(pendingKey(p));}catch(_){}}
  async function repoJson(path){
    if(!/^(?:COMPS_REPORTS\/)[a-zA-Z0-9_./-]+\.json$/.test(path)||path.includes('..'))throw new Error('Invalid report path');
    const headers={Accept:'application/vnd.github+json'};if(token())headers.Authorization='Bearer '+token();
    const response=await fetch(REPO+'/contents/'+path+'?ref=main',{headers,cache:'no-store'});
    if(response.status===404)return null;if(!response.ok)throw new Error('קריאת המקור לא הושלמה ('+response.status+')');
    const body=await response.json();if(body.encoding!=='base64'||typeof body.content!=='string')throw new Error('מבנה קובץ המקור אינו תקין');
    const bytes=Uint8Array.from(atob(body.content.replace(/\s/g,'')),c=>c.charCodeAt(0));return JSON.parse(new TextDecoder().decode(bytes));
  }
  async function key(id){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(String(id)));return Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,'0')).join('').slice(0,32);}
  async function loadMedia(k){
    if(state.historical){const media=state.property.report_listing_media;if(media&&media.property_id===String(state.property.id)&&JSON.stringify(media.identity)===JSON.stringify(S?.identity(state.property)))resolveMedia(media);else resolveMedia({});return;}
    const paths=['listing_details','listing_photos',...['howardhanna','eriemoves','tarasa','clearchoice'].map(x=>'additional_sources/'+x)];
    const values=await Promise.allSettled(paths.map(x=>repoJson('COMPS_REPORTS/'+x+'/'+k+'.json')));
    resolveMedia({details:values[0].value,receipt:values[1].value,additional:values.slice(2).filter(x=>x.status==='fulfilled').map(x=>x.value).filter(Boolean)});
  }
  function resolveMedia(media){
    if(!S)return;const p=state.property;state.media=S.reportPhotos(p,media).filter(x=>M.url(x.url));
  }
  async function loadOfficialArea(){
    if(state.historical)return;
    const p=state.property,base=M.matches(state.analysis,p)?state.analysis:{property_id:String(p.id),property:p};
    try{const context=await repoJson('COMPS_REPORTS/public_context/'+await key(p.id)+'.json');
      if(root.PAReportPublicContext?.valid(context,p,state.county,state.reference))state.analysis={...base,public_context:context};
    }catch(_){/* Preserve the last matching context on a temporary source failure. */}
    if(!M.matches(state.analysis,p))return;
    const point=state.analysis.closed_sales?.subject_location||state.analysis.public_context?.parcel_location;
    const geoid=String(point?.geoid||'').slice(0,11)||(point?.parcel_id&&/^\d{6}$/.test(String(point.tract))?'42003'+point.tract:'');
    if(!/^42003\d{6}$/.test(geoid))return;
    try{const area=await repoJson('COMPS_REPORTS/official_area/'+geoid+'.json');
      const candidate={...state.analysis,area};
      if(M.officialArea(candidate,p,state.reference)&&(!state.analysis.area?.observed_at||Date.parse(area.observed_at)>=Date.parse(state.analysis.area.observed_at)))state.analysis=candidate;
    }catch(_){/* Keep the original observation and its date on lookup failure. */}
  }
  async function refreshOfficial(){
    if(state.historical)return;
    const button=$('refresh-official'),view=$('official-status'),t=token();
    if(!t){view.textContent='עדכון המקורות דורש את חיבור האיסוף שהוגדר בחדר העסקאות.';return;}
    button.disabled=true;view.textContent='מעדכן נתונים רשמיים…';
    const sent=Date.now();
    try{
      const response=await fetch(REPO+'/actions/workflows/official-area-quarterly.yml/dispatches',{method:'POST',headers:{Authorization:'Bearer '+t,Accept:'application/vnd.github+json','Content-Type':'application/json'},body:JSON.stringify({ref:'main',inputs:{property_id:String(state.property.id)}})});
      if(!response.ok)throw Error('שליחת העדכון נכשלה ('+response.status+')');
      async function pollOfficial(){
        state.reference=Date.now();await loadOfficialArea();render();
        if(Date.parse(state.analysis?.public_context?.generated_at)>=sent){view.textContent='המקורות הרשמיים עודכנו. מידע שלא התקבל מסומן כחסר.';button.disabled=false;return;}
        if(Date.now()-sent>7*60000){view.textContent='העדכון טרם הסתיים; אפשר לפתוח שוב את הדוח בהמשך.';button.disabled=false;return;}
        root.setTimeout(pollOfficial,7000);
      }
      await pollOfficial();
    }catch(error){view.textContent=error.message;button.disabled=false;}
  }
  async function refresh(k){const report=await repoJson('COMPS_REPORTS/expanded_reports/'+k+'.json');if(M.matches(report,state.property)&&(!state.report||Date.parse(report.generated_at)>=Date.parse(state.report.generated_at))){state.report=report;state.analysis=report.report_analysis||null;}}
  async function collect(){
    const t=token();if(!t){status('לא נמצא חיבור האיסוף של GitHub במחשב הזה. יש להגדירו בהגדרות המערכת בחדר העסקאות.',true);return;}
    const p=state.property,pending=readPending(p),requestId=pending?.request_id||crypto.randomUUID();
    const type=/multi|duplex|triplex|apartment/i.test(p.property_type||p.type||'')?'Multi-Family':/single|rowhouse|townhouse/i.test(p.property_type||p.type||'')?'Single Family':p.property_type||p.type;
    const payload={request_id:requestId,property:{id:String(p.id),address:p.address,city:p.city,state:p.state||'PA',zip:String(p.zip||''),county:p.county||'',property_type:type,beds:n(p.beds),baths:n(p.baths),sqft:n(p.sqft)},mode:type==='Multi-Family'?'profile':'rent',refresh:false};
    if(!pending){
      try{localStorage.setItem(pendingKey(p),JSON.stringify({request_id:requestId,identity:JSON.stringify(M.identity(p)),created_at:Date.now()}));}
      catch(_){throw new Error('לא ניתן לשמור את מזהה בקשת הדוח במחשב. בקשת איסוף חדשה לא נשלחה.');}
      status('בקשת הדוח נשלחת לאיסוף המקורות…');
      const response=await fetch(REPO+'/actions/workflows/expanded-report.yml/dispatches',{method:'POST',headers:{Authorization:'Bearer '+t,Accept:'application/vnd.github+json','Content-Type':'application/json'},body:JSON.stringify({ref:'main',inputs:{payload:JSON.stringify(payload)}})});
      if(!response.ok){if(response.status>=400&&response.status<500)clearPending(p,requestId);throw new Error('שליחת בקשת הדוח נכשלה ('+response.status+'). בדוק הרשאות חיבור בהגדרות המערכת.');}
    }
    const deadline=Date.now()+7*60000;
    async function poll(){
      try{const receipt=await repoJson('COMPS_REPORTS/expanded_receipts/'+requestId+'.json');if(receipt&&M.matches(receipt,p)){clearPending(p,requestId);state.report=receipt;state.analysis=receipt.report_analysis||null;await loadMedia(await key(p.id));await loadOfficialArea();render();status(receipt.report_analysis?.status==='partial'?'איסוף המקורות הסתיים בחלקו. סעיפים חסרים מסומנים בדוח.':'האיסוף הסתיים. כל סעיף מציג את התוצאה והמקור שהתקבלו.');return;}}catch(_){/* A temporary read failure must not dispatch a second request. */}
      if(Date.now()<deadline){status('האיסוף מתבצע בשרת. הדוח יציג את התוצאה כשהיא תתקבל…');root.setTimeout(poll,7000);}else status('האיסוף טרם החזיר תוצאה. פתח שוב את הדוח לאחר סיום בקשת השרת; לא נשלחה בקשה חוזרת.',true);
    }
    await poll();
  }
  async function load(){
    try{
      const params=new URLSearchParams(location.search),id=params.get('id'),saved=params.get('report');let property;
      if(saved){if(!client)throw new Error('חיבור הדוחות השמורים לא נטען');const result=await client.from('saved_reports').select('*').eq('id',saved);if(result.error)throw new Error('לא ניתן לקרוא את הדוח השמור');if(result.data?.length!==1)throw new Error('הדוח השמור לא נמצא');const row=result.data[0];property=row.report_data?.deal_data;state.reference=Date.parse(row.report_data?.date||row.created_at);state.historical=true;if(!Number.isFinite(state.reference)||state.reference>Date.now())throw new Error('תאריך הדוח השמור אינו תקין');}
      else{
        if(!id)throw new Error('פתח את הדוח מתוך נכס בחדר העסקאות.');
        let cloud=false;
        if(client){const result=await client.from('pipeline_deals').select('property_id,deal_data').eq('property_id',id);if(!result.error){if(result.data?.length>1)throw new Error('נמצאו רשומות נכס כפולות בחדר העסקאות');if(result.data?.length===1){cloud=true;property=result.data[0].deal_data;}}}
        if(!cloud){let rows=[];try{rows=JSON.parse(localStorage.getItem('pa_pipeline_deals')||'[]');}catch(_){}const matching=list(rows).filter(p=>String(p.id)===String(id));if(matching.length>1)throw new Error('נמצאו רשומות נכס כפולות במחשב');property=matching[0];if(!property){const response=await fetch('properties.json',{cache:'no-store'});if(!response.ok)throw new Error('מלאי הנכסים אינו זמין');const inventory=list(await response.json()).filter(p=>String(p.id)===String(id));if(inventory.length!==1)throw new Error('לא נמצאה רשומת נכס יחידה במלאי');property=inventory[0];}}
      }
      if(!property||!String(property.id||'')||!property.address||!property.city||(!saved&&String(property.id)!==String(id)))throw new Error('נתוני הנכס אינם מתאימים לבקשה');
      state.property=property;state.report=M.matches(property.expanded_source_report,property)?property.expanded_source_report:null;state.analysis=state.report?.report_analysis||null;
      render();$('refresh-official').hidden=state.historical;const k=await key(property.id);
      if(!state.historical){try{await refresh(k);}catch(_){status('נתוני הנכס נטענו; עדכון המקור מהשרת לא הושלם.',true);}}
      await loadMedia(k);await loadOfficialArea();render();status(state.historical?'עותק דוח שמור · מוצגים הנתונים כפי שנשמרו במועד הדוח.':'דוח מידע לצפייה · הנתונים מוצגים עם מקור ומועד בכל סעיף.');
      openHash();
      if(!state.historical){const requested=params.get('collect')==='1';if(requested){params.delete('collect');history.replaceState(null,'',location.pathname+'?'+params.toString()+location.hash);}if(readPending(property)||(requested&&(!M.matches(state.analysis,property)||!M.fresh(state.analysis.generated_at,1))))await collect();}
    }catch(error){status(error.message,true);if(!state.property)$('report-content').hidden=true;}
  }
  function openHash(){const id=location.hash.slice(1).replace(/^section-/,'');const section=M.SECTIONS.find(x=>x.id===id);if(section){tab(section.tab);$('section-'+id).scrollIntoView({block:'start'});}}
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
  document.getElementById('tabs-bar').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const buttons=Array.from(document.querySelectorAll('[data-tab]')),at=buttons.indexOf(document.activeElement);if(at<0)return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(at+(event.key==='ArrowLeft'?1:buttons.length-1))%buttons.length;buttons[next].focus();tab(buttons[next].dataset.tab);});
  let printedDetails=[];
  root.addEventListener('beforeprint',()=>{printedDetails=Array.from(document.querySelectorAll('details')).filter(x=>!x.open);printedDetails.forEach(x=>x.open=true);});
  root.addEventListener('afterprint',()=>{printedDetails.forEach(x=>x.open=false);printedDetails=[];});
  $('refresh-official').addEventListener('click',refreshOfficial);
  $('print-report').addEventListener('click',()=>root.print());
  root.addEventListener('hashchange',openHash);load();
})(window);
