'use strict';
// Offline mocks only: no Google metadata/image requests or real API keys.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const api = require('./street-view-preview.js');
const countyApi = require('./street-view.js');
const TEST_KEY = 'AIza' + 'x'.repeat(35); // Synthetic test value, never used for a network request.
const PANO = 'test_pano_same_camera_12345';
const LOCATION = {lat: 40.4597766, lng: -79.8579718};
const TARGET = {lat: 40.4597766, lng: -79.8577718, quality: 'county_address_point'};
const okMetadata = (pano = PANO) => ({status: 'OK', pano_id: pano, location: {...LOCATION}});
const A = {query: '9242 Valley St, Pittsburgh, PA 15235, USA', streetViewUrl: 'street-view.html?' + new URLSearchParams({
    address: '9242 Valley St', city: 'Pittsburgh', zip: '15235', county: 'Allegheny', municipality: 'Penn Hills'})};
const B = {query: '233 Macfarlane Dr, Pittsburgh, PA 15235, USA', streetViewUrl: 'street-view.html?' + new URLSearchParams({
    address: '233 Macfarlane Dr', city: 'Pittsburgh', zip: '15235', county: 'Allegheny', municipality: 'Penn Hills'})};
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}

function environment() {
    const nodes = new Map();
    const calls = [];
    const images = [];
    const lookups = [];
    const settings = {autoLookup: true};
    const timers = new Map();
    let nextTimer = 0;
    function node(id) {
        const attributes = {id};
        const value = {id, attributes, hidden: false, parentNode: null,
            removeAttribute(name) { delete attributes[name]; },
            getAttribute(name) { return attributes[name] ?? null; },
            cloneNode(deep) { assert.equal(deep, false); const clone = node(id); Object.assign(clone.attributes, attributes); return clone; },
            replaceChild(replacement, previous) {
                assert.equal(previous.parentNode, value);
                replacement.parentNode = value;
                previous.parentNode = null;
                nodes.set(replacement.id, replacement);
            },
            set innerHTML(value) { throw new Error('Preview must not set HTML'); },
        };
        Object.defineProperty(value, 'src', {get() { return attributes.src || ''; }, set(url) { attributes.src = url; images.push({element: value, url}); }});
        Object.defineProperty(value, 'href', {get() { return attributes.href || ''; }, set(url) { attributes.href = url; }});
        return value;
    }
    for (const id of ['modal-street-view-preview', 'modal-street-view-preview-link', 'modal-street-view-preview-image']) nodes.set(id, node(id));
    nodes.get('modal-street-view-preview').hidden = true;
    nodes.get('modal-street-view-preview-image').parentNode = nodes.get('modal-street-view-preview-link');
    const document = {getElementById(id) { assert.ok(nodes.has(id), 'Unexpected DOM access: ' + id); return nodes.get(id); }};
    const window = {location: {href: 'https://example.test/project/index.html?ignored=1'}, document, AbortController,
        fetch(url, options) { const pending = deferred(); calls.push({url, options, ...pending}); return pending.promise; },
        setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, {fn, delay}); return id; },
        clearTimeout(id) { timers.delete(id); }};
    window.PAStreetView = {readRequest: countyApi.readRequest, supportsCounty: countyApi.supportsCounty,
        resolveOnce(request, env) {
            const value = {request, onResult: env.onResult, cancelled: false};
            lookups.push(value);
            if (settings.autoLookup) env.onResult({status: 'matched', point: {...TARGET,
                quality: request.county === 'erie' ? 'county_parcel_centroid' : 'county_address_point'}});
            return {cancel() { value.cancelled = true; env.onResult({status: 'cancelled'}); }};
        }};
    return {window, document, nodes, calls, images, timers, lookups, settings, preview: api.createPreview({window, document})};
}

