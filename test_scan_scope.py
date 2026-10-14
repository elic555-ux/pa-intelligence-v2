"""Offline scope, common-filter, retention, workflow and analysis regressions."""
from contextlib import ExitStack, contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import base64
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch, Mock

import analyzer
import orchestrator as scanner

ROOT = Path(__file__).resolve().parent
SETTINGS = {"price_range": {"min": 50000, "max": 190000}, "min_sqft": 700,
            "max_sqft": 1600, "min_beds": 1, "max_beds": 3, "min_baths": 0,
            "property_types": ["Single Family", "Duplex / Triplex"]}


def property_row(source="mls", **changes):
    row = {"id": source + "-sample", "address": "12 Example St", "city": "Verona",
           "county": "Allegheny", "state": "PA", "zip": "15147", "source_type": source,
           "price": 100000, "sqft": 1200, "beds": 2, "baths": 1,
           "property_type": "Single Family", "market_status": "active"}
    return {**row, "municipality": changes.get("city", row["city"]), **changes}


def scope(counties=("Allegheny",), cities=(), rules=None):
    return scanner.compile_scan_scope(list(cities), list(counties), rules or {})


@contextmanager
def temporary_repo():
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            yield Path(directory)
        finally:
            os.chdir(original)


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.verona = scope(rules={"Allegheny County": {"allLocations": False, "locations": ["Verona"]}})

    def test_every_source_rejects_outside_selected_municipality(self):
        for source in ("mls", "reo", "sheriff", "tax"):
            with self.subTest(source=source):
                self.assertIsNone(scanner.scan_property_rejection(property_row(source), SETTINGS, self.verona))
                self.assertEqual(scanner.scan_property_rejection(property_row(source, city="McKeesport"), SETTINGS, self.verona), "location")

    def test_every_source_obeys_size_beds_baths_and_property_type(self):
        examples = [({"sqft": 5000, "beds": 6}, "sqft"), ({"beds": 6}, "beds"),
                    ({"property_type": "Townhouse"}, "property_type"),
                    ({"property_type": None}, "property_type_unknown"),
                    ({"sqft": None}, "incomplete_required_data")]
        for source in ("mls", "reo", "sheriff", "tax"):
            for changes, expected in examples:
                with self.subTest(source=source, changes=changes):
                    self.assertEqual(scanner.scan_property_rejection(property_row(source, **changes), SETTINGS, self.verona), expected)
            with self.subTest(source=source, baths="missing"):
                self.assertEqual(scanner.scan_property_rejection(property_row(source, baths=None), {**SETTINGS, "min_baths": 1}, self.verona), "baths_unverified")

    def test_published_asking_price_filtered_without_using_auction_amounts(self):
        for source in ("mls", "reo"):
            self.assertEqual(scanner.scan_property_rejection(property_row(source, price=300000), SETTINGS, self.verona), "price")
        for source in ("sheriff", "tax"):
            row = property_row(source, price=None, opening_bid=250, minimum_bid=1000)
            before = deepcopy(row)
            self.assertIsNone(scanner.scan_property_rejection(row, SETTINGS, self.verona))
            self.assertEqual(row, before)
            self.assertIsNone(scanner.scan_property_rejection(property_row(source, price=250), SETTINGS, self.verona))

    def test_unknown_price_does_not_become_a_bid_or_an_estimate(self):
        row = property_row("reo", price=None)
        self.assertIsNone(scanner.scan_property_rejection(row, SETTINGS, self.verona))
        self.assertIsNone(row["price"])

    def test_city_scope_refines_county_and_city_query_is_preserved(self):
        chosen = scope(cities=("Pittsburgh",), rules={
            "Allegheny County": {"allLocations": False, "locations": ["Verona"]},
            "Pittsburgh": {"allLocations": False, "locations": ["Oakland"]}})
        self.assertEqual(chosen["mls_scan_areas"], ["Allegheny County", "Pittsburgh"])
        oakland = property_row(city="Pittsburgh", source_scan_area="Allegheny County", source_location="Oakland")
        self.assertIsNone(scanner.scan_property_rejection(oakland, SETTINGS, chosen))
        self.assertEqual(scanner.scan_property_rejection({**oakland, "source_location": "Shadyside"}, SETTINGS, chosen), "location")

    def test_whole_county_does_not_bypass_an_explicit_city_restriction(self):
        chosen = scope(cities=("Pittsburgh",), rules={"Pittsburgh": {"allLocations": False, "locations": ["Oakland"]}})
        self.assertEqual(scanner.scan_property_rejection(property_row(city="Pittsburgh", source_location="Shadyside"), SETTINGS, chosen), "location")
        self.assertIsNone(scanner.scan_property_rejection(property_row(), SETTINGS, chosen))

    def test_city_only_never_authorizes_the_rest_of_its_county(self):
        chosen = scope(counties=(), cities=("Pittsburgh",))
        self.assertEqual(chosen["counties"], [])
        self.assertEqual(chosen["county_sources"], ["Allegheny"])
        self.assertEqual(chosen["mls_scan_areas"], ["Pittsburgh"])
        self.assertEqual(scanner.scan_property_rejection(property_row("reo"), SETTINGS, chosen), "outside_scope")

    def test_erie_city_alias_and_county_are_distinct(self):
        chosen = scope(counties=("Erie",), cities=("Erie",), rules={"Erie": {"allLocations": False, "locations": ["West Bay"]}})
        row = property_row("tax", county="Erie", city="City of Erie", source_location="West Bay")
        self.assertIsNone(scanner.scan_property_rejection(row, SETTINGS, chosen))
        self.assertEqual(scanner.scan_property_rejection({**row, "source_location": None}, SETTINGS, chosen), "location_unverified")
        self.assertIsNone(scanner.scan_property_rejection({**row, "city": "Millcreek", "municipality": "Millcreek", "source_location": None}, SETTINGS, chosen))
        self.assertIsNone(scanner.scan_property_rejection({**row, "city": "Erie", "municipality": "Millcreek", "source_location": None}, SETTINGS, chosen))

    def test_known_city_label_cannot_hide_another_municipality(self):
        chosen = scope(counties=("Erie",), rules={"Erie County": {"allLocations": False, "locations": ["Erie"]}})
        self.assertEqual(scanner.scan_property_rejection(property_row(county="Erie", city="Corry", source_location="Erie"), SETTINGS, chosen), "location")

    def test_unknown_neighborhood_requires_review_for_every_source(self):
        chosen = scope(counties=(), cities=("Pittsburgh",), rules={"Pittsburgh": {"allLocations": False, "locations": ["Oakland"]}})
        for source in ("mls", "reo", "sheriff", "tax"):
            self.assertEqual(scanner.scan_property_rejection(property_row(source, city="Pittsburgh"), SETTINGS, chosen), "location_unverified")

    def test_wrong_state_and_wrong_city_feed_are_rejected(self):
        self.assertEqual(scanner.scan_property_rejection(property_row(state="NY"), SETTINGS, self.verona), "state")
        self.assertEqual(scanner.scan_property_rejection(property_row(source_scan_area="Pittsburgh"), SETTINGS, self.verona), "city_mismatch")

    def test_nonfinite_required_facts_cannot_pass(self):
        for value in (float("nan"), float("inf"), "NaN", "Infinity", True):
            self.assertEqual(scanner.scan_property_rejection(property_row(sqft=value), SETTINGS, self.verona), "incomplete_required_data")

    def test_empty_partial_or_unsupported_scope_fails_closed(self):
        for args in [([], [], {}), ([], ["Unknown"], {}), (["Unknown"], [], {}),
                     ([], ["Allegheny"], {"Allegheny County": {"allLocations": False, "locations": []}}),
                     ([], ["Allegheny"], {"Allegheny County": {"allLocations": True, "locations": ["Verona"]}})]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                scanner.compile_scan_scope(*args)

    def test_scope_compilation_does_not_mutate_saved_selections(self):
        raw = {"Allegheny": {"allLocations": False, "locations": ["Verona"]}}
        before = deepcopy(raw)
        chosen = scope(rules=raw)
        self.assertEqual(raw, before)
        self.assertEqual(chosen["geo_areas"]["Allegheny County"]["locations"], ["Verona"])


