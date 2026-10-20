#!/usr/bin/env python3
"""Manual, resumable ErieMoves completion: at most three existing MLS properties."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
import property_sources as registry
import eriemoves_listing_source as source

VERSION = 'erie-listing-pilot-1.0.0-20261010'
STATE = Path('COMPS_REPORTS/erie_listing_pilot/state.json')
STATUS = Path('COMPS_REPORTS/erie_listing_pilot/status.json')
DIRECTORY = source.ORIGIN + '/listings/my-active-listings/page/1'
MAX_PROPERTIES, MAX_PAGES, MAX_REQUESTS, MAX_SECONDS = 3, 3, 7, 240
STOP_BATCH = {'source_blocked', 'source_rate_limited', 'robots_disallowed',
    'source_cooldown', 'source_request_budget_exhausted', 'batch_time_limit',
    'source_redirect_stopped', 'source_unavailable', 'source_response_too_large',
    'robots_delay_exceeds_pilot_limit'}


def fingerprint(row):
    binding = [str(row['id']), source.identity4(row), registry.identity(row)['county'],
               registry.listing_id(row), registry.safe_url(row.get('url'))]
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def eligible_rows(repo):
    inventory = registry.read_json(repo / 'properties.json')
    if not isinstance(inventory, list) or any(not isinstance(r, dict) for r in inventory):
        raise ValueError('invalid_inventory')
    counts = Counter(str(r.get('id') or '') for r in inventory)
    aliases = repo / registry.ROOT / 'aliases.json'
    ambiguous = registry.read_json(aliases).get('ambiguous', {}) if aliases.exists() else {}
    rows, excluded = {}, Counter()
    for row in inventory:
        if row.get('source_type') != 'mls' or registry.identity(row)['county'] != 'Erie':
            continue
        key = str(row.get('id') or '')
        if not key or counts[key] != 1 or key in ambiguous or row.get('_idAmbiguous'):
            excluded['ambiguous_identity'] += 1
        elif not registry.identity(row)['complete'] or not registry.listing_id(row) or not registry.safe_url(row.get('url')):
            excluded['incomplete_identity'] += 1
        elif row.get('market_status') != 'active':
            excluded['unconfirmed_or_inactive_listing'] += 1
        else:
            rows[key] = row
    mls_counts = Counter(registry.listing_id(r) for r in rows.values())
    for key in list(rows):
        if mls_counts[registry.listing_id(rows[key])] != 1:
            rows.pop(key)
            excluded['ambiguous_mls'] += 1
    return rows, dict(excluded)


def address_matches(row, text):
    subject = registry.identity(row)
    text = registry.norm(str(text).replace(',', ' '))
    suffix = re.search(r'\s+' + re.escape(subject['city']) + r'\s+pa\s+' + subject['zip'] + r'$', text)
    return bool(suffix and registry.parse_address(text[:suffix.start()])[:2]
                == (subject['street'], subject['unit']))


def parse_directory(html, rows, page):
    """Only explicit active listing cards select links; detail parsing proves identity."""
    soup = BeautifulSoup(html, 'html.parser')
    canonical = soup.find_all('link', rel='canonical')
    expected_canonical = source.ORIGIN + '/listings/my-active-listings' + ('?page=' + str(page) if page > 1 else '')
    if len(canonical) != 1 or canonical[0].get('href') != expected_canonical:
        raise source.StopSource('source_discovery_page_unavailable')
    cards = soup.select('.singlelisting a.linktooverlay[href]')
    if not cards:
        raise source.StopSource('source_discovery_page_unavailable')
    by_mls = {registry.listing_id(row): row for row in rows.values()}
    found = {}
    for anchor in cards:
        card = anchor.find_parent(class_='singlelisting')
        mls_nodes = card.select('.single-listing-mlsnumber')
        labels = {registry.norm(n.get_text(' ', strip=True)) for n in card.select('.status-label')}
        if len(mls_nodes) != 1 or labels != {'active'}:
            continue
        match = re.fullmatch(r'MLS\s*#\s*(\d+)', mls_nodes[0].get_text(' ', strip=True), re.I)
        row = by_mls.get(match[1]) if match else None
        addresses = card.select('.single-listing-address')
        url = source.source_url(urljoin(DIRECTORY, anchor['href']))
        if (row and url and len(addresses) == 1
                and address_matches(row, addresses[0].get_text(' ', strip=True))
                and registry.erie_url_matches(row, url)):
            found.setdefault(str(row['id']), set()).add(url)
    pages = [page]
    for anchor in soup.find_all('a', href=True):
        link = urlparse(urljoin(DIRECTORY, anchor['href']))
        values = parse_qs(link.query).get('page', [])
        if (link.scheme == 'https' and link.netloc == 'eriemoves.com'
                and link.path in {'/listings/my-active-listings/page/1', '/listings/my-active-listings'}
                and set(parse_qs(link.query)) == {'page'}
                and len(values) == 1 and values[0].isdigit() and 1 <= int(values[0]) <= 100):
            pages.append(int(values[0]))
    complete = page >= max(pages)
    return found, 1 if complete else page + 1, complete


def record_stop(repo, state, report, error, now):
    report.update(status=error.status, http_status=error.http_status,
                  request_url=getattr(error, 'request_url', None))
    if error.status in source.GLOBAL_BLOCKS:
        retry = source.cooldown_until(error.retry_after, now)
        report['retry_after_at'] = retry
        registry.atomic_json(repo / source.SOURCE_STATE, {'provider': source.PROVIDER,
            'status': error.status, 'checked_at': now.isoformat(), 'retry_after_at': retry,
            'http_status': error.http_status, 'request_url': report['request_url']})
    elif error.status not in source.NO_FAILURE_RECEIPT:
        retry = (now + timedelta(days=1)).isoformat()
        state['discovery_retry_after_at'] = retry
        report['retry_after_at'] = retry


def run(repo, mode='check', reader=None, now=None):
    repo = Path(repo)
    if mode not in {'check', 'pilot'}:
        raise ValueError('unsupported_mode')
    now = now or datetime.now(timezone.utc)
    state = registry.read_json(repo / STATE) if (repo / STATE).exists() else {'schema': 1, 'entries': {}, 'discovery_page': 1}
    if (state.get('schema') != 1 or not isinstance(state.get('entries'), dict)
            or not isinstance(state.get('discovery_page'), int) or not 1 <= state['discovery_page'] <= 100):
        raise ValueError('invalid_queue_state')
    rows, excluded = eligible_rows(repo)
    report = {'version': VERSION, 'mode': mode, 'provider': source.PROVIDER,
        'status': 'check_ok', 'should_save': False, 'automatic_enabled': False,
        'new_rentcast_calls': 0, 'new_source_network_requests': 0,
        'max_properties_per_run': MAX_PROPERTIES, 'max_directory_pages_per_run': MAX_PAGES,
        'max_source_requests_per_run': MAX_REQUESTS, 'max_seconds_per_run': MAX_SECONDS,
        'properties_sha256': hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest(),
        'eligible_properties': len(rows), 'excluded': excluded, 'cached_properties': 0,
        'attempted_properties': 0, 'new_snapshots': 0, 'directory_pages': 0,
        'discovered_links': 0, 'ambiguous_links': 0, 'results': [],
        'coverage': 'Only active listings present in the public ErieMoves agency directory.'}
    entries = {}
    for key, row in rows.items():
        signature = fingerprint(row)
        old = state['entries'].get(key)
        entry = dict(old) if isinstance(old, dict) and old.get('fingerprint') == signature else {'fingerprint': signature}
        cached = source.read_snapshot(repo, row, now)
        if source.fresh(cached, now):
            entry.update(status='cache_used', retry_after_at=None)
            report['cached_properties'] += 1
        else:
            if entry.get('status') == 'cache_used':
                entry.update(status='queued', retry_after_at=None)
            entry.setdefault('status', 'queued')
            own_path = repo / source.ROOT / (source.cache_key(key) + '.json')
            own = registry.read_json(own_path) if own_path.exists() else None
            if source.bound(row, own):
                entry['source_url'] = own['source_url']
                retry = source.parse_date(own.get('last_attempt', {}).get('retry_after_at'))
                if retry and retry > now:
                    entry['retry_after_at'] = retry.isoformat()
            if entry.get('source_url') and not registry.erie_url_matches(row, entry['source_url']):
                entry.pop('source_url')
        entries[key] = entry
    state['entries'] = entries
    pending = {k: rows[k] for k, e in entries.items() if e['status'] != 'cache_used'}
    due = {k: row for k, row in pending.items() if not source.parse_date(entries[k].get('retry_after_at'))
           or source.parse_date(entries[k]['retry_after_at']) <= now}
    report.update(pending=len(pending), due=len(due), next_discovery_page=state['discovery_page'])
    if mode == 'check':
        return report
    report['status'] = 'cache_current' if not pending else 'waiting_for_retry'
    provider_state = registry.read_json(repo / source.SOURCE_STATE) if (repo / source.SOURCE_STATE).exists() else {}
    cooldown = source.parse_date(provider_state.get('retry_after_at'))
    if due and cooldown and cooldown > now:
        report.update(status='source_cooldown', retry_after_at=cooldown.isoformat())
    elif due:
        reader = reader or source.PublicReader(max_requests=MAX_REQUESTS)
        allowed = reader.requests + MAX_REQUESTS
        reader.max_requests = min(reader.max_requests, allowed) if reader.max_requests is not None else allowed
        deadline = reader.clock() + MAX_SECONDS
        reader.deadline = min(reader.deadline, deadline) if reader.deadline is not None else deadline
        before = reader.requests
        try:
            reader.initialize()
            unresolved = {k: row for k, row in due.items() if not entries[k].get('source_url')}
            retry = source.parse_date(state.get('discovery_retry_after_at'))
            ready_links = sum(bool(entries[k].get('source_url')) for k in due)
            if unresolved and ready_links < MAX_PROPERTIES and (not retry or retry <= now):
                found = {}
                for _ in range(MAX_PAGES):
                    page = state['discovery_page']
                    url = DIRECTORY + ('?page=' + str(page) if page > 1 else '')
                    links, next_page, complete = parse_directory(reader.get(url), unresolved, page)
                    report['directory_pages'] += 1
                    for key, urls in links.items():
                        found.setdefault(key, set()).update(urls)
                        if len(found[key]) == 1:
                            entries[key]['source_url'] = next(iter(found[key]))
                            entries[key]['status'] = 'link_found'
                        else:
                            entries[key].pop('source_url', None)
                            entries[key].update(status='ambiguous_source_links',
                                retry_after_at=(now + timedelta(days=1)).isoformat())
                    report['discovered_links'] = sum(len(urls) == 1 for urls in found.values())
                    report['ambiguous_links'] = sum(len(urls) > 1 for urls in found.values())
                    state['discovery_page'] = next_page
                    if complete:
                        state['discovery_retry_after_at'] = (now + timedelta(days=1)).isoformat()
                        break
                    if sum(bool(entries[k].get('source_url')) for k in due) >= MAX_PROPERTIES:
                        break
            candidates = sorted((k for k in due if entries[k].get('source_url')),
                key=lambda k: (entries[k].get('last_attempt_at', ''), -int(registry.listing_id(rows[k]))))[:MAX_PROPERTIES]
            for key in candidates:
                result = source.run(repo, 'fetch', key, requested_url=entries[key]['source_url'],
                    reader=reader, write_status=False, now=now)
                report['results'].append(result)
                report['attempted_properties'] += int(result['listing_requests'] > 0)
                report['new_snapshots'] += int(result['cache_saved'])
                entries[key].update(status='cache_used' if result['status'] in {'published', 'cache_used'} else result['status'],
                    last_attempt_at=now.isoformat(), retry_after_at=result.get('retry_after_at'))
                if result['status'] in STOP_BATCH:
                    report['status'] = result['status']
                    for field in ('http_status', 'request_url', 'retry_after_at'):
                        if field in result:
                            report[field] = result[field]
                    break
            else:
                report['status'] = 'pilot_complete' if candidates else 'no_matching_links_in_checked_pages'
        except source.StopSource as error:
            record_stop(repo, state, report, error, now)
        report['new_source_network_requests'] = reader.requests - before
    report.update(pending=sum(e['status'] != 'cache_used' for e in entries.values()),
                  next_discovery_page=state['discovery_page'], should_save=True)
    state['updated_at'] = now.isoformat()
    registry.atomic_json(repo / STATE, state)
    registry.atomic_json(repo / STATUS, report)
    return report


def summary(report):
    labels = {'check_ok': 'בדיקה תקינה של הנתונים השמורים', 'pilot_complete': 'קבוצת הניסוי הושלמה',
        'cache_current': 'כל הפרטים שבתור כבר שמורים במטמון טרי', 'waiting_for_retry': 'ממתין למועד ניסיון נוסף',
        'source_cooldown': 'המקור בהמתנה לאחר בקשה קודמת', 'source_blocked': 'המקור חסם את הבקשה',
        'source_rate_limited': 'המקור ביקש להמתין', 'robots_disallowed': 'המקור אינו מתיר גישה אוטומטית לעמוד',
        'source_request_budget_exhausted': 'הושגה מגבלת הבקשות בריצה', 'batch_time_limit': 'הושגה מגבלת הזמן בריצה',
        'source_redirect_stopped': 'המקור הפנה לעמוד אחר; הבקשה נעצרה', 'source_unavailable': 'המקור אינו זמין',
        'source_response_too_large': 'עמוד המקור חרג ממגבלת הגודל',
        'robots_delay_exceeds_pilot_limit': 'זמן ההמתנה שדורש המקור חורג ממגבלת הניסוי',
        'source_discovery_page_unavailable': 'עמוד הרשימה אינו זמין או אינו תואם לעמוד המבוקש',
        'no_matching_links_in_checked_pages': 'לא נמצאו קישורים תואמים בדפים שנבדקו בריצה זו',
        'published': 'נשמר מפרט חדש', 'cache_used': 'נעשה שימוש במפרט שמור',
        'source_identity_mismatch': 'זהות המודעה לא תאמה לנכס; לא צורפו פרטים',
        'listing_not_available': 'המודעה אינה זמינה'}
    lines = ['## השלמת פרטי Erie — ניסוי ידני', '',
        '- מצב: ' + labels.get(report['status'], report['status']), '- הפעלה אוטומטית: לא',
        '- פניות חדשות ל־RentCast: 0',
        '- בקשות חדשות לאתר המקור: ' + str(report['new_source_network_requests']),
        '- דפי רשימה שנבדקו: ' + str(report['directory_pages']),
        '- נכסים עם מטמון טרי: ' + str(report['cached_properties']),
        '- קישורי מועמדים שאותרו: ' + str(report['discovered_links']),
        '- נכסים שנבדקו מול עמוד מודעה: ' + str(report['attempted_properties']),
        '- מפרטים חדשים שנשמרו בריצה: ' + str(report['new_snapshots']),
        '- נכסים שנותרו בתור: ' + str(report['pending']),
        '- דף איתור להתחלת הריצה הבאה: ' + str(report['next_discovery_page']), '',
        'עד 3 נכסים, 3 דפי רשימה ו־7 בקשות HTTP בריצה; לפחות 10 שניות בין בקשות.',
        'הכיסוי מוגבל למודעות הפעילות ברשימה הציבורית של ErieMoves. נכס שלא נמצא אינו נחשב לנכס שאין לו מודעה.',
        'מספר MLS, כתובת, עיר, מיקוד ומחוז מאומתים שוב בעמוד המודעה לפני שמירת פרטים.',
        'מצב published מציין שמירה מקומית; נתונים נשמרים במאגר רק לאחר הצלחת שלב Save Erie pilot.', '']
    if report.get('retry_after_at'):
        lines.append('- זמן ניסיון נוסף: ' + report['retry_after_at'] + ' (UTC)')
    for result in report['results']:
        lines.append('- ' + result['property_id'] + ': ' + labels.get(result['status'], result['status']) +
            '; שדות ' + str(result['cached_facts']) + '; תמונות ' + str(result['cached_photos']))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'pilot'))
    parser.add_argument('--repo', type=Path, default=Path('.'))
    args = parser.parse_args()
    report = run(args.repo, args.mode)
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
