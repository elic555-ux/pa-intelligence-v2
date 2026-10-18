"""Keep the existing bounded completion worker awake; never fetch listing sources."""
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from scan_schedule import completion_due

REPOSITORY = 'elic555-ux/pa-intelligence-v2'
WORKFLOW = 'automatic-listing-sources.yml'
API = 'https://api.github.com'
STATE = 'COMPS_REPORTS/automatic_listing_sources/state.json'
INTERVAL_SECONDS = 15 * 60
SESSION_SECONDS = 330 * 60  # Finish below GitHub's six-hour hosted-job limit.
MAX_RESPONSE = 2_000_000


class ApiError(Exception):
    def __init__(self, status=0, retry_after=0):
        self.status = int(status)
        self.retry_after = max(0, min(7200, int(retry_after or 0)))
        super().__init__('GitHub API unavailable (HTTP %s)' % self.status)

    @property
    def denied(self):
        return self.status in {401, 403} and not self.retry_after


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def retry_seconds(headers, now=None):
    now = now or datetime.now(timezone.utc)
    value = headers.get('Retry-After', '')
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            seconds = math.ceil((parsedate_to_datetime(value) - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            seconds = 0
    if headers.get('X-RateLimit-Remaining') == '0':
        try:
            seconds = max(seconds, math.ceil(float(headers.get('X-RateLimit-Reset', '0')) - now.timestamp()))
        except (ValueError, TypeError, OverflowError):
            pass
    return max(0, min(7200, seconds))


class GitHub:
    """Fixed repository/endpoints; no arbitrary URLs, redirects or token output."""
    def __init__(self, token, opener=None):
        if not isinstance(token, str) or not token.strip():
            raise ValueError('A repository-scoped GITHUB_TOKEN is required')
        self._token = token
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect())

    def _request(self, suffix, body=None):
        # All suffixes are constants defined by the methods below.
        request = urllib.request.Request(
            API + '/repos/' + REPOSITORY + suffix,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + self._token,
                     'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json',
                     'User-Agent': 'pa-completion-heartbeat/1.0'})
        try:
            with self._opener.open(request, timeout=30) as response:
                status = response.status
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ApiError(status)
                if body is not None:
                    if status != 204:
                        raise ApiError(status)
                    return None
                if status != 200:
                    raise ApiError(status)
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, retry_seconds(exc.headers)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if isinstance(exc, ApiError):
                raise
            raise ApiError() from None

    def public_repository(self):
        info = self._request('')
        return (isinstance(info, dict) and info.get('private') is False
                and info.get('full_name') == REPOSITORY
                and info.get('default_branch') == 'main'
                and info.get('archived') is False and info.get('disabled') is False)

    def read_json(self, path):
        if path not in {'scan_config.json', STATE}:
            raise ValueError('Unsupported heartbeat path')
        payload = self._request('/contents/' + path + '?ref=main')
        if (not isinstance(payload, dict) or payload.get('type') != 'file'
                or payload.get('encoding') != 'base64'
                or not isinstance(payload.get('content'), str)):
            raise ApiError()
        try:
            raw = base64.b64decode(payload['content'].replace('\n', ''), validate=True)
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError):
            raise ApiError() from None

    def dispatch(self):
        self._request('/actions/workflows/' + WORKFLOW + '/dispatches',
                      {'ref': 'main', 'inputs': {'mode': 'automatic'}})


def tick(api, now=None):
    """Re-read current main settings each time; the writer validates them again."""
    now = now or datetime.now(timezone.utc)
    record = {'checked_at': now.isoformat(), 'new_source_requests': 0,
              'new_rentcast_calls': 0}
    # This cost boundary is rechecked during the session, not only at startup.
    if not api.public_repository():
        return dict(record, status='public_repository_required', stop=True)
    profile = api.read_json('scan_config.json')
    config = profile.get('completionSettings') or {}
    if not isinstance(config, dict):
        return dict(record, status='invalid_saved_schedule')
    if profile.get('scheduleControlVersion') != 2 or config.get('enabled') is not True:
        return dict(record, status='completion_paused')
    state = api.read_json(STATE)
    try:
        due = completion_due(profile, state, now)
    except (ValueError, TypeError, KeyError, OverflowError):
        return dict(record, status='invalid_saved_schedule')
    if not due:
        return dict(record, status='not_due_or_daily_limit')
    api.dispatch()
    return dict(record, status='dispatch_accepted', mode='automatic')


def run_session(api, duration=SESSION_SECONDS, interval=INTERVAL_SECONDS,
                clock=time.monotonic, sleep=time.sleep, now=None, emit=None):
    """One dispatcher per workflow concurrency group; no writer lock while waiting."""
    if interval != INTERVAL_SECONDS or not 0 < duration <= SESSION_SECONDS:
        raise ValueError('Invalid heartbeat timing')
    now = now or (lambda: datetime.now(timezone.utc))
    emit = emit or (lambda record: print(json.dumps(record), flush=True))
    start = clock()
    end = start + duration
    target = start
    count = 0
    cooldown_until = start
    while target < end:
        while clock() < target:
            sleep(min(60, target - clock()))
        if clock() >= end:
            break
        try:
            if clock() < cooldown_until:
                record = {'checked_at': now().isoformat(), 'status': 'github_api_cooldown'}
            else:
                record = tick(api, now())
        except ApiError as exc:
            record = {'checked_at': now().isoformat(), 'status': 'github_api_denied' if exc.denied else 'github_api_unavailable',
                      'http_status': exc.status, 'stop': exc.denied}
            cooldown_until = max(cooldown_until, clock() + exc.retry_after)
        count += 1
        # Fixed cadence; skip missed ticks instead of catching up in a burst.
        target = start + (max(count, math.floor((clock() - start) / interval) + 1)) * interval
        record['next_check_in_seconds'] = max(0, round(target - clock())) if target < end else None
        emit(record)
        if record.get('stop'):
            return {'checks': count, 'status': record['status'], 'stopped': True}
    return {'checks': count, 'status': 'session_complete', 'stopped': False}


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
        raise SystemExit('Heartbeat is restricted to the public production repository')
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise SystemExit('Heartbeat only runs from main')
    api = GitHub(os.environ.get('GH_TOKEN', ''))
    def emit(record):
        print(json.dumps(record), flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with Path(summary).open('a', encoding='utf-8') as handle:
                handle.write('- ' + record['checked_at'] + ': ' + record['status'] + '\n')
    result = run_session(api, emit=emit)
    print(json.dumps(result), flush=True)
    if result.get('stopped'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
