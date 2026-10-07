"""On-demand RentCast reports. No scanner imports, demos or automatic paid refresh.

The workflow commits reservations BEFORE HTTP requests. The legacy RentCast
ledger stays shared with comps_engine.py; reserved calls conservatively count
against its successful_api_calls field, including timeouts and failed calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any

import requests

ROOT = Path("COMPS_REPORTS")
ENDPOINTS = {"value": "/avm/value", "rent": "/avm/rent/long-term"}
DOCS = {"value": "https://developers.rentcast.io/reference/value-estimate",
        "rent": "https://developers.rentcast.io/reference/rent-estimate-long-term"}
VERSION = "expanded-report-8.13.0-20261007"
MULTI = {"Multi-Family", "Apartment"}


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().isoformat(timespec="seconds")


def read(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and 0 <= n <= 10**10 else None
    except (ValueError, TypeError):
        return None


def text(value, limit=180):
    if not isinstance(value, str) or len(value) > limit or re.search(r"[\x00-\x1f<>]", value):
        raise ValueError("Invalid text field")
    return value.strip()


def norm(value):
    s = re.sub(r"[^a-z0-9# ]", " ", str(value or "").lower())
    aliases = {"street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "place": "pl",
               "boulevard": "blvd", "lane": "ln", "court": "ct", "north": "n", "south": "s",
               "east": "e", "west": "w", "apartment": "unit", "apt": "unit", "suite": "unit"}
    return " ".join(aliases.get(w, w) for w in s.replace("#", " unit ").split())


def type_name(value):
    value = str(value or "").strip().lower()
    if "duplex" in value or "triplex" in value or "multi" in value:
        return "Multi-Family"
    if "apartment" in value:
        return "Apartment"
    for name in ("Single Family", "Condo", "Townhouse", "Manufactured", "Land"):
        if norm(value).replace(" ", "") == norm(name).replace(" ", "") or (name == "Single Family" and "single" in value):
            return name
    return None


def identity(p):
    return {key: p.get(key) for key in ("id", "address", "city", "state", "zip", "property_type", "beds", "baths", "sqft")}


def property_key(property_id):
    return hashlib.sha256(str(property_id).encode()).hexdigest()[:32]


def subject_signature(p):
    return hashlib.sha256(json.dumps(identity(p), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Missing request")
    request_id = text(data.get("request_id"), 80)
    if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", request_id):
        raise ValueError("Invalid request identifier")
    raw = data.get("property")
    if not isinstance(raw, dict):
        raise ValueError("Missing property")
    p = {key: text(raw.get(key, "")) for key in ("id", "address", "city", "state", "zip")}
    if not p["id"] or not re.match(r"^\d+[A-Za-z]?\s", p["address"]) or not p["city"]:
        raise ValueError("A property ID and full street address are required")
    if p["state"] != "PA" or not re.fullmatch(r"\d{5}(?:-\d{4})?", p["zip"]):
        raise ValueError("A Pennsylvania address and ZIP code are required")
    p["property_type"] = type_name(raw.get("property_type"))
    if p["property_type"] is None or p["property_type"] == "Land":
        raise ValueError("Residential property type is missing or unsupported")
    for key in ("beds", "baths", "sqft"):
        p[key] = number(raw.get(key))
        if raw.get(key) not in (None, "") and p[key] is None:
            raise ValueError("Invalid property attributes")
        if p[key] is not None and ((key == "sqft" and not 100 <= p[key] <= 100000) or (key != "sqft" and p[key] > 100)):
            raise ValueError("Invalid property attributes")
    mode = data.get("mode")
    if mode not in ("value", "rent", "both"):
        raise ValueError("Invalid report mode")
    if p["property_type"] in MULTI and mode != "value":
        raise ValueError("Multi-family rent AVMs are per unit. Request a building value report; document building rent separately.")
    return {"request_id": request_id, "property": p, "mode": mode, "refresh": data.get("refresh") is True}


def periods(day, current=None):
    current = current or now()
    year, month = current.year, current.month
    if current.day < day:
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    start = date(year, month, day)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return start.isoformat(), date(next_year, next_month, day).isoformat()


def load_usage():
    path = ROOT / "rentcast_usage.json"
    ledger = read(path)
    if not ledger:
        raise ValueError("usage_ledger_missing")
    day = ledger.get("billing_day")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
        raise ValueError("billing_cycle_not_configured")
    start, reset = periods(day)
    if ledger.get("cycle_start") != start:
        old_start = ledger.get("cycle_start")
        if not isinstance(old_start, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", old_start) or old_start > start:
            raise ValueError("usage_ledger_invalid_cycle")
        date.fromisoformat(old_start)
        history_path = ROOT / "rentcast_usage_history" / (old_start + ".json")
        if not history_path.exists():
            write(history_path, ledger)
        ledger = {"provider": "RentCast", "billing_day": day, "cycle_start": start,
                  "successful_api_calls": 0, "reserved_requests": {}, "previous_cycle": old_start}
    used = number(ledger.get("successful_api_calls"))
    if used is None or used != int(used):
        raise ValueError("usage_ledger_invalid_count")
    ledger.update(plan_limit=50, auto_stop_at=45, warning_at=36, next_reset=reset)
    return ledger


def public_usage(ledger):
    used = int(ledger["successful_api_calls"])
    return {"used": used, "limit": 50, "auto_stop_at": 45, "remaining_safe": max(0, 45 - used),
            "reserve": 5, "cycle_start": ledger["cycle_start"], "next_reset": ledger["next_reset"],
            "count_basis": "legacy_calls_plus_conservative_reserved_requests"}


def parameters(p, kind):
    params = {"address": f"{p['address']}, {p['city']}, {p['state']} {p['zip']}",
              "propertyType": p["property_type"], "maxRadius": 2, "daysOld": 90 if kind == "rent" else 180,
              "compCount": 10, "lookupSubjectAttributes": "true"}
    # RentCast requires per-unit rental attributes; whole-building rent is blocked.
    for local, remote in (("beds", "bedrooms"), ("baths", "bathrooms"), ("sqft", "squareFootage")):
        if p[local] is not None:
            params[remote] = p[local]
    return params


def cache_path(p, kind):
    key = json.dumps({"endpoint": ENDPOINTS[kind], "params": parameters(p, kind)}, sort_keys=True, separators=(",", ":"))
    return ROOT / "rentcast_cache" / ("report_" + hashlib.sha256(key.encode()).hexdigest() + ".json")


def age_days(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now() - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def cached(p, kind):
    path = cache_path(p, kind)
    candidate = read(path)
    if candidate and candidate.get("endpoint") == ENDPOINTS[kind] and candidate.get("params") == parameters(p, kind):
        return candidate, path
    # Reuse older RentCast caches only when all request attributes actually match.
    for old in sorted((ROOT / "rentcast_cache").glob("*.json")):
        candidate = read(old)
        if not candidate or candidate.get("endpoint") != ENDPOINTS[kind]:
            continue
        old_params = candidate.get("params") or {}
        if norm(candidate.get("address")) != norm(parameters(p, kind)["address"]):
            continue
        if all(old_params.get(k) == v for k, v in parameters(p, kind).items() if k not in {"compCount", "maxRadius", "daysOld"}):
            return candidate, old
    return None, path


def match_subject(p, record):
    if not isinstance(record, dict):
        return False
    address = " ".join(str(record.get(k) or "").strip() for k in ("addressLine1", "addressLine2")).strip()
    if not address:
        address = str(record.get("formattedAddress") or "").split(",")[0]
    if norm(address) != norm(p["address"]) or str(record.get("state") or "").upper() != p["state"]:
        return False
    if str(record.get("zipCode") or "")[:5] != p["zip"][:5] or norm(record.get("city")) != norm(p["city"]):
        return False
    if type_name(record.get("propertyType")) != p["property_type"]:
        return False
    for local, remote in (("beds", "bedrooms"), ("baths", "bathrooms")):
        if p[local] is not None and number(record.get(remote)) != p[local]:
            return False
    actual_sqft = number(record.get("squareFootage"))
    if p["sqft"] and (not actual_sqft or abs(actual_sqft / p["sqft"] - 1) > 0.1):
        return False
    return True


def comparable(p, row, kind):
    if not isinstance(row, dict):
        return None
    amount = number(row.get("price"))
    distance, correlation = number(row.get("distance")), number(row.get("correlation"))
    last_seen = row.get("lastSeenDate")
    age = age_days(last_seen)
    max_age = 90 if kind == "rent" else 180
    if not amount or distance is None or distance > 2 or correlation is None or not 0.8 <= correlation <= 1:
        return None
    if age is None or not 0 <= age <= max_age or type_name(row.get("propertyType")) != p["property_type"]:
        return None
    if p["beds"] is not None and (number(row.get("bedrooms")) is None or abs(number(row["bedrooms"]) - p["beds"]) > 1):
        return None
    area = number(row.get("squareFootage"))
    if p["sqft"] and (not area or not 0.75 <= area / p["sqft"] <= 1.25):
        return None
    addr = str(row.get("formattedAddress") or row.get("addressLine1") or "").strip()
    if not addr or str(row.get("state") or "").upper() != p["state"]:
        return None
    # A source listing is never relabelled as a verified closed sale/lease.
    return {"address": addr, "amount": amount, "kind": "rental_listing" if kind == "rent" else "sale_listing",
            "date": last_seen, "beds": number(row.get("bedrooms")), "baths": number(row.get("bathrooms")),
            "sqft": area, "year_built": number(row.get("yearBuilt")), "distance": distance,
            "correlation": correlation, "status": str(row.get("status") or ""), "provider": "RentCast"}


def normalized(p, kind, payload, observed_at, path, source_mode):
    if not isinstance(payload, dict) or not match_subject(p, payload.get("subjectProperty")):
        return {"status": "subject_mismatch", "estimate": None, "comparables": [], "observed_at": observed_at}
    rows = payload.get("comparables")
    rows = rows if isinstance(rows, list) else []
    usable, seen = [], set()
    for row in rows:
        item = comparable(p, row, kind)
        if item and norm(item["address"]) not in seen and norm(item["address"].split(",")[0]) != norm(p["address"]):
            seen.add(norm(item["address"]))
            usable.append(item)
    field, low, high = ("rent", "rentRangeLow", "rentRangeHigh") if kind == "rent" else ("price", "priceRangeLow", "priceRangeHigh")
    value, lo, hi = number(payload.get(field)), number(payload.get(low)), number(payload.get(high))
    age = age_days(observed_at)
    fresh = age is not None and 0 <= age <= (30 if kind == "rent" else 60)
    enough = len(usable) >= 3 and len(usable) >= math.ceil(len(rows) * 0.7)
    bounded = bool(value and lo and hi and lo <= value <= hi and (hi - lo) / value <= 0.4)
    accepted = fresh and enough and bounded
    reason = "accepted" if accepted else "stale" if not fresh else "insufficient_comparables" if not enough else "estimate_range_unreliable"
    estimate = {"value": value, "range_low": lo, "range_high": hi, "status": "provider_estimate",
                "basis": "current_market_value" if kind == "value" else "market_rent_estimate"} if accepted else None
    return {"status": reason, "estimate": estimate, "comparables": usable, "observed_at": observed_at,
            "cache_path": str(path), "source_mode": source_mode, "source_name": "RentCast",
            "source_url": DOCS[kind], "subject": payload["subjectProperty"],
            "quality": {"accepted_comparables": len(usable), "returned_comparables": len(rows),
                        "max_radius_miles": 2, "minimum_correlation": 0.8,
                        "range_width_limit": 0.4, "minimum_comparables": 3},
            "valuation_is_after_repair_evidence": False}


def prepare(event_path: Path, run_id: str):
    if not re.fullmatch(r"\d{1,30}", run_id):
        raise ValueError("Invalid workflow run ID")
    event = json.loads(event_path.read_text(encoding="utf-8"))
    raw = event.get("inputs", {}).get("payload")
    if not isinstance(raw, str) or len(raw) > 6000:
        raise ValueError("Invalid workflow payload")
    req = validate_request(json.loads(raw))
    existing = read(ROOT / "expanded_receipts" / (req["request_id"] + ".json"))
    if existing and existing.get("request_id") == req["request_id"]:
        # GitHub manual job re-runs must never consume the quota twice.
        job = {**req, "run_id": run_id, "status": "already_completed", "slots": [], "generated_at": stamp()}
        write(ROOT / "expanded_requests" / (run_id + ".json"), job)
        return job
    old_job = read(ROOT / "expanded_requests" / (run_id + ".json"))
    if old_job:
        if old_job.get("request_id") != req["request_id"]:
            raise ValueError("Run identifier conflict")
        if old_job.get("status") != "completed" and int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
            old_job["status"] = "api_rerun_blocked"
            write(ROOT / "expanded_requests" / (run_id + ".json"), old_job)
        return old_job
    job = {**req, "run_id": run_id, "status": "prepared", "slots": [], "generated_at": stamp(), "calls_reserved": 0}
    kinds = ["value", "rent"] if req["mode"] == "both" else [req["mode"]]
    for kind in kinds:
        prior, path = cached(req["property"], kind)
        fresh = prior and age_days(prior.get("cached_at")) is not None and 0 <= age_days(prior["cached_at"]) <= (30 if kind == "rent" else 60)
        job["slots"].append({"kind": kind, "cache": str(path), "mode": "cache" if fresh and not req["refresh"] else "api"})
    needed = sum(slot["mode"] == "api" for slot in job["slots"])
    try:
        ledger = load_usage()
        if req["request_id"] in ledger.get("reserved_requests", {}):
            job["status"] = "request_already_reserved"
        elif needed and int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
            job["status"] = "api_rerun_blocked"
        elif needed and not os.environ.get("RENTCAST_API_KEY", "").strip():
            job["status"] = "api_key_missing"
        elif needed and int(ledger["successful_api_calls"]) + needed > 45:
            job["status"] = "usage_guard_blocked"
        elif needed:
            # A durable reservation, including failed attempts, is counted before HTTP.
            ledger["successful_api_calls"] += needed
            ledger.setdefault("reserved_requests", {})[req["request_id"]] = {"count": needed, "run_id": run_id, "at": stamp()}
            ledger["updated_at"] = stamp()
            write(ROOT / "rentcast_usage.json", ledger)
            job["calls_reserved"] = needed
        job["usage"] = public_usage(ledger)
    except ValueError as exc:
        job["status"] = str(exc)
    write(ROOT / "expanded_requests" / (run_id + ".json"), job)
    return job


def execute(run_id: str):
    if not re.fullmatch(r"\d{1,30}", run_id):
        raise ValueError("Invalid workflow run ID")
    job_path = ROOT / "expanded_requests" / (run_id + ".json")
    job = read(job_path)
    if not job:
        raise ValueError("No committed request reservation")
    if job["status"] in {"already_completed", "completed"}:
        return job
    p = job["property"]
    result_path = ROOT / "expanded_reports" / (property_key(p["id"]) + ".json")
    previous = read(result_path) or {}
    same_subject = previous.get("subject_signature") == subject_signature(p)
    result = {"version": VERSION, "property_id": p["id"], "property": p, "subject_signature": subject_signature(p),
              "request_id": job["request_id"], "run_id": run_id, "generated_at": stamp(),
              "status": job["status"], "sources": dict(previous.get("sources", {})) if same_subject else {},
              "usage": job.get("usage"), "calls_reserved": job.get("calls_reserved", 0), "calls_sent": 0, "errors": {}}
    if job["status"] == "prepared":
        for slot in job["slots"]:
            kind = slot["kind"]
            path = Path(slot["cache"])
            candidate = read(path)
            if slot["mode"] == "api":
                if slot.get("attempted_at"):
                    result["errors"][kind] = "attempt_already_reserved_and_sent"
                    continue
                slot["attempted_at"] = stamp()
                # Also prevents re-running execute locally in the same checkout.
                write(job_path, job)
                try:
                    result["calls_sent"] += 1
                    response = requests.get("https://api.rentcast.io/v1" + ENDPOINTS[kind],
                        params=parameters(p, kind), headers={"Accept": "application/json", "X-Api-Key": os.environ["RENTCAST_API_KEY"]}, timeout=30, allow_redirects=False)
                    if response.status_code != 200:
                        result["errors"][kind] = "authentication_failed" if response.status_code in {401, 403} else "rate_limited" if response.status_code == 429 else "http_error_" + str(response.status_code)
                        if response.status_code in {401, 403, 429}:
                            break  # Never automatically retry or spend another call.
                        continue
                    payload = response.json()
                    if not isinstance(payload, dict):
                        result["errors"][kind] = "invalid_response"
                        continue
                    candidate = {"cached_at": stamp(), "endpoint": ENDPOINTS[kind], "address": parameters(p, kind)["address"],
                                 "params": parameters(p, kind), "response": payload}
                    # Never replace valid cache with an unrelated property's response.
                    if not match_subject(p, payload.get("subjectProperty")):
                        result["errors"][kind] = "subject_mismatch"
                        continue
                    write(path, candidate)
                except (requests.RequestException, ValueError, KeyError):
                    result["errors"][kind] = "request_failed_without_retry"
                    continue
            if candidate:
                result["sources"][kind] = normalized(p, kind, candidate.get("response"), candidate.get("cached_at"), path, slot["mode"])
        result["status"] = "partial" if result["errors"] else "completed"
    result["generated_at"] = stamp()
    write(result_path, result)
    write(ROOT / "expanded_receipts" / (job["request_id"] + ".json"), result)
    job["status"] = "completed"
    write(job_path, job)
    return result


def main():
    parser = argparse.ArgumentParser(description="Property-specific RentCast report")
    parser.add_argument("action", choices=("prepare", "execute"))
    parser.add_argument("--event-file", type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.event_file, args.run_id) if args.action == "prepare" else execute(args.run_id)
        print(json.dumps({"status": result.get("status"), "request_id": result.get("request_id"), "calls_reserved": result.get("calls_reserved", 0)}, ensure_ascii=False))
    except (OSError, ValueError, TypeError) as exc:
        # No request headers, credentials, URLs with secrets or response body in logs.
        print("Report preparation failed:", str(exc)[:180])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
