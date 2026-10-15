"""Cheap idle gate: saved inputs only, standard library, no source access."""
import hashlib
import json
from pathlib import Path

FIELDS = ('id', 'source_type', 'address', 'city', 'state', 'zip', 'zipcode',
          'county', 'docket_id', 'mls_id', 'url', 'listed_date', 'market_status',
          'price', 'sqft', 'beds', 'baths', 'property_type')


def input_fingerprint(repo, profile):
    repo = Path(repo)
    inventory = json.loads((repo / 'properties.json').read_text(encoding='utf-8'))
    review_path = repo / 'COMPS_REPORTS/scan_review/records.json'
    review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {'items': {}}
    if not isinstance(inventory, list) or not isinstance(review, dict) or not isinstance(review.get('items'), dict):
        raise ValueError('Invalid saved completion inputs')
    records = [row for row in inventory if isinstance(row, dict)]
    records += [item['record'] for item in review['items'].values()
                if isinstance(item, dict) and item.get('status') == 'needs_details' and isinstance(item.get('record'), dict)]
    subjects = [json.dumps({key: row.get(key) for key in FIELDS}, sort_keys=True)
                for row in records if row.get('source_type') == 'mls']
    inputs = {'subjects': sorted(subjects), 'maxPrice': profile.get('maxPrice'),
              'startDate': (profile.get('completionSettings') or {}).get('startDate')}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def idle_unchanged(repo, profile, state):
    return (state.get('worker_state') == 'idle'
            and state.get('input_fingerprint') == input_fingerprint(repo, profile))
