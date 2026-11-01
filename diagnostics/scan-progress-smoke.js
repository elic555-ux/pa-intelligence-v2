(function(){
 'use strict';
 const result=document.getElementById('result');let ticks=0,finished=0;
 const fail=error=>{document.body.dataset.result='failed';result.textContent='הבדיקה נכשלה: '+error.message;};
 try{
  PAScanProgress.create({
   ...PAScanProgress.browserTimers(window),requestId:'browser-smoke',requestedAt:Date.now(),
   loadReport:()=>new Promise(()=>{}),
   loadRun:()=>new Promise(resolve=>window.setTimeout(()=>resolve({status:'completed',conclusion:'success'}),1200)),
   onTick:()=>ticks++,onTimeout:()=>fail(new Error('timeout')),onError:fail,
   onFinish(){
    finished++;const stoppedAt=ticks;
    window.setTimeout(()=>{
     if(finished!==1||ticks!==stoppedAt){fail(new Error('timer did not stop'));return;}
     document.body.dataset.result='passed';
     result.textContent='עבר: הטיימר התחיל ונעצר פעם אחת עם סיום הריצה, גם כשמקור העדכון השני אינו מגיב. אין שגיאת Illegal invocation.';
    },1200);
   }
  });
 }catch(error){fail(error);}
})();
