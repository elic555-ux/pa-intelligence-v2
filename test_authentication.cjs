/* Offline checks of the actual login gate; no credentials or cloud writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
function extract(name) {
    let start = html.indexOf('function ' + name + '(');
    if (html.slice(start - 6, start) === 'async ') start -= 6;
    const end = html.indexOf('\n        }', start);
    assert.ok(start >= 0 && end > start, name);
    return html.slice(start, end + '\n        }'.length);
}
function setup() {
    const elements = new Map();
    const el = id => {
        if (!elements.has(id)) elements.set(id, {hidden: true, value: '', textContent: '', disabled: false});
        return elements.get(id);
    };
    const timers = [];
    let initCalls = 0, signInCalls = 0, callback;
    const auth = {
        getUser: async () => ({data: {user: {id: 'owner'}}}),
        signInWithPassword: async () => {signInCalls++; return {error: null};},
        onAuthStateChange: fn => {callback = fn;}
    };
    const ctx = vm.createContext({document: {getElementById: el}, supabaseClient: {auth},
        PA_OWNER_ID: 'owner', paAuthVerified: false, paAuthGeneration: 0, paInitPromise: null,
        init: async () => {initCalls++;}, setTimeout: fn => timers.push(fn),
        window: {location: {reload() {}}}, localStorage: {removeItem() {}}
    });
    vm.runInContext(['showPaAuthPending','lockPaApp','verifyPaAccess','loginPaApp','bootstrapPaAuthentication'].map(extract).join('\n'), ctx);
    return {ctx, auth, el, timers, get initCalls() {return initCalls;},
        get signInCalls() {return signInCalls;}, get callback() {return callback;}};
}
(async () => {
    let t = setup();
    await t.ctx.verifyPaAccess();
    assert.equal(t.el('pa-app-shell').hidden, false);
    assert.equal(t.el('pa-login-screen').hidden, true);
    assert.equal(t.initCalls, 1);
    await t.ctx.verifyPaAccess();
    assert.equal(t.initCalls, 1);
    t = setup();
    t.auth.getUser = async () => ({data: {user: {id: 'other'}}});
    await t.ctx.verifyPaAccess();
    assert.equal(t.el('pa-app-shell').hidden, true);
    assert.equal(t.initCalls, 0);
    t = setup();
    t.auth.getUser = async () => ({data: {user: null}});
    await t.ctx.verifyPaAccess();
    assert.equal(t.initCalls, 0);
    t = setup();
    t.auth.signInWithPassword = async () => ({error: {message: 'invalid credentials'}});
    t.el('pa-login-password').value = 'test-only';
    await t.ctx.loginPaApp({preventDefault() {}});
    assert.equal(t.el('pa-login-password').value, '');
    assert.equal(t.el('pa-login-submit').disabled, false);
    assert.equal(t.el('pa-app-shell').hidden, true);
    assert.equal(t.initCalls, 0);
    t = setup();
    t.el('pa-login-submit').disabled = true;
    await t.ctx.loginPaApp({preventDefault() {}});
    assert.equal(t.signInCalls, 0);
    t = setup();
    await t.ctx.bootstrapPaAuthentication();
    const generation = t.ctx.paAuthGeneration;
    t.callback('TOKEN_REFRESHED', {user: {id: 'owner'}});
    assert.equal(t.ctx.paAuthGeneration, generation);
    assert.equal(t.timers.length, 1);
    await t.timers[0]();
    assert.equal(t.ctx.paAuthGeneration, generation + 1);
    t = setup();
    t.auth.getUser = async () => ({data: {user: {id: 'owner', email: 'owner@example.test'}}});
    await t.ctx.verifyPaAccess();
    assert.equal(t.el('pa-account-email').textContent, 'owner@example.test');
    assert.equal(t.el('pa-account-status').hidden, false);
    t.auth.getUser = async () => ({data: {user: {id: 'other', email: 'other@example.test'}}});
    await t.ctx.verifyPaAccess();
    assert.equal(t.el('pa-account-email').textContent, '');
    assert.equal(t.el('pa-account-status').hidden, true);
    t = setup();
    t.auth.getUser = async () => ({data: {user: {id: 'owner', email: '<b>owner</b>@example.test'}}});
    await t.ctx.verifyPaAccess();
    assert.equal(t.el('pa-account-email').textContent, '<b>owner</b>@example.test');
    t.ctx.lockPaApp();
    assert.equal(t.el('pa-account-email').textContent, '');
    assert.equal(t.el('pa-account-status').hidden, true);
    assert.match(html, /<form id="pa-login-form"[^>]*\shidden[\s>]/);
    t = setup();
    let resolveUser, resolveInit;
    t.auth.getUser = () => new Promise(resolve => {resolveUser = resolve;});
    t.ctx.init = () => new Promise(resolve => {resolveInit = resolve;});
    const boot = t.ctx.bootstrapPaAuthentication();
    assert.equal(t.el('pa-login-form').hidden, true);
    assert.equal(t.el('pa-app-shell').hidden, true);
    assert.equal(t.el('pa-auth-heading').textContent, 'טוען את המערכת');
    resolveUser({data: {user: {id: 'owner'}}});
    await new Promise(setImmediate);
    assert.equal(typeof resolveInit, 'function');
    assert.equal(t.el('pa-login-form').hidden, true);
    assert.equal(t.el('pa-app-shell').hidden, true);
    resolveInit();
    await boot;
    assert.equal(t.el('pa-app-shell').hidden, false);
    assert.equal(t.el('pa-login-screen').hidden, true);
    assert.equal(t.signInCalls, 0);
    t = setup();
    t.auth.getUser = async () => ({data: {user: null}});
    await t.ctx.bootstrapPaAuthentication();
    assert.equal(t.el('pa-login-form').hidden, false);
    assert.equal(t.el('pa-auth-heading').textContent, 'כניסה למערכת');
    t = setup();
    t.auth.getUser = async () => {throw new Error('network unavailable');};
    await t.ctx.bootstrapPaAuthentication();
    assert.equal(t.el('pa-login-form').hidden, false);
    assert.equal(t.el('pa-app-shell').hidden, true);
    t = setup();
    t.ctx.init = async () => {throw new Error('init failed');};
    await t.ctx.bootstrapPaAuthentication();
    assert.equal(t.el('pa-login-form').hidden, false);
    assert.equal(t.el('pa-app-shell').hidden, true);
    t = setup();
    t.ctx.init = () => new Promise(resolve => {resolveInit = resolve;});
    const interrupted = t.ctx.bootstrapPaAuthentication();
    await new Promise(setImmediate);
    t.callback('SIGNED_OUT', null);
    resolveInit();
    await interrupted;
    assert.equal(t.el('pa-app-shell').hidden, true);
    assert.equal(t.el('pa-login-form').hidden, false);
    t = setup();
    await t.ctx.bootstrapPaAuthentication();
    t.callback('TOKEN_REFRESHED', {user: {id: 'owner'}});
    assert.equal(t.el('pa-app-shell').hidden, false);
    assert.equal(t.el('pa-login-screen').hidden, true);
    assert.equal(t.el('pa-login-form').hidden, true);
    await t.timers[0]();
    assert.equal(t.el('pa-app-shell').hidden, false);
    console.log('14 authentication, account display and session-loading checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
