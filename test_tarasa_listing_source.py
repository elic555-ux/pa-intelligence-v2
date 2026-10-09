import copy
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

import property_sources as registry
import secondary_listing_source as legacy
import tarasa_listing_source as source
from test_secondary_listing_source import ROW, URL, page


def tarasa_page(row=ROW, canonical=None):
    return page(row).replace('1001', registry.listing_id(row)).replace(URL, canonical or source.target_url(row))


class FakeReader:
    def __init__(self, html=None, error=None):
        self.html, self.error, self.requests, self.initialized = html or tarasa_page(), error, 0, False
    def initialize(self):
        if not self.initialized:
            self.requests += 1
            self.initialized = True
    def get(self, url):
        self.requests += 1
        if self.error:
            raise self.error
        return self.html


class TarasaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        registry.atomic_json(self.repo / 'properties.json', [ROW])
        registry.atomic_json(self.repo / 'cloud-test.json', {'deal': 'protected'})
        self.protected = {name: (self.repo / name).read_bytes() for name in ('properties.json', 'cloud-test.json')}

    def record(self, days=0):
        return source.parse_detail(tarasa_page(), ROW, source.target_url(ROW), (self.now-timedelta(days=days)).isoformat())

    def put(self, record, root=source.ROOT):
        registry.atomic_json(self.repo / root / (source.cache_key(ROW['id']) + '.json'), record)

    def test_verified_fields_photos_and_original_units(self):
        report = source.run(self.repo, 'fetch', ROW['id'], reader=FakeReader(), now=self.now)
        self.assertEqual(report['status'], 'published')
        self.assertEqual(report['new_source_network_requests'], 2)
        self.assertEqual(report['new_rentcast_calls'], 0)
        r = registry.read_json(self.repo / source.ROOT / (source.cache_key(ROW['id'])+'.json'))
        self.assertEqual(r['provider'], 'tarasa')
        self.assertEqual(r['facts']['heating']['source'], source.SOURCE)
        self.assertEqual(r['facts']['roof_type']['source_url'], source.target_url(ROW))
        self.assertEqual(r['facts']['lot_area_acres']['unit'], 'acre')
        self.assertNotIn('occupancy', r['facts'])
        self.assertNotIn('roof_condition', r['facts'])
        self.assertNotIn('lot_size', r['facts'])
        self.assertEqual(len(r['photos']), 1)
        self.assertEqual(self.protected, {n: (self.repo/n).read_bytes() for n in self.protected})

    def test_check_is_network_and_write_free(self):
        self.put(self.record())
        before = {p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        reader = FakeReader()
        self.assertEqual(source.run(self.repo, 'check', ROW['id'], reader=reader)['status'], 'cache_ready')
        self.assertEqual(reader.requests, 0)
        self.assertEqual(before, {p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})

    def test_fresh_tarasa_cache_has_zero_http(self):
        self.put(self.record())
        reader = FakeReader(error=AssertionError('No HTTP expected'))
        report = source.run(self.repo, 'fetch', ROW['id'], reader=reader, now=self.now)
        self.assertEqual(report['status'], 'cache_used')
        self.assertEqual(reader.requests, 0)

    def test_clear_choice_cache_reused_without_copy_or_http(self):
        clear = legacy.parse_detail(page(), ROW, URL, self.now.isoformat())
        self.put(clear, legacy.ROOT)
        before = (self.repo/legacy.ROOT/(source.cache_key(ROW['id'])+'.json')).read_bytes()
        reader = FakeReader(error=AssertionError('No HTTP expected'))
        report = source.run(self.repo, 'fetch', ROW['id'], reader=reader, now=self.now)
        self.assertEqual(report['status'], 'cache_used')
        self.assertEqual(report['cache_provider'], 'clearchoice')
        self.assertFalse((self.repo/source.ROOT).exists())
        self.assertEqual(reader.requests, 0)
        self.assertEqual(before, (self.repo/legacy.ROOT/(source.cache_key(ROW['id'])+'.json')).read_bytes())

    def test_clear_choice_block_cannot_block_tarasa(self):
        registry.atomic_json(self.repo/legacy.SOURCE_STATE, {'retry_after_at': (self.now+timedelta(days=2)).isoformat()})
        report = source.run(self.repo, 'fetch', ROW['id'], reader=FakeReader(), now=self.now)
        self.assertEqual(report['status'], 'published')

    def test_tarasa_block_stops_and_records_exact_request(self):
        error = source.StopSource('source_blocked', 403, '172800')
        error.request_url = source.ORIGIN + '/robots.txt'
        report = source.run(self.repo, 'fetch', ROW['id'], reader=FakeReader(error=error), now=self.now)
        self.assertEqual(report['status'], 'source_blocked')
        state = registry.read_json(self.repo/source.SOURCE_STATE)
        self.assertEqual(state['http_status'], 403)
        self.assertEqual(state['request_url'], error.request_url)
        self.assertGreaterEqual(source.parse_date(state['retry_after_at']), self.now+timedelta(days=2))
        reader = FakeReader()
        self.assertEqual(source.run(self.repo, 'fetch', ROW['id'], reader=reader, now=self.now)['status'], 'source_cooldown')
        self.assertEqual(reader.requests, 0)

    def test_failed_refresh_preserves_original_facts_and_retrieval_date(self):
        old = self.record(8)
        self.put(old)
        source.run(self.repo, 'fetch', ROW['id'], reader=FakeReader(error=source.StopSource('source_blocked',403)), now=self.now)
        saved = registry.read_json(self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json'))
        self.assertEqual(saved['facts'], old['facts'])
        self.assertEqual(saved['retrieved_at'], old['retrieved_at'])
        self.assertEqual(saved['status'], 'published')

    def test_same_mls_canonical_slug_still_requires_exact_address(self):
        alternate = source.ORIGIN + '/property-search/detail/56/1001/canonical-street-slug/'
        r = source.parse_detail(tarasa_page(canonical=alternate), ROW, source.target_url(ROW))
        self.assertEqual(r['source_url'], alternate)
        with self.assertRaisesRegex(ValueError, 'full address'):
            source.parse_detail(tarasa_page({**ROW,'city':'Beechview'},alternate), ROW, source.target_url(ROW))

    def test_wrong_mls_host_county_unit_and_fraction_rejected(self):
        with self.assertRaisesRegex(ValueError, 'canonical MLS'):
            source.parse_detail(tarasa_page(canonical=source.target_url(ROW).replace('/1001/','/9999/')), ROW, source.target_url(ROW))
        with self.assertRaisesRegex(ValueError, 'canonical MLS'):
            source.parse_detail(tarasa_page().replace('www.tarasa.com','www.tarasa.com.evil.test'), ROW, source.target_url(ROW))
        for row in ({**ROW,'address':'12 First St #2'}, {**ROW,'address':'12 1/2 First St'}, {**ROW,'zip':'15217'}):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, 'full address'):
                source.parse_detail(tarasa_page(row, source.target_url(ROW)), ROW, source.target_url(ROW))
        with self.assertRaisesRegex(ValueError, 'county'):
            source.parse_detail(tarasa_page().replace('Allegheny-South','Erie'), ROW, source.target_url(ROW))

    def test_invalid_requested_url_is_rejected_before_http(self):
        for url in ('https://www.tarasa.com.evil.test/x', source.target_url(ROW).replace('/1001/','/9999/'), URL):
            reader = FakeReader()
            self.assertEqual(source.run(self.repo,'fetch',ROW['id'],url,reader=reader)['status'],'unsupported_source_url')
            self.assertEqual(reader.requests, 0)

    def test_ambiguous_inventory_id_never_fetches(self):
        registry.atomic_json(self.repo/'properties.json',[ROW,{**ROW,'address':'98 Other St'}])
        reader = FakeReader()
        self.assertEqual(source.run(self.repo,'fetch',ROW['id'],reader=reader)['status'],'ambiguous_or_missing_property_id')
        self.assertEqual(reader.requests, 0)

    def test_request_limit_is_not_a_failure_or_cooldown(self):
        report = source.run(self.repo,'fetch',ROW['id'],reader=FakeReader(error=source.StopSource('source_request_budget_exhausted')))
        self.assertEqual(report['status'],'source_request_budget_exhausted')
        self.assertFalse((self.repo/source.ROOT).exists())
        self.assertFalse((self.repo/source.SOURCE_STATE).exists())

    def test_tarasa_and_clear_choice_join_same_entity_with_separate_provenance(self):
        registry.save(self.repo,*registry.prepare(self.repo))
        entity_before = set(registry.prepare(self.repo)[0]['entities'])
        self.put(legacy.parse_detail(page(),ROW,URL,self.now.isoformat()),legacy.ROOT)
        tarasa = self.record()
        tarasa['facts']['roof_type']['value'] = 'Metal'
        self.put(tarasa)
        reg, manifest = registry.prepare(self.repo)
        self.assertEqual(entity_before,set(reg['entities']))
        self.assertEqual(manifest['bound_additional_snapshots'],2)
        self.assertEqual(manifest['entities'],1)
        self.assertEqual(next(iter(reg['entities'].values()))['fields']['roof_type']['status'],'conflicting_source_values')
        self.assertEqual(self.protected,{n:(self.repo/n).read_bytes() for n in self.protected})

    def test_provider_swaps_and_wrong_fact_sources_do_not_join(self):
        r=self.record()
        r['provider']='clearchoice'
        self.assertFalse(registry.secondary_record_matches(ROW,r))
        r=self.record()
        r['source_url']=r['source_url'].replace('/1001/','/9999/')
        self.assertFalse(registry.secondary_record_matches(ROW,r))
        r=self.record()
        r['facts']['roof_type']['source']=legacy.SOURCE
        self.put(r)
        reg,_=registry.prepare(self.repo)
        self.assertNotIn('roof_type',next(iter(reg['entities'].values()))['fields'])

    def test_current_relisting_does_not_reuse_old_record(self):
        r=self.record()
        self.assertFalse(source.bound({**ROW,'docket_id':'MLS-1002'},r))
        self.assertFalse(source.bound({**ROW,'url':'https://www.redfin.com/other/home/9'},r))

    def test_missing_listing_does_not_create_provider_block(self):
        class Opener:
            def open(self,req,timeout):
                if req.full_url.endswith('/robots.txt'):
                    return io.BytesIO(b'User-agent: *\nDisallow: /private/\n')
                raise HTTPError(req.full_url,404,'Not found',{},None)
        clock=[0]
        reader=source.PublicReader(opener=Opener(),clock=lambda:clock[0],sleep=lambda delay:clock.__setitem__(0,clock[0]+delay))
        report=source.run(self.repo,'fetch',ROW['id'],reader=reader)
        self.assertEqual(report['status'],'listing_not_available')
        self.assertFalse((self.repo/source.SOURCE_STATE).exists())

    def test_http_challenge_and_disallowed_paths_stop(self):
        class Response(io.BytesIO):
            status=218
        class Challenge:
            def open(self,req,timeout):
                return Response(b'JavaScript challenge')
        reader=source.PublicReader(opener=Challenge())
        with self.assertRaises(source.StopSource) as error:
            reader.initialize()
        self.assertEqual(error.exception.http_status,218)
        self.assertEqual(reader.requests,1)

    def test_html_robots_challenge_cannot_authorize_listing_fetch(self):
        class Challenge:
            def open(self,req,timeout):
                return io.BytesIO(b'<html><script>challenge()</script></html>')
        reader=source.PublicReader(opener=Challenge())
        with self.assertRaises(source.StopSource):
            reader.initialize()
        self.assertEqual(reader.requests,1)
        self.assertIsNone(reader.robots)


if __name__=='__main__':
    unittest.main()
