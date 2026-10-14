/* Real scheduling controls; simulated DOM only, no requests or saves. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, {value: '', checked: false, disabled: false, dataset: {}});
    return elements.get(id);
};
function extract(name) {
    const start = html.indexOf('function ' + name + '(');
    assert.ok(start >= 0, name + ' must exist');
    const end = html.indexOf('\n        }', start);
    assert.ok(end > start, name + ' must close');
    return html.slice(start, end + '\n        }'.length);
}
const context = vm.createContext({
    document: {getElementById: element},
    AVAILABLE_SCAN_SECTORS: new Set(['mls']),
    updateScheduleFields() {},
    scanRangeNumber: (value, fallback) => value === 'all' ? fallback :
        value === null || value === undefined ? null : Number(value),
    scanAreaSelected: () => true,
    fetch: () => { throw new Error('Network is forbidden'); },
    localStorage: {setItem: () => { throw new Error('Storage writes are forbidden'); }}
});
vm.runInContext(['updateCompletionFields', 'readCompletionControls', 'applyScheduleControls',
                 'scanConfigurationErrors'].map(extract).join('\n'), context);
const profile = {scheduleControlVersion: 2, autoScanEnabled: true, sectors: ['mls'],
    scheduleTimezone: 'America/New_York', schedules: {},
    counties: ['Allegheny'], cities: [], minPrice: 50000, maxPrice: 190000,
    minSqft: 700, maxSqft: 2150, minBeds: 1, maxBeds: 3, minBaths: 'all',
    completionSettings: {enabled: true, frequency: 'every_15_minutes', batchSize: 3,
                         dailyLimit: 60, startDate: '2026-09-12', times: ['08:00']}};
let passed = 0;
function check(name, callback) { callback(); passed++; console.log('PASS', name); }
check('all inline application scripts parse', () => {
    for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi))
        new vm.Script(match[1]);
});
check('quarter-hour and all three batch sizes are offered', () => {
    assert.match(html, /id="completion-frequency"[^>]*>[\s\S]*?value="every_15_minutes"/);
    const select = html.match(/<select id="completion-batch-size"[^>]*>([\s\S]*?)<\/select>/)[1];
    assert.deepEqual([...select.matchAll(/value="(\d+)"/g)].map(m => Number(m[1])), [3, 4, 5]);
});
check('loading and reading preserve each selected batch size and interval', () => {
    const before = JSON.stringify(profile);
    for (const size of [3, 4, 5]) {
        context.applyScheduleControls({...profile, completionSettings: {...profile.completionSettings, batchSize: size}});
        const result = context.readCompletionControls();
        assert.equal(result.batchSize, size);
        assert.equal(result.frequency, 'every_15_minutes');
        assert.equal(result.dailyLimit, 60);
        assert.equal(result.timezone, 'America/New_York');
        assert.equal(result.startDate, '2026-09-12');
        assert.equal(element('completion-time').disabled, true);
        assert.equal(element('completion-time2').disabled, true);
    }
    assert.equal(JSON.stringify(profile), before);
});
check('legacy ten and missing size display conservative three without changing the profile', () => {
    for (const size of [10, undefined]) {
        const input = {...profile, completionSettings: {...profile.completionSettings, batchSize: size}};
        const before = JSON.stringify(input);
        context.applyScheduleControls(input);
        assert.equal(context.readCompletionControls().batchSize, 3);
        assert.equal(JSON.stringify(input), before);
    }
});
check('daily and twice-daily schedules keep their original time controls', () => {
    for (const frequency of ['daily', 'twice_daily']) {
        context.applyScheduleControls({...profile, completionSettings: {
            ...profile.completionSettings, frequency, times: ['08:00', '20:00']}});
        const result = context.readCompletionControls();
        assert.equal(element('completion-time').disabled, false);
        assert.equal(element('completion-time2').disabled, frequency !== 'twice_daily');
        assert.deepEqual(Array.from(result.times), frequency === 'twice_daily' ? ['08:00', '20:00'] : ['08:00']);
    }
});
check('save validation accepts supported settings and rejects unsafe values', () => {
    assert.equal(context.scanConfigurationErrors(profile).length, 0);
    for (const batchSize of [0, 2, 6, 10, '3', null])
        assert.ok(context.scanConfigurationErrors({...profile,
            completionSettings: {...profile.completionSettings, batchSize}}).length > 0);
    assert.ok(context.scanConfigurationErrors({...profile, completionSettings: {
        ...profile.completionSettings, frequency: 'every_minute'}}).length > 0);
});
check('pause remains paused through control loading and reading', () => {
    context.applyScheduleControls({...profile, completionSettings: {
        ...profile.completionSettings, enabled: false}});
    assert.equal(context.readCompletionControls().enabled, false);
});
console.log(passed + ' completion control checks passed');
