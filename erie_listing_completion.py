#!/usr/bin/env python3
"""One manual launch; bounded Erie groups with optional GitHub checkpoints."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import erie_listing_pilot as pilot

VERSION = 'erie-listing-completion-1.0.0-20261010'
SESSION = pilot.STATE.parent / 'completion.json'
MAX_GROUPS, MAX_REQUESTS, MAX_SECONDS = 10, 40, 600
CONTINUE = {'pilot_complete', 'no_matching_links_in_checked_pages'}


def save_checkpoint(repo, expected_inventory_sha256):
    """Save only source evidence; never merge, force-push or overwrite inventory."""
    repo = Path(repo)
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, check=True,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60).stdout.strip()
    if hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest() != expected_inventory_sha256:
        raise RuntimeError('inventory_changed')
    git('fetch', 'origin', 'main')
    if git('rev-parse', 'HEAD') != git('rev-parse', 'origin/main'):
        raise RuntimeError('repository_changed')
    data, manifest = pilot.registry.prepare(repo)
    pilot.registry.save(repo, data, manifest)
    if hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest() != expected_inventory_sha256:
        raise RuntimeError('inventory_changed')
    _, current = pilot.registry.prepare(repo)
    if any(current[k] != manifest[k] for k in ('properties_sha256', 'input_fingerprint')):
        raise RuntimeError('registry_inputs_changed')
    roots = ['COMPS_REPORTS/erie_listing_pilot', 'COMPS_REPORTS/property_sources']
    if (repo / 'COMPS_REPORTS/additional_sources').is_dir():
        roots.append('COMPS_REPORTS/additional_sources')
    git('add', '--', *roots)
    staged = git('diff', '--cached', '--name-only').splitlines()
    if any(not any(name.startswith(root + '/') for root in roots) for name in staged):
        raise RuntimeError('unexpected_staged_files')
    if not staged:
        return
    git('commit', '-m', 'Save ErieMoves completion checkpoint')
    git('push', 'origin', 'HEAD:main')


def run(repo, reader=None, now=None, checkpoint=None):
    repo = Path(repo)
    now = now or datetime.now(timezone.utc)
    initial = pilot.run(repo, 'check', now=now)
    report = {**initial, 'version': VERSION, 'mode': 'complete', 'status': 'running',
        'max_groups_per_run': MAX_GROUPS, 'max_properties_per_run': MAX_GROUPS * pilot.MAX_PROPERTIES,
        'max_source_requests_per_run': MAX_REQUESTS, 'max_seconds_per_run': MAX_SECONDS,
        'groups_completed': 0, 'checkpoints_saved': 0, 'checkpoint_enabled': checkpoint is not None,
        'should_save': False, 'results': [], 'groups': []}
    reader = reader or pilot.source.PublicReader(max_requests=MAX_REQUESTS)
    base_requests = reader.requests
    # Caller limits remain binding; per-group limits are renewed, never global limits.
    request_limit = min(base_requests + MAX_REQUESTS, reader.max_requests) if reader.max_requests is not None else base_requests + MAX_REQUESTS
    deadline = min(reader.clock() + MAX_SECONDS, reader.deadline) if reader.deadline is not None else reader.clock() + MAX_SECONDS

    def persist():
        report['new_source_network_requests'] = reader.requests - base_requests
        report['should_save'] = True
        pilot.registry.atomic_json(repo / SESSION, report)

    def checkpoint_now():
        if checkpoint is None:
            return True
        report['checkpoints_saved'] += 1
        persist()
        try:
            checkpoint(repo, report['properties_sha256'])
            return True
        except Exception as error:
            report['checkpoints_saved'] -= 1
            report['status'] = 'checkpoint_failed'
            report['error_type'] = type(error).__name__
            persist()
            return False

    for _ in range(MAX_GROUPS):
        if reader.requests >= request_limit:
            report['status'] = 'source_request_budget_exhausted'
            break
        if reader.clock() + 25 > deadline:
            report['status'] = 'batch_time_limit'
            break
        before = pilot.registry.read_json(repo / pilot.STATE) if (repo / pilot.STATE).exists() else {'discovery_page': 1}
        reader.max_requests = min(request_limit, reader.requests + pilot.MAX_REQUESTS)
        reader.deadline = min(deadline, reader.clock() + pilot.MAX_SECONDS)
        try:
            group = pilot.run(repo, 'pilot', reader=reader, now=now)
        except Exception as error:
            report.update(status='completion_failed', error_type=type(error).__name__)
            break
        report['groups_completed'] += 1
        report['groups'].append({k: group[k] for k in ('status', 'directory_pages', 'new_snapshots', 'new_source_network_requests', 'next_discovery_page')})
        for key in ('directory_pages', 'attempted_properties', 'new_snapshots', 'discovered_links', 'ambiguous_links'):
            report[key] += group[key]
        report['results'].extend(group['results'])
        for key in ('pending', 'due', 'next_discovery_page'):
            report[key] = group[key]
        for key in ('retry_after_at', 'http_status', 'request_url'):
            if key in group:
                report[key] = group[key]
        report['status'] = group['status']
        state = pilot.registry.read_json(repo / pilot.STATE)
        ready = any(e.get('source_url') and e.get('status') != 'cache_used'
            and (not pilot.source.parse_date(e.get('retry_after_at')) or pilot.source.parse_date(e['retry_after_at']) <= now)
            for e in state['entries'].values())
        discovery_wait = pilot.source.parse_date(state.get('discovery_retry_after_at'))
        if group['pending'] == 0:
            report['status'] = 'cache_current'
        elif group['status'] in CONTINUE and discovery_wait and discovery_wait > now and not ready:
            report['status'] = 'directory_pass_complete'
        elif group['status'] in CONTINUE and not (group['directory_pages'] or group['attempted_properties'] or before['discovery_page'] != state['discovery_page']):
            report['status'] = 'waiting_for_retry'
        persist()
        print('Erie group ' + str(report['groups_completed']) + ': ' + group['status'] +
            '; new=' + str(group['new_snapshots']) + '; next_page=' + str(group['next_discovery_page']), flush=True)
        if not checkpoint_now():
            return report
        if report['status'] not in CONTINUE:
            break
    else:
        report['status'] = 'group_limit_reached' if report['status'] in CONTINUE else report['status']
    persist()
    # Record the final stop reason as well as the earlier successful groups.
    checkpoint_now()
    return report


def summary(report):
    labels = {'directory_pass_complete': 'מעבר רשימת המקור הושלם; נכסים נוספים עשויים להימצא במקורות אחרים',
        'group_limit_reached': 'הושגה מגבלת הקבוצות; נקודת ההמשך נשמרה',
        'checkpoint_failed': 'השמירה ב־GitHub נכשלה; ההשלמה נעצרה',
        'completion_failed': 'אירעה שגיאה; ההשלמה נעצרה',
        'source_request_budget_exhausted': 'הושגה מגבלת הבקשות; נקודת ההמשך נשמרה',
        'batch_time_limit': 'הושגה מגבלת הזמן; נקודת ההמשך נשמרה',
        'cache_current': 'כל המפרטים שבתור טריים', 'waiting_for_retry': 'ממתין למועד ניסיון נוסף',
        'source_blocked': 'המקור חסם את הבקשה', 'source_rate_limited': 'המקור ביקש להמתין',
        'source_cooldown': 'המקור בהמתנה', 'robots_disallowed': 'המקור אינו מתיר גישה אוטומטית',
        'source_discovery_page_unavailable': 'עמוד הרשימה אינו זמין או אינו תואם',
        'source_redirect_stopped': 'המקור הפנה לעמוד אחר; ההשלמה נעצרה',
        'source_unavailable': 'המקור אינו זמין',
        'source_response_too_large': 'עמוד המקור חרג ממגבלת הגודל',
        'robots_delay_exceeds_pilot_limit': 'ההמתנה שדורש המקור חורגת ממגבלת הקבוצה'}
    lines = ['## השלמת Erie — הפעלה אחת', '', '- מצב: ' + labels.get(report['status'], report['status']),
        '- הפעלה מתוזמנת: לא; המשך בין קבוצות: כן', '- פניות ל־RentCast: 0',
        '- קבוצות שהושלמו: ' + str(report['groups_completed']),
        '- שמירות GitHub שהצליחו: ' + str(report['checkpoints_saved']),
        '- בקשות חדשות למקור: ' + str(report['new_source_network_requests']),
        '- מפרטים חדשים: ' + str(report['new_snapshots']),
        '- נכסים שנותרו בתור: ' + str(report['pending']),
        '- דף איתור להמשך: ' + str(report['next_discovery_page']), '',
        'עד 10 קבוצות של 3 נכסים ו־40 בקשות למקור, בחלון השלמה של 10 דקות (שלבי בדיקה ושמירה עשויים להאריך את משך הפעולה). לפחות 10 שניות בין בקשות גם במעבר בין קבוצות.',
        'כל קבוצה נשמרת ב־GitHub לפני המשך הקבוצה הבאה. כשל שמירה עוצר את ההשלמה.',
        'הכיסוי מוגבל לרשימת המודעות הפעילות הציבורית של ErieMoves. נכס שלא נמצא אינו נחשב לנכס ללא מודעה.', '']
    if report.get('retry_after_at'):
        lines.append('- ניסיון נוסף החל מ־' + report['retry_after_at'] + ' (UTC)')
    for item in report['results']:
        lines.append('- ' + item['property_id'] + ': ' + item['status'] + '; שדות ' + str(item['cached_facts']) + '; תמונות ' + str(item['cached_photos']))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--checkpoint-git', action='store_true')
    args = parser.parse_args()
    report = run(args.repo, checkpoint=save_checkpoint if args.checkpoint_git else None)
    print(json.dumps(report, ensure_ascii=False))
    print(summary(report))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write('save=' + ('false' if args.checkpoint_git else 'true') + '\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(summary(report))
    if report['status'] in {'checkpoint_failed', 'completion_failed'}:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
