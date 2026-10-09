#!/usr/bin/env python3
"""Offline property identity and source-observation registry, pilot stage 1.

No HTTP client, credentials, paid API, or writes to properties.json are used.
Source observations remain separate. Ambiguous joins and conflicting facts
are reported, never resolved by overwriting scanner or deal-room records.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

VERSION = "property-sources-1.1.0-20261009"
ROOT = Path("COMPS_REPORTS/property_sources")
SUFFIXES = {"street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
            "place": "pl", "boulevard": "blvd", "lane": "ln", "court": "ct",
            "terrace": "ter", "north": "n", "south": "s", "east": "e", "west": "w"}
STRUCTURE = ("beds", "baths", "sqft", "year_built", "lot_size", "property_type")
TECHNICAL = ("occupancy", "roof_type", "roof_condition", "heating", "cooling",
             "hvac_type", "parking", "construction", "basement", "stories", "water", "sewer")
FIELDS = STRUCTURE + TECHNICAL + ("price", "market_status", "lot_area_acres", "parking_spaces", "total_rooms", "style")
NUMERIC = {"beds", "baths", "sqft", "year_built", "lot_size", "price", "stories", "lot_area_acres", "parking_spaces", "total_rooms"}
COUNTIES = {"allegheny": "Allegheny", "erie": "Erie"}
UNITS = {"sqft": "sqft", "lot_size": "sqft", "price": "USD", "lot_area_acres": "acre"}
BAD_STATUS = re.compile(r"estimated|simulated|placeholder|unverified|default", re.I)


def digest(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def norm(value):
    text = str(value or "").casefold().strip()
    text = re.sub(r"(?<=[a-z])\.(?=\s|$)", "", text)
    return " ".join(text.split())


def safe_url(value):
    try:
        p = urlparse(str(value or ""))
        if (p.scheme == "https" and p.hostname and not p.username and not p.password
                and p.port in (None, 443)):
            return p._replace(fragment="").geturl()
    except ValueError:
        pass
    return None


def parse_address(value):
    text = norm(value).replace(",", " ")
    text = re.sub(r"#\s*", " unit ", text)
    text = re.sub(r"\b(?:apartment|apt|suite)\.?\s+", "unit ", text)
    match = re.search(r"\bunit\s+([a-z0-9]+(?:[-/][a-z0-9]+)*)\s*$", text)
    unit = match.group(1) if match else None
    if match:
        text = text[:match.start()]
    street = " ".join(SUFFIXES.get(w, w) for w in text.split())
    complete = bool(re.fullmatch(r"\d+[a-z]?(?:-\d+[a-z]?)?(?:\s+\d+/\d+)?\s+[a-z0-9].+", street))
    # A partial/unrecognized unit expression must not become a whole-building join.
    if "unit" in street.split() or "#" in street:
        complete = False
    return street, unit, complete


def canonical_parcel(value, county):
    text = norm(value)
    # The existing Allegheny scanner represents one parcel as both 83-A-285
    # and the portal PIN 0083A00285000000. Erie uses a different format.
    if county == "Allegheny":
        match = re.fullmatch(r"(\d{1,4})[-\s]+([a-z]{1,2})[-\s]+(\d{1,5})", text)
        if match:
            ward, section, lot = match.groups()
            return f"{int(ward):04d}{section}{int(lot):05d}000000"
    return re.sub(r"[^a-z0-9]", "", text)


def identity(row):
    street, unit, complete = parse_address(row.get("address"))
    explicit_unit = norm(row.get("unit") or row.get("unit_number")) or None
    if explicit_unit:
        if not re.fullmatch(r"[a-z0-9]+(?:[-/][a-z0-9]+)*", explicit_unit) or (unit and unit != explicit_unit):
            complete = False
        else:
            unit = explicit_unit
    county = COUNTIES.get(norm(row.get("county")).removesuffix(" county"))
    zip_code = str(row.get("zip") or "").strip()[:5]
    state = str(row.get("state") or row.get("source_state") or "PA").upper()
    parcels = row.get("parcel_ids")
    if not isinstance(parcels, list):
        parcels = [row.get("parcel_id")] if row.get("parcel_id") else []
    parcels = sorted({canonical_parcel(p, county) for p in parcels if p} - {""})
    property_type = norm(row.get("property_type") or row.get("type"))
    missing_unit = not unit and any(w in property_type for w in ("condo", "apartment", "co-op"))
    return {"street": street, "unit": unit, "city": norm(row.get("city")),
            "zip": zip_code, "state": state, "county": county, "parcels": parcels,
            "mls_board": norm(row.get("mls_board") or row.get("source_mls_board")) or None,
            "listing_id": listing_id(row),
            "complete": bool(complete and county and state == "PA" and re.fullmatch(r"\d{5}", zip_code)
                             and norm(row.get("city")) and not missing_unit)}


def listing_id(row):
    value = str(row.get("listing_id") or row.get("docket_id") or row.get("id") or "")
    m = re.fullmatch(r"(?:(?:PA-)?MLS-)?(\d+)", value)
    return m.group(1) if m else None


def address_key(subject):
    return tuple(subject.get(k) for k in ("street", "unit", "city", "state", "zip", "county"))


def match_reason(a, b):
    """Return a strong join reason or None. No fuzzy/geographic-nearness join."""
    if not a.get("complete") or not b.get("complete"):
        return None
    if any(a.get(k) != b.get(k) for k in ("street", "unit", "state", "zip", "county")):
        return None
    ap, bp = a.get("parcels") or [], b.get("parcels") or []
    if ap and bp and ap != bp:
        return None
    if ap and ap == bp:
        return "same_county_parcel_set_and_unit"
    if (a.get("mls_board") and a.get("mls_board") == b.get("mls_board")
            and a.get("listing_id") and a.get("listing_id") == b.get("listing_id")):
        return "same_board_listing_and_subject"
    if address_key(a) == address_key(b):
        return "exact_full_address_and_unit"
    return None


def provider(url, name):
    host = (urlparse(url or "").hostname or "").removeprefix("www.")
    return host or norm(name) or "unknown"


def clean_value(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or norm(value) in {"unknown", "n/a", "none", "null", "-", "לא ידוע", "לא פורסם", "לא אומת"}:
        return None
    return value[:1000]


def make_observation(row, source_name, source_url, kind, facts, checked_at=None, as_of=None,
                     source_record_id=None, method=None, photos=None):
    subject = identity(row)
    source_url = safe_url(source_url)
    output = {"subject": subject, "legacy_property_id": str(row.get("id") or ""),
              "source_name": str(source_name or "unknown")[:160], "source_url": source_url,
              "provider": provider(source_url, source_name), "kind": kind,
              "source_record_id": str(source_record_id or row.get("id") or ""),
              "checked_at": checked_at, "source_as_of": as_of, "method": method,
              "facts": {}, "photos": deepcopy(photos or [])[:20]}
    for field, entry in facts.items():
        if field not in FIELDS:
            continue
        entry = entry if isinstance(entry, dict) else {"value": entry}
        if entry.get("source_url") and safe_url(entry["source_url"]) != source_url:
            continue
        if entry.get("property_id") and str(entry["property_id"]) != str(row.get("id") or ""):
            continue
        if entry.get("listing_id") and str(entry["listing_id"]) != listing_id(row):
            continue
        value = clean_value(entry.get("value"))
        if value is None or BAD_STATUS.search(str(entry.get("status") or "")):
            continue
        if field in TECHNICAL and not (entry.get("source") or kind == "county_record"):
            continue
        if not source_url:
            continue
        output["facts"][field] = {"value": value, "unit": entry.get("unit") or UNITS.get(field),
                                  "status": entry.get("status") or "reported_by_source",
                                  "checked_at": entry.get("checked_at") or checked_at,
                                  "source_as_of": entry.get("source_as_of") or as_of,
                                  "method": entry.get("method") or method}
        if isinstance(entry.get("excerpt"), str):
            output["facts"][field]["excerpt"] = entry["excerpt"][:300]
    # Photos are referenced with provenance; bytes are not downloaded or generated.
    output["photos"] = [{**p, "url": safe_url(p["url"]), "source_url": source_url,
                          "source": p.get("source") or source_name,
                          "retrieved_at": p.get("retrieved_at") or checked_at}
                         for p in output["photos"] if isinstance(p, dict) and source_url
                         and safe_url(p.get("url")) and
                         (not p.get("source_url") or safe_url(p["source_url"]) == source_url)]
    output["id"] = "observation-" + digest(output)
    return output


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def secondary_source_url(value):
    url = safe_url(value)
    if not url:
        return None
    p = urlparse(url)
    if (p.hostname == "www.clearchoiceenterprises.com" and not p.query
            and re.fullmatch(r"/idx/[a-z0-9-]+/\d+_spid/", p.path)):
        return url
    return None


def secondary_record_matches(row, record):
    return bool(isinstance(record, dict) and record.get("provider") == "clearchoice"
        and record.get("status") == "published" and record.get("property_id") == str(row.get("id"))
        and record.get("listing_id") == listing_id(row)
        and safe_url(row.get("url")) and safe_url(row.get("url")) == safe_url(record.get("inventory_source_url"))
        and secondary_source_url(record.get("source_url"))
        and identity(row)["complete"] and (record.get("subject") or {}).get("complete")
        and address_key(identity(row)) == address_key(record["subject"]))


def collect(repo):
    rows = read_json(repo / "properties.json")
    if not isinstance(rows, list):
        raise ValueError("properties.json must be an array")
    observations, skipped, details_bound, details_rejected, county_rejected = [], 0, 0, 0, 0
    by_id, by_address = defaultdict(list), defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            skipped += 1
            continue
        subject = identity(row)
        if subject["county"] not in COUNTIES.values():
            skipped += 1
            continue
        by_id[str(row["id"])].append(row)
        by_address[address_key(subject)].append(row)
        kind = row.get("source_type") or "inventory_record"
        facts = {k: row[k] for k in STRUCTURE if clean_value(row.get(k)) is not None}
        # Court/repository amounts never become an asking price.
        if kind in {"mls", "reo"}:
            facts.update({k: row[k] for k in ("price", "market_status") if row.get(k) is not None})
        for field, entry in (row.get("technical_facts") or {}).items():
            if field in TECHNICAL and isinstance(entry, dict) and safe_url(entry.get("source_url")) == safe_url(row.get("url")):
                if not entry.get("listing_id") or entry["listing_id"] == listing_id(row):
                    facts[field] = entry
        observations.append(make_observation(row, row.get("source"), row.get("url"), kind, facts,
            row.get("last_source_check") or row.get("last_seen"), row.get("source_published_date"),
            row.get("docket_id") or row.get("id"), "existing_scanner_record"))
        county = row.get("county_property_data")
        if isinstance(county, dict) and safe_url(county.get("source_url")):
            county_parcel = county.get("parcel_id") or county.get("pin")
            county_row = dict(row)
            county_row["parcel_ids"] = [county_parcel] if county_parcel else []
            county_row["parcel_id"] = county_parcel
            if county.get("address"):
                county_row["address"] = county["address"]
            county_subject = identity(county_row)
            pin_parameters = parse_qs(urlparse(county["source_url"]).query)
            url_pin = (pin_parameters.get("pin") or pin_parameters.get("ID") or [None])[0]
            pin_matches = (not url_pin or not county_parcel or
                           canonical_parcel(url_pin, subject["county"]) == canonical_parcel(county_parcel, subject["county"]))
            parcel_matches = (bool(subject["parcels"] and county_subject["parcels"]) and
                              set(county_subject["parcels"]).issubset(subject["parcels"]))
            address_matches = bool(county.get("address") and match_reason(subject, county_subject))
            if not pin_matches or not (parcel_matches or address_matches):
                county_rejected += 1
                continue
            cf = {k: county[k] for k in STRUCTURE if county.get(k) is not None}
            for dest, src in (("hvac_type", "heating_cooling"), ("roof_type", "roof_type"),
                              ("basement", "basement"), ("stories", "stories")):
                if county.get(src) is not None:
                    cf[dest] = county[src]
            observations.append(make_observation(county_row, county.get("source"), county["source_url"],
                "county_record", cf, county.get("retrieved_at"), county.get("source_as_of"),
                county_parcel, "existing_county_record"))
    for path in sorted((repo / "COMPS_REPORTS/listing_details").glob("*.json")):
        record = read_json(path)
        if not isinstance(record, dict):
            raise ValueError("listing detail snapshot must be an object")
        candidates = by_id.get(str(record.get("property_id") or ""), [])
        matched = []
        for row in candidates:
            key = record.get("identity") or []
            if len(key) != 4:
                continue
            record_row = {**row, "address": key[0], "city": key[1], "state": key[2], "zip": key[3]}
            if (address_key(identity(row)) == address_key(identity(record_row))
                    and safe_url(row.get("url")) == safe_url(record.get("source_url"))
                    and listing_id(row) == record.get("listing_id")):
                matched.append(row)
        if len(matched) != 1 or record.get("status") != "published":
            details_rejected += 1
            continue
        row = matched[0]
        details_bound += 1
        facts = {}
        for key, entry in (record.get("facts") or {}).items():
            if (isinstance(entry, dict) and entry.get("listing_id") == record.get("listing_id")
                    and safe_url(entry.get("source_url")) == safe_url(record.get("source_url"))):
                facts[key] = entry
        observations.append(make_observation(row, "Redfin / MLS", record["source_url"], "listing_details",
            facts, record.get("retrieved_at"), record.get("source_updated_at"), record["listing_id"],
            record.get("method"), record.get("photos")))
    for path in sorted((repo / "COMPS_REPORTS/internal_data/profiles").glob("*.json")):
        record = read_json(path)
        if not isinstance(record, dict) or record.get("status") != "matched":
            continue
        row = {**(record.get("subject") or {}), "county": record.get("county"), "parcel_id": record.get("parcel_id")}
        candidates = by_address.get(address_key(identity(row)), [])
        if len(candidates) != 1:
            continue
        row["id"] = candidates[0]["id"]
        observations.append(make_observation(row, record.get("source_name"), record.get("source_url"),
            "county_record", record.get("fields") or {}, record.get("retrieved_at"),
            record.get("source_as_of"), record.get("parcel_id"), record.get("basis"), record.get("photos")))
    secondary_bound, secondary_rejected = 0, 0
    for path in sorted((repo / "COMPS_REPORTS/additional_sources/clearchoice").glob("*.json")):
        record = read_json(path)
        candidates = by_id.get(str(record.get("property_id") or ""), []) if isinstance(record, dict) else []
        if len(candidates) != 1 or not secondary_record_matches(candidates[0], record):
            secondary_rejected += 1
            continue
        row, facts, photos = candidates[0], {}, []
        for field, entry in (record.get("facts") or {}).items():
            if (isinstance(entry, dict) and entry.get("listing_id") == listing_id(row)
                    and entry.get("property_id") == str(row["id"])
                    and safe_url(entry.get("source_url")) == safe_url(record["source_url"])):
                facts[field] = entry
        for photo in record.get("photos") or []:
            if not isinstance(photo, dict) or photo.get("listing_id") != listing_id(row):
                continue
            url = safe_url(photo.get("url"))
            p = urlparse(url or "")
            match = re.fullmatch(r"/(?:pics[123]x|large)/v\d+/\d+/\d+_(\d+)_(\d{2,3})\.jpg", p.path)
            if (url and p.hostname == "cdn.listingphotos.sierrastatic.com" and not p.query
                    and match and match[1] == listing_id(row)
                    and safe_url(photo.get("source_url")) == safe_url(record["source_url"])):
                photos.append(photo)
        secondary_bound += 1
        observations.append(make_observation(row, record.get("source_name"), record["source_url"],
            "additional_listing_details", facts, record.get("retrieved_at"), record.get("source_updated_at"),
            record["listing_id"], record.get("method"), photos[:3]))
    return observations, {"inventory_rows": len(rows), "out_of_scope_or_invalid_rows": skipped,
                           "bound_listing_snapshots": details_bound, "excluded_listing_snapshots": details_rejected,
                           "excluded_county_snapshots": county_rejected,
                           "bound_additional_snapshots": secondary_bound, "excluded_additional_snapshots": secondary_rejected}


def fact_key(field, entry):
    value = entry["value"]
    if field in NUMERIC:
        try:
            number = float(str(value).replace(",", ""))
            if math.isfinite(number):
                value = number
        except ValueError:
            pass
    elif field == "property_type":
        # Compare known enum aliases while retaining each source's original text.
        aliases = {"single family": "single_family", "single-family": "single_family",
                   "single family residential": "single_family", "בית חד משפחתי": "single_family"}
        value = aliases.get(norm(value), value)
    return digest([value if isinstance(value, (int, float)) else norm(value), entry.get("unit")])


def aggregate(observations):
    """Keep contradictory values, even from the same provider, for review."""
    fields = defaultdict(list)
    for observation in observations:
        for field, entry in observation["facts"].items():
            fields[field].append({**entry, "observation_id": observation["id"],
                                  "source_name": observation["source_name"],
                                  "source_url": observation["source_url"],
                                  "method": entry.get("method") or observation["method"]})
    result = {}
    for field, entries in fields.items():
        values = {fact_key(field, e) for e in entries}
        result[field] = {"status": "conflicting_source_values" if len(values) > 1 else "reported_by_source",
                         "value": entries[0]["value"] if len(values) == 1 else None,
                         "unit": entries[0].get("unit"), "observations": entries}
    return result


def build_registry(observations):
    unique = {o["id"]: o for o in observations}
    observations = sorted(unique.values(), key=lambda o: o["id"])
    buckets = defaultdict(list)
    for i, o in enumerate(observations):
        s = o["subject"]
        if not s["complete"]:
            continue
        buckets[("address", address_key(s))].append(i)
        if s["parcels"]:
            buckets[("parcels", s["county"], tuple(s["parcels"]), s["unit"])].append(i)
        if s["mls_board"] and s["listing_id"]:
            buckets[("listing", s["mls_board"], s["listing_id"])].append(i)
    parent = list(range(len(observations)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for values in buckets.values():
        for i, j in itertools.combinations(values, 2):
            if match_reason(observations[i]["subject"], observations[j]["subject"]):
                parent[find(j)] = find(i)
    components = defaultdict(list)
    for i, o in enumerate(observations):
        components[find(i)].append(o)
    entities, ambiguity = {}, []
    for component in components.values():
        incompatible = any(not match_reason(a["subject"], b["subject"])
                           for a, b in itertools.combinations(component, 2))
        groups = [[o] for o in component] if incompatible else [component]
        if incompatible:
            ambiguity.append({"reason": "transitive_identity_conflict", "observation_ids": [o["id"] for o in component]})
        for group in groups:
            # Use the richest compatible subject rather than a timestamp-dependent
            # observation order. A parcel set identifies a bundle, not one of its lots.
            subject = min((o["subject"] for o in group), key=lambda s: (not bool(s["parcels"]), digest(s)))
            seed = list(min(address_key(o["subject"]) for o in group))
            seed += [subject["parcels"]]
            # Conflicting parcel claims at the same address must remain separate.
            if incompatible or not subject["complete"]:
                seed += [group[0]["id"]]
            entity_id = "pa-property-" + digest(seed)
            if entity_id in entities:
                raise ValueError("unexpected entity identity collision")
            entities[entity_id] = {"entity_id": entity_id, "subject": deepcopy(subject),
                "legacy_property_ids": sorted({o["legacy_property_id"] for o in group if o["legacy_property_id"]}),
                "observation_ids": [o["id"] for o in group],
                "identity_status": "ambiguous" if incompatible else "matched" if subject["complete"] else "incomplete",
                "sources": sorted({o["source_url"] for o in group if o["source_url"]}),
                "fields": aggregate(group), "photos": [p for o in group for p in o["photos"]]}
    same_address = defaultdict(list)
    for eid, entity in entities.items():
        if entity["subject"]["complete"]:
            same_address[address_key(entity["subject"])].append(eid)
    for ids in same_address.values():
        if len(ids) > 1:
            ambiguity.append({"reason": "separate_identity_claims_at_same_address", "entity_ids": ids})
            for eid in ids:
                entities[eid]["identity_status"] = "ambiguous"
    aliases = defaultdict(list)
    for eid, entity in entities.items():
        for alias in entity["legacy_property_ids"]:
            aliases[alias].append(eid)
    ambiguous_aliases = {key: value for key, value in aliases.items() if len(value) != 1
                         or entities[value[0]]["identity_status"] != "matched"}
    return {"entities": entities, "observations": unique,
            "aliases": {key: value[0] for key, value in aliases.items() if key not in ambiguous_aliases},
            "ambiguous_aliases": ambiguous_aliases, "identity_review": ambiguity}


def preserve_entity_ids(repo, registry):
    """Retain a prior ID only when one compatible entity claims it uniquely."""
    folder = repo / ROOT / "entities"
    previous = {}
    for path in sorted(folder.glob("*.json")):
        previous.update(read_json(path))
    if not previous:
        return
    by_alias = defaultdict(set)
    for eid, entity in previous.items():
        if entity.get("identity_status") == "matched":
            for alias in entity.get("legacy_property_ids", []):
                by_alias[alias].add(eid)
    proposed, claimed = {}, defaultdict(list)
    for eid, entity in registry["entities"].items():
        if entity["identity_status"] != "matched":
            continue
        candidates = set().union(*(by_alias[a] for a in entity["legacy_property_ids"]))
        candidates = {old for old in candidates if match_reason(previous[old]["subject"], entity["subject"])}
        if len(candidates) == 1:
            old = next(iter(candidates))
            proposed[eid] = old
            claimed[old].append(eid)
    remap = {new: old for new, old in proposed.items() if len(claimed[old]) == 1
             and (old not in registry["entities"] or old == new)}
    if not remap:
        return
    registry["entities"] = {remap.get(eid, eid): {**entity, "entity_id": remap.get(eid, eid)}
                            for eid, entity in registry["entities"].items()}
    registry["aliases"] = {alias: remap.get(eid, eid) for alias, eid in registry["aliases"].items()}


def prepare(repo):
    observations, audit = collect(repo)
    registry = build_registry(observations)
    preserve_entity_ids(repo, registry)
    entities = registry["entities"].values()
    sources = {o["provider"] for o in observations if o["source_url"]}
    manifest = {"schema": 1, "version": VERSION, "mode": "offline_identity_pilot",
        "new_rentcast_calls": 0, "new_network_requests": 0, **audit,
        "observations": len(registry["observations"]), "entities": len(registry["entities"]),
        "matched_aliases": len(registry["aliases"]), "ambiguous_aliases": len(registry["ambiguous_aliases"]),
        "multi_source_entities": sum(len(e["sources"]) > 1 for e in entities),
        "field_conflicts": sum(f["status"] == "conflicting_source_values" for e in entities for f in e["fields"].values()),
        "source_domains": sorted(sources),
        "properties_sha256": hashlib.sha256((repo / "properties.json").read_bytes()).hexdigest(),
        "input_fingerprint": digest(sorted(registry["observations"]))}
    return registry, manifest


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == body:
        return
    temp = path.with_suffix(".tmp")
    temp.write_text(body, encoding="utf-8")
    temp.replace(path)


def save(repo, registry, manifest):
    output = repo / ROOT
    # Observation snapshots are content-addressed and retained across builds.
    for oid, observation in registry["observations"].items():
        atomic_json(output / "observations" / (oid + ".json"), observation)
    for prefix in "0123456789abcdef":
        shard = {eid: entity for eid, entity in registry["entities"].items() if eid.removeprefix("pa-property-")[0] == prefix}
        atomic_json(output / "entities" / (prefix + ".json"), shard)
    atomic_json(output / "aliases.json", {"matched": registry["aliases"], "ambiguous": registry["ambiguous_aliases"]})
    atomic_json(output / "identity_review.json", registry["identity_review"])
    atomic_json(output / "manifest.json", manifest)


def lookup(repo, legacy_property_id):
    """Future UI adapters can read this without changing saved deal-room IDs."""
    output = repo / ROOT
    aliases = read_json(output / "aliases.json")
    if legacy_property_id in aliases.get("ambiguous", {}):
        return {"status": "ambiguous_identity"}
    entity_id = aliases.get("matched", {}).get(legacy_property_id)
    if not entity_id:
        return {"status": "not_indexed"}
    shard = entity_id.removeprefix("pa-property-")[0]
    entity = read_json(output / "entities" / (shard + ".json")).get(entity_id)
    return {"status": "matched", "entity": entity}


def summary_text(manifest, registry):
    lines = ["## PROPERTY_SOURCES_PILOT_OK", "", "ניסוי זיהוי נכסים ותיעוד מקורות — ללא שאיבה מהרשת.", "",
        "- New RentCast calls: **0**", "- New network requests: **0**",
        f"- גרסה: {VERSION}", f"- רשומות שנבדקו במאגר: {manifest['inventory_rows']}",
        f"- תצפיות מקור במיפוי: {manifest['observations']}",
        f"- כרטיסי זיהוי בניסוי: {manifest['entities']}",
        f"- נכסים עם יותר מקישור מקור אחד: {manifest['multi_source_entities']}",
        f"- מזהים עמומים שהופרדו לבדיקה: {manifest['ambiguous_aliases']}",
        f"- שדות עם ערכים סותרים: {manifest['field_conflicts']}", "",
        f"מקורות נוספים שהוזנו מקבצים שמורים: {manifest.get('bound_additional_snapshots', 0)}. ריצה זו אינה פונה לאתרי המקור.",
        "properties.json, חדר העסקאות, תקציב RentCast והדוחות הקיימים נשארים ללא כתיבה.", ""]
    examples = sorted((e for e in registry["entities"].values() if len(e["sources"]) > 1),
        key=lambda e: (not any(f["status"] == "conflicting_source_values" for f in e["fields"].values()),
                       e["subject"]["street"]))[:5]
    if examples:
        lines += ["| נכס | מספר קישורי מקור | שדות עם סתירה |", "|---|---:|---|"]
        for e in examples:
            address = e["subject"]["street"].replace("|", " ")
            conflicts = ", ".join(k for k, f in e["fields"].items() if f["status"] == "conflicting_source_values") or "אין"
            lines.append(f"| {address} | {len(e['sources'])} | {conflicts} |")
    if registry["ambiguous_aliases"]:
        lines += ["", "מזהים שלא יקושרו אוטומטית:", ""]
        for alias, ids in sorted(registry["ambiguous_aliases"].items())[:8]:
            addresses = sorted({registry["entities"][eid]["subject"]["street"] for eid in ids})
            lines.append(f"- {alias}: " + "; ".join(addresses))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "build"))
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    registry, manifest = prepare(args.repo)
    if args.mode == "build":
        save(args.repo, registry, manifest)
    print(json.dumps(manifest, ensure_ascii=False))
    print(summary_text(manifest, registry))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary_text(manifest, registry))


if __name__ == "__main__":
    main()
