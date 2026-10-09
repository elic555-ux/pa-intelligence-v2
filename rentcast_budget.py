"""Shared, conservative RentCast budget. No network requests or implicit resets."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = 2
LIMIT, STOP, WARNING = 50, 45, 36
HELD_STATES = {"reserved", "sent", "unknown"}
FINAL_STATES = {"success", "http_error", "empty_response"}


def clock():
    return datetime.now(timezone.utc)


def stamp():
    return clock().isoformat(timespec="seconds")


def read(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("usage_ledger_missing") from None
    if not isinstance(result, dict):
        raise ValueError("usage_ledger_invalid_count")
    return result


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10**9 or value != int(value):
        raise ValueError("usage_ledger_invalid_count")
    return int(value)


def period(day, current=None):
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
        raise ValueError("billing_cycle_not_configured")
    current = current or clock()
    year, month = current.year, current.month
    if current.day < day:
        month -= 1
        if not month:
            year, month = year - 1, 12
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(year, month, day).isoformat(), date(next_year, next_month, day).isoformat()


def totals(ledger):
    legacy = count(ledger.get("legacy_budget_used"))
    confirmed = count(ledger.get("confirmed_successful_calls"))
    journal = ledger.get("request_journal")
    if not isinstance(journal, dict):
        raise ValueError("usage_ledger_invalid_count")
    held, recorded_successes = 0, 0
    for request in journal.values():
        if not isinstance(request, dict) or not isinstance(request.get("slots"), dict):
            raise ValueError("usage_ledger_invalid_count")
        for slot in request["slots"].values():
            if not isinstance(slot, dict) or slot.get("state") not in HELD_STATES | FINAL_STATES:
                raise ValueError("usage_ledger_invalid_count")
            held += slot["state"] in HELD_STATES
            recorded_successes += slot["state"] == "success"
    if confirmed != recorded_successes:
        raise ValueError("usage_ledger_invalid_count")
    return {"legacy": legacy, "confirmed": confirmed, "held": held,
            "used": legacy + confirmed + held}


def adapt_legacy(raw):
    baseline = count(raw.get("successful_api_calls"))
    reservations = raw.get("reserved_requests", {})
    if not isinstance(reservations, dict):
        raise ValueError("usage_ledger_invalid_count")
    held = 0
    for item in reservations.values():
        if not isinstance(item, dict):
            raise ValueError("usage_ledger_invalid_count")
        held += count(item.get("count"))
    if baseline < held:
        raise ValueError("usage_ledger_invalid_count")
    # Those reservations were already charged against the legacy baseline.
    # Do not add them again, or claim that they were successful HTTP requests.
    return {"schema_version": SCHEMA, "provider": "RentCast",
            "billing_day": raw.get("billing_day"), "cycle_start": raw.get("cycle_start"),
            "next_reset": raw.get("next_reset"), "legacy_budget_used": baseline,
            "confirmed_successful_calls": 0, "request_journal": {},
            "legacy_request_history": copy.deepcopy(reservations),
            "billing_cycle_verified": False,
            "last_successful_call_at": raw.get("last_successful_call_at"),
            "migration_status": "preview_only",
            "count_basis": "legacy_baseline_plus_confirmed_calls_and_held_reservations"}


def load(root=Path("COMPS_REPORTS")):
    raw = read(Path(root) / "rentcast_usage.json")
    if raw.get("schema_version") == SCHEMA:
        ledger = copy.deepcopy(raw)
    elif raw.get("schema_version") is None:
        ledger = adapt_legacy(raw)
    else:
        raise ValueError("usage_ledger_invalid_count")
    start, reset = period(ledger.get("billing_day"))
    stored = ledger.get("cycle_start")
    try:
        if not isinstance(stored, str) or date.fromisoformat(stored).isoformat() != stored:
            raise ValueError()
    except ValueError:
        raise ValueError("usage_ledger_invalid_cycle") from None
    if stored != start:
        raise ValueError("usage_ledger_invalid_cycle")
    values = totals(ledger)
    if raw.get("schema_version") == SCHEMA and count(raw.get("successful_api_calls")) != values["used"]:
        raise ValueError("usage_ledger_invalid_count")
    ledger.update(plan_limit=LIMIT, auto_stop_at=STOP, warning_at=WARNING, next_reset=reset,
                  used=values["used"], successful_api_calls=values["used"])
    return ledger


def public(ledger):
    values = totals(ledger)
    used = values["used"]
    return {"used": used, "limit": LIMIT, "auto_stop_at": STOP,
            "remaining_safe": max(0, STOP-used), "reserve": LIMIT-STOP,
            "warning_at": WARNING, "legacy_budget_used": values["legacy"],
            "confirmed_successful_calls": values["confirmed"],
            "reserved_or_unknown_calls": values["held"],
            "cycle_start": ledger["cycle_start"], "next_reset": ledger["next_reset"],
            "billing_cycle_verified": ledger.get("billing_cycle_verified") is True,
            "count_basis": "legacy_baseline_plus_confirmed_calls_and_held_reservations",
            "level": "blocked" if used >= STOP else "warning" if used >= WARNING else "ok"}


def save(root, ledger):
    values = totals(ledger)
    ledger.update(used=values["used"], successful_api_calls=values["used"], updated_at=stamp())
    ledger["reserved_requests"] = {
        request_id: {"count": sum(s["state"] in HELD_STATES for s in item["slots"].values()),
                     "run_id": item["run_id"], "at": item["at"]}
        for request_id, item in ledger["request_journal"].items()
        if any(s["state"] in HELD_STATES for s in item["slots"].values())}
    write(Path(root) / "rentcast_usage.json", ledger)
    return ledger


def migrate(root=Path("COMPS_REPORTS")):
    root = Path(root)
    raw = read(root / "rentcast_usage.json")
    ledger = load(root)
    if raw.get("schema_version") == SCHEMA:
        return {"status": "already_migrated", "usage": public(ledger)}
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:16]
    archive = root / "rentcast_usage_history" / f"legacy-{ledger['cycle_start']}-{digest}.json"
    if not archive.exists():
        write(archive, raw)
    ledger.update(migration_status="completed", migrated_at=stamp(), legacy_archive=str(archive))
    save(root, ledger)
    return {"status": "migrated", "usage": public(ledger)}


def reserve(root, request_id, run_id, kinds):
    ledger = load(root)
    if ledger.get("migration_status") != "completed" or ledger.get("billing_cycle_verified") is not True:
        raise ValueError("usage_guard_blocked")
    if request_id in ledger["request_journal"] or request_id in ledger.get("legacy_request_history", {}):
        raise ValueError("request_already_reserved")
    if not kinds or len(set(kinds)) != len(kinds) or any(kind not in {"value", "rent"} for kind in kinds):
        raise ValueError("Invalid report slots")
    if totals(ledger)["used"] + len(kinds) > STOP:
        raise ValueError("usage_guard_blocked")
    ledger["request_journal"][request_id] = {"run_id": str(run_id), "at": stamp(),
        "slots": {kind: {"state": "reserved"} for kind in kinds}}
    return save(root, ledger)


def slot_for(ledger, request_id, kind, run_id):
    item = ledger["request_journal"].get(request_id)
    if not item or item.get("run_id") != str(run_id) or kind not in item["slots"]:
        raise ValueError("request_already_reserved")
    return item["slots"][kind]


def mark_sent(root, request_id, kind, run_id):
    ledger = load(root)
    slot = slot_for(ledger, request_id, kind, run_id)
    if slot["state"] != "reserved":
        raise ValueError("attempt_already_reserved_and_sent")
    slot.update(state="sent", sent_at=stamp())
    return save(root, ledger)


def record_response(root, request_id, kind, run_id, http_status=None, body_present=None):
    ledger = load(root)
    slot = slot_for(ledger, request_id, kind, run_id)
    if slot["state"] not in {"reserved", "sent"}:
        return ledger  # Idempotent; never charge or release a slot twice.
    if http_status is None:
        state = "unknown"
    elif http_status == 200 and body_present is True:
        state = "success"
        ledger["confirmed_successful_calls"] += 1
        ledger["last_successful_call_at"] = stamp()
    elif http_status == 200 and body_present is False:
        state = "empty_response"
    elif http_status == 200:
        state = "unknown"  # A 200 with unknown body availability stays held.
    else:
        state = "http_error"
    slot.update(state=state, http_status=http_status, completed_at=stamp())
    return save(root, ledger)
