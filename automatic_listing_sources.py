"""Cache-first, resumable Tarasa completion of the existing MLS scan queue."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import property_sources as registry
import tarasa_listing_source as source

VERSION = 'automatic-mls-sources-1.1.0-20261009'
STATE = Path('COMPS_REPORTS/automatic_listing_sources/state.json')
STATUS = Path('COMPS_REPORTS/automatic_listing_sources/status.json')
# Property cap, HTTP cap (robots included), worker time in seconds.
PROFILES = {'pilot': (3, 8, 240), 'automatic': (25, 30, 600), 'batch': (100, 110, 1500)}
STOP_BATCH = {'source_blocked', 'source_rate_limited', 'robots_disallowed',
    'source_cooldown', 'source_request_budget_exhausted', 'batch_time_limit',
    'source_redirect_stopped', 'source_unavailable', 'source_response_too_large',
    'robots_delay_exceeds_pilot_limit'}


def fingerprint(row):
    # This binding is deliberately identical to the installed queue version.
    binding = [str(row['id']), source.identity4(row), registry.identity(row)['county'],
               registry.listing_id(row), registry.safe_url(row.get('url'))]
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def eligible_rows(repo):
    inventory = registry.read_json(repo / 'properties.json')
    if not isinstance(inventory, list):
        raise ValueError('Inventory must be an array.')
    counts = Counter(str(row.get('id')) for row in inventory if isinstance(row, dict))
    aliases = repo / registry.ROOT / 'aliases.json'
    ambiguous = registry.read_json(aliases).get('ambiguous', {}) if aliases.exists() else {}
    rows, excluded = {}, Counter()
    for row in inventory:
        if not isinstance(row, dict) or row.get('source_type') != 'mls':
            continue
        key = str(row.get('id') or '')
        if not key or counts[key] != 1 or key in ambiguous:
            excluded['ambiguous_identity'] += 1
        elif registry.norm(row.get('county')).removesuffix(' county') not in registry.COUNTIES:
            excluded['outside_initial_source_coverage'] += 1
        elif not registry.identity(row)['complete'] or not registry.listing_id(row) or not registry.safe_url(row.get('url')):
            excluded['incomplete_identity'] += 1
        elif row.get('market_status') not in (None, '', 'active'):
            excluded['unconfirmed_or_inactive_listing'] += 1
        else:
            rows[key] = row
    return rows, dict(excluded)


def run(repo, mode='check', upstream_sha='', reader=None, now=None):
    repo = Path(repo)
    if mode not in {'check', 'pilot', 'batch', 'enable', 'disable', 'automatic'}:
        raise ValueError('Unsupported mode.')
    now = now or datetime.now(timezone.utc)
    state = registry.read_json(repo / STATE) if (repo / STATE).exists() else {'schema': 1, 'enabled': False, 'entries': {}}
    if state.get('schema') != 1 or not isinstance(state.get('entries'), dict):
        raise ValueError('Invalid queue state.')
    scan_report = registry.read_json(repo / 'scanner_status.json') if (repo / 'scanner_status.json').exists() else {}
    event = scan_report.get('last_event', {})
    caps = PROFILES.get(mode, PROFILES['automatic'])
    report = {'version': VERSION, 'mode': mode, 'provider': source.PROVIDER, 'source_name': source.SOURCE,
        'enabled': bool(state.get('enabled')), 'status': 'ready', 'should_save': False,
        'new_rentcast_calls': 0, 'new_source_network_requests': 0,
        'max_properties_per_run': caps[0], 'max_source_requests_per_run': caps[1], 'max_seconds_per_run': caps[2],
        'properties_sha256': hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest(),
        'scan_id': None, 'cached_properties': 0, 'attempted_properties': 0,
        'new_snapshots': 0, 'results': [], 'pending': 0, 'due': 0, 'migrated_provider_entries': 0}
    if mode == 'automatic':
        if not state.get('enabled'):
            report['status'] = 'automatic_disabled'
            return report
        if upstream_sha and event.get('github_sha') != upstream_sha:
            report['status'] = 'upstream_scan_no_longer_current'
            return report
        if event.get('status') not in {'success', 'partial'} or 'mls' not in event.get('active_sectors', []):
            report['status'] = 'no_new_mls_scan'
            return report
        scan = event
    else:
        scan = scan_report.get('last_scan', {})
    report['scan_id'] = scan.get('scan_id')
    rows, excluded = eligible_rows(repo)
    report.update(eligible_properties=len(rows), excluded=excluded)
    entries = {}
    for key, row in rows.items():
        old = state['entries'].get(key)
        is_current = bool(scan.get('scan_id') and row.get('last_scan_id') == scan['scan_id'])
        if not old and not is_current:
            continue
        signature = fingerprint(row)
        entry = dict(old) if old and old.get('fingerprint') == signature else {'fingerprint': signature}
        if entry.get('active_provider') != source.PROVIDER:
            # A Clear Choice wait cannot suppress a different provider. Preserve
            # queue membership and activation; recheck caches before new access.
            for field in ('source_url', 'retry_after_at', 'last_attempt_at'):
                entry.pop(field, None)
            entry['status'] = 'queued'
            report['migrated_provider_entries'] += int(bool(old))
        entry['active_provider'] = source.PROVIDER
        cached = source.read_snapshot(repo, row, now)
        if source.fresh(cached, now):
            entry.update(status='cache_used', retry_after_at=None, cache_provider=cached['provider'])
            report['cached_properties'] += 1
        else:
            if entry.get('status') == 'cache_used':
                entry.update(status='queued', retry_after_at=None)
            entry.setdefault('status', 'queued')
            own_path = repo / source.ROOT / (source.cache_key(key) + '.json')
            own = registry.read_json(own_path) if own_path.exists() else None
            if source.bound(row, own):
                retry = source.parse_date(own.get('last_attempt', {}).get('retry_after_at'))
                if retry and retry > now:
                    entry['retry_after_at'] = retry.isoformat()
                entry['source_url'] = own['source_url']
        entries[key] = entry
    state['entries'] = entries
    pending = {k: rows[k] for k, e in entries.items() if e.get('status') != 'cache_used'}
    due = {k: row for k, row in pending.items() if not source.parse_date(entries[k].get('retry_after_at'))
           or source.parse_date(entries[k].get('retry_after_at')) <= now}
    report.update(pending=len(pending), due=len(due))
    if mode == 'check':
        report['status'] = 'check_ok'
        return report
    if mode in {'enable', 'disable'}:
        state['enabled'] = mode == 'enable'
        report.update(enabled=state['enabled'], status='automatic_enabled' if state['enabled'] else 'automatic_disabled')
    elif due:
        provider_state = registry.read_json(repo / source.SOURCE_STATE) if (repo / source.SOURCE_STATE).exists() else {}
        cooldown = source.parse_date(provider_state.get('retry_after_at'))
        if cooldown and cooldown > now:
            report.update(status='source_cooldown', retry_after_at=cooldown.isoformat(),
                http_status=provider_state.get('http_status'), request_url=provider_state.get('request_url'))
        else:
            reader = reader or source.PublicReader(max_requests=caps[1])
            reader.max_requests = min(reader.max_requests, caps[1]) if reader.max_requests is not None else caps[1]
            deadline = reader.clock() + caps[2]
            reader.deadline = min(reader.deadline, deadline) if reader.deadline is not None else deadline
            before = reader.requests
            # Unattempted listings first, then oldest attempts: missing listings
            # cannot keep taking all slots in subsequent batches.
            candidates = sorted(due, key=lambda k: (entries[k].get('last_attempt_at', ''), -int(registry.listing_id(due[k]))))[:caps[0]]
            for key in candidates:
                result = source.run(repo, 'fetch', key, reader=reader, write_status=False, now=now)
                report['results'].append(result)
                report['attempted_properties'] += int(result['listing_requests'] > 0)
                report['new_snapshots'] += int(result['cache_saved'])
                entries[key].update(status='cache_used' if result['status'] in {'published', 'cache_used'} else result['status'],
                    last_attempt_at=now.isoformat(), retry_after_at=result.get('retry_after_at'))
                if result.get('source_url'):
                    entries[key]['source_url'] = result['source_url']
                if result['status'] in STOP_BATCH:
                    report['status'] = result['status']
                    for field in ('http_status', 'request_url', 'retry_after_at'):
                        if field in result:
                            report[field] = result[field]
                    break
            report['new_source_network_requests'] = reader.requests - before
            if report['status'] == 'ready':
                report['status'] = 'batch_complete'
    else:
        report['status'] = 'waiting_for_retry' if pending else 'queue_current'
    report['pending'] = sum(e.get('status') != 'cache_used' for e in entries.values())
    state.update(updated_at=now.isoformat(), last_scan_id=scan.get('scan_id'), active_provider=source.PROVIDER)
    report['should_save'] = True
    registry.atomic_json(repo / STATE, state)
    registry.atomic_json(repo / STATUS, report)
    return report


def summary(report):
    lines = ['## AUTOMATIC_MLS_SOURCES', '', '- New RentCast calls: **0**',
        '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
        '- מקור פעיל: Tarasa / River Point Realty', '- מצב: ' + report['status'],
        '- השלמה אוטומטית מופעלת: ' + ('כן' if report['enabled'] else 'לא'),
        '- נכסים שנבדקו מול המקור בריצה: ' + str(report['attempted_properties']),
        '- מטמונים חדשים: ' + str(report['new_snapshots']),
        '- נכסים שהשתמשו במטמון שמור: ' + str(report['cached_properties']),
        '- נכסים שממתינים בתור: ' + str(report['pending']), '',
        'מגבלה בריצה זו: עד ' + str(report['max_properties_per_run']) + ' נכסים ועד ' + str(report['max_source_requests_per_run']) + ' בקשות.',
        'לפחות 10 שניות בין בקשות. אין המתנה של יום בין נכסים; חסימה עוצרת את המקור ומוצגת עם זמן ניסיון נוסף.',
        'התור ממשיך מהמקום שנשמר. מטמוני Clear Choice התקינים נשמרים ונבדקים לפני גישה למקור.',
        'הסריקה, כל המחוזות והמסננים, properties.json, חדר העסקאות ותקציב RentCast נשארים ללא שינוי.', '']
    if report.get('retry_after_at'):
        lines.append('- ניסיון נוסף למקור: ' + report['retry_after_at'] + ' (UTC)')
    if report.get('http_status'):
        lines.append('- HTTP: ' + str(report['http_status']))
    if report.get('request_url'):
        lines.append('- עמוד הבקשה: ' + report['request_url'])
    for item in report['results']:
        lines.append('- ' + item['property_id'] + ': ' + item['status'] + '; שדות ' + str(item['cached_facts']) + '; תמונות ' + str(item['cached_photos']))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'pilot', 'batch', 'enable', 'disable', 'automatic'))
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--upstream-sha', default='')
    args = parser.parse_args()
    report = run(args.repo, args.mode, args.upstream_sha)
    print(json.dumps(report, ensure_ascii=False))
    print(summary(report))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write('save=' + ('true' if report['should_save'] else 'false') + '\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(summary(report))


if __name__ == '__main__':
    main()
