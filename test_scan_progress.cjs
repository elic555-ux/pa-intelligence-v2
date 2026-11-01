'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const P=require('./scan-progress.js');
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function fixture(overrides={}){
 let time=0,id=0;const intervals=new Map(),timeouts=new Map(),ticks=[],finished=[],errors=[];
 const o={requestId:'mine',baselineScanId:'before',requestedAt:0,now:()=>time,
  setInterval:fn=>{intervals.set(++id,fn);return id;},clearInterval:n=>intervals.delete(n),
  setTimeout:fn=>{timeouts.set(++id,fn);return id;},clearTimeout:n=>timeouts.delete(n),
  onTick:n=>ticks.push(n),onFinish:x=>finished.push(x),onTimeout:()=>errors.push('timeout'),onError:()=>errors.push('read'),
  loadReport:async()=>null,loadRun:async()=>null,...overrides};
 const monitor=P.create(o);
 return {monitor,intervals,timeouts,ticks,finished,errors,advance(ms){time=ms;for(const fn of [...intervals.values()])fn();}};
}
(async()=>{
 let report=null,reads=0;
 const a=fixture({loadReport:async()=>{reads++;return report;}});await flush();
 report={last_scan:{request_id:'mine',status:'partial'}};
 a.advance(21037);await flush(); // No tick at 20, 40, 60 seconds: background tab throttling.
 assert.equal(reads,2);assert.equal(a.finished.length,1);assert.equal(a.intervals.size,0);
 a.advance(61037);assert.equal(a.ticks.at(-1),21);
 const never=deferred(),b=fixture({loadReport:()=>never.promise,loadRun:async()=>({status:'completed',conclusion:'success'})});await flush();
 assert.equal(b.finished.length,1);assert.equal(b.intervals.size,0);assert.equal(b.timeouts.size,0);
 never.resolve({last_event:{request_id:'mine',status:'failed'}});await flush();assert.equal(b.finished.length,1);
 const pending=deferred(),c=fixture({loadReport:()=>pending.promise});c.monitor.cancel();
 pending.resolve({last_event:{request_id:'mine',status:'success'}});await flush();assert.equal(c.finished.length,0);
 for(const status of ['failed','skipped','partial','success']){
  const x=fixture({loadReport:async()=>({last_event:{request_id:'mine',status}})});await flush();assert.equal(x.finished[0].status,status);assert.equal(x.intervals.size,0);
 }
 const wrong=fixture({loadReport:async()=>({last_scan:{request_id:'someone-else',status:'success'}})});await flush();assert.equal(wrong.finished.length,0);wrong.monitor.cancel();
 const legacy={scan_id:'new',status:'success',started_at:'1970-01-01T00:00:01Z'};
 assert.equal(P.reportResult({last_scan:{...legacy,trigger:'scheduled'}},'mine','before',0),null);
 assert.ok(P.reportResult({last_scan:{...legacy,trigger:'manual'}},'mine','before',0));
 const run={display_title:'סריקה יזומה mine',path:'.github/workflows/cron-daily-mls.yml',event:'workflow_dispatch',head_branch:'main',status:'completed',conclusion:'success'};
 assert.equal(P.matchingRun({workflow_runs:[run]},'mine'),run);
 for(const change of [{display_title:'סריקה יזומה other'},{path:'.github/workflows/other.yml'},{event:'push'},{head_branch:'other'}])assert.equal(P.matchingRun({...run,...change},'mine'),null);
 const slow=fixture({loadReport:()=>new Promise(()=>{}),loadRun:()=>new Promise(()=>{})});
 for(const fn of [...slow.timeouts.values()])fn();await flush();slow.advance(21000);assert.equal(slow.intervals.size,1);slow.advance(600001);assert.deepEqual(slow.errors,['timeout']);assert.equal(slow.intervals.size,0);
 const refreshing=deferred(),frozen=fixture({loadReport:async()=>({last_event:{request_id:'mine',status:'success'}}),onFinish:()=>refreshing.promise});await flush();assert.equal(frozen.intervals.size,0);frozen.advance(100000);assert.equal(frozen.ticks.at(-1),0);refreshing.resolve();
 // Exercise the real page controller so a completed older scan cannot close a new toast.
 const html=fs.readFileSync(__dirname+'/index.html','utf8');
 function extract(name){const start=html.indexOf('function '+name+'('),end=html.indexOf('\n        }',start);assert.ok(start>=0&&end>start);return html.slice(start,end+10);}
 const opts=[],elements=new Map(),gate=deferred();let reloads=0;
 const el=id=>{if(!elements.has(id))elements.set(id,{textContent:'',classList:{add(){},remove(){}}});return elements.get(id);};
 const browser={};let nativeCalls=0;
 for(const name of ['setInterval','clearInterval','setTimeout','clearTimeout'])browser[name]=function(){assert.equal(this,browser,'native timer must keep its Window receiver');nativeCalls++;return 123;};
 const ctx=vm.createContext({window:browser,Date,document:{getElementById:el},PAScanProgress:{browserTimers:P.browserTimers,create:o=>{for(const name of ['setInterval','clearInterval','setTimeout','clearTimeout'])o[name](()=>{},1);opts.push(o);return {cancel(){}};}},activeScanMonitor:null,scanMonitorGeneration:0,scannerReport:null,lastKnownScanId:null,scanStartTime:null,setInterval:browser.setInterval,clearInterval:browser.clearInterval,setTimeout:browser.setTimeout,clearTimeout:browser.clearTimeout,loadScannerStatus:async()=>null,loadProperties:async()=>{reloads++;await gate.promise;},loadGeoCatalog:async()=>{},localStorage:{getItem:()=>null}});
 vm.runInContext(extract('closeScanToast')+'\n'+extract('startBackgroundScanMonitor'),ctx);
 ctx.startBackgroundScanMonitor('first');const old=opts[0].onFinish({status:'success'});await flush();ctx.startBackgroundScanMonitor('second');const generation=ctx.scanMonitorGeneration;gate.resolve();await old;
 assert.equal(ctx.scanMonitorGeneration,generation);assert.equal(reloads,1);
 assert.equal(nativeCalls,8);
 assert.ok(html.indexOf('src="scan-progress.js')<html.indexOf('function startBackgroundScanMonitor'));
 console.log('PASS scan timer: delayed ticks, terminal states, independent run status, cancellation, hung sources, frozen refresh and old/new scan race');
})().catch(e=>{console.error(e);process.exitCode=1;});
