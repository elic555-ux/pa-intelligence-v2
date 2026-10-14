/* Preview-only UX; changes to real services remain blocked by runtime and CSP. */
(function(root){
'use strict';
const passive=['triggerOnDemandExecution','saveScanSettingsToServer','saveManualScanSettings','saveScheduleSettings','saveGitHubConnection','requestRentCastReport'];
for(const name of passive)if(typeof root[name]==='function')root[name]=()=>root.PaPreview.message();
if(typeof root.logoutPaApp==='function')root.logoutPaApp=()=>root.PaPreview.reset();
const setup=()=>{
 const banner=document.createElement('aside');banner.id='pa-preview-banner';banner.dir='rtl';
 banner.style.cssText='position:fixed;top:0;left:0;right:0;z-index:100000;display:flex;justify-content:center;align-items:center;flex-wrap:wrap;gap:12px;padding:10px;background:#78350f;border-bottom:2px solid #fbbf24;color:#fff;font:700 15px sans-serif';
 const label=document.createElement('strong');label.textContent='סביבת בדיקה מעודכנת · נתוני דוגמה · עד 2 ניסיונות השלמה · שמירה מקומית · RentCast כבוי';
 banner.appendChild(label);
 const reset=document.createElement('button');reset.type='button';reset.textContent='נקה נתוני ניסוי';reset.style.cssText='border:1px solid #fde68a;border-radius:8px;padding:4px 10px;background:#451a03;color:#fff;cursor:pointer';reset.onclick=()=>root.PaPreview.reset();banner.appendChild(reset);
 const link=document.createElement('a');link.href='../';link.target='_blank';link.rel='noopener noreferrer';link.textContent='פתח מערכת פעילה';link.style.cssText='color:#fde68a;text-decoration:underline';banner.appendChild(link);
 document.body.prepend(banner);document.body.style.paddingTop='60px';
 const header=document.getElementById('app-header');if(header)header.style.top='60px';
 const logout=document.getElementById('pa-logout-button');if(logout)logout.textContent='נקה נתוני ניסוי';
 for(const id of ['ondemand-token-input','setting-token-input','github-token']){const n=document.getElementById(id);if(n){n.disabled=true;n.value='';n.placeholder='אין צורך במפתח בסביבת בדיקה';}}
 const mode=document.getElementById('rentcast-mode');if(mode){mode.value='profile';for(const option of mode.options)if(option.value!=='profile')option.disabled=true;}
 document.title='סביבת בדיקה | '+document.title;
};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup,{once:true});else setup();
})(window);
