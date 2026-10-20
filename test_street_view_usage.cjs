'use strict';
const assert=require('node:assert/strict'),test=require('node:test'),crypto=require('node:crypto');
const {createCounter,receipt}=require('./street-view-usage.js');
const valid=(used=0,more={})=>({schema_version:1,period:'2026-10',used,limit:9000,warning_at:8000,time_zone:'Asia/Jerusalem',allowed:used<9000,...more});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function harness(handler){
    const calls=[],timers=new Map(),status={textContent:''};let n=0;
    const win={crypto,AbortController,setTimeout(fn){timers.set(++n,fn);return n;},clearTimeout(id){timers.delete(id);}};
    const client={rpc(name,args){calls.push({name,args});return handler(name,args);}};
    const env={window:win,document:{getElementById(id){return id==='street-view-usage-status'?status:null;}},getClient:()=>client};
    return {counter:createCounter(env),calls,timers,status,env};
}
test('server contract rejects malformed counts, limits, months and unbound permits',()=>{
    for(const more of [{used:-1},{used:9001},{used:1.5},{used:'1'},{limit:10000},{period:'2026-13'},{allowed:1},{time_zone:'UTC'},{schema_version:2}])assert.throws(()=>receipt(valid(0,more)));
    assert.throws(()=>receipt(valid(1,{allowed:true,reason:'reserved',request_id:'other'}),'expected'));
    assert.throws(()=>receipt(valid(0,{allowed:true,reason:'reserved',request_id:'x'}),'x'));
    assert.throws(()=>receipt(valid(1,{allowed:false,reason:'limit',request_id:'x'}),'x'));
});
test('no browser-local counter and no client-selected month, limit, user or key',async()=>{
    const h=harness((name,args)=>Promise.resolve({data:valid(1,{request_id:args.p_request_id,reason:'reserved'})}));
    assert.equal(await h.counter.reserve(),true);
    assert.equal(h.calls[0].name,'reserve_street_view_attempt');
    assert.deepEqual(Object.keys(h.calls[0].args),['p_request_id']);
    assert.match(h.calls[0].args.p_request_id,/^[0-9a-f-]{36}$/);
    assert.equal(h.timers.size,0);
});
test('the last slot is allowed, subsequent requests are blocked and display stop',async()=>{
    let used=8999;
    const h=harness((_,args)=>{const allowed=used<9000;if(allowed)used++;return {data:valid(used,{allowed,reason:allowed?'reserved':'limit',request_id:args.p_request_id})};});
    assert.equal(await h.counter.reserve(),true);assert.equal(await h.counter.reserve(),false);
    assert.match(h.status.textContent,/9,000 \/ 9,000/);assert.match(h.status.textContent,/נעצרו/);
    assert.notEqual(h.calls[0].args.p_request_id,h.calls[1].args.p_request_id);
});
test('warning is visible at 8000 while another reservation remains allowed',async()=>{
    const h=harness((_,args)=>({data:valid(8000,{reason:'reserved',request_id:args.p_request_id})}));
    assert.equal(await h.counter.reserve(),true);assert.match(h.status.textContent,/התקרבות/);
});
test('missing client, secure random IDs, denied RPC and malformed receipts block',async()=>{
    for(const mode of ['client','random','rpc','receipt']){
        const h=harness(()=>mode==='rpc'?{error:{message:'secret must not reach UI'}}:{data:valid(-1)});
        if(mode==='client')h.env.getClient=()=>null;
        if(mode==='random')h.env.window.crypto={};
        assert.equal(await h.counter.reserve(),false);assert.match(h.status.textContent,/לא נשלחה/);
        assert.ok(!h.status.textContent.includes('secret'));assert.equal(h.timers.size,0);
    }
});
test('timeout blocks without retry; an eventual server success cannot grant a late frame',async()=>{
    const pending=deferred(),h=harness(()=>pending.promise);
    const permit=h.counter.reserve();[...h.timers.values()][0]();
    assert.equal(await permit,false);assert.equal(h.calls.length,1);
    pending.resolve({data:valid(1,{request_id:h.calls[0].args.p_request_id,reason:'reserved'})});
    await new Promise(r=>setImmediate(r));assert.match(h.status.textContent,/לא נשלחה/);
});
test('late status responses cannot reduce the displayed count or roll back month',async()=>{
    const pending=[],h=harness(()=>{const d=deferred();pending.push(d);return d.promise;});
    const old=h.counter.refreshStatus(),fresh=h.counter.refreshStatus();
    pending[1].resolve({data:valid(8001)});await fresh;pending[0].resolve({data:valid(2)});await old;
    assert.match(h.status.textContent,/8,001/);
    const next=h.counter.refreshStatus();pending[2].resolve({data:valid(0,{period:'2026-11'})});await next;
    assert.match(h.status.textContent,/2026-11/);
});
test('sign-out clears state and rejects a pending permit',async()=>{
    const pending=deferred(),h=harness(()=>pending.promise),permit=h.counter.reserve();
    h.counter.clear();pending.resolve({data:valid(1,{request_id:h.calls[0].args.p_request_id,reason:'reserved'})});
    assert.equal(await permit,false);assert.match(h.status.textContent,/נדרשת התחברות/);
});
test('status refresh does not consume a slot and a replayed permit never authorizes a frame',async()=>{
    const h=harness((name,args)=>({data:name==='street_view_usage_status'?valid(2):valid(2,{allowed:false,reason:'duplicate',request_id:args.p_request_id})}));
    assert.equal((await h.counter.refreshStatus()).used,2);assert.equal(h.calls[0].name,'street_view_usage_status');
    assert.equal(await h.counter.reserve(),false);
});
