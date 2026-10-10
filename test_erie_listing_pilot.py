"""Offline regression tests for a manual, resumable, three-property Erie pilot."""
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import erie_listing_pilot as worker
import eriemoves_listing_source as source
import property_sources as registry
from test_eriemoves_listing_source import ROW, SPEC, page


def make_rows(count=7):
    return [{**copy.deepcopy(ROW), 'id': 'PA-MLS-' + str(5001 + i),
        'docket_id': 'MLS-' + str(5001 + i), 'address': str(12 + 2*i) + ' First St',
        'url': 'https://www.redfin.com/PA/Erie/test/home/' + str(5001 + i),
        'market_status': 'active', 'last_scan_id': 'unchanged'} for i in range(count)]


def listing_url(row, listing_number=None):
    number = listing_number or str(900000 + int(registry.listing_id(row)))
    return source.ORIGIN + '/listing/PA/Erie/' + row['address'].replace(' ', '-') + '-16504/' + number


def directory(rows, current=1, last=1, status='Active'):
    cards = []
    for row in rows:
        cards.append('<div class="singlelisting"><a class="linktooverlay" href="' + listing_url(row) + '">' +
            '<span class="status-label">' + status + '</span><div class="single-listing-address">' +
            row['address'] + ' Erie, PA 16504</div><div class="single-listing-mlsnumber">MLS# ' +
            registry.listing_id(row) + '</div></a></div>')
    canonical = source.ORIGIN + '/listings/my-active-listings' + ('?page=' + str(current) if current > 1 else '')
    return ('<link rel="canonical" href="' + canonical + '">' +
        ''.join(cards) + ('<a href="?page=' + str(last) + '">last</a>' if current < last else ''))


def detail(row):
    return page().replace(SPEC['url'], listing_url(row)).replace(ROW['address'], row['address']).replace('193473', registry.listing_id(row))


class Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.rows = make_rows()
        self.now = datetime(2026, 10, 10, 16, tzinfo=timezone.utc)
        self.put_inventory()
        for name in ['scanner_status.json', 'scan_config.json', 'scan_log.json', 'deals.json']:
            registry.atomic_json(self.repo / name, {'protected': name})
        registry.atomic_json(self.repo / 'COMPS_REPORTS/automatic_listing_sources/state.json', {'enabled': False, 'entries': {'other': 'untouched'}})
        registry.atomic_json(self.repo / source.STATUS, {'request_id': 'manual-request-untouched'})
        self.protected = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}

    def put_inventory(self):
        registry.atomic_json(self.repo / 'properties.json', self.rows)

    def reader(self, pages=None, blocked='', max_requests=7, wrong=(), robots='User-agent: *\nDisallow: /wp-admin/\n'):
        rows, clock, calls = copy.deepcopy(self.rows), [0], []
        pages = pages or {1: directory(rows)}
        class Opener:
            def open(self, request, timeout):
                url = request.full_url
                calls.append((url, clock[0]))
                if url.endswith('/robots.txt'):
                    body = robots
                elif '/listings/my-active-listings' in url:
                    if blocked == 'directory':
                        raise HTTPError(url, 403, 'blocked', {'Retry-After': '172800'}, None)
                    number = int(parse_qs(urlparse(url).query).get('page', ['1'])[0])
                    body = pages[number]
                else:
                    if blocked == 'detail':
                        raise HTTPError(url, 403, 'blocked', {}, None)
                    row = next(r for r in rows if listing_url(r) == url)
                    body = detail(row)
                    if row['id'] in wrong:
                        body = body.replace('MLS #:</strong>' + registry.listing_id(row), 'MLS #:</strong>999999')
                return io.BytesIO(body.encode())
        reader = source.PublicReader(opener=Opener(), max_requests=max_requests,
            clock=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))
        reader.calls = calls
        return reader

    def test_check_reads_inventory_without_network_or_writes(self):
        reader = self.reader()
        report = worker.run(self.repo, 'check', reader, self.now)
        self.assertEqual(report['eligible_properties'], 7)
        self.assertEqual(report['pending'], 7)
        self.assertEqual(reader.requests, 0)
        self.assertFalse(report['should_save'])
        self.assertEqual(self.protected, {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})

    def test_pilot_completes_only_three_and_preserves_inventory_and_other_queues(self):
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader, self.now)
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(report['attempted_properties'], 3)
        self.assertEqual(report['pending'], 4)
        self.assertEqual(report['new_source_network_requests'], 5)
        self.assertFalse(report['automatic_enabled'])
        self.assertIn('קבוצת הניסוי הושלמה', worker.summary(report))
        self.assertTrue(all(b[1] - a[1] >= 10 for a, b in zip(reader.calls, reader.calls[1:])))
        for path, original in self.protected.items():
            self.assertEqual((self.repo / path).read_bytes(), original)
        for result in report['results']:
            row = next(r for r in self.rows if r['id'] == result['property_id'])
            saved = registry.read_json(self.repo / source.ROOT / (source.cache_key(row['id']) + '.json'))
            self.assertTrue(source.bound(row, saved))
            self.assertEqual(len(saved['photos']), 1)

    def test_next_run_uses_saved_links_then_fresh_cache_uses_no_http(self):
        worker.run(self.repo, 'pilot', self.reader(), self.now)
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader, self.now)
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(report['cached_properties'], 3)
        self.assertEqual(report['directory_pages'], 0)
        self.assertEqual(reader.requests, 4)
        self.assertEqual(worker.run(self.repo, 'pilot', self.reader(), self.now)['new_snapshots'], 1)
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader, self.now)
        self.assertEqual(report['status'], 'cache_current')
        self.assertEqual(report['cached_properties'], 7)
        self.assertEqual(reader.requests, 0)

    def test_discovery_resumes_after_three_pages(self):
        other = [{**self.rows[0], 'id': 'PA-MLS-8888', 'docket_id': 'MLS-8888'}]
        pages = {i: directory(other if i < 4 else self.rows, i, 4) for i in range(1, 5)}
        report = worker.run(self.repo, 'pilot', self.reader(pages), self.now)
        self.assertEqual(report['directory_pages'], 3)
        self.assertEqual(report['new_snapshots'], 0)
        self.assertEqual(report['next_discovery_page'], 4)
        report = worker.run(self.repo, 'pilot', self.reader(pages), self.now)
        self.assertEqual(report['directory_pages'], 1)
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(report['next_discovery_page'], 1)

    def test_three_pages_and_three_details_include_robots_in_seven_http_cap(self):
        pages = {i: directory(self.rows[i-1:i], i, 3) for i in range(1, 4)}
        reader = self.reader(pages)
        report = worker.run(self.repo, 'pilot', reader, self.now)
        self.assertEqual(report['directory_pages'], 3)
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(reader.requests, 7)

    def test_budget_exhaustion_keeps_discovered_links_for_next_run(self):
        pages = {i: directory(self.rows[:3], i, 3) for i in range(1, 4)}
        report = worker.run(self.repo, 'pilot', self.reader(pages, max_requests=2), self.now)
        self.assertEqual(report['status'], 'source_request_budget_exhausted')
        self.assertEqual(report['next_discovery_page'], 2)
        self.assertEqual(report['new_source_network_requests'], 2)
        state = registry.read_json(self.repo / worker.STATE)
        self.assertTrue(state['entries'][self.rows[0]['id']]['source_url'])
        self.assertNotIn('discovery_retry_after_at', state)
        self.assertFalse((self.repo / source.SOURCE_STATE).exists())

    def test_deadline_prevents_any_http(self):
        reader = self.reader()
        reader.deadline = 20
        report = worker.run(self.repo, 'pilot', reader, self.now)
        self.assertEqual(report['status'], 'batch_time_limit')
        self.assertEqual(reader.requests, 0)
        self.assertFalse((self.repo / source.SOURCE_STATE).exists())

    def test_directory_block_stops_and_persists_source_specific_cooldown(self):
        report = worker.run(self.repo, 'pilot', self.reader(blocked='directory'), self.now)
        self.assertEqual(report['status'], 'source_blocked')
        self.assertEqual(report['new_source_network_requests'], 2)
        self.assertEqual(report['attempted_properties'], 0)
        reader = self.reader()
        self.assertEqual(worker.run(self.repo, 'pilot', reader, self.now)['status'], 'source_cooldown')
        self.assertEqual(reader.requests, 0)
        self.assertEqual(registry.read_json(self.repo / source.SOURCE_STATE)['provider'], 'eriemoves')

    def test_detail_block_stops_after_first_property(self):
        report = worker.run(self.repo, 'pilot', self.reader(blocked='detail'), self.now)
        self.assertEqual(report['status'], 'source_blocked')
        self.assertEqual(report['attempted_properties'], 1)
        self.assertEqual(report['new_snapshots'], 0)

    def test_wrong_detail_mls_rejected_without_stopping_other_rows(self):
        report = worker.run(self.repo, 'pilot', self.reader(wrong=(self.rows[-1]['id'],)), self.now)
        self.assertEqual([r['status'] for r in report['results']], ['source_identity_mismatch', 'published', 'published'])
        self.assertEqual(report['new_snapshots'], 2)
        failed = registry.read_json(self.repo / source.ROOT / (source.cache_key(self.rows[-1]['id']) + '.json'))
        self.assertFalse(failed['facts'])
        self.assertFalse((self.repo / source.SOURCE_STATE).exists())

    def test_pending_cards_wrong_addresses_and_untrusted_links_rejected(self):
        rows = {r['id']: r for r in self.rows}
        cases = [directory(self.rows, status='Pending'),
            directory(self.rows).replace(' First St Erie, PA', ' Wrong St Erie, PA'),
            directory(self.rows).replace('https://eriemoves.com/listing/', 'https://evil.example/listing/'),
            directory(self.rows).replace('-16504/', '-16505/')]
        for html in cases:
            with self.subTest(html=html[:80]):
                self.assertFalse(worker.parse_directory(html, rows, 1)[0])

    def test_two_links_for_same_property_across_pages_are_ambiguous(self):
        first = directory(self.rows[:1], 1, 2)
        second = directory(self.rows[:1], 2, 2).replace('/905001', '/999999')
        report = worker.run(self.repo, 'pilot', self.reader({1: first, 2: second}), self.now)
        self.assertEqual(report['ambiguous_links'], 1)
        self.assertEqual(report['attempted_properties'], 0)
        entry = registry.read_json(self.repo / worker.STATE)['entries'][self.rows[0]['id']]
        self.assertNotIn('source_url', entry)

    def test_duplicate_ids_mls_aliases_and_inactive_rows_excluded(self):
        self.rows.extend([{**self.rows[0], 'address': '99 Other St'},
            {**self.rows[1], 'id': 'OTHER-ID'},
            {**self.rows[2], 'id': 'PA-MLS-9001', 'docket_id': 'MLS-9001', 'market_status': 'sold'}])
        self.put_inventory()
        registry.atomic_json(self.repo / registry.ROOT / 'aliases.json', {'ambiguous': {self.rows[3]['id']: ['review']}})
        report = worker.run(self.repo, 'check', now=self.now)
        self.assertEqual(report['eligible_properties'], 4)
        self.assertEqual(report['excluded']['ambiguous_identity'], 3)
        self.assertEqual(report['excluded']['ambiguous_mls'], 2)
        self.assertEqual(report['excluded']['unconfirmed_or_inactive_listing'], 1)

    def test_changed_inventory_binding_cannot_reuse_prior_cache_or_link(self):
        worker.run(self.repo, 'pilot', self.reader(), self.now)
        self.rows[-1]['address'] = '99 Other St'
        self.put_inventory()
        report = worker.run(self.repo, 'check', now=self.now)
        self.assertEqual(report['cached_properties'], 2)
        worker.run(self.repo, 'pilot', self.reader(), self.now)
        entry = registry.read_json(self.repo / worker.STATE)['entries'][self.rows[-1]['id']]
        self.assertNotIn('source_url', entry)

    def test_fresh_cache_wins_over_host_cooldown(self):
        for row in self.rows:
            snapshot = source.parse_detail(detail(row), row, listing_url(row), self.now.isoformat())
            registry.atomic_json(self.repo / source.ROOT / (source.cache_key(row['id']) + '.json'), snapshot)
        registry.atomic_json(self.repo / source.SOURCE_STATE, {'retry_after_at': (self.now + timedelta(days=2)).isoformat()})
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader, self.now)
        self.assertEqual(report['cached_properties'], 7)
        self.assertEqual(reader.requests, 0)

    def test_robots_disallow_stops_before_directory(self):
        report = worker.run(self.repo, 'pilot', self.reader(robots='User-agent: *\nDisallow: /\n'), self.now)
        self.assertEqual(report['status'], 'robots_disallowed')
        self.assertEqual(report['new_source_network_requests'], 1)
        self.assertEqual(report['directory_pages'], 0)

    def test_malformed_directory_is_reported_without_snapshot(self):
        report = worker.run(self.repo, 'pilot', self.reader({1: '<html>Unavailable</html>'}), self.now)
        self.assertEqual(report['status'], 'source_discovery_page_unavailable')
        self.assertEqual(report['new_snapshots'], 0)
        self.assertFalse((self.repo / source.ROOT).exists())

    def test_paginated_canonical_must_match_requested_page(self):
        rows = {r['id']: r for r in self.rows}
        self.assertTrue(worker.parse_directory(directory(self.rows, 2, 3), rows, 2)[0])
        with self.assertRaises(source.StopSource):
            worker.parse_directory(directory(self.rows, 2, 3), rows, 3)


if __name__ == '__main__':
    unittest.main()