async function config(env, value = {enabled: true, apiKey: TEST_KEY}) {
    assert.equal(env.calls.length, 1);
    assert.equal(env.calls[0].url, 'https://example.test/project/street-view-preview-config.json');
    env.calls[0].resolve({ok: true, json: async () => value});
    await tick();
}
function metadataCall(env) { return env.calls.find(call => new URL(call.url).hostname === 'maps.googleapis.com'); }
async function metadata(env, value = okMetadata()) {
    const call = metadataCall(env);
    assert.ok(call);
    call.resolve({ok: true, json: async () => value});
    await tick();
}

test('browser module exposes render/cancel without config, metadata or image traffic on load', () => {
    const env = environment();
    vm.runInNewContext(fs.readFileSync(require.resolve('./street-view-preview.js'), 'utf8'),
        {window: env.window, URL, URLSearchParams});
    assert.equal(typeof env.window.PAStreetPreview.render, 'function');
    assert.equal(typeof env.window.PAStreetPreview.cancel, 'function');
    assert.equal(env.calls.length, 0);
    assert.equal(env.images.length, 0);
});

test('disabled, missing or invalid config performs zero Google calls and keeps preview hidden', async () => {
    for (const value of [{enabled: false, apiKey: TEST_KEY}, {enabled: true, apiKey: ''}, {}, null,
        {enabled: 'true', apiKey: TEST_KEY}, {enabled: true, apiKey: 'not-a-configured-key'}]) {
        const env = environment();
        const pending = env.preview.render(A);
        await config(env, value);
        await pending;
        assert.equal(env.calls.length, 1);
        assert.equal(env.lookups.length, 0, 'disabled configuration must not query county GIS');
        assert.equal(env.images.length, 0);
        assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
        assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
    }
});

test('config is same-origin, credential-free, fetched once and unknown fields do not enable anything', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    await config(env, {enabled: false, apiKey: '', extraEndpoint: 'https://evil.test', enabledPreview: true});
    await pending;
    await env.preview.render(B);
    assert.equal(env.calls.length, 1);
    assert.equal(env.calls[0].options.mode, 'same-origin');
    assert.equal(env.calls[0].options.credentials, 'omit');
    assert.equal(env.calls[0].options.cache, 'no-store');
    assert.equal(env.timers.size, 0);
});

test('config HTTP/JSON/network failures stay quiet, never load an image and are not retried automatically', async () => {
    for (const mode of ['http', 'json', 'network']) {
        const env = environment();
        const pending = env.preview.render(A);
        if (mode === 'network') env.calls[0].reject(new Error('unavailable'));
        else env.calls[0].resolve({ok: mode !== 'http', json: async () => { throw new Error('bad config'); }});
        await pending;
        await env.preview.render(B);
        assert.equal(env.calls.length, 1);
        assert.equal(env.images.length, 0);
        assert.equal(env.timers.size, 0);
    }
});

test('invalid destination does not even fetch optional config', async () => {
    const env = environment();
    for (const destination of [null, {}, {query: ''}, {query: 'Pittsburgh'}, {query: 'Unknown, Pittsburgh, PA 15235, USA'},
        {query: '40.45,-79.85, PA 15235, USA'}, {query: '12 Main St, London, UK'}]) await env.preview.render(destination);
    assert.equal(env.calls.length, 0);
    assert.equal(env.images.length, 0);
});

test('metadata uses the full address, fixed Google origin and referrer-compatible credential-free CORS', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    await config(env);
    const call = metadataCall(env);
    const url = new URL(call.url);
    assert.equal(url.origin, 'https://maps.googleapis.com');
    assert.equal(url.pathname, '/maps/api/streetview/metadata');
    assert.equal(url.searchParams.get('location'), A.query);
    assert.deepEqual([...url.searchParams.keys()].sort(), ['key', 'location']);
    assert.equal(call.options.credentials, 'omit');
    assert.equal(call.options.mode, 'cors');
    assert.equal(call.options.referrerPolicy, 'strict-origin-when-cross-origin');
    assert.ok(call.options.signal instanceof AbortSignal);
    await metadata(env);
    await pending;
});

