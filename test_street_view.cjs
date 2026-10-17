'use strict';
// Dependency-free offline tests. No HTTP, geocoding or Google calls are made.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const api = require('./street-view.js');

const INPUT = {address: '9242 Valley Street', city: 'Pittsburgh', zip: '15235', county: 'Allegheny County'};
function request(overrides = {}) { return api.readRequest('?' + new URLSearchParams({...INPUT, ...overrides})); }
function fixture() {
    // Official county address-point response observed during read-only QA.
    // This point is not the different Census interpolated road coordinate.
    return {spatialReference: {wkid: 4326}, features: [{attributes: {
        ADDR_NUM: 9242, FULL_ADDRESS: '9242 VALLEY ST', ZIP_CODE: '15235', STATE: 'PA',
        MUNICIPALITY: 'PENN HILLS MUNICIPALITY', STATUS: 'ACTIVE'},
        geometry: {x: -79.857771813301468, y: 40.4597766352951}}]};
}
function altered(attributeChanges = {}, geometryChanges = {}) {
    const payload = fixture();
    Object.assign(payload.features[0].attributes, attributeChanges);
    Object.assign(payload.features[0].geometry, geometryChanges);
    return payload;
}
function erieRequest(overrides = {}) {
    return api.readRequest('?' + new URLSearchParams({address: '1915 Liberty Street', city: 'Erie',
        zip: '16502', county: 'Erie County', municipality: '', ...overrides}));
}
function erieFixture() {
    // Official public Erie parcel-centroid response observed in browser QA.
    return {spatialReference: {wkid: 4326}, features: [{attributes: {fid: 49495,
        taxpin: '19060020011500', municipali: 'City of Erie', fullstreet: '1915   LIBERTY ST'},
        centroid: {x: -80.09373085017114, y: 42.11109295181472}}]};
}
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

function environment(search = '?' + new URLSearchParams(INPUT)) {
    const timers = new Map();
    const allTimers = [];
    const scripts = [];
    const results = [];
    const navigations = [];
    const fetchRequests = [];
    const events = new Map();
    let nextTimer = 0;
    const nodes = Object.fromEntries(['street-view-status', 'street-view-address', 'street-view-fallback', 'street-view-cancel'].map(id => [id, {
        hidden: true, textContent: '', handlers: {},
        addEventListener(event, fn) { this.handlers[event] = fn; },
        set innerHTML(value) { throw new Error('User-supplied text must not be assigned as HTML'); },
    }]));
    const head = {
        appendChild(script) { script.parentNode = head; scripts.push(script); return script; },
        removeChild(script) { assert.equal(script.parentNode, head); script.parentNode = null; },
    };
    const document = {documentElement: {dataset: {}}, head,
        createElement(tag) { assert.equal(tag, 'script'); return {parentNode: null}; },
        getElementById(id) { assert.ok(nodes[id], 'unknown UI element: ' + id); return nodes[id]; }};
    const window = {location: {search, replace(url) { navigations.push(url); }}, AbortController,
        fetch(url, options) { const pending = deferred(); fetchRequests.push({url, options, ...pending}); return pending.promise; },
        setTimeout(fn, milliseconds) { const id = ++nextTimer; timers.set(id, fn); allTimers.push({id, fn, milliseconds}); return id; },
        clearTimeout(id) { timers.delete(id); },
        addEventListener(event, fn) { events.set(event, fn); }};
    return {window, document, setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
        onResult: result => results.push(result), timers, allTimers, scripts, results, navigations, nodes, events, fetchRequests};
}

test('input preserves full address, source locality, ZIP+4 and actual county parameter', () => {
    const value = request({address: ' 9242\tValley Street ', zip: '15235-0042'});
    assert.equal(value.query, '9242 Valley Street, Pittsburgh, PA 15235-0042, USA');
    assert.equal(value.county, 'allegheny');
    assert.equal(value.zip5, '15235');
    assert.equal(value.identity.house, '9242');
    assert.equal(new URL(value.searchUrl).searchParams.get('query'), value.query);
});

