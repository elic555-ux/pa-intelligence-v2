#!/usr/bin/env python3
"""Read-only, bounded access check for three independent public listing sources.

No inventory, source cache, queue, cloud, quota or activation writes. No RentCast,
tokens, login, browser challenges, proxy, redirects or automatic retries.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import property_sources as registry
import secondary_listing_source as existing

VERSION = 'alternative-source-access-1.0.0-20261009'
PROPERTY_ID = 'PA-MLS-1778408'
EXPECTED = ('2346 fremont pl', 'pittsburgh', 'PA', '15216', 'Allegheny')
AGENT = existing.AGENT
MAX_REQUESTS = 6
INTERVAL = 10
SOURCES = (
    {'id': 'tarasa', 'name': 'Tarasa / River Point Realty',
     'origin': 'https://www.tarasa.com',
     'url': 'https://www.tarasa.com/property-search/detail/56/1778408/2346-fremont-pl-pittsburgh-pa-15216/'},
    {'id': 'coldwellbankerhomes', 'name': 'Coldwell Banker Homes',
     'origin': 'https://www.coldwellbankerhomes.com',
     'url': 'https://www.coldwellbankerhomes.com/pa/beechview/2346-fremont-pl/pid_74181188/'},
    {'id': 'propertypanorama', 'name': 'Property Panorama',
     'origin': 'https://www.propertypanorama.com',
     'url': 'https://www.propertypanorama.com/instaview-tour/wpn/1778408'},
)


class StopCheck(Exception):
    def __init__(self, status, http_status=None, retry_after=None):
        super().__init__(status)
        self.status, self.http_status, self.retry_after = status, http_status, retry_after


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StopCheck('redirect_stopped', code)


class Reader:
    def __init__(self, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.opener = opener or build_opener(NoRedirect())
        self.clock, self.sleep = clock, sleep
        self.requests, self.last_request, self.deadline = 0, {}, clock() + 240

    def get(self, spec, url, interval=INTERVAL):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.netloc != urlparse(spec['origin']).netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise StopCheck('unsupported_url')
        if self.requests >= MAX_REQUESTS:
            raise StopCheck('request_budget_exhausted')
        last = self.last_request.get(spec['origin'])
        wait = max(0, interval - (self.clock() - last)) if last is not None else 0
        if self.clock() + wait + 25 > self.deadline:
            raise StopCheck('time_limit')
        if wait:
            self.sleep(wait)
        self.last_request[spec['origin']] = self.clock()
        self.requests += 1
        request = Request(url, headers={'User-Agent': AGENT,
                                       'Accept': 'text/html,text/plain;q=0.9'})
        try:
            with self.opener.open(request, timeout=25) as response:
                status = response.status
                if status != 200:
                    raise StopCheck('unexpected_http_status', status)
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise StopCheck('response_too_large', status)
                return body.decode('utf-8', errors='replace')
        except HTTPError as error:
            status = ('source_blocked' if error.code in (401, 403) else
                      'source_rate_limited' if error.code == 429 else 'source_unavailable')
            raise StopCheck(status, error.code, error.headers.get('Retry-After')) from error
        except (URLError, TimeoutError, OSError) as error:
            raise StopCheck('source_unavailable') from error


def nodes(soup):
    """Read only top-level listing/property objects, not nested agent addresses."""
    result = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        values = data if isinstance(data, list) else data.get('@graph', [data]) if isinstance(data, dict) else []
        result.extend(value for value in values if isinstance(value, dict))
    return result


def has_type(value, name):
    types = value.get('@type', [])
    return name in ([types] if isinstance(types, str) else types)


def same_address(address, row):
    expected = registry.identity(row)
    actual = registry.identity({'address': address.get('streetAddress'),
        'city': address.get('addressLocality'), 'state': address.get('addressRegion'),
        'zip': address.get('postalCode'), 'county': expected['county']})
    return actual['complete'] and registry.address_key(actual) == registry.address_key(expected)


def inspect_page(html, spec, row):
    soup = BeautifulSoup(html, 'html.parser')
    canonical = soup.find('link', rel='canonical')
    if not canonical or canonical.get('href') != spec['url']:
        raise StopCheck('canonical_or_listing_missing', 200)
    values, mls = nodes(soup), registry.listing_id(row)
    evidence = {'technical_fields': 0, 'technical_names': [], 'photo_links': 0,
                'identity_verified': False, 'inventory_merge_allowed': False}
    if spec['id'] == 'tarasa':
        listings = [v for v in values if has_type(v, 'RealEstateListing') and v.get('url') == spec['url']]
        props = [v for v in values if v.get('@id') == spec['url'] + '#property' and isinstance(v.get('address'), dict)]
        if len(listings) != 1 or len(props) != 1:
            raise StopCheck('subject_identity_mismatch', 200)
        about = listings[0].get('about')
        about = about.get('@id') if isinstance(about, dict) else about
        if about != props[0].get('@id') or not same_address(props[0]['address'], row):
            raise StopCheck('subject_identity_mismatch', 200)
        primary, details, h1 = soup.find(attrs={'data-mls': True}), soup.find(id='propertyDetails'), soup.find('h1')
        mls_labels = [n.find_next_sibling('strong') for n in soup.find_all('span',
                      string=lambda s: s and s.strip() == 'MLS #')]
        if (not primary or primary.get('data-mls') != mls or details is None or h1 is None
                or not any(n and n.get_text(strip=True) == mls for n in mls_labels)):
            raise StopCheck('subject_identity_mismatch', 200)
        headline = h1.find('span') or h1
        if registry.parse_address(headline.get_text(' ', strip=True))[:2] != registry.parse_address(row['address'])[:2]:
            raise StopCheck('subject_identity_mismatch', 200)
        pairs = {}
        for label in details.find_all('strong'):
            value = label.parent.find('span', recursive=False)
            if value:
                key, text = label.get_text(' ', strip=True), value.get_text(' ', strip=True)
                if key in pairs and pairs[key] != text:
                    raise StopCheck('conflicting_subject_labels', 200)
                pairs[key] = text
        if registry.COUNTIES.get(registry.norm(pairs.get('County', '').split('-')[0])) != registry.identity(row)['county']:
            raise StopCheck('subject_identity_mismatch', 200)
        available = [field for label, field in existing.LABELS.items() if label in pairs
                     and registry.clean_value(existing.numeric(pairs[label]) if field in existing.NUMERIC else pairs[label]) is not None]
        if re.fullmatch(r'\d+(?:\.\d+)?\s+Acres?', pairs.get('Lot Size', ''), re.I):
            available.append('lot_area_acres')
        photos = {re.search(r'_(\d{2,3})\.jpg$', img['src'])[1] for img in soup.find_all('img', src=True)
                  if existing.photo_url(img['src'], mls)}
        evidence.update(identity_verified=True, technical_fields=len(available), technical_names=available,
                        photo_links=min(3, len(photos)), status='verified_technical_listing')
    elif spec['id'] == 'coldwellbankerhomes':
        listings = [v for v in values if has_type(v, 'RealEstateListing') and v.get('url') == spec['url']]
        title = soup.find('meta', property='og:title')
        h1 = soup.find('h1')
        if (len(listings) != 1 or not h1 or not title
                or not re.search(r'\bMLS\s*#?\s*' + re.escape(mls) + r'\b', title.get('content', ''))):
            raise StopCheck('subject_identity_mismatch', 200)
        text = listings[0].get('name', '')
        match = re.fullmatch(r'(.+),\s*([^,]+),\s*(PA)\s+(\d{5})', text)
        if not match or registry.parse_address(match[1])[:2] != registry.parse_address(row['address'])[:2] or match[4] != registry.identity(row)['zip']:
            raise StopCheck('subject_identity_mismatch', 200)
        # Beechview is published instead of Pittsburgh. Record the variant,
        # but do not authorize an automatic merge or silently invent an alias.
        photo_urls = listings[0].get('image', [])
        if not isinstance(photo_urls, list):
            photo_urls = [photo_urls]
        photos = {p for p in photo_urls if isinstance(p, str) and re.fullmatch(
            r'https://m1?\.cbhomes\.com/p/716/' + re.escape(mls) + r'/[a-zA-Z0-9]+/full\.webp', p)}
        evidence.update(status='accessible_city_variant_needs_review', published_city=match[2],
                        photo_links=min(3, len(photos)))
    else:
        offers = [v for v in values if has_type(v, 'Offer') and v.get('url') == spec['url']]
        rooms = [v for v in values if has_type(v, 'Accommodation') and isinstance(v.get('address'), dict)]
        description = soup.find('meta', attrs={'name': 'description'})
        if (len(offers) != 1 or len(rooms) != 1 or not same_address(rooms[0]['address'], row)
                or not description or not re.match(r'MLS\s*#\s*:\s*' + re.escape(mls) + r'\b', description.get('content', ''))):
            raise StopCheck('subject_identity_mismatch', 200)
        photo = offers[0].get('image', '')
        pattern = r'https://www\.propertypanorama\.com/photos/wpn/' + re.escape(mls[:-3]) + '/' + re.escape(mls[-3:]) + r'/full/[a-f0-9]{64}\.jpg'
        evidence.update(identity_verified=True, status='verified_tour_listing',
                        photo_links=int(isinstance(photo, str) and bool(re.fullmatch(pattern, photo))))
    return evidence


def subject(repo):
    path = repo / 'properties.json'
    if not path.exists():
        raise ValueError('missing_inventory')
    rows = registry.read_json(path)
    found = [r for r in rows if isinstance(r, dict) and str(r.get('id')) == PROPERTY_ID]
    if len(found) != 1:
        raise ValueError('missing_or_ambiguous_test_property')
    row, expected = found[0], EXPECTED
    identity = registry.identity(row)
    current = (identity['street'], identity['city'], identity['state'], identity['zip'], identity['county'])
    if (current != expected or identity['unit'] or not identity['complete']
            or row.get('source_type') != 'mls' or registry.listing_id(row) != '1778408'):
        raise ValueError('test_property_identity_changed')
    aliases = repo / registry.ROOT / 'aliases.json'
    if aliases.exists() and PROPERTY_ID in registry.read_json(aliases).get('ambiguous', {}):
        raise ValueError('ambiguous_test_property')
    return row


def run(repo, mode='check', reader=None):
    if mode not in {'check', 'probe'}:
        raise ValueError('Unsupported mode')
    report = {'version': VERSION, 'mode': mode, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'new_rentcast_calls': 0, 'new_source_network_requests': 0, 'new_snapshots': 0,
              'property_id': PROPERTY_ID, 'max_requests': MAX_REQUESTS, 'results': [],
              'inventory_writes': 0, 'automatic_activation_changed': False}
    try:
        row = subject(repo)
    except ValueError as error:
        report['status'] = str(error)
        return report
    before = (repo / 'properties.json').read_bytes()
    report['properties_sha256'] = hashlib.sha256(before).hexdigest()
    if mode == 'check':
        report.update(status='check_ok', sources=[s['name'] for s in SOURCES])
        return report
    reader = reader or Reader()
    start_requests = reader.requests
    for spec in SOURCES:
        result = {'provider': spec['id'], 'source': spec['name'], 'source_url': spec['url'],
                  'stage': 'robots', 'source_network_requests': 0, 'technical_fields': 0,
                  'photo_links': 0, 'identity_verified': False, 'inventory_merge_allowed': False}
        start = reader.requests
        try:
            rules = reader.get(spec, spec['origin'] + '/robots.txt')
            if re.search(r'<\s*(?:!doctype|html|script)\b', rules, re.I):
                raise StopCheck('robots_response_not_rules', 200)
            robots = RobotFileParser()
            robots.parse(rules.splitlines())
            if not robots.can_fetch(AGENT, spec['url']):
                raise StopCheck('robots_disallowed')
            delay = max(INTERVAL, robots.crawl_delay(AGENT) or robots.crawl_delay('*') or 0)
            if delay > 60:
                raise StopCheck('crawl_delay_exceeds_check_limit')
            result['stage'] = 'listing'
            html = reader.get(spec, spec['url'], delay)
            result.update(inspect_page(html, spec, row), http_status=200)
        except StopCheck as error:
            result.update(status=error.status, http_status=error.http_status,
                          retry_after=error.retry_after)
        result['source_network_requests'] = reader.requests - start
        report['results'].append(result)
    report['new_source_network_requests'] = reader.requests - start_requests
    report['status'] = ('alternative_technical_source_available' if any(
        r.get('status') == 'verified_technical_listing' for r in report['results']) else
        'alternative_tour_only' if any(r.get('status') == 'verified_tour_listing' for r in report['results']) else
        'accessible_source_needs_identity_review' if any(r.get('status') == 'accessible_city_variant_needs_review' for r in report['results']) else
        'no_verified_alternative')
    if (repo / 'properties.json').read_bytes() != before:
        raise RuntimeError('Inventory changed concurrently; check results must not be applied.')
    return report


def summary(report):
    lines = ['## ALTERNATIVE_SOURCE_ACCESS_CHECK', '',
             '- New RentCast calls: **0**',
             '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
             '- מצב: ' + report['status'], '- נכס לבדיקה: ' + PROPERTY_ID,
             '- מטמונים חדשים: **0**', '- שינוי בהפעלת ההשלמה האוטומטית: **לא**', '',
             '| מקור | תוצאה | שלב | HTTP | בקשות | שדות טכניים | קישורי תמונות |',
             '|---|---|---|---|---|---:|---:|']
    for result in report['results']:
        lines.append('| ' + result['source'] + ' | ' + result['status'] + ' | ' + result['stage'] + ' | ' +
                     str(result.get('http_status') or '—') + ' | ' + str(result['source_network_requests']) + ' | ' +
                     str(result['technical_fields']) + ' | ' + str(result['photo_links']) + ' |')
    lines.extend(['', 'הבדיקה קוראת עד שני עמודים לכל מקור: robots.txt ועמוד הנכס. לפחות 10 שניות בין בקשות לאותו מקור.',
                  'Clear Choice אינו נבדק. חסימה מפסיקה את הבדיקה באותו מקור; המקורות האחרים נבדקים בנפרד.',
                  'קישורי תמונות נספרים מתוך העמוד בלבד; התמונות אינן מורדות.',
                  'זו בדיקת גישה וזיהוי בלבד. אין שמירה למאגר ואין חיבור אוטומטי למקור חלופי.',
                  'properties.json, התור, מטמוני המקור, חדר העסקאות, תקציב RentCast והסריקה נשארים ללא כתיבה.',
                  'חלופת שם עיר אינה מאוחדת אוטומטית.', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'probe'))
    parser.add_argument('--repo', type=Path, default=Path('.'))
    args = parser.parse_args()
    report = run(args.repo, args.mode)
    print(json.dumps(report, ensure_ascii=False))
    print(summary(report))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
            output.write(summary(report))


if __name__ == '__main__':
    main()
