#!/usr/bin/env python3
"""Cache-first public Howard Hanna enrichment of one existing MLS listing.

Uses a discovered public property URL, then independently checks the primary listing,
full address and current inventory binding. County context comes from the public Erie County directory; no county label is claimed on the detail page. No paid
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

VERSION = 'howardhanna-source-1.0.0-20261010'
PROVIDER = 'howardhanna'
ORIGIN, SOURCE, _ = core.PROVIDERS[PROVIDER]
ROOT = Path('COMPS_REPORTS/additional_sources/howardhanna')
SOURCE_STATE = Path('COMPS_REPORTS/additional_sources/howardhanna_state.json')
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


def target_url(row):
    # Numeric MLS routes are not guessed for this provider.
    return None


def parse_detail(html, row, requested_url, checked_at=None):
    url = source_url(requested_url)
    if not url or not registry.hanna_url_matches(row, url):
        raise ValueError('source_identity_mismatch: source URL address')
    soup = BeautifulSoup(html, 'html.parser')
    canonical = soup.find_all('link', rel='canonical')
    if len(canonical) != 1 or source_url(canonical[0].get('href')) != url:
        raise ValueError('source_identity_mismatch: canonical')
    headlines = soup.select('h1[itemprop="address"]')
    if len(headlines) != 1:
        raise ValueError('source_identity_mismatch: subject address')
    page_row = {'county': 'Erie'}
    for prop, key in [('streetAddress','address'), ('addressLocality','city'), ('addressRegion','state'), ('postalCode','zip')]:
        nodes = headlines[0].select('[itemprop="' + prop + '"]')
        if len(nodes) != 1:
            raise ValueError('source_identity_mismatch: address parts')
        page_row[key] = nodes[0].get_text(' ', strip=True)
    expected = registry.identity(row)
    if registry.address_key(registry.identity(page_row)) != registry.address_key(expected):
        raise ValueError('source_identity_mismatch: full address')
    primary = soup.select('button.topbar-btn.save-property[data-mlsname][data-mlsnumber]')
    if len(primary) != 1 or primary[0]['data-mlsname'] != 'EriePA' or primary[0]['data-mlsnumber'] != registry.listing_id(row):
        raise ValueError('source_identity_mismatch: primary MLS and board')
    headings = [h for h in soup.find_all('h2') if h.get_text(' ',strip=True) == 'Property Details']
    if len(headings) != 1:
        raise ValueError('source_unavailable: labeled property details')
    details = headings[0].parent
    headers = soup.select('.prop-main > .prop-section')
    header = headers[0] if headers else None
    if header is None:
        raise ValueError('source_unavailable: property summary')
    statuses = {registry.norm(x.get_text(' ',strip=True)) for x in headlines[0].parent.select('.badge-status .text')}
    if not statuses <= {'new listing', 'price changed', 'active', 'open house'}:
        raise ValueError('source_identity_mismatch: inactive listing')
    pairs = {}
    for container in (header, details):
        for item in container.select('dl .dl-item'):
            dt, dd = item.find('dt',recursive=False), item.find('dd',recursive=False)
            if not dt or not dd:
                continue
            label, value = registry.norm(dt.get_text(' ',strip=True)), dd.get_text(' ',strip=True)
            if label in pairs and pairs[label] != value:
                raise ValueError('source_identity_mismatch: conflicting subject labels')
            pairs[label] = value
    if pairs.get('mls #') != registry.listing_id(row) or pairs.get('mls#') != registry.listing_id(row):
        raise ValueError('source_identity_mismatch: labeled MLS')
    checked_at = checked_at or core.timestamp()
    facts = {}
    def fact(field, value, label, unit=None):
        if registry.clean_value(value) is not None:
            if field in facts and facts[field]['value'] != value:
                raise ValueError('source_identity_mismatch: conflicting subject facts')
            facts[field] = {'value':value,'unit':unit,'source':SOURCE,'source_url':url,
                'listing_id':registry.listing_id(row),'property_id':str(row['id']),
                'checked_at':checked_at,'source_as_of':None,'method':'public_listing_label',
                'status':'reported_by_source','label':label}
    for label, field in {'beds':'beds','sq.ft':'sqft','built':'year_built','stories':'stories',
                         'garage size':'parking_spaces','architecture':'style'}.items():
        value = pairs.get(label)
        if value is not None:
            fact(field, value if field == 'style' else core.numeric(value), label, 'sqft' if field == 'sqft' else None)
    full = pairs.get('full bath') or pairs.get('full baths') or pairs.get('bath') or pairs.get('baths')
    partial = pairs.get('partial bath') or pairs.get('partial baths')
    if full is not None and core.numeric(full) is not None and (partial is None or core.numeric(partial) is not None):
        fact('baths',core.numeric(full) + (core.numeric(partial) or 0)/2, 'full baths + half of partial baths' if partial else 'bath')
    feature_groups = {}
    for name in ('interior-features-collapse','exterior-features-collapse','room-dimensions-collapse'):
        feature_groups[name] = [x.get_text(' ',strip=True) for x in details.select('#' + name + ' li.list-group-item')]
    mappings = {'roof_type': {'asphalt roof','metal roof','slate roof','tile roof','wood roof','rubber roof','composition roof'},
        'heating': {'forced air heat','baseboard heat','hot water heat','electric heat','gas heat','oil heat','propane heat'},
        'cooling': {'central air','window unit air conditioner','wall unit air conditioner'},
        'basement': {'full basement','finished basement','partially finished basement','unfinished basement','crawlspace'},
        'construction': {'aluminum siding','vinyl siding','brick exterior','stone exterior','wood exterior','stucco exterior'},
        'parking': {'garage','attached parking','attached garage','detached garage','off street parking'},
        'water': {'public water','well water'}, 'sewer': {'public sewer','septic system'}}
    labels = feature_groups['interior-features-collapse'] + feature_groups['exterior-features-collapse']
    for field, allowed in mappings.items():
        values = [value for value in labels if registry.norm(value) in allowed]
        if values:
            fact(field, ', '.join(dict.fromkeys(values)), ' / '.join(values))
    for value in feature_groups['exterior-features-collapse']:
        match = re.fullmatch(r'Lot acreage is: (\d+(?:\.\d+)?)',value)
        if match:
            fact('lot_area_acres',float(match[1]),value,'acre')
    for value in feature_groups['room-dimensions-collapse']:
        match = re.fullmatch(r'Number of rooms: (\d+)',value)
        if match:
            fact('total_rooms',int(match[1]),value)
    photos, seen = [], set()
    for anchor in soup.select('#galleria a[href]'):
        photo = anchor['href']
        if registry.hanna_photo_url(photo) and photo not in seen:
            seen.add(photo)
            photos.append({'url':photo,'source':SOURCE,'source_url':url,'listing_id':registry.listing_id(row),
                'retrieved_at':checked_at,'capture_date':None})
            if len(photos) == 3:
                break
    return {'schema':1,'version':VERSION,'provider':PROVIDER,'property_id':str(row['id']),
        'listing_id':registry.listing_id(row),'identity':identity4(row),'subject':expected,
        'inventory_source_url':registry.safe_url(row.get('url')),'source_url':url,'source_name':SOURCE,
        'status':'published','method':'public_listing_labels_and_microdata_identity',
        'identity_evidence':{'address_and_mls':'verified_on_detail','board':'EriePA',
            'county_label_on_detail':'not_reported','county_context':'public_erie_county_directory'},
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
    for provider, root in ((PROVIDER, ROOT), ('eriemoves', Path('COMPS_REPORTS/additional_sources/eriemoves')), ('tarasa', Path('COMPS_REPORTS/additional_sources/tarasa')), (core.PROVIDER, core.ROOT)):
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
        if registry.identity(row)['county'] != 'Erie' or row.get('market_status') != 'active':
            raise ValueError('outside_initial_source_coverage')
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
    if requested_url and (not source_url(requested_url) or not registry.hanna_url_matches(row, requested_url)):
        report['status'] = 'unsupported_source_url'
    elif fresh(cached, now):
        report['status'] = 'cache_used'
    elif not (requested_url or (own or {}).get('source_url')):
        report['status'] = 'source_url_required'
    elif cooldown and cooldown > now:
        report.update(status='source_cooldown', retry_after_at=cooldown.isoformat(),
                      http_status=state.get('http_status'), request_url=state.get('request_url'))
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
    lines = ['## TARASA_PROPERTY_SOURCE', '', '- New RentCast calls: **0**',
        '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
        '- מקור פעיל: Tarasa / River Point Realty', '- מצב: ' + report['status'],
        '- נכס: ' + report['property_id'], '- שדות שמורים: ' + str(report['cached_facts']),
        '- תמונות שמורות: ' + str(report['cached_photos']),
        '- נשמר מטמון חדש: ' + ('כן' if report['cache_saved'] else 'לא'),
        '- מקור המטמון: ' + str(report.get('cache_provider') or 'אין'), '',
        'נבדק מטמון תקין לפני פנייה למקור. כל שמירה מחייבת התאמה של MLS ושל הכתובת המלאה.',
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
    parser.add_argument('--property-id', default='PA-MLS-1778408')
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