test('missing, placeholder, city-only or numeric-only address and absent locality are rejected', () => {
    for (const address of ['', ' ', 'Unknown', 'N/A', 'Pittsburgh', '15235', '12']) assert.equal(request({address}), null);
    assert.equal(request({city: '', zip: ''}), null);
    assert.equal(request({city: 'Unknown', zip: ''}), null);
    assert.equal(request({zip: "15235' OR 1=1"}), null);
    assert.equal(request({zip: '1523'}), null);
    assert.equal(api.readRequest('?address=a&address=b&city=Pittsburgh&zip=15235'), null);
});

test('suffix aliases and street punctuation normalize narrowly while core street and direction remain distinct', () => {
    assert.equal(api.streetIdentity('9242 valley street.').key, '9242 VALLEY ST');
    assert.equal(api.streetIdentity("12 O’Hara Drive").key, "12 OHARA DR");
    assert.equal(api.streetIdentity('12 North Main Street N').key, '12 NORTH MAIN ST N');
    assert.notEqual(api.streetIdentity('12 N Main St').key, api.streetIdentity('12 S Main St').key);
    assert.notEqual(api.streetIdentity('12 Street Road').key, api.streetIdentity('12 St Road').key);
    assert.notEqual(api.streetIdentity('12 Valley St').key, api.streetIdentity('12 Valley View St').key);
});

test('house suffixes, ranges and fractions are preserved and unsupported forms are not queried as integers', () => {
    for (const address of ['12A Main St', '12-14 Main St', '12 1/2 Main St', '12½ Main St']) {
        const value = request({address});
        assert.ok(value, address + ' remains a complete address for the explicit Maps fallback');
        assert.equal(api.countyRequestUrl(value), null);
        assert.notEqual(value.identity.house, '12');
    }
    for (const address of ['12.5 Main St', '12,5 Main St', "1'25 Main St", '1’25 Main St', '12/5 Main St']) {
        assert.equal(request({address}), null, address + ' must never become house 125');
    }
});

test('explicit county selects only its provider and unsupported counties cannot fall through postal hints', () => {
    for (const county of ['Westmoreland County', 'Cambria', 'Butler County']) {
        const value = request({county});
        assert.ok(value);
        assert.equal(api.supportsCounty(value), false);
        assert.equal(api.countyRequestUrl(value), null);
        assert.equal(api.validateCountyResponse(fixture(), value), null);
    }
    for (const county of ['Erie', 'Erie County', ' Erie\t County ']) {
        const value = request({county});
        assert.equal(api.countyProvider(value), 'erie');
        assert.equal(new URL(api.countyRequestUrl(value)).hostname, 'gis.eriecountypa.gov');
        assert.equal(api.validateCountyResponse(fixture(), value), null, 'Allegheny point cannot be used for Erie');
    }
    assert.equal(api.supportsCounty(request({county: '', zip: '15235'})), true);
    assert.equal(api.supportsCounty(request({county: '', city: 'Erie', zip: '16501'})), false);
});

test('JSONP request is fixed county origin and callback with bounded unambiguous SQL parameters', () => {
    const url = new URL(api.countyRequestUrl(request()));
    assert.equal(url.origin, 'https://gisdata.alleghenycounty.us');
    assert.equal(url.pathname, '/arcgis/rest/services/EGIS/AC_AddressPointsWithLabels/MapServer/0/query');
    assert.equal(url.searchParams.get('where'), "ADDR_NUM=9242 AND ZIP_CODE='15235'");
    assert.equal(url.searchParams.get('outSR'), '4326');
    assert.equal(url.searchParams.get('resultRecordCount'), '100');
    assert.equal(url.searchParams.get('callback'), 'paStreetView');
    assert.equal(url.searchParams.get('returnGeometry'), 'true');
    assert.equal(url.username, '');
    assert.equal(url.password, '');
    const input = request({address: '9242 Valley St &where=1=1#x', city: '<img src=x onerror=alert(1)> &callback=evil'});
    const injected = new URL(api.countyRequestUrl(input));
    assert.equal(injected.searchParams.get('where'), url.searchParams.get('where'));
    assert.equal(injected.searchParams.get('callback'), 'paStreetView');
    assert.equal(injected.hash, '');
    assert.equal([...injected.searchParams.keys()].filter(key => key === 'where').length, 1);
});