class RetentionTests(unittest.TestCase):
    def test_original_record_and_tracking_id_retained_and_deduplicated(self):
        with temporary_repo():
            row = property_row("sheriff", property_type=None, notes="Keep my original notes", deal_room_id="room-12")
            scanner.retain_scan_review([(row, "property_type_unknown")], "scan-1", "source_candidates")
            scanner.retain_scan_review([(row, "property_type_unknown")], "scan-2", "sheriff_cache")
            review = json.loads(Path(scanner.SCAN_REVIEW_FILE).read_text())
            self.assertEqual(len(review["items"]), 1)
            entry = next(iter(review["items"].values()))
            self.assertEqual(entry["record"], row)
            self.assertEqual(entry["origins"], ["sheriff_cache", "source_candidates"])

    def test_same_listing_id_different_address_kept_separate(self):
        with temporary_repo():
            a = property_row()
            b = property_row(address="14 Example St")
            scanner.retain_scan_review([(a, "location"), (b, "location")], "scan", "source_candidates")
            self.assertEqual(len(json.loads(Path(scanner.SCAN_REVIEW_FILE).read_text())["items"]), 2)

    def test_invalid_review_file_is_never_overwritten(self):
        with temporary_repo():
            path = Path(scanner.SCAN_REVIEW_FILE)
            path.parent.mkdir(parents=True)
            path.write_text("broken old review")
            with self.assertRaises(ValueError):
                scanner.retain_scan_review([(property_row(), "location")], "scan", "source_candidates")
            self.assertEqual(path.read_text(), "broken old review")


