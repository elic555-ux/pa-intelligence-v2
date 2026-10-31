import json, tempfile, unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
import report_area as area

class AreaTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=datetime(2026,10,31,tzinfo=timezone.utc)
        patch.dict(area.os.environ,{'CENSUS_API_KEY':'offline-test-key'}).start();self.addCleanup(patch.stopall)
        self.point={'geoid':'420031702001001','tract':'170200','parcel_id':'0012E00199000000'}
    def payload(self, geoid='42003170200'):
        h=['NAME']+[c+s for c in area.VARIABLES for s in ('E','M')]+['state','county','tract']
        return [h,['Census Tract 1702']+['100','10']*len(area.VARIABLES)+[geoid[:2],geoid[2:5],geoid[5:]]]
    def test_official_values_and_margins_are_preserved_without_grade(self):
        with patch.object(area,'get_json',return_value=self.payload()) as get:
            r=area.collect(self.point,self.root,now=self.now)
        self.assertEqual(r['metrics'][0]['value'],100);self.assertEqual(r['metrics'][0]['margin_of_error'],10)
        self.assertIsNone(r['class']);self.assertFalse(r['class_verified']);self.assertEqual(get.call_count,1)
    def test_same_quarter_reuses_cache_without_network(self):
        with patch.object(area,'get_json',return_value=self.payload()):area.collect(self.point,self.root,now=self.now)
        with patch.object(area,'get_json',side_effect=AssertionError('network')):
            self.assertEqual(area.collect(self.point,self.root,now=self.now)['status'],'available')
    def test_new_quarter_failure_keeps_old_date_and_marks_stale(self):
        with patch.object(area,'get_json',return_value=self.payload()):area.collect(self.point,self.root,now=self.now)
        later=datetime(2027,1,2,tzinfo=timezone.utc)
        with patch.object(area,'get_json',side_effect=OSError('offline')):
            r=area.collect(self.point,self.root,now=later)
        self.assertEqual(r['status'],'cached_stale');self.assertEqual(r['checked_quarter'],'2026-Q4')
    def test_no_geography_or_disabled_lookup_never_calls_network(self):
        with patch.object(area,'get_json',side_effect=AssertionError('network')):
            self.assertEqual(area.collect({},self.root)['status'],'geography_unavailable')
            self.assertEqual(area.collect(self.point,self.root,allow_public=False)['status'],'lookup_disabled')
    def test_wrong_geography_rejected_and_suppression_not_zero(self):
        with self.assertRaises(ValueError):area.parse(self.payload('42003170300'),'42003170200',2024,self.now.isoformat())
        p=self.payload();p[1][1]='-666666666'
        self.assertIsNone(area.parse(p,'42003170200',2024,self.now.isoformat())['metrics'][0]['value'])

if __name__=='__main__':unittest.main()
