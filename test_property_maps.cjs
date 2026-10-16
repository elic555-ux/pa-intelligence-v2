'use strict';
// Offline regression tests for the actual page helper, renderer and map links.
// Run: node test_property_maps.cjs (optional argument: another index.html path).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function loadFunction(file, functionName, globals = {}) {
    const source = fs.readFileSync(file, 'utf8');
    const start = source.indexOf('function ' + functionName + '(');
    assert.ok(start >= 0, 'actual index must define ' + functionName);
    const scriptEnd = source.indexOf('</script>', start);
    assert.ok(scriptEnd > start, 'helper must be inside a script');
    const sandbox = {URLSearchParams, URL, ...globals};
    // Compile the function's shortest complete prefix. Do not execute page startup.
    for (let end = source.indexOf('}', start); end >= 0 && end < scriptEnd; end = source.indexOf('}', end + 1)) {
        try {
            const code = new vm.Script('(' + source.slice(start, end + 1) + ')');
            const helper = code.runInNewContext(sandbox);
            assert.equal(typeof helper, 'function');
            return helper;
        } catch (error) {
            if (!(error instanceof SyntaxError) && error.name !== 'SyntaxError') throw error;
        }
    }
    throw new Error('Could not extract complete ' + functionName + ' function');
}

const helperFile = process.argv[2] || path.join(__dirname, 'index.html');
const propertyMapDestination = loadFunction(helperFile, 'propertyMapDestination');
const base = {address: '233 Macfarlane Dr', city: 'Pittsburgh', county: 'Allegheny', zip: '15235'};
function inspect(property) {
    const result = propertyMapDestination(property);
    assert.ok(result, 'expected address target');
    const url = new URL(result.searchUrl);
    assert.equal(url.protocol, 'https:');
    assert.equal(url.hostname, 'www.google.com');
    assert.equal(url.pathname, '/maps/search/');
    assert.equal(url.searchParams.get('api'), '1');
    assert.equal(url.searchParams.get('query'), result.query);
    assert.deepEqual([...url.searchParams.keys()].sort(), ['api', 'query']);
    assert.equal(url.hash, '');
    assert.ok(result.searchUrl.length <= 2048);
    return result;
}

test('uses full actual public address, locality, PA, ZIP, USA', () => {
    const before = {...base};
    const result = inspect(base);
    assert.equal(result.query, '233 Macfarlane Dr, Pittsburgh, PA 15235, USA');
    assert.deepEqual(base, before, 'helper must not alter inventory');
});

test('normalizes whitespace without losing unit, fraction or apostrophe', () => {
    const result = inspect({address: '  12\t1/2 O\'Hara Ave\nApt 2  ', city: ' Pittsburgh\t ', zip: ' 15213-0042 '});
    assert.equal(result.query, "12 1/2 O'Hara Ave Apt 2, Pittsburgh, PA 15213-0042, USA");
});

test('city takes precedence over municipality; blank city uses municipality', () => {
    assert.equal(inspect({...base, municipality: 'Penn Hills'}).query, '233 Macfarlane Dr, Pittsburgh, PA 15235, USA');
    assert.equal(inspect({...base, city: ' \n ', municipality: 'Penn Hills'}).query, '233 Macfarlane Dr, Penn Hills, PA 15235, USA');
    assert.equal(inspect({...base, city: 'Unknown', municipality: 'Penn Hills'}).query, '233 Macfarlane Dr, Penn Hills, PA 15235, USA');
});

test('preserves leading-zero and ZIP+4 strings and accepts numeric ZIP', () => {
    assert.ok(inspect({...base, zip: '01234-0007'}).query.includes('PA 01234-0007'));
    assert.ok(inspect({...base, zip: 15235}).query.includes('PA 15235'));
});

test('quotes, ampersands, hashes and percent sequences cannot inject URL parameters', () => {
    const address = '12 "Oak & Elm" 1/2 St &api=0&query=wrong#fragment%26pano=evil';
    const result = inspect({...base, address});
    assert.equal(new URL(result.searchUrl).searchParams.get('query'), address + ', Pittsburgh, PA 15235, USA');
    assert.ok(!result.searchUrl.includes('"'));
    assert.ok(!new URL(result.searchUrl).searchParams.has('pano'));
});

test('retains Unicode address characters as one decoded search value', () => {
    assert.equal(inspect({...base, address: '12½ O’Hara Ave'}).query, '12½ O’Hara Ave, Pittsburgh, PA 15235, USA');
});