class PipelineTests(unittest.TestCase):
    def run_case(self, incomplete_existing=False, invalid_review=False):
        profile = json.loads((ROOT / "manual_scan_config.json").read_text())
        profile.update({"counties": ["Erie"], "cities": [], "geoAreas": {
            "Erie County": {"allLocations": False, "locations": ["Erie"]}},
            "sectors": ["mls", "reo", "sheriff", "tax"], "requestId": "request-test"})
        base = {"county": "Erie", "city": "Erie", "zip": "16501"}
        good = [property_row(source, id=source + "-good", address=str(12 + i) + " Example St", **base)
                for i, source in enumerate(("mls", "reo", "sheriff", "tax"))]
        bad_city = property_row("reo", id="outside-city", address="88 Example St", **{**base, "city": "Corry"})
        bad_size = property_row("sheriff", id="large-sheriff", address="90 Example St", sqft=5000, beds=6, **base)
        unknown_type = property_row("tax", id="unknown-tax", address="92 Example St", property_type=None, **base)
        old = property_row(id="historical-row", address="200 Existing St", **{**base, "city": "Corry"})
        if incomplete_existing:
            old["sqft"] = None
        write_json("manual_scan_config.json", profile)
        write_json("scan_config.json", {**profile, "counties": ["Allegheny"]})
        write_json("properties.json", [old])
        write_json("sheriff_listings.json", [bad_size])
        if invalid_review:
            Path(scanner.SCAN_REVIEW_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(scanner.SCAN_REVIEW_FILE).write_text("broken review")
        before = Path("properties.json").read_bytes()
        audit = {"status": "success", "eligible_after_required_data_gate": 2, "cache_hits": 2,
                 "profile_failures": 0, "profile_status_counts": {}, "required_missing_field_counts": {},
                 "required_missing_pattern_counts": {}, "profile_field_presence_counts": {}, "gis_field_presence_counts": {}}
        with ExitStack() as stack:
            # The engine chooses CONFIG_FILE at import time, before this fixture
            # changes GITHUB_EVENT_NAME. Bind the test profile explicitly so a
            # scheduled runner cannot select an unmocked county source.
            stack.enter_context(patch.object(scanner, "CONFIG_FILE", "manual_scan_config.json"))
            stack.enter_context(patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "test-run",
                "PA_SCAN_CONFIG_COMMIT": "", "PA_SCAN_REQUEST_ID": ""}))
            stack.enter_context(patch.object(scanner.requests.sessions.Session, "send", side_effect=AssertionError("Live HTTP is forbidden")))
            fetch_mls = stack.enter_context(patch.object(scanner, "fetch_live_mls_for_city", return_value=[good[0]]))
            stack.enter_context(patch.object(scanner, "fetch_erie_sheriff_listings", return_value=([good[2], bad_size], {"status": "success"})))
            stack.enter_context(patch.object(scanner, "fetch_homesteps_reo", return_value=([good[1], bad_city], {"status": "success"})))
            stack.enter_context(patch.object(scanner, "fetch_hud_homestore_reo", return_value=([], {"status": "success", "counties": {}})))
            stack.enter_context(patch.object(scanner, "fetch_erie_repository_list", return_value=([good[3], unknown_type], {"status": "success", "source_url": "offline"})))
            stack.enter_context(patch.object(scanner, "enrich_erie_repository_rows", side_effect=lambda rows, cache: (rows, cache, deepcopy(audit))))
            with redirect_stdout(io.StringIO()):
                scanner.run_orchestrator()
        return good, old, before, fetch_mls

    def test_all_source_results_and_sheriff_cache_share_the_same_gate(self):
        with temporary_repo():
            good, old, _, _ = self.run_case()
            rows = json.loads(Path("properties.json").read_text())
            self.assertEqual({row["id"] for row in rows}, {row["id"] for row in good} | {old["id"]})
            sheriff = json.loads(Path("sheriff_listings.json").read_text())
            self.assertEqual([row["id"] for row in sheriff], ["sheriff-good"])
            review = json.loads(Path(scanner.SCAN_REVIEW_FILE).read_text())
            self.assertEqual({entry["record"]["id"] for entry in review["items"].values()}, {"outside-city", "large-sheriff", "unknown-tax"})
            event = json.loads(Path("scanner_status.json").read_text())["last_event"]
            self.assertEqual(event["source_passed_filters"], {"mls": 1, "sheriff": 1, "tax": 1, "reo": 1})
            self.assertEqual(event["request_id"], "request-test")
            context = json.loads(Path(scanner.SCAN_CONTEXT_FILE).read_text())
            expected_rows = [row for row in rows if row["id"] != old["id"]]
            self.assertEqual(set(context["property_keys"]), {analyzer.analysis_scope_key(row) for row in expected_rows})
            self.assertNotIn(analyzer.analysis_scope_key(old), context["property_keys"])

    def test_pipeline_fixture_uses_its_own_profile_on_a_scheduled_runner(self):
        with patch.object(scanner, "CONFIG_FILE", "scan_config.json"), \
             patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule", "PA_SCAN_CONFIG_FILE": "scan_config.json"}):
            with temporary_repo():
                good, old, _, _ = self.run_case()
                rows = json.loads(Path("properties.json").read_text())
                self.assertEqual({row["id"] for row in rows}, {row["id"] for row in good} | {old["id"]})
                event = json.loads(Path("scanner_status.json").read_text())["last_event"]
                self.assertEqual(event["config_source"], "manual_scan_config.json")
                self.assertEqual(event["trigger"], "manual")
            self.assertEqual(scanner.CONFIG_FILE, "scan_config.json")

    def test_incomplete_existing_row_is_preserved_before_removal(self):
        with temporary_repo():
            _, old, _, _ = self.run_case(incomplete_existing=True)
            review = json.loads(Path(scanner.SCAN_REVIEW_FILE).read_text())
            retained = [entry["record"] for entry in review["items"].values() if entry["record"]["id"] == old["id"]]
            self.assertEqual(retained, [old])

    def test_preservation_failure_stops_sources_and_inventory_writes(self):
        with temporary_repo():
            _, _, before, fetch = self.run_case(incomplete_existing=True, invalid_review=True)
            fetch.assert_not_called()
            self.assertEqual(Path("properties.json").read_bytes(), before)
            self.assertEqual(json.loads(Path("scanner_status.json").read_text())["last_event"]["status"], "failed")


