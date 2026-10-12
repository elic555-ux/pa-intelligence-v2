"""Offline guard for the UI configuration consumed by the central scanner."""

import ast
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parent


UI_GUARD = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const clone = value => JSON.parse(JSON.stringify(value));
const encode = value => Buffer.from(JSON.stringify(value, null, 2), 'utf8').toString('base64');
const server = {
    'scan_config.json': {profile: clone(input.scheduled), sha: 'scheduled-original'},
    'manual_scan_config.json': {profile: clone(input.manual), sha: 'manual-original'}
};
const original = clone(server);
const calls = [], confirmed = [], errors = [], monitors = [], alerts = [];
const button = {innerHTML: 'start scan'};
let closed = false, manualBusy = false, scheduledBusy = false, verified = false;
let releaseReadback;
const readbackGate = new Promise(resolve => { releaseReadback = resolve; });
const response = (status, body) => ({ok: status >= 200 && status < 300, status,
    json: async () => clone(body), text: async () => 'simulated server failure'});
const context = vm.createContext({
    console: {error() {}},
    window: {crypto: {randomUUID: () => 'guard-request-id'}},
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    escape, unescape,
    manualScanSaveInProgress: false, scheduledScanSaveInProgress: false,
    document: {getElementById: id => id === 'btn-trigger-scan' ? button : {value: 'offline-test-token'}},
    localStorage: {
        getItem: key => { assert.equal(key, 'pa_github_token'); return 'offline-test-token'; },
        setItem: (key, value) => {
            assert.equal(key, 'pa_github_token', 'scan parameters must stay on the server');
            assert.equal(value, 'offline-test-token');
        }
    },
    lucide: {createIcons() {}},
    validatedScanConfig: manual => clone(manual ? input.manual : input.scheduled),
    cleanScanSettings: clone,
    scanProfileFileName: prefix => prefix === 'ondemand' ? 'manual_scan_config.json' : 'scan_config.json',
    setScanSaveStatus() {}, scanSaveReceiptMessage: () => 'verified server save',
    showScanSaveError: (prefix, message) => errors.push({prefix, message}),
    cacheConfirmedScanProfile: (prefix, profile) => confirmed.push({prefix, profile: clone(profile)}),
    applyScanProfileToForm() {}, applyConfirmedScheduledState() {}, applyBoardScanPriceRange() {},
    setManualScanSaving: busy => { manualBusy = busy; context.manualScanSaveInProgress = busy; },
    setScheduledScanSaving: busy => { scheduledBusy = busy; context.scheduledScanSaveInProgress = busy; },
    closeOnDemandScanModal: () => { closed = true; },
    startBackgroundScanMonitor: requestId => monitors.push(requestId),
    alert: message => alerts.push(message),
    fetch: async (url, options = {}) => {
        const method = options.method || 'GET';
        calls.push({url, method});
        if (url.endsWith('/dispatches')) {
            assert.equal(method, 'POST');
            assert.equal(verified, true, 'dispatch must wait for server readback');
            assert.equal(JSON.parse(options.body).ref, 'main');
            if (input.failure === 'dispatch-network') throw new Error('simulated dispatch failure');
            return response(input.failure === 'dispatch-http' ? 403 : 204, {});
        }
        const name = url.split('/contents/')[1]?.split('?')[0];
        assert.ok(server[name], 'unexpected endpoint: ' + url);
        const entry = server[name];
        if (method === 'PUT') {
            assert.equal(JSON.parse(options.body).sha, original[name].sha);
            if (input.failure === 'write-network') throw new Error('simulated write failure');
            if (input.failure === 'write-http') return response(403, {message: 'forbidden'});
            if (input.failure === 'write-conflict') return response(409, {message: 'conflict'});
            const body = JSON.parse(options.body);
            assert.equal(body.branch, 'main');
            entry.profile = JSON.parse(Buffer.from(body.content, 'base64').toString('utf8'));
            entry.sha = 'saved-profile-sha';
            return response(200, {content: input.failure === 'missing-save-sha' ? {} : {sha: entry.sha}});
        }
        assert.equal(method, 'GET');
        assert.equal(options.cache, 'no-store');
        if (entry.sha === original[name].sha) {
            if (input.failure === 'initial-read') return response(401, {message: 'unauthorized'});
        } else {
            if (input.mode === 'wait-for-readback') await readbackGate;
            if (input.failure === 'readback-network') throw new Error('simulated readback failure');
            if (input.failure === 'readback-http') return response(500, {message: 'unavailable'});
            verified = true;
        }
        const body = {sha: entry.sha, content: encode(entry.profile)};
        if (entry.sha !== original[name].sha) {
            if (input.failure === 'content-mismatch') body.content = encode({...entry.profile, maxPrice: 1});
            if (input.failure === 'sha-mismatch') body.sha = 'different-server-sha';
            if (input.failure === 'missing-content') delete body.content;
        }
        return response(200, body);
    }
});
vm.runInContext(input.functions.join('\n'), context, {timeout: 1000});
(async () => {
    const scheduled = input.mode === 'scheduled-save';
    const saveOnly = scheduled || input.mode === 'manual-save';
    const pending = scheduled ? context.saveScanSettingsToServer() :
        saveOnly ? context.saveManualScanSettings() : context.triggerOnDemandExecution();
    if (input.mode === 'wait-for-readback') {
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(calls.length, 3);
        assert.equal(manualBusy, true);
        assert.equal(confirmed.length, 0);
        assert.equal(closed, false);
        assert.equal(monitors.length, 0);
        releaseReadback();
    }
    await pending;
    const untouched = scheduled ? 'manual_scan_config.json' : 'scan_config.json';
    const target = scheduled ? 'scan_config.json' : 'manual_scan_config.json';
    assert.deepEqual(server[untouched], original[untouched], 'the other profile must stay unchanged');
    assert.equal(manualBusy || scheduledBusy, false, 'controls must unlock after completion');
    assert.equal(button.innerHTML, 'start scan');
    const dispatches = calls.filter(call => call.method === 'POST');
    if (input.failure && !input.failure.startsWith('dispatch-')) {
        assert.equal(errors.length, 1);
        assert.equal(confirmed.length, 0);
        assert.equal(dispatches.length, 0);
        assert.equal(closed, false);
        assert.equal(monitors.length, 0);
    } else {
        assert.equal(errors.length, 0);
        assert.equal(confirmed.length, 1);
        assert.equal(confirmed[0].prefix, scheduled ? 'setting' : 'ondemand');
        const expected = clone(scheduled ? input.scheduled : input.manual);
        if (!saveOnly) expected.requestId = 'guard-request-id';
        assert.deepEqual(server[target].profile, expected);
        assert.deepEqual(confirmed[0].profile, expected);
        assert.deepEqual(calls.slice(0, 3).map(call => call.method), ['GET', 'PUT', 'GET']);
        assert.ok(calls.slice(0, 3).every(call => call.url.includes('/contents/' + target)));
        assert.equal(dispatches.length, saveOnly ? 0 : 1);
        const dispatched = !saveOnly && !input.failure;
        assert.equal(closed, dispatched);
        assert.deepEqual(monitors, dispatched ? ['guard-request-id'] : []);
        if (input.failure) assert.equal(alerts.length, 1);
    }
})().catch(error => { console.error = require('node:console').error; console.error(error); process.exitCode = 1; });
"""


def ui_function(html, name):
    """Read the actual top-level UI function, including one-line wrappers."""
    pattern = r"^        (?:async )?function " + re.escape(name) + r"\([^\n]*\) \{"
    match = re.search(pattern, html, re.MULTILINE)
    if not match:
        raise AssertionError(f"UI function missing: {name}")
    start = match.start()
    first_line_end = html.index("\n", start)
    if html[start:first_line_end].rstrip().endswith("}"):
        return html[start:first_line_end]
    end = re.search(r"^        \}", html[first_line_end:], re.MULTILINE)
    if not end:
        raise AssertionError(f"UI function incomplete: {name}")
    return html[start:first_line_end + end.end()]


def config_loader():
    tree = ast.parse((ROOT / "orchestrator.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "load_server_config")
    scope = {"CONFIG_FILE": "scan_config.json", "math": math}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "orchestrator.py", "exec"), scope)
    return scope


def geography_filter():
    tree = ast.parse((ROOT / "orchestrator.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "geography_rejection")
    scope = {"REGION_MAP": {
        "Allegheny County": {"region_type": "5", "county_name": "Allegheny"},
        "Erie County": {"region_type": "5", "county_name": "Erie"},
        "Erie": {"region_type": "6", "county_name": "Erie"},
    }}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "orchestrator.py", "exec"), scope)
    return scope["geography_rejection"]


class ScanControlsTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "scan_config.json").read_text(encoding="utf-8"))
        self.scope = config_loader()

    def load(self, data):
        self.scope["load_json_file"] = lambda path, default: data
        return self.scope["load_server_config"]()

    def test_current_ui_values_reach_scanner(self):
        result = self.load(self.config)
        self.assertIsNotNone(result)
        settings = result["scan_settings"]
        self.assertEqual(settings["target_counties"], self.config["counties"])
        self.assertEqual(settings["target_cities"], self.config["cities"])
        self.assertEqual(settings["price_range"]["min"], float(self.config["minPrice"]))
        self.assertEqual(settings["price_range"]["max"], float(self.config["maxPrice"]))
        self.assertEqual(settings["max_sqft"], float(str(self.config["maxSqft"]).replace(",", "")))
        self.assertEqual(settings["property_types"], self.config["propertyTypes"])
        self.assertEqual(settings["deal_categories"], self.config["sectors"])
        self.assertEqual(result["autoScanEnabled"], self.config["autoScanEnabled"])
        self.assertEqual(result["schedules"], self.config["schedules"])

    def test_manual_profile_is_separately_readable(self):
        manual = json.loads((ROOT / "manual_scan_config.json").read_text(encoding="utf-8"))
        self.scope["CONFIG_FILE"] = "manual_scan_config.json"
        result = self.load(manual)
        self.assertIsNotNone(result)
        self.assertEqual(result["config_source"], "manual_scan_config.json")
        self.assertEqual(result["scan_settings"]["target_counties"], manual["counties"])

    def test_missing_or_legacy_config_fails_closed(self):
        self.assertIsNone(self.load(None))
        self.assertIsNone(self.load(json.loads((ROOT / "config.json").read_text(encoding="utf-8"))))

    def test_invalid_scope_and_filter_fail_closed(self):
        for changes in ({"counties": [], "cities": []}, {"maxSqft": "NaN"},
                        {"propertyTypes": []}, {"sectors": []}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.load({**self.config, **changes}))

    def test_legacy_hidden_county_city_cannot_start_county_scan(self):
        self.assertIsNone(self.load({**self.config, "counties": [], "cities": ["Allegheny"]}))
        result = self.load({**self.config, "counties": ["Erie"],
                            "cities": ["Allegheny", "Pittsburgh"]})
        self.assertEqual(result["scan_settings"]["target_cities"], ["Pittsburgh"])

        tree = ast.parse((ROOT / "orchestrator.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "build_mls_scan_areas")
        scope = {"COUNTY_REGION_KEYS": {"Allegheny": "Allegheny County", "Erie": "Erie County"},
                 "CITY_COUNTY": {"Pittsburgh": "Allegheny", "Erie": "Erie"}}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "orchestrator.py", "exec"), scope)
        self.assertEqual(scope["build_mls_scan_areas"](["Allegheny", "Pittsburgh"], ["Erie"]),
                         ["Erie County", "Pittsburgh"])

    def test_manual_dispatch_preserves_schedule(self):
        self.run_ui_case("manual-dispatch")
        scanner = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
        self.assertIn('"manual_scan_config.json" if os.environ.get("GITHUB_EVENT_NAME"', scanner)

    def run_ui_case(self, mode, failure=None):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the offline UI guard")
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        names = ("githubScanError", "decodeScanProfile", "putScanConfig", "putManualScanConfig",
                 "triggerOnDemandExecution", "saveManualScanSettings", "saveScanSettingsToServer")
        payload = {"mode": mode, "failure": failure, "scheduled": self.config,
                   "manual": json.loads((ROOT / "manual_scan_config.json").read_text(encoding="utf-8")),
                   "functions": [ui_function(html, name) for name in names]}
        result = subprocess.run([node, "-e", UI_GUARD], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scheduled_save_preserves_manual_profile(self):
        self.run_ui_case("scheduled-save")

    def test_manual_save_does_not_dispatch(self):
        self.run_ui_case("manual-save")

    def test_manual_dispatch_waits_for_server_readback(self):
        self.run_ui_case("wait-for-readback")

    def test_unverified_manual_save_blocks_dispatch(self):
        for failure in ("initial-read", "write-http", "write-conflict", "write-network",
                        "readback-http", "readback-network", "content-mismatch", "sha-mismatch",
                        "missing-save-sha", "missing-content"):
            with self.subTest(failure=failure):
                self.run_ui_case("manual-dispatch", failure)

    def test_unverified_scheduled_save_is_not_confirmed(self):
        for failure in ("write-http", "readback-http", "content-mismatch", "sha-mismatch"):
            with self.subTest(failure=failure):
                self.run_ui_case("scheduled-save", failure)

    def test_dispatch_failure_keeps_form_open(self):
        for failure in ("dispatch-http", "dispatch-network"):
            with self.subTest(failure=failure):
                self.run_ui_case("manual-dispatch", failure)

    def test_workflow_keeps_offline_guard_and_pauses_schedule(self):
        workflow = (ROOT / ".github/workflows/cron-daily-mls.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest -q test_scan_controls", workflow)
        self.assertIn("actions/setup-node@v4", workflow)
        self.assertNotRegex(workflow, r"(?m)^  schedule\s*:")

    def test_county_source_locations_are_enforced(self):
        reject = geography_filter()
        chosen = {"Allegheny": {"allLocations": False,
                                 "locations": ["Pitcairn", "Pittsburgh", "Verona"]}}
        for location in ("Pitcairn", "Pittsburgh", "Verona"):
            self.assertIsNone(reject({"source_scan_area": "Allegheny County",
                                      "city": location, "source_location": location}, chosen))
        self.assertEqual(reject({"source_scan_area": "Allegheny County",
                                 "city": "McKeesport", "source_location": "McKeesport"}, chosen), "location")

    def test_city_scope_does_not_restrict_same_named_county(self):
        reject = geography_filter()
        scopes = {"Erie": {"allLocations": False, "locations": ["West Bay"]},
                  "Erie County": {"allLocations": True, "locations": []}}
        self.assertIsNone(reject({"source_scan_area": "Erie County", "city": "Millcreek",
                                  "source_location": "Millcreek"}, scopes))
        self.assertEqual(reject({"source_scan_area": "Erie", "city": "Erie",
                                 "source_location": "Millcreek"}, scopes), "location")


if __name__ == "__main__":
    unittest.main()
