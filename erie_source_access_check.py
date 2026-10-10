#!/usr/bin/env python3
"""Bounded, read-only Erie listing pilot. No caches, queue or inventory writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import property_sources as registry
import property_source_access_check as transport

VERSION = 'erie-source-access-1.0.0-20261010'
SAMPLES = (
    {'provider': 'howardhanna', 'name': 'Howard Hanna',
     'origin': 'https://www.howardhanna.com', 'property_id': 'PA-MLS-193665',
     'address': '118 Parkway Dr', 'city': 'Erie', 'zip': '16511',
     'url': 'https://www.howardhanna.com/property/118-parkway-drive-erie-pa-16511-420001626618'},
    {'provider': 'howardhanna', 'name': 'Howard Hanna',
     'origin': 'https://www.howardhanna.com', 'property_id': 'PA-MLS-193660',
     'address': '1915 Apple Dr', 'city': 'Fairview', 'zip': '16415',
     'url': 'https://www.howardhanna.com/property/1915-apple-drive-fairview-pa-16415-420000213753'},
    {'provider': 'eriemoves', 'name': 'ErieMoves / Coldwell Banker Select',
     'origin': 'https://eriemoves.com', 'property_id': 'PA-MLS-193473',
     'address': '4117 Maxwell Ave', 'city': 'Erie', 'zip': '16504',
     'url': 'https://eriemoves.com/listing/PA/Erie/4117-Maxwell-Avenue-16504/233972876'},
)
Stop = transport.StopCheck


def subjects(repo):
    rows = json.loads((repo / 'properties.json').read_text(encoding='utf-8'))
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError('invalid_inventory')
    result = []
    for spec in SAMPLES:
        matches = [r for r in rows if str(r.get('id')) == spec['property_id']]
        expected = registry.identity(dict(spec, county='Erie', state='PA'))
        if len(matches) != 1:
            raise ValueError('ambiguous_or_missing_sample: ' + spec['property_id'])
        row = matches[0]
        identity = registry.identity(row)
        if (not identity['complete'] or registry.address_key(identity) != registry.address_key(expected)
                or registry.listing_id(row) != spec['property_id'].removeprefix('PA-MLS-')):
            raise ValueError('sample_inventory_identity_changed: ' + spec['property_id'])
        result.append((spec, row))
    return result


def headline_matches(soup, row):
    headings = soup.find_all('h1')
    if len(headings) != 1:
        return False
    text = registry.norm(headings[0].get_text(' ', strip=True).replace(',', ' '))
    subject = registry.identity(row)
    suffix = re.search(r'\s+' + re.escape(subject['city']) + r'\s+pa\s+' + subject['zip'] + r'$', text)
    return bool(suffix and registry.parse_address(text[:suffix.start()])[:2]
                == (subject['street'], subject['unit']))


def inspect_page(html, spec, row):
    soup = BeautifulSoup(html, 'html.parser')
    canonical = soup.find_all('link', rel='canonical')
    if len(canonical) != 1 or canonical[0].get('href') != spec['url']:
        raise Stop('canonical_identity_mismatch', 200)
    mls = registry.listing_id(row)
    if not headline_matches(soup, row):
        raise Stop('source_identity_mismatch', 200)
    photos, names = set(), set()
    if spec['provider'] == 'howardhanna':
        title = soup.title.get_text() if soup.title else ''
        labels = soup.find_all('dt', string=lambda x: x and x.strip() == 'MLS#')
        if (not re.search(r'MLS\s*#\s*' + re.escape(mls) + r'(?!\d)', title)
                or not any(n.parent.find('dd') and n.parent.find('dd').get_text(strip=True) == mls for n in labels)):
            raise Stop('source_identity_mismatch', 200)
        heading = soup.find('h2', string=lambda x: x and x.strip() == 'Property Details')
        section = heading.find_parent('div', class_='prop-section') if heading else None
        if section is None:
            raise Stop('technical_section_missing', 200)
        text = registry.norm(section.get_text(' ', strip=True))
        patterns = {'roof_type': r'\broof\b', 'heating': r'\b(?:heat|heating)\b',
                    'cooling': r'\b(?:central air|air conditioning|cooling)\b',
                    'basement': r'\bbasement\b', 'construction': r'\b(?:siding|brick)\b',
                    'water': r'\b(?:public water|water source)\b', 'sewer': r'\bsewer\b',
                    'stories': r'\bstories\b', 'year_built': r'\bbuilt\b',
                    'style': r'\barchitecture\b', 'lot_area_acres': r'\blot acreage\b'}
        names = {name for name, pattern in patterns.items() if re.search(pattern, text)}
        for image in soup.find_all('img'):
            alt = str(image.get('alt', ''))
            if (alt.endswith(' property photo')
                    and registry.parse_address(alt[:-15])[:2] == registry.parse_address(row['address'])[:2]):
                url = registry.safe_url(image.get('src'))
                if url and urlparse(url).hostname == 'photos.prod.cirrussystem.net':
                    photos.add(url)
    elif spec['provider'] == 'eriemoves':
        graph = []
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.get_text())
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get('@graph'), list):
                graph.extend(n for n in data['@graph'] if isinstance(n, dict))
        listings = [n for n in graph if n.get('@type') == 'RealEstateListing' and n.get('url') == spec['url']]
        homes = [n for n in graph if n.get('@type') == 'House' and n.get('@id') == spec['url'] + '/#listingdata']
        cells = soup.select('.listing-spec-table .spec-cell')
        mls_cells = [c for c in cells if c.find('strong') and registry.norm(c.find('strong').get_text()) == 'mls #:']
        if (len(listings) != 1 or len(homes) != 1 or len(mls_cells) != 1
                or mls_cells[0].get_text(' ', strip=True).split()[-1] != mls
                or listings[0].get('about') != {'@id': homes[0].get('@id')}
                or not transport.same_address(homes[0].get('address', {}), row)):
            raise Stop('source_identity_mismatch', 200)
        county_cells = [c for c in cells if c.find('strong') and c.find('strong').get_text(strip=True) == 'County']
        if len(county_cells) != 1 or registry.norm(county_cells[0].get_text(' ', strip=True)) != 'county erie county':
            raise Stop('source_identity_mismatch', 200)
        sections = {'heating_cooling': 'ld_heat_cool', 'basement': 'ld_basement',
                    'exterior_features': 'ld_ext_feat', 'utilities': 'ld_util_info',
                    'parking': 'ld_parking', 'stories': 'ld_stories', 'living_area': 'ld_approx_living_area'}
        names = {name for name, identifier in sections.items() if soup.find(id=identifier)
                 and soup.find(id=identifier).find_all('li')}
        for image in homes[0].get('image', []):
            if not isinstance(image, dict):
                continue
            url = registry.safe_url(image.get('url'))
            if url and re.fullmatch(r'i\d+\.moxi\.onl', urlparse(url).hostname or ''):
                photos.add(url)
    else:
        raise Stop('unsupported_provider')
    return {'status': 'verified_technical_listing' if names else 'verified_identity_only',
            'identity_verified': True, 'technical_groups': len(names),
            'technical_names': sorted(names), 'photo_links': len(photos)}


def run(repo, mode='check', reader=None):
    repo = Path(repo)
    if mode not in ('check', 'probe'):
        raise ValueError('unsupported_mode')
    original = (repo / 'properties.json').read_bytes()
    report = {'version': VERSION, 'mode': mode, 'status': 'check_ok',
              'new_rentcast_calls': 0, 'new_source_network_requests': 0,
              'new_snapshots': 0, 'automatic_activation_changed': False,
              'properties_sha256': hashlib.sha256(original).hexdigest(), 'results': []}
    try:
        samples = subjects(repo)
    except ValueError as error:
        report['status'] = str(error)
        return report
    if mode == 'check':
        report['sample_properties'] = [spec['property_id'] for spec, row in samples]
        return report
    reader = reader or transport.Reader()
    initial = reader.requests
    rules_by_origin, stopped = {}, {}
    for spec, row in samples:
        start = reader.requests
        result = {'property_id': spec['property_id'], 'source': spec['name'],
                  'source_url': spec['url'], 'stage': 'robots', 'identity_verified': False,
                  'technical_groups': 0, 'photo_links': 0, 'source_network_requests': 0}
        try:
            if spec['origin'] in stopped:
                raise Stop('source_stopped_after_' + stopped[spec['origin']])
            if spec['origin'] not in rules_by_origin:
                rules = reader.get(spec, spec['origin'] + '/robots.txt')
                if re.search(r'<\s*(?:!doctype|html|script)\b', rules, re.I):
                    raise Stop('robots_response_not_rules', 200)
                parser = RobotFileParser()
                parser.parse(rules.splitlines())
                rules_by_origin[spec['origin']] = parser
            parser = rules_by_origin[spec['origin']]
            if not parser.can_fetch(transport.AGENT, spec['url']):
                raise Stop('robots_disallowed')
            delay = max(10, parser.crawl_delay(transport.AGENT) or parser.crawl_delay('*') or 0)
            if delay > 60:
                raise Stop('crawl_delay_exceeds_check_limit')
            result['stage'] = 'listing'
            result.update(inspect_page(reader.get(spec, spec['url'], delay), spec, row), http_status=200)
        except Stop as error:
            result.update(status=error.status, http_status=error.http_status, retry_after=error.retry_after)
            if result['stage'] == 'robots' or error.status in {'source_blocked', 'source_rate_limited', 'redirect_stopped'}:
                stopped[spec['origin']] = error.status
        result['source_network_requests'] = reader.requests - start
        report['results'].append(result)
    report['new_source_network_requests'] = reader.requests - initial
    report['status'] = 'erie_source_available' if any(r.get('status') == 'verified_technical_listing'
                                                   for r in report['results']) else 'no_verified_erie_source'
    if (repo / 'properties.json').read_bytes() != original:
        raise RuntimeError('Inventory changed during checking; results cannot be applied.')
    return report


def summary(report):
    lines = ['## ERIE_SOURCE_ACCESS_CHECK', '', '- New RentCast calls: **0**',
             '- New source network requests: **' + str(report['new_source_network_requests']) + '**',
             '- מצב: ' + report['status'], '- מטמונים חדשים: **0**',
             '- שינוי בהפעלת ההשלמה האוטומטית: **לא**', '',
             '| נכס | מקור | תוצאה | HTTP | בקשות | קבוצות מידע טכני | קישורי תמונות |',
             '|---|---|---|---|---:|---:|---:|']
    for r in report['results']:
        lines.append('| ' + ' | '.join(str(r.get(k) if r.get(k) is not None else '—') for k in
                     ('property_id', 'source', 'status', 'http_status', 'source_network_requests',
                      'technical_groups', 'photo_links')) + ' |')
    lines.extend(['', 'שלושה נכסים קבועים; עד שש בקשות בסך הכול. לפחות 10 שניות בין בקשות לאותו מקור.',
                  'נבדקים מספר MLS, כתובת מלאה ומקור הפרסום. קבוצות מידע אינן מספר השדות בדוח.',
                  'קישורי התמונות נספרים בעמוד בלבד; התמונות אינן מורדות.',
                  'חסימה עוצרת בקשות נוספות לאותו מקור. המקור השני נבדק בנפרד.',
                  'אין כתיבה למאגר הנכסים, לתור, למטמונים, לחדר העסקאות או לתקציב RentCast.',
                  'הבדיקה אינה מחברת מקור חדש להשלמה האוטומטית.', ''])
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
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(summary(report))


if __name__ == '__main__':
    main()