test('coordinate and callback parameters supplied in the page URL have no influence', () => {
    const ordinary = '?' + new URLSearchParams(INPUT);
    const value = api.readRequest(ordinary + '&lat=0&lng=0&viewpoint=0,0&callback=evil&provider=https://evil.test/');
    assert.deepEqual(value, request());
    assert.equal(api.countyRequestUrl(value), api.countyRequestUrl(request()));
});

test('exact single ACTIVE county point is used without substituting Census range coordinates', () => {
    const value = api.validateCountyResponse(fixture(), request());
    assert.deepEqual(value, {lat: 40.4597766352951, lng: -79.857771813301468,
        matchedAddress: '9242 VALLEY ST', quality: 'county_address_point'});
    const url = new URL(api.panoramaUrl(value));
    assert.equal(url.origin, 'https://www.google.com');
    assert.equal(url.pathname, '/maps/@');
    assert.deepEqual([...url.searchParams.keys()].sort(), ['api', 'map_action', 'viewpoint']);
    assert.equal(url.searchParams.get('api'), '1');
    assert.equal(url.searchParams.get('map_action'), 'pano');
    assert.equal(url.searchParams.get('viewpoint'), '40.4597766,-79.8577718');
    assert.ok(!url.href.includes('79.858705'), 'never use known interpolated range coordinate');
});

test('street, house, state, ZIP and status mismatches fail closed', () => {
    for (const change of [{FULL_ADDRESS: '9222 VALLEY ST'}, {ADDR_NUM: 9222}, {FULL_ADDRESS: '9242 VALLEY VIEW ST'},
        {FULL_ADDRESS: '9242 S VALLEY ST'}, {STATE: 'NY'}, {ZIP_CODE: '15234'}, {STATUS: 'INACTIVE'},
        {FULL_ADDRESS: null}, {STATE: null}, {ZIP_CODE: null}]) {
        assert.equal(api.validateCountyResponse(altered(change), request()), null, JSON.stringify(change));
    }
    assert.ok(api.validateCountyResponse(fixture(), request({zip: '15235-1234'})));
});

test('without ZIP a normalized matching municipality is mandatory and query never invents ZIP', () => {
    const value = request({city: 'Penn Hills', zip: ''});
    assert.ok(api.validateCountyResponse(fixture(), value));
    assert.equal(new URL(api.countyRequestUrl(value)).searchParams.get('where'), 'ADDR_NUM=9242');
    assert.equal(api.validateCountyResponse(fixture(), request({city: 'Pittsburgh', zip: ''})), null);
});

test('ambiguous matches, provider errors and exceeded transfer limits never navigate', () => {
    const ambiguous = fixture();
    ambiguous.features.push(structuredClone(ambiguous.features[0]));
    ambiguous.features[1].geometry.x += 0.0001;
    assert.equal(api.validateCountyResponse(ambiguous, request()), null);
    for (const payload of [null, {}, {features: []}, {error: {code: 500}},
        {...fixture(), exceededTransferLimit: true}, {...fixture(), exceededTransferLimit: 'true'},
        {...fixture(), features: Array(101).fill(fixture().features[0])},
        {...fixture(), spatialReference: {wkid: 3857}},
        {...fixture(), spatialReference: {wkid: 4326, latestWkid: 3857}}]) assert.equal(api.validateCountyResponse(payload, request()), null);
});

test('finite WGS84 points inside Pennsylvania bounds are required', () => {
    for (const change of [{x: 0}, {y: 0}, {x: Infinity}, {y: NaN}, {x: '-79.85'},
        {y: 43}, {x: -81}, {spatialReference: {wkid: 3857}}, {spatialReference: {wkid: 4326, latestWkid: 3857}}]) {
        assert.equal(api.validateCountyResponse(altered({}, change), request()), null);
    }
    for (const point of [null, {lat: NaN, lng: -79}, {lat: 40, lng: '79'}, {lat: 0, lng: 0}]) assert.equal(api.panoramaUrl(point), null);
});

