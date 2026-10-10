#!/usr/bin/env python3
"""Bounded Clear Choice listing enrichment pilot. No RentCast or inventory writes.

One requested inventory listing, at most three public discovery pages and one
detail page. Robots rules, a minimum ten-second interval, cache and cooldown
apply. A block stops the attempt; no browser/proxy/cookie fallback is used.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import property_sources as registry

VERSION = 'additional-source-1.4.0-20261010'
ORIGIN = 'https://www.clearchoiceenterprises.com'
PROVIDER = 'clearchoice'
SOURCE = 'Clear Choice / MLS'
AGENT = 'PA-Property-SourceReader/1.0'
ROOT = Path('COMPS_REPORTS/additional_sources/clearchoice')
STATUS = Path('COMPS_REPORTS/additional_source_status.json')
SOURCE_STATE = Path('COMPS_REPORTS/additional_sources/clearchoice_state.json')
CACHE_DAYS = 7
PROVIDERS = {
    'howardhanna': ('https://www.howardhanna.com', 'Howard Hanna / Greater Erie MLS', r'/property/[a-z0-9-]+-\d{9,15}'),
    'eriemoves': ('https://eriemoves.com', 'ErieMoves / Coldwell Banker Select / MLS', r'/listing/PA/[A-Za-z0-9-]+/[A-Za-z0-9-]+/\d+'),
    'clearchoice': ('https://www.clearchoiceenterprises.com', 'Clear Choice / MLS', r'/idx/[a-z0-9-]+/\d+_spid/'),
    'tarasa': ('https://www.tarasa.com', 'Tarasa / River Point Realty / MLS', r'/property-search/detail/56/\d+/[a-z0-9-]+/'),
}
KNOWN_URLS = {'1778408': ORIGIN + '/idx/2346-fremont-pl-pittsburgh-pa-15216/1810062759_spid/'}
LABELS = {'Roof': 'roof_type', 'Heating': 'heating', 'Cooling': 'cooling',
          'Parking Features': 'parking', 'Parking Total': 'parking_spaces',
          'Construction Materials': 'construction', 'Basement': 'basement',
          'Stories': 'stories', 'Water': 'water', 'Sewer': 'sewer',
          'Bedrooms': 'beds', 'Bathrooms': 'baths', 'Building Area': 'sqft',
          'Year Built': 'year_built', 'Total Rooms': 'total_rooms', 'Style': 'style'}
FIELD_UNITS = {'sqft': 'sqft', 'lot_area_acres': 'acre'}
NUMERIC = {'beds', 'baths', 'sqft', 'year_built', 'stories', 'parking_spaces', 'total_rooms'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def cache_key(property_id):
    return hashlib.sha256(str(property_id).encode()).hexdigest()[:32]


def identity4(row):
    subject = registry.identity(row)
    street = subject['street'] + (' unit ' + subject['unit'] if subject['unit'] else '')
    return [street.upper(), subject['city'].upper(), subject['state'], subject['zip']]


def source_url(value, provider=PROVIDER):
    try:
        u = urlparse(str(value or ''))
        origin, _, pattern = PROVIDERS[provider]
        if (u.scheme == 'https' and u.hostname == urlparse(origin).hostname
                and not u.username and not u.password and u.port in (None, 443)
                and not u.query and not u.fragment
                and re.fullmatch(pattern, u.path)):
            return u.geturl()
    except (ValueError, KeyError):
        pass
    return None


def photo_url(value, mls):
    try:
        u = urlparse(str(value or ''))
        match = re.fullmatch(r'/(?:pics[123]x|large)/v\d+/\d+/\d+_(\d+)_(\d{2,3})\.jpg', u.path)
        return bool(u.scheme == 'https' and u.hostname == 'cdn.listingphotos.sierrastatic.com'
                    and not u.username and not u.password and u.port in (None, 443)
                    and not u.query and not u.fragment and match and match[1] == str(mls))
    except ValueError:
        return False


def bound(row, record, provider=PROVIDER):
    return bool(isinstance(record, dict) and record.get('provider') == provider
        and record.get('property_id') == str(row.get('id'))
        and record.get('identity') == identity4(row)
        and record.get('listing_id') == registry.listing_id(row)
        and registry.safe_url(row.get('url'))
        and registry.safe_url(record.get('inventory_source_url')) == registry.safe_url(row.get('url'))
        and source_url(record.get('source_url'), provider)
        and (provider != 'eriemoves' or registry.erie_url_matches(row, record['source_url']))
        and (provider != 'howardhanna' or registry.hanna_url_matches(row, record['source_url']))
        and (provider != 'tarasa' or urlparse(record['source_url']).path.split('/')[4] == registry.listing_id(row))
        and registry.address_key(record.get('subject') or {}) == registry.address_key(registry.identity(row)))


def eligible(repo, property_id):
    rows = registry.read_json(repo / 'properties.json')
    matches = [r for r in rows if isinstance(r, dict) and str(r.get('id')) == property_id]
    if len(matches) != 1:
        raise ValueError('ambiguous_or_missing_property_id')
    row = matches[0]
    if row.get('source_type') != 'mls' or not registry.identity(row)['complete'] or not registry.listing_id(row):
        raise ValueError('unsupported_or_incomplete_identity')
    # Existing identity review takes precedence over any attempted source join.
    aliases_path = repo / registry.ROOT / 'aliases.json'
    if aliases_path.exists() and property_id in registry.read_json(aliases_path).get('ambiguous', {}):
        raise ValueError('ambiguous_property_identity')
    return row


def numeric(text):
    if not re.fullmatch(r'\d+(?:,\d{3})*(?:\.\d+)?', str(text).strip()):
        return None
    value = float(str(text).replace(',', ''))
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() else value


def parse_detail(html, row, url, checked_at=None, provider=PROVIDER):
    """Extract only the subject's labeled fields, never nearby cards/agent data."""
    url = source_url(url, provider)
    if not url:
        raise ValueError('unsupported_source_url')
    source_name = PROVIDERS[provider][1]
    if provider == 'tarasa' and urlparse(url).path.split('/')[4] != registry.listing_id(row):
        raise ValueError('source_identity_mismatch: URL MLS')
    soup = BeautifulSoup(html, 'html.parser')
    canonical = soup.find('link', rel='canonical')
    if not canonical or source_url(canonical.get('href'), provider) != url:
        raise ValueError('source_identity_mismatch: canonical')
    details, h1 = soup.find(id='propertyDetails'), soup.find('h1')
    if details is None or h1 is None:
        raise ValueError('source_unavailable: property details missing')
    pairs = {}
    for label in details.find_all('strong'):
        value = label.parent.find('span', recursive=False)
        if value:
            key, text = label.get_text(' ', strip=True), value.get_text(' ', strip=True)
            if key in pairs and pairs[key] != text:
                raise ValueError('source_identity_mismatch: duplicate subject labels')
            pairs[key] = text
    county = pairs.get('County', '').split('-')[0].strip()
    if registry.COUNTIES.get(registry.norm(county)) != registry.identity(row)['county']:
        raise ValueError('source_identity_mismatch: county')
    listing_objects, property_objects = [], []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        values = data if isinstance(data, list) else data.get('@graph', [data]) if isinstance(data, dict) else []
        for item in values:
            if not isinstance(item, dict):
                continue
            types = item.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if 'RealEstateListing' in types and item.get('url') == url:
                listing_objects.append(item)
            if item.get('@id') == url + '#property' and isinstance(item.get('address'), dict):
                property_objects.append(item)
    if len(listing_objects) != 1 or len(property_objects) != 1:
        raise ValueError('source_identity_mismatch: structured listing')
    listing, prop = listing_objects[0], property_objects[0]
    about = listing.get('about')
    if isinstance(about, dict):
        about = about.get('@id')
    if about != prop.get('@id'):
        raise ValueError('source_identity_mismatch: listing subject')
    address = prop['address']
    page_row = {'address': address.get('streetAddress'), 'city': address.get('addressLocality'),
                'state': address.get('addressRegion'), 'zip': address.get('postalCode'), 'county': county}
    subject = registry.identity(page_row)
    expected = registry.identity(row)
    if not subject['complete'] or registry.address_key(subject) != registry.address_key(expected):
        raise ValueError('source_identity_mismatch: full address')
    first_h1_span = h1.find('span')
    h1_street = first_h1_span.get_text(' ', strip=True) if first_h1_span else h1.get_text(' ', strip=True)
    if registry.parse_address(h1_street)[:2] != registry.parse_address(row.get('address'))[:2]:
        raise ValueError('source_identity_mismatch: headline')
    mls = registry.listing_id(row)
    # The primary save button and explicit MLS label must agree. Similar listings
    # elsewhere in the page cannot supply either identifier.
    primary = soup.find(attrs={'data-mls': True})
    mls_labels = [n.find_next_sibling('strong') for n in soup.find_all('span', string=lambda s: s and s.strip() == 'MLS #')]
    if not primary or str(primary.get('data-mls')) != mls or not any(n and n.get_text(strip=True) == mls for n in mls_labels):
        raise ValueError('source_identity_mismatch: MLS')
    checked_at = checked_at or timestamp()
    updated = None
    for label in soup.find_all('span', string=lambda s: s and s.strip() == 'Last Updated'):
        sibling = label.find_next_sibling('strong')
        try:
            updated = datetime.strptime(sibling.get_text(strip=True), '%m/%d/%Y').date().isoformat()
            break
        except (ValueError, AttributeError):
            continue
    facts = {}
    def fact(field, value, label, unit=None):
        if registry.clean_value(value) is None:
            return
        facts[field] = {'value': value, 'unit': unit or FIELD_UNITS.get(field),
            'source': source_name, 'source_url': url, 'listing_id': mls, 'property_id': str(row['id']),
            'checked_at': checked_at, 'source_as_of': updated, 'method': 'public_listing_label',
            'status': 'reported_by_source', 'label': label}
    for label, field in LABELS.items():
        if label in pairs:
            value = numeric(pairs[label]) if field in NUMERIC else pairs[label]
            fact(field, value, label)
    acres = re.fullmatch(r'(\d+(?:\.\d+)?)\s+Acres?', pairs.get('Lot Size', ''), re.I)
    if acres:
        # The published acreage is rounded. Do not convert it into an exact
        # square-foot measurement or replace the scanner's original lot size.
        fact('lot_area_acres', float(acres[1]), 'Lot Size', 'acre')
    photos, seen = [], set()
    for img in soup.find_all('img', src=True):
        photo = img['src']
        if not photo_url(photo, mls):
            continue
        seq = re.search(r'_(\d{2,3})\.jpg$', photo)[1]
        if seq in seen:
            continue
        seen.add(seq)
        photos.append({'url': photo, 'source': source_name, 'source_url': url, 'listing_id': mls,
                       'retrieved_at': checked_at, 'capture_date': None})
        if len(photos) == 3:
            break
    return {'schema': 1, 'version': VERSION, 'provider': provider, 'property_id': str(row['id']),
        'listing_id': mls, 'identity': identity4(row), 'subject': expected,
        'inventory_source_url': registry.safe_url(row.get('url')), 'source_url': url,
        'source_name': source_name, 'status': 'published', 'method': 'public_listing_labels_and_jsonld_identity',
        'retrieved_at': checked_at, 'source_updated_at': updated, 'facts': facts, 'photos': photos,
        'missing_fields': [k for k in ('occupancy', 'roof_condition') if k not in facts],
        'html_sha256': hashlib.sha256(html.encode()).hexdigest()}