test('never reads wrong or unavailable stored coordinates', () => {
    const property = {...base};
    for (const field of ['lat', 'lng', 'latitude', 'longitude']) {
        Object.defineProperty(property, field, {get() { throw new Error('coordinates must not be read'); }});
    }
    assert.equal(inspect(property).searchUrl, inspect(base).searchUrl);
    assert.equal(inspect({...base, lat: 0, lng: 0}).searchUrl, inspect(base).searchUrl);
});

test('missing, malformed and placeholder street addresses produce no generic map URL', () => {
    for (const value of [null, undefined, {}, [], {...base, address: null}, {...base, address: ''},
        {...base, address: ' \n\t '}, {...base, address: {}}, {...base, address: 'Unknown'},
        {...base, address: 'N/A'}, {...base, address: '...'}, {...base, address: 'Allegheny County'}, {...base, address: 'כתובת לא ידועה'},
        {...base, address: '15235'}, {...base, address: '12'}, {...base, address: '12 1/2'},
        {...base, address: 'Pittsburgh'}, {...base, address: 'Allegheny'}, {...base, address: 'Penn Hills', municipality: 'Penn Hills'}]) {
        assert.equal(propertyMapDestination(value), null);
    }
});

test('incomplete locality cannot fall back to current location or county', () => {
    assert.equal(propertyMapDestination({address: base.address, county: 'Allegheny'}), null);
    assert.equal(inspect({address: base.address, zip: '15235'}).query, '233 Macfarlane Dr, PA 15235, USA');
    assert.equal(inspect({address: base.address, municipality: 'Penn Hills'}).query, '233 Macfarlane Dr, Penn Hills, PA, USA');
});

test('switch A to B to invalid returns independent targets without stale address or coordinates', () => {
    const first = inspect(base);
    const second = inspect({address: '9242 Valley St', city: 'Pittsburgh', zip: '15235', lat: 40.1, lng: -80.9});
    assert.notEqual(first.searchUrl, second.searchUrl);
    assert.equal(second.query, '9242 Valley St, Pittsburgh, PA 15235, USA');
    assert.ok(!second.searchUrl.includes('Macfarlane'));
    assert.equal(propertyMapDestination({address: '', city: 'Pittsburgh', lat: 40.1, lng: -80.9}), null);
    assert.equal(first.query, '233 Macfarlane Dr, Pittsburgh, PA 15235, USA');
});

test('oversized encoded address cannot produce an unsupported Maps URL', () => {
    assert.equal(propertyMapDestination({...base, address: 'House ' + '½'.repeat(1000)}), null);
});

if (path.extname(helperFile) === '.html') {
    test('actual index renderer replaces both links for A to B and clears all targets on invalid property', () => {
        const nodes = Object.fromEntries(['modal-property-map-section', 'modal-property-map-address',
            'modal-property-map-link', 'modal-footer-map-link'].map(id => [id, {
                hidden: true, textContent: '',
                removeAttribute(name) { delete this[name]; },
                set innerHTML(value) { throw new Error('map address must use textContent, not HTML'); },
            }]));
        const document = {getElementById(id) {
            assert.ok(Object.hasOwn(nodes, id), 'renderer must use known property map elements: ' + id);
            return nodes[id];
        }};
        const render = loadFunction(helperFile, 'renderPropertyMapDestination', {document});
        const A = inspect(base);
        const B = inspect({address: '9242 Valley St', municipality: 'Penn Hills', zip: '15235', lat: 0, lng: 0});
        for (const destination of [A, B, null, A]) {
            render(destination);
            assert.equal(nodes['modal-property-map-section'].hidden, !destination);
            assert.equal(nodes['modal-property-map-address'].textContent, destination?.query || '');
            for (const id of ['modal-property-map-link', 'modal-footer-map-link']) {
                assert.equal(nodes[id].hidden, !destination);
                if (destination) assert.equal(nodes[id].href, destination.searchUrl);
                else assert.equal(Object.hasOwn(nodes[id], 'href'), false, 'hidden link must lose stale href');
            }
        }
    });

    test('both actual external map anchors use a separate tab with opener protection', () => {
        const source = fs.readFileSync(helperFile, 'utf8');
        for (const id of ['modal-property-map-link', 'modal-footer-map-link']) {
            const tag = source.match(new RegExp('<a\\b[^>]*\\bid="' + id + '"[^>]*>'))?.[0];
            assert.ok(tag, 'missing external anchor ' + id);
            assert.match(tag, /\btarget="_blank"/);
            assert.match(tag, /\brel="[^"]*\bnoopener\b[^"]*"/);
            assert.match(tag, /\brel="[^"]*\bnoreferrer\b[^"]*"/);
        }
    });
}
