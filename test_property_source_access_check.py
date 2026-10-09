import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

import property_source_access_check as check
import property_sources as registry
from test_secondary_listing_source import ROW as ORIGINAL_ROW, URL as ORIGINAL_URL, page

ROW = {**copy.deepcopy(ORIGINAL_ROW), 'id': check.PROPERTY_ID, 'docket_id': 'MLS-1778408',
       'address': '2346 Fremont Pl', 'city': 'Pittsburgh', 'zip': '15216', 'county': 'Allegheny',
       'url': 'https://www.redfin.com/PA/Pittsburgh/2346-Fremont-Pl-15216/home/74695032'}


def fixture(provider):
    spec = next(s for s in check.SOURCES if s['id'] == provider)
    if provider == 'tarasa':
        return page(ROW).replace(ORIGINAL_URL, spec['url']).replace('1001', '1778408')
    if provider == 'coldwellbankerhomes':
        values = [{'@type': ['Product', 'RealEstateListing'], 'url': spec['url'],
                   'name': '2346 Fremont Pl, Beechview, PA 15216',
                   'image': ['https://m.cbhomes.com/p/716/1778408/AB12/full.webp']}]
        return ('<link rel="canonical" href="' + spec['url'] + '">'
                '<h1>2346 Fremont Pl Beechview, PA 15216</h1>'
                '<meta property="og:title" content="2346 Fremont Pl, Beechview, PA 15216 - MLS 1778408">'
                '<script type="application/ld+json">' + json.dumps(values) + '</script>')
    values = [{'@type': 'Offer', 'url': spec['url'],
               'image': 'https://www.propertypanorama.com/photos/wpn/1778/408/full/' + 'a' * 64 + '.jpg'},
              {'@type': 'Accommodation', 'address': {'streetAddress': ROW['address'],
                'addressLocality': ROW['city'], 'addressRegion': 'PA', 'postalCode': ROW['zip']}}]
    return ('<link rel="canonical" href="' + spec['url'] + '">'
            '<meta name="description" content="MLS #: 1778408. Published property description">'
            '<script type="application/ld+json">' + json.dumps({'@graph': values}) + '</script>')


class Response(io.BytesIO):
    status = 200


class AccessCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        registry.atomic_json(self.repo / 'properties.json', [ROW])
        for path in ('scanner_status.json', 'sheriff_listings.json', 'rentcast_budget.json',
                     'COMPS_REPORTS/automatic_listing_sources/state.json',
                     'COMPS_REPORTS/additional_sources/clearchoice_state.json',
                     'COMPS_REPORTS/additional_source_status.json', 'deals.html'):
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('protected existing bytes', encoding='utf-8')

    def files(self):
        return {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}

    def reader(self, replacements=None):
        now, calls, overrides = [0], [], replacements or {}
        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url, now[0], dict(request.header_items())))
                spec = next(s for s in check.SOURCES if request.full_url.startswith(s['origin'] + '/'))
                stage = 'robots' if request.full_url.endswith('/robots.txt') else 'listing'
                value = overrides.get((spec['id'], stage))
                if isinstance(value, Exception):
                    raise value
                body = value if isinstance(value, str) else ('User-agent: *\nCrawl-delay: 5\nDisallow: /private/\n'
                       if stage == 'robots' else fixture(spec['id']))
                response = Response(body.encode())
                if isinstance(value, int):
                    response.status = value
                return response
        def sleep(delay):
            now[0] += delay
        reader = check.Reader(Opener(), clock=lambda: now[0], sleep=sleep)
        reader.calls = calls
        return reader

    def test_check_has_zero_network_and_no_writes(self):
        reader, before = self.reader(), self.files()
        report = check.run(self.repo, 'check', reader)
        self.assertEqual(report['status'], 'check_ok')
        self.assertEqual(reader.requests, 0)
        self.assertEqual(self.files(), before)

    def test_probe_is_bounded_read_only_and_uses_no_credentials(self):
        reader, before = self.reader(), self.files()
        report = check.run(self.repo, 'probe', reader)
        self.assertEqual(report['status'], 'alternative_technical_source_available')
        self.assertEqual(report['new_source_network_requests'], 6)
        self.assertEqual(report['new_rentcast_calls'], 0)
        self.assertEqual(report['new_snapshots'], 0)
        self.assertEqual(self.files(), before)
        self.assertFalse(any('clearchoice' in url for url, _, _ in reader.calls))
        for spec in check.SOURCES:
            calls = [c for c in reader.calls if c[0].startswith(spec['origin'] + '/')]
            self.assertGreaterEqual(calls[1][1] - calls[0][1], 10)
        for _, _, headers in reader.calls:
            self.assertFalse({'authorization', 'cookie', 'x-api-key'} & {k.lower() for k in headers})

    def test_blocked_robots_stop_that_source_and_allow_independent_sources(self):
        url = check.SOURCES[0]['origin'] + '/robots.txt'
        reader = self.reader({('tarasa', 'robots'): HTTPError(url, 403, 'blocked', {}, None)})
        report = check.run(self.repo, 'probe', reader)
        self.assertEqual(report['results'][0]['status'], 'source_blocked')
        self.assertEqual(report['results'][0]['stage'], 'robots')
        self.assertEqual(report['results'][0]['source_network_requests'], 1)
        self.assertEqual(report['new_source_network_requests'], 5)
        self.assertEqual(report['status'], 'alternative_tour_only')

    def test_rate_limit_records_retry_after_without_retry(self):
        error = HTTPError(check.SOURCES[0]['origin'] + '/robots.txt', 429, 'limited', {'Retry-After': '86400'}, None)
        report = check.run(self.repo, 'probe', self.reader({('tarasa', 'robots'): error}))
        self.assertEqual(report['results'][0]['retry_after'], '86400')
        self.assertEqual(report['results'][0]['source_network_requests'], 1)

    def test_robots_disallow_prevents_listing_request(self):
        reader = self.reader({('tarasa', 'robots'): 'User-agent: *\nDisallow: /\n'})
        report = check.run(self.repo, 'probe', reader)
        self.assertEqual(report['results'][0]['status'], 'robots_disallowed')
        self.assertEqual(report['results'][0]['source_network_requests'], 1)

    def test_long_crawl_delay_is_not_ignored(self):
        report = check.run(self.repo, 'probe', self.reader({('tarasa', 'robots'): 'User-agent: *\nCrawl-delay: 85\n'}))
        self.assertEqual(report['results'][0]['status'], 'crawl_delay_exceeds_check_limit')
        self.assertEqual(report['results'][0]['source_network_requests'], 1)

    def test_nonstandard_challenge_status_is_not_marked_available(self):
        report = check.run(self.repo, 'probe', self.reader({('tarasa', 'listing'): 218}))
        self.assertEqual(report['results'][0]['status'], 'unexpected_http_status')
        self.assertEqual(report['results'][0]['http_status'], 218)

    def test_html_challenge_in_robots_does_not_authorize_fetch(self):
        report = check.run(self.repo, 'probe', self.reader({('tarasa', 'robots'): '<html><script>challenge()</script></html>'}))
        self.assertEqual(report['results'][0]['status'], 'robots_response_not_rules')
        self.assertEqual(report['results'][0]['source_network_requests'], 1)

    def test_redirects_are_stopped(self):
        with self.assertRaises(check.StopCheck):
            check.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.test/challenge')

    def test_changed_or_duplicate_inventory_subject_causes_zero_requests(self):
        for rows in ([{**ROW, 'zip': '15224'}], [ROW, ROW], []):
            with self.subTest(rows=rows):
                registry.atomic_json(self.repo / 'properties.json', rows)
                reader = self.reader()
                report = check.run(self.repo, 'probe', reader)
                self.assertEqual(reader.requests, 0)
                self.assertNotEqual(report['status'], 'alternative_technical_source_available')

    def test_ambiguous_alias_is_not_joined(self):
        registry.atomic_json(self.repo / registry.ROOT / 'aliases.json', {'ambiguous': {check.PROPERTY_ID: {}}})
        reader = self.reader()
        self.assertEqual(check.run(self.repo, 'probe', reader)['status'], 'ambiguous_test_property')
        self.assertEqual(reader.requests, 0)

    def test_wrong_primary_address_rejected_even_with_same_mls(self):
        html = fixture('tarasa').replace('2346 Fremont Pl', '2348 Fremont Pl')
        report = check.run(self.repo, 'probe', self.reader({('tarasa', 'listing'): html}))
        self.assertEqual(report['results'][0]['status'], 'subject_identity_mismatch')

    def test_wrong_primary_mls_rejected_even_with_matching_nearby_number(self):
        html = fixture('tarasa').replace('data-mls="1778408"', 'data-mls="9999999"')
        report = check.run(self.repo, 'probe', self.reader({('tarasa', 'listing'): html}))
        self.assertEqual(report['results'][0]['status'], 'subject_identity_mismatch')

    def test_wrong_canonical_is_not_accepted(self):
        html = fixture('tarasa').replace('rel="canonical"', 'rel="alternate"')
        report = check.run(self.repo, 'probe', self.reader({('tarasa', 'listing'): html}))
        self.assertEqual(report['results'][0]['status'], 'canonical_or_listing_missing')

    def test_city_variant_is_exposed_and_never_merged(self):
        report = check.run(self.repo, 'probe', self.reader())
        result = report['results'][1]
        self.assertEqual(result['published_city'], 'Beechview')
        self.assertEqual(result['status'], 'accessible_city_variant_needs_review')
        self.assertFalse(result['identity_verified'])
        self.assertFalse(result['inventory_merge_allowed'])

    def test_rounded_acreage_not_converted_and_nearby_facts_not_counted(self):
        result = check.inspect_page(fixture('tarasa'), check.SOURCES[0], ROW)
        self.assertIn('lot_area_acres', result['technical_names'])
        self.assertNotIn('lot_size', result['technical_names'])
        self.assertNotIn('occupancy', result['technical_names'])
        self.assertEqual(result['photo_links'], 1)

    def test_duplicate_contradictory_subject_labels_rejected(self):
        html = fixture('tarasa').replace('</section>', '<div><strong>Roof</strong><span>Metal</span></div></section>')
        with self.assertRaisesRegex(check.StopCheck, 'conflicting_subject_labels'):
            check.inspect_page(html, check.SOURCES[0], ROW)

    def test_other_hosts_credentials_and_budget_rejected_before_requests(self):
        reader = self.reader()
        for url in ('https://www.tarasa.com.evil.test/listing', 'https://user:pass@www.tarasa.com/listing',
                    'http://www.tarasa.com/listing', check.SOURCES[0]['url'] + '?token=secret'):
            with self.subTest(url=url), self.assertRaises(check.StopCheck):
                reader.get(check.SOURCES[0], url)
        self.assertEqual(reader.requests, 0)
        reader.requests = check.MAX_REQUESTS
        with self.assertRaisesRegex(check.StopCheck, 'request_budget_exhausted'):
            reader.get(check.SOURCES[0], check.SOURCES[0]['url'])

    def test_deadline_stops_before_any_network(self):
        reader = self.reader()
        reader.deadline = 20
        with self.assertRaisesRegex(check.StopCheck, 'time_limit'):
            reader.get(check.SOURCES[0], check.SOURCES[0]['url'])
        self.assertEqual(reader.requests, 0)


if __name__ == '__main__':
    unittest.main()