class StopSource(Exception):
    def __init__(self, status, http_status=None, retry_after=None):
        super().__init__(status)
        self.status, self.http_status, self.retry_after = status, http_status, retry_after


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Unexpected redirects may be a challenge or another site's homepage.
        # Stop rather than following unvalidated destinations.
        raise StopSource('source_redirect_stopped', code)


class PublicReader:
    def __init__(self, interval=10, clock=time.monotonic, sleep=time.sleep, opener=None, max_requests=None, deadline=None, origin=ORIGIN):
        if origin not in {v[0] for v in PROVIDERS.values()}:
            raise ValueError('Unsupported public source origin')
        self.origin = origin
        self.interval, self.clock, self.sleep = max(10, interval), clock, sleep
        self.opener = opener or build_opener(SameOriginRedirect())
        self.last_request, self.requests, self.robots = None, 0, None
        self.max_requests, self.deadline = max_requests, deadline
        self.page_cache = {}

    def _get(self, url, robots=False):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != urlparse(self.origin).netloc:
            raise StopSource('unsupported_source_url')
        if not robots and (not self.robots or not self.robots.can_fetch(AGENT, url)):
            raise StopSource('robots_disallowed')
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise StopSource('source_request_budget_exhausted')
        wait = 0 if self.last_request is None else max(0, self.interval - (self.clock() - self.last_request))
        if self.deadline is not None and self.clock() + wait + 25 > self.deadline:
            raise StopSource('batch_time_limit')
        if self.last_request is not None:
            self.sleep(wait)
        self.last_request = self.clock()
        self.requests += 1
        req = Request(url, headers={'User-Agent': AGENT, 'Accept': 'text/html,text/plain;q=0.9'})
        try:
            with self.opener.open(req, timeout=25) as response:
                if getattr(response, 'status', 200) != 200:
                    raise StopSource('source_unavailable', response.status)
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise StopSource('source_response_too_large')
                return body.decode('utf-8', errors='replace')
        except HTTPError as error:
            status = 'source_blocked' if error.code in (401, 403) else 'source_rate_limited' if error.code == 429 else 'source_unavailable'
            raise StopSource(status, error.code, error.headers.get('Retry-After')) from error
        except (URLError, TimeoutError, OSError) as error:
            raise StopSource('source_unavailable') from error

    def initialize(self):
        if self.robots is not None:
            return
        body = self._get(self.origin + '/robots.txt', robots=True)
        if re.search(r'<(?:!doctype|html|script|body)\b', body, re.I) or not re.search(r'^\s*user-agent\s*:', body, re.I | re.M):
            raise StopSource('source_unavailable')
        rp = RobotFileParser()
        rp.parse(body.splitlines())
        delay = rp.crawl_delay(AGENT) or rp.crawl_delay('*') or 0
        if delay > 60:
            raise StopSource('robots_delay_exceeds_pilot_limit')
        self.interval = max(self.interval, delay)
        self.robots = rp

    def get(self, url):
        if url not in self.page_cache:
            self.page_cache[url] = self._get(url)
        return self.page_cache[url]


