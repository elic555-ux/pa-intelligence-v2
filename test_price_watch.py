"""Offline price-band boundaries, persistence and promotion after a real drop."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import automatic_listing_sources as completion
import orchestrator as scanner
import property_sources as registry
from price_watch import watch_ceiling, classify_price
from test_secondary_listing_source import ROW

ROOT = Path(__file__).parent


class PriceWatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.profile = json.loads((ROOT / 'manual_scan_config.json').read_text(encoding='utf-8'))
        self.profile.update(counties=['Allegheny'], cities=[], geoAreas={}, sectors=['mls'],
                            minPrice=50000, maxPrice=190000)
        registry.atomic_json(self.repo / 'manual_scan_config.json', self.profile)
        registry.atomic_json(self.repo / 'scan_config.json', self.profile)
        registry.atomic_json(self.repo / 'properties.json', [])
        self.calls = []

    def row(self, number, price):
        return {**deepcopy(ROW), 'id': 'PA-MLS-' + str(number), 'docket_id': 'MLS-' + str(number),
                'address': str(number) + ' First St', 'source_scan_area': 'Allegheny County',
                'property_type': 'Single Family', 'price': price, 'market_status': 'active',
                'listed_date': datetime.now(timezone.utc).date().isoformat()}

    def scan(self, rows):
        original = Path.cwd()
        def fetch(city, low, high, audit):
            self.calls.append((low, high))
            audit.update(status='success', rows=len(rows))
            return deepcopy(rows)
        try:
            os.chdir(self.repo)
            with patch.object(scanner, 'CONFIG_FILE', 'manual_scan_config.json'), \
                    patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'workflow_dispatch',
                        'PA_SCAN_CONFIG_COMMIT': '', 'PA_SCAN_REQUEST_ID': ''}), \
                    patch.object(scanner, 'fetch_live_mls_for_city', side_effect=fetch), \
                    redirect_stdout(io.StringIO()):
                scanner.run_orchestrator()
        finally:
            os.chdir(original)
        return registry.read_json(self.repo / 'scanner_status.json')['last_event']

    def test_exact_inclusive_boundary_uses_decimal_dollars(self):
        self.assertEqual(watch_ceiling(190000), 209000)
        self.assertEqual(classify_price(190000, 190000), 'within_budget')
        self.assertEqual(classify_price(190001, 190000), 'background_watch')
        self.assertEqual(classify_price(209000, 190000), 'background_watch')
        self.assertEqual(classify_price(209001, 190000), 'historical_outside_watch')
        self.assertEqual(classify_price(None, 190000), 'price_unknown')

    def test_background_rows_are_saved_separately_and_historical_rows_survive(self):
        historical = self.row(9000, 239000)
        registry.atomic_json(self.repo / 'properties.json', [historical])
        event = self.scan([self.row(1001, 190000), self.row(1002, 209000), self.row(1003, 209001)])
        self.assertEqual(self.calls, [(50000, 209000)])
        self.assertEqual(event['after_filters'], 1)
        self.assertEqual(event['background_price_watch_rows'], 1)
        saved = {p['id']: p for p in registry.read_json(self.repo / 'properties.json')}
        self.assertNotIn('PA-MLS-1003', saved)
        self.assertEqual(saved['PA-MLS-1002']['price_watch_status'], 'background_watch')
        self.assertEqual(saved['PA-MLS-9000']['price'], 239000)
        self.assertNotIn('last_scan_id', saved['PA-MLS-9000'])

    def test_watch_price_drop_enters_regular_results_and_keeps_price_history(self):
        self.scan([self.row(1001, 205000)])
        event = self.scan([self.row(1001, 185000)])
        saved = registry.read_json(self.repo / 'properties.json')[0]
        self.assertEqual(event['after_filters'], 1)
        self.assertEqual(saved['price_watch_status'], 'within_budget')
        self.assertEqual(saved['price'], 185000)
        self.assertEqual([item['price'] for item in saved['price_history']], [205000, 185000])
        self.assertEqual(saved['price_drop_history'][-1]['previous_price'], 205000)
        self.assertEqual(saved['price_drop_history'][-1]['current_price'], 185000)

    def test_non_price_filters_still_apply_to_watch_band(self):
        invalid = self.row(1001, 200000)
        invalid['sqft'] = 5000
        event = self.scan([invalid])
        self.assertEqual(event['background_price_watch_rows'], 0)
        self.assertEqual(registry.read_json(self.repo / 'properties.json'), [])

    def test_completion_skips_above_band_without_deleting_history(self):
        rows = [self.row(1001, 190000), self.row(1002, 209000), self.row(1003, 239000)]
        registry.atomic_json(self.repo / 'properties.json', rows)
        before = (self.repo / 'properties.json').read_bytes()
        eligible, excluded = completion.eligible_rows(self.repo)
        self.assertEqual(set(eligible), {'PA-MLS-1001', 'PA-MLS-1002'})
        self.assertEqual(excluded['above_price_watch_ceiling'], 1)
        self.assertEqual((self.repo / 'properties.json').read_bytes(), before)

    def test_completion_prefers_within_budget_rows_in_the_same_waiting_group(self):
        now = datetime.now(timezone.utc)
        due = {'inside': self.row(1001, 180000), 'background': self.row(9001, 205000)}
        entries = {key: {'first_queued_at': now.isoformat()} for key in due}
        self.assertEqual(completion.select_candidates(due, entries, 1, None, now, 190000), ['inside'])


if __name__ == '__main__':
    unittest.main()