class AnalysisTests(unittest.TestCase):
    def test_snapshot_is_used_instead_of_unrelated_scheduled_profile(self):
        with temporary_repo():
            row = property_row(city="Pittsburgh")
            other_address = {**row, "address": "14 Example St"}
            other_county = property_row(id="erie", city="Erie", county="Erie")
            write_json("properties.json", [row, other_address, other_county])
            write_json("scan_config.json", {"counties": ["Erie"], "cities": []})
            write_json("context.json", {"schema": 1, "github_run_id": "run", "scope": {"county_sources": ["Allegheny"]},
                "property_keys": [analyzer.analysis_scope_key(row)], "passed_counties": []})
            with patch.dict(os.environ, {"PA_SCAN_CONTEXT_FILE": "context.json", "GITHUB_RUN_ID": "run"}), \
                 patch.object(analyzer, "get_coordinates", return_value=(40.0, -79.0)) as geocode, \
                 patch.object(analyzer.time, "sleep"), redirect_stdout(io.StringIO()):
                analyzer.run_analyzer()
            geocode.assert_called_once_with(row["address"], row["city"])

    def test_city_only_configuration_resolves_its_source_county(self):
        with temporary_repo():
            write_json("manual_scan_config.json", {"counties": [], "cities": ["Pittsburgh"]})
            with patch.object(analyzer, "CONFIG_FILE", "manual_scan_config.json"):
                self.assertEqual(analyzer.load_selected_counties(), {"allegheny"})

    def test_context_from_another_run_fails_before_geocoding(self):
        with temporary_repo():
            write_json("context.json", {"schema": 1, "github_run_id": "old", "scope": {}, "property_keys": []})
            with patch.dict(os.environ, {"PA_SCAN_CONTEXT_FILE": "context.json", "GITHUB_RUN_ID": "new"}), self.assertRaises(ValueError):
                analyzer.load_scan_context()