def discover(reader, row):
    for page in range(1, 4):
        suffix = '' if page == 1 else '?pg=' + str(page)
        html = reader.get(ORIGIN + '/newest-listings/' + suffix)
        soup = BeautifulSoup(html, 'html.parser')
        candidates = []
        cards = soup.select('.si-listing[data-url]')
        if not cards:
            raise StopSource('source_discovery_page_unavailable')
        for card in cards:
            button = card.find(attrs={'data-mls': True})
            street = card.select_one('.si-listing__title-main')
            city = card.select_one('.si-listing__title-description')
            expected_city = registry.norm(f"{row['city']} PA {str(row['zip'])[:5]}")
            if (not button or str(button.get('data-mls')) != registry.listing_id(row)
                    or not street or registry.parse_address(street.get_text(' ', strip=True))[:2] != registry.parse_address(row['address'])[:2]
                    or not city or registry.norm(city.get_text(' ', strip=True).replace(',', ' ')) != expected_city):
                continue
            # Discovery selects a URL; parse_detail independently verifies full
            # address, county, primary MLS and JSON-LD before facts are saved.
            url = source_url(urljoin(ORIGIN, card.get('data-url', '')))
            if url:
                candidates.append(url)
        candidates = sorted(set(candidates))
        if len(candidates) > 1:
            raise StopSource('ambiguous_source_links')
        if candidates:
            return candidates[0]
    raise StopSource('source_listing_not_found_in_pilot_pages')


