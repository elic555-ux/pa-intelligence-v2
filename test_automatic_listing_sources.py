import copy
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

import automatic_listing_sources as worker
import property_sources as registry
import secondary_listing_source as legacy
import tarasa_listing_source as source
from test_secondary_listing_source import ROW, URL, page
from test_tarasa_listing_source import tarasa_page


def make_rows(count=5):
    return [{**copy.deepcopy(ROW),'id':'PA-MLS-'+str(1001+i),'docket_id':'MLS-'+str(1001+i),
        'address':str(12+i*2)+' First St','url':'https://www.redfin.com/PA/Pittsburgh/x/home/'+str(1001+i),
        'listed_date':datetime.now(timezone.utc).date().isoformat(),
        'last_scan_id':'S1','market_status':'active'} for i in range(count)]


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        self.now=datetime.now(timezone.utc).replace(hour=12, minute=7, second=0, microsecond=0)
        self.rows=make_rows()
        self.put_inventory()
        self.scan={'last_scan':{'scan_id':'S1'},'last_event':{'scan_id':'S1','status':'partial','active_sectors':['mls'],'github_sha':'abc'}}
        registry.atomic_json(self.repo/'scanner_status.json',self.scan)
        registry.atomic_json(self.repo/source.STATUS,{'request_id':'manual-protected'})
        registry.atomic_json(self.repo/'cloud-test.json',{'deal':'protected'})
        self.manual_before=(self.repo/source.STATUS).read_bytes()

    def put_inventory(self):
        registry.atomic_json(self.repo/'properties.json',self.rows)
        self.inventory_before=(self.repo/'properties.json').read_bytes()

    def reader(self,blocked=False,max_requests=110,missing=(),wrong=()):
        rows,clock,calls=self.rows,[0],[]
        class Opener:
            def open(self,req,timeout):
                url=req.full_url
                calls.append((url,clock[0]))
                if url.endswith('/robots.txt'):
                    return io.BytesIO(b'User-agent: *\nCrawl-delay: 5\nDisallow: /sist/\n')
                mls=url.split('/')[6]
                if blocked:
                    raise HTTPError(url,403,'blocked',{'Retry-After':'172800'},None)
                if mls in missing:
                    raise HTTPError(url,404,'not found',{},None)
                row=next(r for r in rows if registry.listing_id(r)==mls)
                html=tarasa_page(row)
                if mls in wrong:
                    html=html.replace('"addressLocality": "Pittsburgh"','"addressLocality": "Beechview"')
                return io.BytesIO(html.encode())
        reader=source.PublicReader(opener=Opener(),clock=lambda:clock[0],sleep=lambda d:clock.__setitem__(0,clock[0]+d),max_requests=max_requests)
        reader.calls=calls
        return reader


    def integrated_config(self, automatic=False, enabled=True, batch_size=3, frequency='hourly'):
        profile = {'scheduleControlVersion': 2, 'autoScanEnabled': automatic,
            'completionSettings': {'enabled': enabled, 'frequency': frequency, 'batchSize': batch_size,
                'timezone': 'UTC', 'dailyLimit': 60,
                'startDate': (self.now.date() - timedelta(days=30)).isoformat()}}
        registry.atomic_json(self.repo / 'scan_config.json', profile)

    def test_scan_mode_collects_without_background_activation(self):
        self.integrated_config()
        report = worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        self.assertTrue(report['scan_integrated'])
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(report['new_rentcast_calls'], 0)
        self.assertLessEqual(report['new_source_network_requests'], 13)
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.inventory_before)
        self.assertEqual((self.repo / source.STATUS).read_bytes(), self.manual_before)

    def test_scan_mode_requires_a_current_successful_mls_scan(self):
        self.integrated_config()
        for event in (
            {**self.scan['last_event'], 'status': 'failed'},
            {**self.scan['last_event'], 'active_sectors': ['sheriff']},
            {**self.scan['last_event'], 'scan_id': None},
        ):
            registry.atomic_json(self.repo / 'scanner_status.json', {'last_event': event})
            reader = self.reader()
            report = worker.run(self.repo, 'scan', 'abc', reader, now=self.now)
            self.assertEqual(report['status'], 'no_new_mls_scan')
            self.assertFalse(report['should_save'])
            self.assertEqual(reader.requests, 0)

    def test_scan_mode_preserves_explicit_completion_pause(self):
        self.integrated_config(enabled=False)
        reader = self.reader()
        report = worker.run(self.repo, 'scan', 'abc', reader, now=self.now)
        self.assertEqual(report['status'], 'automatic_disabled')
        self.assertEqual(reader.requests, 0)

    def test_scan_mode_prioritizes_current_scan_rows(self):
        self.integrated_config()
        self.rows = make_rows(20)
        for row in self.rows[5:]:
            row['last_scan_id'] = 'older'
        self.put_inventory()
        report = worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        self.assertEqual(report['attempted_properties'], 3)
        self.assertLessEqual(set(item['property_id'] for item in report['results']),
                             set(row['id'] for row in self.rows[:5]))

    def test_replayed_scan_does_not_repeat_work(self):
        self.integrated_config()
        worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        reader = self.reader()
        report = worker.run(self.repo, 'scan', 'abc', reader, now=self.now)
        self.assertEqual(report['status'], 'scan_already_processed')
        self.assertFalse(report['should_save'])
        self.assertEqual(reader.requests, 0)

    def test_scan_completion_prevents_same_slot_background_requests(self):
        self.integrated_config(automatic=True)
        worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        reader = self.reader()
        report = worker.run(self.repo, 'automatic', 'abc', reader, now=self.now)
        self.assertEqual(report['status'], 'not_due_or_daily_limit')
        self.assertEqual(reader.requests, 0)

    def test_scan_completion_shares_daily_limit_with_background(self):
        self.integrated_config(automatic=True)
        registry.atomic_json(self.repo / worker.STATE, {'schema': 1, 'enabled': False,
            'entries': {}, 'budget_day': self.now.date().isoformat(), 'daily_attempted': 58})
        report = worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        self.assertEqual(report['attempted_properties'], 2)
        self.assertEqual(registry.read_json(self.repo / worker.STATE)['daily_attempted'], 60)
        self.scan['last_event']['scan_id'] = 'S2'
        registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
        reader = self.reader()
        report = worker.run(self.repo, 'scan', 'abc', reader, now=self.now)
        self.assertEqual(report['status'], 'daily_limit_reached')
        self.assertEqual(reader.requests, 0)

    def test_check_is_read_only_and_network_free(self):
        before={p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        reader=self.reader()
        report=worker.run(self.repo,'check',reader=reader,now=self.now)
        self.assertEqual(report['pending'],5)
        self.assertFalse(report['enabled'])
        self.assertEqual(reader.requests,0)
        self.assertEqual(before,{p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})

    def test_pilot_shares_robots_and_uses_direct_routes_without_directory(self):
        reader=self.reader()
        report=worker.run(self.repo,'pilot',reader=reader,now=self.now)
        self.assertEqual(report['new_snapshots'],3)
        self.assertEqual(report['attempted_properties'],3)
        self.assertEqual(report['pending'],2)
        self.assertEqual(report['new_source_network_requests'],4)
        self.assertFalse(report['enabled'])
        self.assertTrue(all(b[1]-a[1]>=10 for a,b in zip(reader.calls,reader.calls[1:])))
        self.assertFalse(any('site-map' in url or 'newest-listings' in url for url,_ in reader.calls))
        self.assertEqual((self.repo/'properties.json').read_bytes(),self.inventory_before)
        self.assertEqual((self.repo/source.STATUS).read_bytes(),self.manual_before)

    def test_next_run_resumes_immediately_then_all_cached_uses_zero_http(self):
        worker.run(self.repo,'pilot',reader=self.reader(),now=self.now)
        report=worker.run(self.repo,'pilot',reader=self.reader(),now=self.now)
        self.assertEqual(report['cached_properties'],3)
        self.assertEqual(report['new_snapshots'],2)
        self.assertEqual(report['pending'],0)
        reader=self.reader()
        self.assertEqual(worker.run(self.repo,'pilot',reader=reader,now=self.now)['cached_properties'],5)
        self.assertEqual(reader.requests,0)

    def test_large_batch_and_automatic_have_independent_caps(self):
        self.rows=make_rows(105)
        self.put_inventory()
        report=worker.run(self.repo,'batch',reader=self.reader(),now=self.now)
        self.assertEqual(report['new_snapshots'],10)
        self.assertEqual(report['pending'],95)
        self.assertEqual(report['new_source_network_requests'],11)
        self.assertFalse(report['enabled'])
        self.assertEqual((self.repo/'properties.json').read_bytes(),self.inventory_before)

    def test_enabled_automatic_caps_at_ten(self):
        self.rows=make_rows(30)
        self.put_inventory()
        worker.run(self.repo,'enable',now=self.now)
        report=worker.run(self.repo,'automatic','abc',reader=self.reader(),now=self.now)
        self.assertEqual(report['new_snapshots'],10)
        self.assertEqual(report['new_source_network_requests'],11)
        self.assertEqual(report['pending'],20)

    def test_legacy_queue_membership_activation_and_cached_facts_survive_migration(self):
        entries={r['id']:{'fingerprint':worker.fingerprint(r),'status':'waiting_for_discovery',
            'source_url':URL,'retry_after_at':(self.now+timedelta(days=2)).isoformat()} for r in self.rows}
        registry.atomic_json(self.repo/worker.STATE,{'schema':1,'enabled':False,'entries':entries,'discovery_page':17})
        old=legacy.parse_detail(page(self.rows[0]),self.rows[0],URL,self.now.isoformat())
        path=self.repo/legacy.ROOT/(source.cache_key(self.rows[0]['id'])+'.json')
        registry.atomic_json(path,old)
        cache_before=path.read_bytes()
        registry.atomic_json(self.repo/legacy.SOURCE_STATE,{'retry_after_at':(self.now+timedelta(days=3)).isoformat()})
        report=worker.run(self.repo,'pilot',reader=self.reader(),now=self.now)
        state=registry.read_json(self.repo/worker.STATE)
        self.assertEqual(report['migrated_provider_entries'],5)
        self.assertEqual(set(state['entries']),set(entries))
        self.assertFalse(state['enabled'])
        self.assertEqual(report['cached_properties'],1)
        self.assertEqual(report['new_snapshots'],3)
        self.assertEqual(cache_before,path.read_bytes())

    def test_missing_or_mismatched_listing_does_not_stop_other_rows(self):
        report=worker.run(self.repo,'pilot',reader=self.reader(missing=('1005',),wrong=('1004',)),now=self.now)
        self.assertEqual(report['status'],'batch_complete')
        self.assertEqual(report['attempted_properties'],3)
        self.assertEqual(report['new_snapshots'],1)
        self.assertEqual([r['status'] for r in report['results']],['listing_not_available','source_identity_mismatch','published'])
        self.assertFalse((self.repo/source.SOURCE_STATE).exists())
        report=worker.run(self.repo,'pilot',reader=self.reader(),now=self.now)
        self.assertEqual(report['new_snapshots'],2)
        self.assertEqual(report['pending'],1)
        self.assertEqual(report['review_required'],1)

    def test_block_stops_rest_of_batch_and_cooldown_is_tarasa_only(self):
        report=worker.run(self.repo,'pilot',reader=self.reader(blocked=True),now=self.now)
        self.assertEqual(report['status'],'source_blocked')
        self.assertEqual(report['attempted_properties'],1)
        self.assertEqual(report['new_snapshots'],0)
        self.assertEqual(report['new_source_network_requests'],2)
        reader=self.reader()
        self.assertEqual(worker.run(self.repo,'pilot',reader=reader,now=self.now)['status'],'source_cooldown')
        self.assertEqual(reader.requests,0)
        self.assertEqual(registry.read_json(self.repo/source.SOURCE_STATE)['provider'],'tarasa')

    def test_request_budget_does_not_create_failure_cooldown(self):
        report=worker.run(self.repo,'pilot',reader=self.reader(max_requests=1),now=self.now)
        self.assertEqual(report['status'],'source_request_budget_exhausted')
        self.assertEqual(report['new_source_network_requests'],1)
        self.assertEqual(report['attempted_properties'],0)
        self.assertEqual(report['pending'],5)
        self.assertFalse((self.repo/source.SOURCE_STATE).exists())
        self.assertEqual(worker.run(self.repo,'pilot',reader=self.reader(),now=self.now)['new_snapshots'],3)

    def test_deadline_stops_before_any_http_without_block(self):
        reader=self.reader()
        reader.deadline=20
        report=worker.run(self.repo,'pilot',reader=reader,now=self.now)
        self.assertEqual(report['status'],'batch_time_limit')
        self.assertEqual(reader.requests,0)
        self.assertFalse((self.repo/source.SOURCE_STATE).exists())

    def test_automatic_requires_activation_and_skip_never_advances_queue(self):
        reader=self.reader()
        self.assertEqual(worker.run(self.repo,'automatic','abc',reader)['status'],'automatic_disabled')
        self.assertEqual(reader.requests,0)
        worker.run(self.repo,'enable',now=self.now)
        before=(self.repo/worker.STATE).read_bytes()
        self.scan['last_event']['status']='skipped'
        registry.atomic_json(self.repo/'scanner_status.json',self.scan)
        self.assertEqual(worker.run(self.repo,'automatic','abc',reader)['status'],'no_new_mls_scan')
        self.assertEqual(before,(self.repo/worker.STATE).read_bytes())
        self.assertEqual(reader.requests,0)

    def test_changed_upstream_scan_is_rejected(self):
        worker.run(self.repo,'enable',now=self.now)
        self.assertEqual(worker.run(self.repo,'automatic','old',self.reader())['status'],'upstream_scan_no_longer_current')

    def test_ambiguous_outside_and_inactive_rows_do_not_fetch(self):
        self.rows.extend([{**self.rows[0],'address':'98 Other St'},
            {**self.rows[1],'id':'PA-MLS-2010','docket_id':'MLS-2010','county':'Butler'},
            {**self.rows[1],'id':'PA-MLS-2011','docket_id':'MLS-2011','market_status':'sold'}])
        self.put_inventory()
        report=worker.run(self.repo,'check')
        self.assertEqual(report['eligible_properties'],4)
        self.assertEqual(report['excluded']['ambiguous_identity'],2)
        self.assertEqual(report['excluded']['outside_initial_source_coverage'],1)
        self.assertEqual(report['excluded']['unconfirmed_or_inactive_listing'],1)

    def test_erie_rows_leave_tarasa_queue_without_network_or_inventory_edits(self):
        erie={**copy.deepcopy(self.rows[0]),'id':'PA-MLS-193460',
            'docket_id':'MLS-193460','county':'Erie','city':'Erie',
            'url':'https://www.redfin.com/PA/Erie/x/home/193460'}
        self.rows=[erie]
        self.put_inventory()
        # A previously queued Erie property must disappear from the Tarasa
        # queue even when the earlier request was already attempted.
        registry.atomic_json(self.repo/worker.STATE,{'schema':1,'enabled':False,
            'entries':{erie['id']:{'fingerprint':worker.fingerprint(erie),
                'active_provider':source.PROVIDER,'status':'listing_not_available'}}})
        reader=self.reader()
        report=worker.run(self.repo,'pilot',reader=reader,now=self.now)
        self.assertEqual(report['excluded']['outside_tarasa_request_scope'],1)
        self.assertEqual(report['eligible_properties'],0)
        self.assertEqual(report['pending'],0)
        self.assertEqual(report['new_source_network_requests'],0)
        self.assertEqual(reader.requests,0)
        self.assertEqual(registry.read_json(self.repo/worker.STATE)['entries'],{})
        self.assertEqual((self.repo/'properties.json').read_bytes(),self.inventory_before)

    def test_allegheny_still_fetches_when_erie_is_excluded(self):
        self.rows=[self.rows[0],{**copy.deepcopy(self.rows[1]),
            'county':'Erie','city':'Erie'}]
        self.put_inventory()
        report=worker.run(self.repo,'pilot',reader=self.reader(),now=self.now)
        self.assertEqual(report['excluded']['outside_tarasa_request_scope'],1)
        self.assertEqual(report['attempted_properties'],1)
        self.assertEqual(report['new_snapshots'],1)
        self.assertEqual(report['new_source_network_requests'],2)
        self.assertEqual(report['results'][0]['property_id'],self.rows[0]['id'])

    def test_failed_identity_is_visible_but_not_automatically_retried(self):
        self.rows=make_rows(1)
        self.put_inventory()
        first=worker.run(self.repo,'pilot',reader=self.reader(wrong=('1001',)),now=self.now)
        self.assertEqual(first['review_required'],1)
        reader=self.reader()
        second=worker.run(self.repo,'pilot',reader=reader,now=self.now)
        self.assertEqual(second['review_required'],1)
        self.assertEqual(second['pending'],0)
        self.assertEqual(second['new_source_network_requests'],0)
        self.assertEqual(reader.requests,0)

    def test_missing_listing_retries_automatically_only_after_saved_wait(self):
        self.rows=make_rows(1)
        self.put_inventory()
        self.integrated_config(automatic=True)
        first=worker.run(self.repo,'scan','abc',self.reader(missing=('1001',)),now=self.now)
        self.assertEqual(first['new_snapshots'],0)
        self.assertEqual(first['pending'],1)
        self.assertEqual(first['review_required'],0)
        self.assertEqual(first['results'][0]['status'],'listing_not_available')

        def next_scan(scan_id):
            self.rows[0]['last_scan_id']=scan_id
            self.put_inventory()
            event={**self.scan['last_event'],'scan_id':scan_id}
            registry.atomic_json(self.repo/'scanner_status.json',{'last_event':event,'last_scan':event})

        next_scan('S2')
        early_reader=self.reader()
        early=worker.run(self.repo,'scan','abc',early_reader,now=self.now+timedelta(hours=1))
        self.assertEqual(early['status'],'waiting_for_retry')
        self.assertEqual(early_reader.requests,0)
        self.assertEqual(early['pending'],1)

        next_scan('S3')
        retry_reader=self.reader()
        retried=worker.run(self.repo,'scan','abc',retry_reader,now=self.now+timedelta(days=2))
        self.assertEqual(retried['new_snapshots'],1)
        self.assertEqual(retried['pending'],0)
        self.assertEqual(retried['review_required'],0)
        self.assertEqual(retried['results'][0]['status'],'published')
        self.assertEqual(retried['new_rentcast_calls'],0)
        self.assertEqual((self.repo/'properties.json').read_bytes(),self.inventory_before)

    def test_long_unverified_id_never_becomes_tarasa_request(self):
        self.rows=[{**make_rows(1)[0], 'id':'PA-MLS-2208152674674600913',
            'docket_id':'MLS-2208152674674600913'}]
        self.put_inventory()
        reader=self.reader()
        report=worker.run(self.repo,'pilot',reader=reader,now=self.now)
        self.assertEqual(report['excluded']['unverified_tarasa_listing_id'],1)
        self.assertEqual(report['pending'],0)
        self.assertEqual(reader.requests,0)

    def test_relisting_cannot_reuse_saved_cache_or_previous_url(self):
        worker.run(self.repo,'batch',reader=self.reader(),now=self.now)
        self.rows[0]['docket_id']='MLS-9911'
        self.put_inventory()
        worker.run(self.repo,'enable',now=self.now)
        entry=registry.read_json(self.repo/worker.STATE)['entries'][self.rows[0]['id']]
        self.assertEqual(entry['status'],'queued')
        self.assertNotIn('source_url',entry)

    def test_recent_inventory_enters_queue_independent_of_last_scan(self):
        self.rows[0]['last_scan_id']='older'
        self.put_inventory()
        self.assertEqual(worker.run(self.repo,'check')['pending'],5)
        worker.run(self.repo,'enable',now=self.now)
        self.rows[1]['last_scan_id']='older'
        self.put_inventory()
        self.assertEqual(worker.run(self.repo,'check')['pending'],5)

    def test_toggle_changes_no_source_cache_or_network(self):
        reader=self.reader()
        self.assertTrue(worker.run(self.repo,'enable',reader=reader)['enabled'])
        self.assertFalse(worker.run(self.repo,'disable',reader=reader)['enabled'])
        self.assertEqual(reader.requests,0)
        self.assertFalse((self.repo/source.ROOT).exists())
        self.assertEqual((self.repo/source.STATUS).read_bytes(),self.manual_before)

    def test_expired_cache_is_refreshable_and_fresh_cache_wins_over_host_cooldown(self):
        worker.run(self.repo,'batch',reader=self.reader(),now=self.now)
        future=self.now+timedelta(days=8)
        self.assertEqual(worker.run(self.repo,'check',now=future)['pending'],5)
        registry.atomic_json(self.repo/source.SOURCE_STATE,{'retry_after_at':(self.now+timedelta(days=1)).isoformat()})
        reader=self.reader()
        report=worker.run(self.repo,'batch',reader=reader,now=self.now)
        self.assertEqual(report['status'],'queue_current')
        self.assertEqual(reader.requests,0)

    def test_saved_batch_size_is_honored_in_scan_background_and_manual_modes(self):
        for size in (3, 4, 5):
            for mode in ('scan', 'automatic', 'batch', 'group10'):
                with self.subTest(size=size, mode=mode), tempfile.TemporaryDirectory() as temp:
                    self.repo = Path(temp)
                    self.rows = make_rows(20)
                    self.put_inventory()
                    registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
                    self.integrated_config(automatic=True, batch_size=size)
                    saved = (self.repo / 'scan_config.json').read_bytes()
                    report = worker.run(self.repo, mode, reader=self.reader(), now=self.now)
                    self.assertEqual(report['attempted_properties'], size)
                    self.assertEqual(report['new_snapshots'], size)
                    self.assertEqual(report['max_properties_per_run'], size)
                    self.assertLessEqual(report['new_source_network_requests'], 13)
                    self.assertEqual((self.repo / 'scan_config.json').read_bytes(), saved)
                    self.assertEqual((self.repo / 'properties.json').read_bytes(), self.inventory_before)

    def test_legacy_ten_setting_migrates_to_three_without_rewriting_config(self):
        self.integrated_config(batch_size=10)
        saved = (self.repo / 'scan_config.json').read_bytes()
        report = worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        self.assertEqual(report['attempted_properties'], 3)
        self.assertEqual((self.repo / 'scan_config.json').read_bytes(), saved)

    def test_invalid_batch_size_and_daily_limit_make_no_requests_or_writes(self):
        for field, invalid in [('batchSize', n) for n in (0, -1, 2, 6, 80, True, '3', None)] + [
                ('dailyLimit', 0), ('dailyLimit', 65), ('dailyLimit', 130), ('dailyLimit', True)]:
            with self.subTest(field=field, invalid=invalid):
                self.integrated_config()
                profile = registry.read_json(self.repo / 'scan_config.json')
                profile['completionSettings'][field] = invalid
                registry.atomic_json(self.repo / 'scan_config.json', profile)
                before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
                reader = self.reader()
                with self.assertRaises(ValueError):
                    worker.run(self.repo, 'scan', 'abc', reader, now=self.now)
                self.assertEqual(reader.requests, 0)
                self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes()
                                         for p in self.repo.rglob('*') if p.is_file()})

    def test_reserved_backlog_turn_progresses_despite_continuous_new_arrivals(self):
        self.rows = make_rows(8)
        self.put_inventory()
        self.integrated_config(automatic=True, frequency='every_15_minutes')
        first = worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        old_ids = {r['id'] for r in self.rows} - {r['property_id'] for r in first['results']}
        served_old = set()
        for turn in range(1, 6):
            newcomers = []
            for offset in range(5):
                mls = 2000 + turn * 10 + offset
                newcomers.append({**copy.deepcopy(self.rows[0]), 'id': 'PA-MLS-' + str(mls),
                    'docket_id': 'MLS-' + str(mls), 'address': str(mls) + ' First St',
                    'url': 'https://www.redfin.com/PA/Pittsburgh/x/home/' + str(mls)})
            self.rows.extend(newcomers)
            self.put_inventory()
            report = worker.run(self.repo, 'automatic', reader=self.reader(),
                                now=self.now + timedelta(minutes=15 * turn))
            ids = [item['property_id'] for item in report['results']]
            self.assertEqual(len(ids), 3)
            self.assertIn(ids[0], old_ids)
            self.assertTrue(set(ids[1:]) <= {row['id'] for row in newcomers})
            served_old.add(ids[0])
        self.assertEqual(served_old, old_ids)

    def test_one_remaining_daily_turn_is_given_to_waiting_backlog(self):
        self.rows = make_rows(8)
        self.put_inventory()
        self.integrated_config(automatic=True, frequency='every_15_minutes')
        first = worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        old_ids = {row['id'] for row in self.rows} - {item['property_id'] for item in first['results']}
        state = registry.read_json(self.repo / worker.STATE)
        state['daily_attempted'] = 59
        registry.atomic_json(self.repo / worker.STATE, state)
        self.rows.append({**copy.deepcopy(self.rows[0]), 'id': 'PA-MLS-9000',
            'docket_id': 'MLS-9000', 'address': '9000 First St',
            'url': 'https://www.redfin.com/PA/Pittsburgh/x/home/9000'})
        self.put_inventory()
        report = worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now + timedelta(minutes=15))
        self.assertEqual(report['attempted_properties'], 1)
        self.assertIn(report['results'][0]['property_id'], old_ids)
        self.assertEqual(registry.read_json(self.repo / worker.STATE)['daily_attempted'], 60)

    def test_background_then_new_scan_does_not_double_request_in_quarter_hour(self):
        self.integrated_config(automatic=True, frequency='every_15_minutes')
        worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now)
        self.scan['last_event']['scan_id'] = 'S2'
        registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
        before = (self.repo / worker.STATE).read_bytes()
        reader = self.reader()
        report = worker.run(self.repo, 'scan', 'abc', reader, now=self.now)
        self.assertEqual(report['status'], 'completion_slot_already_processed')
        self.assertFalse(report['should_save'])
        self.assertEqual(reader.requests, 0)
        self.assertEqual((self.repo / worker.STATE).read_bytes(), before)
        later = worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now + timedelta(minutes=15))
        self.assertEqual(later['new_snapshots'], 2)

    def test_sixty_attempts_are_shared_across_manual_background_and_scan(self):
        self.now = self.now.replace(hour=4, minute=7, second=0, microsecond=0)
        self.rows = make_rows(75)
        self.put_inventory()
        self.integrated_config(automatic=True, batch_size=5, frequency='every_15_minutes')
        attempted = 0
        for turn in range(12):
            mode = ('scan', 'automatic', 'batch')[turn % 3]
            self.scan['last_event']['scan_id'] = 'S' + str(turn + 1)
            registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
            report = worker.run(self.repo, mode, reader=self.reader(),
                                now=self.now + timedelta(minutes=15 * turn))
            attempted += report['attempted_properties']
        self.assertEqual(attempted, 60)
        self.assertEqual(registry.read_json(self.repo / worker.STATE)['daily_attempted'], 60)
        for mode in ('scan', 'automatic', 'batch'):
            self.scan['last_event']['scan_id'] = 'S-limit'
            registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
            reader = self.reader()
            report = worker.run(self.repo, mode, reader=reader, now=self.now + timedelta(hours=4))
            self.assertEqual(report['attempted_properties'], 0)
            self.assertEqual(reader.requests, 0)
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.inventory_before)

    def test_fresh_snapshot_with_unpublished_fields_is_not_refetched(self):
        self.rows = make_rows(1)
        self.put_inventory()
        self.integrated_config(automatic=True, frequency='every_15_minutes')
        worker.run(self.repo, 'scan', 'abc', self.reader(), now=self.now)
        path = self.repo / source.ROOT / (source.cache_key(self.rows[0]['id']) + '.json')
        cached = registry.read_json(path)
        cached['facts'] = {'beds': cached['facts']['beds']}
        cached['photos'] = []
        registry.atomic_json(path, cached)
        saved = path.read_bytes()
        reader = self.reader()
        report = worker.run(self.repo, 'automatic', reader=reader, now=self.now + timedelta(minutes=15))
        self.assertEqual(report['cached_properties'], 1)
        self.assertEqual(reader.requests, 0)
        self.assertEqual(path.read_bytes(), saved)

    def test_quarter_hour_respects_provider_cooldown_without_spending_daily_attempts(self):
        self.integrated_config(automatic=True, frequency='every_15_minutes')
        worker.run(self.repo, 'scan', 'abc', self.reader(blocked=True), now=self.now)
        reader = self.reader()
        report = worker.run(self.repo, 'automatic', reader=reader, now=self.now + timedelta(minutes=15))
        self.assertEqual(report['status'], 'source_cooldown')
        self.assertEqual(reader.requests, 0)
        self.assertEqual(registry.read_json(self.repo / worker.STATE)['daily_attempted'], 1)

    def test_two_failed_attempts_skip_property_and_keep_scan_success(self):
        self.rows = make_rows(4)
        self.put_inventory()
        self.integrated_config(automatic=True)
        self.scan['last_event']['status'] = 'success'
        registry.atomic_json(self.repo / 'scanner_status.json', self.scan)
        scan_before = (self.repo / 'scanner_status.json').read_bytes()
        first = worker.run(self.repo, 'scan', 'abc', self.reader(missing=('1004',)), now=self.now)
        self.assertEqual(first['new_snapshots'], 2)
        state = registry.read_json(self.repo / worker.STATE)
        self.assertEqual(state['entries']['PA-MLS-1004']['unsuccessful_attempts'], 1)
        early_reader = self.reader(missing=('1004',))
        early = worker.run(self.repo, 'automatic', reader=early_reader, now=self.now + timedelta(hours=1))
        self.assertNotIn('PA-MLS-1004', [r['property_id'] for r in early['results']])
        second = worker.run(self.repo, 'automatic', reader=self.reader(missing=('1004',)),
                            now=self.now + timedelta(days=2))
        self.assertEqual(second['incomplete_properties'], 1)
        self.assertEqual(second['completion_status'], 'partial')
        state = registry.read_json(self.repo / worker.STATE)
        self.assertEqual(state['entries']['PA-MLS-1004']['unsuccessful_attempts'], 2)
        self.assertEqual(state['entries']['PA-MLS-1004']['status'], 'completion_incomplete')
        reader = self.reader()
        final = worker.run(self.repo, 'automatic', reader=reader, now=self.now + timedelta(days=4))
        self.assertEqual(reader.requests, 0)
        self.assertEqual(final['incomplete_properties'], 1)
        self.assertEqual(final['pending'], 0)
        self.assertEqual((self.repo / 'scanner_status.json').read_bytes(), scan_before)
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.inventory_before)

    def test_saved_receipt_preserves_two_attempt_limit_if_queue_is_rebuilt(self):
        self.rows = make_rows(1)
        self.put_inventory()
        self.integrated_config(automatic=True)
        worker.run(self.repo, 'scan', 'abc', self.reader(missing=('1001',)), now=self.now)
        worker.run(self.repo, 'automatic', reader=self.reader(missing=('1001',)), now=self.now + timedelta(days=2))
        receipt_path = self.repo / source.ROOT / (source.cache_key(self.rows[0]['id']) + '.json')
        receipt = registry.read_json(receipt_path)
        self.assertEqual(receipt['last_attempt']['unsuccessful_attempts'], 2)
        self.assertEqual(receipt['last_attempt']['completion_status'], 'not_completed')
        (self.repo / worker.STATE).unlink()
        reader = self.reader()
        report = worker.run(self.repo, 'automatic', reader=reader, now=self.now + timedelta(days=4))
        self.assertEqual(report['incomplete_properties'], 1)
        self.assertEqual(report['completion_status'], 'not_completed')
        self.assertEqual(reader.requests, 0)

    def test_successful_second_attempt_resets_failure_count(self):
        self.rows = make_rows(1)
        self.put_inventory()
        self.integrated_config(automatic=True)
        worker.run(self.repo, 'scan', 'abc', self.reader(missing=('1001',)), now=self.now)
        report = worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now + timedelta(days=2))
        self.assertEqual(report['new_snapshots'], 1)
        self.assertEqual(report['incomplete_properties'], 0)
        self.assertEqual(registry.read_json(self.repo / worker.STATE)['entries'][self.rows[0]['id']]['unsuccessful_attempts'], 0)

    def test_relisting_resets_exhausted_completion_with_new_identity(self):
        self.rows = make_rows(1)
        self.put_inventory()
        self.integrated_config(automatic=True)
        worker.run(self.repo, 'scan', 'abc', self.reader(missing=('1001',)), now=self.now)
        worker.run(self.repo, 'automatic', reader=self.reader(missing=('1001',)), now=self.now + timedelta(days=2))
        self.rows[0]['docket_id'] = 'MLS-9911'
        self.put_inventory()
        report = worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now + timedelta(days=4))
        self.assertEqual(report['new_snapshots'], 1)
        self.assertEqual(report['incomplete_properties'], 0)

    def test_budget_stop_does_not_consume_a_property_retry(self):
        self.integrated_config()
        report = worker.run(self.repo, 'scan', 'abc', self.reader(max_requests=1), now=self.now)
        self.assertEqual(report['attempted_properties'], 0)
        state = registry.read_json(self.repo / worker.STATE)
        self.assertTrue(all(e['unsuccessful_attempts'] == 0 for e in state['entries'].values()))
        self.assertEqual(report['incomplete_properties'], 0)

    def test_failed_refresh_exhaustion_keeps_prior_facts_and_photos(self):
        self.rows = make_rows(1)
        self.put_inventory()
        worker.run(self.repo, 'pilot', reader=self.reader(), now=self.now)
        path = self.repo / source.ROOT / (source.cache_key(self.rows[0]['id']) + '.json')
        original = registry.read_json(path)
        worker.run(self.repo, 'pilot', reader=self.reader(blocked=True), now=self.now + timedelta(days=8))
        report = worker.run(self.repo, 'pilot', reader=self.reader(blocked=True), now=self.now + timedelta(days=11))
        self.assertEqual(report['incomplete_properties'], 1)
        saved = registry.read_json(path)
        self.assertEqual(saved['facts'], original['facts'])
        self.assertEqual(saved['photos'], original['photos'])
        reader = self.reader()
        worker.run(self.repo, 'pilot', reader=reader, now=self.now + timedelta(days=14))
        self.assertEqual(reader.requests, 0)

    def test_empty_verified_snapshot_does_not_trigger_repeated_completion(self):
        self.rows = make_rows(1)
        self.put_inventory()
        worker.run(self.repo, 'pilot', reader=self.reader(), now=self.now)
        path = self.repo / source.ROOT / (source.cache_key(self.rows[0]['id']) + '.json')
        empty = registry.read_json(path)
        empty['facts'] = {}; empty['photos'] = []
        registry.atomic_json(path, empty)
        reader = self.reader()
        report = worker.run(self.repo, 'pilot', reader=reader, now=self.now + timedelta(minutes=15))
        self.assertEqual(report['incomplete_properties'], 1)
        self.assertEqual(report['completion_status'], 'not_completed')
        self.assertEqual(reader.requests, 0)

    def test_workflow_keeps_completion_failures_out_of_scan_failure_stage(self):
        root = Path(__file__).resolve().parent
        workflow = (root / '.github/workflows/cron-daily-mls.yml').read_text()
        stage = workflow.split('- name: Record failed stage for the UI', 1)[1].split('run: |', 1)[0]
        self.assertNotIn('source_details', stage)
        self.assertNotIn('source_registry', stage)
        self.assertIn('continue-on-error: true', workflow)
        self.assertIn('השלמת פרטי המודעות לא הסתיימה', workflow)


if __name__=='__main__':
    unittest.main()
