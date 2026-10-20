'use strict';
const assert = require('node:assert/strict');
const rehab = require('./rehab-report.js');
const subject = 'qa-property-1';
const empty = () => rehab.normalizePlan(null, subject);
const test = (name, run) => { run(); process.stdout.write('PASS ' + name + '\n'); };

test('24 specific items in three groups; unknown prices stay null', () => {
  assert.equal(rehab.ITEMS.length, 24);
  assert.deepEqual(rehab.GROUPS.map(group => group.items.length), [8, 7, 9]);
  const plan = empty();
  assert.equal(Object.keys(plan.items).length, 24);
  assert.ok(Object.values(plan.items).every(item => item.cost === null && item.condition === 'unassessed' && item.scope === ''));
  const summary = rehab.summarize(plan);
  assert.equal(summary.priced_count, 0);
  assert.equal(summary.active_count, 24);
  assert.equal(summary.complete, false);
  assert.match(rehab.renderHTML({ property: { id: subject }, plan }), /טרם תומחר/);
});

test('explicit zero counts as priced; a blank does not', () => {
  const plan = empty();
  plan.items.roof.cost = '0';
  plan.items.kitchen.cost = '';
  const normalized = rehab.normalizePlan(plan, subject);
  assert.equal(normalized.items.roof.cost, 0);
  assert.equal(normalized.items.kitchen.cost, null);
  assert.equal(rehab.summarize(plan).priced_count, 1);
  assert.equal(rehab.summarize(plan).subtotal, 0);
});

test('negative, nonfinite, malformed, over-limit and over-precision prices rejected', () => {
  for (const bad of [-1, Infinity, NaN, true, {}, [], '-3', '1e3', '0x10', '1,200', '$300', '12oops', '4.333', 100000001]) {
    const plan = empty(); plan.items.roof.cost = bad;
    const normalized = rehab.normalizePlan(plan, subject);
    assert.equal(normalized.items.roof.cost, null, String(bad));
    assert.ok(normalized.validation_errors.roof, String(bad));
    assert.equal(rehab.summarize(plan).priced_count, 0);
  }
  const plan = empty(); plan.items.roof.cost = ' 123.45 ';
  assert.equal(rehab.normalizePlan(plan, subject).items.roof.cost, 123.45);
});

test('excluded items keep entered data but do not count or inflate budget', () => {
  const plan = empty();
  plan.items.roof = { cost: 9000, condition: 'not_applicable', scope: 'retained for a later review' };
  plan.items.kitchen.cost = 100;
  const summary = rehab.summarize(plan);
  assert.equal(summary.subtotal, 100);
  assert.equal(summary.priced_count, 1);
  assert.equal(summary.active_count, 23);
  assert.equal(summary.excluded_count, 1);
  assert.equal(rehab.normalizePlan(plan, subject).items.roof.cost, 9000);
});

test('subtotal, group sums, reserve and rounding agree without double counting', () => {
  const plan = empty();
  plan.items.roof.cost = 100.1;
  plan.items.kitchen.cost = 200.2;
  plan.items.walls.cost = 10;
  plan.items.windows.cost = 50;
  plan.contingency_pct = 12.5;
  const summary = rehab.summarize(plan);
  assert.deepEqual(summary.groups.map(group => group.subtotal), [300.3, 10, 50]);
  assert.equal(summary.subtotal, 360.3);
  assert.equal(summary.contingency_amount, 45.04);
  assert.equal(summary.total, 405.34);
  assert.equal(summary.priced_count, 4);
  assert.equal(summary.complete, false);
});

test('reserve is optional; invalid percentages are visible validation errors', () => {
  for (const value of ['', null, undefined]) {
    const plan = empty(); plan.contingency_pct = value;
    assert.equal(rehab.normalizePlan(plan, subject).contingency_pct, 0);
    assert.equal(rehab.normalizePlan(plan, subject).validation_errors.contingency_pct, undefined);
  }
  for (const value of [-1, 101, Infinity, 'oops', '1e1', true]) {
    const plan = empty(); plan.contingency_pct = value;
    assert.ok(rehab.normalizePlan(plan, subject).validation_errors.contingency_pct);
  }
});

