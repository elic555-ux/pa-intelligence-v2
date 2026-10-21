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
from urllib.parse import urlparse, urljoin, parse_qs
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
    'preferredrealty': ('https://www.thepreferredrealty.com', 'Berkshire Hathaway The Preferred Realty / MLS'),
    'coldwellbanker': ('https://www.coldwellbankerhomes.com', 'Coldwell Banker / MLS'),
    'weichert': ('https://www.weichert.com', 'Weichert / MLS'),
}
PAGE_HOSTS = {'www.coldwellbankerhomes.com', 'www.weichert.com', 'www.thepreferredrealty.com'}
IMAGE_HOSTS = {'cdn.listingphotos.sierrastatic.com', 'm.cbhomes.com', 'm1.cbhomes.com',
               'm2.cbhomes.com', 'm3.cbhomes.com', 'd36xftgacqn2p.cloudfront.net', 'api.cotality.com'}
MEDIA_REDIRECT_HOST = 'media-cdn-v2.corelogic.com'
# Observed public URLs, not invented pid or image URLs. Still require full
# identity verification of the current page; a hidden address is rejected.
SEEDS = {
    '1774373': 'https://www.coldwellbankerhomes.com/pa/brackenridge/812-prospect-st/pid_73713539/',
    '1774350': 'https://www.coldwellbankerhomes.com/pa/pittsburgh/480-hamil-rd/pid_73712480/',
}
MAX_ATTEMPTS = 2
FRESH_DAYS = 7
PREFERRED_PARSER_REVISION = 2


class Unavailable(Exception):
    def __init__(self, status, http_status=None, retry_after=None, location=None):
        self.status, self.http_status, self.retry_after = status, http_status, retry_after
        self.location = location
        super().__init__(status)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unavailable('redirect_stopped', code, location=newurl)


def safe_url(value, image=False):
    try:
        u = urlparse(str(value or ''))
        hosts = IMAGE_HOSTS if image else PAGE_HOSTS
        if (u.scheme != 'https' or u.hostname not in hosts or u.username or u.password
                or u.port not in (None, 443) or u.fragment or u.query):
            return None
        if image:
            pattern = (r'/trestle/Media/Property/(?:PHOTO-Jpeg/\d+/\d+|\d+)/(?:[A-Za-z0-9_-]+/){2}[A-Za-z0-9_-]+'
                       if u.hostname == 'api.cotality.com' else r'/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|webp)')
            if not re.fullmatch(pattern, u.path, re.I):
                return None
        elif u.hostname == 'www.thepreferredrealty.com':
            if not re.fullmatch(r'/real-estate/\d{4,10}/', u.path):
                return None
        elif u.hostname == 'www.coldwellbankerhomes.com':
            if not re.fullmatch(r'/pa/[a-z0-9-]+/(?:[a-z0-9-]+/pid_\d+/)?', u.path):
                return None
        elif not re.fullmatch(r'/(?:\d+/?|PA/[A-Za-z0-9-]+/[A-Za-z0-9-]+/)', u.path):
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


