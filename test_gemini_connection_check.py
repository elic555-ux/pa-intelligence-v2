import unittest
from pathlib import Path
from unittest.mock import patch
import gemini_connection_check as c

class GeminiConnectionCheckTests(unittest.TestCase):
    def setUp(self):
        self.p={'id':'PA-MLS-1779799','address':'1822 Fox Way','city':'Pittsburgh','state':'PA','zip':'15203'}
    def test_success_is_photo_findings_and_not_a_completed_budget(self):
        result={'status':'partial','reason':'pricing_sources_not_connected','photo_count':3,'items':[{'name':'test'}],
            'photo_coverage':[{}, {}, {}],'exterior_assessments':[{}]*6}
        with patch.object(c.store,'read',return_value=[self.p]),patch.object(c.store,'write') as write,patch.object(c.gemini,'collect',return_value=result) as collect:
            summary=c.check(self.p['id'],Path('offline'))
        self.assertTrue(summary['connection_verified']);self.assertFalse(summary['renovation_budget_complete'])
        self.assertEqual(summary['rentcast_calls'],0);self.assertEqual(collect.call_count,1)
        self.assertEqual(summary['assessed_photo_count'],3);self.assertEqual(summary['exterior_assessments_count'],6)
        self.assertEqual(write.call_args.args[0],Path('offline/COMPS_REPORTS/gemini_connection_check.json'))
    def test_missing_key_or_quota_stop_is_not_success(self):
        for status in ['provider_not_configured','quota_exhausted','source_unavailable','photos_unavailable']:
            with patch.object(c.store,'read',return_value=[self.p]),patch.object(c.store,'write'),patch.object(c.gemini,'collect',return_value={'status':status,'reason':'test'}):
                self.assertFalse(c.check(self.p['id'])['connection_verified'])
    def test_missing_or_duplicate_identity_never_contacts_provider(self):
        for rows in [[],[self.p,self.p],None]:
            with patch.object(c.store,'read',return_value=rows),patch.object(c.gemini,'collect',side_effect=AssertionError('API called')):
                with self.assertRaises(ValueError):c.check(self.p['id'])
    def test_property_id_cannot_be_a_path_or_shell_command(self):
        with patch.object(c.store,'read',side_effect=AssertionError('file access')):
            for value in ['../../private','property; echo key','a b','']:
                with self.assertRaises(ValueError):c.check(value)

if __name__=='__main__':unittest.main()
