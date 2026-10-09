import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

import automatic_listing_sources as worker
import property_sources as registry
import secondary_listing_source as source
from test_secondary_listing_source import ROW, URL, page


def make_rows():
    return [{**copy.deepcopy(ROW), 'id': 'PA-MLS-' + str(1001 + i),
             'docket_id': 'MLS-' + str(1001 + i), 'address': str(12 + i * 2) + ' First St',
             'url': 'https://www.redfin.com/PA/Pittsburgh/x/home/' + str(1001 + i),
             'last_scan_id': 'S1', 'market_status': 'active'} for i in range(5)]


def listing_url(row):
    return URL if registry.listing_id(row) == '1001' else source.ORIGIN + '/idx/listing/' + registry.listing_id(row) + '_spid/'


def cards(rows):
    return ''.join('<div class="si-listing" data-url="' + listing_url(row) + '">' +
                   '<button data-mls="' + registry.listing_id(row) + '"></button>' +
                   '<div class="si-listing__title-main">' + row['address'] + '</div>' +
                   '<div class="si-listing__title-description">Pittsburgh, PA 15216</div></div>' for row in rows)


def directory(rows):
    return ''.join('<a href="' + listing_url(row) + '">' + row['address'] +
                   ' Pittsburgh, PA 15216 MLS # ' + registry.listing_id(row) + '</a>' for row in rows)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.rows = make_rows()
        self.put_inventory()
        self.scan = {'last_scan': {'scan_id': 'S1'}, 'last_event': {'scan_id': 'S1', 'status': 'partial',
                     'active_sectors': ['mls'], 'github_sha': 'abc'}}
        registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
        registry.atomic_json(self.repo / source.STATUS, {'request_id': 'manual-protected'})
        self.inventory_before = (self.repo / 'properties.json').read_bytes()
        self.manual_before = (self.repo / source.STATUS).read_bytes()

    def put_inventory(self):
        registry.atomic_json(self.repo / 'properties.json', self.rows)

    def reader(self, blocked=False, max_requests=8, indexed=None):
        rows, now, calls = self.rows, [0], []
        indexed = rows if indexed is None else indexed
        class Opener:
            def open(self, req, timeout):
                url = req.full_url
                calls.append((url, now[0]))
                if url.endswith('/robots.txt'):
                    return io.BytesIO(b'User-agent: *\nCrawl-delay: 5\nDisallow: /sist/\n')
                if '/idx/site-map/' in url:
                    return io.BytesIO(directory(indexed).encode())
                if blocked:
                    raise HTTPError(url, 403, 'blocked', {'Retry-After': '172800'}, None)
                row = next(r for r in rows if listing_url(r) == url)
                html = page(row).replace(URL, listing_url(row)).replace('1001', registry.listing_id(row))
                return io.BytesIO(html.encode())
        def sleep(delay):
            now[0] += delay
        reader = source.PublicReader(clock=lambda: now[0], sleep=sleep, opener=Opener(), max_requests=max_requests)
        reader.calls = calls
        return reader

    def test_check_is_read_only_and_network_free(self):
        before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        reader = self.reader()
        report = worker.run(self.repo, 'check', reader=reader)
        self.assertEqual(report['status'], 'check_ok')
        self.assertEqual(report['pending'], 5)
        self.assertEqual(reader.requests, 0)
        self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})

    def test_pilot_limits_three_properties_and_shares_index(self):
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader=reader)
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(report['attempted_properties'], 3)
        self.assertEqual(report['pending'], 2)
        self.assertEqual(report['new_source_network_requests'], 5)
        self.assertFalse(report['enabled'])
        self.assertTrue(all(b[1] - a[1] >= 10 for a, b in zip(reader.calls, reader.calls[1:])))
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.inventory_before)
        self.assertEqual((self.repo / source.STATUS).read_bytes(), self.manual_before)

    def test_next_run_resumes_then_cache_uses_zero_network(self):
        worker.run(self.repo, 'pilot', reader=self.reader())
        report = worker.run(self.repo, 'pilot', reader=self.reader())
        self.assertEqual(report['cached_properties'], 3)
        self.assertEqual(report['new_snapshots'], 2)
        self.assertEqual(report['pending'], 0)
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader=reader)
        self.assertEqual(report['cached_properties'], 5)
        self.assertEqual(reader.requests, 0)

    def test_block_stops_other_properties_and_future_run(self):
        report = worker.run(self.repo, 'pilot', reader=self.reader(blocked=True))
        self.assertEqual(report['status'], 'source_blocked')
        self.assertEqual(report['attempted_properties'], 1)
        self.assertEqual(report['new_snapshots'], 0)
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader=reader)
        self.assertEqual(report['status'], 'source_cooldown')
        self.assertEqual(reader.requests, 0)
        cooldown = registry.read_json(self.repo / source.SOURCE_STATE)
        self.assertEqual(cooldown['http_status'], 403)

    def test_request_budget_preserves_queue_without_failure_cooldown(self):
        report = worker.run(self.repo, 'pilot', reader=self.reader(max_requests=1))
        self.assertEqual(report['status'], 'source_request_budget_exhausted')
        self.assertEqual(report['new_source_network_requests'], 1)
        self.assertEqual(report['pending'], 5)
        self.assertFalse((self.repo / source.SOURCE_STATE).exists())
        self.assertEqual(worker.run(self.repo, 'pilot', reader=self.reader())['new_snapshots'], 3)

    def test_automatic_requires_manual_activation(self):
        reader = self.reader()
        self.assertEqual(worker.run(self.repo, 'automatic', 'abc', reader)['status'], 'automatic_disabled')
        self.assertEqual(reader.requests, 0)
        worker.run(self.repo, 'enable', reader=reader)
        self.assertEqual(reader.requests, 0)
        self.assertEqual(worker.run(self.repo, 'automatic', 'abc', reader)['new_snapshots'], 3)

    def test_skipped_scans_do_not_advance_queue(self):
        worker.run(self.repo, 'enable')
        before = (self.repo / worker.STATE).read_bytes()
        self.scan['last_event']['status'] = 'skipped'
        registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
        reader = self.reader()
        self.assertEqual(worker.run(self.repo, 'automatic', 'abc', reader)['status'], 'no_new_mls_scan')
        self.assertEqual(reader.requests, 0)
        self.assertEqual((self.repo / worker.STATE).read_bytes(), before)

    def test_changed_upstream_scan_does_not_use_other_scan(self):
        worker.run(self.repo, 'enable')
        reader = self.reader()
        self.assertEqual(worker.run(self.repo, 'automatic', 'old', reader)['status'], 'upstream_scan_no_longer_current')
        self.assertEqual(reader.requests, 0)

    def test_ambiguous_ids_are_excluded(self):
        self.rows.append({**self.rows[0], 'address': '98 Other St'})
        self.put_inventory()
        report = worker.run(self.repo, 'check')
        self.assertEqual(report['excluded']['ambiguous_identity'], 2)
        self.assertEqual(report['eligible_properties'], 4)

    def test_relisted_home_does_not_reuse_old_snapshot_or_link(self):
        worker.run(self.repo, 'pilot', reader=self.reader())
        self.rows[0]['docket_id'] = 'MLS-9911'
        self.put_inventory()
        report = worker.run(self.repo, 'check')
        self.assertEqual(report['cached_properties'], 2)
        # check does not overwrite the stored queue; a fetch rebinds it.
        worker.run(self.repo, 'enable')
        entry = registry.read_json(self.repo / worker.STATE)['entries'][self.rows[0]['id']]
        self.assertNotIn('source_url', entry)

    def test_reader_fetches_robots_and_each_url_once(self):
        reader = self.reader()
        reader.initialize()
        reader.initialize()
        reader.get(URL)
        reader.get(URL)
        self.assertEqual(reader.requests, 2)

    def test_discovery_miss_remains_queued_with_retry(self):
        self.rows = self.rows[:1]
        self.put_inventory()
        unrelated = [{**self.rows[0], 'docket_id': 'MLS-9999'}]
        report = worker.run(self.repo, 'pilot', reader=self.reader(indexed=unrelated))
        self.assertEqual(report['status'], 'no_matching_public_links')
        self.assertEqual(report['pending'], 1)
        reader = self.reader()
        self.assertEqual(worker.run(self.repo, 'pilot', reader=reader)['status'], 'waiting_for_retry')
        self.assertEqual(reader.requests, 0)

    def test_deadline_stops_before_network(self):
        reader = self.reader()
        reader.deadline = 20
        with self.assertRaises(source.StopSource) as error:
            reader.initialize()
        self.assertEqual(error.exception.status, 'batch_time_limit')
        self.assertEqual(reader.requests, 0)

    def test_queue_never_restores_removed_inventory(self):
        worker.run(self.repo, 'enable')
        self.rows = self.rows[:1]
        self.put_inventory()
        worker.run(self.repo, 'disable')
        self.assertEqual(len(registry.read_json(self.repo / worker.STATE)['entries']), 1)
        self.assertEqual(registry.read_json(self.repo / 'properties.json'), self.rows)

    def test_directory_cursor_advances_instead_of_repeating_first_pages(self):
        visits = []
        unrelated = [{**self.rows[0], 'docket_id': 'MLS-9999'}]
        class Reader:
            requests = 0
            def get(self, url):
                visits.append(url)
                self.requests += 1
                return directory(unrelated) + '<a href="/idx/site-map/?offset=12">12</a>'
        found, next_page, finished = source.discover_directory(Reader(), self.rows, 4)
        self.assertEqual(found, {})
        self.assertEqual(next_page, 7)
        self.assertFalse(finished)
        self.assertEqual(len(visits), 3)
        self.assertIn('offset=4', visits[0])
        self.assertIn('offset=6', visits[-1])

    def test_removed_directory_page_resets_cursor_without_following_redirect(self):
        class Reader:
            def get(self, url):
                raise source.StopSource('source_redirect_stopped', 301)
        with self.assertRaises(source.StopSource) as error:
            source.discover_directory(Reader(), self.rows, 134)
        self.assertTrue(error.exception.reset_directory)


if __name__ == '__main__':
    unittest.main()