def parse_date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def discover_many(reader, rows):
    """Match a bounded shared public index to current, unambiguous inventory rows."""
    by_mls = {}
    for row in rows:
        by_mls.setdefault(registry.listing_id(row), []).append(row)
    found = {}
    for page in range(1, 4):
        suffix = '' if page == 1 else '?pg=' + str(page)
        soup = BeautifulSoup(reader.get(ORIGIN + '/newest-listings/' + suffix), 'html.parser')
        cards = soup.select('.si-listing[data-url]')
        if not cards:
            raise StopSource('source_discovery_page_unavailable')
        for card in cards:
            button = card.find(attrs={'data-mls': True})
            matches = by_mls.get(str(button.get('data-mls')), []) if button else []
            if len(matches) != 1:
                continue
            row = matches[0]
            street = card.select_one('.si-listing__title-main')
            city = card.select_one('.si-listing__title-description')
            if not street or not city:
                continue
            if registry.parse_address(street.get_text(' ', strip=True))[:2] != registry.parse_address(row['address'])[:2]:
                continue
            if registry.norm(city.get_text(' ', strip=True).replace(',', ' ')) != registry.norm(f"{row['city']} PA {str(row['zip'])[:5]}"):
                continue
            url = source_url(urljoin(ORIGIN, card.get('data-url', '')))
            if url:
                found.setdefault(str(row['id']), set()).add(url)
    return {key: next(iter(urls)) for key, urls in found.items() if len(urls) == 1}


