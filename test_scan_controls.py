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

    def test_missing_or_legacy_config_fails_closed(self):
        self.assertIsNone(self.load(None))
        self.assertIsNone(self.load(json.loads((ROOT / "config.json").read_text(encoding="utf-8"))))

    def test_invalid_scope_and_filter_fail_closed(self):
        for changes in ({"counties": [], "cities": []}, {"maxSqft": "NaN"},
                        {"propertyTypes": []}, {"sectors": []}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.load({**self.config, **changes}))

    def test_manual_dispatch_preserves_schedule(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        start = html.index("async function triggerOnDemandExecution()")
        end = html.index("function openAutoScanModal()", start)
        manual = html[start:end]
        self.assertLess(manual.index("config.schedules = savedConfig.schedules"),
                        manual.index("const contentBase64 ="))
        self.assertLess(manual.index("config.autoScanEnabled = savedConfig.autoScanEnabled"),
                        manual.index("const contentBase64 ="))


if __name__ == "__main__":
    unittest.main()
