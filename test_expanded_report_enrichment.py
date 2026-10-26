"""Report execution must persist enrichment without repeating provider calls."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import expanded_report as report

class ExecutionIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.p={'id':'qa-1','address':'18 Main St','city':'Pittsburgh','state':'PA','zip':'15203'}
        self.root=Path('offline/COMPS_REPORTS')
        self.job={'status':'prepared','property':self.p,'request_id':'test-request','slots':[], 'mode':'profile'}
        self.data={str(self.root/'expanded_requests/123.json'):copy.deepcopy(self.job)}
        self.analysis={'status':'partial','property_id':'qa-1','rehab':{'status':'partial'}}
        self.patches=[patch.object(report,'ROOT',self.root),
            patch.object(report,'read',side_effect=lambda p:copy.deepcopy(self.data.get(str(p)))),
            patch.object(report,'write',side_effect=lambda p,v:self.data.__setitem__(str(p),copy.deepcopy(v))),
            patch.object(report.store,'profile',return_value={'county_record':{'status':'matched'}}),
            patch.object(report.report_enrichment,'collect',return_value=self.analysis),
            patch.object(report.requests,'get',side_effect=AssertionError('No RentCast call expected')),
            patch.dict(os.environ,{'GITHUB_ACTIONS':'true','FREE_COUNTY_LOOKUPS_ENABLED':'true'})]
        self.started=[p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)
    def test_partial_analysis_is_saved_to_property_and_matching_request_receipt(self):
        result=report.execute('123')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['report_analysis'],self.analysis)
        self.assertEqual(result['calls_sent'],0)
        self.assertEqual(self.data[str(self.root/'expanded_receipts/test-request.json')],result)
        self.assertEqual(self.data[str(self.root/'expanded_reports'/(report.property_key('qa-1')+'.json'))],result)
        self.assertEqual(self.data[str(self.root/'expanded_requests/123.json')]['status'],'completed')
    def test_actions_passes_durable_reservation_callback_to_ai_collector(self):
        with patch.object(report.report_provider_reservation,'persist_gemini_reservation') as persist:
            report.execute('123')
            callback=self.started[4].call_args.kwargs['before_gemini_send']
            self.assertTrue(callable(callback));persist.assert_not_called()
            callback();persist.assert_called_once_with(self.root.parent)
    def test_completed_request_replay_cannot_repeat_enrichment_or_api(self):
        report.execute('123');self.started[4].reset_mock()
        result=report.execute('123')
        self.assertEqual(result['status'],'completed');self.started[4].assert_not_called()

if __name__=='__main__':unittest.main()

