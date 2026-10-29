import copy,unittest
from pathlib import Path
from unittest.mock import patch
import report_source_audit as audit

class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.p={'id':'qa-1','address':'18 Main St','city':'Pittsburgh','state':'PA','zip':'15203'}
        self.prior={'property_id':'qa-1','property':self.p,'generated_at':'2026-10-22','sources':{'rent':{'observed_at':'2026-10-22','estimate':{'value':1820}}}}
        self.analysis={'closed_sales':{'status':'available','subject_location':{'neighborhood':'test'}},
            'financing':{'products':[{}]},'rehab':{'status':'partial','items':[{}],'pricebook':{'status':'current'}}}
    def run_audit(self,prior):
        saved={}
        with patch.object(audit.store,'profile',return_value={'county_record':{'status':'matched'}}),patch.object(audit.report_enrichment,'collect',return_value=self.analysis) as collect,patch.object(audit.store,'read',return_value=prior),patch.object(audit.store,'write',side_effect=lambda path,value:saved.__setitem__(str(path),copy.deepcopy(value))):
            result=audit.collect(self.p,{'property_id':'qa-1'},Path('offline'))
        return result,saved,collect
    def test_existing_rent_receipt_and_original_date_are_preserved(self):
        result,saved,collect=self.run_audit(self.prior)
        target=next(v for k,v in saved.items() if 'expanded_reports' in k)
        self.assertEqual(target['sources'],self.prior['sources']);self.assertEqual(target['generated_at'],'2026-10-22')
        self.assertTrue(result['sources_verified']);self.assertEqual(result['rentcast_calls'],0)
        self.assertEqual(collect.call_args.kwargs['rehab_result'],{'property_id':'qa-1'})
    def test_foreign_receipt_is_never_updated(self):
        result,saved,_=self.run_audit({**self.prior,'property':{**self.p,'address':'22 Other St'}})
        self.assertFalse(result['existing_rent_receipt_preserved'])
        self.assertTrue(all('expanded_reports' not in k for k in saved))
    def test_interior_assessments_are_photo_analysis_even_without_repair_findings(self):
        self.analysis['rehab'].update(items=[],interior_assessments=[{'scene':'kitchen','condition':'no_obvious_damage_in_view'}])
        result,_,_=self.run_audit(self.prior)
        self.assertTrue(result['checks']['photo_analysis'])
    def test_verified_connections_do_not_claim_complete_budget(self):
        result,_,_=self.run_audit(self.prior)
        self.assertIn('renovation_scope_and_quantities',result['remaining_data'])
        self.assertIn('after_repair_valuation',result['remaining_data'])
    def test_exterior_only_assessment_is_not_mistaken_for_missing_analysis(self):
        self.analysis['rehab'].update(items=[],interior_assessments=[],exterior_assessments=[{'component':'roof_surface','condition':'not_assessable'}])
        result,_,_=self.run_audit(self.prior)
        self.assertTrue(result['checks']['photo_analysis'])

if __name__=='__main__':unittest.main()
