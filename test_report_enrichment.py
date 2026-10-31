import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import report_enrichment as enrich

class ReportEnrichmentTest(unittest.TestCase):
    def setUp(self):
        patch.object(enrich.report_public_context,'collect',return_value={'status':'partial'}).start()
        patch.object(enrich.report_area,'collect',return_value={'status':'not_collected','class':None,'class_verified':False,'neighborhood':'South Side Flats'}).start()
        patch.object(enrich.report_property_taxes,'collect',return_value={'status':'lookup_disabled'}).start()
        self.addCleanup(patch.stopall)
        self.p={'id':'qa-1','address':'18 Main St','city':'Pittsburgh','state':'PA','zip':'15203','sqft':1400,'beds':3}
        self.profile={'inventory':{'status':'matched'},'county_record':{'status':'matched','county':'Allegheny','parcel_id':'0012E00199000000','subject':self.p,'fields':{'sqft':1500,'beds':3}}}
    def test_county_fields_require_matching_address(self):
        self.assertEqual(enrich.base_property(self.p,self.profile)['sqft'],1500)
        self.profile['county_record']['subject']={**self.p,'address':'22 Other St'}
        self.assertEqual(enrich.base_property(self.p,self.profile)['sqft'],1400)
    def test_disabled_collection_makes_no_network_or_paid_calls(self):
        with patch.object(enrich.closed_sales,'collect',side_effect=AssertionError('network')),patch.object(enrich.report_financing,'collect',side_effect=AssertionError('network')):
            r=enrich.collect(self.p,self.profile,allow_public=False)
        self.assertNotIn('rehab',r)
    def test_ambiguous_subject_never_contacts_providers(self):
        self.profile['inventory']['status']='ambiguous_identity'
        with patch.object(enrich.closed_sales,'collect',side_effect=AssertionError('network')):
            self.assertEqual(enrich.collect(self.p,self.profile)['status'],'subject_mismatch')
    def test_source_failure_does_not_discard_independent_results(self):
        with patch.object(enrich.closed_sales,'collect',side_effect=OSError('offline')),patch.object(enrich.report_financing,'collect',return_value={'status':'available','products':[{'lender':'test'}]}):
            r=enrich.collect(self.p,self.profile)
        self.assertEqual(r['closed_sales']['status'],'source_unavailable');self.assertEqual(len(r['financing']['products']),1)
    def test_no_invented_neighborhood_grade_from_price(self):
        with patch.object(enrich.closed_sales,'collect',return_value={'subject_location':{'neighborhood':'South Side Flats','tract':'170200','source_as_of':'2025-03'}}),patch.object(enrich.report_financing,'collect',return_value={'status':'available','products':[]}):
            r=enrich.collect({**self.p,'price':999999},self.profile)
        self.assertIsNone(r['area']['class']);self.assertFalse(r['area']['class_verified']);self.assertEqual(r['area']['neighborhood'],'South Side Flats')

if __name__=='__main__':unittest.main()
