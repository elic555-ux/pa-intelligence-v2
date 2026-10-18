'use strict';
// Offline only. Synthetic key and fake native frames; no keyed network calls.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const api = require('./street-view-preview.js');
const countyApi = require('./street-view.js');
const TEST_KEY = 'AIza' + 'x'.repeat(35);
const TARGET = {lat: 40.4597766, lng: -79.8577718, quality: 'county_address_point'};
const A = {query: '9242 Valley St, Pittsburgh, PA 15235, USA', streetViewUrl: 'street-view.html?' + new URLSearchParams({
    address: '9242 Valley St', city: 'Pittsburgh', zip: '15235', county: 'Allegheny', municipality: 'Penn Hills'})};
const B = {query: '233 Macfarlane Dr, Pittsburgh, PA 15235, USA', streetViewUrl: 'street-view.html?' + new URLSearchParams({
    address: '233 Macfarlane Dr', city: 'Pittsburgh', zip: '15235', county: 'Allegheny', municipality: 'Penn Hills'})};
const ENABLED = {enabled: true, provider: 'maps-embed', apiKey: TEST_KEY};
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}

function environment() {
    const nodes = new Map(), calls = [], frames = [], lookups = [], timers = new Map();
    const settings = {autoLookup: true};
    let nextTimer = 0;
    function node(id) {
        const attributes = {id};
        const value = {id, attributes, hidden: false, parentNode: null,
            removeAttribute(name) { delete attributes[name]; },
            getAttribute(name) { return attributes[name] ?? null; },
            cloneNode(deep) { assert.equal(deep, false); const clone = node(id); Object.assign(clone.attributes, attributes); return clone; },
            replaceChild(replacement, previous) {
                assert.equal(previous.parentNode, value);
                replacement.parentNode = value; previous.parentNode = null; nodes.set(replacement.id, replacement);
            },
            set innerHTML(value) { throw new Error('Preview must not set HTML'); },
        };
        Object.defineProperty(value, 'src', {get() { return attributes.src || ''; }, set(url) { attributes.src = url; frames.push({element: value, url}); }});
        Object.defineProperty(value, 'href', {get() { return attributes.href || ''; }, set(url) { attributes.href = url; }});
        return value;
    }
    for (const id of ['modal-street-view-preview', 'modal-street-view-preview-link', 'modal-street-view-preview-frame']) nodes.set(id, node(id));
    const container = nodes.get('modal-street-view-preview'); container.hidden = true;
    nodes.get('modal-street-view-preview-frame').parentNode = container;
    nodes.get('modal-street-view-preview-frame').attributes.class = 'block w-full min-h-200';
    nodes.get('modal-street-view-preview-link').parentNode = container;
    const document = {getElementById(id) { assert.ok(nodes.has(id), 'Unexpected DOM access: ' + id); return nodes.get(id); }};
    const window = {location: {href: 'https://example.test/project/index.html?ignored=1'}, document, AbortController,
        fetch(url, options) { const pending = deferred(); calls.push({url, options, ...pending}); return pending.promise; },
        setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, {fn, delay}); return id; },
        clearTimeout(id) { timers.delete(id); }};
    window.PAStreetView = {readRequest: countyApi.readRequest, supportsCounty: countyApi.supportsCounty,
        resolveOnce(request, env) {
            const lookup = {request, onResult: env.onResult, cancelled: false}; lookups.push(lookup);
            if (settings.autoLookup) env.onResult({status: 'matched', point: {...TARGET,
                quality: request.county === 'erie' ? 'county_parcel_centroid' : 'county_address_point'}});
            return {cancel() { lookup.cancelled = true; env.onResult({status: 'cancelled'}); }};
        }};
    return {window, document, nodes, calls, frames, lookups, timers, settings, preview: api.createPreview({window, document})};
}
async function config(env, value = ENABLED) {
    assert.equal(env.calls.length, 1);
    assert.equal(env.calls[0].url, 'https://example.test/project/street-view-preview-config.json');
    env.calls[0].resolve({ok: true, json: async () => value});
    await tick();
}
async function ready(env, destination = A) {
    const pending = env.preview.render(destination); await config(env); await pending;
    return env.nodes.get('modal-street-view-preview-frame');
}

