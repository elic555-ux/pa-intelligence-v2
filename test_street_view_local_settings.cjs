'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const factory = require('./street-view-local-settings.js');
const key = 'AIza' + 'x'.repeat(35); // synthetic, offline only
test('browser storage persists only explicit valid input and can remove it', () => {
    const items = new Map();
    const localStorage = {getItem: k => items.get(k), setItem: (k,v) => items.set(k,v), removeItem: k => items.delete(k)};
    const api = factory({localStorage}, null);
    assert.equal(api.read(), null);
    assert.equal(api.save('invalid'), 'invalid'); assert.equal(items.size, 0);
    assert.equal(api.save('  ' + key + '  '), 'saved');
    assert.equal(factory({localStorage}, null).read().apiKey, key);
    assert.equal(api.clear(), true); assert.equal(api.read(), null);
});
test('unavailable or denied browser storage fails closed', () => {
    const win = {}; Object.defineProperty(win, 'localStorage', {get() {throw Error('denied');}});
    const api = factory(win, null);
    assert.equal(api.read(), null); assert.equal(api.save(key), 'unavailable'); assert.equal(api.clear(), false);
});
