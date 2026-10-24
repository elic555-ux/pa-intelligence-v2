/* Report-only adapters. Existing inventory, quota guards and evidence stay authoritative. */
'use strict';
let dealAnalysisController=null;
let reportListingMedia=null;

function renderDealAnalysis(){
    if(!currentDeal||!window.PADealAnalysis||(!reportReady&&!dealAnalysisController))return;
    if(currentSavedReportId&&!currentDeal.investment_scenario){document.getElementById('deal-analysis-root').innerHTML='<h2>מחשבון עסקה</h2><p>לא נשמר תרחיש במועד הדוח ההיסטורי הזה. לבדיקת הנחות פתח את דוח הנכס העדכני.</p>';dealAnalysisController=null;return;}
    document.getElementById('documented-calculator').hidden=true;
    const overview=document.getElementById('investment-scenario-overview');
    overview.hidden=!window.PADealAnalysis.matches(currentDeal.investment_scenario,currentDeal);
    if(!overview.hidden)document.getElementById('investment-scenario-summary').textContent=window.PADealAnalysis.summary(currentDeal,window.PADealAnalysis.calculate(currentDeal,currentDeal.investment_scenario,financialSnapshot(currentDeal,referenceTime())));
    const budget=window.PARehabReport&&currentDeal.rehab_plan?.property_id===String(currentDeal.id)?window.PARehabReport.summarize(currentDeal.rehab_plan):null;
    if(!overview.hidden&&Object.values(window.PADealAnalysis.normalize(currentDeal.investment_scenario,currentDeal).rehab_links||{}).some(link=>budget?.revision!==link.revision))document.getElementById('investment-scenario-summary').textContent+=' תקציב סעיף 07 השתנה מאז ההעתקה. יש לבדוק את הסכום המקושר בסעיף 08.';
    dealAnalysisController=window.PADealAnalysis.mount(document.getElementById('deal-analysis-root'),{
        property:currentDeal,scenario:currentDeal.investment_scenario,source:financialSnapshot(currentDeal,referenceTime()),
        readOnly:!reportReady||!!currentSavedReportId||!cloudAvailable,busy:reportSavePending,
        storage:window.localStorage,rehabBudget:budget?{...budget,dirty:!!rehabPlanController?.isDirty()}:null,
        onSave:async function(scenario){
            if(!window.PADealAnalysis.matches(scenario,currentDeal))throw new Error('התרחיש אינו מתאים לזהות הנכס');
            const next=copy(currentDeal);next.investment_scenario=copy(scenario);
            if(reportListingMedia)next.report_listing_media=copy(reportListingMedia);
            await commitCurrentDeal(next);
        }
    });
}
async function flushDealAnalysis(){
    if(typeof rehabPlanController!=='undefined'&&rehabPlanController?.isDirty())throw new Error('יש לשמור את טיוטת השיפוץ בסעיף 07 לפני שמירת הדוח.');
    if(dealAnalysisController&&!currentSavedReportId){
        await dealAnalysisController.save();
        if(dealAnalysisController.dirty)throw new Error('יש לשמור את טיוטת המחשבון לפני ייצוא או שמירת דוח.');
    }
}
function reportMediaSnapshot(){
    const api=window.SecondarySourceEvidence;if(!api||!currentDeal)return null;
    const media=currentSavedReportId?currentDeal.report_listing_media:reportListingMedia||currentDeal.report_listing_media;
    return media&&media.property_id===String(currentDeal.id)&&JSON.stringify(media.identity)===JSON.stringify(api.identity(currentDeal))?media:null;
}
function reportOriginalPhotos(){
    const api=window.SecondarySourceEvidence;if(!api||!currentDeal)return [];
    const media=reportMediaSnapshot(),p=currentDeal;
    const original=media?.details?api.originalPhotos({...p,listing_source_details:media.details,listing_photos:media.details.photos}):api.originalPhotos(p);
    const extra=api.fallbackPhotos(p,media?.receipt);
    const secondary=api.combinedPhotos(p,media?.additional||[]),seen=new Set();
    return [...original,...extra,...secondary].filter(photo=>!seen.has(photo.url)&&seen.add(photo.url));
}
async function loadReportListingMedia(){
    const api=window.SecondarySourceEvidence;if(!currentDeal||currentSavedReportId||!api)return;
    const p=copy(currentDeal);if(p._idAmbiguous||p.source_type!=='mls')return;
    const key=await api.key(p.id);
    const paths=['COMPS_REPORTS/listing_details/'+key+'.json','COMPS_REPORTS/listing_photos/'+key+'.json',
        ...['howardhanna','eriemoves','tarasa','clearchoice'].map(provider=>'COMPS_REPORTS/additional_sources/'+provider+'/'+key+'.json')];
    const results=await Promise.allSettled(paths.map(getRepoJson));
    if(!currentDeal||String(currentDeal.id)!==String(p.id)||JSON.stringify(api.identity(currentDeal))!==JSON.stringify(api.identity(p)))return;
    const value=i=>results[i].status==='fulfilled'?results[i].value:null;
    reportListingMedia={property_id:String(p.id),identity:api.identity(p),details:value(0),receipt:value(1),additional:results.slice(2).filter(r=>r.status==='fulfilled').map(r=>r.value).filter(Boolean)};
    renderImages();
    if(typeof renderRehabPlan==='function')renderRehabPlan();
}
function renderReportPriceHistory(){
    const root=document.getElementById('report-price-history');if(!root||!currentDeal)return;
    const rows=(Array.isArray(currentDeal.price_history)?currentDeal.price_history:[]).filter(row=>positiveFinancialNumber(row.price)!==null&&financialDate(row.date)!==null&&financialDate(row.date)<=referenceTime()).sort((a,b)=>financialDate(b.date)-financialDate(a.date));
    root.innerHTML=rows.length?'<table><thead><tr><th>מועד תצפית</th><th>מחיר בפרסום</th><th>מקור שנשמר</th></tr></thead><tbody>'+rows.slice(0,20).map(row=>'<tr><td>'+escapeHtml(row.date.slice(0,10))+'</td><td>'+financialMoney(positiveFinancialNumber(row.price))+'</td><td>'+escapeHtml(row.source||'לא נשמר')+'</td></tr>').join('')+'</tbody></table><p class="small muted">היסטוריית מחירי פרסום כפי שנצפתה בסריקות. שינוי במחיר אינו מוכיח מוטיבציה של המוכר או ביטול חוזה.</p>':'<p>לא נשמרה עדיין היסטוריית מחירים לנכס.</p>';
}