def public_media_redirect(origin, target):
    """One observed media hop; its temporary signature is never persisted."""
    try:
        u = urlparse(target or '')
        query = parse_qs(u.query, keep_blank_values=True)
        return bool(urlparse(origin).hostname == 'api.cotality.com' and safe_url(origin, image=True)
            and u.scheme == 'https' and u.hostname == MEDIA_REDIRECT_HOST
            and not u.username and not u.password and not u.fragment and u.port in (None, 443)
            and re.fullmatch(r'/[a-f0-9]{20}/[A-Za-z0-9_-]+\.jpg', u.path)
            and set(query) == {'Expires', 'KeyName', 'Signature'}
            and all(len(v) == 1 for v in query.values())
            and re.fullmatch(r'\d{10}', query['Expires'][0])
            and re.fullmatch(r'[A-Za-z0-9_-]+', query['KeyName'][0])
            and re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', query['Signature'][0]))
    except ValueError:
        return False


class Reader:
    def __init__(self, max_requests=13, seconds=240, clock=time.monotonic,
                 sleep=time.sleep, opener=None):
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + min(240, max(1, seconds))
        self.max_requests = min(20, max(0, max_requests))
        self.requests, self.last, self.interval = 0, None, 10
        self.rules, self.pages, self.blocked, self.host_cooldowns = {}, {}, {}, {}
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
                self.host_cooldowns[urlparse(url).hostname] = core.cooldown_until(exc.headers.get('Retry-After'), datetime.now(timezone.utc))
            raise Unavailable('blocked' if exc.code in (401, 403) else
                              'rate_limited' if exc.code == 429 else 'http_error', exc.code, exc.headers.get('Retry-After')) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise Unavailable('unavailable') from exc

    def robots(self, url, public_media=False):
        u = urlparse(url)
        host = u.hostname
        if host in self.blocked:
            raise Unavailable('host_cooldown', self.blocked[host], self.host_cooldowns.get(host))
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
                # A real 404/410 means no robots document. Other failures stop
                # access except the narrowly scoped public-media case below.
                if exc.status == 'http_error' and exc.http_status in (404, 410):
                    self.rules[host] = None
                elif (public_media and host == MEDIA_REDIRECT_HOST and exc.status == 'blocked'
                      and exc.http_status == 403 and not exc.retry_after):
                    # The public signed-media CDN has no readable robots object.
                    # RFC 9309 §2.3.1.3 permits resources when robots returns 4xx.
                    # This exception is only for a validated public media hop;
                    # a 403 on the image itself still stops and cools the host.
                    self.rules[host] = None
                    self.blocked.pop(host, None)
                    self.host_cooldowns.pop(host, None)
                else:
                    self.blocked[host] = exc.http_status
                    self.host_cooldowns[host] = core.cooldown_until(exc.retry_after, datetime.now(timezone.utc))
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
        try:
            body, mime = self.raw(url, 3_000_000, 'image/jpeg,image/png,image/webp')
        except Unavailable as exc:
            if (exc.status != 'redirect_stopped' or exc.http_status != 302
                    or not public_media_redirect(url, exc.location)):
                raise
            self.robots(exc.location, public_media=True)
            body, mime = self.raw(exc.location, 3_000_000, 'image/jpeg,image/png,image/webp')
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
    if provider == 'preferredrealty':
        return parse_preferred(soup, row, url)
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


def parse_preferred(soup, row, url):
    """Bind the public MLS route and primary address before reading its gallery."""
    mls = str(registry.listing_id(row))
    canonical = soup.find('link', rel='canonical')
    meta = soup.find('meta', attrs={'name': 'description'})
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    address = re.match(r'^(.+?),\s*([^,]+),\s*PA\s+(\d{5})(?:-\d{4})?\s*\|', title)
    labels = re.findall(r'\bMLS\s*#\s*:\s*(\d{4,10})\b', meta.get('content', '') if meta else '')
    if (not canonical or canonical.get('href') != url or
            urlparse(url).path != '/real-estate/' + mls + '/' or labels != [mls] or not address):
        raise Unavailable('identity_mismatch')
    # This provider appends the neighborhood in parentheses to the postal city.
    city = re.sub(r'\s+\([^()]+\)$', '', address[2]).strip()
    if not address_match(row, address[1], city, 'PA', address[3]):
        raise Unavailable('identity_mismatch')
    photos = []
    for img in soup.select('.flat-gallery-single img'):
        alt = re.fullmatch(r'(.+?)\s+-\s+photo\s+\d+', img.get('alt', ''), re.I)
        if not alt or registry.parse_address(alt[1])[0].upper() != core.identity4(row)[0]:
            continue
        image = safe_url(img.get('src'), image=True)
        if image and urlparse(image).hostname == 'api.cotality.com' and image not in photos:
            photos.append(image)
    return photos[:3]


def discover(reader, row, provider):
    if provider == 'preferredrealty':
        mls = str(registry.listing_id(row) or '')
        if not re.fullmatch(r'\d{4,10}', mls):
            raise Unavailable('unsupported_listing_id')
        return SOURCES[provider][0] + '/real-estate/' + mls + '/'
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
            for parent in list(a.parents)[:3]:
                labels = re.findall(r'\bMLS\s*#?\s*(\d{4,10})\b', parent.get_text(' ', strip=True) + ' ' +
                    ' '.join(img.get('alt', '') for img in parent.find_all('img')), re.I)
                if set(labels) == {str(registry.listing_id(row))}:
                    context += ' MLS#' + str(registry.listing_id(row))
                    break
            if re.fullmatch(r'/\d+/?', urlparse(url).path) and re.search(
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


def cooldown(value, now):
    parsed = core.parse_date(value)
    return max(now + timedelta(days=1), parsed).isoformat() if parsed else core.cooldown_until(value, now)


def source_fingerprint(repo, row):
    source_url, _, urls = primary_photos(repo, row)
    return hashlib.sha256(json.dumps([source_url, urls], sort_keys=True).encode()).hexdigest()


def provider_attempts(record):
    attempts = {a.get('provider'): dict(a) for a in record.get('attempts', [])}
    old = attempts.get('preferredrealty', {})
    if (old.get('parser_revision', 1) < PREFERRED_PARSER_REVISION
            and old.get('status') == 'no_loadable_photos' and not old.get('image_failures')):
        # Reconsider galleries the previous URL parser could not read. Preserve
        # actual HTTP failures, host cooldowns and every other provider's limits.
        attempts.pop('preferredrealty', None)
    return attempts


def complete(repo, row, reader, previous=None, now=None):
    now = now or datetime.now(timezone.utc)
    checked = now.isoformat()
    valid_old = dict(previous) if same_binding(row, previous) else {}
    current_source = source_fingerprint(repo, row)
    if valid_old.get('source_fingerprint') != current_source:
        valid_old['attempts'] = []
    report = {'schema': 1, 'property_id': str(row['id']), 'listing_id': registry.listing_id(row),
        'identity': core.identity4(row), 'county': registry.identity(row)['county'],
        'inventory_source_url': registry.safe_url(row.get('url')), 'checked_at': checked,
        'source_fingerprint': current_source,
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
    attempts = provider_attempts(valid_old)
    primary_url, primary_name, cached_urls = primary_photos(repo, row)
    providers = ('preferredrealty', 'saved_source', 'coldwellbanker', 'weichert')
    providers = sorted(providers, key=lambda provider: bool(attempts.get(provider, {}).get('unsuccessful_attempts', 0)))
    for provider in providers:
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
        if provider == 'preferredrealty':
            attempt['parser_revision'] = PREFERRED_PARSER_REVISION
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
                                     'retry_after_at': cooldown(exc.retry_after, now)})
                    if exc.status in {'request_budget', 'time_budget'}:
                        raise
            attempt['image_failures'] = failures
            attempt['status'] = 'available' if any(p['provider'] == provider for p in report['photos']) else 'no_loadable_photos'
        except Unavailable as exc:
            attempt.update(status=exc.status, http_status=exc.http_status,
                           retry_after_at=cooldown(exc.retry_after, now))
        if attempt['status'] in {'request_budget', 'time_budget'}:
            attempt.pop('retry_after_at', None)
        if attempt.get('image_failures') and all(f['status'] == 'host_cooldown' for f in attempt['image_failures']):
            attempt['status'] = 'host_cooldown'
        attempt['network_requests'] = reader.requests - before
        if attempt['status'] == 'host_cooldown':
            attempt['retry_after_at'] = max([attempt.get('retry_after_at') or (now + timedelta(days=1)).isoformat()] +
                [f['retry_after_at'] for f in attempt.get('image_failures', [])])
        if attempt['status'] not in {'available', 'request_budget', 'time_budget', 'host_cooldown'}:
            attempt['unsuccessful_attempts'] += 1
            attempt['retry_after_at'] = max([attempt.get('retry_after_at') or (now + timedelta(days=1)).isoformat()] +
                [f['retry_after_at'] for f in attempt.get('image_failures', [])])
        report['attempts'].append(attempt)
        if len(report['photos']) >= 3 or attempt['status'] in {'request_budget', 'time_budget'}:
            break
    report['status'] = 'available' if report['photos'] else 'not_completed'
    report['sources_exhausted'] = all(a.get('unsuccessful_attempts', 0) >= MAX_ATTEMPTS
        for a in report['attempts']) and len(report['attempts']) == len(providers)
    return report



def queue(repo, rows, now=None):
    """Return due IDs, unfinished count and the next local wake time."""
    now = now or datetime.now(timezone.utc)
    due, pending, wake = [], 0, []
    for key, row in rows.items():
        path = Path(repo) / ROOT / (core.cache_key(key) + '.json')
        record = registry.read_json(path) if path.exists() else None
        if not same_binding(row, record):
            record = {}
        fresh = []
        for item in record.get('photos', []):
            checked = core.parse_date(item.get('checked_at'))
            if (checked and checked <= now < checked + timedelta(days=FRESH_DAYS)
                    and item.get('verification', {}).get('status') == 'image_verified'
                    and safe_url(item.get('url'), image=True)):
                fresh.append(checked + timedelta(days=FRESH_DAYS))
        if fresh:
            wake.append(min(fresh))
            continue
        attempts = provider_attempts(record)
        if record.get('source_fingerprint') != source_fingerprint(repo, row):
            attempts = {}
        open_sources = [attempts.get(provider, {}) for provider in ('saved_source', *SOURCES)
                        if attempts.get(provider, {}).get('unsuccessful_attempts', 0) < MAX_ATTEMPTS]
        if not open_sources:
            continue
        pending += 1
        retries = [core.parse_date(a.get('retry_after_at')) for a in open_sources]
        if any(r is None or r <= now for r in retries):
            # New arrivals do not starve old retries: a checked property returns
            # behind properties waiting since their publication, then rotates.
            stamp = core.parse_date(record.get('checked_at')) or core.parse_date(row.get('listed_date')) or now
            due.append((stamp, key))
        else:
            wake.append(min(retries))
    due.sort()
    return [key for _, key in due], pending, min(wake).isoformat() if wake else None


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
    old_state = registry.read_json(repo / STATE) if (repo / STATE).exists() else {}
    if hasattr(reader, 'host_cooldowns'):
        for host, value in old_state.get('host_cooldowns', {}).items():
            if host in PAGE_HOSTS | IMAGE_HOSTS | {MEDIA_REDIRECT_HOST} and core.parse_date(value) and core.parse_date(value) > now:
                reader.blocked[host] = None
                reader.host_cooldowns[host] = value
    result = {'schema': 1, 'new_rentcast_calls': 0, 'results': [], 'new_source_network_requests': 0}
    before = reader.requests
    requested = list(dict.fromkeys(ids))[:3]
    total_cap = getattr(reader, 'max_requests', None)
    if total_cap is not None:
        # Do not divide a small HTTP budget into turns too short to reach a
        # backup image. Fewer complete turns beat endlessly repeated robots/pages.
        requested = requested[:max(1, (total_cap - reader.requests) // 8)]
    for index, key in enumerate(requested):
        if key not in targets:
            result['results'].append({'property_id': key, 'status': 'unsupported_or_ambiguous'})
            continue
        path = repo / ROOT / (core.cache_key(key) + '.json')
        previous = registry.read_json(path) if path.exists() else None
        if total_cap is not None:
            remaining_targets = len(requested) - index
            reader.max_requests = reader.requests + max(0, (total_cap - reader.requests) // remaining_targets)
        row_before = reader.requests
        record = complete(repo, targets[key], reader, previous=previous, now=now)
        record['new_source_network_requests'] = reader.requests - row_before
        if total_cap is not None:
            reader.max_requests = total_cap
        result['results'].append(record)
        if save:
            registry.atomic_json(path, record)
    if save and hasattr(reader, 'host_cooldowns'):
        registry.atomic_json(repo / STATE, {'schema': 1, 'updated_at': now.isoformat(),
            'host_cooldowns': reader.host_cooldowns})
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
