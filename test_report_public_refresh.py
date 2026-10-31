import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import report_public_refresh as refresh

class PublicRefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'COMPS_REPORTS'
        self.p={'id':'qa','address':'1 Main St','city':'Pittsburgh','state':'PA','zip':'15203'}
        (self.base/'properties.json').write_text(json.dumps([self.p]))
        self.county={'status':'matched','subject':self.p,'parcel_id':'0012E00199000000'}
    def test_official_only_refresh_preserves_archived_reports_and_writes_matching_context(self):
        old=self.root/'expanded_reports/history.json';old.parent.mkdir(parents=True);old.write_text('{"historical":true}')
        context={'property_id':'qa','property':self.p,'education':{'status':'available'},'crime':{'status':'not_collected'}}
        with patch.object(refresh.store,'profile',return_value={'county_record':self.county}),patch.object(refresh.closed_sales,'parcel_point',return_value={}),patch.object(refresh.report_area,'collect',return_value={'status':'available'}),patch.object(refresh.report_public_context,'collect',return_value=context):
            result=refresh.refresh(self.root,['qa'])
        self.assertEqual(result['properties'][0]['education'],'available')
        self.assertEqual(json.loads((self.root/'public_context'/(refresh.key('qa')+'.json')).read_text())['property_id'],'qa')
        self.assertEqual(old.read_text(),'{"historical":true}')
    def test_duplicate_inventory_does_not_lookup_or_overwrite_context(self):
        (self.base/'properties.json').write_text(json.dumps([self.p,self.p]))
        with patch.object(refresh.store,'profile',side_effect=AssertionError('lookup')):
            result=refresh.refresh(self.root,['qa'])
        self.assertEqual(result['properties'][0]['status'],'ambiguous_or_missing_inventory')
    def test_foreign_county_subject_never_queries_school_or_census(self):
        county={**self.county,'subject':{**self.p,'address':'2 Other St'}}
        with patch.object(refresh.store,'profile',return_value={'county_record':county}),patch.object(refresh.report_area,'collect',side_effect=AssertionError('lookup')):
            result=refresh.refresh(self.root,['qa'])
        self.assertEqual(result['properties'][0]['status'],'county_identity_unavailable')

if __name__=='__main__':unittest.main()
