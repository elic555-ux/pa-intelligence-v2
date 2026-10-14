"""Offline boundaries between discovery, saved candidates and completion."""
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import redirect_stdout
import io
import json
import os
import textwrap
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import automatic_listing_sources as worker
import completion_subjects as subjects
import listing_source as basic_source
import orchestrator as scanner
import property_sources as registry
import tarasa_listing_source as source
from test_secondary_listing_source import ROW
from test_tarasa_listing_source import tarasa_page


class IndependentCompletionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.now = datetime.now(timezone.utc).replace(hour=12, minute=7)
        self.row = {**deepcopy(ROW), 'sqft': None, 'beds': None,
                    'property_type': 'Single Family', 'market_status': 'active',
                    'source_scan_area': 'Allegheny County', 'price': 100000,
                    'listed_date': self.now.date().isoformat()}
        self.settings = {'price_range': {'min': 50000, 'max': 190000},
                         'min_sqft': 700, 'max_sqft': 1600, 'min_beds': 1,
                         'max_beds': 3, 'min_baths': 0, 'property_types': ['Single Family']}
        self.scope = scanner.compile_scan_scope([], ['Allegheny'], {})
        registry.atomic_json(self.repo / 'properties.json', [])
        registry.atomic_json(self.repo / 'scanner_status.json', {'last_event': {'status': 'failed'}})
        registry.atomic_json(self.repo / 'scan_config.json', {'scheduleControlVersion': 2,
            'autoScanEnabled': False, 'completionSettings': {'enabled': True,
                'frequency': 'every_15_minutes', 'timezone': 'UTC', 'dailyLimit': 60,
                'batchSize': 3, 'startDate': self.now.date().isoformat()}})
        self.save_review()

    def save_review(self, context=True):
        item = {'record': deepcopy(self.row), 'status': 'needs_details'}
        if context:
            item['filter_context'] = {'settings': deepcopy(self.settings), 'scope': deepcopy(self.scope)}
        registry.atomic_json(self.repo / subjects.REVIEW, {'schema': 1, 'items': {'saved': item}})

    def reader(self, wrong=False):
        html = tarasa_page(self.row)
        if wrong:
            html = html.replace('"addressLocality": "Pittsburgh"', '"addressLocality": "Other City"')
        times = [0]
        class Opener:
            def open(self, request, timeout):
                if request.full_url.endswith('/robots.txt'):
                    return io.BytesIO(b'User-agent: *\nAllow: /\n')
                return io.BytesIO(html.encode())
        return source.PublicReader(opener=Opener(), clock=lambda: times[0],
            sleep=lambda delay: times.__setitem__(0, times[0] + delay), max_requests=13)

    def test_completes_saved_review_without_discovery_or_inventory_promotion(self):
        protected = {name: (self.repo / name).read_bytes() for name in
                     ('properties.json', 'scanner_status.json', 'scan_config.json')}
        with patch.object(scanner, 'fetch_live_mls_for_city', side_effect=AssertionError('No discovery')):
            report = worker.run(self.repo, 'automatic', reader=self.reader(), now=self.now)
        self.assertEqual(report['new_snapshots'], 1)
        self.assertEqual(report['review_properties'], 1)
        self.assertFalse(report['scan_integrated'])
        self.assertEqual(report['review_filter_results'], {'ready_for_review': 1})
        saved = registry.read_json(self.repo / subjects.REVIEW)['items']['saved']
        self.assertIsNone(saved['record']['sqft'])
        self.assertEqual(saved['completed_record']['sqft'], 900)
        self.assertEqual(saved['completed_record']['beds'], 2)
        self.assertEqual(saved['completed_record']['completion_facts']['sqft']['unit'], 'sqft')
        for name, content in protected.items():
            self.assertEqual((self.repo / name).read_bytes(), content)

    def test_cache_completes_review_without_requests_and_no_check_writes(self):
        record = source.parse_detail(tarasa_page(self.row), self.row,
                                     source.target_url(self.row), self.now.isoformat())
        registry.atomic_json(self.repo / source.ROOT / (source.cache_key(self.row['id']) + '.json'), record)
        before = (self.repo / subjects.REVIEW).read_bytes()
        reader = self.reader()
        worker.run(self.repo, 'check', reader=reader, now=self.now)
        self.assertEqual(before, (self.repo / subjects.REVIEW).read_bytes())
        report = worker.run(self.repo, 'batch', reader=reader, now=self.now)
        self.assertEqual(reader.requests, 0)
        self.assertEqual(report['review_updated'], 1)

    def test_original_filter_context_wins_over_changed_global_settings(self):
        self.settings['max_sqft'] = 800
        self.save_review()
        report = worker.run(self.repo, 'batch', reader=self.reader(), now=self.now)
        self.assertEqual(report['review_filter_results'], {'outside_filters': 1})
        item = registry.read_json(self.repo / subjects.REVIEW)['items']['saved']
        self.assertEqual(item['completion']['filter_rejection'], 'sqft')

    def test_legacy_candidates_require_filter_review_and_never_get_promoted(self):
        self.save_review(context=False)
        report = worker.run(self.repo, 'batch', reader=self.reader(), now=self.now)
        self.assertEqual(report['review_filter_results'], {'needs_filter_review': 1})
        self.assertEqual(registry.read_json(self.repo / 'properties.json'), [])

    def test_identity_conflict_never_supplies_parameters(self):
        before = (self.repo / subjects.REVIEW).read_bytes()
        report = worker.run(self.repo, 'batch', reader=self.reader(wrong=True), now=self.now)
        self.assertEqual(report['review_updated'], 0)
        self.assertEqual(report['review_required'], 1)
        self.assertEqual((self.repo / subjects.REVIEW).read_bytes(), before)

    def test_conflicting_saved_ids_are_excluded_before_access(self):
        review = registry.read_json(self.repo / subjects.REVIEW)
        other = deepcopy(review['items']['saved'])
        other['record']['address'] = '98 Other St'
        review['items']['other'] = other
        registry.atomic_json(self.repo / subjects.REVIEW, review)
        reader = self.reader()
        report = worker.run(self.repo, 'batch', reader=reader, now=self.now)
        self.assertEqual(report['eligible_properties'], 0)
        self.assertEqual(reader.requests, 0)

    def test_inventory_review_conflict_does_not_fetch_either_identity(self):
        registry.atomic_json(self.repo / 'properties.json', [{**self.row, 'address': '98 Other St'}])
        reader = self.reader()
        self.assertEqual(worker.run(self.repo, 'batch', reader=reader, now=self.now)['eligible_properties'], 0)
        self.assertEqual(reader.requests, 0)

    def test_original_values_and_non_subject_facts_are_not_overwritten(self):
        row = {**self.row, 'sqft': 1200}
        snapshot = source.parse_detail(tarasa_page(self.row), self.row, source.target_url(self.row))
        snapshot['facts']['beds']['listing_id'] = '9999'
        completed = subjects.complete_record(row, snapshot)
        self.assertEqual(completed['sqft'], 1200)
        self.assertIsNone(completed['beds'])

    def test_explicit_source_type_completes_unknown_type_without_inferring_from_style(self):
        row = {**self.row, 'property_type': None, 'source_property_type': 'Unknown'}
        html = tarasa_page(row).replace('</section>',
            '<div><strong>Property Type</strong><span>Single Family</span></div></section>')
        snapshot = source.parse_detail(html, row, source.target_url(row))
        self.assertEqual(subjects.complete_record(row, snapshot)['property_type'], 'Single Family')
        snapshot['facts'].pop('property_type')
        snapshot['facts']['style'] = {'value': 'Single Family'}
        self.assertIsNone(subjects.complete_record(row, snapshot)['property_type'])

    def test_corrupt_review_fails_before_network_or_inventory_changes(self):
        registry.atomic_json(self.repo / subjects.REVIEW, {'schema': 0, 'items': {}})
        reader = self.reader()
        with self.assertRaises(ValueError):
            worker.run(self.repo, 'batch', reader=reader, now=self.now)
        self.assertEqual(reader.requests, 0)

    def test_workflows_have_separate_triggers_and_only_completion_saves_snapshots(self):
        root = Path(__file__).parent
        discovery = (root / '.github/workflows/cron-daily-mls.yml').read_text(encoding='utf-8')
        completion = (root / '.github/workflows/automatic-listing-sources.yml').read_text(encoding='utf-8')
        self.assertNotIn('python automatic_listing_sources.py', discovery)
        self.assertNotIn('COMPS_REPORTS/additional_sources', discovery)
        self.assertNotIn('workflow_run:', completion)
        self.assertIn('COMPS_REPORTS/scan_review', completion)
        self.assertNotIn('git add -- properties.json', completion)

    def test_manual_completion_workflow_executes_worker_without_running_discovery(self):
        workflow = (Path(__file__).parent / '.github/workflows/automatic-listing-sources.yml').read_text(encoding='utf-8')
        block = workflow.split('- name: Complete a bounded group or change activation', 1)[1]
        code = textwrap.dedent(block.split("python - <<'PY'", 1)[1].split('\n          PY', 1)[0])
        with patch.dict(os.environ, {'AUTO_EVENT': 'workflow_dispatch', 'AUTO_MODE': 'batch'}), \
                patch('subprocess.run') as run:
            exec(compile(code, '<completion-workflow>', 'exec'), {})
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1:], ['automatic_listing_sources.py', 'batch'])

    def test_discovery_success_is_not_downgraded_by_missing_mls_parameters(self):
        profile = json.loads((Path(__file__).parent / 'manual_scan_config.json').read_text(encoding='utf-8'))
        profile.update(counties=['Allegheny'], cities=[], geoAreas={}, sectors=['mls'])
        registry.atomic_json(self.repo / 'manual_scan_config.json', profile)
        registry.atomic_json(self.repo / subjects.REVIEW, {'schema': 1, 'items': {}})
        original = Path.cwd()
        def fetch(city, low, high, audit):
            audit.update(status='success', rows=1, coverage='not_proven_complete')
            return [deepcopy(self.row)]
        try:
            os.chdir(self.repo)
            with patch.object(scanner, 'CONFIG_FILE', 'manual_scan_config.json'), \
                    patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'workflow_dispatch',
                        'PA_SCAN_CONFIG_COMMIT': '', 'PA_SCAN_REQUEST_ID': ''}), \
                    patch.object(scanner, 'fetch_live_mls_for_city', side_effect=fetch), \
                    patch.object(source, 'run', side_effect=AssertionError('No completion in discovery')), \
                    patch.object(basic_source, 'get_html', side_effect=AssertionError('No detail pages')), \
                    redirect_stdout(io.StringIO()):
                scanner.run_orchestrator()
        finally:
            os.chdir(original)
        event = registry.read_json(self.repo / 'scanner_status.json')['last_event']
        self.assertEqual(event['status'], 'success')
        self.assertEqual(event['sources']['mls']['needs_details'], 1)
        self.assertEqual(event['listing_source_details']['external_page_requests'], 0)
        item = next(iter(registry.read_json(self.repo / subjects.REVIEW)['items'].values()))
        self.assertIn('filter_context', item)
        self.assertEqual(registry.read_json(self.repo / 'properties.json'), [])


if __name__ == '__main__':
    unittest.main()