test('native image and click URL bind the exact same pano and camera; key never appears in click URL', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    await config(env);
    await metadata(env);
    await pending;
    assert.equal(env.images.length, 1);
    const image = env.nodes.get('modal-street-view-preview-image');
    const imageUrl = new URL(image.src);
    assert.equal(imageUrl.origin, 'https://maps.googleapis.com');
    assert.equal(imageUrl.pathname, '/maps/api/streetview');
    assert.equal(imageUrl.searchParams.get('pano'), PANO);
    assert.equal(imageUrl.searchParams.get('size'), '640x360');
    assert.equal(imageUrl.searchParams.get('return_error_code'), 'true');
    assert.equal(image.referrerPolicy, 'strict-origin-when-cross-origin');
    assert.equal(image.loading, 'eager');
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true, 'Do not reveal an unloaded image');
    assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
    image.onload();
    const link = new URL(env.nodes.get('modal-street-view-preview-link').href);
    assert.equal(link.origin, 'https://www.google.com');
    assert.equal(link.pathname, '/maps/@');
    assert.equal(link.searchParams.get('api'), '1');
    assert.equal(link.searchParams.get('map_action'), 'pano');
    assert.equal(link.searchParams.get('pano'), PANO);
    for (const [key, value] of [['heading', '90'], ['pitch', '0'], ['fov', '90']]) {
        assert.equal(imageUrl.searchParams.get(key), value);
        assert.equal(link.searchParams.get(key), value);
    }
    assert.equal(link.searchParams.has('key'), false);
    assert.ok(!link.href.includes(TEST_KEY));
    assert.ok(!image.alt.includes(TEST_KEY));
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, false);
});

test('image failure hides preview and removes stale src/href while normal Street View fallback remains independent', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    await config(env); await metadata(env); await pending;
    const image = env.nodes.get('modal-street-view-preview-image');
    image.onload();
    image.onerror();
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    assert.equal(image.src, '');
    assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
});

test('missing, denied or malformed metadata never requests the potentially billable image', async () => {
    for (const value of [null, {}, {status: 'ZERO_RESULTS'}, {status: 'REQUEST_DENIED', error_message: TEST_KEY},
        {status: 'OK'}, {status: 'OK', pano_id: 'bad&id=other'}, {status: 'OK', pano_id: 'bad pano'}]) {
        const env = environment();
        const pending = env.preview.render(A);
        await config(env); await metadata(env, value); await pending;
        assert.equal(env.images.length, 0);
        assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
        assert.equal(env.timers.size, 0);
    }
});

test('address and panorama parameters cannot change fixed endpoints or inject other URL parameters', () => {
    const query = '12 "Oak & Elm" St &key=evil#fragment, Pittsburgh, PA 15235, USA';
    const url = new URL(api.metadataUrl(query, TEST_KEY));
    assert.equal(url.searchParams.get('location'), query);
    assert.equal([...url.searchParams.keys()].filter(key => key === 'key').length, 1);
    assert.equal(url.hash, '');
    assert.equal(api.panoId('pano&key=evil'), null);
    const pano = 'F:sample/path/with_opaque-ID';
    assert.equal(new URL(api.imageUrl(pano, TEST_KEY, '90')).searchParams.get('pano'), pano);
    assert.equal(new URL(api.panoramaUrl(pano, '90')).searchParams.get('pano'), pano);
});

test('duplicate renders of one property do not duplicate metadata or image calls', async () => {
    const env = environment();
    const first = env.preview.render(A);
    await env.preview.render({...A});
    await config(env);
    await env.preview.render(A);
    assert.equal(env.calls.length, 2);
    await metadata(env); await first;
    env.nodes.get('modal-street-view-preview-image').onload();
    await env.preview.render(A);
    assert.equal(env.calls.length, 2);
    assert.equal(env.images.length, 1);
});

