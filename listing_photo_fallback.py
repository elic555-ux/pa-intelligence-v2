"""Bounded photo-only fallback. Separate evidence, no inventory/financial writes.

Only published source pages and image URLs from an identity-verified listing
are accepted. No search API, credentials, guessed image names or paid service.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import property_sources as registry
import secondary_listing_source as core
from completion_subjects import load_subjects

ROOT = Path('COMPS_REPORTS/listing_photos')
STATE = ROOT / 'state.json'
AGENT = 'PA-Property-PhotoReader/1.0'
SOURCES = {
    'coldwellbanker': ('https://www.coldwellbankerhomes.com', 'Coldwell Banker / MLS'),
    'weichert': ('https://www.weichert.com', 'Weichert / MLS'),
}
PAGE_HOSTS = {'www.coldwellbankerhomes.com', 'www.weichert.com'}
IMAGE_HOSTS = {'cdn.listingphotos.sierrastatic.com', 'm.cbhomes.com', 'm1.cbhomes.com',
               'm2.cbhomes.com', 'm3.cbhomes.com', 'd36xftgacqn2p.cloudfront.net'}
# Observed public URLs, not invented pid or image URLs. Still require full
# identity verification of the current page; a hidden address is rejected.
SEEDS = {
    '1774373': 'https://www.coldwellbankerhomes.com/pa/brackenridge/812-prospect-st/pid_73713539/',
    '1774350': 'https://www.coldwellbankerhomes.com/pa/pittsburgh/480-hamil-rd/pid_73712480/',
}
MAX_ATTEMPTS = 2
FRESH_DAYS = 7


class Unavailable(Exception):
    def __init__(self, status, http_status=None, retry_after=None):
        self.status, self.http_status, self.retry_after = status, http_status, retry_after
        super().__init__(status)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unavailable('redirect_stopped', code)


def safe_url(value, image=False):
    try:
        u = urlparse(str(value or ''))
        hosts = IMAGE_HOSTS if image else PAGE_HOSTS
        if (u.scheme != 'https' or u.hostname not in hosts or u.username or u.password
                or u.port not in (None, 443) or u.fragment or u.query):
            return None
        if image:
            if not re.fullmatch(r'/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|webp)', u.path, re.I):
                return None
        elif u.hostname == 'www.coldwellbankerhomes.com':
            if not re.fullmatch(r'/pa/[a-z0-9-]+/(?:[a-z0-9-]+/pid_\d+/)?', u.path):
                return None
        elif not re.fullmatch(r'/(?:\d+/|PA/[A-Za-z0-9-]+/[A-Za-z0-9-]+/)', u.path):
            return None
        return u.geturl()
    except ValueError:
        return None


def image_type(body, content_type):
    content_type = content_type.split(';')[0].strip().lower()
    if content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
        return None
    if content_type == 'image/jpeg' and body.startswith(b'\xff\xd8\xff'):
        return content_type
    if content_type == 'image/png' and body.startswith(b'\x89PNG\r\n\x1a\n'):
        return content_type
    if (content_type == 'image/webp' and body[:4] == b'RIFF' and body[8:12] == b'WEBP'):
        return content_type
    return None


class Reader:
    def __init__(self, max_requests=13, seconds=240, clock=time.monotonic,
                 sleep=time.sleep, opener=None):
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + min(240, max(1, seconds))
        self.max_requests = min(20, max(0, max_requests))
        self.requests, self.last, self.interval = 0, None, 10
        self.rules, self.pages, self.blocked = {}, {}, {}
        self.opener = opener or build_opener(NoRedirect())

    def raw(self, url, limit, accept):
        wait = 0 if self.last is None else max(0, self.interval - (self.clock() - self.last))
        if self.requests >= self.max_requests:
            raise Unavailable('request_budget')
        if self.clock() + wait + 20 >= self.deadline:
            raise Unavailable('time_budget')
        if wait:
            self.sleep(wait)
        self.last = self.clock()
        self.requests += 1
        request = Request(url, headers={'User-Agent': AGENT, 'Accept': accept})
        try:
            with self.opener.open(request, timeout=20) as response:
                if response.status != 200:
                    raise Unavailable('http_error', response.status)
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise Unavailable('too_large')
                return body, response.headers.get('Content-Type', '')
        except HTTPError as exc:
            if exc.code in (401, 403, 429):
                self.blocked[urlparse(url).hostname] = exc.code
            raise Unavailable('blocked' if exc.code in (401, 403) else
                              'rate_limited' if exc.code == 429 else 'http_error', exc.code, exc.headers.get('Retry-After')) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise Unavailable('unavailable') from exc

    def robots(self, url):
        u = urlparse(url)
        host = u.hostname
        if host in self.blocked:
            raise Unavailable('blocked_host', self.blocked[host])
        if host not in self.rules:
            robots_url = 'https://' + host + '/robots.txt'
            try:
                body, _ = self.raw(robots_url, 250_000, 'text/plain')
                text = body.decode('utf-8', errors='replace')
                if re.search(r'<(?:html|script|!doctype)\b', text, re.I) or not re.search(
                        r'^\s*user-agent\s*:', text, re.I | re.M):
                    raise Unavailable('robots_unavailable')
                rp = RobotFileParser()
                rp.parse(text.splitlines())
                delay = rp.crawl_delay(AGENT) or rp.crawl_delay('*') or 0
                rate = rp.request_rate(AGENT) or rp.request_rate('*')
                if rate and rate.requests:
                    delay = max(delay, rate.seconds / rate.requests)
                if delay > 60:
                    raise Unavailable('robots_delay')
                self.interval = max(self.interval, delay)
                self.rules[host] = rp
            except Unavailable as exc:
                # A real 404/410 means no robots document. 403/429, HTML,
                # malformed rules and network failures are never treated as allow.
                if exc.status == 'http_error' and exc.http_status in (404, 410):
                    self.rules[host] = None
                else:
                    self.blocked[host] = exc.http_status
                    raise
        rp = self.rules[host]
        if rp is not None and not rp.can_fetch(AGENT, url):
            raise Unavailable('robots_disallowed')

    def page(self, url):
        if not safe_url(url):
            raise Unavailable('untrusted_url')
        if url not in self.pages:
            self.robots(url)
            body, mime = self.raw(url, 2_000_000, 'text/html')
            if 'html' not in mime.lower():
                raise Unavailable('not_html')
            self.pages[url] = body.decode('utf-8', errors='replace')
        return self.pages[url]

    def image(self, url):
        if not safe_url(url, image=True):
            raise Unavailable('untrusted_image')
        self.robots(url)
        body, mime = self.raw(url, 3_000_000, 'image/jpeg,image/png,image/webp')
        kind = image_type(body, mime)
        if not kind or len(body) < 100:
            raise Unavailable('not_image')
        return {'content_type': kind, 'sha256': hashlib.sha256(body).hexdigest(),
                'bytes': len(body)}


def same_binding(row, record):
    return bool(isinstance(record, dict) and record.get('schema') == 1
        and record.get('property_id') == str(row['id'])
        and record.get('listing_id') == registry.listing_id(row)
        and record.get('identity') == core.identity4(row)
        and record.get('inventory_source_url') == registry.safe_url(row.get('url'))
        and record.get('county') == registry.identity(row)['county'])


def json_nodes(soup):
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        pending = data if isinstance(data, list) else [data]
        while pending:
            item = pending.pop()
            if not isinstance(item, dict):
                continue
            yield item
            for key in ('@graph', 'mainEntity', 'about', 'item'):
                value = item.get(key)
                pending.extend(value if isinstance(value, list) else [value] if isinstance(value, dict) else [])


def address_match(row, street, city, state, zip_code):
    identity = core.identity4(row)
    candidate = {'address': street, 'city': city, 'state': state, 'zip': zip_code, 'county': row.get('county')}
    return registry.identity(candidate)['complete'] and core.identity4(candidate) == identity


def parse_listing(html, row, url, provider):
    if provider not in SOURCES or not safe_url(url) or urlparse(url).netloc != urlparse(SOURCES[provider][0]).netloc:
        raise Unavailable('untrusted_source')
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    primary_title = title + ' ' + ' '.join(h.get_text(' ', strip=True) for h in soup.find_all('h1')[:1])
    if re.search(r'address not (?:provided|disclosed)|undisclosed address', primary_title, re.I):
        raise Unavailable('identity_hidden')
    mls = registry.listing_id(row)
    # The MLS must appear as a labeled identifier in the title or primary
    # property details; a random neighboring listing link is not sufficient.
    text = soup.get_text(' ', strip=True)
    labels = re.findall(r'\bMLS\s*(?:ID|Number|#|No\.?)?\s*:?\s*(\d{4,10})\b', primary_title, re.I)
    if not labels:
        detail = re.search(r'\bMLS\s*(?:ID|Number|#|No\.?)\s*:?\s*(\d{4,10})\b', text, re.I)
        labels = [detail[1]] if detail else []
    if str(mls) not in labels:
        raise Unavailable('identity_mismatch')
    primary_address = re.search(r'(.+?),\s*([^,]+),?\s+PA\s+(\d{5})(?:-\d{4})?', title, re.I)
    if primary_address and not address_match(row, primary_address[1], primary_address[2], 'PA', primary_address[3]):
        raise Unavailable('identity_mismatch')
    candidates, subject_nodes, matched = [], [], False
    for node in json_nodes(soup):
        address = node.get('address')
        if not isinstance(address, dict):
            continue
        region = address.get('addressRegion')
        if isinstance(region, dict):
            region = region.get('name')
        if address_match(row, address.get('streetAddress'), address.get('addressLocality'),
                         region, address.get('postalCode')):
            subject_nodes.append(node)
            matched = True
    if not matched:
        # Coldwell's primary title includes the full street/city/state/ZIP.
        expected = core.identity4(row)
        pattern = r'(.+?),\s*([^,]+),?\s+PA\s+(\d{5})(?:-\d{4})?'
        address = re.search(pattern, title, re.I)
        if address:
            street = re.sub(r'\s*[-|]\s*$', '', address[1]).strip()
            matched = address_match(row, street, address[2], 'PA', address[3])
    if not matched:
        raise Unavailable('identity_mismatch')
    for node in subject_nodes:
        imgs = node.get('image') or node.get('photo') or []
        for image in imgs if isinstance(imgs, list) else [imgs]:
            candidates.append(image.get('url') or image.get('contentUrl') if isinstance(image, dict) else image)
    meta = soup.find('meta', property='og:image')
    if meta:
        candidates.append(meta.get('content'))
    street = core.identity4(row)[0]
    for img in soup.find_all('img'):
        alt = registry.parse_address(img.get('alt', '').split(',')[0].split(' - Photo')[0])[0].upper()
        if alt != street and not re.search(r'\b' + re.escape(str(mls)) + r'\b', img.get('alt', '')):
            continue
        for attr in ('src', 'data-src'):
            candidates.append(img.get(attr))
    result, seen = [], set()
    for value in candidates:
        photo = safe_url(urljoin(url, str(value or '')), image=True)
        if photo and photo not in seen:
            seen.add(photo)
            result.append(photo)
    return result[:6]


def discover(reader, row, provider):
    if provider == 'coldwellbanker' and registry.listing_id(row) in SEEDS:
        return SEEDS[registry.listing_id(row)]
    subject = registry.identity(row)
    city = re.sub(r'[^a-z0-9]+', '-', subject['city'].lower()).strip('-')
    county = subject['county'].title()
    origin = SOURCES[provider][0]
    directory = origin + ('/pa/' + city + '/' if provider == 'coldwellbanker'
                          else '/PA/' + county + '/' + city.title() + '/')
    if not safe_url(directory):
        raise Unavailable('no_candidate')
    soup = BeautifulSoup(reader.page(directory), 'html.parser')
    for a in soup.find_all('a', href=True):
        url = safe_url(urljoin(origin, a['href']))
        if not url or urlparse(url).hostname != urlparse(origin).hostname:
            continue
        if provider == 'coldwellbanker':
            parts = urlparse(url).path.split('/')
            if len(parts) == 6 and parts[4].startswith('pid_') and registry.parse_address(parts[3].replace('-', ' '))[0].upper() == core.identity4(row)[0]:
                return url
        else:
            context = a.get_text(' ', strip=True) + ' ' + str(a.get('title', '')) + ' ' + str(a.get('aria-label', ''))
            context += ' '.join(img.get('alt', '') for img in a.find_all('img'))
            if re.fullmatch(r'/\d+/', urlparse(url).path) and re.search(
                    r'\bMLS\s*#?\s*' + re.escape(str(registry.listing_id(row))) + r'\b', context, re.I):
                return url
    raise Unavailable('no_candidate')


def primary_photos(repo, row):
    import tarasa_listing_source as source
    records = source.snapshots(repo, row)
    for record in records:
        photos = [p['url'] for p in record.get('photos', []) if core.photo_url(p.get('url'), registry.listing_id(row))]
        if photos:
            return record['source_url'], record['source_name'], photos[:3]
    return None, None, []


def complete(repo, row, reader, previous=None, now=None):
    now = now or datetime.now(timezone.utc)
    checked = now.isoformat()
    valid_old = previous if same_binding(row, previous) else {}
    report = {'schema': 1, 'property_id': str(row['id']), 'listing_id': registry.listing_id(row),
        'identity': core.identity4(row), 'county': registry.identity(row)['county'],
        'inventory_source_url': registry.safe_url(row.get('url')), 'checked_at': checked,
        'photos': [], 'attempts': [], 'status': 'not_completed', 'new_rentcast_calls': 0}
    for photo in valid_old.get('photos', []):
        when = core.parse_date(photo.get('checked_at'))
        if when and timedelta(0) <= now - when < timedelta(days=FRESH_DAYS):
            if (safe_url(photo.get('url'), image=True) and photo.get('verification', {}).get('status') == 'image_verified'
                    and photo.get('listing_id') == registry.listing_id(row)):
                report['photos'].append(photo)
    if len(report['photos']) >= 3:
        report['status'] = 'cache_used'
        report['attempts'] = valid_old.get('attempts', [])
        return report
    attempts = {a.get('provider'): a for a in valid_old.get('attempts', [])}
    primary_url, primary_name, cached_urls = primary_photos(repo, row)
    for provider in ('saved_source', 'coldwellbanker', 'weichert'):
        old = attempts.get(provider, {})
        if any(p['provider'] == provider for p in report['photos']):
            report['attempts'].append(old or {'provider': provider, 'status': 'available', 'unsuccessful_attempts': 0})
            continue
        if old.get('unsuccessful_attempts', 0) >= MAX_ATTEMPTS:
            report['attempts'].append(old)
            continue
        retry = core.parse_date(old.get('retry_after_at'))
        if retry and retry > now:
            report['attempts'].append(old)
            continue
        attempt = {'provider': provider, 'checked_at': checked, 'unsuccessful_attempts': old.get('unsuccessful_attempts', 0)}
        before = reader.requests
        try:
            if provider == 'saved_source':
                source_url, name, urls = primary_url, primary_name, cached_urls
                if not urls:
                    raise Unavailable('no_saved_photos')
            else:
                source_url = discover(reader, row, provider)
                name = SOURCES[provider][1]
                urls = parse_listing(reader.page(source_url), row, source_url, provider)
            attempt['source_url'] = source_url
            failures = []
            for url in urls:
                if len(report['photos']) >= 3:
                    break
                if any(p['url'] == url for p in report['photos']):
                    continue
                try:
                    verification = reader.image(url)
                    report['photos'].append({'url': url, 'source_url': source_url, 'source_name': name,
                        'provider': provider, 'listing_id': registry.listing_id(row), 'checked_at': checked,
                        'verification': {'status': 'image_verified', **verification}})
                except Unavailable as exc:
                    failures.append({'url': url, 'status': exc.status, 'http_status': exc.http_status,
                                     'retry_after_at': core.cooldown_until(exc.retry_after, now)})
                    if exc.status in {'request_budget', 'time_budget'}:
                        raise
            attempt['image_failures'] = failures
            attempt['status'] = 'available' if any(p['provider'] == provider for p in report['photos']) else 'no_loadable_photos'
        except Unavailable as exc:
            attempt.update(status=exc.status, http_status=exc.http_status,
                           retry_after_at=core.cooldown_until(exc.retry_after, now))
        if attempt['status'] in {'request_budget', 'time_budget'}:
            attempt.pop('retry_after_at', None)
        attempt['network_requests'] = reader.requests - before
        if attempt['status'] not in {'available', 'request_budget', 'time_budget'}:
            attempt['unsuccessful_attempts'] += 1
            attempt['retry_after_at'] = max([attempt.get('retry_after_at') or (now + timedelta(days=1)).isoformat()] +
                [f['retry_after_at'] for f in attempt.get('image_failures', [])])
        report['attempts'].append(attempt)
        if len(report['photos']) >= 3 or attempt['status'] in {'request_budget', 'time_budget'}:
            break
    report['status'] = 'available' if report['photos'] else 'not_completed'
    report['sources_exhausted'] = all(a.get('unsuccessful_attempts', 0) >= MAX_ATTEMPTS
        for a in report['attempts']) and len(report['attempts']) == 3
    return report


def run(repo, ids, max_requests=13, reader=None, now=None, save=False):
    repo = Path(repo)
    now = now or datetime.now(timezone.utc)
    rows = load_subjects(repo)
    counts = {}
    for row in rows:
        counts[str(row.get('id'))] = counts.get(str(row.get('id')), 0) + 1
    targets = {str(r['id']): r for r in rows if counts[str(r['id'])] == 1 and not r.get('_idAmbiguous')
        and r.get('source_type') == 'mls' and registry.identity(r)['complete']
        and registry.identity(r)['county'] in {'Allegheny', 'Erie'}
        and registry.safe_url(r.get('url')) and registry.listing_id(r)}
    reader = reader or Reader(max_requests=max_requests)
    result = {'schema': 1, 'new_rentcast_calls': 0, 'results': [], 'new_source_network_requests': 0}
    before = reader.requests
    requested = list(dict.fromkeys(ids))[:3]
    total_cap = getattr(reader, 'max_requests', None)
    for index, key in enumerate(requested):
        if key not in targets:
            result['results'].append({'property_id': key, 'status': 'unsupported_or_ambiguous'})
            continue
        path = repo / ROOT / (core.cache_key(key) + '.json')
        previous = registry.read_json(path) if path.exists() else None
        if total_cap is not None:
            remaining_targets = len(requested) - index
            reader.max_requests = reader.requests + max(0, (total_cap - reader.requests) // remaining_targets)
        record = complete(repo, targets[key], reader, previous=previous, now=now)
        if total_cap is not None:
            reader.max_requests = total_cap
        result['results'].append(record)
        if save:
            registry.atomic_json(path, record)
    result['new_source_network_requests'] = reader.requests - before
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--id', action='append', required=True)
    parser.add_argument('--max-requests', type=int, default=13)
    parser.add_argument('--save', action='store_true', help='write photo evidence only; default is read-only trial')
    args = parser.parse_args()
    report = run(args.repo, args.id, max_requests=args.max_requests, save=args.save)
    print(json.dumps(report, ensure_ascii=False))
    for item in report['results']:
        print(item['property_id'] + ': ' + item['status'] + '; verified photos=' + str(len(item.get('photos', []))))


if __name__ == '__main__':
    main()
