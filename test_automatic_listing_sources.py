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
        'last_scan_id':'S1','market_status':'active'} for i in range(count)]


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        self.now=datetime.now(timezone.utc)
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
        self.assertEqual(report['new_snapshots'],100)
        self.assertEqual(report['pending'],5)
        self.assertEqual(report['new_source_network_requests'],101)
        self.assertFalse(report['enabled'])
        self.assertEqual((self.repo/'properties.json').read_bytes(),self.inventory_before)

    def test_enabled_automatic_caps_at_twenty_five(self):
        self.rows=make_rows(30)
        self.put_inventory()
        worker.run(self.repo,'enable',now=self.now)
        report=worker.run(self.repo,'automatic','abc',reader=self.reader(),now=self.now)
        self.assertEqual(report['new_snapshots'],25)
        self.assertEqual(report['new_source_network_requests'],26)
        self.assertEqual(report['pending'],5)

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
        self.assertEqual(report['pending'],2)

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

    def test_relisting_cannot_reuse_saved_cache_or_previous_url(self):
        worker.run(self.repo,'batch',reader=self.reader(),now=self.now)
        self.rows[0]['docket_id']='MLS-9911'
        self.put_inventory()
        worker.run(self.repo,'enable',now=self.now)
        entry=registry.read_json(self.repo/worker.STATE)['entries'][self.rows[0]['id']]
        self.assertEqual(entry['status'],'queued')
        self.assertNotIn('source_url',entry)

    def test_only_actual_scan_adds_new_rows_but_old_queue_resumes(self):
        self.rows[0]['last_scan_id']='older'
        self.put_inventory()
        self.assertEqual(worker.run(self.repo,'check')['pending'],4)
        worker.run(self.repo,'enable',now=self.now)
        self.rows[1]['last_scan_id']='older'
        self.put_inventory()
        self.assertEqual(worker.run(self.repo,'check')['pending'],4)

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


if __name__=='__main__':
    unittest.main()