test('close while config is loading prevents any subsequent Google request', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    env.preview.cancel();
    await config(env); await pending;
    assert.equal(env.calls.length, 1);
    assert.equal(env.images.length, 0);
});

test('A to B aborts A and ignores its late metadata without clearing B', async () => {
    const env = environment();
    const first = env.preview.render(A);
    await config(env);
    const callA = metadataCall(env);
    const second = env.preview.render(B);
    await tick();
    const callB = env.calls[2];
    assert.equal(callA.options.signal.aborted, true);
    callA.resolve({ok: true, json: async () => okMetadata('old_property_pano_12345')});
    await first;
    assert.equal(env.images.length, 0);
    callB.resolve({ok: true, json: async () => okMetadata()});
    await second;
    env.nodes.get('modal-street-view-preview-image').onload();
    assert.equal(new URL(env.nodes.get('modal-street-view-preview-link').href).searchParams.get('pano'), PANO);
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, false);
});

test('late image load/error from A cannot reveal or clear the new B image', async () => {
    const env = environment();
    const first = env.preview.render(A);
    await config(env); await metadata(env); await first;
    const imageA = env.nodes.get('modal-street-view-preview-image');
    const oldLoad = imageA.onload, oldError = imageA.onerror;
    const second = env.preview.render(B);
    await tick();
    env.calls[2].resolve({ok: true, json: async () => okMetadata('new_property_pano_12345')});
    await second;
    const imageB = env.nodes.get('modal-street-view-preview-image');
    assert.notEqual(imageA, imageB);
    oldLoad(); oldError();
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    imageB.onload(); oldError();
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, false);
    assert.equal(new URL(env.nodes.get('modal-street-view-preview-link').href).searchParams.get('pano'), 'new_property_pano_12345');
});

test('timeout aborts metadata and late success cannot create an image', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    await config(env);
    const call = metadataCall(env);
    assert.equal(env.timers.size, 1);
    const timer = [...env.timers.values()][0];
    assert.equal(timer.delay, 12000);
    timer.fn();
    assert.equal(call.options.signal.aborted, true);
    call.resolve({ok: true, json: async () => okMetadata()});
    await pending;
    assert.equal(env.images.length, 0);
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
});

test('close after headers but before JSON completion aborts and ignores the delayed payload', async () => {
    const env = environment();
    const body = deferred();
    const pending = env.preview.render(A);
    await config(env);
    const call = metadataCall(env);
    call.resolve({ok: true, json: () => body.promise});
    await tick();
    env.preview.cancel();
    body.resolve(okMetadata());
    await pending;
    assert.equal(call.options.signal.aborted, true);
    assert.equal(env.images.length, 0);
    assert.equal(env.timers.size, 0);
});

test('component stores no local copies or keys and logs no error payloads', () => {
    const source = fs.readFileSync(require.resolve('./street-view-preview.js'), 'utf8');
    assert.ok(!/localStorage|sessionStorage|indexedDB|createObjectURL|\.blob\(|console\./.test(source));
});

test('metadata must include a finite Pennsylvania location before an image or click target is prepared', async () => {
    for (const location of [undefined, null, {lat: 0, lng: 0}, {lat: 43, lng: -79},
        {lat: 40, lng: -81}, {lat: '40.4', lng: -79}, {lat: NaN, lng: -79}, {lat: 40, lng: Infinity}]) {
        const env = environment();
        const pending = env.preview.render(A);
        await config(env);
        await metadata(env, {...okMetadata(), location});
        await pending;
        assert.equal(env.images.length, 0);
        assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
        assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
    }
});

test('config timeout aborts and a late enabled response cannot start Google requests', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    const timer = [...env.timers.values()][0];
    assert.equal(timer.delay, 12000);
    timer.fn();
    assert.equal(env.calls[0].options.signal.aborted, true);
    await config(env);
    await pending;
    assert.equal(env.calls.length, 1);
    assert.equal(env.images.length, 0);
    assert.equal(env.timers.size, 0);
});