test('completeness includes zero; all-excluded is not a completed cost estimate', () => {
  const plan = empty();
  Object.values(plan.items).forEach(item => { item.cost = 0; });
  assert.equal(rehab.summarize(plan).complete, true);
  plan.items.roof.cost = null;
  assert.equal(rehab.summarize(plan).complete, false);
  plan.items.roof.condition = 'not_applicable';
  assert.equal(rehab.summarize(plan).complete, true);
  Object.values(plan.items).forEach(item => { item.condition = 'not_applicable'; });
  assert.equal(rehab.summarize(plan).complete, false);
});

test('wrong property and prototype keys never carry data into another report', () => {
  const foreign = { property_id: 'different-property', items: { roof: { cost: 50000, scope: 'foreign', condition: 'repair' } }, contingency_pct: 15 };
  const plan = rehab.normalizePlan(foreign, subject);
  assert.equal(plan.items.roof.cost, null);
  assert.equal(plan.items.roof.scope, '');
  assert.equal(plan.contingency_pct, 0);
  const withPrototype = { property_id: subject, items: Object.create({ roof: { cost: 99 } }) };
  assert.equal(rehab.normalizePlan(withPrototype, subject).items.roof.cost, null);
  assert.equal(rehab.normalizePlan({ property_id: '', items: { roof: { cost: 99 } } }, '').items.roof.cost, null);
});

test('HTML escapes property, work scope, inspection and documented source fields', () => {
  const plan = empty();
  const attack = '</textarea><script>alert(1)</script><img src=x onerror="alert(2)">';
  plan.items.roof.scope = attack;
  const html = rehab.renderHTML({ property: { id: subject, address: attack }, plan,
    inspection: { source_name: attack, document_ref: attack, findings: { main: attack } },
    documentedTotals: { flip: { value: 1200, source_name: attack } } });
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<img '));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('&quot;alert(2)&quot;'));
  assert.equal((html.match(/data-item=/g) || []).length, 24);
});

test('read-only output has no editors; editable output has static print counterparts', () => {
  const plan = empty(); plan.items.roof.cost = 15;
  const readonly = rehab.renderHTML({ property: { id: subject }, plan, readOnly: true });
  assert.ok(!/<(?:input|textarea|select)\b/.test(readonly));
  assert.ok(!readonly.includes('rh-save-button'));
  const editable = rehab.renderHTML({ property: { id: subject }, plan });
  assert.ok(editable.includes('rh-static rh-cost-value'));
  assert.ok(editable.includes('rh-static rh-condition-value'));
  assert.ok(editable.includes('rh-static rh-scope-value'));
  assert.ok(editable.includes('אינה מעדכנת תחשיבי השקעה'));
});

test('documented source totals never change the manual estimate', () => {
  const plan = empty(); plan.items.roof.cost = 100;
  const before = rehab.summarize(plan);
  const html = rehab.renderHTML({ property: { id: subject }, plan, documentedTotals: { flip: { value: 45000, source_name: 'Contractor' }, rental: { value: 22000, source_name: 'Quote' } } });
  assert.ok(html.includes('$45,000'));
  assert.ok(html.includes('$22,000'));
  assert.deepEqual(rehab.summarize(plan), before);
  assert.equal(before.subtotal, 100);
});

test('input plan is not mutated and arbitrary fields are not persisted', () => {
  const plan = { property_id: subject, items: { roof: { cost: '25', condition: 'repair', scope: 'Inspect flashing', html: '<b>' }, extra: { cost: 90 } }, secret: 'not part of schema' };
  const before = JSON.stringify(plan);
  const normalized = rehab.normalizePlan(plan, subject);
  assert.equal(JSON.stringify(plan), before);
  assert.equal(normalized.secret, undefined);
  assert.equal(normalized.items.extra, undefined);
  assert.equal(normalized.items.roof.html, undefined);
});

