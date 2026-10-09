"""Cache-first MLS completion after actual scans; manual pilot before activation."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import property_sources as registry
import secondary_listing_source as source

VERSION = 'automatic-mls-sources-1.0.0-20261009'
STATE = Path('COMPS_REPORTS/automatic_listing_sources/state.json')
STATUS = Path('COMPS_REPORTS/automatic_listing_sources/status.json')
MAX_PROPERTIES = 3
MAX_REQUESTS = 8
MAX_SECONDS = 240
STOP_BATCH = {'source_blocked', 'source_rate_limited', 'robots_disallowed',
              'source_cooldown', 'source_request_budget_exhausted', 'batch_time_limit',
              'source_redirect_stopped', 'source_unavailable'}


def fingerprint(row):
    binding = [str(row['id']), source.identity4(row), registry.identity(row)['county'],
               registry.listing_id(row), registry.safe_url(row.get('url'))]
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def read_snapshot(repo, row):
    path = repo / source.ROOT / (source.cache_key(row['id']) + '.json')
    record = registry.read_json(path) if path.exists() else None
    return record if source.bound(row, record) else None


def fresh(record, now):
    retrieved = source.parse_date((record or {}).get('retrieved_at'))
    return bool(record and record.get('status') == 'published' and retrieved and
                timedelta(0) <= now - retrieved < timedelta(days=source.CACHE_DAYS))


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
    if mode not in {'check', 'pilot', 'enable', 'disable', 'automatic'}:
        raise ValueError('Unsupported mode.')
    now = now or datetime.now(timezone.utc)
    state = registry.read_json(repo / STATE) if (repo / STATE).exists() else {'schema': 1, 'enabled': False, 'entries': {}}
    if state.get('schema') != 1 or not isinstance(state.get('entries'), dict):
        raise ValueError('Invalid queue state.')
    scan_report = registry.read_json(repo / 'scanner_status.json') if (repo / 'scanner_status.json').exists() else {}
    event = scan_report.get('last_event', {})
    report = {'version': VERSION, 'mode': mode, 'enabled': bool(state.get('enabled')),
              'status': 'ready', 'should_save': False, 'new_rentcast_calls': 0, 'new_source_network_requests': 0,
              'max_properties_per_run': MAX_PROPERTIES, 'max_source_requests_per_run': MAX_REQUESTS,
              'properties_sha256': hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest(),
              'scan_id': None, 'cached_properties': 0, 'attempted_properties': 0,
              'new_snapshots': 0, 'results': [], 'pending': 0, 'due': 0}
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
        # New work follows the user's actual scan; existing queued work resumes.
        if not old and not is_current:
            continue
        signature = fingerprint(row)
        entry = dict(old) if old and old.get('fingerprint') == signature else {'fingerprint': signature}
        record = read_snapshot(repo, row)
        if fresh(record, now):
            entry.update(status='cache_used', retry_after_at=None)
            report['cached_properties'] += 1
        else:
            if entry.get('status') == 'cache_used':
                entry.update(status='queued', retry_after_at=None)
            entry.setdefault('status', 'queued')
            retry = source.parse_date((record or {}).get('last_attempt', {}).get('retry_after_at'))
            if retry and retry > now:
                entry['retry_after_at'] = retry.isoformat()
            if record and source.source_url(record.get('source_url')):
                entry['source_url'] = record['source_url']
        entries[key] = entry
    state['entries'] = entries
    pending = {k: rows[k] for k, e in entries.items() if e.get('status') != 'cache_used'}
    due = {k: row for k, row in pending.items()
           if not source.parse_date(entries[k].get('retry_after_at')) or
           source.parse_date(entries[k].get('retry_after_at')) <= now}
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
            report['status'] = 'source_cooldown'
        else:
            reader = reader or source.PublicReader(max_requests=MAX_REQUESTS, deadline=time.monotonic() + MAX_SECONDS)
            known = {k: entries[k].get('source_url') or source.KNOWN_URLS.get(registry.listing_id(row)) for k, row in due.items()}
            known = {k: url for k, url in known.items() if source.source_url(url)}
            try:
                reader.initialize()
                if len(known) < MAX_PROPERTIES:
                    unknown = [row for key, row in due.items() if key not in known]
                    if unknown:
                        discovered, next_page, cycle_complete = source.discover_directory(reader, unknown, state.get('discovery_page', 1))
                        state['discovery_page'] = next_page
                        report['discovery_next_page'] = next_page
                    else:
                        discovered, cycle_complete = {}, False
                    known.update(discovered)
                    for key, url in discovered.items():
                        entries[key]['source_url'] = url
                    for key in due:
                        if key not in known:
                            entries[key].update(status='waiting_for_discovery', retry_after_at=(now + timedelta(days=1)).isoformat() if cycle_complete else None)
                candidates = sorted(known, key=lambda k: (entries[k].get('last_attempt_at', ''), k))[:MAX_PROPERTIES]
                for key in candidates:
                    result = source.run(repo, 'fetch', key, known[key], reader=reader, write_status=False)
                    report['results'].append(result)
                    report['attempted_properties'] += 1
                    report['new_snapshots'] += int(result['cache_saved'])
                    entries[key].update(status='cache_used' if result['status'] in {'published', 'cache_used'} else result['status'],
                        last_attempt_at=now.isoformat(), retry_after_at=None if result['status'] in {'published', 'cache_used', 'source_request_budget_exhausted', 'batch_time_limit'} else (now + timedelta(days=1)).isoformat())
                    if result['status'] in STOP_BATCH:
                        report['status'] = result['status']
                        break
                if report['status'] == 'ready':
                    report['status'] = 'batch_complete' if candidates else 'no_matching_public_links'
            except source.StopSource as error:
                report['status'] = error.status
                if getattr(error, 'reset_directory', False):
                    state['discovery_page'] = 1
                    report['discovery_next_page'] = 1
                if error.status in {'source_blocked', 'source_rate_limited', 'robots_disallowed'}:
                    registry.atomic_json(repo / source.SOURCE_STATE, {'provider': source.PROVIDER, 'status': error.status,
                        'checked_at': now.isoformat(), 'http_status': error.http_status,
                        'retry_after_at': source.cooldown_until(error.retry_after, now)})
            report['new_source_network_requests'] = reader.requests
    else:
        report['status'] = 'waiting_for_retry' if pending else 'queue_current'
    report['pending'] = sum(e.get('status') != 'cache_used' for e in entries.values())
    state.update(updated_at=now.isoformat(), last_scan_id=scan.get('scan_id'))
    report['should_save'] = True
    registry.atomic_json(repo / STATE, state)
    registry.atomic_json(repo / STATUS, report)
    return report


def summary(report):
    lines = ['## AUTOMATIC_MLS_SOURCES', '', '- New RentCast calls: **0**',
             '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
             '- מצב: ' + report['status'], '- השלמה אוטומטית מופעלת: ' + ('כן' if report['enabled'] else 'לא'),
             '- נכסים שנבדקו מול המקור בריצה: ' + str(report['attempted_properties']),
             '- מטמונים חדשים: ' + str(report['new_snapshots']),
             '- נכסים שהשתמשו במטמון שמור: ' + str(report['cached_properties']),
             '- נכסים שממתינים בתור: ' + str(report['pending']), '',
             'עד 3 נכסים ועד 8 בקשות למקור בכל ריצה; לפחות 10 שניות בין בקשות. חסימה עוצרת את ההשלמה.',
             'הסריקה, המחוזות, המסננים, properties.json, חדר העסקאות ותקציב RentCast נשארים ללא שינוי.',
             'החיבור הראשון משתמש במפת המודעות הציבורית של Clear Choice. עד שלושה עמודים בכל ריצה; הריצה הבאה ממשיכה מהמקום שנשמר.', '']
    for item in report['results']:
        lines.append('- ' + item['property_id'] + ': ' + item['status'] + '; שדות ' + str(item['cached_facts']) + '; תמונות ' + str(item['cached_photos']))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'pilot', 'enable', 'disable', 'automatic'))
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
