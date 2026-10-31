"""Official ACS area facts, cached by Census tract and calendar quarter.

No neighborhood/street grade is generated. Published estimates and margins of
error are kept together. A failed refresh never makes an old snapshot current.
"""
from __future__ import annotations
import json, os, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

VERSION = 'official-area-1'
VARIABLES = {
    'B19013_001': ('הכנסה חציונית למשק בית', 'USD/year'),
    'B25003_002': ('יחידות מאוכלסות בבעלות הדיירים', 'housing_units'),
    'B25003_003': ('יחידות מאוכלסות בשכירות', 'housing_units'),
    'B25002_003': ('יחידות דיור ריקות', 'housing_units'),
    'B25064_001': ('שכר דירה חציוני כולל הוצאות נלוות', 'USD/month'),
    'B23025_004': ('מועסקים אזרחיים מגיל 16', 'people'),
    'B23025_005': ('מובטלים אזרחיים מגיל 16', 'people'),
    'B15003_022': ('בעלי תואר ראשון בלבד מגיל 25', 'people'),
}
LINKS = [
    {'label': 'ביצועי בתי ספר — נתוני מדינת פנסילבניה', 'url': 'https://www.futurereadypa.org/Home/DataFiles', 'status': 'reference_only'},
    {'label': 'פשיעה — משטרת מדינת פנסילבניה', 'url': 'https://www.ucr.pa.gov/PAUCRSPUBLIC', 'status': 'reference_only'},
]

def clock(): return datetime.now(timezone.utc)
def quarter(t): return f'{t.year}-Q{(t.month-1)//3+1}'
def get_json(url):
    if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'api.census.gov':
        raise ValueError('Unexpected Census host')
    with urlopen(Request(url, headers={'User-Agent': 'PA-Intelligence-Official-Area/1'}), timeout=15) as response:
        if urlsplit(response.url).hostname != 'api.census.gov': raise ValueError('Unexpected redirect')
        body = response.read(1024*1024+1)
        if len(body) > 1024*1024: raise ValueError('Oversized Census response')
        return json.loads(body)

def tract_id(point):
    geoid = str(point.get('geoid') or '')
    if re.fullmatch(r'42003\d{6}(?:\d{1,4})?', geoid): return geoid[:11]
    tract = str(point.get('tract') or '')
    # This fallback is only for geometry already matched to an Allegheny parcel.
    if re.fullmatch(r'\d{6}', tract) and tract != '000000' and point.get('parcel_id'):
        return '42003'+tract
    return None

def parse(payload, geoid, year, observed_at):
    if not isinstance(payload, list) or len(payload) != 2: raise ValueError('Unexpected Census rows')
    header, values = payload
    if len(header) != len(values): raise ValueError('Invalid Census row')
    row = dict(zip(header, values))
    if ''.join(str(row.get(k, '')) for k in ('state','county','tract')) != geoid:
        raise ValueError('Census geography mismatch')
    def value(key):
        try:
            n = int(row[key])
            return n if n >= 0 else None  # ACS suppression/special codes are negative.
        except (ValueError, TypeError, KeyError): return None
    return {'version': VERSION, 'status': 'available', 'geoid': geoid,
        'geography_level': 'census_tract', 'geography_name': row.get('NAME'),
        'source_name': 'U.S. Census Bureau — ACS 5-year',
        'source_url': f'https://data.census.gov/profile?g=140XX00US{geoid}',
        'source_period': f'{year-4}–{year}', 'dataset_year': year,
        'observed_at': observed_at, 'checked_quarter': quarter(datetime.fromisoformat(observed_at)),
        'metrics': [{'code': code, 'label': label, 'value': value(code+'E'),
                     'margin_of_error': value(code+'M'), 'unit': unit}
                    for code,(label,unit) in VARIABLES.items()]}

def collect(point, root=Path('COMPS_REPORTS'), allow_public=True, now=None):
    now = now or clock(); geoid = tract_id(point or {})
    base = {'version': VERSION, 'status': 'not_collected', 'metrics': [],
            'reference_links': LINKS, 'class': None, 'class_verified': False,
            'neighborhood': (point or {}).get('neighborhood'), 'tract': (point or {}).get('tract')}
    if not geoid: return {**base, 'status': 'geography_unavailable'}
    path = Path(root)/'official_area'/(geoid+'.json'); cached = None
    try: cached = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): pass
    if not isinstance(cached, dict) or cached.get('version') != VERSION or cached.get('geoid') != geoid: cached = None
    if cached and cached.get('checked_quarter') == quarter(now) and datetime.fromisoformat(cached['observed_at']) <= now:
        return {**base, **cached, 'reference_links': LINKS}
    if not allow_public:
        return {**base, **(cached or {}), 'status': 'cached_stale' if cached else 'lookup_disabled'}
    api_key = os.environ.get('CENSUS_API_KEY', '').strip()
    if not api_key:
        return {**base, **(cached or {}), 'status': 'cached_stale' if cached else 'provider_not_configured'}
    fields = ['NAME']+[code+s for code in VARIABLES for s in ('E','M')]
    for year in range(now.year-1, now.year-4, -1):
        url = f'https://api.census.gov/data/{year}/acs/acs5?'+urlencode({
            'get': ','.join(fields), 'for': 'tract:'+geoid[5:], 'in': 'state:42 county:003'})
        try:
            result = parse(get_json(url+'&'+urlencode({'key':api_key})), geoid, year, now.isoformat(timespec='seconds'))
            result['api_url'] = url
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'); tmp.replace(path)
            return {**base, **result}
        except (OSError, ValueError, TypeError): continue
    return {**base, **(cached or {}), 'status': 'cached_stale' if cached else 'source_unavailable',
            'refresh_attempted_at': now.isoformat(timespec='seconds')}

def refresh_cached(root=Path('COMPS_REPORTS')):
    """Refresh only known tracts. Does not scan listings or call paid providers."""
    rows=[]
    for path in sorted((Path(root)/'official_area').glob('*.json')):
        if not re.fullmatch(r'42003\d{6}',path.stem): continue
        result=collect({'geoid':path.stem},root)
        rows.append({'geoid':path.stem,'status':result['status']})
    return rows

if __name__=='__main__':
    print(json.dumps({'areas':refresh_cached()},ensure_ascii=False))