test('module load exposes render/cancel without config, county or Google traffic', () => {
    const env = environment();
    vm.runInNewContext(fs.readFileSync(require.resolve('./street-view-preview.js'), 'utf8'), {window: env.window, URL, URLSearchParams});
    assert.equal(typeof env.window.PAStreetPreview.render, 'function');
    assert.equal(typeof env.window.PAStreetPreview.cancel, 'function');
    assert.equal(env.calls.length, 0); assert.equal(env.lookups.length, 0); assert.equal(env.frames.length, 0);
});

test('only explicit enabled maps-embed configuration with a valid browser key enables lookup', async () => {
    for (const value of [{enabled: false, provider: 'maps-embed', apiKey: TEST_KEY},
        {enabled: true, apiKey: TEST_KEY}, {enabled: true, provider: 'static', apiKey: TEST_KEY},
        {enabled: true, provider: 'maps-embed', apiKey: ''}, {enabled: true, provider: 'maps-embed', apiKey: 'not-configured'},
        {enabled: 'true', provider: 'maps-embed', apiKey: TEST_KEY}, {}, null]) {
        const env = environment(); const pending = env.preview.render(A);
        await config(env, value); await pending;
        assert.equal(env.calls.length, 1); assert.equal(env.lookups.length, 0); assert.equal(env.frames.length, 0);
        assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
        assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
    }
});

test('optional config is same-origin, credential-free, no-store and read once', async () => {
    const env = environment(); const pending = env.preview.render(A);
    await config(env, {enabled: false, provider: 'maps-embed', apiKey: '', fallbackProvider: 'anything'}); await pending;
    await env.preview.render(B);
    assert.equal(env.calls.length, 1);
    assert.equal(env.calls[0].options.mode, 'same-origin'); assert.equal(env.calls[0].options.credentials, 'omit');
    assert.equal(env.calls[0].options.cache, 'no-store'); assert.equal(env.timers.size, 0);
});

test('config HTTP, network and malformed JSON failures never load county data or Google', async () => {
    for (const mode of ['http', 'network', 'json']) {
        const env = environment(); const pending = env.preview.render(A);
        if (mode === 'network') env.calls[0].reject(new Error('not available'));
        else env.calls[0].resolve({ok: mode !== 'http', json: async () => { throw new Error('invalid config'); }});
        await pending; await env.preview.render(B);
        assert.equal(env.calls.length, 1); assert.equal(env.lookups.length, 0); assert.equal(env.frames.length, 0);
        assert.equal(env.timers.size, 0);
    }
});

test('config timeout and modal close prevent late enabled config from creating any provider request', async () => {
    for (const mode of ['timeout', 'close']) {
        const env = environment(); const pending = env.preview.render(A);
        if (mode === 'close') env.preview.cancel();
        else { const timer = [...env.timers.values()][0]; assert.equal(timer.delay, 12000); timer.fn();
            assert.equal(env.calls[0].options.signal.aborted, true); }
        await config(env); await pending;
        assert.equal(env.calls.length, 1); assert.equal(env.lookups.length, 0); assert.equal(env.frames.length, 0);
    }
});

test('invalid public address does not fetch optional config', async () => {
    const env = environment();
    for (const destination of [null, {}, {query: ''}, {query: 'Pittsburgh'}, {query: 'Unknown, Pittsburgh, PA 15235, USA'},
        {query: '40.45,-79.85, PA 15235, USA'}, {query: '12 Main St, London, UK'}]) await env.preview.render(destination);
    assert.equal(env.calls.length, 0); assert.equal(env.lookups.length, 0); assert.equal(env.frames.length, 0);
});

test('the resolver must be local, match the entire public address and select a supported county', async () => {
    for (const destination of [{...A, streetViewUrl: 'https://evil.test/street-view.html'},
        {...A, streetViewUrl: 'other.html?' + A.streetViewUrl.split('?')[1]}, {...A, streetViewUrl: B.streetViewUrl},
        {...A, streetViewUrl: A.streetViewUrl.replace('county=Allegheny', 'county=UnknownCounty')}, {query: A.query}]) {
        const env = environment(); const pending = env.preview.render(destination); await config(env); await pending;
        assert.equal(env.lookups.length, 0); assert.equal(env.frames.length, 0); assert.equal(env.calls.length, 1);
    }
});

