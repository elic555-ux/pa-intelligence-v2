const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
const start = html.indexOf('async function loadCompletionStatus()');
const end = html.indexOf('\n        }', start);
assert.ok(start >= 0 && end > start);
const actual = html.slice(start, end + '\n        }'.length);
const panel = {textContent: ''};
let data, ok = true, calls = 0;
const context = vm.createContext({
    document: {getElementById: id => id === 'completion-source-status' ? panel : null},
    scannerDateLabel: value => value,
    fetch: async (url, options) => {
        calls++;
        assert.match(url, /^COMPS_REPORTS\/automatic_listing_sources\/status.json\?t=/);
        assert.equal(options.cache, 'no-store');
        return {ok, json: async () => data};
    }
});
vm.runInContext(actual, context);
(async () => {
    data = {pending: 102, new_snapshots: 3, review_required: 12, incomplete_properties: 0,
            checked_at: '2026-10-14T18:00:00Z'};
    await context.loadCompletionStatus();
    assert.match(panel.textContent, /תהליך עצמאי/);
    assert.match(panel.textContent, /ממתינים 102/);
    assert.match(panel.textContent, /2026-10-14T18:00:00Z/);
    data = {...data, pending: '<script>bad</script>'};
    await context.loadCompletionStatus();
    assert.match(panel.textContent, /אינו זמין/);
    assert.doesNotMatch(panel.textContent, /<script>/);
    ok = false;
    await context.loadCompletionStatus();
    assert.match(panel.textContent, /אינו זמין/);
    assert.equal(calls, 3);
    assert.match(html, /השלמת הפרטים נשלטת בנפרד/);
    console.log('PASS independent completion status renders, validates counts and handles unavailable reports');
})().catch(error => {console.error(error); process.exitCode = 1;});
