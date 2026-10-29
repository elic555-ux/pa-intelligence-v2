import copy,os,unittest
from pathlib import Path
from unittest.mock import patch
import property_bundle_quality_trial as trial

class BundleTrialTests(unittest.TestCase):
    def setUp(self):
        self.trigger={'trial_id':'school-test','case_id':trial.CASE_ID,'manifest_sha256':trial.MANIFEST_SHA,'model':'gemini-3.5-flash-lite','free_tier_confirmed':True}
        self.subject={'id':trial.PROPERTY_ID,'address':'2414 School St','city':'Pittsburgh','state':'PA','zip':'15235'}
        self.fixture,self.prepared=trial.load_fixture()
        self.result={'status':'partial','complete':False,'total_low':None,'total_high':None,'photo_count':7,
            'photos':[{'sha256':p['sha256']} for p in self.fixture['photos']],
            'photo_coverage':[{'photo_id':'photo_'+str(i+1)} for i in range(7)]}
    def test_pinned_real_fixture_and_changed_bytes(self):
        self.assertEqual(len(self.prepared),7)
        with patch.object(Path,'read_bytes',return_value=b'changed'):
            with self.assertRaisesRegex(ValueError,'fixture_manifest_changed'):trial.load_fixture()
        original=Path.read_bytes
        def read(path):return b'changed' if path.name.endswith('.jpg') else original(path)
        with patch.object(Path,'read_bytes',read):
            with self.assertRaisesRegex(ValueError,'fixture_image_changed'):trial.load_fixture()
    def test_guards_stop_before_any_intake_or_provider_access(self):
        with patch.object(trial,'load_fixture',side_effect=AssertionError('intake called')),patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true'}):
            for change in [{'free_tier_confirmed':False},{'manifest_sha256':'wrong'},{'model':'paid-model'},{'trial_id':'../escape'}]:
                with self.assertRaises(ValueError):trial.run(Path('offline'),dict(self.trigger,**change))
    def test_subject_mismatch_cannot_call_provider(self):
        with patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true'}),patch.object(trial.store,'read',return_value=[dict(self.subject,address='another home')]),patch.object(trial.gemini,'collect',side_effect=AssertionError('provider called')):
            with self.assertRaisesRegex(ValueError,'property_identity_mismatch'):trial.run(Path('offline'),self.trigger)
    def test_all_seven_real_bytes_no_expected_labels_and_no_live_write(self):
        def collect(subject,root,before_send=None):
            inputs=trial.gemini.verified_inputs(subject,root.parent)
            self.assertNotIn('expected',str(inputs));self.assertNotIn('thorough_repair',str(inputs))
            self.assertEqual([trial.gemini.fetch_image(p)[2] for p in inputs['photos']],[p['sha256'] for p in self.fixture['photos']])
            return self.result
        with patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true'}),patch.object(trial.store,'read',return_value=[self.subject]),patch.object(trial.gemini,'collect',side_effect=collect),patch.object(trial.store,'write') as write:
            summary=trial.run(Path('offline'),self.trigger)
        self.assertTrue(summary['evaluation']['contract_passed']);self.assertEqual(summary['evaluation']['visual_quality'],'pending_independent_review')
        self.assertNotIn('expanded_reports',str(write.call_args));self.assertFalse(summary['live_report_updated'])
    def test_missing_or_wrong_bytes_and_invented_total_fail_contract(self):
        for change in [{'photo_count':6},{'photos':list(reversed(self.result['photos']))},{'photo_coverage':[]},{'total_high':1}]:
            self.assertFalse(trial.evaluate(dict(self.result,**change),self.fixture)['contract_passed'])
    def test_outage_is_not_a_visual_quality_result(self):
        r=trial.evaluate({'status':'source_unavailable','reason':'provider_http_503'},self.fixture)
        self.assertEqual(r['visual_quality'],'not_evaluated');self.assertEqual(r['checks'],{})
    def test_branch_code_checkout_cannot_push_to_main(self):
        with patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true'}):
            with self.assertRaisesRegex(ValueError,'separate_data_checkout_required'):trial.run(Path(trial.__file__).parent,self.trigger,persist=True)

if __name__=='__main__':unittest.main()