test('JSONP success cleans script, timer and handlers before delivering exactly one result', () => {
    const env = environment();
    const controller = api.resolveOnce(request(), env);
    assert.equal(env.scripts.length, 1);
    assert.equal(env.allTimers[0].milliseconds, 12000);
    const script = env.scripts[0];
    const callback = env.window.paStreetView;
    const oldError = script.onerror;
    env.window.paStreetView(fixture());
    assert.equal(env.results.length, 1);
    assert.equal(env.results[0].status, 'matched');
    assert.equal(env.timers.size, 0);
    assert.equal(script.parentNode, null);
    assert.equal(script.onerror, null);
    assert.equal(script.onload, null);
    callback(fixture()); oldError(); controller.cancel(); env.window.paStreetView(fixture());
    assert.equal(env.results.length, 1, 'late and duplicate callbacks must be harmless');
});

test('timeout stops the request at 12 seconds and all later callbacks remain inert', () => {
    const env = environment();
    api.resolveOnce(request(), env);
    const callback = env.window.paStreetView;
    env.allTimers[0].fn();
    assert.equal(env.results[0].status, 'timeout');
    assert.equal(env.scripts[0].parentNode, null);
    assert.equal(env.timers.size, 0);
    callback(fixture()); env.window.paStreetView(fixture());
    assert.equal(env.results.length, 1);
    assert.equal(env.navigations.length, 0);
});

test('load error, loaded script without callback, malformed response and append failure clean up', () => {
    for (const mode of ['error', 'no-callback', 'malformed', 'throwing-payload', 'append-failure']) {
        const env = environment();
        if (mode === 'append-failure') env.document.head.appendChild = () => { throw new Error('blocked'); };
        api.resolveOnce(request(), env);
        if (mode === 'error') env.scripts[0].onerror();
        if (mode === 'no-callback') env.scripts[0].onload();
        if (mode === 'malformed') env.window.paStreetView({features: 'not an array'});
        if (mode === 'throwing-payload') env.window.paStreetView({get features() { throw new Error('bad payload'); }});
        assert.equal(env.results.length, 1, mode);
        assert.equal(env.results[0].status, 'unavailable', mode);
        assert.equal(env.timers.size, 0, mode);
        assert.ok(env.scripts.every(script => script.parentNode === null), mode);
    }
});

test('user cancellation prevents both stored and global late callbacks from matching', () => {
    const env = environment();
    const controller = api.resolveOnce(request(), env);
    const callback = env.window.paStreetView;
    controller.cancel(); callback(fixture()); env.window.paStreetView(fixture());
    assert.equal(env.results.length, 1);
    assert.equal(env.results[0].status, 'cancelled');
    assert.equal(env.scripts[0].parentNode, null);
    assert.equal(env.timers.size, 0);
});

test('unsupported county or complex house form creates no provider script or timer', () => {
    for (const value of [request({county: 'Westmoreland County'}), request({address: '12A Main St'}), null]) {
        const env = environment();
        api.resolveOnce(value, env);
        assert.equal(env.scripts.length, 0);
        assert.equal(env.timers.size, 0);
        assert.equal(env.results[0].status, 'unavailable');
    }
});

test('page mounts once, displays user input only as text and replaces this tab on exact match', () => {
    const env = environment();
    api.mount(env.window, env.document);
    api.mount(env.window, env.document);
    assert.equal(env.scripts.length, 1);
    assert.equal(env.nodes['street-view-address'].textContent, request().query);
    env.window.paStreetView(fixture());
    assert.equal(env.navigations.length, 1);
    assert.equal(new URL(env.navigations[0]).searchParams.get('map_action'), 'pano');
    assert.equal(env.nodes['street-view-fallback'].hidden, true);
});

