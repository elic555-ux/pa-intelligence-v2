"""Offline guard for the UI configuration consumed by the central scanner."""

import ast
import json
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


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
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        start = html.index("async function triggerOnDemandExecution()")
        end = html.index("function openAutoScanModal()", start)
        manual = html[start:end]
        self.assertIn("contents/manual_scan_config.json", html)
        self.assertNotIn("contents/scan_config.json", manual)
        self.assertIn("await putManualScanConfig(config, tk)", manual)
        self.assertIn("localStorage.setItem('pa_ondemand_state_v7', JSON.stringify(config))", html)
        self.assertIn("await loadManualScanProfile()", html)
        self.assertTrue((ROOT / "manual_scan_config.json").is_file())
        scanner = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
        self.assertIn('"manual_scan_config.json" if os.environ.get("GITHUB_EVENT_NAME"', scanner)

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