class SavedRequestTests(unittest.TestCase):
    def setUp(self):
        self.env = {"PA_SCAN_CONFIG_COMMIT": "a" * 40, "PA_SCAN_REQUEST_ID": "request-one",
                    "GITHUB_REPOSITORY": "owner/project", "GITHUB_TOKEN": "offline-test-token"}
        self.profile = {"requestId": "request-one", "maxPrice": 190000}

    def response(self, profile):
        return Mock(json=lambda: {"encoding": "base64",
            "content": base64.b64encode(json.dumps(profile).encode()).decode()})

    def test_queued_request_reads_its_saved_commit(self):
        with patch.dict(os.environ, self.env), patch.object(scanner, "CONFIG_FILE", "manual_scan_config.json"), \
                patch.object(scanner.requests, "get", return_value=self.response(self.profile)) as get, \
                patch.object(scanner, "load_json_file", side_effect=AssertionError("Do not read a newer profile")):
            self.assertEqual(scanner.load_run_profile(), self.profile)
            self.assertEqual(get.call_args.kwargs["params"], {"ref": "a" * 40})

    def test_another_request_profile_is_rejected_without_fallback(self):
        with patch.dict(os.environ, self.env), patch.object(scanner, "CONFIG_FILE", "manual_scan_config.json"), \
                patch.object(scanner.requests, "get", return_value=self.response({**self.profile, "requestId": "another"})), \
                patch.object(scanner, "load_json_file", side_effect=AssertionError("No fallback")), redirect_stdout(io.StringIO()):
            self.assertIsNone(scanner.load_run_profile())

    def test_failed_snapshot_read_stops_without_fallback(self):
        with patch.dict(os.environ, self.env), patch.object(scanner, "CONFIG_FILE", "manual_scan_config.json"), \
                patch.object(scanner.requests, "get", side_effect=scanner.requests.Timeout("offline")), \
                patch.object(scanner, "load_json_file", side_effect=AssertionError("No fallback")), redirect_stdout(io.StringIO()):
            self.assertIsNone(scanner.load_run_profile())

    def test_request_without_a_commit_cannot_use_current_profile(self):
        with patch.dict(os.environ, {**self.env, "PA_SCAN_CONFIG_COMMIT": ""}), \
                patch.object(scanner.requests, "get", side_effect=AssertionError("No network")), \
                patch.object(scanner, "load_json_file", side_effect=AssertionError("No fallback")), redirect_stdout(io.StringIO()):
            self.assertIsNone(scanner.load_run_profile())