def discover_directory(reader, rows, start_page=1):
    """Use the public listing directory, resuming after at most three pages."""
    by_mls = {}
    for row in rows:
        by_mls.setdefault(registry.listing_id(row), []).append(row)
    found, next_page = {}, max(1, int(start_page))
    cycle_complete = False
    for _ in range(3):
        number = next_page
        url = ORIGIN + '/idx/site-map/' + ('?offset=' + str(number) if number > 1 else '')
        try:
            soup = BeautifulSoup(reader.get(url), 'html.parser')
        except StopSource as error:
            if number > 1 and error.status in {'source_redirect_stopped', 'source_unavailable'}:
                error.reset_directory = True
            raise
        listing_links = 0
        pages = [number]
        for anchor in soup.find_all('a', href=True):
            candidate = source_url(urljoin(ORIGIN, anchor['href']))
            if candidate:
                listing_links += 1
                label = anchor.get_text(' ', strip=True)
                match = re.fullmatch(r'(.+?)\s+PA\s+(\d{5})(?:-\d{4})?\s+MLS\s*#\s*(\d+)', label, flags=re.I)
                matches = by_mls.get(match[3], []) if match else []
                if len(matches) != 1:
                    continue
                row = matches[0]
                city = re.sub(r'\s+', ' ', str(row['city']).strip())
                prefix = match[1].rstrip(' ,')
                if match[2] != str(row['zip'])[:5] or not prefix.casefold().endswith(' ' + city.casefold()):
                    continue
                street = prefix[:-len(city)].strip()
                if registry.parse_address(street)[:2] != registry.parse_address(row['address'])[:2]:
                    continue
                found.setdefault(str(row['id']), set()).add(candidate)
            else:
                link = urlparse(urljoin(ORIGIN, anchor['href']))
                offsets = parse_qs(link.query).get('offset', [])
                if link.scheme == 'https' and link.netloc == 'www.clearchoiceenterprises.com' and link.path == '/idx/site-map/' and len(offsets) == 1 and offsets[0].isdigit():
                    if 1 <= int(offsets[0]) <= 1000:
                        pages.append(int(offsets[0]))
        if not listing_links:
            error = StopSource('source_discovery_page_unavailable')
            error.reset_directory = number > 1
            raise error
        maximum = max(pages)
        cycle_complete = number >= maximum
        next_page = 1 if cycle_complete else number + 1
        if sum(len(urls) == 1 for urls in found.values()) >= min(3, len(rows)) or cycle_complete:
            break
    return ({key: next(iter(urls)) for key, urls in found.items() if len(urls) == 1},
            next_page, cycle_complete)


def cooldown_until(retry_after, now):
    until = now + timedelta(days=1)
    if retry_after:
        if str(retry_after).isdigit():
            until = max(until, now + timedelta(seconds=min(int(retry_after), 31_536_000)))
        else:
            from email.utils import parsedate_to_datetime
            try:
                until = max(until, parsedate_to_datetime(str(retry_after)))
            except (ValueError, TypeError, OverflowError):
                pass
    return until.isoformat()


