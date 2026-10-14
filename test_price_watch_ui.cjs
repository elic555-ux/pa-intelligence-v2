const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
function actual(name) {
    const start = html.indexOf('function ' + name + '(');
    const end = html.indexOf('\n        }', start);
    assert.ok(start >= 0 && end > start);
    return html.slice(start, end + '\n        }'.length);
}
const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, {value: '', checked: true, textContent: ''});
    return elements.get(id);
};
element('filter-type').value = 'all';
const rows = [190000, 190001, 209000, 209001, 239000].map((price, i) =>
    ({id: String(i), address: 'First St', source_type: 'mls', price}));
const context = vm.createContext({
    document: {getElementById: element}, rawProperties: rows,
    boardScanPriceRange: {min: 50000, max: 190000},
    boardPriceRange: () => ({min: 50000, max: 300000, error: ''}),
    getMarketAskingPrice: p => p.price, moneyData: p => '$' + p,
    numericData: value => value === undefined ? null : Number(value),
    isPipelineProperty: () => false, isWatchlistedProperty: () => false,
    matchesSourceCategory: () => true, mainGeoKey: () => 'Allegheny',
    watchlistFilterActive: false, activeCategory: 'mls', selectedCitiesFilter: null
});
vm.runInContext(['propertySearchTerms', 'propertyMatchesSearch', 'getFilteredProperties',
                 'propertyPriceDisplayHtml'].map(actual).join('\n'), context);
assert.deepEqual(Array.from(context.getFilteredProperties(), p => p.price), [190000]);
element('property-search').value = 'First';
element('property-search-all').checked = false;
assert.deepEqual(Array.from(context.getFilteredProperties(), p => p.price), [190000]);
element('property-search-all').checked = true;
assert.equal(context.getFilteredProperties().length, 5);
assert.match(context.propertyPriceDisplayHtml(rows[2]), /מעקב ברקע/);
assert.match(context.propertyPriceDisplayHtml(rows[3]), /מעל תקרת המעקב/);
assert.doesNotMatch(context.propertyPriceDisplayHtml(rows[0]), /מעקב|היסטוריה/);
rows[1].price = 185000;
element('property-search').value = '';
assert.deepEqual(Array.from(context.getFilteredProperties(), p => p.price), [190000, 185000]);
console.log('PASS ordinary results stay within budget; all-inventory search labels background/history; price drop reenters results');
