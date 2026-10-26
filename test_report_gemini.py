import copy,json,os,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import requests
import report_gemini as g

class GeminiReportTests(unittest.TestCase):
    def setUp(self):
        self.p={'id':'qa-1','address':'18 Main St','city':'Pittsburgh','state':'PA','zip':'15203'}
        self.photo={'id':'photo_1','url':'https://ssl.cdn-redfin.com/photo/test.jpg',
                    'source_url':'https://www.redfin.com/PA/Pittsburgh/18-Main-St-15203/home/123',
                    'sha256':'a'*64}
        self.data={'summary':'ממצא מתמונת פנים','limitations':['הגג אינו נראה'],
                   'photo_coverage':[{'photo_id':'photo_1','scene':'living_dining'}],
                   'items':[{'name':'רצפה','group':'interior','status':'observed',
                    'finding':'בלאי נראה לעין','scope':'בדיקת הרצפה לפני קביעת היקף העבודה',
                    'rationale':'פני הרצפה נראים בתמונה','photo_ids':['photo_1'],'fact_keys':[]}]}
        self.root=Path('offline-gemini-test/COMPS_REPORTS');self.saved={}
        for guard in [patch.object(g.store,'read',side_effect=lambda path:copy.deepcopy(self.saved.get(str(path)))),
                      patch.object(g.store,'write',side_effect=lambda path,value:self.saved.__setitem__(str(path),copy.deepcopy(value)))]:
            guard.start();self.addCleanup(guard.stop)
        self.env=patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'true','GEMINI_API_KEY':'unit-test-only-key'})
        self.env.start();self.addCleanup(self.env.stop)
    def fake_response(self,status=200,data=None):
        r=MagicMock();r.__enter__.return_value=r;r.status_code=status
        r.iter_content.return_value=[json.dumps({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(data or self.data)}]}}]}).encode()]
        return r
    def run_collect(self,response=None,error=None,before_send=None):
        inputs={'property_id':'qa-1','identity':['18 MAIN ST','PITTSBURGH','PA','15203'],'photos':[self.photo],'facts':{}}
        with patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',return_value=('image/jpeg',b'test-image','a'*64)),patch.object(g.requests,'post',return_value=response or self.fake_response(),side_effect=error) as post:
            result=g.collect(self.p,self.root,before_send=before_send)
            return result,post
    def test_disabled_guard_never_reads_images_or_contacts_api(self):
        with patch.dict(os.environ,{'GEMINI_REPORT_FREE_TIER_ENABLED':'false'}),patch.object(g,'verified_inputs',side_effect=AssertionError('unexpected network')):
            self.assertEqual(g.collect(self.p,self.root)['status'],'provider_not_configured')
    def test_missing_key_never_contacts_any_source(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':''}),patch.object(g,'verified_inputs',side_effect=AssertionError('unexpected source')):
            self.assertEqual(g.collect(self.p,self.root)['reason'],'gemini_key_not_configured')
    def test_findings_never_create_a_budget_or_prices(self):
        r=g.normalize(self.data,self.p,[self.photo],{})
        self.assertEqual(r['status'],'partial');self.assertFalse(r['complete'])
        self.assertIsNone(r['total_high']);self.assertIsNone(r['items'][0]['cost_high'])
    def test_listing_fact_text_is_grounded_and_never_claims_work_unnecessary(self):
        self.data['summary']='invented provider summary'
        row=self.data['items'][0];row.update(status='listing_reported',photo_ids=[],fact_keys=['heating'],finding='invented system',scope='no work needed')
        r=g.normalize(self.data,self.p,[self.photo],{'heating':{'value':'Forced Air'}})
        self.assertEqual(r['items'][0]['finding'],'חימום: Forced Air')
        self.assertNotIn('no work needed',r['items'][0]['scope']);self.assertNotIn('invented',r['summary'])
    def test_rejects_hallucinated_photo_reference(self):
        self.data['items'][0]['photo_ids']=['photo_99']
        with self.assertRaises(ValueError):g.normalize(self.data,self.p,[self.photo],{})
    def test_observed_requires_photo_and_listing_requires_fact(self):
        self.data['items'][0]['photo_ids']=[]
        with self.assertRaises(ValueError):g.normalize(self.data,self.p,[self.photo],{})
        self.data['items'][0]['status']='listing_reported'
        with self.assertRaises(ValueError):g.normalize(self.data,self.p,[self.photo],{})
    def test_model_cannot_supply_dollar_costs(self):
        self.data['items'][0]['cost_high']=100
        with self.assertRaises(ValueError):g.normalize(self.data,self.p,[self.photo],{})
    def test_api_uses_fixed_endpoint_header_key_no_tools_and_is_cached(self):
        r,post=self.run_collect()
        self.assertEqual(r['status'],'partial');self.assertEqual(post.call_count,1)
        args,kw=post.call_args
        self.assertEqual(g.MODEL,'gemini-3.5-flash-lite')
        self.assertEqual(args[0],g.API);self.assertNotIn('key=',args[0]);self.assertFalse(kw['allow_redirects'])
        self.assertEqual(kw['timeout'],(5,180));self.assertEqual(r['input_photo_count'],1)
        self.assertNotIn('tools',kw['json']);self.assertNotIn('unit-test-only-key',json.dumps(kw['json']))
        second,next_post=self.run_collect()
        self.assertEqual(next_post.call_count,0);self.assertEqual(second['signature'],r['signature'])
        self.assertNotIn('unit-test-only-key',json.dumps(self.saved))
    def test_quota_error_no_retry_or_alternate_model(self):
        r,post=self.run_collect(self.fake_response(status=429))
        self.assertEqual(r['status'],'quota_exhausted');self.assertEqual(post.call_count,1)
        again,next_post=self.run_collect();self.assertEqual(again['status'],'quota_exhausted');self.assertEqual(next_post.call_count,0)
    def test_transient_503_requires_cooldown_and_another_explicit_run(self):
        result,post=self.run_collect(self.fake_response(status=503))
        self.assertEqual(result['http_status'],503);self.assertEqual(post.call_count,1)
        again,next_post=self.run_collect()
        self.assertEqual(again['reason'],'provider_http_503');self.assertEqual(next_post.call_count,0)
        with patch.object(g.store,'age_days',return_value=16/(24*60)):
            result,post=self.run_collect()
        self.assertEqual(post.call_count,1);self.assertEqual(result['status'],'partial')
        self.assertEqual(g.store.read(self.root/'gemini_findings/usage.json')['reserved_calls'],2)
    def test_503_cooldown_does_not_shorten_quota_or_unknown_failure_cache(self):
        self.assertEqual(g.cache_days({'status':'quota_exhausted','reason':'provider_http_429','http_status':429}),1)
        self.assertEqual(g.cache_days({'status':'source_unavailable','reason':'provider_http_503'}),1)
        self.assertEqual(g.cache_days({'status':'partial'}),30)
    def test_timeout_is_reserved_and_not_automatically_retried(self):
        r,post=self.run_collect(error=requests.Timeout())
        self.assertEqual(r['status'],'source_unavailable');self.assertEqual(post.call_count,1)
        self.assertEqual(r['reason'],'provider_timeout');self.assertEqual(r['failure_stage'],'transport')
        self.assertIsNone(r['http_status']);self.assertEqual(r['input_photo_count'],1)
        again,next_post=self.run_collect();self.assertEqual(next_post.call_count,0)
        self.assertEqual(g.store.read(self.root/'gemini_findings/usage.json')['reserved_calls'],1)
    def test_local_daily_cap_blocks_api(self):
        g.store.write(self.root/'gemini_findings/usage.json',{'date':g.store.stamp()[:10],'reserved_calls':5})
        r,post=self.run_collect();self.assertEqual(r['status'],'quota_exhausted');self.assertEqual(post.call_count,0)
    def test_failed_reservation_persistence_prevents_model_request(self):
        def fail():raise OSError('repository write failed')
        r,post=self.run_collect(before_send=fail)
        self.assertEqual(r['status'],'source_unavailable');self.assertEqual(post.call_count,0)
        self.assertEqual(r['reason'],'attempt_reservation_not_persisted')
        self.assertEqual(g.store.read(self.root/'gemini_findings/usage.json')['reserved_calls'],1)
    def test_invalid_or_truncated_response_rejected(self):
        response=self.fake_response();response.iter_content.return_value=[b'{"candidates":[]}']
        r,post=self.run_collect(response);self.assertEqual(r['status'],'source_unavailable')
        self.assertEqual(r['http_status'],200);self.assertEqual(r['error_code'],'model_response_incomplete')
    def test_invalid_json_is_distinct_from_timeout_or_key_error(self):
        response=self.fake_response();response.iter_content.return_value=[b'not-json']
        r,post=self.run_collect(response)
        self.assertEqual(r['reason'],'provider_json_invalid');self.assertEqual(r['http_status'],200)
    def test_unexpected_exception_text_is_not_saved(self):
        with patch.object(g,'normalize',side_effect=ValueError('unit-test-only-key')):
            r,post=self.run_collect()
        self.assertEqual(r['error_code'],'response_validation_failed')
        self.assertNotIn('unit-test-only-key',json.dumps(self.saved))
    def test_provider_error_keeps_category_but_discards_message_and_key(self):
        response=self.fake_response(status=403)
        response.iter_content.return_value=[json.dumps({'error':{'status':'PERMISSION_DENIED','message':'API key not valid: unit-test-only-key'}}).encode()]
        result,post=self.run_collect(response)
        self.assertEqual(result['provider_status'],'PERMISSION_DENIED');self.assertEqual(result['provider_hint'],'api_key_invalid')
        self.assertNotIn('unit-test-only-key',json.dumps(self.saved))
    def test_no_photos_no_api(self):
        with patch.object(g,'verified_inputs',return_value={'photos':[]}),patch.object(g.requests,'post',side_effect=AssertionError('API called')):
            self.assertEqual(g.collect(self.p,self.root)['status'],'photos_unavailable')
    def test_untrusted_image_url_is_never_fetched(self):
        for url in ['http://ssl.cdn-redfin.com/photo/a.jpg','https://localhost/image.jpg','https://ssl.cdn-redfin.com@evil.example/a.jpg','https://ssl.cdn-redfin.com/photo/a.jpg?token=abc']:
            with patch.object(g.requests,'get',side_effect=AssertionError('source called')):
                with self.assertRaises(ValueError):g.fetch_image({'url':url})

    def test_coverage_is_required_for_every_supplied_photo(self):
        for coverage in [[],[{'photo_id':'photo_99','scene':'kitchen'}],[{'photo_id':'photo_1','scene':'invented'}]]:
            with self.subTest(coverage=coverage),self.assertRaises(ValueError):g.normalize(dict(self.data,photo_coverage=coverage),self.p,[self.photo],{})
        two=[self.photo,dict(self.photo,id='photo_2')]
        with self.assertRaises(ValueError):g.normalize(dict(self.data,photo_coverage=[{'photo_id':'photo_1','scene':'kitchen'}]*2),self.p,two,{})
    def test_all_22_source_photos_including_later_interiors_are_analyzed_once(self):
        rows=[dict(self.photo,url='https://ssl.cdn-redfin.com/photo/'+str(i)+'.jpg') for i in range(22)]
        data=dict(self.data,photo_coverage=[{'photo_id':'photo_'+str(i+1),'scene':'exterior' if i<2 else 'kitchen'} for i in range(22)])
        def image(row):
            i=int(row['url'].split('/')[-1].split('.')[0]);return 'image/jpeg',b'image',format(i,'064x')
        inputs={'property_id':'qa-1','identity':['18 MAIN ST','PITTSBURGH','PA','15203'],'photos':rows,'facts':{}}
        with patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',side_effect=image) as fetched,patch.object(g.requests,'post',return_value=self.fake_response(data=data)) as post:
            result=g.collect(self.p,self.root)
        self.assertEqual(result['photo_count'],22);self.assertEqual(len(result['photo_coverage']),22)
        self.assertEqual(fetched.call_count,22);self.assertEqual(post.call_count,1)
        self.assertEqual(result['photo_intake']['prepared_photos'],22)
        self.assertTrue(result['photos'][-1]['url'].endswith('/21.jpg'))
        self.assertEqual(post.call_args.kwargs['json']['generationConfig']['maxOutputTokens'],8192)
        self.assertIsNone(result['total_high'])
    def test_maximum60_and_spread_order_cover_tail_without_unbounded_requests(self):
        for count in [0,1,2,22,60]:self.assertEqual(sorted(g.spread_order(count)),list(range(count)))
        self.assertEqual(g.spread_order(60)[:2],[0,59])
        rows=[dict(self.photo,url='https://ssl.cdn-redfin.com/photo/'+str(i)+'.jpg') for i in range(65)]
        inputs={'property_id':'qa-1','identity':[],'photos':rows,'facts':{}}
        data=dict(self.data,photo_coverage=[{'photo_id':'photo_'+str(i+1),'scene':'unclear'} for i in range(60)])
        with patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',side_effect=lambda row:('image/jpeg',b'image',row['url'])) as fetched,patch.object(g.requests,'post',return_value=self.fake_response(data=data)):
            result=g.collect(self.p,self.root)
        self.assertEqual(result['photo_count'],60);self.assertEqual(fetched.call_count,60)
        self.assertEqual(result['photo_intake']['available_links'],65)
    def test_existing_byte_cap_keeps_partial_intake_explicit(self):
        rows=[dict(self.photo,url=self.photo['url']+str(i)) for i in range(4)]
        inputs={'property_id':'qa-1','identity':[],'photos':rows,'facts':{}}
        with patch.object(g,'MAX_TOTAL_IMAGE_BYTES',5),patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',side_effect=lambda row:('image/jpeg',b'abc',row['url'])),patch.object(g.requests,'post',return_value=self.fake_response()):
            result=g.collect(self.p,self.root)
        self.assertEqual(result['photo_count'],1);self.assertEqual(result['photo_intake']['excluded_by_byte_limit'],3)
        self.assertEqual(g.MAX_TOTAL_IMAGE_BYTES,12_000_000);self.assertEqual(g.MAX_DAILY_CALLS,5)
    def test_fetch_deadline_does_not_claim_unattempted_photos_were_analyzed(self):
        inputs={'property_id':'qa-1','identity':[],'photos':[self.photo]*20,'facts':{}}
        with patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',return_value=('image/jpeg',b'image','a'*64)) as fetched,patch.object(g.time,'monotonic',side_effect=[0,0,91,100,101]),patch.object(g.requests,'post',return_value=self.fake_response()):
            result=g.collect(self.p,self.root)
        self.assertEqual(fetched.call_count,1);self.assertEqual(result['photo_count'],1);self.assertEqual(result['photo_intake']['not_attempted'],19)

if __name__=='__main__':unittest.main()