test('only verified county points of the expected quality and finite PA coordinates may create an iframe', async () => {
    for (const point of [null, {...TARGET, quality: 'unverified_cache'}, {lat: 0, lng: 0, quality: 'county_address_point'},
        {lat: '40.4', lng: -79.8, quality: 'county_address_point'}]) {
        const env = environment(); env.settings.autoLookup = false;
        const pending = env.preview.render(A); await config(env);
        env.lookups[0].onResult({status: point ? 'matched' : 'unavailable', point}); await pending;
        assert.equal(env.frames.length, 0); assert.equal(env.calls.length, 1);
    }
});

test('the only keyed Google URL is Maps Embed StreetView with fixed origin and verified coordinates', async () => {
    const env = environment(); const frame = await ready(env);
    assert.equal(env.frames.length, 1); assert.equal(env.calls.length, 1); assert.equal(env.lookups.length, 1);
    const url = new URL(frame.src);
    assert.equal(url.origin, 'https://www.google.com'); assert.equal(url.pathname, '/maps/embed/v1/streetview');
    assert.deepEqual([...url.searchParams.keys()].sort(), ['key', 'location']);
    assert.equal(url.searchParams.get('location'), '40.4597766,-79.8577718');
    assert.equal(url.searchParams.has('heading'), false); assert.equal(url.searchParams.has('pano'), false);
    assert.equal(url.hash, '');
    assert.equal(frame.referrerPolicy, 'strict-origin-when-cross-origin'); assert.equal(frame.loading, 'eager');
    assert.equal(frame.allowFullscreen, true); assert.equal(frame.getAttribute('class'), 'block w-full min-h-200');
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
    assert.ok(env.calls.every(call => new URL(call.url).origin === 'https://example.test'), 'no Google fetch endpoint');
});

test('load reveals native interactive iframe and a separate keyless map link using the same county point', async () => {
    const env = environment(); const frame = await ready(env); frame.onload();
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, false);
    const link = new URL(env.nodes.get('modal-street-view-preview-link').href);
    assert.equal(link.origin, 'https://www.google.com'); assert.equal(link.pathname, '/maps/@');
    assert.deepEqual([...link.searchParams.keys()].sort(), ['api', 'map_action', 'viewpoint']);
    assert.equal(link.searchParams.get('map_action'), 'pano'); assert.equal(link.searchParams.get('viewpoint'), '40.4597766,-79.8577718');
    assert.ok(!link.href.includes(TEST_KEY)); assert.ok(!frame.title.includes(TEST_KEY));
    assert.equal(env.timers.size, 0);
});

test('iframe load failure removes its source, hides preview and leaves no stale click URL', async () => {
    const env = environment(); const frame = await ready(env); frame.onerror();
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    assert.equal(frame.src, ''); assert.equal(frame.onload, null); assert.equal(frame.onerror, null);
    assert.equal(env.nodes.get('modal-street-view-preview-link').href, ''); assert.equal(env.timers.size, 0);
});

test('iframe timeout is bounded and a captured late load cannot revive a timed-out frame', async () => {
    const env = environment(); const frame = await ready(env); const lateLoad = frame.onload;
    const timer = [...env.timers.values()][0]; assert.equal(timer.delay, 12000); timer.fn(); lateLoad();
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    assert.equal(frame.src, ''); assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
    assert.equal(env.timers.size, 0);
});

test('close removes iframe source and late load/error callbacks cannot reopen it', async () => {
    const env = environment(); const frame = await ready(env); const load = frame.onload, error = frame.onerror;
    frame.onload(); env.preview.cancel(); load(); error();
    assert.equal(frame.src, ''); assert.equal(frame.onload, null); assert.equal(frame.onerror, null);
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true); assert.equal(env.timers.size, 0);
});

test('switching A to B replaces the iframe and stale A events cannot reveal or clear B', async () => {
    const env = environment(); const frameA = await ready(env); const oldLoad = frameA.onload, oldError = frameA.onerror;
    await env.preview.render(B); const frameB = env.nodes.get('modal-street-view-preview-frame');
    assert.notEqual(frameA, frameB); assert.equal(frameA.src, '');
    oldLoad(); oldError(); assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    frameB.onload(); oldError(); assert.equal(env.nodes.get('modal-street-view-preview').hidden, false);
    assert.equal(env.calls.length, 1); assert.equal(env.lookups.length, 2); assert.equal(env.frames.length, 2);
});

test('same property renders do not duplicate iframe loads, but changed county context clears old content', async () => {
    const env = environment(); const frame = await ready(env); frame.onload(); await env.preview.render({...A});
    assert.equal(env.frames.length, 1); assert.equal(env.lookups.length, 1);
    await env.preview.render({...A, streetViewUrl: A.streetViewUrl.replace('county=Allegheny', 'county=UnknownCounty')});
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true); assert.equal(frame.src, '');
    assert.equal(env.frames.length, 1);
});

