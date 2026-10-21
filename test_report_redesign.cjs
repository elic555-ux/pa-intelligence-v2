'use strict';

// Offline integration coverage for the real report adapter. No browser, API,
// credentials, package installation or external data is used by this test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'report.html'), 'utf8');
function sourceFunction(name) {
    const match = new RegExp('(?:^|\\n)((?:async )?function ' + name + '\\s*\\()').exec(source);
    assert.ok(match, 'Report function exists: ' + name);
    const start = match.index + (source[match.index] === '\n' ? 1 : 0);
    // Let the JavaScript parser find the complete declaration, including braces
    // in strings/regular expressions/templates, without a second JS parser.
    for (let end = source.indexOf('}', start); end >= 0; end = source.indexOf('}', end + 1)) {
        const candidate = source.slice(start, end + 1);
        try { new vm.Script('(' + candidate + ')'); return candidate; } catch (_) {}
    }
    throw new Error('Cannot extract report function: ' + name);
}

class Element {
    constructor(tag = 'div', attrs = {}, text = '') {
        this.tagName = tag.toUpperCase(); this.attributes = {...attrs}; this.children = [];
        this.parentNode = null; this.textContent = text; this.innerHTML = ''; this.value = '';
        this.style = {}; this.disabled = false; this.open = false;
        this.classList = {
            contains: token => this.classes().includes(token),
            add: token => { this.attributes.class = [...new Set([...this.classes(), token])].join(' '); },
            remove: token => { this.attributes.class = this.classes().filter(x => x !== token).join(' '); },
            toggle: (token, force) => {
                const enabled = force === undefined ? !this.classList.contains(token) : !!force;
                enabled ? this.classList.add(token) : this.classList.remove(token); return enabled;
            }
        };
    }
    classes() { return String(this.attributes.class || '').split(/\s+/).filter(Boolean); }
    get id() { return this.attributes.id || ''; }
    set id(value) { this.attributes.id = value; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    removeAttribute(key) { delete this.attributes[key]; }
    getAttribute(key) { return this.attributes[key] ?? null; }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    append(...children) { for (const child of children) this.appendChild(child); }
    remove() {
        if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(x => x !== this);
        this.parentNode = null;
    }
    matches(selector) {
        if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
        if (selector === '[hidden]') return Object.hasOwn(this.attributes, 'hidden');
        if (selector === '[id]') return Object.hasOwn(this.attributes, 'id');
        if (selector === '[id^="inspection-"]') return this.id.startsWith('inspection-');
        if (/^[a-z]+$/.test(selector)) return this.tagName === selector.toUpperCase();
        throw new Error('Unsupported test DOM selector: ' + selector);
    }
    querySelectorAll(selector) {
        const choices = selector.split(',').map(x => x.trim()), found = [];
        const visit = node => { for (const child of node.children) {
            if (choices.some(choice => child.matches(choice))) found.push(child); visit(child);
        }};
        visit(this); return found;
    }
    cloneNode(deep) {
        const clone = new Element(this.tagName, this.attributes, this.textContent);
        Object.assign(clone, {innerHTML: this.innerHTML, value: this.value, open: this.open, style: {...this.style}});
        if (deep) for (const child of this.children) clone.appendChild(child.cloneNode(true));
        return clone;
    }
    allText() { return [this.textContent, this.innerHTML, ...this.children.map(x => x.allText())].join(' '); }
}

function makeDocument() {
    const elements = new Map();
    return {
        elements,
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, new Element('div', {id}));
            return elements.get(id);
        },
        createElement: tag => new Element(tag),
        querySelectorAll: () => []
    };
}
const plain = value => JSON.parse(JSON.stringify(value));
const stamp = new Date(Date.now() - 3_600_000).toISOString();
function property() {
    const p = {
        id: 'qa-report-101', address: '101 Example St', city: 'Erie', state: 'PA', zip: '16501',
        price: 150000, last_source_check: stamp, url: 'https://example.test/listing',
        source_type: 'mls', type: 'Single Family', beds: 3, baths: 2, sqft: 1600,
        financial_targets: {confirmed: true, cap_rate_pct: 8, flip_profit_amount: 30000},
        financial_evidence: {}
    };
    const entries = {
        rent: [1300, 'lease', 'monthly'], arv: [260000, 'appraisal_after_repair', 'total'],
        flipRehab: [30000, 'contractor_quote', 'total'], rentalRehab: [20000, 'contractor_quote', 'total'],
        operatingExpenses: [4000, 'operating_budget', 'annual'], vacancyLoss: [1000, 'operating_budget', 'annual'],
        acquisitionCosts: [5000, 'closing_estimate', 'total'], sellingCosts: [10000, 'selling_quote', 'total'],
        holdingCosts: [3000, 'holding_budget', 'total'], financingCosts: [2000, 'lender_quote', 'total']
    };
    for (const [key, [value, method, period]] of Object.entries(entries)) p.financial_evidence[key] = {
        value, method, period, property_id: p.id, source_name: 'Offline test document',
        document_ref: 'QA-document-' + key, observed_at: stamp, status: 'documented', property_match_confirmed: true
    };
    return p;
}
function marketReport(p = property()) {
    return {
        property_id: p.id, property: {address: p.address, city: p.city, state: p.state, zip: p.zip},
        sources: {value: {
            status: 'accepted', observed_at: stamp, estimate: {value: 185000, range_low: 175000, range_high: 195000},
            subject: {addressLine1: p.address, city: p.city, state: p.state, zipCode: p.zip,
                propertyType: p.type, bedrooms: p.beds, bathrooms: p.baths, squareFootage: p.sqft},
            quality: {minimum_comparables: 3}, comparables: [{}, {}, {}]
        }}
    };
}

