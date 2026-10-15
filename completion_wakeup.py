"""Cheap idle gate: saved inputs only, standard library, no source access."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ('id', 'source_type', 'address', 'city', 'state', 'zip', 'zipcode',
          'county', 'docket_id', 'mls_id', 'url', 'listed_date', 'market_status',
          'price', 'sqft', 'beds', 'baths', 'property_type')


def published_day(row):
    """Match the worker's saved publication-date formats without dependencies."""
    value = str(row.get('listed_date') or '').strip()
    try:
        if len(value) == 10 and value[4] == '-' and value[7] == '-':
            return datetime.strptime(value, '%Y-%m-%d').date()
        if '/' in value:
            return datetime.strptime(value, '%d/%m/%Y').date()
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    except (ValueError, TypeError):
        return None


def input_fingerprint(repo, profile, today=None):
    repo = Path(repo)
    today = today or datetime.now(timezone.utc).date()
    inventory = json.loads((repo / 'properties.json').read_text(encoding='utf-8'))
    review_path = repo / 'COMPS_REPORTS/scan_review/records.json'
    review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {'items': {}}
    if not isinstance(inventory, list) or not isinstance(review, dict) or not isinstance(review.get('items'), dict):
        raise ValueError('Invalid saved completion inputs')
    records = [(row, None) for row in inventory if isinstance(row, dict)]
    records += [(item['record'], item) for item in review['items'].values()
                if isinstance(item, dict) and item.get('status') == 'needs_details' and isinstance(item.get('record'), dict)]
    subjects, property_ids = [], set()
    for row, item in records:
        if row.get('source_type') != 'mls':
            continue
        property_ids.add(str(row.get('id') or ''))
        publication = published_day(row)
        subject = {'record': {key: row.get(key) for key in FIELDS},
                   'future_publication': publication is not None and publication > today}
        if item is not None:
            subject['filter_context'] = item.get('filter_context')
            completion = item.get('completion')
            subject['needs_revalidation'] = (isinstance(completion, dict)
                and completion.get('status') == 'needs_revalidation')
        subjects.append(json.dumps(subject, sort_keys=True))
    aliases_path = repo / 'COMPS_REPORTS/property_sources/aliases.json'
    aliases = json.loads(aliases_path.read_text(encoding='utf-8')) if aliases_path.exists() else {}
    ambiguous = aliases.get('ambiguous', {})
    if not isinstance(ambiguous, dict):
        raise ValueError('Invalid saved identity aliases')
    inputs = {'subjects': sorted(subjects), 'maxPrice': profile.get('maxPrice'),
              'startDate': (profile.get('completionSettings') or {}).get('startDate'),
              'ambiguous_subjects': sorted(property_ids.intersection(ambiguous))}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def idle_unchanged(repo, profile, state, today=None):
    return (state.get('worker_state') == 'idle'
            and state.get('input_fingerprint') == input_fingerprint(repo, profile, today))