def run(repo, mode, property_id, requested_url='', request_id='', reader=None, write_status=True):
    report = {'version': VERSION, 'mode': mode, 'property_id': property_id, 'request_id': request_id,
              'new_rentcast_calls': 0, 'new_source_network_requests': 0, 'cache_saved': False,
              'cached_facts': 0, 'cached_photos': 0,
              'properties_sha256': hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest()}
    try:
        row = eligible(repo, property_id)
    except ValueError as error:
        report['status'] = str(error)
        if mode == 'fetch' and write_status:
            registry.atomic_json(repo / STATUS, report)
        return report
    path = repo / ROOT / (cache_key(property_id) + '.json')
    old = registry.read_json(path) if path.exists() else None
    old = old if bound(row, old) else None
    now = datetime.now(timezone.utc)
    report.update(cached_facts=len((old or {}).get('facts', {})), cached_photos=len((old or {}).get('photos', [])))
    if mode == 'check':
        report['status'] = 'cache_ready' if old and old.get('status') == 'published' else 'cache_missing'
        return report
    state = registry.read_json(repo / SOURCE_STATE) if (repo / SOURCE_STATE).exists() else {}
    cooldowns = [parse_date((old or {}).get('last_attempt', {}).get('retry_after_at')), parse_date(state.get('retry_after_at'))]
    cooldown = max((d for d in cooldowns if d), default=None)
    retrieved = parse_date((old or {}).get('retrieved_at'))
    if old and old.get('status') == 'published' and retrieved and retrieved <= now and now - retrieved < timedelta(days=CACHE_DAYS):
        report['status'] = 'cache_used'
    elif cooldown and cooldown > now:
        report['status'] = 'source_cooldown'
    elif requested_url and not source_url(requested_url):
        report['status'] = 'unsupported_source_url'
    else:
        reader = reader or PublicReader()
        requests_before = reader.requests
        try:
            reader.initialize()
            url = source_url(requested_url) if requested_url else (old or {}).get('source_url') or KNOWN_URLS.get(registry.listing_id(row))
            if requested_url and not url:
                raise StopSource('unsupported_source_url')
            url = url or discover(reader, row)
            html = reader.get(url)
            record = parse_detail(html, row, url)
            registry.atomic_json(path, record)
            report.update(status='published', cache_saved=True, cached_facts=len(record['facts']), cached_photos=len(record['photos']))
        except (StopSource, ValueError) as error:
            status = error.status if isinstance(error, StopSource) else 'source_identity_mismatch' if str(error).startswith('source_identity_mismatch') else 'source_unavailable'
            # Keep successful facts and their true retrieval date on any failed
            # attempt. A per-source receipt records the block/cooldown separately.
            url = source_url(requested_url) or source_url((old or {}).get('source_url')) or KNOWN_URLS.get(registry.listing_id(row))
            if url and status not in ('source_request_budget_exhausted', 'batch_time_limit'):
                record = dict(old or {'schema': 1, 'provider': PROVIDER, 'property_id': property_id,
                    'identity': identity4(row), 'subject': registry.identity(row), 'listing_id': registry.listing_id(row),
                    'inventory_source_url': registry.safe_url(row.get('url')), 'source_url': url,
                    'status': status, 'facts': {}, 'photos': [], 'retrieved_at': None})
                record['last_attempt'] = {'status': status, 'checked_at': now.isoformat(),
                    'http_status': getattr(error, 'http_status', None),
                    'retry_after_at': cooldown_until(getattr(error, 'retry_after', None), now)}
                registry.atomic_json(path, record)
            if isinstance(error, StopSource) and status in ('source_blocked', 'source_rate_limited', 'robots_disallowed'):
                registry.atomic_json(repo / SOURCE_STATE, {'provider': PROVIDER, 'status': status,
                    'checked_at': now.isoformat(), 'http_status': error.http_status,
                    'retry_after_at': cooldown_until(error.retry_after, now)})
            report['status'] = status
        report['new_source_network_requests'] = reader.requests - requests_before
    if write_status:
        registry.atomic_json(repo / STATUS, report)
    return report


def summary(report):
    return '\n'.join(['## ADDITIONAL_SOURCE_PILOT', '',
        '- New RentCast calls: **0**',
        '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
        '- מצב: ' + report['status'], '- נכס: ' + report['property_id'],
        '- שדות שמורים: ' + str(report['cached_facts']), '- תמונות שמורות: ' + str(report['cached_photos']),
        '- נשמר מטמון חדש בריצה זו: ' + ('כן' if report['cache_saved'] else 'לא'), '',
        'הניסוי משלים נכס קיים ממקור נוסף; properties.json ונתוני חדר העסקאות אינם נכתבים.',
        'בדיקת check קוראת קבצים בלבד. fetch משתמש במטמון לפני פנייה למקור.', ''])


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
