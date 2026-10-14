import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scan_schedule as schedule
import automatic_listing_sources as worker
import property_sources as registry
from test_automatic_listing_sources import QueueTests, make_rows


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 18, 25, tzinfo=timezone.utc)
        self.spec = {'enabled': True, 'frequency': 'twice_daily', 'times': ['08:00', '20:00'], 'timezone': 'Asia/Jerusalem'}

    def test_two_daily_slots_and_no_repeat_after_completion(self):
        self.assertIn('20:00', schedule.due_slot(self.spec, self.now, '2026-10-12T08:17:00+03:00'))
        self.assertIsNone(schedule.due_slot(self.spec, self.now, '2026-10-12T20:17:00+03:00'))
        before = self.now.replace(hour=4)
        self.assertIsNone(schedule.due_slot(self.spec, before))

    def test_israel_and_new_york_use_their_own_local_clock_and_dst(self):
        self.assertIsNone(schedule.due_slot({**self.spec, 'times': ['08:00'], 'frequency': 'daily', 'timezone': 'America/New_York'}, self.now.replace(hour=10)))
        winter = datetime(2026, 12, 12, 7, tzinfo=timezone.utc)
        self.assertIn('+02:00', schedule.due_slot({**self.spec, 'frequency': 'daily', 'times': ['08:00']}, winter))

    def test_off_weekly_and_monthly_days(self):
        self.assertIsNone(schedule.due_slot({**self.spec, 'enabled': False}, self.now))
        weekly = {**self.spec, 'frequency': 'weekly', 'times': ['08:00'], 'day': 'Wednesday'}
        self.assertIsNone(schedule.due_slot(weekly, self.now))
        self.assertIsNotNone(schedule.due_slot({**weekly, 'day': 'Monday'}, self.now))
        monthly = {**weekly, 'frequency': 'monthly', 'dayOfMonth': 12}
        self.assertIsNotNone(schedule.due_slot(monthly, self.now))
        self.assertIsNone(schedule.due_slot({**monthly, 'dayOfMonth': 13}, self.now))

    def test_master_off_and_unselected_sector_are_not_due(self):
        p = {'autoScanEnabled': True, 'sectors': ['mls'], 'schedules': {'mls': self.spec, 'sheriff': self.spec}}
        self.assertEqual(schedule.scan_sectors(p, {}, self.now), ['mls'])
        self.assertEqual(schedule.scan_sectors({**p, 'autoScanEnabled': False}, {}, self.now), [])

    def test_reject_duplicate_times_and_unsupported_minutes(self):
        for times in [['08:00', '08:00'], ['08:01', '20:00']]:
            with self.assertRaises(ValueError):
                schedule.due_slot({**self.spec, 'times': times}, self.now)

    def test_completion_interval_cap_and_master(self):
        p = {'scheduleControlVersion': 2, 'autoScanEnabled': True,
             'completionSettings': {'enabled': True, 'frequency': 'hourly', 'timezone': 'Asia/Jerusalem', 'dailyLimit': 20}}
        self.assertTrue(schedule.completion_due(p, {}, self.now))
        self.assertFalse(schedule.completion_due(p, {'last_automatic_run': self.now.isoformat()}, self.now))
        self.assertFalse(schedule.completion_due(p, {'budget_day': '2026-10-12', 'daily_attempted': 20}, self.now))
        self.assertTrue(schedule.completion_due({**p, 'autoScanEnabled': False}, {}, self.now))
        self.assertFalse(schedule.completion_due({**p, 'scheduleControlVersion': 1}, {}, self.now))
        self.assertTrue(schedule.completion_due(p, {'budget_day': '2026-10-11', 'daily_attempted': 20}, self.now))

    def test_quarter_hour_repeats_only_in_the_next_slot(self):
        spec = {'frequency': 'every_15_minutes', 'timezone': 'UTC'}
        start = datetime(2026, 10, 14, 12, 7, tzinfo=timezone.utc)
        self.assertEqual(schedule.due_slot(spec, start), '2026-10-14T12:00:00+00:00')
        self.assertIsNone(schedule.due_slot(spec, start + timedelta(minutes=7), start.isoformat()))
        self.assertIn('12:15:00', schedule.due_slot(spec, start + timedelta(minutes=15), start.isoformat()))

    def test_delayed_runners_use_only_latest_slot_without_catchup_burst(self):
        spec = {'frequency': 'every_15_minutes', 'timezone': 'UTC'}
        delayed = datetime(2026, 10, 14, 13, 52, tzinfo=timezone.utc)
        slot = schedule.due_slot(spec, delayed, '2026-10-14T12:07:00+00:00')
        self.assertEqual(slot, '2026-10-14T13:45:00+00:00')
        self.assertIsNone(schedule.due_slot(spec, delayed, delayed.isoformat()))

    def test_quarter_hour_preserves_both_dst_fall_back_hours(self):
        spec = {'frequency': 'every_15_minutes', 'timezone': 'America/New_York'}
        repeated = datetime(2026, 11, 1, 6, 7, tzinfo=timezone.utc)
        slot = schedule.due_slot(spec, repeated, '2026-11-01T05:52:00+00:00')
        self.assertEqual(slot, '2026-11-01T01:00:00-05:00')
        self.assertIsNone(schedule.due_slot(spec, repeated, repeated.isoformat()))
        self.assertIsNotNone(schedule.due_slot(spec, repeated + timedelta(minutes=15), repeated.isoformat()))

    def test_quarter_hour_spring_forward_uses_actual_elapsed_time(self):
        spec = {'frequency': 'every_15_minutes', 'timezone': 'America/New_York'}
        current = datetime(2026, 3, 8, 7, 7, tzinfo=timezone.utc)
        self.assertEqual(schedule.due_slot(spec, current, '2026-03-08T06:52:00+00:00'),
                         '2026-03-08T03:00:00-04:00')

    def test_daily_budget_rolls_over_at_configured_local_midnight(self):
        profile = {'scheduleControlVersion': 2, 'autoScanEnabled': True,
            'completionSettings': {'enabled': True, 'frequency': 'every_15_minutes',
                                   'timezone': 'America/New_York', 'dailyLimit': 60, 'batchSize': 3}}
        state = {'budget_day': '2026-10-13', 'daily_attempted': 60}
        self.assertFalse(schedule.completion_due(profile, state, datetime(2026, 10, 14, 3, 52, tzinfo=timezone.utc)))
        self.assertTrue(schedule.completion_due(profile, state, datetime(2026, 10, 14, 4, 7, tzinfo=timezone.utc)))

    def test_quarter_hour_pause_and_exhausted_budget_are_not_due(self):
        profile = {'scheduleControlVersion': 2, 'autoScanEnabled': True,
            'completionSettings': {'enabled': True, 'frequency': 'every_15_minutes',
                                   'timezone': 'UTC', 'dailyLimit': 60, 'batchSize': 3}}
        self.assertTrue(schedule.completion_due({**profile, 'autoScanEnabled': False}, {}, self.now))
        self.assertFalse(schedule.completion_due({**profile,
            'completionSettings': {**profile['completionSettings'], 'enabled': False}}, {}, self.now))
        self.assertFalse(schedule.completion_due(profile, {'budget_day': '2026-10-12', 'daily_attempted': 60}, self.now))

    def test_completion_workflow_is_quarter_hour_and_uses_shared_write_lock(self):
        root = Path(__file__).resolve().parent
        workflow = (root / '.github/workflows/automatic-listing-sources.yml').read_text()
        self.assertIn("cron: '7,22,37,52 * * * *'", workflow)
        self.assertIn("'pa-repo-write-queue'", workflow)
        self.assertIn('cancel-in-progress: false', workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("requests.sessions.Session.request = blocked", workflow)
        self.assertIn('node test_completion_controls.cjs', workflow)


class RecentQueueTests(QueueTests):
    def test_recent_publication_not_first_seen_controls_scope(self):
        old = {**self.rows[0], 'listed_date': (self.now - timedelta(days=31)).date().isoformat(), 'first_seen': self.now.isoformat()}
        boundary = {**self.rows[1], 'listed_date': (self.now - timedelta(days=30)).strftime('%d/%m/%Y')}
        missing = {**self.rows[2], 'listed_date': '', 'first_seen': self.now.isoformat()}
        future = {**self.rows[3], 'listed_date': (self.now + timedelta(days=1)).date().isoformat()}
        self.rows = [old, boundary, missing, future, self.rows[4]]
        self.put_inventory()
        report = worker.run(self.repo, 'check', now=self.now)
        self.assertEqual(report['eligible_properties'], 2)
        self.assertEqual(report['excluded']['older_than_completion_scope'], 1)
        self.assertEqual(report['excluded']['listing_date_unknown'], 1)
        self.assertEqual(report['excluded']['future_listing_date'], 1)
        self.assertEqual((self.repo/'properties.json').read_bytes(), self.inventory_before)

    def test_start_date_is_saved_and_future_new_listings_join(self):
        worker.run(self.repo, 'pilot', reader=self.reader(), now=self.now)
        start = registry.read_json(self.repo/worker.STATE)['completion_start_date']
        later = self.now + timedelta(days=40)
        self.rows.append({**make_rows(1)[0], 'id': 'PA-MLS-9001', 'docket_id': 'MLS-9001', 'address': '9001 First St', 'listed_date': later.date().isoformat()})
        self.put_inventory()
        report = worker.run(self.repo, 'check', now=later)
        self.assertEqual(report['completion_start_date'], start)
        self.assertEqual(report['eligible_properties'], 6)

    def test_ui_activation_daily_cap_and_manual_profile_are_preserved(self):
        self.rows = make_rows(25)
        self.put_inventory()
        profile = {'scheduleControlVersion': 2, 'autoScanEnabled': True,
                   'completionSettings': {'enabled': True, 'frequency': 'hourly', 'timezone': 'UTC',
                       'dailyLimit': 20, 'batchSize': 5, 'startDate': (self.now-timedelta(days=30)).date().isoformat()}}
        registry.atomic_json(self.repo/'scan_config.json', profile)
        saved = (self.repo/'scan_config.json').read_bytes()
        first = worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now)
        self.assertEqual(first['new_snapshots'], 5)
        second = worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now+timedelta(hours=1))
        self.assertEqual(second['new_snapshots'], 5)
        reader = self.reader()
        third = worker.run(self.repo, 'automatic', reader=reader, now=self.now+timedelta(hours=2))
        self.assertEqual(third['new_snapshots'], 5)
        fourth = worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now+timedelta(hours=3))
        self.assertEqual(fourth['new_snapshots'], 5)
        reader = self.reader()
        final = worker.run(self.repo, 'automatic', reader=reader, now=self.now+timedelta(hours=4))
        self.assertEqual(final['status'], 'not_due_or_daily_limit')
        self.assertEqual(reader.requests, 0)
        self.assertEqual((self.repo/'scan_config.json').read_bytes(), saved)
        self.assertEqual((self.repo/'properties.json').read_bytes(), self.inventory_before)


if __name__ == '__main__':
    unittest.main()

