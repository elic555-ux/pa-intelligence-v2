"""Behavior checks for the offline identity pilot; every example is synthetic."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import property_sources as ps


def subject(**changes):
    row = {"id": "PA-MLS-100", "docket_id": "MLS-100", "address": "2346 Fremont Place",
           "city": "Pittsburgh", "county": "Allegheny", "state": "PA", "zip": "15216",
           "source": "Example listing", "source_type": "mls", "property_type": "Single Family",
           "url": "https://first.example/listings/100", "last_source_check": "2026-10-09",
           "price": 145000, "beds": 2, "baths": 1, "sqft": 1247}
    return {**row, **changes}


def observe(row, **facts):
    return ps.make_observation(row, row["source"], row["url"], "listing", facts,
                               "2026-10-09", source_record_id=row.get("docket_id"))


def registry(*rows):
    return ps.build_registry([observe(row, beds=2) for row in rows])


class IdentityTests(unittest.TestCase):
    def test_two_websites_one_property(self):
        a = subject()
        b = subject(id="second-999", url="https://second.example/homes/999", address="2346 Fremont Pl")
        result = registry(a, b)
        self.assertEqual(len(result["entities"]), 1)
        self.assertEqual(result["aliases"][a["id"]], result["aliases"][b["id"]])
        self.assertEqual(len(next(iter(result["entities"].values()))["sources"]), 2)

    def test_relisting_one_subject_two_listing_observations(self):
        a = subject()
        b = subject(id="PA-MLS-101", docket_id="MLS-101", url="https://second.example/101")
        r = registry(a, b)
        self.assertEqual(len(r["entities"]), 1)
        self.assertEqual(len(r["observations"]), 2)
        self.assertEqual(len(r["aliases"]), 2)

    def test_different_units_do_not_merge_even_same_parcel(self):
        a = subject(address="4601 Fifth Ave #621", parcel_id="0052J00019000000", property_type="Condo")
        b = subject(id="PA-MLS-101", address="4601 Fifth Avenue Apt 622", parcel_id="0052J00019000000", property_type="Condo")
        self.assertEqual(len(registry(a, b)["entities"]), 2)

    def test_unit_aliases_match(self):
        a = subject(address="4601 Fifth Ave #621", property_type="Condo")
        b = subject(id="PA-MLS-101", address="4601 Fifth Avenue Apartment 621", property_type="Condo")
        self.assertEqual(len(registry(a, b)["entities"]), 1)

    def test_condo_without_unit_not_joined_to_building(self):
        a = subject(address="4601 Fifth Ave", property_type="Condo")
        b = subject(id="second-100", address="4601 Fifth Avenue", property_type="Condo")
        r = registry(a, b)
        self.assertEqual(len(r["entities"]), 2)
        self.assertFalse(r["aliases"])

    def test_same_mls_number_different_addresses_not_merged(self):
        a = subject(mls_board="Example MLS")
        b = subject(address="2347 Fremont Pl", url="https://second.example/100", mls_board="Example MLS")
        r = registry(a, b)
        self.assertEqual(len(r["entities"]), 2)
        self.assertIn("PA-MLS-100", r["ambiguous_aliases"])

    def test_board_and_address_confirm_city_alias(self):
        a = subject(mls_board="Example MLS")
        b = subject(id="second-100", city="Beechview", mls_board="Example MLS", url="https://second.example/100")
        self.assertEqual(len(registry(a, b)["entities"]), 1)

    def test_city_difference_without_corroboration_not_merged(self):
        a = subject()
        b = subject(id="second-100", city="Beechview", url="https://second.example/100")
        self.assertEqual(len(registry(a, b)["entities"]), 2)

    def test_verified_parcel_allows_postal_city_difference(self):
        a = subject(parcel_id="0062E00036000000")
        b = subject(id="second-100", city="Beechview", parcel_id="0062E00036000000", url="https://second.example/100")
        self.assertEqual(len(registry(a, b)["entities"]), 1)

    def test_allegheny_printed_parcel_and_portal_pin_match(self):
        a = subject(parcel_id="83-A-285")
        b = subject(id="second", city="Beechview", parcel_id="0083A00285000000")
        self.assertEqual(len(registry(a, b)["entities"]), 1)

    def test_conflicting_parcels_not_bridged_by_unknown_parcel(self):
        rows = [subject(id="first", parcel_id="11-A-1"),
                subject(id="unknown", url="https://second.example/100"),
                subject(id="third", parcel_id="11-A-2", url="https://third.example/100")]
        r = registry(*rows)
        self.assertEqual(len(r["entities"]), 3)
        self.assertFalse(r["aliases"])
        self.assertIn("transitive_identity_conflict", {e["reason"] for e in r["identity_review"]})

    def test_multi_parcel_sale_not_joined_to_one_component(self):
        a = subject(parcel_ids=["1-A-1", "1-A-2", "1-A-3"])
        b = subject(id="county-1", parcel_id="1-A-1", url="https://county.example/1")
        r = registry(a, b)
        self.assertEqual(len(r["entities"]), 2)
        self.assertFalse(r["aliases"])

    def test_separate_parcel_claims_at_one_address_need_review(self):
        r = registry(subject(parcel_id="11-A-1"), subject(id="second", parcel_id="11-A-2"))
        self.assertEqual(len(r["entities"]), 2)
        self.assertFalse(r["aliases"])

    def test_explicit_unit_field_and_address_unit_must_agree(self):
        a = subject(address="4601 Fifth Ave", unit="621", property_type="Condo")
        b = subject(id="second", address="4601 Fifth Avenue #621", property_type="Condo")
        self.assertEqual(len(registry(a, b)["entities"]), 1)
        c = subject(id="third", address="4601 Fifth Avenue #622", unit="621", property_type="Condo")
        r = registry(a, c)
        self.assertEqual(len(r["entities"]), 2)
        self.assertIn("third", r["ambiguous_aliases"])

    def test_fraction_range_and_direction_preserved(self):
        for address in ["2346 1/2 Fremont Pl", "2346-2347 Fremont Pl", "2346 N Fremont Pl"]:
            with self.subTest(address=address):
                self.assertEqual(len(registry(subject(), subject(id="second", address=address))["entities"]), 2)

    def test_other_state_and_county_not_same_subject(self):
        for changes in [{"state": "OH"}, {"county": "Erie"}, {"zip": "15217"}]:
            with self.subTest(changes=changes):
                self.assertEqual(len(registry(subject(), subject(id="second", **changes))["entities"]), 2)

    def test_repeat_input_is_idempotent(self):
        o = observe(subject(), sqft=1247)
        self.assertEqual(len(ps.build_registry([o, deepcopy(o)])["observations"]), 1)


class EvidenceTests(unittest.TestCase):
    def test_conflicting_values_preserved_without_recommendation(self):
        a = observe(subject(), sqft=1247)
        b = observe(subject(id="second", url="https://second.example/100"), sqft=1600)
        r = ps.build_registry([a, b])
        field = next(iter(r["entities"].values()))["fields"]["sqft"]
        self.assertEqual(field["status"], "conflicting_source_values")
        self.assertIsNone(field["value"])
        self.assertEqual({e["value"] for e in field["observations"]}, {1247, 1600})

    def test_numeric_formatting_not_false_conflict(self):
        fields = ps.aggregate([observe(subject(), sqft=1247), observe(subject(id="second"), sqft="1,247")])
        self.assertEqual(fields["sqft"]["status"], "reported_by_source")

    def test_financial_estimates_not_imported_as_source_facts(self):
        o = observe(subject(), arv=300000, monthly_rent_est=1300, flip_rehab=20000, price=145000)
        self.assertEqual(set(o["facts"]), {"price"})

    def test_legacy_technical_defaults_not_promoted(self):
        o = observe(subject(), occupancy="פנוי / בתיאום", roof_condition="תקין", parking="חניה מוסדרת")
        self.assertFalse(o["facts"])

    def test_roof_material_separate_from_condition(self):
        o = observe(subject(), roof_type={"value": "Composition", "source": "Example", "status": "published"})
        self.assertIn("roof_type", o["facts"])
        self.assertNotIn("roof_condition", o["facts"])

    def test_known_property_type_translation_not_false_conflict(self):
        fields = ps.aggregate([observe(subject(), property_type="Single Family"),
                               observe(subject(id="second"), property_type="בית חד משפחתי")])
        self.assertEqual(fields["property_type"]["status"], "reported_by_source")

    def test_fact_and_photo_from_other_source_not_attached(self):
        row = subject()
        o = ps.make_observation(row, row["source"], row["url"], "listing",
            {"roof_type":{"value":"Asphalt", "source":"Other", "source_url":"https://other.example/999"}},
            photos=[{"url":"https://other.example/image.jpg", "source_url":"https://other.example/999"}])
        self.assertFalse(o["facts"])
        self.assertFalse(o["photos"])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.properties = self.repo / "properties.json"
        self.properties.write_text(json.dumps([subject()]), encoding="utf-8")
        self.original = self.properties.read_bytes()

    def test_build_never_writes_original_or_budget_and_is_idempotent(self):
        budget = self.repo / "COMPS_REPORTS/rentcast_usage.json"
        budget.parent.mkdir(parents=True)
        budget.write_text('{"usage":3,"allow_new_api_requests":false}')
        before = budget.read_bytes()
        r, manifest = ps.prepare(self.repo)
        ps.save(self.repo, r, manifest)
        files = {str(p.relative_to(self.repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (self.repo / ps.ROOT).rglob("*.json")}
        r, manifest = ps.prepare(self.repo)
        ps.save(self.repo, r, manifest)
        after = {str(p.relative_to(self.repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (self.repo / ps.ROOT).rglob("*.json")}
        self.assertEqual(files, after)
        self.assertEqual(self.properties.read_bytes(), self.original)
        self.assertEqual(budget.read_bytes(), before)
        self.assertEqual(ps.lookup(self.repo, "PA-MLS-100")["status"], "matched")

    def test_court_price_never_asking_price(self):
        row = subject(source_type="tax", price=250)
        self.properties.write_text(json.dumps([row]))
        r, _ = ps.prepare(self.repo)
        self.assertNotIn("price", next(iter(r["entities"].values()))["fields"])

    def test_embedded_county_snapshot_must_match_parcel_and_url(self):
        row = subject(parcel_id="83-A-285", county_property_data={
            "pin":"0083A00286000000", "beds":6, "roof_type":"Shingle",
            "source_url":"https://realestate.alleghenycounty.us/BuildingInfo?ID=0083A00286000000"})
        self.properties.write_text(json.dumps([row]))
        r, manifest = ps.prepare(self.repo)
        self.assertEqual(manifest["excluded_county_snapshots"],1)
        self.assertNotIn("roof_type",next(iter(r["entities"].values()))["fields"])

    def test_detail_snapshot_wrong_listing_excluded(self):
        folder = self.repo / "COMPS_REPORTS/listing_details"
        folder.mkdir(parents=True)
        record = {"property_id": "PA-MLS-100", "identity": ["2346 FREMONT PL", "PITTSBURGH", "PA", "15216"],
                  "listing_id": "101", "source_url": subject()["url"], "status": "published",
                  "facts": {"roof_type": {"value": "Asphalt", "source": "Example"}}}
        (folder / "wrong.json").write_text(json.dumps(record))
        r, manifest = ps.prepare(self.repo)
        self.assertEqual(manifest["excluded_listing_snapshots"], 1)
        self.assertNotIn("roof_type", next(iter(r["entities"].values()))["fields"])

    def test_offline_prepare_uses_no_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network is forbidden")):
            _, manifest = ps.prepare(self.repo)
        self.assertEqual(manifest["new_network_requests"], 0)
        self.assertEqual(manifest["new_rentcast_calls"], 0)

    def test_later_parcel_enrichment_preserves_property_id(self):
        r, manifest = ps.prepare(self.repo)
        old_id = r["aliases"]["PA-MLS-100"]
        ps.save(self.repo, r, manifest)
        self.properties.write_text(json.dumps([subject(parcel_id="0062E00036000000")]))
        r, manifest = ps.prepare(self.repo)
        self.assertEqual(r["aliases"]["PA-MLS-100"], old_id)
        ps.save(self.repo, r, manifest)
        self.assertEqual(ps.lookup(self.repo, "PA-MLS-100")["entity"]["subject"]["parcels"], ["0062e00036000000"])

    def test_identity_split_does_not_reuse_one_id_for_two_entities(self):
        a, b = subject(), subject(id="second", url="https://second.example/100")
        self.properties.write_text(json.dumps([a,b]))
        r, manifest = ps.prepare(self.repo)
        old_id = r["aliases"]["PA-MLS-100"]
        ps.save(self.repo, r, manifest)
        self.properties.write_text(json.dumps([{**a,"parcel_id":"11-A-1"},{**b,"parcel_id":"11-A-2"}]))
        r, _ = ps.prepare(self.repo)
        self.assertEqual(len(r["entities"]), 2)
        self.assertFalse(r["aliases"])
        self.assertNotIn(old_id, r["entities"])


if __name__ == "__main__":
    unittest.main()
