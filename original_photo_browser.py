"""Bounded original-photo collection through an ordinary public-page browser.

No authentication, stored cookies, proxy, stealth flags or challenge handling.
Access blocks stop the source. Only exact current listings can supply photos.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

import listing_source as source

AGENT = 'PA-Property-PhotoReader/1.0'


def robots_allowed(url):
    request = Request('https://www.redfin.com/robots.txt', headers={'User-Agent': AGENT})
    with urlopen(request, timeout=15) as response:
        if response.status != 200 or response.geturl() != request.full_url:
            raise ValueError('robots_unavailable')
        text = response.read(250001).decode('utf8')
    if len(text) > 250000 or '<html' in text.lower() or 'user-agent:' not in text.lower():
        raise ValueError('robots_unavailable')
    rules = RobotFileParser()
    rules.parse(text.splitlines())
    return rules.can_fetch(AGENT, url)


def collect(page, row):
    url = source.source_url(row)
    if not url or not source.listing_id(row):
        return {'property_id': str(row.get('id')), 'status': 'unsupported_source', 'photos': []}
    result = {'property_id': str(row['id']), 'source_url': url, 'photos': []}
    response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
    status = response.status if response else None
    result['http_status'] = status
    if status != 200:
        return {**result, 'status': 'source_blocked' if status in (202, 401, 403, 405, 429) else 'source_unavailable'}
    if source.source_url({'url': page.url}) != url:
        return {**result, 'status': 'source_identity_mismatch'}
    page.locator('h1').first.wait_for(timeout=8000)
    record = source.parse_listing(page.content(), row)
    # Exclude recommendation cards after the primary heading. Retain only
    # actual subject-gallery images already loaded by the browser.
    candidates = page.locator('img').evaluate_all('''images => {
        const h1 = document.querySelector('h1');
        return images.filter(img => h1 &&
            Boolean(img.compareDocumentPosition(h1) & Node.DOCUMENT_POSITION_FOLLOWING) &&
            img.complete && img.naturalWidth > 0).map(img => ({url:img.currentSrc || img.src, alt:img.alt}));
    }''')
    photos, seen = [], set()
    for image in candidates:
        if (source.identity(row)[0] not in source.norm(image['alt'])
                or not source.photo_url(image['url']) or image['url'] in seen):
            continue
        seen.add(image['url'])
        photos.append({'url': image['url'], 'source_url': url, 'source': 'Redfin / MLS',
                       'retrieved_at': record['retrieved_at'], 'capture_date': None})
        if len(photos) == 3:
            break
    record.update(facts={}, description=None, photos=photos,
                  method='public_listing_browser', status='published' if photos else 'no_published_photos')
    return {**result, 'status': record['status'], 'photos': photos, 'record': record}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--id', action='append', required=True)
    args = parser.parse_args()
    rows = json.loads((args.repo / 'properties.json').read_text(encoding='utf8'))
    selected = []
    for key in list(dict.fromkeys(args.id))[:2]:
        matches = [row for row in rows if str(row.get('id')) == key]
        if len(matches) != 1:
            raise ValueError('ambiguous_or_missing_property')
        selected.append(matches[0])
    results = []
    try:
        if not all(robots_allowed(source.source_url(row)) for row in selected[:1]):
            raise ValueError('robots_disallowed')
    except (HTTPError, ValueError, OSError) as error:
        results = [{'property_id': str(row['id']), 'status': 'robots_unavailable',
                    'error': type(error).__name__, 'http_status': getattr(error, 'code', None), 'photos': []}
                   for row in selected]
    if not results:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel='chromium', headless=True)
            context = browser.new_context(accept_downloads=False)
            page = context.new_page()
            for index, row in enumerate(selected):
                if index:
                    time.sleep(10)
                try:
                    result = collect(page, row)
                except Exception as error:
                    result = {'property_id': str(row['id']), 'status': 'source_unavailable',
                              'error': type(error).__name__, 'photos': []}
                results.append(result)
                if result['status'] == 'source_blocked':
                    break
            context.close()
            browser.close()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': 'read_only_trial', 'results': results}
    print(json.dumps(report))


if __name__ == '__main__':
    main()
