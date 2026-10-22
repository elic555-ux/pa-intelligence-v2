"""Offline shared-budget regression checks. No provider calls or credentials."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import rentcast_budget as budget


def fresh():
    return {"schema_version": 2, "provider": "RentCast", "billing_day": 22,
            "cycle_start": "2026-10-22", "next_reset": "2026-11-22",
            "legacy_budget_used": 0, "confirmed_successful_calls": 0,
            "request_journal": {}, "legacy_request_history": {},
            "migration_status": "completed", "billing_cycle_verified": True,
            "used": 0, "successful_api_calls": 0, "reserved_requests": {}}


class ReportProviderBudgetTests(unittest.TestCase):
    def setUp(self):
        self.ledger = fresh()
        self.patches = [patch.object(budget, "clock", return_value=datetime(2026, 10, 22, 19, tzinfo=timezone.utc)),
                        patch.object(budget, "read", side_effect=lambda _: copy.deepcopy(self.ledger)),
                        patch.object(budget, "write", side_effect=lambda _, value: setattr(self, "ledger", copy.deepcopy(value)))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_new_verified_cycle_reserves_both_calls_before_network(self):
        result = budget.reserve(Path("offline"), "report-1", "123", ["value", "rent"])
        self.assertEqual(result["used"], 2)
        self.assertEqual(result["confirmed_successful_calls"], 0)
        self.assertEqual(result["reserved_requests"]["report-1"]["count"], 2)

    def test_one_success_one_unknown_keeps_both_calls_in_budget(self):
        budget.reserve(Path("offline"), "report-1", "123", ["value", "rent"])
        budget.mark_sent(Path("offline"), "report-1", "value", "123")
        budget.record_response(Path("offline"), "report-1", "value", "123", http_status=200, body_present=True)
        result = budget.record_response(Path("offline"), "report-1", "rent", "123")
        self.assertEqual(result["used"], 2)
        self.assertEqual(result["confirmed_successful_calls"], 1)
        self.assertEqual(result["request_journal"]["report-1"]["slots"]["rent"]["state"], "unknown")

    def test_stop_at_45_and_duplicate_replay_cannot_spend_again(self):
        self.ledger.update(legacy_budget_used=44, used=44, successful_api_calls=44)
        with self.assertRaisesRegex(ValueError, "usage_guard_blocked"):
            budget.reserve(Path("offline"), "too-large", "123", ["value", "rent"])
        budget.reserve(Path("offline"), "one", "123", ["value"])
        self.assertEqual(self.ledger["used"], 45)
        with self.assertRaisesRegex(ValueError, "request_already_reserved"):
            budget.reserve(Path("offline"), "one", "123", ["value"])
        with self.assertRaisesRegex(ValueError, "usage_guard_blocked"):
            budget.reserve(Path("offline"), "another", "123", ["rent"])

    def test_provider_http_error_releases_hold_without_counting_as_billed_success(self):
        budget.reserve(Path("offline"), "report-1", "123", ["value"])
        result = budget.record_response(Path("offline"), "report-1", "value", "123", http_status=403, body_present=True)
        self.assertEqual(result["used"], 0)
        self.assertEqual(result["confirmed_successful_calls"], 0)

    def test_unverified_or_previous_cycle_cannot_reserve_calls(self):
        self.ledger["billing_cycle_verified"] = False
        with self.assertRaisesRegex(ValueError, "usage_guard_blocked"):
            budget.reserve(Path("offline"), "report-1", "123", ["value"])
        self.ledger = fresh()
        self.ledger["cycle_start"] = "2026-09-22"
        with self.assertRaisesRegex(ValueError, "usage_ledger_invalid_cycle"):
            budget.reserve(Path("offline"), "report-1", "123", ["value"])

    def test_reconciliation_preserves_previous_cycle_history_and_provider_evidence(self):
        # Read the committed evidence directly, outside the mocked storage adapter.
        root = Path(__file__).parent / "COMPS_REPORTS"
        current = json.loads((root / "rentcast_usage.json").read_text(encoding="utf-8"))
        archive = Path(__file__).parent / current["previous_cycle_archive"]
        old = json.loads(archive.read_text(encoding="utf-8"))
        self.assertEqual(old["cycle_start"], "2026-09-22")
        self.assertEqual(old["used"], 3)
        self.assertEqual(current["verification"]["provider_reported_used"], 0)
        self.assertEqual(current["verification"]["provider_plan_limit"], 50)
        self.assertEqual(current["legacy_request_history"], old["legacy_request_history"])


if __name__ == "__main__":
    unittest.main()