function harness(p = property()) {
    const document = makeDocument(), writes = [], mounted = [], editability = [];
    let latest = plain(p), writeError = null;
    const storage = new Map();
    const context = vm.createContext({
        console, URL, document, currentDeal: plain(p), currentSavedReportId: null,
        cloudAvailable: true, reportSavePending: false, reportReady: true,
        copy: plain, referenceTime: () => Date.now(),
        EVIDENCE_LABELS: {arv: 'ARV', rent: 'Rent', flipRehab: 'Flip rehabilitation'},
        window: {PARehabReport: {mount: (root, options) => mounted.push({root, options})}},
        localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
        matchedCountyRecord: () => null, validInspection: () => null,
        renderComparables: () => {}, renderInspection: () => {}, renderInternalData: () => {}, renderAvailability: () => {},
        renderImages: () => {}, renderSourceLinks: () => {},
        setEditability: () => editability.push(context.reportSavePending),
        latestCloudDeal: async () => plain(latest),
        supabaseClient: {from: table => {
            assert.equal(table, 'pipeline_deals');
            return {upsert: async value => { writes.push(plain(value)); return {error: writeError}; }};
        }}
    });
    const financialStart = source.indexOf('const FINANCIAL_MODEL_VERSION');
    const financialEnd = source.indexOf('let currentDeal=', financialStart);
    assert.ok(financialStart >= 0 && financialEnd > financialStart, 'Actual financial implementation is present');
    vm.runInContext(source.slice(financialStart, financialEnd), context);
    for (const name of ['escapeHtml', 'put', 'addressNorm', 'propertyType', 'sameProperty', 'sourceAccepted',
        'displayMetric', 'moneyOrMissing', 'renderReport', 'renderRehabPlan', 'cacheLocally',
        'commitCurrentDeal', 'buildPrintableReport']) vm.runInContext(sourceFunction(name), context);
    return {context, document, writes, mounted, editability, storage,
        setLatest(value) { latest = plain(value); }, setWriteError(value) { writeError = value; }};
}

const tests = [];
function test(name, fn) { tests.push({name, fn}); }

test('Accepted RentCast current-market estimate renders separately from documented ARV', () => {
    const p = property(); p.expanded_source_report = marketReport(p);
    const h = harness(p); h.context.renderReport();
    assert.equal(h.document.getElementById('rep-market-value').textContent, '$185,000');
    assert.equal(h.document.getElementById('rep-arv').textContent, '$260,000');
    assert.equal(h.document.getElementById('calc-arv-value').textContent, '$260,000');
    assert.equal(h.context.financialSnapshot(h.context.currentDeal).arv, 260000);
});

test('A documented ARV does not fill in a missing current-market estimate', () => {
    const h = harness(); h.context.renderReport();
    assert.equal(h.document.getElementById('rep-arv').textContent, '$260,000');
    assert.equal(h.document.getElementById('rep-market-value').textContent, 'חסרים נתונים');
    assert.equal(h.document.getElementById('rep-market-value').classList.contains('unavailable'), true);
});

for (const [name, change] of [
    ['rejected provider status', p => { p.expanded_source_report.sources.value.status = 'insufficient_comparables'; }],
    ['stale provider estimate', p => { p.expanded_source_report.sources.value.observed_at = new Date(Date.now() - 61 * 86400000).toISOString(); }],
    ['another property subject', p => { p.expanded_source_report.sources.value.subject.addressLine1 = '999 Wrong Ave'; }]
]) test('Current-market estimate remains missing for ' + name, () => {
    const p = property(); p.expanded_source_report = marketReport(p); change(p);
    const h = harness(p); h.context.renderReport();
    assert.equal(h.document.getElementById('rep-market-value').classList.contains('unavailable'), true);
    assert.equal(h.document.getElementById('rep-arv').textContent, '$260,000');
});

