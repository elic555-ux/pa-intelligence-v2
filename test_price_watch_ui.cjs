const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.resolve(process.argv[2] || 'index.html'), 'utf8');
function actual(name) {
    const start = html.indexOf('function ' + name + '(');
    const firstLine = html.slice(start, html.indexOf('\n', start));
    if (/\{.*\}\s*$/.test(firstLine)) return firstLine;
    const end = html.indexOf('\n        }', start);
    assert.ok(start >= 0 && end > start, 'actual function exists: ' + name);
    return html.slice(start, end + '\n        }'.length);
}
const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, {
        value: '', checked: true, textContent: '', innerHTML: '', hidden: false,
        classList: {toggle() {}}, focus() {}
    });
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
    watchlistFilterActive: false, activeCategory: 'mls', selectedCitiesFilter: null,
    latestNewFilterActive: false, watchlistPendingIds: new Set(),
    scannerReport: {last_scan: {scan_id: 'latest-scan', started_at: '2026-10-18T04:00:00Z'}},
    parseDate: value => Date.parse(value) || 0,
    currentViewMode: 'table', viewedDeals: new Set(), uiPropertyKey: p => p.id,
    updateBoardPriceSummary() {},
    financialSnapshot: () => ({}), escapeHtml: String,
    propertyCityDisplay: p => p.city || '', propertyStatusDisplayHtml: () => '',
    hasRecordedPriceDrop: () => false, priceDropDetailsHtml: () => '',
    livingAreaDisplayHtml: () => '', featureDisplayHtml: () => '',
    financialDisplayHtml: () => '', propertyDateDisplayHtml: () => '',
    neighborhoodBadgeHtml: () => '', analysisMoneyData: () => '',
    financialExplanationHtml: () => '', lucide: {createIcons() {}}
});
vm.runInContext(['propertySearchTerms', 'propertyMatchesSearch', 'clearPropertySearch', 'applyFilters', 'getFilteredProperties',
                 'propertyPriceDisplayHtml', 'isLatestNewProperty', 'updateResultSummary',
                 'renderCards'].map(actual).join('\n'), context);

// Retain the existing price/background-watch regression coverage.
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

// Both fixtures are real-shape records; scan metadata is intentionally synthetic.
// Only the rendering helper outputs unrelated to filtering are stubbed above.
const older = {id: 'older', address: '9242 Valley St', city: 'Pittsburgh', source_type: 'mls', price: 150000,
    listed_date: '2026-10-01', first_seen: '2026-10-01', last_scan_id: 'prior-scan', scan_status: 'new'};
const latest = {id: 'latest', address: '100 Latest St', city: 'Pittsburgh', source_type: 'mls', price: 160000,
    listed_date: '2026-10-18', first_seen: '2026-10-18T04:10:00Z', last_scan_id: 'latest-scan', scan_status: 'new'};
context.rawProperties = [older, latest];
context.latestNewFilterActive = true;
element('sort-by').value = 'date_desc';
element('property-search').value = older.address;
element('property-search-all').checked = true;
assert.equal(context.getFilteredProperties().length, 1, 'all-inventory search must find the prior-scan record');
for (const mode of ['table', 'grid']) {
    context.currentViewMode = mode;
    context.renderCards();
    assert.match(element('properties-container').innerHTML, /9242 Valley St/,
        mode + ': all-inventory search must render the prior-scan record with latest-new active');
    assert.equal(element('filtered-result-count').textContent, 'תוצאות לאחר סינון: 1',
        mode + ': summary must count the displayed all-inventory result');
    assert.equal(element('property-search-status').textContent, 'בכל המאגר, כולל ארכיון');
    assert.equal(element('filtered-unknown-price').hidden, true);
    assert.equal(context.latestNewFilterActive, true, 'search must preserve the latest-new selection');
}
console.log('PASS all-inventory search renders prior-scan matches in table/grid with matching counts and scope status');

element('property-search-all').checked = false;
for (const mode of ['table', 'grid']) {
    context.currentViewMode = mode;
    context.renderCards();
    assert.doesNotMatch(element('properties-container').innerHTML, /9242 Valley St/,
        mode + ': within-filter search must retain the latest-new restriction');
    assert.match(element('properties-container').innerHTML, /לא נמצא נכס שתואם לחיפוש/);
    assert.equal(element('filtered-result-count').textContent, 'תוצאות לאחר סינון: 0');
    assert.equal(element('property-search-status').textContent, 'בתוך המסננים');
}
console.log('PASS within-filter search retains the latest-new restriction in table/grid and count');

element('property-search-all').checked = true;
element('property-search').value = 'St';
for (const mode of ['table', 'grid']) {
    context.currentViewMode = mode;
    context.renderCards();
    assert.match(element('properties-container').innerHTML, /9242 Valley St/);
    assert.match(element('properties-container').innerHTML, /100 Latest St/);
    assert.equal(element('filtered-result-count').textContent, 'תוצאות לאחר סינון: 2');
}
console.log('PASS all-inventory search shows both prior/latest scan matches with count 2');

for (const mode of ['table', 'grid']) {
    context.currentViewMode = mode;
    element('property-search').value = 'St';
    context.clearPropertySearch();
    assert.equal(element('property-search').value, '', 'clear-search button must empty the query');
    assert.doesNotMatch(element('properties-container').innerHTML, /9242 Valley St/);
    assert.match(element('properties-container').innerHTML, /100 Latest St/);
    assert.equal(element('filtered-result-count').textContent, 'תוצאות לאחר סינון: 1');
    assert.equal(element('property-search-status').textContent, '');
    assert.deepEqual(Array.from(context.getFilteredProperties(), p => p.id), ['latest']);
}
context.latestNewFilterActive = false;
assert.deepEqual(Array.from(context.getFilteredProperties(), p => p.id), ['older', 'latest']);
console.log('PASS clearing search restores the active latest-new filter; disabling latest-new restores ordinary results');