// A small event/DOM adapter exercises the real mount and save handlers without a browser or network.
function node() {
  return { textContent: '', innerHTML: '', disabled: false, attributes: {}, classList: { toggle() {} },
    setAttribute(key, value) { this.attributes[key] = value; }, getAttribute(key) { return this.attributes[key]; } };
}
function host() {
  const root = node();
  const nodes = new Map();
  root.listeners = {};
  root.addEventListener = (name, listener) => { root.listeners[name] = listener; };
  root.removeEventListener = name => { delete root.listeners[name]; };
  root.querySelector = selector => {
    if (!nodes.has(selector)) {
      const item = node();
      const fields = new Map();
      item.querySelector = field => { if (!fields.has(field)) fields.set(field, node()); return fields.get(field); };
      nodes.set(selector, item);
    }
    return nodes.get(selector);
  };
  root.querySelectorAll = () => [];
  root.edit = (id, field, value) => {
    const row = { getAttribute: () => id };
    const target = { value, getAttribute: () => field, closest: () => row };
    root.listeners.input({ target });
  };
  root.save = () => root.listeners.click({ target: { closest: () => root.querySelector('.rh-save-button') } });
  return root;
}
const asyncTest = async (name, run) => { await run(); process.stdout.write('PASS ' + name + '\n'); };
(async () => {
  await asyncTest('mount preserves unsaved inputs on repeated same-property render and resets other property', async () => {
    const element = host();
    const options = { property: { id: subject }, plan: empty(), onSave: async () => {} };
    const instance = rehab.mount(element, options);
    element.edit('roof', 'cost', '123.45');
    assert.equal(instance.isDirty(), true);
    assert.equal(rehab.mount(element, options), instance);
    assert.equal(instance.getPlan().items.roof.cost, 123.45);
    instance.update({ ...options, property: { id: 'another' } });
    assert.equal(instance.isDirty(), false);
    assert.equal(instance.getPlan().items.roof.cost, null);
    instance.destroy();
    assert.equal(element.listeners.input, undefined);
  });
  await asyncTest('save rejects invalid raw draft and permits correction plus explicit zero', async () => {
    const element = host(); let saves = 0; let saved;
    const instance = rehab.mount(element, { property: { id: subject }, plan: empty(), onSave: async plan => { saves++; saved = plan; } });
    element.edit('roof', 'cost', '-4');
    await element.save();
    assert.equal(saves, 0);
    assert.match(element.querySelector('.rh-save-status').textContent, /לתקן/);
    element.edit('roof', 'cost', '0');
    await element.save();
    assert.equal(saves, 1);
    assert.equal(saved.items.roof.cost, 0);
    assert.equal(saved.validation_errors, undefined);
    assert.ok(saved.updated_at);
    assert.equal(instance.isDirty(), false);
  });
  await asyncTest('pending save survives parent readOnly rerenders and cannot be double submitted', async () => {
    const element = host(); let release; let saves = 0;
    const wait = new Promise(resolve => { release = resolve; });
    const options = { property: { id: subject }, plan: empty(), onSave: async () => { saves++; await wait; } };
    const instance = rehab.mount(element, options);
    element.edit('roof', 'cost', '200');
    const pending = element.save();
    instance.update({ ...options, readOnly: true });
    await element.save();
    assert.equal(saves, 1);
    instance.update({ ...options, readOnly: false });
    release(); await pending;
    assert.equal(instance.getPlan().items.roof.cost, 200);
    assert.equal(instance.isDirty(), false);
    assert.match(element.innerHTML, /rh-save-button/);
  });
  await asyncTest('failed save keeps draft and allows retry; read-only events do nothing', async () => {
    const element = host(); let fail = true;
    const options = { property: { id: subject }, plan: empty(), onSave: async () => { if (fail) throw new Error('offline'); } };
    const instance = rehab.mount(element, options);
    element.edit('roof', 'cost', '300');
    await element.save();
    assert.equal(instance.isDirty(), true);
    assert.equal(instance.getPlan().items.roof.cost, 300);
    assert.match(element.querySelector('.rh-save-status').textContent, /offline/);
    fail = false; await element.save();
    assert.equal(instance.isDirty(), false);
    instance.update({ ...options, readOnly: true });
    element.edit('roof', 'cost', '999');
    assert.equal(instance.getPlan().items.roof.cost, null); // new clean options restored the saved snapshot
    assert.equal(instance.isDirty(), false);
  });
  await asyncTest('late save response does not overwrite a newly selected property or its later return', async () => {
    const element = host(); let release;
    const wait = new Promise(resolve => { release = resolve; });
    const options = { property: { id: subject }, plan: empty(), onSave: async () => wait };
    const instance = rehab.mount(element, options);
    element.edit('roof', 'cost', '200');
    const pending = element.save();
    instance.update({ ...options, property: { id: 'another' } });
    instance.update(options);
    element.edit('roof', 'cost', '400');
    release(); await pending;
    assert.equal(instance.getPlan().items.roof.cost, 400);
    assert.equal(instance.isDirty(), true);
  });
  process.stdout.write('17 rehab report test groups passed. No network calls.\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