class SheriffLocationAndTypeTests(unittest.TestCase):
    def test_documented_municipality_controls_scope_instead_of_postal_city(self):
        selection = scope(rules={"Allegheny County": {"allLocations": False, "locations": ["Penn Hills"]}})
        row = property_row("sheriff", city="Pittsburgh", municipality="Penn Hills")
        self.assertIsNone(scanner.scan_property_rejection(row, SETTINGS, selection))
        verona = scope(rules={"Allegheny County": {"allLocations": False, "locations": ["Verona"]}})
        row["city"] = "Verona"
        self.assertEqual(scanner.scan_property_rejection(row, SETTINGS, verona), "location")

    def test_whole_city_label_does_not_require_a_neighborhood_but_city_must_be_verified(self):
        selection = scope(counties=(), cities=("Pittsburgh",), rules={"Pittsburgh": {"allLocations": False, "locations": ["Pittsburgh"]}})
        row = property_row("sheriff", city="Pittsburgh", municipality="Pittsburgh")
        self.assertIsNone(scanner.scan_property_rejection(row, SETTINGS, selection))
        self.assertEqual(scanner.scan_property_rejection({**row, "municipality": "Sharpsburg"}, SETTINGS, selection), "outside_scope")
        self.assertEqual(scanner.scan_property_rejection({**row, "municipality": None}, SETTINGS, selection), "geography_unverified")

    def test_neighborhood_only_selection_still_requires_neighborhood_facts(self):
        selection = scope(counties=(), cities=("Pittsburgh",), rules={"Pittsburgh": {"allLocations": False, "locations": ["Oakland"]}})
        row = property_row("sheriff", city="Pittsburgh", municipality="Pittsburgh")
        self.assertEqual(scanner.scan_property_rejection(row, SETTINGS, selection), "location_unverified")
        self.assertIsNone(scanner.scan_property_rejection({**row, "neighborhood": "Oakland"}, SETTINGS, selection))

    def test_known_county_use_descriptions_are_mapped_without_guessing(self):
        for raw, expected in [("SINGLE FAMILY", "Single Family"), ("TWO FAMILY", "Duplex / Triplex"),
                              ("THREE FAMILY", "Duplex / Triplex"), ("FOUR FAMILY", "Multi-Family")]:
            self.assertEqual(scanner.county_use_property_type(raw), expected)
        for unknown in [None, "RESIDENTIAL", "ROWHOUSE", "100", "RETL/APT'S OVER"]:
            self.assertIsNone(scanner.county_use_property_type(unknown))
        selection = scope(rules={"Allegheny County": {"allLocations": False, "locations": ["Verona"]}})
        self.assertEqual(scanner.scan_property_rejection(property_row("sheriff", property_type=scanner.county_use_property_type("FOUR FAMILY")), SETTINGS, selection), "property_type")

    def test_lookup_limit_remains_effective_after_initial_backfill(self):
        with temporary_repo():
            write_json(scanner.SHERIFF_PROPERTY_CACHE_FILE, {"_meta": {"initial_backfill_complete": True}})
            rows = [property_row("sheriff", parcel_id="123-A-" + str(i)) for i in range(1, 6)]
            with patch.object(scanner, "fetch_county_building_data", return_value=None) as fetch:
                scanner.enrich_sheriff_rows_from_county(rows, max_lookups=2)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(sum(r["county_property_status"] == "pending_backfill" for r in rows), 3)
            with patch.object(scanner, "fetch_county_building_data", side_effect=AssertionError("Zero lookups means no HTTP")):
                scanner.enrich_sheriff_rows_from_county(rows, max_lookups=0)

    def test_cached_type_is_attached_with_provenance_without_changing_other_facts(self):
        with temporary_repo():
            row = property_row("sheriff", property_type=None, type=None, parcel_id="123-A-1", price=None)
            original = deepcopy(row)
            pin = scanner.county_pin_from_sheriff_parcel(row["parcel_id"])
            data = {"pin": pin, "use_code": "SINGLE FAMILY", "source": "Allegheny County Real Estate Portal",
                    "source_url": "https://realestate.alleghenycounty.us/BuildingInfo?ID=" + pin,
                    "retrieved_at": "2026-10-12T10:00:00-04:00"}
            write_json(scanner.SHERIFF_PROPERTY_CACHE_FILE, {pin: data})
            with patch.object(scanner, "fetch_county_building_data", side_effect=AssertionError("Cached facts require no HTTP")):
                scanner.enrich_sheriff_rows_from_county([row], max_lookups=0)
            self.assertEqual(row["property_type"], "Single Family")
            self.assertEqual(row["source_property_type"], "SINGLE FAMILY")
            self.assertEqual(row["property_type_source_url"], data["source_url"])
            for field in ["price", "address", "city", "municipality", "sqft", "beds", "baths"]:
                self.assertEqual(row[field], original[field])
            row["property_type"] = "Land / Lot"
            scanner.enrich_sheriff_rows_from_county([row], max_lookups=0)
            self.assertEqual(row["property_type"], "Land / Lot")