test('Saving a rehabilitation plan merges latest server fields and preserves verified financials', async () => {
    const p = property(), h = harness(p), server = plain(p);
    server.owner_notes = 'A different user changed this on the server';
    server.custom_images = ['https://example.test/latest-photo.jpg'];
    server.financial_evidence.rent.value = 1450;
    server.financial_evidence.rent.document_ref = 'QA-latest-server-lease';
    h.context.applyFinancialSnapshot(server);
    const beforeSnapshot = plain(h.context.financialSnapshot(server));
    h.setLatest(server);
    const next = plain(p);
    next.rehab_plan = {version: 1, property_id: p.id, contingency_percent: 10,
        sections: [{id: 'interior', items: [{id: 'paint', description: 'Planning estimate', low: 2500, high: 5000}]}]};
    await h.context.commitCurrentDeal(next);
    assert.equal(h.writes.length, 1);
    const saved = h.writes[0].deal_data;
    assert.deepEqual(saved.rehab_plan, next.rehab_plan);
    assert.equal(saved.owner_notes, server.owner_notes);
    assert.deepEqual(saved.custom_images, server.custom_images);
    assert.deepEqual(saved.financial_evidence, server.financial_evidence);
    assert.deepEqual(plain(h.context.financialSnapshot(saved)), beforeSnapshot);
    assert.deepEqual(saved.analysis_inputs, plain(server.analysis_inputs));
    assert.equal(saved.flip_rehab, 30000);
    assert.equal(saved.monthly_rent_est, 1450);
    assert.equal(h.context.reportSavePending, false);
    assert.deepEqual(JSON.parse(h.storage.get('pa_pipeline_deals'))[0].rehab_plan, next.rehab_plan);
});

test('Saved reports reject plan writes before reading or writing cloud data', async () => {
    const h = harness(); h.context.currentSavedReportId = 'saved-report-1';
    h.context.latestCloudDeal = async () => { assert.fail('A saved report must not read for write'); };
    const next = plain(h.context.currentDeal); next.rehab_plan = {property_id: next.id};
    await assert.rejects(h.context.commitCurrentDeal(next), /מצב צפייה/);
    assert.equal(h.writes.length, 0);
    assert.equal(h.context.currentDeal.rehab_plan, undefined);
});

test('The rehabilitation adapter passes read-only state and rejects another property plan', async () => {
    const h = harness(); h.context.currentSavedReportId = 'saved-report-1';
    h.context.renderRehabPlan();
    const options = h.mounted.at(-1).options;
    assert.equal(options.readOnly, true);
    await assert.rejects(options.onSave({property_id: 'another-property'}), /לנכס אחר/);
    await assert.rejects(options.onSave({property_id: h.context.currentDeal.id}), /מצב צפייה/);
    assert.equal(h.writes.length, 0);
});

test('The rehabilitation adapter saves the same-property plan through the real merge', async () => {
    const h = harness(); h.context.renderRehabPlan();
    const options = h.mounted.at(-1).options, plan = {property_id: h.context.currentDeal.id, notes: 'Offline plan'};
    assert.equal(options.readOnly, false);
    assert.equal(options.documentedTotals.flip.value, 30000);
    await options.onSave(plan);
    assert.deepEqual(h.writes[0].deal_data.rehab_plan, plan);
});

test('Cloud identity change rejects the plan and releases the saving lock', async () => {
    const h = harness(), latest = property(); latest.address = '999 Different Property'; h.setLatest(latest);
    const next = plain(h.context.currentDeal); next.rehab_plan = {property_id: next.id};
    await assert.rejects(h.context.commitCurrentDeal(next), /זיהוי הנכס השתנה/);
    assert.equal(h.writes.length, 0); assert.equal(h.context.reportSavePending, false);
});

test('Cloud write failure keeps the existing plan and releases the saving lock', async () => {
    const p = property(); p.rehab_plan = {property_id: p.id, notes: 'Existing plan'};
    const h = harness(p); h.setWriteError({message: 'Offline test refusal'});
    const next = plain(p); next.rehab_plan.notes = 'Unsaved edit';
    await assert.rejects(h.context.commitCurrentDeal(next), /Offline test refusal/);
    assert.deepEqual(h.context.currentDeal.rehab_plan, p.rehab_plan);
    assert.equal(h.context.reportSavePending, false);
    assert.equal(h.storage.has('pa_pipeline_deals'), false);
});

