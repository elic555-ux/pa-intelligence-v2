"""Saved subjects only: completion never discovers or promotes inventory rows."""
from collections import defaultdict, Counter
from copy import deepcopy
import math
from pathlib import Path

import property_sources as registry

REVIEW = Path('COMPS_REPORTS/scan_review/records.json')


def read_review(repo):
    path = Path(repo) / REVIEW
    if not path.exists():
        return {'schema': 1, 'items': {}}
    review = registry.read_json(path)
    if (not isinstance(review, dict) or review.get('schema') != 1
            or not isinstance(review.get('items'), dict)):
        raise ValueError('Invalid saved review records.')
    return review


def binding(row):
    return (str(row.get('id') or ''), registry.address_key(registry.identity(row)),
            registry.listing_id(row), registry.safe_url(row.get('url')))


def preserve_completion(previous, current):
    """Keep evidence for the same complete listing; invalidate changed inputs."""
    old_row, row = previous.get('record'), current.get('record')
    if not isinstance(old_row, dict) or not isinstance(row, dict):
        return
    if (row.get('source_type') != 'mls' or old_row.get('source_type') != 'mls'
            or not registry.identity(row)['complete'] or not registry.identity(old_row)['complete']
            or not registry.listing_id(row) or not registry.safe_url(row.get('url'))
            or binding(old_row) != binding(row)):
        return
    if 'filter_context' not in current and isinstance(previous.get('filter_context'), dict):
        current['filter_context'] = deepcopy(previous['filter_context'])
    if not isinstance(previous.get('completion'), dict) or not isinstance(previous.get('completed_record'), dict):
        return
    if binding(previous['completed_record']) != binding(row):
        return
    current['completion'] = deepcopy(previous['completion'])
    current['completed_record'] = deepcopy(previous['completed_record'])
    def inputs(value):
        return {k: v for k, v in value.items() if k not in {
            'last_source_check', 'last_seen', 'last_scan_id', 'seen_count', 'scan_status'}}
    if (inputs(old_row) != inputs(row)
            or previous.get('filter_context') != current.get('filter_context')):
        current['completion'].update(status='needs_revalidation',
            filter_rejection='source_record_or_filters_changed')


def load_subjects(repo):
    inventory = registry.read_json(Path(repo) / 'properties.json')
    if not isinstance(inventory, list):
        raise ValueError('Inventory must be an array.')
    rows = [deepcopy(row) for row in inventory if isinstance(row, dict)]
    inventory_by_id = defaultdict(list)
    for row in rows:
        inventory_by_id[str(row.get('id') or '')].append(row)
    groups = defaultdict(list)
    for key, item in read_review(repo)['items'].items():
        if not isinstance(item, dict) or item.get('status') != 'needs_details':
            continue
        row = item.get('record')
        if isinstance(row, dict) and row.get('source_type') == 'mls' and row.get('id'):
            groups[str(row['id'])].append((key, item))
    for property_id, items in groups.items():
        signatures = {binding(item['record']) for _, item in items}
        stored = inventory_by_id.get(property_id, [])
        if stored and len(signatures) == 1 and all(binding(row) in signatures for row in stored):
            continue  # The same listing is already handled through inventory.
        if len(signatures) != 1 or stored:
            # Keep duplicate IDs so the queue's identity gate excludes them.
            rows.extend(deepcopy(item['record']) for _, item in items)
            if not stored and len(items) == 1:
                rows.append(deepcopy(items[0][1]['record']))
            continue
        _, latest = max(items, key=lambda pair: str(pair[1].get('last_checked_at') or ''))
        row = deepcopy(latest['record'])
        row['_completion_origin'] = 'review'
        rows.append(row)
    return rows


def missing_positive(value):
    try:
        return isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0
    except (TypeError, ValueError):
        return True


def complete_record(row, snapshot):
    from orchestrator import normalize_property_type
    result = deepcopy(row)
    result.pop('_completion_origin', None)
    facts = snapshot.get('facts') or {}
    result.setdefault('completion_facts', {})
    for field in ('sqft', 'beds', 'baths'):
        fact = facts.get(field)
        if (not isinstance(fact, dict) or fact.get('property_id') != str(row['id'])
                or fact.get('listing_id') != registry.listing_id(row)
                or fact.get('source_url') != snapshot.get('source_url')
                or missing_positive(fact.get('value'))
                or field == 'sqft' and fact.get('unit') != 'sqft'):
            continue
        if missing_positive(result.get(field)):
            result[field] = fact['value']
            result['completion_facts'][field] = deepcopy(fact)
    fact = facts.get('property_type')
    if (isinstance(fact, dict) and fact.get('property_id') == str(row['id'])
            and fact.get('listing_id') == registry.listing_id(row)
            and fact.get('source_url') == snapshot.get('source_url')):
        original_type = result.get('property_type') or result.get('type') or result.get('source_property_type')
        normalized = normalize_property_type(fact.get('value'))
        if normalized and not normalize_property_type(original_type):
            result['property_type'] = normalized
            result['completion_facts']['property_type'] = deepcopy(fact)
    return result


def update_review(repo, eligible, now):
    """Keep completed candidates separate and recheck their original filters."""
    import tarasa_listing_source as source
    from orchestrator import scan_property_rejection

    review = read_review(repo)
    changed, results = 0, Counter()
    for item in review['items'].values():
        if not isinstance(item, dict) or item.get('status') != 'needs_details':
            continue
        row = item.get('record')
        if not isinstance(row, dict):
            continue
        selected = eligible.get(str(row.get('id') or ''))
        if not selected or binding(row) != binding(selected):
            continue
        snapshot = source.read_snapshot(repo, row, now)
        previous = item.get('completion')
        previous_record = item.get('completed_record')
        retrieved = source.parse_date((snapshot or {}).get('retrieved_at'))
        retained_evidence = (isinstance(previous, dict) and isinstance(previous_record, dict)
            and binding(previous_record) == binding(row) and retrieved is not None and retrieved <= now
            and previous.get('source_url') == (snapshot or {}).get('source_url')
            and previous.get('retrieved_at') == (snapshot or {}).get('retrieved_at'))
        # A settled snapshot is kept indefinitely. Recheck changed observations
        # or filters against that same evidence without refreshing the source.
        # An expired snapshot alone cannot supply a first completion.
        if not source.fresh(snapshot, now) and not retained_evidence:
            continue
        completed = complete_record(row, snapshot)
        context = item.get('filter_context')
        if (isinstance(context, dict) and isinstance(context.get('settings'), dict)
                and isinstance(context.get('scope'), dict) and 'geo_areas' in context['scope']):
            rejection = scan_property_rejection(completed, context['settings'], context['scope'])
            status = 'ready_for_review' if rejection is None else 'needs_details' if rejection in {
                'incomplete_required_data', 'property_type_unknown', 'baths_unverified',
                'location_unverified', 'geography_unverified'} else 'outside_filters'
        else:
            rejection, status = 'original_filter_context_missing', 'needs_filter_review'
        receipt = {'status': status, 'filter_rejection': rejection,
                   'source_url': snapshot['source_url'], 'retrieved_at': snapshot.get('retrieved_at')}
        if item.get('completion') != receipt or item.get('completed_record') != completed:
            item.update(completion=receipt, completed_record=completed)
            changed += 1
        results[status] += 1
    if changed:
        review['completion_updated_at'] = now.isoformat()
        registry.atomic_json(Path(repo) / REVIEW, review)
    return changed, dict(results)

