"""Offline tests against a stripped real public Howard Hanna detail fixture."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import property_sources as registry
import howardhanna_listing_source as source
import eriemoves_listing_source as erie
from test_eriemoves_listing_source import ROW as ERIE_ROW, SPEC as ERIE_SPEC, page as erie_page

FIXTURES = Path(__file__).parent / 'test_fixtures'
ROW = registry.read_json(FIXTURES / 'howardhanna_193660_row.json')
RECORD = registry.read_json(FIXTURES / 'howardhanna_193660.json')
URL = RECORD['source_url']
HTML = (FIXTURES / 'howardhanna_193660.html').read_text()


class FakeReader:
    def __init__(self, html=HTML, error=None):
        self.html, self.error, self.requests, self.initialized = html, error, 0, False
    def initialize(self):
        if not self.initialized:
            self.requests += 1
            self.initialized = True
    def get(self, url):
        self.requests += 1
        if self.error:
            raise self.error
        return self.html


class Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.now = datetime(2026,10,10,18,30,tzinfo=timezone.utc)
        registry.atomic_json(self.repo / 'properties.json',[ROW])
        self.inventory = (self.repo / 'properties.json').read_bytes()

    def test_real_fixture_has_exact_facts_units_and_only_published_gallery(self):
        record = source.parse_detail(HTML,ROW,URL,self.now.isoformat())
        self.assertEqual(len(record['facts']),16)
        self.assertEqual(record['facts']['sqft']['value'],1488)
        self.assertEqual(record['facts']['lot_area_acres']['unit'],'acre')
        self.assertEqual(record['facts']['roof_type']['value'],'Asphalt roof')
        self.assertEqual(record['facts']['baths']['value'],1)
        self.assertEqual(len(record['photos']),1)
        self.assertNotIn('occupancy',record['facts'])
        self.assertNotIn('roof_condition',record['facts'])
        self.assertEqual(record['identity_evidence']['county_label_on_detail'],'not_reported')
        self.assertTrue(registry.secondary_record_matches(ROW,record))

    def test_mls_canonical_board_address_city_zip_and_unit_are_independent(self):
        cases = [HTML.replace('data-mlsnumber="193660"','data-mlsnumber="999999"'),
            HTML.replace('<dd>193660</dd>','<dd>999999</dd>'),
            HTML.replace('data-mlsname="EriePA"','data-mlsname="WestPenn"'),
            HTML.replace('itemprop="streetAddress">1915 APPLE Drive','itemprop="streetAddress">1915 APPLE Drive Unit 2'),
            HTML.replace('itemprop="addressLocality">Fairview','itemprop="addressLocality">Erie'),
            HTML.replace('itemprop="postalCode">16415','itemprop="postalCode">16504'),
            HTML.replace('itemprop="addressRegion">PA','itemprop="addressRegion">NY'),
            HTML.replace('rel="canonical"','rel="alternate"'),
            HTML.replace('New Listing','Pending')]
        for html in cases:
            with self.subTest(html=html[:100]):
                with self.assertRaisesRegex(ValueError,'source_identity_mismatch'):
                    source.parse_detail(html,ROW,URL)

    def test_duplicate_summary_and_header_cannot_supply_facts(self):
        modified = HTML.replace('<dd>1957</dd>','<dd>1957</dd><div class="dl-item"><dt>built</dt><dd>2001</dd></div>')
        with self.assertRaisesRegex(ValueError,'conflicting subject labels'):
            source.parse_detail(modified,ROW,URL)
        modified = HTML.replace('</div><div class="prop-section', '</div><div class="prop-section',1) + '<dl><div class="dl-item"><dt>built</dt><dd>2026</dd></div></dl><img src="https://evil.example/agent.jpg">'
        record = source.parse_detail(modified,ROW,URL)
        self.assertEqual(record['facts']['year_built']['value'],1957)
        self.assertEqual(len(record['photos']),1)

    def test_bad_source_urls_and_photo_hosts_queries_are_rejected(self):
        for url in (URL+'?mlsnumber=193660',URL+'#fragment',URL.replace('www.howardhanna.com','evil.example'),URL.replace('https:','http:'),URL.replace('www.howardhanna.com','x@www.howardhanna.com')):
            self.assertIsNone(source.source_url(url))
        photo=RECORD['photos'][0]['url']
        self.assertTrue(registry.hanna_photo_url(photo))
        for value in (photo.replace('?d=l','?d=t'),photo+'&track=1',photo+'#fragment',photo.replace('photos.prod.cirrussystem.net','evil.example')):
            self.assertFalse(registry.hanna_photo_url(value))

    def test_fetch_binds_registry_without_mutating_inventory(self):
        reader = FakeReader()
        report = source.run(self.repo,'fetch',ROW['id'],URL,reader=reader,now=self.now,write_status=False)
        self.assertEqual(report['status'],'published')
        self.assertEqual(report['new_source_network_requests'],2)
        self.assertEqual(report['new_rentcast_calls'],0)
        record=registry.read_json(self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json'))
        self.assertTrue(source.bound(ROW,record))
        observations,stats=registry.collect(self.repo)
        self.assertEqual(stats['bound_additional_snapshots'],1)
        self.assertEqual((self.repo/'properties.json').read_bytes(),self.inventory)

    def test_check_has_no_requests_or_writes_and_fetch_does_not_guess_url(self):
        reader=FakeReader()
        before={str(p):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        self.assertEqual(source.run(self.repo,'check',ROW['id'],reader=reader)['status'],'cache_missing')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})
        self.assertEqual(source.run(self.repo,'fetch',ROW['id'],reader=reader,write_status=False)['status'],'source_url_required')
        self.assertEqual(reader.requests,0)

    def test_fresh_snapshot_precedes_cooldown_and_identity_change_invalidates_it(self):
        record=source.parse_detail(HTML,ROW,URL,self.now.isoformat())
        registry.atomic_json(self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json'),record)
        registry.atomic_json(self.repo/source.SOURCE_STATE,{'retry_after_at':(self.now+timedelta(days=2)).isoformat()})
        reader=FakeReader()
        self.assertEqual(source.run(self.repo,'fetch',ROW['id'],URL,reader=reader,now=self.now)['status'],'cache_used')
        self.assertEqual(reader.requests,0)
        self.assertFalse(source.bound({**ROW,'address':ROW['address']+' Unit 2'},record))
        self.assertFalse(source.bound({**ROW,'url':ROW['url']+'/changed'},record))
        self.assertFalse(source.bound({**ROW,'county':'Allegheny'},record))

    def test_erie_cache_is_reused_without_copy_or_requests(self):
        row={**ERIE_ROW,'market_status':'active'}
        registry.atomic_json(self.repo/'properties.json',[row])
        record=erie.parse_detail(erie_page(),row,ERIE_SPEC['url'],self.now.isoformat())
        registry.atomic_json(self.repo/erie.ROOT/(source.cache_key(row['id'])+'.json'),record)
        reader=FakeReader()
        report=source.run(self.repo,'fetch',row['id'],reader=reader,now=self.now)
        self.assertEqual(report['status'],'cache_used')
        self.assertEqual(report['cache_provider'],'eriemoves')
        self.assertEqual(reader.requests,0)
        self.assertFalse((self.repo/source.ROOT).exists())

    def test_failed_refresh_preserves_successful_snapshot(self):
        record=source.parse_detail(HTML,ROW,URL,(self.now-timedelta(days=8)).isoformat())
        path=self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json')
        registry.atomic_json(path,record)
        report=source.run(self.repo,'fetch',ROW['id'],URL,reader=FakeReader(error=source.StopSource('source_blocked',403)),now=self.now)
        self.assertEqual(report['status'],'source_blocked')
        saved=registry.read_json(path)
        self.assertEqual(saved['facts'],record['facts'])
        self.assertEqual(saved['retrieved_at'],record['retrieved_at'])
        reader=FakeReader()
        self.assertEqual(source.run(self.repo,'fetch',ROW['id'],URL,reader=reader,now=self.now)['status'],'source_cooldown')
        self.assertEqual(reader.requests,0)


if __name__=='__main__':
    unittest.main()
