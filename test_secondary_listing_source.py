import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

import property_sources as registry
import secondary_listing_source as source

URL = source.ORIGIN + '/idx/12-first-st-pittsburgh-pa-15216/123456_spid/'
ROW = {'id': 'PA-MLS-1001', 'docket_id': 'MLS-1001', 'source_type': 'mls',
       'address': '12 First St', 'city': 'Pittsburgh', 'county': 'Allegheny', 'zip': '15216',
       'url': 'https://www.redfin.com/PA/Pittsburgh/12-First-St-15216/home/12345',
       'beds': 2, 'baths': 1, 'sqft': 900, 'lot_size': 3201}


def page(row=None):
    row = row or ROW
    structured = [{'@type': 'RealEstateListing', 'url': URL, 'about': {'@id': URL + '#property'}},
        {'@type': ['Product', 'SingleFamilyResidence'], '@id': URL + '#property',
         'address': {'streetAddress': row['address'], 'addressLocality': row['city'],
                     'addressRegion': 'PA', 'postalCode': row['zip']}}]
    fields = {'County': 'Allegheny-South', 'Roof': 'Composition', 'Heating': 'Forced Air, Gas',
              'Cooling': 'Wall/Window Unit(s)', 'Parking Features': 'Off Street', 'Parking Total': '2.0',
              'Lot Size': '0.07 Acres', 'Lot Dimensions': '0.0735', 'Building Area': '900.0',
              'Bedrooms': '2', 'Bathrooms': '1', 'Year Built': '1890'}
    details = ''.join('<div><strong>' + k + '</strong><span>' + v + '</span></div>' for k, v in fields.items())
    return ('<html><link rel="canonical" href="' + URL + '">'
        '<h1><span>' + row['address'] + '</span><span>Pittsburgh PA 15216</span></h1>'
        '<button data-mls="1001"></button><span>MLS #</span><strong>1001</strong>'
        '<span>Last Updated</span><strong>10/8/2026</strong>'
        '<script type="application/ld+json">' + json.dumps(structured) + '</script>'
        '<section id="propertyDetails">' + details + '</section>'
        '<div><strong>Occupancy</strong><span>Nearby property tenant occupied</span></div>'
        '<img src="https://cdn.listingphotos.sierrastatic.com/pics3x/v123/56/56_1001_01.jpg">'
        '<img src="https://cdn.listingphotos.sierrastatic.com/large/v123/56/56_1001_01.jpg">'
        '<img src="https://cdn.listingphotos.sierrastatic.com/pics1x/v123/56/56_9999_02.jpg"></html>')


class FakeReader:
    def __init__(self, html=None, error=None):
        self.html, self.error, self.requests = html or page(), error, 0
    def initialize(self):
        self.requests += 1
    def get(self, url):
        self.requests += 1
        if self.error:
            raise self.error
        return self.html


class AdditionalSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        registry.atomic_json(self.repo / 'properties.json', [ROW])
        self.before = (self.repo / 'properties.json').read_bytes()
    def record(self, days=0):
        return source.parse_detail(page(), ROW, URL, (datetime.now(timezone.utc)-timedelta(days=days)).isoformat())
    def put(self, record):
        registry.atomic_json(self.repo / source.ROOT / (source.cache_key(ROW['id']) + '.json'), record)
    def test_labeled_technical_fields_have_provenance(self):
        r = self.record()
        self.assertEqual(r['facts']['heating']['value'], 'Forced Air, Gas')
        self.assertEqual(r['facts']['parking_spaces']['value'], 2)
        self.assertEqual(r['facts']['roof_type']['source_url'], URL)
        self.assertNotIn('occupancy', r['facts'])
        self.assertNotIn('roof_condition', r['facts'])
    def test_rounded_acreage_is_not_converted_or_replaced(self):
        r = self.record()
        self.assertEqual(r['facts']['lot_area_acres']['value'], .07)
        self.assertEqual(r['facts']['lot_area_acres']['unit'], 'acre')
        self.assertNotIn('lot_size', r['facts'])
    def test_photos_only_current_mls_and_unique_sequence(self):
        r = self.record()
        self.assertEqual(len(r['photos']), 1)
        self.assertIsNone(r['photos'][0]['capture_date'])
        self.assertFalse(source.photo_url('https://cdn.listingphotos.sierrastatic.com.evil.test/x.jpg', '1001'))
    def test_wrong_address_rejected_even_with_same_mls(self):
        with self.assertRaisesRegex(ValueError, 'full address'):
            source.parse_detail(page({**ROW, 'address': '14 First St'}), ROW, URL)
    def test_unit_and_fraction_are_not_collapsed(self):
        for address in ('12 First St #2', '12 1/2 First St'):
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, 'full address'):
                source.parse_detail(page({**ROW, 'address': address}), ROW, URL)
    def test_relisted_mls_rejected(self):
        with self.assertRaisesRegex(ValueError, 'MLS'):
            source.parse_detail(page().replace('data-mls="1001"', 'data-mls="1002"'), ROW, URL)
    def test_county_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'county'):
            source.parse_detail(page().replace('Allegheny-South', 'Erie'), ROW, URL)
    def test_canonical_or_property_reference_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'canonical'):
            source.parse_detail(page().replace('rel="canonical"', 'rel="alternate"'), ROW, URL)
        with self.assertRaisesRegex(ValueError, 'listing subject'):
            source.parse_detail(page().replace('"about": {"@id":', '"unrelated": {"@id":'), ROW, URL)
    def test_check_has_no_network_or_writes(self):
        self.put(self.record())
        before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        reader = FakeReader()
        report = source.run(self.repo, 'check', ROW['id'], reader=reader)
        self.assertEqual(report['status'], 'cache_ready')
        self.assertEqual(reader.requests, 0)
        self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})
    def test_fresh_cache_uses_no_source_requests(self):
        self.put(self.record())
        reader = FakeReader(error=AssertionError('No source request should occur'))
        report = source.run(self.repo, 'fetch', ROW['id'], reader=reader)
        self.assertEqual(report['status'], 'cache_used')
        self.assertEqual(report['new_source_network_requests'], 0)
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.before)
    def test_fetch_preserves_inventory_and_joins_one_entity(self):
        before_registry, _ = registry.prepare(self.repo)
        registry.save(self.repo, *registry.prepare(self.repo))
        report = source.run(self.repo, 'fetch', ROW['id'], URL, reader=FakeReader())
        new_registry, manifest = registry.prepare(self.repo)
        self.assertEqual(report['status'], 'published')
        self.assertEqual(report['new_rentcast_calls'], 0)
        self.assertEqual(manifest['entities'], 1)
        self.assertEqual(manifest['multi_source_entities'], 1)
        self.assertEqual(manifest['bound_additional_snapshots'], 1)
        self.assertEqual(set(before_registry['entities']), set(new_registry['entities']))
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.before)
    def test_failed_attempt_preserves_successful_facts_and_date(self):
        old = self.record(days=8)
        self.put(old)
        report = source.run(self.repo, 'fetch', ROW['id'], URL, reader=FakeReader(error=source.StopSource('source_blocked', 403)))
        saved = registry.read_json(self.repo / source.ROOT / (source.cache_key(ROW['id']) + '.json'))
        self.assertEqual(report['status'], 'source_blocked')
        self.assertEqual(saved['status'], 'published')
        self.assertEqual(saved['facts'], old['facts'])
        self.assertEqual(saved['retrieved_at'], old['retrieved_at'])
        self.assertGreater(source.parse_date(saved['last_attempt']['retry_after_at']), datetime.now(timezone.utc))
    def test_global_block_cooldown_stops_other_property_request(self):
        source.run(self.repo, 'fetch', ROW['id'], URL, reader=FakeReader(error=source.StopSource('source_blocked', 403)))
        reader = FakeReader()
        report = source.run(self.repo, 'fetch', ROW['id'], URL, reader=reader)
        self.assertEqual(report['status'], 'source_cooldown')
        self.assertEqual(reader.requests, 0)
    def test_long_retry_after_is_respected(self):
        now = datetime.now(timezone.utc)
        self.assertGreaterEqual(source.parse_date(source.cooldown_until('172800', now)), now+timedelta(days=2))
    def test_unsafe_source_url_rejected_without_network(self):
        reader = FakeReader()
        report = source.run(self.repo, 'fetch', ROW['id'], 'https://example.com/idx/x/123_spid/', reader=reader)
        self.assertEqual(report['status'], 'unsupported_source_url')
        self.assertEqual(reader.requests, 0)
    def test_duplicate_property_id_or_known_ambiguity_prevents_fetch(self):
        registry.atomic_json(self.repo / 'properties.json', [ROW, {**ROW, 'address': '14 First St'}])
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            source.eligible(self.repo, ROW['id'])
        reader = FakeReader()
        report = source.run(self.repo, 'fetch', ROW['id'], URL, reader=reader)
        self.assertEqual(report['status'], 'ambiguous_or_missing_property_id')
        self.assertEqual(reader.requests, 0)
        registry.atomic_json(self.repo / 'properties.json', [ROW])
        registry.atomic_json(self.repo / registry.ROOT / 'aliases.json', {'ambiguous': {ROW['id']: []}})
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            source.eligible(self.repo, ROW['id'])
    def test_tampered_snapshot_does_not_join(self):
        r = self.record()
        r['inventory_source_url'] = 'https://example.com/unrelated'
        self.put(r)
        _, manifest = registry.prepare(self.repo)
        self.assertEqual(manifest['bound_additional_snapshots'], 0)
        self.assertEqual(manifest['excluded_additional_snapshots'], 1)
    def test_mismatched_field_does_not_enter_registry(self):
        r = self.record()
        r['facts']['roof_type']['listing_id'] = '2002'
        self.put(r)
        reg, _ = registry.prepare(self.repo)
        self.assertNotIn('roof_type', next(iter(reg['entities'].values()))['fields'])
    def test_reader_spacing_and_robots_denial(self):
        calls, now, waits = [], [0], []
        class Opener:
            def open(self, req, timeout):
                calls.append((req.full_url, now[0]))
                body = b'User-agent: *\nCrawl-delay: 15\nDisallow: /sist/\n' if req.full_url.endswith('robots.txt') else b'<html></html>'
                return io.BytesIO(body)
        def sleep(delay):
            waits.append(delay)
            now[0] += delay
        reader = source.PublicReader(clock=lambda:now[0], sleep=sleep, opener=Opener())
        reader.initialize()
        reader.get(URL)
        self.assertEqual(waits, [15])
        with self.assertRaises(source.StopSource):
            reader.get(source.ORIGIN + '/sist/private')
        self.assertEqual(reader.requests, 2)
    def test_reader_stops_once_on_403_and_429(self):
        for code in (403, 429):
            with self.subTest(code=code):
                class Opener:
                    def open(self, req, timeout):
                        raise HTTPError(req.full_url, code, 'blocked', {'Retry-After':'120'}, None)
                reader = source.PublicReader(opener=Opener())
                with self.assertRaises(source.StopSource) as raised:
                    reader.initialize()
                self.assertEqual(raised.exception.http_status, code)
                self.assertEqual(reader.requests, 1)
    def test_discovery_uses_public_cards_and_confirms_subject(self):
        card = ('<div class="si-listing" data-url="' + URL + '"><button data-mls="1001"></button>'
                '<div class="si-listing__title-main">12 First Street</div>'
                '<div class="si-listing__title-description">Pittsburgh, PA 15216</div></div>')
        reader = FakeReader(html=card)
        self.assertEqual(source.discover(reader, ROW), URL)
        self.assertEqual(reader.requests, 1)
    def test_discovery_stops_after_three_pages(self):
        reader = FakeReader(html='<div class="si-listing" data-url="' + URL + '"><button data-mls="9999"></button></div>')
        with self.assertRaisesRegex(source.StopSource, 'not_found'):
            source.discover(reader, ROW)
        self.assertEqual(reader.requests, 3)


if __name__ == '__main__':
    unittest.main()