test('page invalid/unsupported/no-match states never silently open Google Maps instead of Street View', () => {
    const invalid = environment('?address=Unknown&city=Pittsburgh&zip=15235');
    api.mount(invalid.window, invalid.document);
    assert.equal(invalid.scripts.length, 0);
    assert.equal(invalid.nodes['street-view-fallback'].hidden, true);
    for (const mode of ['unsupported', 'no-match', 'timeout', 'cancel', 'pagehide']) {
        const env = environment('?' + new URLSearchParams({...INPUT, ...(mode === 'unsupported' ? {county: 'Westmoreland County'} : {})}));
        api.mount(env.window, env.document);
        if (mode === 'no-match') env.window.paStreetView({spatialReference: {wkid: 4326}, features: []});
        if (mode === 'timeout') env.allTimers[0].fn();
        if (mode === 'cancel') env.nodes['street-view-cancel'].handlers.click();
        if (mode === 'pagehide') env.events.get('pagehide')();
        assert.equal(env.navigations.length, 0, mode);
        assert.equal(env.nodes['street-view-fallback'].hidden, false, mode);
        assert.equal(new URL(env.nodes['street-view-fallback'].href).searchParams.get('query'), request().query);
        assert.equal(env.timers.size, 0, mode);
        env.window.paStreetView?.(fixture());
        assert.equal(env.navigations.length, 0, 'no late redirect: ' + mode);
    }
});

test('blocked final navigation exposes explicit fallback and keeps the address available', () => {
    const env = environment();
    env.window.location.replace = () => { throw new Error('navigation denied'); };
    api.mount(env.window, env.document);
    env.window.paStreetView(fixture());
    assert.equal(env.nodes['street-view-fallback'].hidden, false);
    assert.equal(env.nodes['street-view-address'].textContent, request().query);
});

test('Erie uses one bounded official parcel query with decimal-only house prefix and no street SQL interpolation', () => {
    const url = new URL(api.countyRequestUrl(erieRequest()));
    assert.equal(url.origin, 'https://gis.eriecountypa.gov');
    assert.equal(url.pathname, '/server/rest/services/Hosted/ErieCountyParcels_Dec2025/FeatureServer/16/query');
    assert.equal(url.searchParams.get('where'), "UPPER(fullstreet) LIKE '1915%'");
    assert.equal(url.searchParams.get('outFields'), 'fid,taxpin,municipali,fullstreet');
    assert.equal(url.searchParams.get('returnCentroid'), 'true');
    assert.equal(url.searchParams.get('returnGeometry'), 'false');
    assert.equal(url.searchParams.get('outSR'), '4326');
    assert.equal(url.searchParams.get('resultRecordCount'), '100');
    assert.equal(url.searchParams.get('callback'), null, 'Erie must return normal JSON for CORS fetch');
    const injection = erieRequest({address: "1915 O'Hara St' OR 1=1--", municipality: "Erie' OR 1=1"});
    const safe = new URL(api.countyRequestUrl(injection));
    assert.equal(safe.searchParams.get('where'), "UPPER(fullstreet) LIKE '1915%'");
    assert.equal(safe.searchParams.get('callback'), null);
    assert.equal(api.countyRequestUrl(erieRequest({address: "1'915 Liberty St"})), null);
});

test('Erie exact normalized multi-space fullstreet and municipality yield a parcel centroid, not a rooftop claim', async () => {
    const value = api.validateCountyResponse(erieFixture(), erieRequest());
    assert.deepEqual(value, {lat: 42.11109295181472, lng: -80.09373085017114,
        matchedAddress: '1915 LIBERTY ST', quality: 'county_parcel_centroid'});
    assert.equal(new URL(api.panoramaUrl(value)).searchParams.get('viewpoint'), '42.1110930,-80.0937309');
    const env = environment();
    api.resolveOnce(erieRequest(), env);
    assert.equal(env.scripts.length, 0);
    assert.equal(env.fetchRequests.length, 1);
    const fetch = env.fetchRequests[0];
    assert.equal(new URL(fetch.url).hostname, 'gis.eriecountypa.gov');
    assert.equal(fetch.options.credentials, 'omit');
    assert.equal(fetch.options.mode, 'cors');
    assert.equal(fetch.options.referrerPolicy, 'no-referrer');
    assert.ok(fetch.options.signal instanceof AbortSignal);
    fetch.resolve({ok: true, json: async () => erieFixture()});
    await tick();
    assert.equal(env.results[0].status, 'matched');
    assert.equal(env.results[0].point.quality, 'county_parcel_centroid');
    assert.equal(env.timers.size, 0);
});

