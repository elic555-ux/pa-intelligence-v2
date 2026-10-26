"""Offline report gallery scope, cache, identity and block regression checks."""
import copy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import unittest
from unittest.mock import patch
import report_gallery as gallery
import tarasa_listing_source as source
from test_secondary_listing_source import ROW
from test_tarasa_listing_source import tarasa_page,FakeReader

def many_photos(count=80):
    html=tarasa_page()
    images=''.join('<img src="https://cdn.listingphotos.sierrastatic.com/pics1x/v123/56/56_1001_'+str(n).zfill(2)+'.jpg">' for n in range(1,count+1))
    return html.replace('</body>',images+'</body>')

class ReportGalleryTest(unittest.TestCase):
    def setUp(self):
        self.repo=Path('offline');self.now=datetime(2026,10,26,12,tzinfo=timezone.utc);self.data={}
        self.path=self.repo/source.ROOT/(source.cache_key(ROW['id'])+'.json')
        self.patches=[patch.object(gallery.core,'eligible',return_value=copy.deepcopy(ROW)),
            patch.object(gallery,'read',side_effect=lambda p:copy.deepcopy(self.data.get(str(p)))),
            patch.object(gallery.registry,'atomic_json',side_effect=lambda p,v:self.data.__setitem__(str(p),copy.deepcopy(v)))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def old(self):
        record=source.parse_detail(tarasa_page(),ROW,source.target_url(ROW),(self.now-timedelta(days=2)).isoformat())
        self.data[str(self.path)]=record;return copy.deepcopy(record)
    def test_explicit_report_retains_up_to_sixty_published_photos_with_two_requests(self):
        old=self.old();reader=FakeReader(html=many_photos())
        r=gallery.collect(ROW,self.repo,reader=reader,now=self.now)
        self.assertEqual((r['status'],r['photo_count'],r['source_network_requests']),('published',60,2))
        self.assertEqual(r['rentcast_calls'],0);self.assertEqual(r['gemini_calls'],0)
        self.assertEqual(self.data[str(self.path)]['identity'],old['identity'])
        self.assertEqual(self.data[str(self.path)]['report_gallery_version'],gallery.VERSION)
    def test_lazy_published_images_are_read_without_guessing_urls(self):
        html=many_photos(15).replace(' src=', ' data-src=')
        r=source.parse_detail(html,ROW,source.target_url(ROW),photo_limit=60)
        self.assertEqual(len(r['photos']),15)
        self.assertTrue(all('_1001_' in x['url'] for x in r['photos']))
    def test_basic_source_parser_still_defaults_to_three(self):
        r=source.parse_detail(many_photos(),ROW,source.target_url(ROW))
        self.assertEqual(len(r['photos']),3)
    def test_current_report_gallery_cache_makes_no_requests_and_keeps_original_date(self):
        record=self.old();record['report_gallery_version']=gallery.VERSION;self.data[str(self.path)]=record
        reader=FakeReader(error=AssertionError('No network expected'))
        r=gallery.collect(ROW,self.repo,reader=reader,now=self.now)
        self.assertEqual(r['status'],'cache_used');self.assertEqual(reader.requests,0)
        self.assertEqual(r['observed_at'],record['retrieved_at'])
    def test_block_preserves_success_and_prevents_another_attempt(self):
        old=self.old();reader=FakeReader(error=source.StopSource('source_blocked',403))
        r=gallery.collect(ROW,self.repo,reader=reader,now=self.now)
        self.assertEqual(r['status'],'source_blocked');self.assertEqual(self.data[str(self.path)],old)
        another=FakeReader(error=AssertionError('Cooldown expected'))
        r=gallery.collect(ROW,self.repo,reader=another,now=self.now)
        self.assertEqual(r['status'],'source_cooldown');self.assertEqual(another.requests,0)
    def test_wrong_full_address_stops_before_network_or_writes(self):
        reader=FakeReader();r=gallery.collect({**ROW,'address':'22 Other St'},self.repo,reader=reader,now=self.now)
        self.assertEqual(r['status'],'subject_mismatch');self.assertEqual(reader.requests,0);self.assertEqual(self.data,{})
    def test_disabled_collection_makes_no_requests(self):
        reader=FakeReader();r=gallery.collect(ROW,self.repo,allow_public=False,reader=reader,now=self.now)
        self.assertEqual(r['status'],'lookup_disabled');self.assertEqual(reader.requests,0)
    def test_empty_or_unrelated_page_cannot_replace_successful_photos(self):
        old=self.old();html=tarasa_page().replace('56_1001_01.jpg','56_9999_01.jpg')
        # Source fixture's only photo belongs to another MLS.
        import re
        html=re.sub(r'<img[^>]+>','',html)
        r=gallery.collect(ROW,self.repo,reader=FakeReader(html=html),now=self.now)
        self.assertEqual(r['status'],'no_source_photos');self.assertEqual(self.data[str(self.path)],old)

if __name__=='__main__':unittest.main()
