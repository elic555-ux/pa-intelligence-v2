import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import photo_quality_trial as trial


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.result={'status':'partial','complete':False,'total_low':None,'total_high':None,
            'photo_count':1,'photos':[{'sha256':trial.IMAGE_SHA}],
            'photo_coverage':[{'photo_id':'photo_1'}],'interior_assessments':[],
            'exterior_assessments':[{'component':c,'condition':'visible_damage' if c=='exterior_walls' else 'not_assessable'} for c in trial.gemini.EXTERIOR_COMPONENTS]}
        self.trigger={'trial_id':'fox-way-front-test','property_id':trial.PROPERTY_ID,
            'image_sha256':trial.IMAGE_SHA,'free_tier_confirmed':True}

    def test_golden_categories_and_unknown_roof_are_required(self):
        self.assertTrue(trial.evaluate(self.result)['passed'])
        for component,condition in [('exterior_walls','cosmetic_optional'),('roof_surface','no_obvious_damage_in_view')]:
            result=copy.deepcopy(self.result)
            next(r for r in result['exterior_assessments'] if r['component']==component)['condition']=condition
            self.assertFalse(trial.evaluate(result)['passed'])

    def test_wrong_bytes_invented_rooms_and_budget_cannot_pass(self):
        for change in [{'photos':[{'sha256':'wrong'}]},{'interior_assessments':[{}]},{'total_high':0}]:
            self.assertFalse(trial.evaluate(dict(self.result,**change))['passed'])

    def test_unapproved_trigger_stops_before_any_source_access(self):
        with patch.object(trial.store,'read',side_effect=AssertionError('source access')):
            for change in [{'free_tier_confirmed':False},{'property_id':'other'},{'image_sha256':'other'}]:
                with self.assertRaises(ValueError):trial.run(Path('offline'),dict(self.trigger,**change))

    def test_real_verified_bytes_are_passed_without_expected_answer_and_no_live_report_write(self):
        prop={'id':trial.PROPERTY_ID}
        inputs={'property_id':trial.PROPERTY_ID,'identity':['verified'],'photos':[{'url':trial.IMAGE_URL,'source_url':'https://example.com/verified-listing'}],'facts':{}}
        with patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true'}),\
             patch.object(trial.store,'read',return_value=[prop]),\
             patch.object(trial.gemini,'verified_inputs',return_value=inputs),\
             patch.object(trial.gemini,'fetch_image',return_value=('image/jpeg',b'real-verified-test-bytes',trial.IMAGE_SHA)) as fetched,\
             patch.object(trial.gemini,'collect',return_value=self.result) as collect,\
             patch.object(trial.store,'write') as write:
            summary=trial.run(Path('offline'),self.trigger)
        self.assertTrue(summary['evaluation']['passed']);self.assertFalse(summary['live_report_updated'])
        self.assertEqual(collect.call_count,1)
        self.assertEqual(fetched.call_args.kwargs,{'read_timeout':60})
        self.assertNotIn('facade_damage_identified',str(collect.call_args))
        self.assertNotIn('expanded_reports',str(write.call_args))

    def test_unverified_source_and_failed_image_never_call_provider(self):
        for missing in [True,False]:
            with patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true'}),\
                 patch.object(trial.store,'read',return_value=[{'id':trial.PROPERTY_ID}]),\
                 patch.object(trial.gemini,'verified_inputs',return_value={'photos':[] if missing else [{'url':trial.IMAGE_URL}]}),\
                 patch.object(trial.gemini,'fetch_image',side_effect=ValueError('image_changed_since_verification')),\
                 patch.object(trial.gemini,'collect',side_effect=AssertionError('provider called')):
                with self.assertRaises(ValueError):trial.run(Path('offline'),self.trigger)


if __name__=='__main__':unittest.main()