test('Erie requires actual municipality when supplied and rejects postal-city mismatch otherwise', () => {
    assert.equal(api.validateCountyResponse(erieFixture(), erieRequest({municipality: 'Fairview Township'})), null);
    assert.equal(api.validateCountyResponse(erieFixture(), erieRequest({city: 'Millcreek', municipality: ''})), null);
    assert.ok(api.validateCountyResponse(erieFixture(), erieRequest({city: 'Pittsburgh', municipality: 'Erie City'})));
    const municipalOnly = erieRequest({city: '', municipality: 'City of Erie', zip: ''});
    assert.ok(api.validateCountyResponse(erieFixture(), municipalOnly));
    assert.equal(new URL(municipalOnly.searchUrl).searchParams.get('query'), '1915 Liberty Street, City of Erie, PA, USA');
    assert.equal(api.countyRequestUrl(erieRequest({city: '', municipality: '', zip: '16502'})), null);
});

test('numeric ordinal STREET tokens normalize without changing raw house numbers or street identity', () => {
    assert.equal(api.streetIdentity('307 E 28th St').key, '307 E 28 ST');
    assert.equal(api.streetIdentity('307 E 28th St').key, api.streetIdentity('307 E 28 ST').key);
    assert.notEqual(api.streetIdentity('307 E 28th St').key, api.streetIdentity('307 E 29 ST').key);
    assert.notEqual(api.streetIdentity('307 E 28th St').key, api.streetIdentity('307 W 28 ST').key);
    assert.equal(api.streetIdentity('307 1st Ave').key, '307 1 AVE');
    assert.equal(api.streetIdentity('307 2nd Ave').key, '307 2 AVE');
    assert.equal(api.streetIdentity('307 3rd Ave').key, '307 3 AVE');
    assert.equal(api.streetIdentity('12.5 E 28th St'), null);
    const payload = erieFixture();
    payload.features[0].attributes.fullstreet = '307   E 28 ST';
    assert.ok(api.validateCountyResponse(payload, erieRequest({address: '307 E 28th Street'})));
});

test('Erie rejects wrong street, ambiguous parcels, incomplete/invalid centroids and transfer-limit responses', () => {
    const wrongStreet = erieFixture(); wrongStreet.features[0].attributes.fullstreet = '19150 LIBERTY ST';
    const ambiguous = erieFixture(); ambiguous.features.push(structuredClone(ambiguous.features[0]));
    const missingCentroid = erieFixture(); delete missingCentroid.features[0].centroid;
    missingCentroid.features[0].geometry = {x: -80.0937, y: 42.1111};
    const invalidPoint = erieFixture(); invalidPoint.features[0].centroid.x = Infinity;
    const wrongSR = erieFixture(); wrongSR.features[0].centroid.spatialReference = {wkid: 3857};
    const emptyMunicipality = erieFixture(); emptyMunicipality.features[0].attributes.municipali = '';
    for (const payload of [wrongStreet, ambiguous, missingCentroid, invalidPoint, wrongSR, emptyMunicipality,
        {...erieFixture(), exceededTransferLimit: true}]) assert.equal(api.validateCountyResponse(payload, erieRequest()), null);
});