test('closing or switching during GIS lookup cancels it and ignores late results before any iframe is loaded', async () => {
    const env = environment(); env.settings.autoLookup = false;
    const first = env.preview.render(A); await config(env); const lookupA = env.lookups[0];
    const second = env.preview.render(B); await tick();
    assert.equal(lookupA.cancelled, true); lookupA.onResult({status: 'matched', point: TARGET}); await first;
    env.preview.cancel(); assert.equal(env.lookups[1].cancelled, true);
    env.lookups[1].onResult({status: 'matched', point: TARGET}); await second;
    assert.equal(env.frames.length, 0); assert.equal(env.calls.length, 1);
});

test('Erie uses its explicitly scoped parcel-centroid quality and wrong quality cannot enable the frame', async () => {
    const destination = {query: '1915 Liberty St, Erie, PA 16502, USA', streetViewUrl: 'street-view.html?' + new URLSearchParams({
        address: '1915 Liberty St', city: 'Erie', zip: '16502', county: 'Erie', municipality: 'City of Erie'})};
    const env = environment(); env.settings.autoLookup = false;
    const pending = env.preview.render(destination); await config(env);
    assert.equal(env.lookups[0].request.county, 'erie');
    env.lookups[0].onResult({status: 'matched', point: {...TARGET, quality: 'county_address_point'}}); await pending;
    assert.equal(env.frames.length, 0);
    env.preview.cancel(); const next = env.preview.render(destination); await tick();
    env.lookups[1].onResult({status: 'matched', point: {lat: 42.111093, lng: -80.093731, quality: 'county_parcel_centroid'}}); await next;
    assert.equal(new URL(env.nodes.get('modal-street-view-preview-frame').src).searchParams.get('location'), '42.1110930,-80.0937310');
});

test('parameter values cannot inject endpoints or headings and invalid coordinates create no URL', () => {
    const url = new URL(api.embedUrl(TARGET, 'synthetic&location=0,0#fragment'));
    assert.equal(url.origin, 'https://www.google.com'); assert.equal(url.pathname, '/maps/embed/v1/streetview');
    assert.equal(url.searchParams.get('location'), '40.4597766,-79.8577718'); assert.equal(url.hash, '');
    assert.deepEqual([...url.searchParams.keys()].sort(), ['key', 'location']);
    for (const point of [null, {lat: 0, lng: 0}, {lat: NaN, lng: -79}, {lat: 40, lng: Infinity}]) {
        assert.equal(api.embedUrl(point, TEST_KEY), null); assert.equal(api.panoramaUrl(point), null);
    }
});

test('charged API paths and image/metadata fallbacks are absent, with no storage, rehosting or secret logging', () => {
    const source = fs.readFileSync(require.resolve('./street-view-preview.js'), 'utf8');
    assert.ok(!/maps\.googleapis\.com|maps\/api\/streetview|maps\/api\/js|metadataUrl|imageUrl|bearingToTarget|pano_id/.test(source));
    assert.ok(!/localStorage|sessionStorage|indexedDB|createObjectURL|\.blob\(|console\./.test(source));
    assert.ok(!/getElementById\('modal-street-view-preview-image'\)/.test(source));
    assert.equal(api.imageUrl, undefined); assert.equal(api.metadataUrl, undefined); assert.equal(api.bearingToTarget, undefined);
});

test('explicit browser-local provider avoids public config fetch and refresh removes frame after deletion', async () => {
    const env = environment();
    let local = ENABLED;
    env.window.PAStreetLocalSettings = {read: () => local};
    await env.preview.render(A);
    assert.equal(env.calls.length, 0);
    const frame = env.nodes.get('modal-street-view-preview-frame');
    assert.equal(new URL(frame.src).searchParams.get('key'), TEST_KEY);
    local = null;
    await env.preview.refresh();
    assert.equal(frame.src, '');
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    assert.equal(env.calls.length, 0);
});

test('closing property prevents local settings refresh from reopening an old property', async () => {
    const env = environment();
    env.window.PAStreetLocalSettings = {read: () => ENABLED};
    await env.preview.render(A);
    env.preview.cancel();
    const count = env.frames.length;
    await env.preview.refresh();
    assert.equal(env.frames.length, count);
});

