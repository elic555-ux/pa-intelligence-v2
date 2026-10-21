"""Independent cache-first completion of saved MLS inventory and review records."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import time
from pathlib import Path

import property_sources as registry
import tarasa_listing_source as source
import listing_photo_fallback as photo_source
from completion_subjects import load_subjects, update_review
from price_watch import watch_ceiling, classify_price
from scan_schedule import completion_due, completion_batch_size, completion_daily_limit, due_slot
from zoneinfo import ZoneInfo
from completion_wakeup import input_fingerprint, idle_unchanged

VERSION = 'automatic-mls-sources-1.12.0-public-gallery-20261020'
STATE = Path('COMPS_REPORTS/automatic_listing_sources/state.json')
STATUS = Path('COMPS_REPORTS/automatic_listing_sources/status.json')
# Property cap, HTTP cap (robots included), worker time in seconds.
PROFILES = {'pilot': (3, 8, 240), 'group10': (10, 13, 240), 'automatic': (10, 13, 240), 'batch': (10, 13, 240), 'scan': (10, 13, 240)}
STOP_BATCH = {'source_blocked', 'source_rate_limited', 'robots_disallowed',
    'source_cooldown', 'source_request_budget_exhausted', 'batch_time_limit',
    'source_redirect_stopped', 'source_unavailable', 'source_response_too_large',
    'robots_delay_exceeds_pilot_limit'}
# A missing public listing follows its saved retry time. An identity conflict
# remains excluded from automatic requests until the binding is reviewed.
REVIEW_STATUSES = {'source_identity_mismatch'}
MAX_UNSUCCESSFUL_ATTEMPTS = 2
TERMINAL_COMPLETION = {'completion_incomplete'}


def fingerprint(row):
    # This binding is deliberately identical to the installed queue version.
    binding = [str(row['id']), source.identity4(row), registry.identity(row)['county'],
               registry.listing_id(row), registry.safe_url(row.get('url'))]
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def published_day(row):
    """Use a saved listing date, never first_seen or last_source_check."""
    value = str(row.get('listed_date') or '').strip()
    try:
        if len(value) == 10 and value[4] == '-' and value[7] == '-':
            return datetime.strptime(value, '%Y-%m-%d').date()
        if '/' in value:
            return datetime.strptime(value, '%d/%m/%Y').date()
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    except (ValueError, TypeError):
        return None


def eligible_rows(repo, start_day=None, today=None, photo_scope=False):
    inventory = load_subjects(repo)
    profile = registry.read_json(repo / 'scan_config.json') if (repo / 'scan_config.json').exists() else {}
    maximum = profile.get('maxPrice')
    ceiling = watch_ceiling(maximum) if maximum is not None else None
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
        elif not photo_scope and registry.norm(row.get('county')).removesuffix(' county') != 'allegheny':
            # The guessed Tarasa detail route returned 404 for current Erie
            # listings. Keep their existing snapshots in the shared registry,
            # but do not spend new Tarasa requests on this unverified route.
            excluded['outside_tarasa_request_scope'] += 1
        elif len(registry.listing_id(row) or '') > 10:
            # This inventory contains 19-digit IDs that cannot be treated as
            # verified broker MLS numbers for a guessed Tarasa detail route.
            excluded['unverified_tarasa_listing_id'] += 1
        elif not registry.identity(row)['complete'] or not registry.listing_id(row) or not registry.safe_url(row.get('url')):
            excluded['incomplete_identity'] += 1
        elif row.get('market_status') not in (None, '', 'active'):
            excluded['unconfirmed_or_inactive_listing'] += 1
        elif ceiling is not None and classify_price(row.get('price'), maximum) == 'historical_outside_watch':
            excluded['above_price_watch_ceiling'] += 1
        elif start_day is not None and published_day(row) is None:
            excluded['listing_date_unknown'] += 1
        elif start_day is not None and published_day(row) < start_day:
            excluded['older_than_completion_scope'] += 1
        elif today is not None and published_day(row) is not None and published_day(row) > today:
            excluded['future_listing_date'] += 1
        else:
            rows[key] = row
    return rows, dict(excluded)


def select_candidates(due, entries, limit, scan_id, now, maximum=None):
    """Reserve an oldest waiting turn when new listings keep arriving."""
    recent, backlog = [], []
    for key in due:
        entry = entries[key]
        queued = source.parse_date(entry.get('first_queued_at')) or now
        (recent if not entry.get('last_attempt_at') and queued >= now else backlog).append(key)
    recent.sort(key=lambda key: (
        1 if maximum is not None and classify_price(due[key].get('price'), maximum) == 'background_watch' else 0,
        0 if scan_id and due[key].get('last_scan_id') == scan_id else 1,
        -(published_day(due[key]).toordinal() if published_day(due[key]) else 0),
        -int(registry.listing_id(due[key]))))
    backlog.sort(key=lambda key: (
        1 if maximum is not None and classify_price(due[key].get('price'), maximum) == 'background_watch' else 0,
        source.parse_date(entries[key].get('last_served_at') or entries[key].get('last_attempt_at')
                          or entries[key].get('first_queued_at')) or now,
        -int(registry.listing_id(due[key]))))
    if recent and backlog:
        # Serve the reserved old turn first, even if a later request stops the run.
        selected = backlog[:1] + recent[:max(0, limit - 1)]
        return (selected + backlog[1:])[:limit]
    return (recent or backlog)[:limit]


def run(repo, mode='check', upstream_sha='', reader=None, now=None, photo_reader=None):
    work_started = time.monotonic()
    repo = Path(repo)
    if mode not in {'check', 'pilot', 'group10', 'batch', 'enable', 'disable', 'automatic', 'scan'}:
        raise ValueError('Unsupported mode.')
    now = now or datetime.now(timezone.utc)
    state = registry.read_json(repo / STATE) if (repo / STATE).exists() else {'schema': 1, 'enabled': False, 'entries': {}}
    if state.get('schema') != 1 or not isinstance(state.get('entries'), dict):
        raise ValueError('Invalid queue state.')
    scan_report = registry.read_json(repo / 'scanner_status.json') if (repo / 'scanner_status.json').exists() else {}
    event = scan_report.get('last_event', {})
    profile = registry.read_json(repo / 'scan_config.json') if (repo / 'scan_config.json').exists() else {}
    controlled = profile.get('scheduleControlVersion') == 2
    completion = profile.get('completionSettings') or {}
    photos_enabled = completion.get('photoFallbackEnabled') is True
    enabled = completion.get('enabled') is True if controlled else bool(state.get('enabled'))
    caps = PROFILES.get(mode, PROFILES['automatic'])
    if controlled:
        caps = (min(caps[0], completion_batch_size(completion)), caps[1], caps[2])
        completion_daily_limit(completion)
    report = {'version': VERSION, 'mode': mode, 'provider': source.PROVIDER, 'source_name': source.SOURCE,
        'checked_at': now.isoformat(),
        'enabled': enabled, 'status': 'ready', 'should_save': False,
        'new_rentcast_calls': 0, 'new_source_network_requests': 0,
        'max_properties_per_run': caps[0], 'max_source_requests_per_run': caps[1], 'max_seconds_per_run': caps[2],
        'properties_sha256': hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest(),
        'scan_id': None, 'scan_integrated': False, 'cached_properties': 0, 'attempted_properties': 0,
        'new_snapshots': 0, 'results': [], 'pending': 0, 'due': 0,
        'review_required': 0, 'migrated_provider_entries': 0,
        'incomplete_properties': 0, 'completion_status': 'not_run',
        'review_updated': 0, 'review_filter_results': {}}
    if mode in {'automatic', 'scan'}:
        if not enabled:
            report['status'] = 'automatic_disabled'
            return report
        if mode == 'automatic' and idle_unchanged(repo, profile, state, now.date(), current=now):
            saved = registry.read_json(repo / STATUS) if (repo / STATUS).exists() else {}
            report.update({key: saved[key] for key in ('pending', 'review_required', 'incomplete_properties', 'completion_status') if key in saved})
            report['cached_properties'] = sum(entry.get('status') == 'cache_used' for entry in state['entries'].values())
            report.update(status='idle_waiting_for_new_properties', worker_state='idle')
            return report
        if mode == 'scan' and upstream_sha and event.get('github_sha') != upstream_sha:
            report['status'] = 'upstream_scan_no_longer_current'
            return report
        if mode == 'scan' and (event.get('status') not in {'success', 'partial'} or 'mls' not in event.get('active_sectors', []) or not event.get('scan_id')):
            report['status'] = 'no_new_mls_scan'
            return report
        if mode == 'scan' and state.get('last_enriched_scan_id') == event.get('scan_id'):
            report['status'] = 'scan_already_processed'
            return report
        if mode == 'automatic' and controlled and not completion_due(profile, state, now):
            report['status'] = 'not_due_or_daily_limit'
            return report
        if (mode == 'scan' and controlled and completion.get('frequency') == 'every_15_minutes'
                and due_slot(completion, now, state.get('last_automatic_run')) is None):
            report['status'] = 'completion_slot_already_processed'
            return report
        scan = event
    else:
        scan = scan_report.get('last_scan', {})
    report['scan_id'] = scan.get('scan_id')
    start_text = completion.get('startDate') if controlled else state.get('completion_start_date')
    if start_text:
        try:
            start_day = datetime.strptime(start_text, '%Y-%m-%d').date()
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid saved completion start date.') from exc
    else:
        start_day = now.date() - timedelta(days=30)
    report['completion_start_date'] = start_day.isoformat()
    report['date_basis'] = 'saved_listing_publication_date'
    rows, excluded = eligible_rows(repo, start_day, now.date())
    report.update(eligible_properties=len(rows), excluded=excluded)
    report['review_properties'] = sum(row.get('_completion_origin') == 'review' for row in rows.values())
    entries = {}
    for key, row in rows.items():
        old = state['entries'].get(key)
        signature = fingerprint(row)
        entry = dict(old) if old and old.get('fingerprint') == signature else {'fingerprint': signature}
        if entry.get('active_provider') != source.PROVIDER:
            # A Clear Choice wait cannot suppress a different provider. Preserve
            # queue membership and activation; recheck caches before new access.
            for field in ('source_url', 'retry_after_at', 'last_attempt_at', 'unsuccessful_attempts'):
                entry.pop(field, None)
            entry['status'] = 'queued'
            report['migrated_provider_entries'] += int(bool(old))
        entry['active_provider'] = source.PROVIDER
        entry.setdefault('first_queued_at', now.isoformat())
        entry.setdefault('unsuccessful_attempts', 0)
        cached = source.read_snapshot(repo, row, now)
        available_cache = bool(cached and (cached.get('facts') or cached.get('photos')))
        settled = available_cache and (entry.get('completion_settled') is True or entry.get('status') == 'cache_used')
        if settled or (source.fresh(cached, now) and available_cache):
            entry.update(status='cache_used', retry_after_at=None, cache_provider=(cached or {}).get('provider', entry.get('cache_provider')),
                         unsuccessful_attempts=0, completion_settled=True)
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
                saved_count = own.get('last_attempt', {}).get('unsuccessful_attempts', 0)
                if type(saved_count) is int:
                    entry['unsuccessful_attempts'] = max(entry['unsuccessful_attempts'], saved_count)
        if (entry['status'] != 'cache_used' and entry['unsuccessful_attempts'] >= MAX_UNSUCCESSFUL_ATTEMPTS):
            entry.update(status='completion_incomplete', retry_after_at=None)
        if source.fresh(cached, now) and not (cached.get('facts') or cached.get('photos')):
            entry.update(status='completion_incomplete', retry_after_at=None, completion_reason='no_source_parameters')
        entries[key] = entry
    state['entries'] = entries
    photo_rows, photo_excluded = eligible_rows(repo, start_day, now.date(), photo_scope=True) if photos_enabled else ({}, {})
    photo_due, photo_pending, next_photo_check = photo_source.queue(repo, photo_rows, now) if photos_enabled else ([], 0, None)
    report.update(photo_eligible_properties=len(photo_rows), photo_excluded=photo_excluded)
    report.update(photo_pending=photo_pending, photo_due=len(photo_due), photo_results=[], verified_photos=0)
    pending = {k: rows[k] for k, e in entries.items()
               if e.get('status') not in REVIEW_STATUSES | TERMINAL_COMPLETION | {'cache_used'}}
    report['review_required'] = sum(e.get('status') in REVIEW_STATUSES for e in entries.values())
    due = {k: row for k, row in pending.items() if not source.parse_date(entries[k].get('retry_after_at'))
           or source.parse_date(entries[k].get('retry_after_at')) <= now}
    report.update(pending=len(pending), due=len(due),
                  incomplete_properties=sum(e.get('status') in TERMINAL_COMPLETION for e in entries.values()))
    if mode == 'check':
        report['status'] = 'check_ok'
        return report
    if mode in {'enable', 'disable'}:
        state['enabled'] = mode == 'enable'
        report.update(enabled=state['enabled'], status='automatic_enabled' if state['enabled'] else 'automatic_disabled')
    elif due:
        remaining = caps[0]
        if controlled:
            budget_day = now.astimezone(ZoneInfo(completion.get('timezone', 'America/New_York'))).date().isoformat()
            used = int(state.get('daily_attempted', 0)) if state.get('budget_day') == budget_day else 0
            remaining = max(0, min(remaining, completion_daily_limit(completion) - used))
            state.update(budget_day=budget_day, daily_attempted=used)
        if photos_enabled and photo_due and remaining > 0:
            remaining -= 1  # reserve one turn for a settled property's missing photos
        provider_state = registry.read_json(repo / source.SOURCE_STATE) if (repo / source.SOURCE_STATE).exists() else {}
        cooldown = source.parse_date(provider_state.get('retry_after_at'))
        if remaining <= 0:
            report['status'] = 'photo_turn_reserved' if photos_enabled and photo_due else 'daily_limit_reached'
        elif cooldown and cooldown > now:
            report.update(status='source_cooldown', retry_after_at=cooldown.isoformat(),
                http_status=provider_state.get('http_status'), request_url=provider_state.get('request_url'))
        else:
            reader = reader or source.PublicReader(max_requests=caps[1])
            reader.max_requests = min(reader.max_requests, caps[1]) if reader.max_requests is not None else caps[1]
            deadline = reader.clock() + caps[2]
            reader.deadline = min(reader.deadline, deadline) if reader.deadline is not None else deadline
            before = reader.requests
            candidates = select_candidates(due, entries, remaining, scan.get('scan_id'), now, profile.get('maxPrice'))
            for key in candidates:
                result = source.run(repo, 'fetch', key, reader=reader, write_status=False, now=now,
                                    include_review=True)
                report['results'].append(result)
                report['attempted_properties'] += int(result['listing_requests'] > 0)
                available = bool(result['cached_facts'] or result['cached_photos'])
                completed = result['status'] in {'published', 'cache_used'} and available
                report['new_snapshots'] += int(result['cache_saved'] and available)
                entries[key].update(status='cache_used' if completed else result['status'],
                    last_attempt_at=now.isoformat(), retry_after_at=result.get('retry_after_at'))
                if completed:
                    entries[key]['unsuccessful_attempts'] = 0
                    entries[key]['completion_settled'] = True
                elif result['listing_requests'] > 0:
                    entries[key]['unsuccessful_attempts'] += 1
                no_parameters = result['status'] in {'published', 'cache_used'} and not available
                if no_parameters or entries[key]['unsuccessful_attempts'] >= MAX_UNSUCCESSFUL_ATTEMPTS:
                    entries[key].update(status='completion_incomplete', retry_after_at=None)
                    if no_parameters:
                        entries[key]['completion_reason'] = 'no_source_parameters'
                    result['completion_status'] = 'not_completed'
                if result['listing_requests'] > 0:
                    entries[key]['last_served_at'] = now.isoformat()
                    own_path = repo / source.ROOT / (source.cache_key(key) + '.json')
                    own = registry.read_json(own_path) if own_path.exists() else None
                    if source.bound(rows[key], own):
                        attempt = own.setdefault('last_attempt', {})
                        attempt.update(unsuccessful_attempts=entries[key]['unsuccessful_attempts'],
                                       checked_at=now.isoformat())
                        if completed:
                            attempt.pop('completion_status', None)
                        elif entries[key]['status'] == 'completion_incomplete':
                            attempt['completion_status'] = 'not_completed'
                        registry.atomic_json(own_path, own)
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
    if photos_enabled and mode not in {'check', 'enable', 'disable'}:
        photo_slots = max(0, caps[0] - len(report['results']))
        if controlled:
            budget_day = now.astimezone(ZoneInfo(completion.get('timezone', 'America/New_York'))).date().isoformat()
            used = int(state.get('daily_attempted', 0)) if state.get('budget_day') == budget_day else 0
            state.update(budget_day=budget_day, daily_attempted=used)
            photo_slots = min(photo_slots, max(0, completion_daily_limit(completion) - used - report['attempted_properties']))
        request_left = max(0, caps[1] - report['new_source_network_requests'])
        seconds_left = max(0, caps[2] - (time.monotonic() - work_started))
        if photo_due and photo_slots and request_left and seconds_left > 25:
            bounded_reader = photo_reader or photo_source.Reader(max_requests=request_left, seconds=seconds_left)
            if hasattr(bounded_reader, 'max_requests'):
                bounded_reader.max_requests = min(bounded_reader.max_requests, bounded_reader.requests + request_left)
            result = photo_source.run(repo, photo_due[:photo_slots], reader=bounded_reader, now=now, save=True)
            report['photo_results'] = result['results']
            report['new_source_network_requests'] += result['new_source_network_requests']
            report['verified_photos'] = sum(len(r.get('photos', [])) for r in result['results'])
            report['attempted_properties'] += sum(r.get('new_source_network_requests', 0) > 0 for r in result['results'])
            if report['status'] in {'queue_current', 'photo_turn_reserved'} and result['results']:
                report['status'] = 'batch_complete'
        photo_due, photo_pending, next_photo_check = photo_source.queue(repo, photo_rows, now)
        report.update(photo_pending=photo_pending, photo_due=len(photo_due))
        state.update(photo_fallback_schema=1, photo_pending=photo_pending,
                     next_photo_check_at=now.isoformat() if photo_due else next_photo_check)
    report['pending'] = sum(e.get('status') not in REVIEW_STATUSES | TERMINAL_COMPLETION | {'cache_used'} for e in entries.values())
    report['review_required'] = sum(e.get('status') in REVIEW_STATUSES for e in entries.values())
    report['incomplete_properties'] = sum(e.get('status') in TERMINAL_COMPLETION for e in entries.values())
    report['completion_status'] = ('partial' if report['incomplete_properties'] and
        (report['cached_properties'] or report['new_snapshots']) else 'not_completed'
        if report['incomplete_properties'] else 'available'
        if report['cached_properties'] or report['new_snapshots'] else 'waiting')
    if controlled:
        state['daily_attempted'] = int(state.get('daily_attempted', 0)) + report['attempted_properties']
    if mode in {'automatic', 'scan'}:
        state['last_automatic_run'] = now.isoformat()
    if mode == 'scan':
        state['last_enriched_scan_id'] = scan.get('scan_id')
    if mode not in {'enable', 'disable'}:
        report['review_updated'], report['review_filter_results'] = update_review(repo, rows, now)
    if mode not in {'enable', 'disable'}:
        idle = report['pending'] == 0 and not photo_due
        if idle:
            report['due'] = 0
        reason = ('waiting_for_photo_retry' if photo_pending else 'no_eligible_properties' if not entries else 'all_subjects_settled') if idle else None
        report.update(worker_state='idle' if idle else 'active', idle_reason=reason)
        state.update(worker_state=report['worker_state'], idle_reason=reason,
                     input_fingerprint=input_fingerprint(repo, profile, now.date()))
    state.update(updated_at=now.isoformat(), last_scan_id=scan.get('scan_id'), active_provider=source.PROVIDER,
                 completion_start_date=start_day.isoformat())
    report['should_save'] = True
    registry.atomic_json(repo / STATE, state)
    registry.atomic_json(repo / STATUS, report)
    return report


def summary(report):
    lines = ['## AUTOMATIC_MLS_SOURCES', '', '- New RentCast calls: **0**',
        '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
        '- מקור מפרט פעיל: Tarasa / River Point Realty',
        '- תמונות שנבדקו מקובץ תמונה בריצה זו: ' + str(report.get('verified_photos', 0)),
        '- נכסים שממתינים להשלמת תמונות ממקורות נוספים: ' + str(report.get('photo_pending', 0)), '- מצב: ' + report['status'],
        '- השלמה בתוך הסריקה: ' + ('כן' if report.get('scan_integrated') else 'לא'),
        '- השלמה מתאריך פרסום: ' + str(report.get('completion_start_date', 'טרם נקבע')),
        '- קבוצה: עד ' + str(report['max_properties_per_run']) + ' נכסים; עד ' + str(report['max_source_requests_per_run']) + ' בקשות למקור בכל ריצה',
        '- תאריך פרסום חסר: ' + str(report.get('excluded', {}).get('listing_date_unknown', 0)),
        '- לפני תחילת ההשלמה: ' + str(report.get('excluded', {}).get('older_than_completion_scope', 0)),
        '- השלמה אוטומטית מופעלת: ' + ('כן' if report['enabled'] else 'לא'),
        '- נכסים שנבדקו מול המקור בריצה: ' + str(report['attempted_properties']),
        '- מטמונים חדשים: ' + str(report['new_snapshots']),
        '- נכסים שהשתמשו במטמון שמור: ' + str(report['cached_properties']),
        '- נכסים שממתינים בתור: ' + str(report['pending']), '',
        '- רשומות בדיקה בתור ההשלמה: ' + str(report.get('review_properties', 0)),
        '- מעל תקרת מעקב המחיר (ללא בקשות השלמה חדשות): ' + str(report.get('excluded', {}).get('above_price_watch_ceiling', 0)),
        '- רשומות בדיקה שהושלמו ועודכנו: ' + str(report.get('review_updated', 0)),
        '- השלמת פרטים שלא הצליחה (בנפרד ממצב הסריקה): ' + str(report.get('incomplete_properties', 0)),
        'עד שני ניסיונות השלמה לא מוצלחים לנכס ולמקור. לאחר מכן עוברים הלאה; אין שינוי במצב הסריקה.',
        '- נכסים שדורשים בדיקת התאמה ידנית לפני ניסיון חוזר: ' + str(report.get('review_required', 0)),
        '- מזהים שאינם מאומתים לנתיב Tarasa: ' + str(report.get('excluded', {}).get('unverified_tarasa_listing_id', 0)),
        'בקשות חדשות ל־Tarasa מוגבלות כעת למחוז Allegheny. מודעות Erie מוחרגות מתור זה בלבד; מפרטים שמורים ממקורות אחרים ממשיכים להיות זמינים.',
        '- נכסי Erie שהוחרגו מתור Tarasa: ' + str(report.get('excluded', {}).get('outside_tarasa_request_scope', 0)),
        'מגבלה בריצה זו: עד ' + str(report['max_properties_per_run']) + ' נכסים ועד ' + str(report['max_source_requests_per_run']) + ' בקשות.',
        'לפחות 10 שניות בין בקשות. אין המתנה של יום בין נכסים; חסימה עוצרת את המקור ומוצגת עם זמן ניסיון נוסף.',
        'התור ממשיך מהמקום שנשמר. מטמוני Clear Choice התקינים נשמרים ונבדקים לפני גישה למקור.',
        'תהליך עצמאי: השלמת רשומות קיימות בלבד, ללא חיפוש נכסים חדשים. מועמדים שהושלמו נשארים בקובץ הבדיקה ונבדקים מול המסננים המקוריים.',
        'properties.json, סטטוס הסריקה, חדר העסקאות ותקציב RentCast נשארים ללא שינוי.', '']
    if report.get('retry_after_at'):
        lines.append('- ניסיון נוסף למקור: ' + report['retry_after_at'] + ' (UTC)')
    if report.get('worker_state') == 'idle':
        lines.append('- השלמת הפרטים בהמתנה לנכסים חדשים או לשינוי נתוני מועמד; אין בקשות מקור. נכסים שטופלו אינם חוזרים לתור בגלל גיל המטמון; עד שני ניסיונות כושלים לנכס ולמקור.')
    if report.get('http_status'):
        lines.append('- HTTP: ' + str(report['http_status']))
    if report.get('request_url'):
        lines.append('- עמוד הבקשה: ' + report['request_url'])
    for item in report['results']:
        lines.append('- ' + item['property_id'] + ': ' + item['status'] + '; שדות ' + str(item['cached_facts']) + '; תמונות ' + str(item['cached_photos']))
    for item in report.get('photo_results', []):
        lines.append('- תמונות ' + item['property_id'] + ': ' + item['status'] + '; זמינות בזמן הבדיקה: ' + str(len(item.get('photos', []))))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'pilot', 'group10', 'batch', 'enable', 'disable', 'automatic', 'scan'))
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


