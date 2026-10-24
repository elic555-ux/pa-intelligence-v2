(function(root,factory){
  'use strict';
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.PAReportSections=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  // IDs describe the content; numbers are the stable user-facing references.
  const SECTIONS=Object.freeze([
    {id:'property',number:'01',title:'פרטי הנכס והמחיר המבוקש',tab:'tab-overview',target:'report-section-property',hint:'זהות הנכס, מאפייני הפרסום ותקציר הנתונים שנאספו.'},
    {id:'photos',number:'02',title:'תמונות הנכס',tab:'tab-overview',target:'report-section-photos',hint:'תמונות מהמקורות והעלאות שלך. מועד איסוף התמונה אינו בהכרח מועד הצילום.'},
    {id:'county',number:'03',title:'פרטי החלקה ורישום המחוז',tab:'tab-overview',target:'internal-data-card',hint:'השוואת הרישום הרשמי לפרטים שנשמרו בחדר העסקאות.'},
    {id:'neighborhood',number:'04',title:'שכונה ואזור',tab:'tab-overview',target:'report-section-neighborhood',hint:'נתוני האזור לפי המקורות הזמינים. נתון שטרם נאסף אינו דירוג נמוך.'},
    {id:'value',number:'05',title:'שווי והשוואות מכירה',tab:'tab-financials',target:'report-section-value',hint:'אומדן שווי שוק, שווי לאחר שיפוץ והשוואות. בסיסי ההערכה מוצגים בנפרד.'},
    {id:'rent',number:'06',title:'שכירות והשוואות להשכרה',tab:'tab-financials',target:'report-section-rent',hint:'אומדן השכירות שנשמר ומודעות ההשוואה שעליהן נשען המקור.'},
    {id:'rehab',number:'07',title:'מפרט ותקציב שיפוץ',tab:'tab-rehab',target:'report-section-rehab',hint:'מצב הסעיפים, היקף העבודה, תקציב וממצאים. הסכומים הידניים נשארים נפרדים מהמקורות.'},
    {id:'scenario',number:'08',title:'תחשיב העסקה והנחות העבודה',tab:'tab-financials',target:'deal-analysis-root',hint:'בדיקת מחיר הצעה, פליפ, השכרה ומימון. כל הנחה שהוזנה מסומנת בתרחיש.'},
    {id:'history',number:'09',title:'היסטוריית מחירי פרסום',tab:'tab-overview',target:'report-section-history',hint:'המחירים כפי שנצפו לאורך הסריקות; אלו אינם בהכרח מחירי עסקאות סגורות.'},
    {id:'sources',number:'10',title:'מקורות, מועדים והשלמת מידע',tab:'tab-financials',target:'report-section-sources',hint:'מה התקבל, מה חסר, המקור לכל נתון ואפשרויות עדכון או הזנת מסמך.'}
  ].map(Object.freeze));
  const instances=new WeakMap();
  const byId=id=>SECTIONS.find(section=>section.id===id)||null;
  const money=value=>'$'+value.toLocaleString('en-US',{maximumFractionDigits:2});
  function element(doc,tag,className,text){const el=doc.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
  function directHeading(target){return Array.from(target.children).find(el=>['H2','H3','H4'].includes(el.tagName)&&!el.classList.contains('rn-heading'))||null;}
  function heading(doc,target,section){
    let h=target.querySelector('[data-rn-heading="'+section.id+'"]');
    if(!h){
      const old=directHeading(target);if(old)old.classList.add('rn-original-heading');
      h=element(doc,'h2','rn-heading no-print');h.setAttribute('data-rn-heading',section.id);h.setAttribute('tabindex','-1');h.setAttribute('aria-label',section.number+' '+section.title);
      h.append(element(doc,'span','rn-number',section.number),element(doc,'span','',section.title));
      const hint=element(doc,'p','rn-hint no-print',section.hint);hint.setAttribute('data-rn-hint',section.id);
      target.insertBefore(h,target.firstChild);h.after(hint);
    }
    return h;
  }
  function moveAfter(node,reference){if(node&&reference)reference.after(node);}
  function organize(root){
    const doc=root.ownerDocument;
    const property=doc.getElementById('report-section-property'),photos=doc.getElementById('report-section-photos');
    const county=doc.getElementById('internal-data-card'),area=doc.getElementById('report-section-neighborhood');
    const history=doc.getElementById('report-section-history'),sources=doc.getElementById('report-section-sources');
    // Move the real nodes, retaining image handlers, form state and all existing IDs.
    moveAfter(photos,property);moveAfter(county,photos);moveAfter(area,county);moveAfter(history,area);
    const summary=doc.getElementById('rep-summary')?.parentElement;
    if(summary&&property&&!property.contains(summary))property.appendChild(summary);
    const savedSummary=doc.getElementById('investment-scenario-overview');
    if(savedSummary&&property&&!property.contains(savedSummary))property.appendChild(savedSummary);
    const value=doc.getElementById('report-section-value');
    if(property&&value){
      const oldGrid=property.querySelector('.report-value-grid');
      if(oldGrid){
        const cards=Array.from(oldGrid.children).slice(1);
        if(cards.length){let grid=value.querySelector('[data-rn-valuations]');if(!grid){grid=element(doc,'div','report-value-grid rn-valuations');grid.setAttribute('data-rn-valuations','');value.insertBefore(grid,value.firstChild);}
          cards.forEach(card=>grid.appendChild(card));oldGrid.classList.add('rn-asking-only');}
      }
    }
    const financials=doc.getElementById('tab-financials'),rent=doc.getElementById('report-section-rent'),scenario=doc.getElementById('deal-analysis-root');
    if(financials&&value&&rent&&scenario){financials.insertBefore(value,financials.firstChild);moveAfter(rent,value);moveAfter(scenario,rent);}
    const availability=doc.getElementById('report-data-title')?.parentElement;
    if(sources){
      if(availability&&!sources.contains(availability))sources.appendChild(availability);
      for(const id of ['evidence-editor','target-editor','source-settings']){const node=doc.getElementById(id);if(node&&!sources.contains(node))sources.appendChild(node);}
    }
    const caption=photos?.querySelector('.report-gallery-caption');if(caption)caption.classList.add('rn-original-heading');
    const oldScenarioHeading=scenario?.querySelector('.da-heading h2');if(oldScenarioHeading)oldScenarioHeading.classList.add('rn-original-heading');
    const rehab=doc.getElementById('report-section-rehab');
    if(rehab){const quote=doc.getElementById('contractor-quotes')?.parentElement,inspection=doc.getElementById('inspection-editor');
      if(quote&&!rehab.contains(quote))rehab.appendChild(quote);if(inspection&&!rehab.contains(inspection))rehab.appendChild(inspection);}
  }
  function navigate(root,id,config={}){
    const section=byId(id);if(!section)return false;
    const target=root.ownerDocument.getElementById(section.target);if(!target||!root.contains(target))return false;
    if(typeof config.onNavigate==='function')config.onNavigate(section.tab);
    if(typeof target.scrollIntoView==='function')target.scrollIntoView({block:'start'});
    const h=target.querySelector('[data-rn-heading="'+section.id+'"]');if(h&&typeof h.focus==='function')h.focus({preventScroll:true});
    if(typeof config.onLocation==='function')config.onLocation(section.target);
    return true;
  }
  function mount(root,config={}){
    if(!root)return null;
    const doc=root.ownerDocument,nav=doc.getElementById('report-sections-nav');if(!nav)return null;
    let state=instances.get(root);
    if(!state){
      nav.appendChild(element(doc,'h2','rn-nav-title','תוכן הדוח'));
      nav.appendChild(element(doc,'p','rn-nav-intro','אפשר לעבור לכל סעיף בלחיצה. המספרים קבועים כדי שנוכל לדייק כל חלק בנפרד.'));
      const links=element(doc,'div','rn-nav-links');
      for(const section of SECTIONS){const a=element(doc,'a','rn-nav-link');a.href='#'+section.target;a.setAttribute('data-rn-link',section.id);
        a.append(element(doc,'span','rn-nav-number',section.number),element(doc,'span','',section.title));links.appendChild(a);}
      nav.appendChild(links);
      state={config,hashHandled:false};
      nav.addEventListener('click',event=>{const a=event.target.closest('[data-rn-link]');if(!a||!nav.contains(a))return;
        // Keep ordinary modified clicks available for opening a numbered section in a new tab.
        if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button>0)return;
        event.preventDefault();navigate(root,a.getAttribute('data-rn-link'),state.config);});
      instances.set(root,state);
    }
    state.config=config;organize(root);
    for(const section of SECTIONS){const target=doc.getElementById(section.target);if(!target||!root.contains(target))continue;
      target.setAttribute('data-report-section',section.id);target.setAttribute('data-report-number',section.number);target.classList.add('rn-section');heading(doc,target,section);}
    const rentTarget=doc.getElementById('report-section-rent');
    if(rentTarget){let info=rentTarget.querySelector('[data-rn-rent]');if(!info){info=element(doc,'div','rn-rent no-print');info.setAttribute('data-rn-rent','');const hint=rentTarget.querySelector('[data-rn-hint="rent"]');if(hint)hint.after(info);else rentTarget.insertBefore(info,rentTarget.firstChild);}
      const value=config.finance?.rent,source=config.finance?.evidence?.rent;
      info.replaceChildren(element(doc,'span','','אומדן / נתון שכירות חודשי שנשמר'),element(doc,'strong','',typeof value==='number'&&Number.isFinite(value)?money(value):'טרם התקבל נתון מתאים'));
      if(source)info.appendChild(element(doc,'small','',source.source_name+' · '+String(source.observed_at||'').slice(0,10)));
    }
    if(!state.hashHandled){state.hashHandled=true;const hash=String(config.hash||'').replace(/^#/,'');const requested=SECTIONS.find(section=>section.target===hash);if(requested)navigate(root,requested.id,{...config,onLocation:null});}
    return {navigate:id=>navigate(root,id,state.config)};
  }
  return {SECTIONS,byId,mount,navigate};
});
