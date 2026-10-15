"""Idle/wake lifecycle with saved inventory, fake HTML, and blocked discovery."""
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import automatic_listing_sources as worker
from completion_wakeup import input_fingerprint, idle_unchanged
import property_sources as registry
import scan_schedule
import test_automatic_listing_sources as fixtures
from test_automatic_listing_sources import make_rows


class CompletionWakeupTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.QueueTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.integrated_config()
        self.fx.rows = self.fx.rows[:1]
        self.fx.put_inventory()

    def run_worker(self, days=0, mode='automatic', **reader_options):
        reader = self.fx.reader(**reader_options)
        result = worker.run(self.fx.repo, mode, reader=reader,
                            now=self.fx.now + timedelta(days=days))
        return result, reader

    def test_empty_queue_sleeps_and_repeated_wake_checks_do_not_write(self):
        self.fx.rows = []
        self.fx.put_inventory()
        result, _ = self.run_worker()
        self.assertEqual(result['worker_state'], 'idle')
        self.assertEqual(result['idle_reason'], 'no_eligible_properties')
        before = {path: path.read_bytes() for path in self.fx.repo.rglob('*.json')}
        result, reader = self.run_worker(days=1)
        self.assertEqual(result['status'], 'idle_waiting_for_new_properties')
        self.assertFalse(result['should_save'])
        self.assertEqual(reader.requests, 0)
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))

    def test_new_property_wakes_but_expired_success_does_not_refetch(self):
        first, _ = self.run_worker()
        self.assertEqual(first['worker_state'], 'idle')
        state = registry.read_json(self.fx.repo / worker.STATE)
        profile = registry.read_json(self.fx.repo / 'scan_config.json')
        old_hash = input_fingerprint(self.fx.repo, profile)
        self.fx.rows[0].update(last_source_check='later', last_scan_id='S2', days_on_market=99)
        self.fx.put_inventory()
        self.assertEqual(input_fingerprint(self.fx.repo, profile), old_hash)
        result, reader = self.run_worker(days=8)
        self.assertEqual(result['status'], 'idle_waiting_for_new_properties')
        self.assertEqual(reader.requests, 0)
        self.fx.rows.append(make_rows(2)[1])
        self.fx.put_inventory()
        self.assertFalse(idle_unchanged(self.fx.repo, profile, state))
        result, reader = self.run_worker(days=8)
        self.assertEqual(result['attempted_properties'], 1)
        self.assertEqual([r['property_id'] for r in result['results']], ['PA-MLS-1002'])
        self.assertEqual(result['worker_state'], 'idle')

    def test_two_failed_attempts_sleep_and_new_arrival_does_not_reset_old_failure(self):
        first, _ = self.run_worker(missing=('1001',))
        self.assertEqual(first['pending'], 1)
        self.assertEqual(first['worker_state'], 'active')
        second, _ = self.run_worker(days=2, missing=('1001',))
        self.assertEqual(second['incomplete_properties'], 1)
        self.assertEqual(second['worker_state'], 'idle')
        self.fx.rows.append(make_rows(2)[1])
        self.fx.put_inventory()
        third, _ = self.run_worker(days=4)
        self.assertEqual([r['property_id'] for r in third['results']], ['PA-MLS-1002'])
        state = registry.read_json(self.fx.repo / worker.STATE)
        self.assertEqual(state['entries']['PA-MLS-1001']['unsuccessful_attempts'], 2)
        self.assertEqual(state['entries']['PA-MLS-1001']['status'], 'completion_incomplete')

    def test_waiting_retry_is_active_and_manual_disable_is_respected(self):
        first, _ = self.run_worker(missing=('1001',))
        self.assertEqual(first['worker_state'], 'active')
        self.fx.integrated_config(enabled=False)
        self.fx.rows.append(make_rows(2)[1])
        self.fx.put_inventory()
        result, reader = self.run_worker(days=2)
        self.assertEqual(result['status'], 'automatic_disabled')
        self.assertEqual(reader.requests, 0)

    def test_scheduler_skips_idle_before_dependencies_and_wakes_for_review_candidate(self):
        self.fx.rows = []
        self.fx.put_inventory()
        self.run_worker()
        current_dir = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            try:
                os.chdir(self.fx.repo)
                with patch('sys.argv', ['scan_schedule.py', 'completion']), \
                        patch.object(scan_schedule, 'completion_due', return_value=True), \
                        patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                    scan_schedule.main()
                    self.assertEqual(output.read_text().strip(), 'due=false')
                    row = make_rows(1)[0]
                    registry.atomic_json(Path('COMPS_REPORTS/scan_review/records.json'),
                        {'schema': 1, 'items': {'new': {'status': 'needs_details', 'record': row}}})
                    output.write_text('')
                    scan_schedule.main()
                    self.assertEqual(output.read_text().strip(), 'due=true')
            finally:
                os.chdir(current_dir)

    def test_new_listing_identity_can_retry_without_inheriting_old_failure(self):
        self.run_worker(missing=('1001',))
        self.run_worker(days=2, missing=('1001',))
        self.fx.rows[0]['url'] += '?listing=new'
        self.fx.put_inventory()
        result, _ = self.run_worker(days=4)
        self.assertEqual(result['attempted_properties'], 1)
        self.assertEqual(result['new_snapshots'], 1)

    def test_resolved_ambiguous_identity_wakes_without_new_inventory(self):
        alias_path = self.fx.repo / registry.ROOT / 'aliases.json'
        key = self.fx.rows[0]['id']
        registry.atomic_json(alias_path, {'ambiguous': {key: ['first', 'other']}})
        first, _ = self.run_worker()
        self.assertEqual(first['worker_state'], 'idle')
        self.assertEqual(first['excluded']['ambiguous_identity'], 1)
        registry.atomic_json(alias_path, {'ambiguous': {}})
        result, reader = self.run_worker(days=1)
        self.assertEqual(result['attempted_properties'], 1)
        self.assertEqual(result['new_snapshots'], 1)
        self.assertEqual(reader.requests, 2)
        result, reader = self.run_worker(days=2)
        self.assertEqual(result['status'], 'idle_waiting_for_new_properties')
        self.assertEqual(reader.requests, 0)

    def test_future_publication_wakes_only_when_it_becomes_eligible(self):
        self.fx.rows[0]['listed_date'] = (self.fx.now.date() + timedelta(days=2)).isoformat()
        self.fx.put_inventory()
        first, _ = self.run_worker()
        self.assertEqual(first['excluded']['future_listing_date'], 1)
        self.assertEqual(first['worker_state'], 'idle')
        profile = registry.read_json(self.fx.repo / 'scan_config.json')
        state = registry.read_json(self.fx.repo / worker.STATE)
        self.assertTrue(idle_unchanged(self.fx.repo, profile, state,
            today=(self.fx.now + timedelta(days=1)).date()))
        self.assertFalse(idle_unchanged(self.fx.repo, profile, state,
            today=(self.fx.now + timedelta(days=2)).date()))
        result, reader = self.run_worker(days=1)
        self.assertEqual(result['status'], 'idle_waiting_for_new_properties')
        self.assertEqual(reader.requests, 0)
        result, reader = self.run_worker(days=2)
        self.assertEqual(result['new_snapshots'], 1)
        self.assertEqual(result['attempted_properties'], 1)
        result, reader = self.run_worker(days=3)
        self.assertEqual(result['status'], 'idle_waiting_for_new_properties')
        self.assertEqual(reader.requests, 0)

    def test_unrelated_alias_changes_do_not_wake_idle_worker(self):
        self.run_worker()
        registry.atomic_json(self.fx.repo / registry.ROOT / 'aliases.json',
            {'ambiguous': {'unrelated-id': ['first', 'other']}})
        result, reader = self.run_worker(days=1)
        self.assertEqual(result['status'], 'idle_waiting_for_new_properties')
        self.assertEqual(reader.requests, 0)
