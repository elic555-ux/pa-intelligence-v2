import copy
import json
from pathlib import Path
import tempfile
import unittest

import erie_source_access_check as check


def row(spec):
    return {'id': spec['property_id'], 'address': spec['address'], 'city': spec['city'],
            'zip': spec['zip'], 'county': 'Erie', 'state': 'PA',
            'docket_id': spec['property_id'].removeprefix('PA-'), 'source_type': 'MLS'}


def page(spec):
    r = row(spec)
    mls = check.registry.listing_id(r)
    prefix = '<link rel="canonical" href="' + spec['url'] + '"><h1>' + r['address'] + ' ' + r['city'] + ', PA ' + r['zip'] + '</h1>'
    if spec['provider'] == 'howardhanna':
        return ('<title>Property MLS #' + mls + '</title>' + prefix +
                '<div class="prop-section"><h2>Property Details</h2>'
                '<dl><div><dt>MLS#</dt><dd>' + mls + '</dd></div></dl>'
                '<p>Built 1951; stories 2; Central air; Forced air heat; Asphalt roof; Full basement; Public water; Public sewer</p></div>'
                '<img alt="' + r['address'] + ' property photo" src="https://photos.prod.cirrussystem.net/example.jpg">'
                '<img alt="Agent portrait" src="https://photos.prod.cirrussystem.net/agent.jpg">')
    house = {'@type': 'House', '@id': spec['url'] + '/#listingdata',
             'address': {'streetAddress': r['address'], 'addressLocality': r['city'],
                         'addressRegion': 'PA', 'postalCode': r['zip']},
             'image': [{'url': 'https://i9.moxi.onl/property.jpg'},
                       {'url': 'https://i9.moxi.onl/property.jpg'},
                       {'url': 'https://untrusted.example/image.jpg'}]}
    listing = {'@type': 'RealEstateListing', 'url': spec['url'], 'about': {'@id': house['@id']}}
    return (prefix + '<script type="application/ld+json">' + json.dumps({'@graph': [listing, house]}) + '</script>'
            '<div class="listing-spec-table"><div class="spec-cell"><strong>MLS #:</strong><br>' + mls + '</div>'
            '<div class="spec-cell"><strong>County</strong><br>Erie County</div></div>'
            '<div id="ld_heat_cool"><ul><li>Gas</li><li>Forced Air</li></ul></div>')


class FakeReader:
    def __init__(self, blocked=None, rules='User-agent: *\nAllow: /', wrong=None):
        self.requests, self.calls = 0, []
        self.blocked, self.rules, self.wrong = blocked, rules, wrong

    def get(self, spec, url, interval=10):
        self.requests += 1
        self.calls.append((spec['origin'], url, interval))
        if spec['origin'] == self.blocked:
            raise check.Stop('source_blocked', 403)
        if url.endswith('/robots.txt'):
            return self.rules
        content = page(spec)
        return content.replace(spec['address'], '99 Wrong St') if spec['property_id'] == self.wrong else content


class ErieChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.rows = [row(s) for s in check.SAMPLES]
        self.write_rows()
        for name in ['COMPS_REPORTS/automatic_listing_sources/state.json',
                     'COMPS_REPORTS/additional_sources/clearchoice/keep.json',
                     'COMPS_REPORTS/additional_sources/tarasa/keep.json',
                     'COMPS_REPORTS/rentcast_usage.json', 'deals.html', 'scanner_status.json']:
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('protected original bytes')

    def write_rows(self):
        (self.repo / 'properties.json').write_text(json.dumps(self.rows))

    def bytes(self):
        return {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}

    def test_check_uses_no_requests_and_writes_nothing(self):
        before, reader = self.bytes(), FakeReader()
        result = check.run(self.repo, 'check', reader)
        self.assertEqual(result['status'], 'check_ok')
        self.assertEqual(reader.requests, 0)
        self.assertEqual(self.bytes(), before)

    def test_probe_verifies_three_samples_with_five_requests(self):
        before, reader = self.bytes(), FakeReader()
        result = check.run(self.repo, 'probe', reader)
        self.assertEqual(result['new_source_network_requests'], 5)
        self.assertEqual([r['status'] for r in result['results']], ['verified_technical_listing'] * 3)
        self.assertEqual(result['new_rentcast_calls'], 0)
        self.assertEqual(result['new_snapshots'], 0)
        self.assertEqual(self.bytes(), before)
        self.assertTrue(all(c[2] >= 10 for c in reader.calls))

    def test_blocked_host_stops_but_second_host_is_checked(self):
        reader = FakeReader(blocked=check.SAMPLES[0]['origin'])
        result = check.run(self.repo, 'probe', reader)
        self.assertEqual(result['new_source_network_requests'], 3)
        self.assertEqual(result['results'][1]['source_network_requests'], 0)
        self.assertEqual(result['results'][2]['status'], 'verified_technical_listing')

    def test_robots_denial_prevents_listing_requests(self):
        reader = FakeReader(rules='User-agent: *\nDisallow: /')
        result = check.run(self.repo, 'probe', reader)
        self.assertEqual(result['new_source_network_requests'], 2)
        self.assertTrue(all(c[1].endswith('/robots.txt') for c in reader.calls))

    def test_robots_html_is_not_accepted_as_permission(self):
        reader = FakeReader(rules='<html>challenge</html>')
        result = check.run(self.repo, 'probe', reader)
        self.assertEqual(result['new_source_network_requests'], 2)
        self.assertTrue(all(not r['identity_verified'] for r in result['results']))

    def test_duplicate_inventory_id_prevents_all_requests(self):
        self.rows.append(copy.deepcopy(self.rows[0]))
        self.write_rows()
        reader = FakeReader()
        self.assertIn('ambiguous_or_missing_sample', check.run(self.repo, 'probe', reader)['status'])
        self.assertEqual(reader.requests, 0)

    def test_changed_inventory_address_prevents_requests(self):
        self.rows[0]['address'] = '99 Different Dr'
        self.write_rows()
        reader = FakeReader()
        self.assertIn('sample_inventory_identity_changed', check.run(self.repo, 'probe', reader)['status'])
        self.assertEqual(reader.requests, 0)

    def test_mls_changed_in_inventory_prevents_requests(self):
        self.rows[0]['listing_id'] = '123456'
        self.write_rows()
        reader = FakeReader()
        self.assertIn('identity_changed', check.run(self.repo, 'probe', reader)['status'])
        self.assertEqual(reader.requests, 0)

    def test_wrong_headline_is_rejected_for_both_providers(self):
        for spec in (check.SAMPLES[0], check.SAMPLES[2]):
            with self.assertRaises(check.Stop):
                check.inspect_page(page(spec).replace(spec['address'], '99 Wrong St'), spec, row(spec))

    def test_wrong_canonical_is_rejected(self):
        spec = check.SAMPLES[0]
        with self.assertRaises(check.Stop):
            check.inspect_page(page(spec).replace(spec['url'], 'https://evil.example/'), spec, row(spec))

    def test_wrong_primary_mls_is_rejected(self):
        spec = check.SAMPLES[0]
        with self.assertRaises(check.Stop):
            check.inspect_page(page(spec).replace('193665', '999999'), spec, row(spec))

    def test_wrong_linked_jsonld_address_is_rejected(self):
        spec = check.SAMPLES[2]
        with self.assertRaises(check.Stop):
            check.inspect_page(page(spec).replace('"postalCode": "16504"', '"postalCode": "99999"'), spec, row(spec))

    def test_wrong_jsonld_about_is_rejected(self):
        spec = check.SAMPLES[2]
        html = page(spec).replace('"about": {"@id": "' + spec['url'] + '/#listingdata"}', '"about": {"@id": "wrong"}')
        with self.assertRaises(check.Stop):
            check.inspect_page(html, spec, row(spec))

    def test_property_photos_only_and_duplicates_not_counted(self):
        for spec in (check.SAMPLES[0], check.SAMPLES[2]):
            self.assertEqual(check.inspect_page(page(spec), spec, row(spec))['photo_links'], 1)

    def test_one_identity_mismatch_does_not_stop_remaining_samples(self):
        result = check.run(self.repo, 'probe', FakeReader(wrong=check.SAMPLES[0]['property_id']))
        self.assertEqual(result['results'][0]['status'], 'source_identity_mismatch')
        self.assertEqual(result['results'][1]['status'], 'verified_technical_listing')


if __name__ == '__main__':
    unittest.main()
