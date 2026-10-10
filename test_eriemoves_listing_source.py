import copy
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
import eriemoves_listing_source as source
import erie_source_access_check as pilot
import property_sources as registry
import secondary_listing_source as core

SPEC=pilot.SAMPLES[-1]
ROW={'id':SPEC['property_id'],'address':SPEC['address'],'city':SPEC['city'],'zip':SPEC['zip'],
     'county':'Erie','state':'PA','source_type':'mls','url':'https://www.redfin.com/PA/Erie/4117-Maxwell-Ave-16504/home/125028978'}
NOW=datetime(2026,10,10,13,tzinfo=timezone.utc)
PHOTO='https://i9.moxi.onl/img-pr-002339/eri/3337e8061d8b2880c71459d578ef95b89875d56a/1_2_full.jpg'
def page():
    url=SPEC['url']
    graph=[{'@type':'RealEstateListing','url':url,'about':{'@id':url+'/#listingdata'},'creditText':'GREATER ERIE BOARD OF REALTORS / Listed By: Broker'},
        {'@type':'House','@id':url+'/#listingdata','address':{'streetAddress':ROW['address'],'addressLocality':'Erie','addressRegion':'PA','postalCode':'16504'},
         'image':[{'url':PHOTO},{'url':PHOTO},{'url':'https://untrusted.example/agent.jpg'}]}]
    return ('<link rel="canonical" href="'+url+'"><h1>4117 Maxwell Ave Erie, PA 16504</h1><script type="application/ld+json">'+json.dumps({'@graph':graph})+'</script>'
        '<div class="listing-spec-table"><div class="spec-cell"><strong>MLS #:</strong>193473</div><div class="spec-cell"><strong>County</strong>Erie County</div>'
        '<div class="spec-cell"><strong>Year Built</strong>1972</div><div class="spec-cell"><strong>Style</strong>One Story</div></div>'
        '<div id="detail_feature_container"><div id="ld_heat_cool"><ul><li>Forced Air</li><li>Gas</li><li>Central Air</li></ul></div>'
        '<div id="ld_basement"><ul><li>Full</li></ul></div><div id="ld_ext_feat"><ul><li>Roof: Metal</li></ul></div>'
        '<div id="ld_util_info"><ul><li>Utilities:&nbsp;Water Source: Public</li><li>Sewer: Public Sewer</li></ul></div>'
        '<div id="ld_approx_living_area"><ul><li>1,008 sqft</li></ul></div></div>')
