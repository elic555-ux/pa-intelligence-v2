import copy,json,os,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import report_gemini as g
import report_surface_observer as observer

class SurfaceObserverTests(unittest.TestCase):
    def setUp(self):
        self.p={'id':'qa-surface'}
        self.photo={'id':'photo_1','url':'https://example.com/real.jpg','source_url':'https://example.com/listing','sha256':'a'*64}
        self.data={'photos':[{'photo_id':'photo_1','scene':'garage','observations':[
            {'component':'קורה גלויה','material':'מתכת','appearance':'כתמים כתומים על פני הקורה','alteration':'corrosion','limitation':'החוזק אינו ידוע'},
            {'component':'קיר','material':'בלוקים צבועים','appearance':'צבע בהיר לאורך הקיר','alteration':'no_visible_change','limitation':''}]}]}
    def test_only_observations_not_repair_or_cost_are_generated(self):
        result=observer.normalize(self.data,self.p,[self.photo],{})
        self.assertEqual(result['analysis_kind'],'surface_observation_only')
        self.assertEqual(result['version'],observer.VERSION)
        self.assertFalse(result['repair_scope_generated']);self.assertIsNone(result['total_high'])
        self.assertEqual(result['photo_coverage'][0]['observations'][0]['alteration'],'corrosion')
    def test_unknown_duplicate_or_missing_photo_and_invented_cost_fail(self):
        for mutate in [lambda d:d['photos'][0].update(photo_id='other'),
                       lambda d:d['photos'].append(copy.deepcopy(d['photos'][0])),
                       lambda d:d.update(photos=[]),
                       lambda d:d.update(cost=100),
                       lambda d:d['photos'][0]['observations'][0].update(cost=1)]:
            d=copy.deepcopy(self.data);mutate(d)
            with self.assertRaises(ValueError):observer.normalize(d,self.p,[self.photo],{})
    def test_operational_assertion_cannot_pass_as_appearance(self):
        d=copy.deepcopy(self.data);d['photos'][0]['observations'][0]['appearance']='המכשיר תקין'
        with self.assertRaisesRegex(ValueError,'invalid_model_text'):observer.normalize(d,self.p,[self.photo],{})
    def test_observer_reuses_real_request_reservation_and_cache_path(self):
        saved={};response=MagicMock();response.__enter__.return_value=response;response.status_code=200
        response.iter_content.return_value=[json.dumps({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(self.data)}]}}]}).encode()]
        inputs={'property_id':'qa-surface','identity':['test'],'facts':{},'photos':[self.photo]}
        root=Path('offline-surface/COMPS_REPORTS')
        with patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true','GEMINI_API_KEY':'unit-test-only-key'}),\
             patch.object(g.store,'read',side_effect=lambda p:copy.deepcopy(saved.get(str(p)))),\
             patch.object(g.store,'write',side_effect=lambda p,v:saved.__setitem__(str(p),copy.deepcopy(v))),\
             patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',return_value=('image/jpeg',b'unit-test-bytes','a'*64)),\
             patch.object(g.requests,'post',return_value=response) as post:
            result=g.collect(self.p,root,analysis_adapter='surface_observer')
            cached=g.collect(self.p,root,analysis_adapter='surface_observer')
        self.assertEqual(post.call_count,1)
        self.assertEqual(saved[str(root/'gemini_findings/usage.json')]['reserved_calls'],1)
        self.assertEqual(result['analysis_kind'],'surface_observation_only');self.assertEqual(cached['signature'],result['signature'])
        payload=post.call_args.kwargs['json']
        self.assertEqual(payload['generationConfig']['responseJsonSchema'],observer.SCHEMA)
        self.assertNotIn('interior_assessment_slots',payload['contents'][0]['parts'][0]['text'])
        self.assertNotIn('unit-test-only-key',json.dumps(result))
    def test_unknown_adapter_stops_without_source_or_provider_access(self):
        with patch.object(g,'verified_inputs',side_effect=AssertionError('source called')),patch.object(g.requests,'post',side_effect=AssertionError('provider called')):
            with self.assertRaisesRegex(ValueError,'unsupported_analysis_adapter'):g.collect(self.p,analysis_adapter='other')

if __name__=='__main__':unittest.main()
