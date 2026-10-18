"""Offline tests: clock-driven cadence, saved limits and fixed GitHub-only dispatch."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import unittest
from unittest import mock
import urllib.error

import completion_heartbeat as heartbeat

NOW = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)
PROFILE = {'scheduleControlVersion': 2, 'completionSettings': {
    'enabled': True, 'frequency': 'every_15_minutes',
    'timezone': 'America/New_York', 'times': ['08:00'],
    'batchSize': 3, 'dailyLimit': 60}}
STATE = {'budget_day': '2026-10-18', 'daily_attempted': 9,
         'last_automatic_run': '2026-10-18T13:30:00+00:00'}


class FakeApi:
    def __init__(self):
        self.profile = copy.deepcopy(PROFILE)
        self.state = copy.deepcopy(STATE)
        self.public = True
        self.dispatched = 0
        self.reads = []
        self.error = None

    def public_repository(self):
        if self.error:
            error, self.error = self.error, None
            raise error
        return self.public

    def read_json(self, path):
        self.reads.append(path)
        return copy.deepcopy(self.profile if path == 'scan_config.json' else self.state)

    def dispatch(self):
        self.dispatched += 1


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        if not 0 < seconds <= 60:
            raise AssertionError('Waits must be bounded')
        self.elapsed += seconds

    def now(self):
        return NOW + timedelta(seconds=self.elapsed)


class TickTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()

    def test_due_tick_dispatches_automatic_mode_without_source_or_rentcast_requests(self):
        result = heartbeat.tick(self.api, NOW)
        self.assertEqual(result['status'], 'dispatch_accepted')
        self.assertEqual(result['mode'], 'automatic')
        self.assertEqual(result['new_source_requests'], 0)
        self.assertEqual(result['new_rentcast_calls'], 0)
        self.assertEqual(self.api.dispatched, 1)

    def test_pause_is_read_before_state_and_makes_no_dispatch(self):
        self.api.profile['completionSettings']['enabled'] = False
        self.assertEqual(heartbeat.tick(self.api, NOW)['status'], 'completion_paused')
        self.assertEqual(self.api.reads, ['scan_config.json'])
        self.assertEqual(self.api.dispatched, 0)

    def test_legacy_profile_does_not_start_work(self):
        self.api.profile.pop('scheduleControlVersion')
        self.assertEqual(heartbeat.tick(self.api, NOW)['status'], 'completion_paused')
        self.assertEqual(self.api.dispatched, 0)

    def test_daily_limit_is_shared_with_existing_completed_attempts(self):
        self.api.state['daily_attempted'] = 60
        self.assertEqual(heartbeat.tick(self.api, NOW)['status'], 'not_due_or_daily_limit')
        self.assertEqual(self.api.dispatched, 0)

    def test_user_lowered_daily_limit_is_respected(self):
        self.api.profile['completionSettings']['dailyLimit'] = 20
        self.api.state['daily_attempted'] = 20
        self.assertEqual(heartbeat.tick(self.api, NOW)['status'], 'not_due_or_daily_limit')
        self.assertEqual(self.api.dispatched, 0)

    def test_already_completed_slot_is_not_dispatched_twice(self):
        self.api.state['last_automatic_run'] = '2026-10-18T14:00:01+00:00'
        self.assertEqual(heartbeat.tick(self.api, NOW + timedelta(minutes=4))['status'],
                         'not_due_or_daily_limit')
        self.assertEqual(self.api.dispatched, 0)

    def test_saved_hourly_frequency_is_not_upgraded_to_quarter_hour(self):
        self.api.profile['completionSettings']['frequency'] = 'hourly'
        self.api.state['last_automatic_run'] = '2026-10-18T14:00:01+00:00'
        self.assertEqual(heartbeat.tick(self.api, NOW + timedelta(minutes=45))['status'],
                         'not_due_or_daily_limit')

    def test_daily_budget_reopens_on_saved_timezone_day(self):
        self.api.state.update(budget_day='2026-10-17', daily_attempted=60)
        self.assertEqual(heartbeat.tick(self.api, NOW)['status'], 'dispatch_accepted')

    def test_public_repository_guard_stops_without_reading_settings(self):
        self.api.public = False
        result = heartbeat.tick(self.api, NOW)
        self.assertTrue(result['stop'])
        self.assertEqual(result['status'], 'public_repository_required')
        self.assertEqual(self.api.reads, [])
        self.assertEqual(self.api.dispatched, 0)

    def test_invalid_setting_shapes_do_not_dispatch(self):
        for config in ('invalid', {'enabled': True, 'batchSize': 500}):
            self.api.profile['completionSettings'] = config
            self.assertEqual(heartbeat.tick(self.api, NOW)['status'], 'invalid_saved_schedule')
        self.assertEqual(self.api.dispatched, 0)

    def test_invalid_timezone_does_not_dispatch(self):
        self.api.profile['completionSettings']['timezone'] = 'not/a/timezone'
        self.assertEqual(heartbeat.tick(self.api, NOW)['status'], 'invalid_saved_schedule')
        self.assertEqual(self.api.dispatched, 0)


class SessionTests(unittest.TestCase):
    def session(self, api, duration, clock=None, emit=None):
        clock = clock or Clock()
        events = []
        result = heartbeat.run_session(api, duration=duration, clock=clock.monotonic,
                                       sleep=clock.sleep, now=clock.now,
                                       emit=emit or events.append)
        return result, events, clock

    def test_three_ticks_are_900_seconds_apart_without_live_sleep(self):
        api = FakeApi()
        result, events, clock = self.session(api, 1801)
        self.assertEqual(api.dispatched, 3)
        stamps = [datetime.fromisoformat(e['checked_at']) for e in events]
        self.assertEqual([(s - stamps[0]).total_seconds() for s in stamps], [0, 900, 1800])
        self.assertEqual(result['checks'], 3)
        self.assertFalse(result['stopped'])

    def test_pause_during_session_is_seen_on_next_tick(self):
        api, clock, events = FakeApi(), Clock(), []
        def emit(record):
            events.append(record)
            api.profile['completionSettings']['enabled'] = False
        self.session(api, 1801, clock, emit)
        self.assertEqual(api.dispatched, 1)
        self.assertEqual([e['status'] for e in events],
                         ['dispatch_accepted', 'completion_paused', 'completion_paused'])

    def test_request_time_does_not_accumulate_cadence_drift(self):
        api, clock, times = FakeApi(), Clock(), []
        def dispatch():
            times.append(clock.elapsed)
            clock.elapsed += 40
        api.dispatch = dispatch
        self.session(api, 1801, clock)
        self.assertEqual(times, [0, 900, 1800])

    def test_missed_slots_are_skipped_instead_of_burst_replayed(self):
        api, clock, times = FakeApi(), Clock(), []
        def dispatch():
            times.append(clock.elapsed)
            if len(times) == 1:
                clock.elapsed += 1900
        api.dispatch = dispatch
        self.session(api, 2701, clock)
        self.assertEqual(times, [0, 2700])

    def test_authorization_denial_stops_once_without_repeated_posts(self):
        api = FakeApi()
        api.error = heartbeat.ApiError(403)
        result, events, _ = self.session(api, 1801)
        self.assertTrue(result['stopped'])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'github_api_denied')
        self.assertEqual(api.dispatched, 0)

    def test_transient_api_error_recovers_at_next_tick(self):
        api = FakeApi()
        api.error = heartbeat.ApiError(503)
        result, events, _ = self.session(api, 901)
        self.assertEqual([e['status'] for e in events],
                         ['github_api_unavailable', 'dispatch_accepted'])
        self.assertEqual(api.dispatched, 1)
        self.assertFalse(result['stopped'])

    def test_api_retry_after_is_respected_across_ticks(self):
        api = FakeApi()
        api.error = heartbeat.ApiError(429, 1800)
        _, events, _ = self.session(api, 1801)
        self.assertEqual([e['status'] for e in events],
                         ['github_api_unavailable', 'github_api_cooldown', 'dispatch_accepted'])
        self.assertEqual(api.dispatched, 1)

    def test_timing_is_bounded_and_cannot_accelerate_or_run_past_limit(self):
        for kwargs in ({'duration': 0}, {'duration': heartbeat.SESSION_SECONDS + 1},
                       {'interval': 1}, {'interval': 901}):
            with self.assertRaises(ValueError):
                heartbeat.run_session(FakeApi(), **kwargs)


class Response:
    def __init__(self, body=None, status=200, raw=None):
        self.status = status
        self.data = raw if raw is not None else json.dumps(body).encode()

    def read(self, size):
        return self.data[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ClientTests(unittest.TestCase):
    def client(self, response):
        opener = mock.Mock()
        opener.open.return_value = response
        return heartbeat.GitHub('test-token-never-printed', opener), opener

    def test_dispatch_target_and_mode_are_fixed_to_production_worker(self):
        api, opener = self.client(Response(status=204, raw=b''))
        api.dispatch()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/repos/' +
                         heartbeat.REPOSITORY + '/actions/workflows/' + heartbeat.WORKFLOW + '/dispatches')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'ref': 'main', 'inputs': {'mode': 'automatic'}})
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 30)

    def test_public_cost_guard_requires_expected_repo_default_branch_and_state(self):
        info = {'private': False, 'full_name': heartbeat.REPOSITORY,
                'default_branch': 'main', 'archived': False, 'disabled': False}
        api, _ = self.client(Response(info))
        self.assertTrue(api.public_repository())
        for key, value in [('private', True), ('full_name', 'other/repo'),
                           ('default_branch', 'preview'), ('archived', True), ('disabled', True)]:
            changed = dict(info, **{key: value})
            api, _ = self.client(Response(changed))
            self.assertFalse(api.public_repository())

    def test_state_is_read_from_current_main_not_stale_checkout(self):
        payload = {'type': 'file', 'encoding': 'base64',
                   'content': base64.b64encode(json.dumps(STATE).encode()).decode() + '\n'}
        api, opener = self.client(Response(payload))
        self.assertEqual(api.read_json(heartbeat.STATE), STATE)
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/contents/' + heartbeat.STATE + '?ref=main'))
        self.assertEqual(request.get_method(), 'GET')

    def test_nonallowlisted_file_path_makes_no_request(self):
        api, opener = self.client(Response({}))
        with self.assertRaises(ValueError):
            api.read_json('https://other.example/secret')
        opener.open.assert_not_called()

    def test_missing_or_corrupt_file_content_fails_closed(self):
        for payload in ({}, {'type': 'file', 'encoding': 'base64', 'content': '%%%'}):
            api, _ = self.client(Response(payload))
            with self.assertRaises(heartbeat.ApiError):
                api.read_json('scan_config.json')

    def test_oversized_response_is_rejected(self):
        api, _ = self.client(Response(raw=b' ' * (heartbeat.MAX_RESPONSE + 1)))
        with self.assertRaises(heartbeat.ApiError):
            api.public_repository()

    def test_unexpected_dispatch_response_is_not_reported_as_accepted(self):
        api, _ = self.client(Response({}, status=200))
        with self.assertRaises(heartbeat.ApiError):
            api.dispatch()

    def test_http_failure_does_not_log_sensitive_error_body(self):
        api, opener = self.client(Response({}))
        opener.open.side_effect = urllib.error.HTTPError(
            'https://api.github.com', 401, 'test-token-never-printed', {},
            io.BytesIO(b'test-token-never-printed'))
        with self.assertRaises(heartbeat.ApiError) as caught:
            api.dispatch()
        self.assertTrue(caught.exception.denied)
        self.assertNotIn('test-token-never-printed', str(caught.exception))

    def test_redirects_never_forward_authorization_to_another_host(self):
        handler = heartbeat.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {},
                                                  'https://other.example/path'))

    def test_retry_after_supports_numeric_date_and_rate_reset(self):
        self.assertEqual(heartbeat.retry_seconds({'Retry-After': '120'}, NOW), 120)
        self.assertEqual(heartbeat.retry_seconds({'Retry-After': 'Sun, 18 Oct 2026 14:30:00 GMT'}, NOW), 1800)
        self.assertEqual(heartbeat.retry_seconds(
            {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': str(int(NOW.timestamp()) + 2000)}, NOW), 2000)
        self.assertEqual(heartbeat.retry_seconds({'Retry-After': '999999'}, NOW), 7200)


if __name__ == '__main__':
    unittest.main()
