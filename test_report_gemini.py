import copy,json,os,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import requests
import report_gemini as g

def interior_rows(coverage):
    return [{'scene':scene,'component':component,'condition':'not_assessable',
        'finding':'הרכיב אינו ברור בתמונה','scope':'בדיקה לפני קביעת עבודה',
        'rationale':'פרטי הרכיב אינם נראים בבירור בזווית המצולמת',
        'photo_ids':[next(r['photo_id'] for r in coverage if r['scene']==scene)]}
        for scene in g.COMPONENTS if any(r['scene']==scene for r in coverage)
        for component in g.COMPONENTS[scene]]

def exterior_rows(coverage):
    refs=[r['photo_id'] for r in coverage if r['scene']=='exterior']
    return {component:{'condition':'not_assessable',
        'finding':'הרכיב אינו ברור בצילום החוץ' if refs else '',
        'scope':'בדיקה לפני קביעת עבודה' if refs else '',
        'rationale':'אין די פרטים בזווית הצילום' if refs else '',
        'photo_ids':refs[:1]} for component in g.EXTERIOR_COMPONENTS}

class GeminiReportTests(unittest.TestCase):
    def setUp(self):
        self.p={'id':'qa-1','address':'18 Main St','city':'Pittsburgh','state':'PA','zip':'15203'}
        self.photo={'id':'photo_1','url':'https://ssl.cdn-redfin.com/photo/test.jpg',
                    'source_url':'https://www.redfin.com/PA/Pittsburgh/18-Main-St-15203/home/123',
                    'sha256':'a'*64}
        self.data={'summary':'ממצא מתמונת פנים','limitations':['הגג אינו נראה'],
                   'photo_coverage':[{'photo_id':'photo_1','scene':'living_dining','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'}],
                   'items':[{'name':'רצפה','group':'interior','status':'observed','condition':'visible_damage',
                    'finding':'בלאי נראה לעין','scope':'בדיקת הרצפה לפני קביעת היקף העבודה',
                    'rationale':'פני הרצפה נראים בתמונה','photo_ids':['photo_1'],'fact_keys':[]}]}
        self.data['interior_assessments']=interior_rows(self.data['photo_coverage'])
        self.data['exterior_assessments']=exterior_rows(self.data['photo_coverage'])
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
        g.store.write(self.root/'gemini_findings/usage.json',{'date':g.store.stamp()[:10],'reserved_calls':20})
        r,post=self.run_collect();self.assertEqual(r['status'],'quota_exhausted');self.assertEqual(post.call_count,0)
    def test_owner_approved_twenty_call_cap_allows_a_sixth_attempt(self):
        g.store.write(self.root/'gemini_findings/usage.json',{'date':g.store.stamp()[:10],'reserved_calls':5})
        r,post=self.run_collect()
        self.assertEqual(post.call_count,1);self.assertEqual(r['status'],'partial')
        self.assertEqual(g.store.read(self.root/'gemini_findings/usage.json')['reserved_calls'],6)
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
        for coverage in [[],[{'photo_id':'photo_99','scene':'kitchen','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'}],[{'photo_id':'photo_1','scene':'invented','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'}]]:
            with self.subTest(coverage=coverage),self.assertRaises(ValueError):g.normalize(dict(self.data,photo_coverage=coverage),self.p,[self.photo],{})
        two=[self.photo,dict(self.photo,id='photo_2')]
        with self.assertRaises(ValueError):g.normalize(dict(self.data,photo_coverage=[{'photo_id':'photo_1','scene':'kitchen','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'}]*2),self.p,two,{})
    def test_every_photo_needs_a_condition_and_nonempty_finding(self):
        for change in [{'condition':'fully_working'},{'finding':''},{'finding':'   '},{'cost':100}]:
            data=copy.deepcopy(self.data);data['photo_coverage'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                g.normalize(data,self.p,[self.photo],{})
        for key in ['condition','finding']:
            data=copy.deepcopy(self.data);del data['photo_coverage'][0][key]
            with self.subTest(missing=key),self.assertRaises(ValueError):
                g.normalize(data,self.p,[self.photo],{})
    def test_visual_description_survives_conservative_condition_wording(self):
        data=copy.deepcopy(self.data)
        description='רצפת עץ וקירות צבועים נראים באזור החלון'
        data['photo_coverage'][0].update(condition='no_obvious_damage_in_view',finding=description)
        data['interior_assessments'][0].update(condition='no_obvious_damage_in_view',finding=description)
        result=g.normalize(data,self.p,[self.photo],{})
        for row in [result['photo_coverage'][0],result['interior_assessments'][0]]:
            self.assertEqual(row['visual_observation'],description)
            self.assertIn('לא נבדקה',row['finding'])
    def test_rejected_text_diagnostics_name_only_known_paths_and_lengths(self):
        data=copy.deepcopy(self.data)
        data['photo_coverage'][0]['finding']=''
        data['items'][0]['scope']='unit-test-only-key'*200
        diagnostics=g.text_diagnostics(data)
        self.assertIn({'path':'photo_coverage.0.finding','problem':'empty','length':0},diagnostics)
        self.assertNotIn('unit-test-only-key',json.dumps(diagnostics))
        result,_=self.run_collect(self.fake_response(data=data))
        self.assertEqual(result['error_code'],'invalid_model_text')
        self.assertEqual(result['text_validation_diagnostics'],diagnostics)
    def test_operational_claims_are_not_retained_as_visual_description(self):
        for claim in ['fully operational and safe','המערכת תקינה ובטוחה לשימוש','leak-free']:
            data=copy.deepcopy(self.data)
            data['photo_coverage'][0].update(condition='no_obvious_damage_in_view',finding=claim)
            row=g.normalize(data,self.p,[self.photo],{})['photo_coverage'][0]
            self.assertNotIn('visual_observation',row)
            self.assertNotIn(claim,row['finding'])
    def test_exterior_requires_all_six_components_with_exterior_evidence(self):
        data=copy.deepcopy(self.data)
        data['photo_coverage'][0]['scene']='exterior';data['interior_assessments']=[]
        data['exterior_assessments']=exterior_rows(data['photo_coverage'])
        result=g.normalize(data,self.p,[self.photo],{})
        self.assertEqual({r['component'] for r in result['exterior_assessments']},set(g.EXTERIOR_COMPONENTS))
        roof=result['exterior_assessments'][0]
        self.assertEqual(roof['condition'],'not_assessable');self.assertIsNone(result['total_high'])
        for mutation in ['missing','foreign','unknown','price']:
            bad=copy.deepcopy(data)
            if mutation=='missing':del bad['exterior_assessments']['roof_surface']
            elif mutation=='foreign':bad['exterior_assessments']['roof_surface']['photo_ids']=['photo_99']
            elif mutation=='unknown':bad['exterior_assessments']['roof_surface']['condition']='working'
            else:bad['exterior_assessments']['roof_surface']['cost']=100
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                g.normalize(bad,self.p,[self.photo],{})
    def test_exterior_cannot_use_interior_evidence_or_invent_an_unseen_component(self):
        data=copy.deepcopy(self.data)
        data['photo_coverage'].append({'photo_id':'photo_2','scene':'exterior','condition':'not_assessable','finding':'זווית מרוחקת'})
        data['exterior_assessments']=exterior_rows(data['photo_coverage'])
        data['exterior_assessments']['roof_surface']['photo_ids']=['photo_1']
        with self.assertRaises(ValueError):g.normalize(data,self.p,[self.photo,dict(self.photo,id='photo_2')],{})
        self.assertEqual(g.normalize(self.data,self.p,[self.photo],{})['exterior_assessments'],[])
        bad=copy.deepcopy(self.data)
        bad['exterior_assessments']['roof_surface'].update(condition='visible_damage',finding='broken roof')
        with self.assertRaises(ValueError):g.normalize(bad,self.p,[self.photo],{})
    def test_unseen_exterior_component_needs_no_invented_photo_or_work(self):
        data=copy.deepcopy(self.data)
        data['photo_coverage'][0]['scene']='exterior';data['interior_assessments']=[]
        data['exterior_assessments']=exterior_rows(data['photo_coverage'])
        data['exterior_assessments']['roof_surface'].update(finding='',scope='',rationale='',photo_ids=[])
        roof=g.normalize(data,self.p,[self.photo],{})['exterior_assessments'][0]
        self.assertEqual(roof['condition'],'not_assessable');self.assertEqual(roof['photo_ids'],[])
        self.assertIn('לא ניתן להעריך',roof['finding']);self.assertNotIn('visual_observation',roof)
        self.assertEqual(g.text_diagnostics(data),[])
        for change in [{'condition':'visible_damage'},{'finding':None}]:
            bad=copy.deepcopy(data);bad['exterior_assessments']['roof_surface'].update(change)
            with self.assertRaises(ValueError):g.normalize(bad,self.p,[self.photo],{})
    def test_no_repair_scope_can_be_empty_without_rejecting_visual_description(self):
        for condition in ['no_obvious_damage_in_view','cosmetic_optional','not_assessable']:
            row={'finding':'משטח חלון לבן נראה בחזית','scope':'','rationale':''}
            cleaned=g.normalize_assessment_text(row,condition)
            self.assertTrue(cleaned['scope']);self.assertTrue(cleaned['rationale'])
        with self.assertRaises(ValueError):
            g.normalize_assessment_text({'finding':'חור בחיפוי','scope':'','rationale':'נראה בתמונה'},'visible_damage')
    def test_listing_only_empty_work_scope_is_safe_and_stays_grounded(self):
        data=copy.deepcopy(self.data)
        data['items'][0].update(status='listing_reported',photo_ids=[],fact_keys=['heating'],finding='',scope='',rationale='')
        result=g.normalize(data,self.p,[self.photo],{'heating':{'value':'Gas'}})
        self.assertEqual(result['items'][0]['finding'],'חימום: Gas')
        self.assertNotIn('visual_observation',result['items'][0])
        self.assertIsNone(result['items'][0]['cost_high'])
    def test_last_photo_damage_cannot_be_lost_in_room_classification(self):
        photos=[dict(self.photo,id='photo_'+str(i+1)) for i in range(22)]
        coverage=[{'photo_id':p['id'],'scene':'exterior','condition':'not_assessable',
            'finding':'הפרטים אינם ברורים'} for p in photos]
        coverage[-1].update(condition='visible_damage',finding='שבר נראה במדרגה החיצונית')
        result=g.normalize(dict(self.data,photo_coverage=coverage,interior_assessments=[],exterior_assessments=exterior_rows(coverage)),self.p,photos,{})
        self.assertEqual(result['photo_coverage'][-1]['finding'],'שבר נראה במדרגה החיצונית')
        self.assertEqual(result['photo_coverage'][-1]['condition'],'visible_damage')
        with self.assertRaises(ValueError):
            g.normalize(dict(self.data,photo_coverage=coverage[:-1],interior_assessments=[]),self.p,photos,{})
    def test_unclear_photo_cannot_certify_no_damage(self):
        data=copy.deepcopy(self.data)
        data['photo_coverage'][0].update(scene='unclear',condition='no_obvious_damage_in_view',finding='everything works')
        data['interior_assessments']=[]
        row=g.normalize(data,self.p,[self.photo],{})['photo_coverage'][0]
        self.assertEqual(row['condition'],'not_assessable');self.assertNotIn('works',row['finding'])
    def test_collector_preserves_links_above_its_photo_cap_in_intake(self):
        inputs={'property_id':'qa-1','identity':[],'photos':[self.photo],'available_photo_count':70,'facts':{}}
        with patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',return_value=('image/jpeg',b'image','a'*64)),patch.object(g.requests,'post',return_value=self.fake_response()):
            result=g.collect(self.p,self.root)
        self.assertEqual(result['photo_intake']['available_links'],70)
        self.assertEqual(result['photo_intake']['excluded_by_photo_limit'],69)
        self.assertEqual(result['photo_intake']['prepared_photos'],1)
    def test_all_22_source_photos_including_later_interiors_are_analyzed_once(self):
        rows=[dict(self.photo,url='https://ssl.cdn-redfin.com/photo/'+str(i)+'.jpg') for i in range(22)]
        data=dict(self.data,photo_coverage=[{'photo_id':'photo_'+str(i+1),'scene':'exterior' if i<2 else 'kitchen','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'} for i in range(22)])
        data['interior_assessments']=interior_rows(data['photo_coverage'])
        data['exterior_assessments']=exterior_rows(data['photo_coverage'])
        def image(row):
            i=int(row['url'].split('/')[-1].split('.')[0]);return 'image/jpeg',b'image',format(i,'064x')
        inputs={'property_id':'qa-1','identity':['18 MAIN ST','PITTSBURGH','PA','15203'],'photos':rows,'facts':{}}
        with patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',side_effect=image) as fetched,patch.object(g.requests,'post',return_value=self.fake_response(data=data)) as post:
            result=g.collect(self.p,self.root)
        self.assertEqual(result['photo_count'],22);self.assertEqual(len(result['photo_coverage']),22)
        self.assertEqual(fetched.call_count,22);self.assertEqual(post.call_count,1)
        self.assertEqual(result['photo_intake']['prepared_photos'],22)
        self.assertTrue(result['photos'][-1]['url'].endswith('/21.jpg'))
        self.assertEqual(post.call_args.kwargs['json']['generationConfig']['maxOutputTokens'],16384)
        self.assertIsNone(result['total_high'])
    def test_maximum60_and_spread_order_cover_tail_without_unbounded_requests(self):
        for count in [0,1,2,22,60]:self.assertEqual(sorted(g.spread_order(count)),list(range(count)))
        self.assertEqual(g.spread_order(60)[:2],[0,59])
        rows=[dict(self.photo,url='https://ssl.cdn-redfin.com/photo/'+str(i)+'.jpg') for i in range(65)]
        inputs={'property_id':'qa-1','identity':[],'photos':rows,'facts':{}}
        data=dict(self.data,photo_coverage=[{'photo_id':'photo_'+str(i+1),'scene':'unclear','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'} for i in range(60)])
        data['interior_assessments']=interior_rows(data['photo_coverage'])
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
        self.assertEqual(g.MAX_TOTAL_IMAGE_BYTES,12_000_000);self.assertEqual(g.MAX_DAILY_CALLS,20)
    def test_fetch_deadline_does_not_claim_unattempted_photos_were_analyzed(self):
        inputs={'property_id':'qa-1','identity':[],'photos':[self.photo]*20,'facts':{}}
        with patch.object(g,'verified_inputs',return_value=inputs),patch.object(g,'fetch_image',return_value=('image/jpeg',b'image','a'*64)) as fetched,patch.object(g.time,'monotonic',side_effect=[0,0,91,100,101]),patch.object(g.requests,'post',return_value=self.fake_response()):
            result=g.collect(self.p,self.root)
        self.assertEqual(fetched.call_count,1);self.assertEqual(result['photo_count'],1);self.assertEqual(result['photo_intake']['not_attempted'],19)

    def test_interior_components_cannot_be_omitted_or_duplicated(self):
        rows=self.data['interior_assessments']
        for invalid in [[],rows[:-1],[rows[0]]*len(rows)]:
            with self.subTest(rows=invalid),self.assertRaises(ValueError):g.normalize(dict(self.data,interior_assessments=invalid),self.p,[self.photo],{})
    def test_interior_evidence_must_be_from_the_same_classified_area(self):
        coverage=[{'photo_id':'photo_1','scene':'kitchen','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'},{'photo_id':'photo_2','scene':'bedroom','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'}]
        data=dict(self.data,photo_coverage=coverage,interior_assessments=interior_rows(coverage))
        data['interior_assessments'][0]['photo_ids']=['photo_2']
        with self.assertRaises(ValueError):g.normalize(data,self.p,[self.photo,dict(self.photo,id='photo_2')],{})
    def test_no_visible_damage_does_not_become_a_replacement_scope_or_zero_cost(self):
        self.data['interior_assessments'][0].update(condition='no_obvious_damage_in_view',scope='replace everything')
        result=g.normalize(self.data,self.p,[self.photo],{})
        row=result['interior_assessments'][0]
        self.assertNotIn('replace',row['scope']);self.assertIsNone(result['total_high']);self.assertFalse(result['complete'])
    def test_unassessable_never_becomes_a_claim_of_damage_or_required_replacement(self):
        self.data['interior_assessments'][0].update(finding='broken floor',scope='replacement required')
        row=g.normalize(self.data,self.p,[self.photo],{})['interior_assessments'][0]
        self.assertNotIn('broken',row['finding']);self.assertNotIn('replacement required',row['scope'])
    def test_optional_cosmetics_cannot_prescribe_required_replacement(self):
        self.data['interior_assessments'][0].update(condition='cosmetic_optional',
            finding='גמר מיושן ללא שבר נראה',scope='replace everything immediately')
        result=g.normalize(self.data,self.p,[self.photo],{})
        row=result['interior_assessments'][0]
        self.assertEqual(row['condition'],'cosmetic_optional')
        self.assertNotIn('replace',row['scope']);self.assertIn('לבחירה',row['scope'])
        self.assertIn('תפקוד לא נבדקו',row['rationale'])
        self.assertIsNone(result['total_high'])
    def test_no_obvious_damage_does_not_certify_function_in_any_displayed_field(self):
        self.data['interior_assessments'][0].update(condition='no_obvious_damage_in_view',
            finding='fully operational and safe',scope='no maintenance ever needed',rationale='passed functional inspection')
        row=g.normalize(self.data,self.p,[self.photo],{})['interior_assessments'][0]
        self.assertNotIn('operational',row['finding']);self.assertNotIn('inspection',row['rationale'])
        self.assertNotIn('ever',row['scope']);self.assertIn('לא נבדקה',row['finding'])
    def test_specific_visible_damage_keeps_its_observation_and_photo_without_prices(self):
        finding='חור נראה בחלק התחתון של הקיר בתמונה'
        self.data['interior_assessments'][0].update(condition='visible_damage',
            finding=finding,scope='תיקון נקודתי של החור',rationale='נראה חוסר בחיפוי הקיר')
        result=g.normalize(self.data,self.p,[self.photo],{})
        row=result['interior_assessments'][0]
        self.assertEqual(row['finding'],finding);self.assertEqual(row['photo_ids'],['photo_1'])
        self.assertIsNone(result['total_high']);self.assertFalse(result['complete'])
    def test_legacy_unspecified_wear_is_not_a_new_repair_diagnosis(self):
        self.data['interior_assessments'][0]['condition']='visible_wear'
        with self.assertRaises(ValueError):g.normalize(self.data,self.p,[self.photo],{})
    def test_unseen_systems_cannot_claim_defects_or_required_replacement(self):
        self.data['items'][0].update(status='not_visible',photo_ids=[],finding='hidden system broken',
            scope='replacement required',rationale='fully inspected')
        row=g.normalize(self.data,self.p,[self.photo],{})['items'][0]
        self.assertNotIn('broken',row['finding']);self.assertNotIn('replacement required',row['scope'])
        self.assertNotIn('fully inspected',row['rationale']);self.assertIsNone(row['cost_high'])
        self.assertEqual(row['condition'],'not_assessable')
    def test_general_exterior_and_system_items_also_distinguish_optional_work(self):
        for group in ['exterior','systems']:
            self.data['items'][0].update(group=group,condition='cosmetic_optional',scope='replace everything')
            row=g.normalize(self.data,self.p,[self.photo],{})['items'][0]
            self.assertEqual(row['condition'],'cosmetic_optional');self.assertNotIn('replace',row['scope'])
    def test_listing_equipment_cannot_become_a_repair_diagnosis(self):
        self.data['items'][0].update(status='listing_reported',photo_ids=[],fact_keys=['heating'])
        row=g.normalize(self.data,self.p,[self.photo],{'heating':{'value':'Gas'}})['items'][0]
        self.assertEqual(row['condition'],'not_assessable');self.assertEqual(row['finding'],'חימום: Gas')
    def test_general_item_unknown_or_missing_condition_is_rejected(self):
        for invalid in ['visible_wear','fully_functional',None]:
            data=copy.deepcopy(self.data);data['items'][0]['condition']=invalid
            with self.assertRaises(ValueError):g.normalize(data,self.p,[self.photo],{})
        data=copy.deepcopy(self.data);del data['items'][0]['condition']
        with self.assertRaises(ValueError):g.normalize(data,self.p,[self.photo],{})
    def test_smaller_provider_grammar_keeps_strict_local_condition_validation(self):
        self.assertEqual(g.SCHEMA['properties']['items']['items']['properties']['condition'],{'type':'string'})
        for value in g.SCHEMA['properties']['interior_assessments']['properties'].values():
            self.assertEqual(value['properties']['condition'],{'type':'string'})
            self.assertIn('condition',value['required'])
        for target in ['items','interior_assessments']:
            data=copy.deepcopy(self.data);data[target][0]['condition']='unknown_open_wire_value'
            with self.assertRaises(ValueError):g.normalize(data,self.p,[self.photo],{})
    def test_cached_cosmetic_claims_do_not_certify_structure_or_function(self):
        result=g.normalize(self.data,self.p,[self.photo],{})
        result['interior_assessments'][0].update(condition='cosmetic_optional',
            finding='בלאי קל',scope='replace cabinets',rationale='no functional or structural defects')
        result['observed_at']='2026-10-27T09:00:00+00:00'
        before=copy.deepcopy(result)
        safe=g.enforce_condition_policy(result)
        row=safe['interior_assessments'][0]
        self.assertNotIn('no functional',row['rationale']);self.assertNotIn('replace',row['scope'])
        self.assertEqual(row['condition'],'cosmetic_optional');self.assertEqual(row['photo_ids'],before['interior_assessments'][0]['photo_ids'])
        self.assertEqual(safe['observed_at'],before['observed_at']);self.assertEqual(result,before)
        self.assertEqual(g.enforce_condition_policy(safe),safe)
    def test_interior_prices_unknown_conditions_and_extra_rooms_are_rejected(self):
        for change in [{'cost':1000},{'condition':'fully_inspected'},{'scene':'garage'},{'component':'whole_house'}]:
            data=copy.deepcopy(self.data);data['interior_assessments'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):g.normalize(data,self.p,[self.photo],{})
    def test_no_interior_photos_means_no_fabricated_interior_assessment(self):
        data=dict(self.data,photo_coverage=[{'photo_id':'photo_1','scene':'exterior','condition':'not_assessable','finding':'פרטי המצב אינם נראים בבירור'}],interior_assessments=[])
        data['exterior_assessments']=exterior_rows(data['photo_coverage'])
        self.assertEqual(g.normalize(data,self.p,[self.photo],{})['interior_assessments'],[])

    def test_wire_schema_requires_named_components_instead_of_a_possibly_empty_list(self):
        wire=g.SCHEMA['properties']['interior_assessments']
        self.assertEqual(wire['type'],'object');self.assertEqual(set(wire['required']),set(g.INTERIOR_SLOTS))
        self.assertEqual(set(g.INTERIOR_SLOTS.values()),{(s,c) for s,codes in g.COMPONENTS.items() for c in codes})
        self.assertNotIn('anyOf',json.dumps(wire));self.assertNotIn('null',json.dumps(wire))
        for value in wire['properties'].values():
            self.assertEqual(set(value['required']),{'condition','finding','scope','rationale','photo_ids'})
    def test_named_scene_objects_are_normalized_and_missing_components_rejected(self):
        wire={scene:None for scene in g.COMPONENTS}
        wire['living_dining']={row['component']:{k:v for k,v in row.items() if k not in {'scene','component'}} for row in self.data['interior_assessments']}
        result=g.normalize(dict(self.data,interior_assessments=wire),self.p,[self.photo],{})
        self.assertEqual(len(result['interior_assessments']),3)
        del wire['living_dining']['flooring']
        with self.assertRaises(ValueError):g.normalize(dict(self.data,interior_assessments=wire),self.p,[self.photo],{})
    def test_named_unphotographed_area_cannot_claim_an_assessment(self):
        wire={scene:None for scene in g.COMPONENTS}
        wire['kitchen']={}
        with self.assertRaises(ValueError):g.normalize(dict(self.data,interior_assessments=wire),self.p,[self.photo],{})

    def test_flat_provider_slots_normalize_to_same_complete_public_assessments(self):
        wire={slot:{'condition':'not_assessable','finding':'','scope':'','rationale':'','photo_ids':[]} for slot in g.INTERIOR_SLOTS}
        for row in self.data['interior_assessments']:
            slot=next(slot for slot,pair in g.INTERIOR_SLOTS.items() if pair==(row['scene'],row['component']))
            wire[slot]={key:value for key,value in row.items() if key not in {'scene','component'}}
        result=g.normalize(dict(self.data,interior_assessments=wire),self.p,[self.photo],{})
        self.assertEqual(len(result['interior_assessments']),3)
        self.assertEqual({r['component'] for r in result['interior_assessments']},set(g.COMPONENTS['living_dining']))
        kitchen=next(slot for slot,pair in g.INTERIOR_SLOTS.items() if pair[0]=='kitchen')
        wire[kitchen]['condition']='visible_wear'
        result=g.normalize(dict(self.data,interior_assessments=wire),self.p,[self.photo],{})
        self.assertEqual({r['scene'] for r in result['interior_assessments']},{'living_dining'})
    def test_provider_schema_error_is_categorized_without_retaining_message(self):
        response=self.fake_response(status=400)
        response.iter_content.return_value=[json.dumps({'error':{'status':'INVALID_ARGUMENT','message':'Schema has too many states; unit-test-only-key'}}).encode()]
        result,_=self.run_collect(response)
        self.assertEqual(result['provider_hint'],'schema_complexity_limit')
        self.assertNotIn('unit-test-only-key',json.dumps(self.saved))

    def test_diagnostics_keep_only_known_structure_and_evidence_counts(self):
        wire={slot:{'condition':'visible_wear','finding':'unit-test-only-key','scope':'private text',
            'rationale':'untrusted text','photo_ids':['photo_99','unit-test-only-key']} for slot in g.INTERIOR_SLOTS}
        result=g.interior_diagnostics(dict(self.data,interior_assessments=wire))
        self.assertEqual(result['expected_components'],3)
        self.assertTrue(all(row['matching_scene_references']==0 for row in result['components']))
        self.assertNotIn('unit-test-only-key',json.dumps(result));self.assertNotIn('private text',json.dumps(result))
    def test_ignored_unphotographed_placeholders_do_not_weaken_visible_area_evidence(self):
        wire={slot:None for slot in g.INTERIOR_SLOTS}
        for row in self.data['interior_assessments']:
            slot=next(slot for slot,pair in g.INTERIOR_SLOTS.items() if pair==(row['scene'],row['component']))
            wire[slot]={key:value for key,value in row.items() if key not in {'scene','component'}}
        self.assertEqual(len(g.normalize(dict(self.data,interior_assessments=wire),self.p,[self.photo],{})['interior_assessments']),3)
        visible=next(slot for slot,pair in g.INTERIOR_SLOTS.items() if pair==('living_dining','flooring'))
        wire[visible]['photo_ids']=['photo_99']
        with self.assertRaises(ValueError):g.normalize(dict(self.data,interior_assessments=wire),self.p,[self.photo],{})

if __name__=='__main__':unittest.main()
