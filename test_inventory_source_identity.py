"""Offline regressions for inventory records sharing an address across feeds."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import orchestrator as scanner

ROOT = Path(__file__).resolve().parent
CONCORDIA_MLS = {
    "id": "PA-MLS-1777053", "docket_id": "MLS-1777053", "source_type": "mls",
    "source": "Redfin", "address": "1616 Concordia St", "city": "Pittsburgh",
    "county": "Allegheny", "zip": "15210", "source_state": "PA",
    "source_location": "Pittsburgh", "source_scan_area": "Pittsburgh",
    "price": 75000, "sqft": 1766, "beds": 3, "baths": 2,
    "type": "Single Family", "property_type": "Single Family",
    "market_status": "active", "price_watch_status": "within_budget",
    "url": "https://www.redfin.com/PA/Pittsburgh/1616-Concordia-St-15210/home/74687291",
    "price_history": [{"date": "2026-10-02T12:45:54-04:00", "price": 75000, "source": "Redfin"}],
}
CONCORDIA_HUD = {
    "id": "PA-REO-HUD-79716299266f976038a1", "docket_id": "442-340983",
    "source_type": "reo", "source": "HUD Home Store", "deal_type": "HUD REO",
    "reo_provider": "hud_home_store", "address": "1616 Concordia St",
    "city": "Pittsburgh", "county": "Allegheny County", "zip": "15210",
    "price": 75000.0, "sqft": 1771, "beds": 3, "baths": 2.0,
    "type": "Single Family", "property_type": "Single Family", "market_status": "active",
    "url": "https://www.hudhomestore.gov/propertydetails?caseNumber=442-340983",
}


def row(source, **changes):
    return {"id": "PA-" + source.upper() + "-1234567", "source_type": source,
        "source": source.upper(), "address": "12 Example St", "city": "Erie",
        "county": "Erie", "zip": "16501", "state": "PA", "source_scan_area": "Erie County",
        "price": None if source in {"tax", "sheriff"} else 100000,
        "sqft": 1200, "beds": 2, "baths": 1, "property_type": "Single Family",
        "market_status": "active", **changes}


class InventorySourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.original_cwd = Path.cwd()
        self.addCleanup(os.chdir, self.original_cwd)
        os.chdir(self.repo)
        self.network = ExitStack()
        self.addCleanup(self.network.close)
        self.network.enter_context(patch.object(scanner.requests.sessions.Session, "send",
            side_effect=AssertionError("Live HTTP is forbidden")))
        self.network.enter_context(patch.object(socket, "create_connection",
            side_effect=AssertionError("Live sockets are forbidden")))

    def write(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def scan(self, stored=(), incoming=(), county="Erie", sectors=None):
        incoming = list(incoming)
        profile = json.loads((ROOT / "manual_scan_config.json").read_text(encoding="utf-8"))
        profile.update(counties=[county], cities=[], geoAreas={}, minPrice=50000, maxPrice=190000,
            minSqft=700, maxSqft=2150, minBeds=1, maxBeds=3, minBaths="all",
            propertyTypes=["Single Family", "Duplex / Triplex"],
            sectors=list(sectors or sorted({r["source_type"] for r in incoming} or {"mls"})))
        self.write("manual_scan_config.json", profile)
        self.write("scan_config.json", profile)
        self.write("properties.json", list(stored))
        by_source = {kind: [deepcopy(r) for r in incoming if r["source_type"] == kind]
                     for kind in ("mls", "reo", "sheriff", "tax")}

        def fetch_mls(area, low, high, audit):
            audit.update(status="success", area=area, rows=len(by_source["mls"]))
            return deepcopy(by_source["mls"])

        erie_audit = {"status": "success", "eligible_after_required_data_gate": len(by_source["tax"]),
            "cache_hits": 0, "profile_failures": 0, "profile_status_counts": {},
            "required_missing_field_counts": {}, "required_missing_pattern_counts": {},
            "profile_field_presence_counts": {}, "gis_field_presence_counts": {}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(scanner, "CONFIG_FILE", "manual_scan_config.json"))
            stack.enter_context(patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch",
                "PA_SCAN_CONFIG_COMMIT": "", "PA_SCAN_REQUEST_ID": "", "GITHUB_RUN_ID": "offline-source-identity"}))
            stack.enter_context(patch.object(scanner, "fetch_live_mls_for_city", side_effect=fetch_mls))
            stack.enter_context(patch.object(scanner, "fetch_homesteps_reo", return_value=([], {"status": "success"})))
            stack.enter_context(patch.object(scanner, "fetch_hud_homestore_reo",
                return_value=(by_source["reo"], {"status": "success", "counties": {}})))
            stack.enter_context(patch.object(scanner, "fetch_erie_sheriff_listings",
                return_value=(by_source["sheriff"], {"status": "success"})))
            stack.enter_context(patch.object(scanner, "fetch_erie_repository_list",
                return_value=(by_source["tax"], {"status": "success", "source_url": "offline"})))
            stack.enter_context(patch.object(scanner, "enrich_erie_repository_rows",
                side_effect=lambda rows, cache: (rows, cache, deepcopy(erie_audit))))
            with redirect_stdout(io.StringIO()):
                scanner.run_orchestrator()
        saved = json.loads((self.repo / "properties.json").read_text(encoding="utf-8"))
        event = json.loads((self.repo / "scanner_status.json").read_text(encoding="utf-8"))["last_event"]
        self.assertNotEqual(event["status"], "failed", event.get("errors"))
        return {r["id"]: r for r in saved}, event

    def test_real_concordia_hud_and_mls_pass_current_physical_filters_and_keep_their_ids(self):
        settings = {"price_range": {"min": 50000, "max": 190000}, "min_sqft": 700,
            "max_sqft": 2150, "min_beds": 1, "max_beds": 3, "min_baths": 0,
            "property_types": ["Single Family", "Duplex / Triplex"]}
        scope = scanner.compile_scan_scope(["Pittsburgh"], ["Allegheny"], {
            "Pittsburgh": {"allLocations": False, "locations": ["Penn Hills", "Pittsburgh"]},
            "Allegheny County": {"allLocations": False, "locations": ["Penn Hills", "Pittsburgh", "Verona"]}})
        for value in (CONCORDIA_MLS, CONCORDIA_HUD):
            self.assertIsNone(scanner.scan_property_rejection(value, settings, scope))
        saved, _ = self.scan([CONCORDIA_MLS], [CONCORDIA_MLS, CONCORDIA_HUD],
                             county="Allegheny", sectors=["mls", "reo"])
        self.assertEqual(set(saved), {CONCORDIA_MLS["id"], CONCORDIA_HUD["id"]})
        self.assertEqual(saved[CONCORDIA_MLS["id"]]["price_history"], CONCORDIA_MLS["price_history"])
        self.assertEqual(saved[CONCORDIA_MLS["id"]]["url"], CONCORDIA_MLS["url"])
        self.assertEqual(saved[CONCORDIA_HUD["id"]]["url"], CONCORDIA_HUD["url"])
        self.assertEqual({e["source"] for e in saved[CONCORDIA_HUD["id"]]["price_history"]}, {"HUD Home Store"})

    def test_all_four_source_records_survive_the_shared_address_filter_and_next_load(self):
        incoming = [row(source) for source in ("mls", "reo", "sheriff", "tax")]
        saved, event = self.scan(incoming=incoming)
        self.assertEqual(set(saved), {r["id"] for r in incoming})
        self.assertEqual(event["duplicates_removed"], 0)
        self.assertEqual(len(scanner.load_existing_properties()), 4)
        for value in incoming:
            self.assertEqual({e["source"] for e in saved[value["id"]]["price_history"]}, {value["source"]})

    def test_same_source_price_drop_keeps_history_without_touching_other_sources(self):
        old = row("mls", price=205000, price_watch_status="background_watch",
            price_history=[{"date": "2026-10-01T10:00:00-04:00", "price": 205000, "source": "MLS"}])
        tax = row("tax")
        saved, _ = self.scan([old, tax], [row("mls", price=185000)])
        mls = saved[old["id"]]
        self.assertEqual(mls["price_watch_status"], "within_budget")
        self.assertEqual([e["price"] for e in mls["price_history"]], [205000, 185000])
        self.assertEqual(mls["price_drop_history"][-1]["drop_amount"], 20000)
        self.assertEqual(saved[tax["id"]].get("price_history"), tax.get("price_history"))
        self.assertIsNone(saved[tax["id"]]["price"])

    def test_raw_mls_seen_updates_the_mls_namespace_even_when_new_facts_fail_filters(self):
        old = row("mls", market_status="off_market_candidate", missing_scan_count=5)
        tax = row("tax", missing_scan_count=7)
        saved, _ = self.scan([old, tax], [row("mls", sqft=5000)])
        self.assertEqual(saved[old["id"]]["market_status"], "active")
        self.assertEqual(saved[old["id"]]["missing_scan_count"], 0)
        self.assertEqual(saved[tax["id"]]["missing_scan_count"], 7)

    def test_background_watch_uses_its_source_namespace(self):
        reo = row("reo", price=180000)
        saved, event = self.scan([reo], [row("mls", price=205000)])
        self.assertEqual(len(saved), 2)
        self.assertEqual(event["background_price_watch_rows"], 1)
        self.assertEqual(saved[reo["id"]]["source_type"], "reo")
        self.assertEqual(saved["PA-MLS-1234567"]["price_watch_status"], "background_watch")

    def test_different_numeric_mls_listings_at_one_address_keep_separate_histories(self):
        old = row("mls", id="PA-MLS-1777053", docket_id="MLS-1777053", price=205000,
            url=CONCORDIA_MLS["url"], price_watch_status="background_watch",
            price_history=[{"date": "2026-10-01T10:00:00-04:00", "price": 205000, "source": "MLS"}])
        new = row("mls", id="PA-MLS-1779999", docket_id="MLS-1779999", price=180000,
            url=CONCORDIA_MLS["url"])
        saved, _ = self.scan([old], [old, new])
        self.assertEqual(set(saved), {old["id"], new["id"]})
        self.assertEqual(saved[old["id"]]["price_history"], old["price_history"])
        self.assertEqual([e["price"] for e in saved[new["id"]]["price_history"]], [180000])
        self.assertFalse(saved[new["id"]]["price_dropped"])
        self.assertEqual(saved[new["id"]]["price_drop_history"], [])
        self.assertEqual(len(scanner.load_existing_properties()), 2)

    def test_different_mls_listings_both_within_budget_survive_result_dedup(self):
        first = row("mls", id="PA-MLS-1777053", docket_id="MLS-1777053", price=185000)
        second = row("mls", id="PA-MLS-1779999", docket_id="MLS-1779999", price=180000)
        saved, event = self.scan(incoming=[first, second])
        self.assertEqual(set(saved), {first["id"], second["id"]})
        self.assertEqual(event["after_filters"], 2)
        self.assertEqual(event["duplicates_removed"], 0)

    def test_same_mls_listing_with_a_legacy_alias_keeps_the_saved_inventory_id(self):
        old = row("mls", id="PA-MLS-legacy-alias", docket_id="MLS-1777053", price=185000,
            price_history=[{"date": "2026-10-01T10:00:00-04:00", "price": 185000, "source": "MLS"}])
        incoming = row("mls", id="PA-MLS-1777053", docket_id="MLS-1777053", price=180000)
        saved, _ = self.scan([old], [incoming])
        self.assertEqual(set(saved), {old["id"]})
        self.assertEqual(saved[old["id"]]["docket_id"], "MLS-1777053")
        self.assertEqual([e["price"] for e in saved[old["id"]]["price_history"]], [185000, 180000])
        self.assertEqual(saved[old["id"]]["price_drop_history"][-1]["drop_amount"], 5000)

    def test_mls_window_dedup_keeps_different_listings_at_the_same_address(self):
        first = row("mls", id="PA-MLS-1777053", docket_id="MLS-1777053")
        second = row("mls", id="PA-MLS-1779999", docket_id="MLS-1779999")
        count = 0

        def fetch_window(area, low, high, audit):
            nonlocal count
            count += 1
            audit.update(status="success", limit_reached=count == 1)
            return [deepcopy(first), deepcopy(second)]

        with patch.object(scanner, "fetch_live_mls_window", side_effect=fetch_window):
            saved = scanner.fetch_live_mls_for_city("Erie County", 50000, 209000)
        self.assertEqual({value["id"] for value in saved}, {first["id"], second["id"]})
        self.assertEqual(len(saved), 2)
        self.assertEqual(count, 3)

    def test_mls_listing_binding_matches_registry_precedence_and_legacy_ids(self):
        numeric = row("mls", id="PA-MLS-1", docket_id="MLS-10", listing_id="123")
        equivalent = row("mls", id="PA-MLS-123", docket_id="MLS-123")
        self.assertEqual(scanner.inventory_key(numeric), scanner.inventory_key(equivalent))
        self.assertNotEqual(scanner.inventory_key(row("mls", id="PA-MLS-unverified-a")),
            scanner.inventory_key(row("mls", id="PA-MLS-unverified-b")))

    def test_explicit_tax_and_sheriff_address_corrections_keep_their_original_ids(self):
        for source in ("tax", "sheriff"):
            with self.subTest(source=source):
                old = row(source, id="old-" + source, parcel_id="123", sale_number="SALE-1", docket_id="DOCKET-1")
                new = row(source, id="new-" + source, address="14 Example St", parcel_id="123",
                    sale_number="SALE-1", docket_id="DOCKET-1")
                saved, _ = self.scan([old], [new])
                self.assertEqual(set(saved), {old["id"]})
                self.assertEqual(saved[old["id"]]["address"], "14 Example St")

    def test_legacy_source_prefixes_join_only_the_corresponding_explicit_source(self):
        for source, property_id in (("mls", "PA-MLS-1234567"), ("reo", "PA-REO-HUD-1"),
                ("sheriff", "PA-SHERIFF-1"), ("tax", "PA-TAX-44910"), ("tax", "tax-erie-repository-123")):
            with self.subTest(property_id=property_id):
                explicit = row(source, id=property_id)
                legacy = {k: v for k, v in explicit.items() if k != "source_type"}
                self.assertEqual(scanner.inventory_key(legacy), scanner.inventory_key(explicit))
                self.assertNotEqual(scanner.inventory_key(legacy), scanner.inventory_key(row("other", id=property_id)))
        legacy = {k: v for k, v in row("mls").items() if k != "source_type"}
        saved, _ = self.scan([legacy], [row("mls", price=90000)])
        self.assertEqual(set(saved), {legacy["id"]})
        self.assertEqual(saved[legacy["id"]]["source_type"], "mls")

    def test_unknown_legacy_sources_do_not_assume_mls_from_the_same_address(self):
        unknown = {k: v for k, v in row("mls", id="legacy-unknown").items() if k != "source_type"}
        other = {**unknown, "id": "legacy-other"}
        self.write("properties.json", [unknown, other, row("mls")])
        self.assertEqual(len(scanner.load_existing_properties()), 3)
        self.assertNotEqual(scanner.inventory_key(unknown), scanner.inventory_key(row("mls")))

    def test_inventory_namespaces_leave_review_identity_unchanged(self):
        value = row("mls")
        expected = scanner.scan_review_key(value)
        scanner.inventory_key(value)
        self.assertEqual(scanner.scan_review_key(value), expected)
        self.assertEqual(scanner.property_key(value), scanner.property_key(row("reo")))


if __name__ == "__main__":
    unittest.main()