test('Printable report keeps static plan costs and strips private fields without mutating the live report', () => {
    const h = harness(), overview = h.document.getElementById('tab-overview');
    overview.appendChild(new Element('p', {}, 'Current-market estimate $185,000; after-repair value $260,000'));
    const financials = h.document.getElementById('tab-financials');
    const privatePanel = new Element('section', {class: 'no-print'});
    const keyInput = new Element('input', {type: 'password'}); keyInput.value = 'FAKE-TEST-KEY-NEVER-REAL';
    privatePanel.appendChild(keyInput); financials.appendChild(privatePanel);
    const rehab = h.document.getElementById('tab-rehab'); rehab.classList.add('hidden');
    const plan = new Element('section', {id: 'rehab-report-root'});
    const cost = new Element('span', {class: 'rehab-static-cost'}, '$2,500 – $5,000');
    const input = new Element('input', {id: 'plan-cost-input'}); input.value = '2500';
    const notes = new Element('textarea'); notes.value = 'Unsaved private edit';
    const details = new Element('details'); details.appendChild(new Element('p', {}, 'Budget assumptions'));
    const hidden = new Element('p', {hidden: ''}, 'Hidden implementation content');
    plan.append(cost, input, notes, details, hidden); rehab.appendChild(plan);
    h.document.getElementById('property-source-links').appendChild(new Element('a', {href: 'https://example.test/listing'}, 'Listing source'));
    const result = h.context.buildPrintableReport();
    assert.ok(result.allText().includes('$2,500 – $5,000'));
    assert.ok(result.allText().includes('Current-market estimate $185,000; after-repair value $260,000'));
    assert.ok(result.allText().includes('Budget assumptions'));
    assert.ok(result.allText().includes('Listing source'));
    assert.equal(result.querySelectorAll('input,select,textarea,button,.no-print,[hidden]').length, 0);
    assert.equal(result.querySelectorAll('[id]').length, 0);
    assert.equal(result.querySelectorAll('details')[0].open, true);
    assert.equal(result.querySelectorAll('.hidden').length, 0);
    assert.ok(!result.allText().includes('FAKE-TEST-KEY-NEVER-REAL'));
    assert.ok(!result.allText().includes('Unsaved private edit'));
    assert.ok(!result.allText().includes('Hidden implementation content'));
    assert.equal(rehab.classList.contains('hidden'), true);
    assert.equal(input.parentNode, plan);
    assert.equal(keyInput.value, 'FAKE-TEST-KEY-NEVER-REAL');
    assert.equal(details.open, false);
});

for (const fail of [false, true]) test('PDF export restores RTL, unlocks controls and removes temporary content after ' + (fail ? 'failure' : 'success'), async () => {
    const h = harness(), statuses = [];
    h.document.body = new Element('body'); h.document.body.style.direction = 'rtl';
    h.document.documentElement = {dir: 'rtl'};
    h.context.pdfExportPending = false; h.context.reportImagesReady = Promise.resolve();
    h.context.reportRequestPending = false;
    vm.runInContext(sourceFunction('setEditability'), h.context);
    h.context.preparePrintableText = () => {};
    h.context.renderReport = () => {};
    h.context.statusText = (id, message) => statuses.push(message);
    h.context.buildPrintableReport = () => new Element('div');
    const capture = new Element('div'); capture.parentElement = new Element('div');
    let options;
    const worker = {
        set(value) { options = value; return this; }, from() { return this; },
        toContainer() { return this; }, get(key, callback) { assert.equal(key, 'container'); callback(capture); return this; },
        async save() {
            assert.equal(h.document.documentElement.dir, 'ltr');
            assert.equal(capture.style.left, '0'); assert.equal(capture.style.right, 'auto');
            assert.equal(capture.style.direction, 'ltr');
            assert.equal(capture.parentElement.style.width, '1000px');
            assert.equal(options.html2canvas.scrollX, 0);
            assert.equal(options.html2canvas.scrollY, 0);
            if (fail) throw new Error('Simulated canvas failure');
        }
    };
    h.context.window.html2pdf = () => worker;
    vm.runInContext(sourceFunction('generateCleanPDF'), h.context);
    await h.context.generateCleanPDF();
    assert.equal(h.document.documentElement.dir, 'rtl');
    assert.equal(h.document.body.style.direction, 'rtl');
    assert.equal(h.document.body.children.length, 0);
    assert.equal(h.context.pdfExportPending, false);
    assert.equal(h.document.getElementById('btn-download-pdf').disabled, false);
    assert.ok(statuses.some(message => fail ? message.includes('Simulated canvas failure') : message.includes('נוצר')));
});

(async () => {
    let passed = 0;
    for (const {name, fn} of tests) {
        try { await fn(); passed++; console.log('PASS ' + name); }
        catch (error) { console.error('FAIL ' + name); console.error(error); process.exitCode = 1; }
    }
    console.log(`${passed}/${tests.length} expanded-report integration checks passed.`);
})();
