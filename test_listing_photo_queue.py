import copy
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest

import automatic_listing_sources as worker
import completion_wakeup as wakeup
import listing_photo_fallback as photo
import property_sources as registry
import tarasa_listing_source as source
from test_listing_photo_fallback import ROW, FakeReader
from test_tarasa_listing_source import tarasa_page
from datetime import datetime, timezone


class PhotoQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.now = datetime(2026, 10, 18, 13, 7, tzinfo=timezone.utc)
        self.rows = [{**copy.deepcopy(ROW), 'price': 84900, 'listed_date': '2026-10-18', 'beds': 2, 'sqft': 900}]
        self.profile = {'scheduleControlVersion': 2, 'maxPrice': 190000,
            'completionSettings': {'enabled': True, 'photoFallbackEnabled': True,
                'frequency': 'every_15_minutes', 'batchSize': 3, 'dailyLimit': 60,
                'timezone': 'UTC', 'startDate': '2026-09-12'}}
        self.save()
    def save(self):
        registry.atomic_json(self.repo/'properties.json', self.rows)
        registry.atomic_json(self.repo/'scan_config.json', self.profile)
        self.before = (self.repo/'properties.json').read_bytes()
    def cache(self, row):
        url = source.target_url(row)
        record = source.parse_detail(tarasa_page(row), row, url, self.now.isoformat())
        registry.atomic_json(self.repo/source.ROOT/(source.cache_key(row['id'])+'.json'), record)
    def primary_reader(self):
        now, rows = [0], self.rows
        class Opener:
            def open(self, request, timeout):
                if request.full_url.endswith('/robots.txt'):
                    return io.BytesIO(b'User-agent: *\nAllow: /\n')
                key = request.full_url.split('/')[6]
                row = next(r for r in rows if registry.listing_id(r) == key)
                return io.BytesIO(tarasa_page(row).encode())
        return source.PublicReader(opener=Opener(), clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0]+delay), max_requests=13)
    def test_settled_details_still_get_photo_fallback_in_same_daily_budget(self):
        self.cache(self.rows[0])
        report = worker.run(self.repo, 'automatic', now=self.now, photo_reader=FakeReader())
        self.assertEqual(report['status'], 'batch_complete')
        self.assertEqual(report['verified_photos'], 3)
        self.assertEqual(report['attempted_properties'], 1)
        self.assertEqual(report['photo_pending'], 0)
        self.assertEqual(report['pending'], 0)
        state = registry.read_json(self.repo/worker.STATE)
        self.assertEqual(state['daily_attempted'], 1)
        self.assertEqual(state['worker_state'], 'idle')
        self.assertEqual((self.repo/'properties.json').read_bytes(), self.before)
    def test_one_reserved_photo_turn_and_total_batch_request_limits(self):
        self.rows += [{**copy.deepcopy(self.rows[0]), 'id': 'PA-MLS-'+str(n),
            'docket_id': 'MLS-'+str(n), 'address': str(12+(n-1001)*2)+' First St',
            'url': ROW['url']+str(n)} for n in (1002, 1003)]
        self.save()
        report = worker.run(self.repo, 'automatic', now=self.now,
            reader=self.primary_reader(), photo_reader=FakeReader())
        self.assertLessEqual(len(report['results']), 2)
        self.assertLessEqual(len(report['photo_results']), 1)
        self.assertLessEqual(report['attempted_properties'], 3)
        self.assertLessEqual(report['new_source_network_requests'], 13)
    def test_last_daily_turn_can_complete_photo_then_limit_stops_all_network(self):
        registry.atomic_json(self.repo/worker.STATE, {'schema': 1, 'enabled': True, 'entries': {},
            'budget_day': self.now.date().isoformat(), 'daily_attempted': 59})
        report = worker.run(self.repo, 'automatic', now=self.now,
            reader=self.primary_reader(), photo_reader=FakeReader())
        self.assertEqual(report['attempted_properties'], 1)
        state = registry.read_json(self.repo/worker.STATE)
        self.assertEqual(state['daily_attempted'], 60)
        fresh = FakeReader()
        later = worker.run(self.repo, 'automatic', now=self.now+timedelta(minutes=15), photo_reader=fresh)
        self.assertEqual(fresh.requests, 0)
        self.assertEqual(later['status'], 'not_due_or_daily_limit')
    def test_idle_gate_wakes_due_photos_and_sleeps_until_saved_retry(self):
        state = {'worker_state': 'idle', 'photo_fallback_schema': 1,
            'input_fingerprint': wakeup.input_fingerprint(self.repo, self.profile, self.now.date()),
            'next_photo_check_at': (self.now+timedelta(days=1)).isoformat()}
        self.assertTrue(wakeup.idle_unchanged(self.repo, self.profile, state, current=self.now))
        self.assertFalse(wakeup.idle_unchanged(self.repo, self.profile, state, current=self.now+timedelta(days=2)))
        state.pop('photo_fallback_schema')
        self.assertFalse(wakeup.idle_unchanged(self.repo, self.profile, state, current=self.now))
    def test_new_source_photo_urls_wake_settled_subject_without_inventory_change(self):
        self.cache(self.rows[0])
        before = wakeup.input_fingerprint(self.repo, self.profile, self.now.date())
        path = self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json')
        record = registry.read_json(path)
        record['photos'][0]['url'] = record['photos'][0]['url'].replace('/v123/', '/v999/')
        # Fixture version strings can differ; always create a real URL change.
        if record == registry.read_json(path):
            record['photos'][0]['url'] += '?changed=1'
        registry.atomic_json(path, record)
        after = wakeup.input_fingerprint(self.repo, self.profile, self.now.date())
        self.assertNotEqual(before, after)
        self.assertEqual((self.repo/'properties.json').read_bytes(), self.before)
    def test_queue_reuses_fresh_photos_and_retries_only_missing_or_expired(self):
        self.cache(self.rows[0])
        rows = {r['id']: r for r in self.rows}
        photo.run(self.repo, [ROW['id']], reader=FakeReader(), now=self.now, save=True)
        due, pending, when = photo.queue(self.repo, rows, self.now)
        self.assertEqual(due, [])
        self.assertEqual(pending, 0)
        self.assertIsNotNone(when)
        due, pending, _ = photo.queue(self.repo, rows, self.now+timedelta(days=8))
        self.assertEqual(due, [ROW['id']])
        self.assertEqual(pending, 1)


if __name__ == '__main__':
    unittest.main()
