"""Manual inventory cleanup; preserve scanner configuration and cloud selections."""
import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VERSION = 'inventory-cleanup-1.0.0-20261009'
TARGET_COUNTIES = {'allegheny', 'erie'}
TABLES = ('pipeline_deals', 'user_watchlist', 'archive_deals')
FILES = ('properties.json', 'sheriff_listings.json')
REPORT_ROOT = Path('COMPS_REPORTS/inventory_cleanup')


def read_cloud_protection(repo):
    text = (repo / 'index.html').read_text(encoding='utf-8')
    url_match = re.search(r"const SUPABASE_URL\s*=\s*['\"]([^'\"]+)['\"]", text)
    key_match = re.search(r"const SUPABASE_KEY\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not url_match or not key_match:
        raise ValueError('Cloud connection not found; cleanup stopped.')
    origin, key = url_match.group(1).rstrip('/'), key_match.group(1)
    if origin != 'https://rccncvnybybastdgdoqe.supabase.co':
        raise ValueError('Unexpected cloud project; cleanup stopped.')
    ids, counts = set(), {}
    for table in TABLES:
        offset, rows_read, expected = 0, 0, None
        # Read the same anonymous project/table scope used by the installed app.
        # No credential, notes, or property IDs are printed or included in reports.
        while True:
            selection = 'property_id,deal_data' if table == 'pipeline_deals' else 'property_id'
            req = Request(origin + '/rest/v1/' + table + '?select=' + selection,
                          headers={'apikey': key, 'Authorization': 'Bearer ' + key,
                                   'Range': f'{offset}-{offset + 999}', 'Prefer': 'count=exact'})
            try:
                with urlopen(req, timeout=25) as response:
                    body = response.read(8_000_001)
                    content_range = response.headers.get('Content-Range', '')
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise ValueError(f'Cannot read {table}; cleanup stopped.') from error
            if len(body) > 8_000_000:
                raise ValueError('Cloud response too large; cleanup stopped.')
            values = json.loads(body)
            total = content_range.rsplit('/', 1)[-1]
            if not isinstance(values, list) or not total.isdigit():
                raise ValueError(f'Unverified cloud response for {table}; cleanup stopped.')
            if expected is None:
                expected = int(total)
            if int(total) != expected:
                raise ValueError('Cloud selections changed during reading; run again.')
            for value in values:
                if not isinstance(value, dict) or not value.get('property_id'):
                    raise ValueError('Invalid saved selection; cleanup stopped.')
                ids.add(str(value['property_id']))
                deal = value.get('deal_data')
                if isinstance(deal, dict) and deal.get('id'):
                    ids.add(str(deal['id']))
            rows_read += len(values)
            if rows_read == expected:
                break
            if not values or rows_read > expected:
                raise ValueError('Incomplete cloud selection list; cleanup stopped.')
            offset += len(values)
        counts[table] = rows_read
    return ids, counts


def county_name(row):
    value = row.get('county')
    if value is not None and not isinstance(value, str):
        raise ValueError('Invalid county field; cleanup stopped.')
    return re.sub(r'\s+county$', '', (value or '').strip(), flags=re.I).casefold()


def partition(rows, protected_ids):
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError('Inventory must contain property objects; cleanup stopped.')
    kept, removed, protected_outside, unknown = [], [], 0, 0
    for row in rows:
        county = county_name(row)
        unidentified = county in {'', 'unknown', 'n/a', 'na', 'none', 'not available', 'pennsylvania'}
        saved = str(row.get('id') or '') in protected_ids
        if unidentified or county in TARGET_COUNTIES or saved:
            kept.append(row)
            unknown += int(unidentified)
            protected_outside += int(saved and not unidentified and county not in TARGET_COUNTIES)
        else:
            removed.append(row)
    return kept, removed, {'before': len(rows), 'remove': len(removed), 'after': len(kept),
                           'protected_outside': protected_outside, 'unidentified_kept': unknown}


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(body)
    os.replace(temporary, path)


def run(repo, mode='check', protection_reader=read_cloud_protection):
    repo = Path(repo)
    if mode not in {'check', 'clean'}:
        raise ValueError('Unsupported mode.')
    if not (repo / 'properties.json').is_file():
        raise ValueError('properties.json is missing; cleanup stopped.')
    originals = {name: (repo / name).read_bytes() for name in FILES if (repo / name).is_file()}
    ids, counts = protection_reader(repo)
    plans = {name: partition(json.loads(body), ids) for name, body in originals.items()}
    report = {'version': VERSION, 'mode': mode, 'status': 'preview',
              'new_rentcast_calls': 0, 'new_source_network_requests': 0,
              'cloud_saved_rows': counts, 'files': {name: plan[2] for name, plan in plans.items()},
              'backup_directory': None}
    if mode == 'check':
        return report
    # Refresh protection immediately before applying the one-time change.
    ids, counts = protection_reader(repo)
    plans = {name: partition(json.loads(body), ids) for name, body in originals.items()}
    report['cloud_saved_rows'] = counts
    report['files'] = {name: plan[2] for name, plan in plans.items()}
    for name, body in originals.items():
        if (repo / name).read_bytes() != body:
            raise ValueError('Inventory changed during checking; cleanup stopped.')
    changed = {name: plan for name, plan in plans.items() if plan[1]}
    if not changed:
        report['status'] = 'nothing_to_remove'
        return report
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = REPORT_ROOT / stamp
    report['backup_directory'] = backup.as_posix()
    report['source_sha256'] = {name: hashlib.sha256(body).hexdigest() for name, body in originals.items()}
    # Preserve complete original bytes before replacing any active inventory file.
    for name in changed:
        atomic_write(repo / backup / name, originals[name])
    for name, (kept, removed, stats) in changed.items():
        output = (json.dumps(kept, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        atomic_write(repo / name, output)
    report['status'] = 'cleaned'
    atomic_write(repo / backup / 'summary.json',
                 (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('check', 'clean'), default='check')
    parser.add_argument('--repo', default='.')
    args = parser.parse_args()
    try:
        report = run(Path(args.repo), args.mode)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, 'Cleanup stopped: ' + str(error) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write('## One-time inventory cleanup\n\n')
            stream.write('New RentCast calls: **0**\n\n')
            stream.write('Mode: **' + args.mode + '** — ' + report['status'] + '\n\n')
            for name, stats in report['files'].items():
                stream.write(f"- {name}: {stats['before']} → {stats['after']}; remove {stats['remove']}; protected outside counties {stats['protected_outside']}; unidentified kept {stats['unidentified_kept']}\n")
            if report['backup_directory']:
                stream.write('\nBackup: `' + report['backup_directory'] + '`\n')
            stream.write('\nScanner, counties, filters, workflows, cloud records, source caches and reports are unchanged.\n')


if __name__ == '__main__':
    main()