test('Erie HTTP, network and malformed JSON failures clean up and report unavailable once', async () => {
    for (const mode of ['http', 'network', 'json', 'payload']) {
        const env = environment();
        api.resolveOnce(erieRequest(), env);
        const fetch = env.fetchRequests[0];
        if (mode === 'network') fetch.reject(new Error('blocked'));
        else if (mode === 'http') fetch.resolve({ok: false, json: async () => erieFixture()});
        else if (mode === 'json') fetch.resolve({ok: true, json: async () => { throw new Error('invalid JSON'); }});
        else fetch.resolve({ok: true, json: async () => ({features: 'bad'})});
        await tick();
        assert.equal(env.results.length, 1, mode);
        assert.equal(env.results[0].status, 'unavailable', mode);
        assert.equal(env.timers.size, 0, mode);
        assert.equal(fetch.options.signal.aborted, true, mode);
        assert.equal(env.scripts.length, 0, mode);
    }
});

test('Erie timeout and user cancellation abort fetch and ignore late network results', async () => {
    for (const mode of ['timeout', 'cancel']) {
        const env = environment();
        const controller = api.resolveOnce(erieRequest(), env);
        const fetch = env.fetchRequests[0];
        assert.equal(env.allTimers[0].milliseconds, 12000);
        if (mode === 'timeout') env.allTimers[0].fn(); else controller.cancel();
        assert.equal(fetch.options.signal.aborted, true);
        fetch.resolve({ok: true, json: async () => erieFixture()});
        await tick();
        assert.equal(env.results.length, 1);
        assert.equal(env.results[0].status, mode === 'timeout' ? 'timeout' : 'cancelled');
        assert.equal(env.timers.size, 0);
    }
});

test('Erie cancellation while reading JSON cannot deliver a stale match', async () => {
    const env = environment();
    const body = deferred();
    const controller = api.resolveOnce(erieRequest(), env);
    env.fetchRequests[0].resolve({ok: true, json: () => body.promise});
    await tick();
    controller.cancel();
    body.resolve(erieFixture());
    await tick();
    assert.equal(env.results.length, 1);
    assert.equal(env.results[0].status, 'cancelled');
    assert.equal(env.fetchRequests[0].options.signal.aborted, true);
});

test('Erie page uses the fetch result to replace this tab and exposes fallback after fetch failure', async () => {
    for (const success of [true, false]) {
        const env = environment('?' + new URLSearchParams({address: '1915 Liberty St', city: 'Erie', zip: '16502', county: 'Erie'}));
        api.mount(env.window, env.document);
        api.mount(env.window, env.document);
        assert.equal(env.fetchRequests.length, 1);
        env.fetchRequests[0].resolve({ok: success, json: async () => erieFixture()});
        await tick();
        assert.equal(env.navigations.length, success ? 1 : 0);
        assert.equal(env.nodes['street-view-fallback'].hidden, success);
        assert.equal(env.scripts.length, 0);
    }
});

test('standalone CSP separates Allegheny JSONP from credential-free Erie CORS and keeps the exact inline CSS hash', () => {
    const html = fs.readFileSync(path.join(__dirname, 'street-view.html'), 'utf8');
    const csp = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
    const css = html.match(/<style>([\s\S]*?)<\/style>/)[1].replace(/\r\n/g, '\n');
    const hash = crypto.createHash('sha256').update(css).digest('base64');
    assert.ok(csp.includes("style-src 'sha256-" + hash + "'"));
    assert.equal(csp.split(';').map(value => value.trim()).find(value => value.startsWith('script-src ')),
        "script-src 'self' https://gisdata.alleghenycounty.us");
    assert.ok(csp.includes("default-src 'none'"));
    assert.equal(csp.split(';').map(value => value.trim()).find(value => value.startsWith('connect-src ')),
        'connect-src https://gis.eriecountypa.gov');
    assert.ok(csp.includes("base-uri 'none'"));
    assert.ok(!/unsafe-inline|unsafe-eval|geocoding\.geo\.census\.gov|\*/.test(csp));
    assert.match(html, /<script src="street-view\.js" defer><\/script>/);
    assert.equal((html.match(/<script\b/g) || []).length, 1);
    assert.match(html, /id="street-view-fallback"[^>]*target="_blank"[^>]*rel="noopener noreferrer"/);
    assert.match(html, /עשוי להציג בית סמוך/);
    assert.match(html, /מרכז חלקה/);
});