test('bearing aims from camera toward the verified target with no north-facing fallback for degenerate or distant points', () => {
    const camera = {lat: 40, lng: -79};
    assert.equal(api.bearingToTarget(camera, {lat: 40.0001, lng: -79}), '0');
    assert.equal(api.bearingToTarget(camera, {lat: 40, lng: -78.9999}), '90');
    assert.equal(api.bearingToTarget(camera, {lat: 39.9999, lng: -79}), '180');
    assert.equal(api.bearingToTarget(camera, {lat: 40, lng: -79.0001}), '270');
    assert.equal(api.bearingToTarget(camera, camera), null);
    assert.equal(api.bearingToTarget(camera, {lat: 40.00000001, lng: -79}), null);
    assert.equal(api.bearingToTarget(camera, {lat: 40.001, lng: -79}), null);
    assert.equal(api.bearingToTarget(camera, {lat: 0, lng: 0}), null);
    assert.equal(api.imageUrl(PANO, TEST_KEY), null);
    assert.equal(api.panoramaUrl(PANO), null);
});

test('local resolver URL must match the full address and supported county before any Google request', async () => {
    for (const destination of [{...A, streetViewUrl: 'https://evil.test/street-view.html?address=9242'},
        {...A, streetViewUrl: 'other.html?' + A.streetViewUrl.split('?')[1]},
        {...A, streetViewUrl: B.streetViewUrl},
        {...A, streetViewUrl: A.streetViewUrl.replace('county=Allegheny', 'county=UnknownCounty')},
        {query: A.query}]) {
        const env = environment();
        const pending = env.preview.render(destination);
        await config(env); await pending;
        assert.equal(env.lookups.length, 0);
        assert.equal(env.calls.length, 1);
        assert.equal(env.images.length, 0);
    }
});

test('county lookup failure, nonmatching quality or implausible camera distance suppresses the image', async () => {
    for (const point of [null, {...TARGET, quality: 'unverified_cache'}, {lat: 0, lng: 0, quality: 'county_address_point'}]) {
        const env = environment(); env.settings.autoLookup = false;
        const pending = env.preview.render(A);
        await config(env);
        env.lookups[0].onResult({status: point ? 'matched' : 'unavailable', point});
        await pending;
        assert.equal(env.calls.length, 1);
        assert.equal(env.images.length, 0);
    }
    for (const location of [{...TARGET}, {lat: 40.46, lng: -79.86}]) {
        const env = environment();
        const pending = env.preview.render(A);
        await config(env); await metadata(env, {...okMetadata(), location}); await pending;
        assert.equal(env.images.length, 0);
    }
});

test('closing or changing property cancels the county lookup and ignores late point results', async () => {
    const env = environment(); env.settings.autoLookup = false;
    const first = env.preview.render(A);
    await config(env);
    const lookupA = env.lookups[0];
    const second = env.preview.render(B);
    await tick();
    assert.equal(lookupA.cancelled, true);
    lookupA.onResult({status: 'matched', point: TARGET});
    await first;
    assert.equal(env.calls.length, 1);
    env.preview.cancel();
    assert.equal(env.lookups[1].cancelled, true);
    env.lookups[1].onResult({status: 'matched', point: TARGET});
    await second;
    assert.equal(env.calls.length, 1);
    assert.equal(env.images.length, 0);
});

test('changed county context for the same address clears the previous image instead of reusing its target', async () => {
    const env = environment();
    const pending = env.preview.render(A);
    await config(env); await metadata(env); await pending;
    env.nodes.get('modal-street-view-preview-image').onload();
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, false);
    await env.preview.render({...A, streetViewUrl: A.streetViewUrl.replace('county=Allegheny', 'county=UnknownCounty')});
    assert.equal(env.nodes.get('modal-street-view-preview').hidden, true);
    assert.equal(env.nodes.get('modal-street-view-preview-link').href, '');
    assert.equal(env.calls.length, 2, 'no metadata lookup for unsupported new context');
});
