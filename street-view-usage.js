(function(root,factory){
    'use strict';
    const api=factory();
    if(typeof module==='object'&&module.exports)module.exports=api;
    else root.PAStreetUsageFactory=api;
}(typeof window==='undefined'?globalThis:window,function(){
    'use strict';
    const LIMIT=9000,WARNING=8000,TIMEOUT=12000;
    function receipt(value,requestId){
        if(!value||typeof value!=='object'||Array.isArray(value)||value.schema_version!==1
            ||value.limit!==LIMIT||value.warning_at!==WARNING||value.time_zone!=='Asia/Jerusalem'
            ||!/^\d{4}-(0[1-9]|1[0-2])$/.test(value.period)||!Number.isSafeInteger(value.used)
            ||value.used<0||value.used>LIMIT||typeof value.allowed!=='boolean')throw new Error('invalid_receipt');
        if(requestId&&(value.request_id!==requestId||!['reserved','limit','duplicate'].includes(value.reason)
            ||(value.allowed&&(value.reason!=='reserved'||value.used<1))
            ||(!value.allowed&&value.reason==='reserved')||(value.reason==='limit'&&value.used!==LIMIT)))throw new Error('invalid_reservation');
        if(!requestId&&value.allowed!==(value.used<LIMIT))throw new Error('invalid_status');
        return {period:value.period,used:value.used,limit:LIMIT,warning_at:WARNING,allowed:value.allowed};
    }
    function createCounter(env){
        const win=env.window,doc=env.document;
        let latest=null,epoch=0;
        function message(text){const node=doc?.getElementById('street-view-usage-status');if(node)node.textContent=text;}
        function show(value){
            if(latest&&(value.period<latest.period||(value.period===latest.period&&value.used<latest.used)))return;
            latest=value;
            const state=value.used>=LIMIT?'טעינות חדשות נעצרו עד לחודש הבא.':value.used>=WARNING?'התקרבות לסף העצירה.':'';
            message('מונה מרכזי · '+value.period+' · '+value.used.toLocaleString('en-US')+' / 9,000 ניסיונות טעינה. '+state);
        }
        async function call(name,args){
            const client=env.getClient();
            if(!client||typeof client.rpc!=='function')throw new Error('unavailable');
            let timer,controller=typeof win.AbortController==='function'?new win.AbortController():null;
            try{
                const pending=client.rpc(name,args);
                const request=controller&&typeof pending?.abortSignal==='function'?pending.abortSignal(controller.signal):pending;
                const result=await Promise.race([Promise.resolve(request),new Promise((_,reject)=>{
                    timer=win.setTimeout(()=>{controller?.abort();reject(new Error('timeout'));},TIMEOUT);
                })]);
                if(!result||result.error)throw new Error('unavailable');
                return result.data;
            }finally{if(timer!==undefined)win.clearTimeout(timer);}
        }
        async function refreshStatus(){
            const token=epoch;
            try{const value=receipt(await call('street_view_usage_status',{}));if(token===epoch)show(value);return token===epoch?value:null;}
            catch(_){if(token===epoch)message('המונה המרכזי אינו זמין. תצוגות חדשות חסומות עד לחידוש החיבור.');return null;}
        }
        async function reserve(){
            const token=epoch;
            try{
                if(typeof win.crypto?.randomUUID!=='function')throw new Error('unavailable');
                const requestId=win.crypto.randomUUID();
                const value=receipt(await call('reserve_street_view_attempt',{p_request_id:requestId}),requestId);
                if(token!==epoch)return false;
                show(value);
                return value.allowed;
            }catch(_){if(token===epoch)message('המונה המרכזי אינו זמין. לא נשלחה בקשת תצוגת רחוב.');return false;}
        }
        function clear(){epoch++;latest=null;message('נדרשת התחברות כדי לקרוא את המונה המרכזי.');}
        const button=doc?.getElementById('street-view-usage-refresh');
        button?.addEventListener('click',()=>refreshStatus());
        return {reserve,refreshStatus,clear};
    }
    return {createCounter,receipt,LIMIT,WARNING,TIMEOUT};
}));