class SheriffPdfReviewTests(unittest.TestCase):
    def document(self):
        header = "SHERIFF'S SALE PROPERTY LISTING\nDate of Sale: Monday, November 2, 2026\nPrinted: 10/12/2026\n"
        def block(number, address):
            return (f"Tracts\nStatus\n1\nCase Number\nGD-26-{number:06d} $1,000.00\n"
                    f"Sale Type\nReal Estate Sale\n{address}\nPittsburgh 123-A-{number}\nActive\nComments:\n")
        return header + block(1, "12 EXAMPLE ST\nPITTSBURGH, PA 15201") + \
            block(2, "UNREADABLE ADDRESS") + block(3, "SECOND UNREADABLE ADDRESS")

    def parsed(self):
        clock = scanner.EST_TZ.localize(datetime(2026, 10, 12, 12))
        with patch.object(scanner, "now_est", return_value=clock):
            return scanner.parse_sheriff_text(self.document(), scanner.SHERIFF_PAGE + "November-Sale-List.pdf")

    def test_two_unreadable_addresses_do_not_discard_a_valid_record(self):
        rows, audit = self.parsed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["address"], "12 Example St")
        self.assertEqual(audit["skipped_active_addresses"], 2)
        self.assertEqual(len(audit["unparsed_records"]), 2)
        for record in audit["unparsed_records"]:
            self.assertIsNone(record["address"])
            self.assertNotIn("price", record)
            self.assertIn("UNREADABLE ADDRESS", record["raw_source_text"])

    def test_raw_records_are_preserved_separately_and_repeated_reads_deduplicate(self):
        _, audit = self.parsed()
        originals = deepcopy(audit["unparsed_records"])
        with temporary_repo():
            scanner.preserve_sheriff_pdf_review(audit, "one")
            _, repeated = self.parsed()
            scanner.preserve_sheriff_pdf_review(repeated, "two")
            items = json.loads(Path(scanner.SCAN_REVIEW_FILE).read_text())["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual({item["status"] for item in items.values()}, {"needs_details"})
            self.assertEqual({item["record"]["id"] for item in items.values()}, {r["id"] for r in originals})
            for original in originals:
                self.assertIn(original, [item["record"] for item in items.values()])
            self.assertNotIn("unparsed_records", audit)
            self.assertEqual(audit["unparsed_records_retained_for_review"], 2)

    def test_expired_document_and_unrecognized_statuses_still_fail(self):
        clock = scanner.EST_TZ.localize(datetime(2026, 12, 1, 12))
        with patch.object(scanner, "now_est", return_value=clock), self.assertRaises(ValueError):
            scanner.parse_sheriff_text(self.document(), scanner.SHERIFF_PAGE)
        clock = scanner.EST_TZ.localize(datetime(2026, 10, 12, 12))
        with patch.object(scanner, "now_est", return_value=clock), self.assertRaises(ValueError):
            scanner.parse_sheriff_text(self.document().replace("\nActive\n", "\nUNVERIFIED\n"), scanner.SHERIFF_PAGE)

    def run_pipeline(self, broken_review=False):
        _, audit = self.parsed()
        profile = json.loads((ROOT / "manual_scan_config.json").read_text())
        profile.update({"counties": ["Allegheny"], "cities": [], "geoAreas": {}, "sectors": ["sheriff"]})
        write_json("manual_scan_config.json", profile)
        write_json("properties.json", [])
        if broken_review:
            Path(scanner.SCAN_REVIEW_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(scanner.SCAN_REVIEW_FILE).write_text("broken review")
        row = property_row("sheriff", city="Pittsburgh", zip="15201")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "pdf-test",
                                    "PA_SCAN_CONFIG_COMMIT": "", "PA_SCAN_REQUEST_ID": ""}), \
                patch.object(scanner, "CONFIG_FILE", "manual_scan_config.json"), \
                patch.object(scanner, "fetch_allegheny_sheriff_listings", return_value=([row], scanner.SHERIFF_PAGE, audit)), \
                patch.object(scanner, "enrich_sheriff_rows_from_county", return_value=0) as enrich, \
                patch.object(scanner.requests.sessions.Session, "send", side_effect=AssertionError("No live HTTP")), \
                redirect_stdout(io.StringIO()):
            scanner.run_orchestrator()
        return row, enrich

    def test_valid_rows_continue_but_unreadable_rows_stay_out_of_inventory(self):
        with temporary_repo():
            row, enrich = self.run_pipeline()
            enrich.assert_called_once()
            records = json.loads(Path("properties.json").read_text())
            self.assertEqual([r["id"] for r in records], [row["id"]])
            event = json.loads(Path("scanner_status.json").read_text())["last_event"]
            self.assertEqual(event["status"], "partial")
            self.assertEqual(event["sources"]["sheriff"]["pdf_records_retained_for_review"], 2)
            self.assertNotIn("tax", event["sources"], "An unselected source must not appear as disconnected in the current run")
            self.assertEqual(len(json.loads(Path(scanner.SCAN_REVIEW_FILE).read_text())["items"]), 2)

    def test_review_write_failure_stops_before_enrichment_and_inventory_write(self):
        with temporary_repo():
            _, enrich = self.run_pipeline(broken_review=True)
            enrich.assert_not_called()
            self.assertEqual(json.loads(Path("properties.json").read_text()), [])
            self.assertEqual(Path(scanner.SCAN_REVIEW_FILE).read_text(), "broken review")
            event = json.loads(Path("scanner_status.json").read_text())["last_event"]
            self.assertEqual(event["status"], "failed")
            self.assertIn("Could not preserve unresolved Sheriff", event["errors"][-1])


