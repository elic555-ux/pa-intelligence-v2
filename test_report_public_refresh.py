import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import report_public_refresh as refresh

class PublicRefreshTest(unittest.TestCase):
    def test_historical_county_school_label_maps_only_the_known_district(self):
        self.assertEqual(refresh.report_public_context.district_key('Penn Hills Twp'),'penn hills')
        self.assertEqual(refresh.report_public_context.district_key('Other Twp'),'other twp')
        self.assertEqual(refresh.report_public_context.district_key('Plum Boro'),'plum borough')
    def test_cached_school_label_is_canonicalized_without_network_or_new_timestamp(self):
        from datetime import datetime,timezone
        m=refresh.report_public_context
        now=datetime(2026,11,1,tzinfo=timezone.utc)
        record={'version':m.EDUCATION_VERSION,'parcel_id':'0012E00199000000','district':'Penn Hills Twp',
            'district_key':'penn hills twp','checked_quarter':'2026-Q4','observed_at':'2026-10-31T10:00:00+00:00'}
        with patch.object(m,'read',return_value=record),patch.object(m.store,'public_get',side_effect=AssertionError('network')):
            result=m.parcel_school_district({'parcel_id':record['parcel_id']},self.root,True,now)
        self.assertEqual(result['district_key'],'penn hills')
        self.assertEqual(result['observed_at'],record['observed_at'])
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
        self.assertEqual(json.loads((self.root/'public_context'/(refresh.key('qa')+'.json')).read_text())['county_record']['subject']['address'],self.p['address'])
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

    def test_municipality_alias_keeps_exact_street_zip_and_unique_county_match(self):
        p={**self.p,'address':'106 Marshall Dr','city':'Penn Hills','zip':'15235'}
        row={'PARID':'0370M00344000000','PROPERTYHOUSENUM':'106','PROPERTYADDRESS':'MARSHALL DR',
            'PROPERTYCITY':'PITTSBURGH','PROPERTYSTATE':'PA','PROPERTYZIP':'15235','MUNIDESC':'Penn Hills  '}
        def lookup(rows,subject=p):
            with patch.object(refresh.store,'public_get',return_value=json.dumps({'success':True,'result':{'records':rows}})):
                return refresh.store.allegheny(subject)
        self.assertEqual(lookup([row])['status'],'matched')
        self.assertEqual(lookup([row])['subject']['city'],'Penn Hills')
        self.assertEqual(lookup([row,row])['status'],'ambiguous_identity')
        self.assertNotEqual(lookup([{**row,'PROPERTYZIP':'15236'}])['status'],'matched')
        self.assertNotEqual(lookup([{**row,'PROPERTYADDRESS':'OTHER ST'}])['status'],'matched')
        self.assertNotEqual(lookup([row],{**p,'city':'Verona'})['status'],'matched')

    def test_free_sales_are_collected_with_county_facts_and_preserved_in_context(self):
        county={**self.county,'county':'Allegheny','fields':{'sqft':751,'beds':2}}
        sales={'property_id':'qa','status':'available','comparables':[{'amount':100000}]}
        with patch.object(refresh.store,'profile',return_value={'county_record':county}),patch.object(refresh.closed_sales,'parcel_point',return_value={}),patch.object(refresh.closed_sales,'collect',return_value=sales) as collect,patch.object(refresh.report_area,'collect',return_value={'status':'available'}),patch.object(refresh.report_public_context,'collect',return_value={'property_id':'qa'}):
            refresh.refresh(self.root,['qa'])
        self.assertEqual(collect.call_args.args[0]['sqft'],751)
        saved=json.loads((self.root/'public_context'/(refresh.key('qa')+'.json')).read_text())
        self.assertEqual(saved['closed_sales'],sales)

if __name__=='__main__':unittest.main()
