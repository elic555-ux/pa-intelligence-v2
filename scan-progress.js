/* Manual scan progress. Read-only: no workflow dispatch or inventory writes. */
(function(root,factory){'use strict';if(typeof module==='object'&&module.exports)module.exports=factory();else root.PAScanProgress=factory();})(typeof globalThis==='undefined'?this:globalThis,function(){
    'use strict';
    const terminal=new Set(['success','partial','failed','skipped','cancelled']);
    function reportResult(report,requestId,baselineScanId,requestedAt){
        const scan=report?.last_scan,event=report?.last_event;
        const own=[event,scan].find(x=>x?.request_id===requestId&&terminal.has(x.status));
        if(own)return own;
        const time=Date.parse(scan?.started_at||scan?.finished_at);
        return scan&&scan.trigger==='manual'&&!scan.request_id&&scan.scan_id&&scan.scan_id!==baselineScanId&&time>=requestedAt-120000&&terminal.has(scan.status)?scan:null;
    }
    function matchingRun(payload,requestId){
        const rows=Array.isArray(payload?.workflow_runs)?payload.workflow_runs:[payload];
        return rows.find(r=>r?.display_title==='סריקה יזומה '+requestId&&r.path==='.github/workflows/cron-daily-mls.yml'&&
            r.event==='workflow_dispatch'&&r.head_branch==='main')||null;
    }
    function runResult(run){
        if(run?.status!=='completed')return null;
        return {status:run.conclusion==='success'?'success':run.conclusion==='skipped'?'skipped':run.conclusion==='cancelled'?'cancelled':'failed',kind:'run'};
    }
    function create(options){
        const now=options.now||Date.now,started=options.requestedAt??now();
        const intervalMs=options.pollMs??15000,timeoutMs=options.timeoutMs??600000,readMs=options.readMs??10000;
        let stopped=false,checking=false,nextPollAt=0,timer=null;
        const pending=new Set();
        function cancel(){
            if(stopped)return;
            stopped=true;
            if(timer!==null)options.clearInterval(timer);
            timer=null;
            for(const cleanup of [...pending])cleanup();
        }
        function finish(value){
            if(stopped)return;
            cancel(); // Stop elapsed time BEFORE refreshing data or awaiting UI work.
            Promise.resolve().then(()=>options.onFinish(value)).catch(error=>options.onError?.(error,true));
        }
        function probe(read,convert){
            return new Promise(resolve=>{
                let done=false;
                const close=()=>{if(done)return;done=true;options.clearTimeout(deadline);pending.delete(close);resolve();};
                const deadline=options.setTimeout(close,readMs);pending.add(close);
                Promise.resolve().then(read).then(value=>{
                    if(done||stopped)return;
                    const result=convert(value);if(result)finish(result);
                }).catch(error=>{if(!done&&!stopped)options.onError?.(error,false);}).finally(close);
            });
        }
        function tick(){
            if(stopped)return;
            const time=now(),elapsed=Math.max(0,Math.floor((time-started)/1000));
            options.onTick(elapsed);
            if(time-started>=timeoutMs){cancel();options.onTimeout();return;}
            if(checking||time<nextPollAt)return;
            checking=true;nextPollAt=time+intervalMs;
            // Each source can finish the monitor independently, even if the other hangs.
            Promise.all([
                probe(options.loadReport,r=>reportResult(r,options.requestId,options.baselineScanId,started)),
                probe(options.loadRun,runResult)
            ]).finally(()=>{checking=false;});
        }
        timer=options.setInterval(tick,1000);tick();
        return {cancel};
    }
    return {create,reportResult,matchingRun,runResult};
});
