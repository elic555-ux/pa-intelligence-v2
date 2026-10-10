#!/usr/bin/env python3
"""Cache-first public ErieMoves enrichment of one existing MLS listing.

Uses an explicit public Erie listing URL, then independently checks the primary listing,
full address, county and current inventory binding. No search directory, paid
API, browser challenge, inventory change or deal-room write is used.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import property_sources as registry
import secondary_listing_source as core
import erie_source_access_check as identity_check

VERSION = 'eriemoves-source-1.0.0-20261010'
PROVIDER = 'eriemoves'
ORIGIN, SOURCE, _ = core.PROVIDERS[PROVIDER]
ROOT = Path('COMPS_REPORTS/additional_sources/eriemoves')
SOURCE_STATE = Path('COMPS_REPORTS/additional_sources/eriemoves_state.json')
STATUS = core.STATUS
CACHE_DAYS = core.CACHE_DAYS
GLOBAL_BLOCKS = {'source_blocked', 'source_rate_limited', 'robots_disallowed'}
NO_FAILURE_RECEIPT = {'source_request_budget_exhausted', 'batch_time_limit'}
StopSource = core.StopSource
parse_date, identity4, cache_key, cooldown_until = core.parse_date, core.identity4, core.cache_key, core.cooldown_until


def source_url(value):
    return core.source_url(value, PROVIDER)


def bound(row, record):
    return core.bound(row, record, PROVIDER)


KNOWN_URLS = {'193473': 'https://eriemoves.com/listing/PA/Erie/4117-Maxwell-Avenue-16504/233972876'}


def target_url(row):
    return KNOWN_URLS.get(registry.listing_id(row), '')


def parse_detail(html, row, requested_url, checked_at=None):
    url = source_url(requested_url)
    if not url or not registry.erie_url_matches(row, url):
        raise ValueError('source_identity_mismatch: unsupported Erie source')
    try:
        identity_check.inspect_page(html, {'provider': PROVIDER, 'url': url}, row)
    except identity_check.Stop as error:
        raise ValueError('source_identity_mismatch: ' + error.status) from error
    soup = BeautifulSoup(html, 'html.parser')
    checked_at = checked_at or datetime.now(timezone.utc).isoformat()
    mls = registry.listing_id(row)
    facts = {}
    def put(field, value, label, unit=None):
        if registry.clean_value(value) is not None:
            facts[field] = {'value': value, 'unit': unit, 'source': SOURCE, 'source_url': url,
                'listing_id': mls, 'property_id': str(row['id']), 'checked_at': checked_at,
                'source_as_of': None, 'method': 'public_listing_label',
                'status': 'reported_by_source', 'label': label}
    cells = {}
    for cell in soup.select('.listing-spec-table .spec-cell'):
        label = cell.find('strong')
        if label:
            key = ' '.join(label.get_text(' ', strip=True).split()).rstrip(':')
            label.extract()
            if key in cells:
                raise ValueError('source_identity_mismatch: duplicate technical label')
            cells[key] = cell.get_text(' ', strip=True)
    for label, field in [('Year Built', 'year_built'), ('Style', 'style')]:
        if label in cells:
            put(field, core.numeric(cells[label]) if field == 'year_built' else cells[label], label)
    lot = re.fullmatch(r'([\d,]+(?:\.\d+)?)\s+(SQFT|Acres?)', cells.get('Lot Size', ''), re.I)
    if lot and lot[2].lower().startswith('acre'):
        put('lot_area_acres', core.numeric(lot[1]), 'Lot Size', 'acre')
    # Square-foot lot measurements stay separate; never overwrite inventory lot size.
    def items(identifier):
        section = soup.select('#detail_feature_container #' + identifier)
        if len(section) > 1:
            raise ValueError('source_identity_mismatch: duplicate technical section')
        return [' '.join(n.get_text(' ', strip=True).split()) for n in section[0].find_all('li')] if section else []
    for identifier, field in [('ld_basement','basement'), ('ld_parking','parking')]:
        values = items(identifier)
        if values: put(field, ', '.join(values), identifier)
    stories = items('ld_stories')
    if len(stories) == 1: put('stories', core.numeric(stories[0]), 'Stories')
    living = items('ld_approx_living_area')
    if len(living) == 1:
        match = re.fullmatch(r'([\d,]+)\s+sqft', living[0], re.I)
        if match: put('sqft', core.numeric(match[1]), 'Living Area', 'sqft')
    for value in items('ld_ext_feat'):
        if value.startswith('Roof:'): put('roof_type', value.partition(':')[2].strip(), 'Roof')
    for value in items('ld_util_info'):
        if value.startswith('Utilities: Water Source:'): put('water', value.split('Water Source:',1)[1].strip(), 'Water Source')
        if value.startswith('Sewer:'): put('sewer', value.partition(':')[2].strip(), 'Sewer')
    hvac = items('ld_heat_cool')
    if hvac:
        # Combined source heading does not always distinguish heat from cooling.
        put('hvac_type', ', '.join(hvac), 'Heating and Cooling')
        cooling = [v for v in hvac if v.lower() in {'central air','window unit(s)','wall unit(s)'}]
        heating = [v for v in hvac if v.lower() in {'forced air','gas','electric','hot water','baseboard','oil'}]
        if cooling: put('cooling', ', '.join(cooling), 'Heating and Cooling')
        if heating: put('heating', ', '.join(heating), 'Heating and Cooling')
    graph=[]
    for script in soup.find_all('script', type='application/ld+json'):
        try: data=json.loads(script.get_text())
        except (ValueError,TypeError): continue
        if isinstance(data,dict) and isinstance(data.get('@graph'),list): graph.extend(data['@graph'])
    listing=next(n for n in graph if isinstance(n,dict) and n.get('@type')=='RealEstateListing' and n.get('url')==url)
    if 'greater erie board of realtors' not in registry.norm(listing.get('creditText')):
        raise ValueError('source_identity_mismatch: MLS board provenance')
    home=next(n for n in graph if isinstance(n,dict) and n.get('@type')=='House' and n.get('@id')==url+'/#listingdata')
    photos=[]; seen=set()
    for image in home.get('image',[]):
        photo=registry.safe_url(image.get('url')) if isinstance(image,dict) else None
        if not registry.moxi_photo_url(photo) or photo in seen: continue
        seen.add(photo)
        photos.append({'url':photo,'source':SOURCE,'source_url':url,'listing_id':mls,
            'retrieved_at':checked_at,'capture_date':None})
        if len(photos)==3: break
    if not facts:
        raise ValueError('source_unavailable: no documented technical facts')
    return {'schema':1,'version':VERSION,'provider':PROVIDER,'property_id':str(row['id']),
        'listing_id':mls,'identity':identity4(row),'subject':registry.identity(row),
        'inventory_source_url':registry.safe_url(row.get('url')),'source_url':url,'source_name':SOURCE,
        'status':'published','method':'public_listing_labels_and_jsonld_identity',
        'retrieved_at':checked_at,'source_updated_at':None,'facts':facts,'photos':photos,
        'missing_fields':['occupancy','roof_condition'], 'html_sha256':hashlib.sha256(html.encode()).hexdigest()}


class PublicReader(core.PublicReader):
    def __init__(self, **kwargs):
        super().__init__(origin=ORIGIN, **kwargs)

    def _get(self, url, robots=False):
        try:
            return super()._get(url, robots)
        except StopSource as error:
            error.request_url = url
            # A missing single listing does not block the other MLS listings.
            # An inaccessible robots file still stops all access to this host.
            if not robots and error.http_status in (404, 410):
                error.status = 'listing_not_available'
            raise


def snapshots(repo, row):
    records = []
    for provider, root in ((PROVIDER, ROOT), ('tarasa', Path('COMPS_REPORTS/additional_sources/tarasa')), (core.PROVIDER, core.ROOT)):
        path = Path(repo) / root / (cache_key(row['id']) + '.json')
        record = registry.read_json(path) if path.exists() else None
        if core.bound(row, record, provider) and record.get('status') == 'published':
            records.append(record)
    return sorted(records, key=lambda r: parse_date(r.get('retrieved_at')) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def fresh(record, now):
    date = parse_date((record or {}).get('retrieved_at'))
    return bool(record and record.get('status') == 'published' and date and
                timedelta(0) <= now - date < timedelta(days=CACHE_DAYS))


def read_snapshot(repo, row, now=None):
    records = snapshots(repo, row)
    now = now or datetime.now(timezone.utc)
    return next((r for r in records if fresh(r, now)), records[0] if records else None)


def run(repo, mode, property_id, requested_url='', request_id='', reader=None, write_status=True, now=None):
    repo = Path(repo)
    if mode not in ('check', 'fetch'):
        raise ValueError('Unsupported source mode')
    now = now or datetime.now(timezone.utc)
    report = {'version': VERSION, 'provider': PROVIDER, 'source_name': SOURCE,
        'mode': mode, 'property_id': property_id, 'request_id': request_id,
        'new_rentcast_calls': 0, 'new_source_network_requests': 0, 'listing_requests': 0, 'cache_saved': False,
        'cached_facts': 0, 'cached_photos': 0,
        'properties_sha256': hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest()}
    try:
        row = core.eligible(repo, property_id)
    except ValueError as error:
        report['status'] = str(error)
        if mode == 'fetch' and write_status:
            registry.atomic_json(repo / STATUS, report)
        return report
    path = repo / ROOT / (cache_key(property_id) + '.json')
    own = registry.read_json(path) if path.exists() else None
    own = own if bound(row, own) else None
    cached = read_snapshot(repo, row, now)
    report.update(cached_facts=len((cached or {}).get('facts', {})),
                  cached_photos=len((cached or {}).get('photos', [])),
                  cache_provider=(cached or {}).get('provider'), target_url=target_url(row))
    if mode == 'check':
        report['status'] = 'cache_ready' if cached else 'cache_missing'
        report['cache_fresh'] = fresh(cached, now)
        return report
    state = registry.read_json(repo / SOURCE_STATE) if (repo / SOURCE_STATE).exists() else {}
    dates = [parse_date(state.get('retry_after_at')), parse_date((own or {}).get('last_attempt', {}).get('retry_after_at'))]
    cooldown = max((d for d in dates if d), default=None)
    if requested_url and not source_url(requested_url):
        report['status'] = 'unsupported_source_url'
    elif fresh(cached, now):
        report['status'] = 'cache_used'
    elif cooldown and cooldown > now:
        report.update(status='source_cooldown', retry_after_at=cooldown.isoformat(),
                      http_status=state.get('http_status'), request_url=state.get('request_url'))
    elif not (requested_url or (own or {}).get('source_url') or target_url(row)):
        report['status'] = 'verified_listing_url_required'
    else:
        reader = reader or PublicReader(max_requests=2)
        before = reader.requests
        listing_before = None
        url = requested_url or (own or {}).get('source_url') or target_url(row)
        try:
            reader.initialize()
            listing_before = reader.requests
            html = reader.get(url)
            record = parse_detail(html, row, url, now.isoformat())
            registry.atomic_json(path, record)
            report.update(status='published', cache_saved=True, cached_facts=len(record['facts']),
                cached_photos=len(record['photos']), cache_provider=PROVIDER, source_url=record['source_url'])
        except (StopSource, ValueError) as error:
            status = error.status if isinstance(error, StopSource) else (
                'source_identity_mismatch' if str(error).startswith('source_identity_mismatch') else 'source_unavailable')
            report.update(status=status, http_status=getattr(error, 'http_status', None),
                          request_url=getattr(error, 'request_url', url))
            if status not in NO_FAILURE_RECEIPT:
                retry = cooldown_until(getattr(error, 'retry_after', None), now)
                # Failed refresh metadata cannot overwrite a successful snapshot.
                record = dict(own or {'schema': 1, 'provider': PROVIDER, 'property_id': property_id,
                    'identity': identity4(row), 'subject': registry.identity(row), 'listing_id': registry.listing_id(row),
                    'inventory_source_url': registry.safe_url(row.get('url')), 'source_url': url,
                    'status': status, 'facts': {}, 'photos': [], 'retrieved_at': None})
                record['last_attempt'] = {'status': status, 'checked_at': now.isoformat(),
                    'http_status': report['http_status'], 'request_url': report['request_url'], 'retry_after_at': retry}
                registry.atomic_json(path, record)
                report['retry_after_at'] = retry
                if status in GLOBAL_BLOCKS:
                    registry.atomic_json(repo / SOURCE_STATE, {'provider': PROVIDER,
                        **record['last_attempt']})
            if isinstance(error, ValueError):
                report['identity_check'] = str(error)
        report['new_source_network_requests'] = reader.requests - before
        report['listing_requests'] = reader.requests - listing_before if listing_before is not None else 0
    if write_status:
        registry.atomic_json(repo / STATUS, report)
    return report


def summary(report):
    lines = ['## ERIEMOVES_PROPERTY_SOURCE', '', '- New RentCast calls: **0**',
        '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
        '- מקור פעיל: ErieMoves / Coldwell Banker Select', '- מצב: ' + report['status'],
        '- נכס: ' + report['property_id'], '- שדות שמורים: ' + str(report['cached_facts']),
        '- תמונות שמורות: ' + str(report['cached_photos']),
        '- נשמר מטמון חדש: ' + ('כן' if report['cache_saved'] else 'לא'),
        '- מקור המטמון: ' + str(report.get('cache_provider') or 'אין'), '',
        'נבדק מטמון תקין לפני פנייה למקור. כל שמירה מחייבת התאמה של MLS ושל הכתובת המלאה.',
        'זהו חיבור ידני לנכס אחד. התור וההשלמה האוטומטית אינם מופעלים או משתנים.',
        'properties.json, חדר העסקאות, הסריקה ותקציב RentCast אינם נכתבים.', '']
    if report.get('retry_after_at'):
        lines.append('- ניסיון נוסף למקור: ' + report['retry_after_at'] + ' (UTC)')
    if report.get('http_status'):
        lines.append('- HTTP: ' + str(report['http_status']))
    if report.get('request_url'):
        lines.append('- עמוד הבקשה: ' + report['request_url'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'fetch'))
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--property-id', default='PA-MLS-193473')
    parser.add_argument('--source-url', default='')
    parser.add_argument('--request-id', default='')
    args = parser.parse_args()
    report = run(args.repo, args.mode, args.property_id.strip(), args.source_url.strip(), args.request_id)
    print(json.dumps(report, ensure_ascii=False))
    print(summary(report))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(summary(report))


if __name__ == '__main__':
    main()