class WorkflowTests(unittest.TestCase):
    def test_failed_guard_records_current_request_instead_of_old_scan(self):
        workflow = (ROOT / ".github/workflows/cron-daily-mls.yml").read_text()
        self.assertIn("test_scan_controls test_scan_scope", workflow)
        self.assertIn("PA_SCAN_CONTEXT_FILE: scan_run_context.json", workflow)
        block = workflow.split('      - name: Record failed stage for the UI', 1)[1].split("      - name: Save data", 1)[0]
        code = block.split("python - <<'PY'\n", 1)[1].rsplit("          PY", 1)[0]
        with temporary_repo():
            Path("orchestrator.py").write_text('ORCHESTRATOR_VERSION = "test-version"\ninvalid syntax here')
            write_json("manual_scan_config.json", {"requestId": "unrelated-newer-request"})
            write_json("scanner_status.json", {"last_event": {"status": "success", "github_run_id": "old", "request_id": "old-request"}})
            env = {"FAILED_STAGE": "checks", "GITHUB_RUN_ID": "current-run", "GITHUB_EVENT_NAME": "workflow_dispatch",
                   "GITHUB_SHA": "current-commit", "PA_SCAN_CONFIG_FILE": "manual_scan_config.json",
                   "PA_SCAN_REQUEST_ID": "new-request", "PA_SCAN_CONFIG_COMMIT": "a" * 40}
            with patch.dict(os.environ, env):
                exec(compile(textwrap.dedent(code), "workflow-failure-handler", "exec"), {})
            event = json.loads(Path("scanner_status.json").read_text())["last_event"]
            self.assertEqual(event["status"], "failed")
            self.assertEqual(event["request_id"], "new-request")
            self.assertEqual(event["github_run_id"], "current-run")
            self.assertEqual(event["failure_stage"], "checks")
            self.assertEqual(event["sources"], {})


if __name__ == "__main__":
    unittest.main()