class Reader:
    def __init__(self,blocked=False,html=None): self.requests=0;self.blocked=blocked;self.html=html or page()
    def initialize(self):
        self.requests+=1
        if self.blocked: raise core.StopSource('source_blocked',403)
    def get(self,url): self.requests+=1;return self.html
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.repo=Path(self.tmp.name)
        (self.repo/'properties.json').write_text(json.dumps([ROW]))
        (self.repo/'sentinel').write_bytes(b'deals quota scanner preserved')
    def tearDown(self): self.tmp.cleanup()
    def parse(self,html=None): return source.parse_detail(html or page(),ROW,SPEC['url'],NOW.isoformat())
    def test_documented_fields_units_and_photos(self):
        r=self.parse();self.assertEqual(r['facts']['water']['value'],'Public');self.assertEqual(r['facts']['sqft']['unit'],'sqft')
        self.assertEqual(len(r['photos']),1);self.assertEqual(r['facts']['heating']['value'],'Forced Air, Gas')
        self.assertNotIn('occupancy',r['facts']);self.assertNotIn('roof_condition',r['facts'])
        self.assertTrue(registry.secondary_record_matches(ROW,r));self.assertTrue(source.bound(ROW,r))
    def test_check_no_network_or_writes(self):
        before={p.name:p.read_bytes() for p in self.repo.iterdir()};reader=Reader()
        r=source.run(self.repo,'check',ROW['id'],reader=reader,now=NOW)
        self.assertEqual(reader.requests,0);self.assertEqual(r['status'],'cache_missing')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.repo.iterdir()})
    def test_fetch_then_cache_no_repeated_network(self):
        original=(self.repo/'properties.json').read_bytes();reader=Reader()
        r=source.run(self.repo,'fetch',ROW['id'],reader=reader,now=NOW);self.assertTrue(r['cache_saved']);self.assertEqual(reader.requests,2)
        r=source.run(self.repo,'fetch',ROW['id'],reader=reader,now=NOW);self.assertEqual(r['status'],'cache_used');self.assertEqual(reader.requests,2)
        self.assertEqual(original,(self.repo/'properties.json').read_bytes());self.assertEqual((self.repo/'sentinel').read_bytes(),b'deals quota scanner preserved')
    def test_block_stops_and_persists_cooldown(self):
        reader=Reader(True);r=source.run(self.repo,'fetch',ROW['id'],reader=reader,now=NOW)
        self.assertEqual(r['status'],'source_blocked');self.assertEqual(reader.requests,1)
        r=source.run(self.repo,'fetch',ROW['id'],reader=reader,now=NOW);self.assertEqual(r['status'],'source_cooldown');self.assertEqual(reader.requests,1)
    def test_wrong_mls_city_zip_canonical_or_board_rejected(self):
        for old,new in [('193473','193474'),('Erie, PA','Fairview, PA'),('16504','16505'),('GREATER ERIE BOARD OF REALTORS','Other board')]:
            with self.subTest(new=new),self.assertRaises(ValueError):self.parse(page().replace(old,new))
    def test_duplicate_house_rejected(self):
        html=page();a=html.index('{"@graph"');b=html.index('</script>',a);data=json.loads(html[a:b]);data['@graph'].append(data['@graph'][1])
        with self.assertRaises(ValueError):self.parse(html[:a]+json.dumps(data)+html[b:])
    def test_duplicate_identity_or_technical_section_rejected(self):
        for extra in ['<h1>Another property</h1>','<div id="ld_heat_cool"><li>Gas</li></div>']:
            html=page()+extra if 'h1' in extra else page().replace('</div></div>',extra+'</div></div>')
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.parse(html)
    def test_url_binding_and_changed_inventory(self):
        r=self.parse()
        for name,value in [('address','4119 Maxwell Ave'),('city','Fairview'),('zip','16505'),('county','Allegheny'),('url','https://example.org/changed')]:
            row={**ROW,name:value};self.assertFalse(source.bound(row,r));self.assertFalse(registry.secondary_record_matches(row,r))
        r['source_url']=r['source_url'].replace('4117-Maxwell','4119-Maxwell');self.assertFalse(source.bound(ROW,r))
    def test_failed_refresh_keeps_previous_evidence(self):
        r=self.parse();path=self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json');registry.atomic_json(path,r)
        later=datetime(2026,10,20,13,tzinfo=timezone.utc)
        result=source.run(self.repo,'fetch',ROW['id'],reader=Reader(html=page().replace('193473','193474')),now=later)
        saved=registry.read_json(path);self.assertFalse(result['cache_saved']);self.assertEqual(saved['retrieved_at'],r['retrieved_at']);self.assertEqual(saved['facts'],r['facts'])
    def test_registry_imports_only_bound_facts(self):
        r=self.parse();r['facts']['roof_type']['listing_id']='wrong';registry.atomic_json(self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json'),r)
        observations,counts=registry.collect(self.repo);self.assertEqual(counts['bound_additional_snapshots'],1)
        self.assertNotIn('roof_type',observations[-1]['facts'])
    def test_missing_property_does_not_fetch(self):
        reader=Reader();r=source.run(self.repo,'fetch','missing',reader=reader,now=NOW);self.assertEqual(reader.requests,0);self.assertEqual(r['status'],'ambiguous_or_missing_property_id')
if __name__=='__main__': unittest.main()
