"""Bounded live MLS/source trial. Writes only to a temporary directory."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import sys
from urllib.parse import urlparse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
import orchestrator
import listing_source as primary
import automatic_listing_sources as completion
import property_sources as registry
import tarasa_listing_source as secondary
import secondary_listing_source as legacy
import howardhanna_listing_pilot as erie

REPO = Path.cwd()
MAX_REQUESTS = 12
MAX_PROPERTIES = 3
ALLOWED_HOSTS = {"www.redfin.com", "www.tarasa.com", "www.howardhanna.com", "cdn.listingphotos.sierrastatic.com"}
requests_log = []
denied = []
blocked_hosts = set()
original_request = requests.sessions.Session.request
original_open = urllib.request.OpenerDirector.open

def guard(url, method="GET"):
    parsed = urlparse(str(url))
    if method.upper() != "GET" or parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        denied.append({"host": parsed.hostname, "method": method})
        raise RuntimeError("Trial rejected a non-whitelisted request")
    if parsed.hostname in blocked_hosts:
        raise RuntimeError("Trial stopped requests to an access-blocked host")
    if len(requests_log) >= MAX_REQUESTS:
        raise RuntimeError("Trial network budget exhausted")
    entry = {"url": str(url), "host": parsed.hostname, "method": method, "status": None}
    requests_log.append(entry)
    return entry

def guarded_request(self, method, url, *args, **kwargs):
    entry = guard(url, method)
    kwargs["allow_redirects"] = False
    try:
        response = original_request(self, method, url, *args, **kwargs)
        entry["status"] = response.status_code
        if response.status_code in (401, 403, 405, 429):
            blocked_hosts.add(urlparse(url).hostname)
        return response
    except Exception as error:
        entry["error"] = type(error).__name__
        raise

def guarded_open(self, fullurl, *args, **kwargs):
    url = fullurl.full_url if hasattr(fullurl, "full_url") else str(fullurl)
    method = fullurl.get_method() if hasattr(fullurl, "get_method") else "GET"
    entry = guard(url, method)
    try:
        response = original_open(self, fullurl, *args, **kwargs)
        entry["status"] = getattr(response, "status", 200)
        return response
    except Exception as error:
        entry["status"] = getattr(error, "code", None)
        entry["error"] = type(error).__name__
        if entry["status"] in (401, 403, 405, 429):
            blocked_hosts.add(urlparse(url).hostname)
        raise

requests.sessions.Session.request = guarded_request
urllib.request.OpenerDirector.open = guarded_open
now = datetime.now(timezone.utc)
profile = registry.read_json(REPO / "scan_config.json")
original_hash = hashlib.sha256((REPO / "properties.json").read_bytes()).hexdigest()
report = {"candidate_source_sha": "aa0e03abec15e148722b5d07cf78ce1409ebd54a",
          "trial_at": now.isoformat(), "new_rentcast_calls": 0, "max_properties": MAX_PROPERTIES,
          "max_network_requests": MAX_REQUESTS, "production_written": False,
          "scope": "one Redfin CSV window per supported test city; at most three detail records",
          "csv": [], "selected": [], "properties": []}

def number(value):
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None

def meets_profile(row):
    if orchestrator.required_property_data_failures(row):
        return False
    if row.get("property_type") not in profile.get("propertyTypes", [row.get("property_type")]):
        return False
    for field, minimum, maximum in (("sqft", "minSqft", "maxSqft"), ("beds", "minBeds", "maxBeds")):
        value, low, high = number(row.get(field)), number(profile.get(minimum)), number(profile.get(maximum))
        if value is None or (low is not None and value < low) or (high is not None and value > high):
            return False
    return True

discovered = []
for area in ("Pittsburgh", "Erie"):
    audit = {}
    if "www.redfin.com" in blocked_hosts:
        report["csv"].append({"area": area, "status": "not_requested_after_access_block"})
        continue
    rows = orchestrator.fetch_live_mls_window(area, profile.get("minPrice", 50000), profile.get("maxPrice", 190000), audit)
    report["csv"].append(audit)
    discovered.extend(rows)
counts = Counter(str(row.get("id")) for row in discovered)
fresh = [row for row in discovered if counts[str(row.get("id"))] == 1 and meets_profile(row)
         and registry.identity(row)["complete"] and registry.listing_id(row)
         and (row.get("county") != "Allegheny" or len(registry.listing_id(row)) <= 10)
         and completion.published_day(row) and now.date() - timedelta(days=30) <= completion.published_day(row) <= now.date()]
queue = registry.read_json(REPO / completion.STATE) if (REPO / completion.STATE).exists() else {"entries": {}}
def selection_priority(row):
    if row.get("county") != "Allegheny":
        return (0, -completion.published_day(row).toordinal())
    cached = secondary.read_snapshot(REPO, row, now)
    if secondary.fresh(cached, now):
        return (3, -completion.published_day(row).toordinal())
    entry = queue.get("entries", {}).get(str(row["id"]), {})
    if entry.get("fingerprint") == completion.fingerprint(row):
        retry = secondary.parse_date(entry.get("retry_after_at"))
        if (retry and retry > now) or entry.get("status") in completion.REVIEW_STATUSES:
            return (2, -completion.published_day(row).toordinal())
    return (0, -completion.published_day(row).toordinal())
fresh.sort(key=selection_priority)
selected = []
for county, limit in (("Allegheny", 2), ("Erie", 1)):
    selected.extend([row for row in fresh if row.get("county") == county][:limit])
for row in fresh:
    if len(selected) < MAX_PROPERTIES and row not in selected:
        selected.append(row)
origin = "live_csv"
if not selected:
    origin = "existing_inventory_source_probe"
    eligible, _ = completion.eligible_rows(REPO, now.date() - timedelta(days=30), now.date())
    available = sorted(eligible.values(), key=lambda row: (secondary.fresh(secondary.read_snapshot(REPO, row, now), now),
                        -(completion.published_day(row) or now.date()).toordinal()))
    selected = [deepcopy(row) for row in available[:MAX_PROPERTIES]]
report["selection_origin"] = origin
report["selected"] = [{key: row.get(key) for key in ("id", "address", "city", "county", "zip", "listed_date", "sqft", "url")} for row in selected]

with tempfile.TemporaryDirectory(prefix="pa-live-source-trial-") as temporary:
    root = Path(temporary)
    scan_id = "LIVE-TRIAL-" + os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    for row in selected:
        row["last_scan_id"] = scan_id
        key = secondary.cache_key(row["id"])
        for directory in (secondary.ROOT, legacy.ROOT, Path("COMPS_REPORTS/additional_sources/howardhanna"),
                          Path("COMPS_REPORTS/additional_sources/eriemoves")):
            path = REPO / directory / (key + ".json")
            if path.exists():
                destination = root / directory / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        cached = primary.read_record(row, REPO)
        if cached:
            primary.write_record(row, cached, root)
    for path in (secondary.SOURCE_STATE, completion.STATE, erie.STATE, erie.source.SOURCE_STATE):
        if (REPO / path).exists():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / path, destination)
    registry.atomic_json(root / "properties.json", selected)
    registry.atomic_json(root / "scan_config.json", profile)
    event = {"scan_id": scan_id, "status": "success" if origin == "live_csv" else "failed",
             "active_sectors": ["mls"], "github_sha": os.environ.get("GITHUB_SHA", "local-trial")}
    registry.atomic_json(root / "scanner_status.json", {"last_scan": event, "last_event": event})
    report["primary"] = primary.enrich_rows(selected, root, limit=MAX_PROPERTIES,
                                           allow_network=os.environ.get("TRIAL_SKIP_BLOCKED_DETAIL_SOURCE") != "true" and "www.redfin.com" not in blocked_hosts)
    registry.atomic_json(root / "properties.json", selected)
    before = (root / "properties.json").read_bytes()
    reader = secondary.PublicReader(max_requests=4, deadline=time.monotonic() + 180)
    integrated = completion.run(root, "scan", upstream_sha=event["github_sha"], reader=reader, now=now)
    report["completion"] = integrated
    report["temporary_inventory_preserved"] = before == (root / "properties.json").read_bytes()
    if origin != "live_csv" and selected:
        report["independent_source_probe"] = secondary.run(root, "fetch", selected[0]["id"], reader=reader,
                                                          write_status=False, now=now)
    for row in selected:
        record = primary.read_record(row, root)
        retained = []
        key = secondary.cache_key(row["id"])
        for provider in registry.SECONDARY_PROVIDERS:
            path = root / "COMPS_REPORTS/additional_sources" / provider / (key + ".json")
            candidate = registry.read_json(path) if path.exists() else None
            if registry.secondary_record_matches(row, candidate):
                retained.append(candidate)
        facts = (record or {}).get("facts", {}) if (record or {}).get("status") == "published" else {}
        additional = {field: entry for saved in retained for field, entry in saved.get("facts", {}).items()}
        photos = [photo for saved in retained for photo in saved.get("photos", [])]
        report["properties"].append({"id": row["id"], "address": row["address"], "county": row.get("county"),
             "primary_status": (record or {}).get("status"), "primary_fields": len(facts),
             "primary_photos": len((record or {}).get("photos", [])) if (record or {}).get("status") == "published" else 0,
             "additional_fields": len(additional), "additional_photos": len(photos),
             "sources": [{"provider": saved["provider"], "url": saved["source_url"], "retrieved_at": saved["retrieved_at"]} for saved in retained],
             "technical_values": {field: additional.get(field) or facts.get(field) for field in
                                  ("roof_type", "heating", "cooling", "parking", "construction", "year_built", "sqft", "lot_area_acres")
                                  if additional.get(field) or facts.get(field)}})
    if any(row.get("county") == "Erie" for row in selected):
        erie_reader = erie.source.PublicReader(max_requests=min(6, MAX_REQUESTS - len(requests_log)),
                                               deadline=time.monotonic() + 180)
        report["independent_erie_source_probe"] = erie.run(root, "pilot", reader=erie_reader, now=now)
        report["erie_automatic_integration"] = False
        for row in selected:
            if row.get("county") == "Erie":
                saved = erie.source.read_snapshot(root, row, now)
                report["independent_erie_property"] = {"id": row["id"], "address": row["address"],
                    "fields": len((saved or {}).get("facts", {})), "photos": len((saved or {}).get("photos", [])),
                    "status": (saved or {}).get("status"), "source_url": (saved or {}).get("source_url")}
    observations, aliases = registry.collect(root)
    built = registry.build_registry(observations)
    report["registry"] = {"observations": len(observations), "groups": len(built) if isinstance(built, (list, dict)) else None}
    report["live_scan_integrated"] = origin == "live_csv" and integrated.get("scan_integrated") and integrated["status"] not in {"no_new_mls_scan", "automatic_disabled"}
    report["sample_details_complete"] = len(selected) == MAX_PROPERTIES and all(
        (row["primary_fields"] >= 3 or row["additional_fields"] >= 3) and
        row["primary_photos"] + row["additional_photos"] >= 1 for row in report["properties"])
    report["new_snapshots"] = integrated.get("new_snapshots", 0)
report["requests"] = requests_log
report["denied_requests"] = denied
report["production_inventory_preserved"] = original_hash == hashlib.sha256((REPO / "properties.json").read_bytes()).hexdigest()
report["production_unchanged"] = report["production_inventory_preserved"] and report["temporary_inventory_preserved"]
assert report["new_rentcast_calls"] == 0 and not denied
assert report["production_unchanged"]
assert len(requests_log) <= MAX_REQUESTS
print("LIVE_SCAN_TRIAL_JSON=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
summary = ["## Limited live source trial", "",
           "- New RentCast calls: 0", "- Production inventory preserved: " + str(report["production_inventory_preserved"]),
           "- Live scan integration tested: " + str(report["live_scan_integrated"]),
           "- All three sample properties have saved technical details/photos: " + str(report["sample_details_complete"]),
           "- New source snapshots: " + str(report["new_snapshots"]),
           "- Network requests: " + str(len(requests_log)) + "/" + str(MAX_REQUESTS),
           "- Selection basis: " + origin, ""]
for row in report["properties"]:
    summary.append("- " + row["address"] + " (" + row["county"] + "): " + str(row["additional_fields"]) +
                   " source fields, " + str(row["additional_photos"]) + " source photos")
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
        stream.write("\n".join(summary) + "\n")
