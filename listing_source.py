"""Source listing facts for basic reports. No RentCast, estimates, or browser secrets.

Reads public Redfin detail HTML once, binds it to the exact home/address/current
MLS number, and saves a dated snapshot. HTTP blocks never trigger a workaround.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

from bs4 import BeautifulSoup

VERSION = "basic-source-1.0-20261009"
DETAILS_DIR = Path("COMPS_REPORTS/listing_details")
MAX_BYTES = 5_000_000
FRESH_DAYS = 7
ALIASES = {"STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
           "BOULEVARD": "BLVD", "LANE": "LN", "COURT": "CT", "PLACE": "PL",
           "TERRACE": "TER", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
           "APARTMENT": "UNIT", "APT": "UNIT", "SUITE": "UNIT", "STE": "UNIT"}
FIELDS = {"occupancy", "roof_type", "roof_condition", "heating", "cooling",
          "hvac_type", "parking", "construction", "basement", "stories",
          "water", "sewer", "property_condition", "property_type"}


def now():
    return datetime.now(timezone.utc).isoformat()


def norm(value):
    text = re.sub(r"#\s*", " UNIT ", str(value or "").upper())
    text = re.sub(r"\b([A-Z]+)\.(?=\s|$)", r"\1", text)
    text = re.sub(r"[^A-Z0-9./\- ]", " ", text)
    return " ".join(ALIASES.get(word, word) for word in text.split())


def identity(p):
    return [norm(p.get("address")), norm(p.get("city")), "PA",
            str(p.get("zip") or "").strip()[:5]]


def source_url(p):
    value = str(p.get("url") or p.get("source_url") or "").strip()
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.hostname not in {"redfin.com", "www.redfin.com"}
            or parsed.username or parsed.password or port not in {None, 443}
            or not re.fullmatch(r"/PA/[^/]+/[^/]+/home/\d+/?", parsed.path)):
        return None
    return "https://www.redfin.com" + parsed.path.rstrip("/")


def listing_id(p):
    value = str(p.get("docket_id") or p.get("id") or "")
    match = re.fullmatch(r"(?:PA-)?MLS-(\d+)", value)
    return match.group(1) if match else None


def cache_key(p):
    # Keep units/fractions and the current listing number; relists are new records.
    if not source_url(p) or not listing_id(p):
        return None
    parts = [*identity(p), source_url(p), listing_id(p)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def matches(record, p):
    return (isinstance(record, dict) and record.get("identity") == identity(p)
            and record.get("source_url") == source_url(p)
            and record.get("listing_id") == listing_id(p))


def fresh(record, days=FRESH_DAYS):
    try:
        dt = datetime.fromisoformat(str(record.get("retrieved_at")).replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        return timedelta(0) <= age <= timedelta(days=days)
    except (TypeError, ValueError):
        return False


def photo_url(value):
    try:
        parsed = urlparse(str(value))
        return (parsed.scheme == "https" and parsed.hostname == "ssl.cdn-redfin.com"
                and not parsed.username and not parsed.password and parsed.port in {None, 443}
                and parsed.path.startswith("/photo/"))
    except ValueError:
        return False


def clean(value, limit=300):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if text and not re.fullmatch(r"(?:unknown|n/a|none|null|not provided|[-—–]+)", text, re.I) else None


def fact(value, p, checked, method="listing_label", excerpt=None):
    return {"value": clean(value), "source": "Redfin / MLS", "source_url": source_url(p),
            "property_id": str(p.get("id")), "listing_id": listing_id(p),
            "checked_at": checked, "status": "published", "method": method,
            "excerpt": clean(excerpt, 220) if excerpt else None}


def _description(soup):
    # Only the subject's About-this-home section, never nearby listing cards.
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        if clean(heading.get_text(" ", strip=True)) == "About this home":
            chunks = []
            for node in heading.next_elements:
                if getattr(node, "name", None) in {"h1", "h2"}:
                    break
                if getattr(node, "name", None) == "p":
                    chunks.append(node.get_text(" ", strip=True))
            if chunks:
                return clean(" ".join(chunks), 4000)
    node = soup.select_one(".remarks .content, .remarks-content, [data-rf-test-id='abp-remarks']")
    return clean(node.get_text(" ", strip=True), 4000) if node else None


def _detail_lines(soup):
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        if clean(heading.get_text(" ", strip=True)) != "Property details":
            continue
        lines = []
        for node in heading.next_elements:
            if getattr(node, "name", None) in {"h1", "h2"}:
                break
            if getattr(node, "name", None) in {"li", "dt", "dd"}:
                # Leaf entries avoid duplicating an entire nested section.
                if node.find(["li", "dt", "dd"]):
                    continue
                value = clean(node.get_text(" ", strip=True))
                if value:
                    lines.append(value)
        if lines:
            return lines
    # Redfin's published MLS detail table uses entryItemContent pairs.
    container = soup.select_one("#propertyDetails, #property-details, .propertyDetails")
    if container:
        return [clean(n.get_text(" ", strip=True)) for n in container.select(".entryItemContent")
                if clean(n.get_text(" ", strip=True))]
    return []


def _label_values(lines):
    result = {}
    labels = {"occupancy": "occupancy", "occupant type": "occupancy",
              "roof": "roof_type", "roof type": "roof_type", "roof material": "roof_type",
              "roof condition": "roof_condition", "heating": "heating",
              "heating type": "heating", "cooling": "cooling", "air conditioning": "cooling",
              "parking": "parking", "parking features": "parking", "parking type": "parking",
              "construction materials": "construction", "exterior": "construction",
              "basement": "basement", "stories": "stories", "levels": "stories",
              "water source": "water", "water": "water", "sewer": "sewer",
              "property condition": "property_condition"}
    for i, line in enumerate(lines):
        label, sep, val = line.partition(":")
        field = labels.get(label.strip().casefold())
        group_labels = {"construction", "utilities", "interior features", "home design", "flooring",
                        "bathrooms", "kitchen", "laundry & utility", "heating & cooling", "hoa & community"}
        if field:
            if not sep and i + 1 < len(lines) and lines[i + 1].casefold() not in set(labels) | group_labels:
                val = lines[i + 1]
            val = clean(val)
            if val:
                result[field] = val
        if line.casefold() in set(labels) | group_labels:
            continue
        # Public summary features use phrases rather than colon pairs.
        phrases = {
            "roof_type": r"^(.{1,80}) roof$",
            "heating": r"^(.{1,100} heating)$",
            "cooling": r"^(.{1,100} (?:air conditioning|cooling))$",
            "parking": r"^(.{0,100}(?:parking|garage)(?: for \d+ vehicles| spaces?)?)$",
            "construction": r"^(.{1,100}) construction$",
            "basement": r"^((?:Has |Walk-out |Full |Partial |Finished |Unfinished ).*basement)$",
            "stories": r"^((?:\d+|Two|Three|One)[ -]stor(?:y|ies))$",
            "water": r"^((?:Public|Private|Well|Municipal) water)$",
            "sewer": r"^((?:Public|Private|Septic|Municipal) sewer)$",
            "property_condition": r"^((?:Resale|New|Excellent|Good|Fair|Poor) condition)$",
        }
        for field, pattern in phrases.items():
            match = re.fullmatch(pattern, line, re.I)
            if match:
                result.setdefault(field, match.group(1))
    return result


def parse_listing(page_html, p):
    """Reject a different unit, address, canonical home, or current MLS number."""
    soup = BeautifulSoup(page_html, "html.parser")
    canonical = soup.find("link", rel="canonical")
    if canonical and source_url({"url": canonical.get("href")}) != source_url(p):
        raise ValueError("source_home_mismatch")
    headings = [clean(h.get_text(" ", strip=True), 500) for h in soup.find_all("h1")]
    title = clean(soup.title.get_text(" ", strip=True), 500) if soup.title else ""
    expected = identity(p)
    address_pattern = re.compile(r"^(.+?),\s*(.+?),\s*(PA)\s+(\d{5})(?:-\d{4})?(?:\s*[|·].*)?$", re.I)
    subjects = []
    for heading in headings or [title]:
        match = address_pattern.fullmatch(heading or "")
        if match:
            subjects.append([norm(match[1]), norm(match[2]), "PA", match[4]])
    if expected not in subjects or any(subject != expected for subject in subjects):
        raise ValueError("source_address_mismatch")
    current_ids = []
    # The first Source: label is the current listing; history can contain older IDs.
    visible = soup.get_text(" ", strip=True)
    current_match = re.search(r"(?:•\s*)?Source:\s*[^#]{1,100}#\s*(\d+)", visible)
    if current_match:
        current_ids.append(current_match[1])
    title_match = re.search(r"MLS\s*#\s*(\d+)", title or "", re.I)
    if title_match:
        current_ids.append(title_match[1])
    if not current_ids or any(value != listing_id(p) for value in current_ids):
        raise ValueError("source_listing_mismatch")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    checked = now()
    description = _description(soup)
    values = _label_values(_detail_lines(soup))
    # Occupancy is accepted only from an explicit present-tense statement.
    if description and "occupancy" not in values:
        rules = [(r"\b(?:currently\s+|(?:home|property|house) is\s+(?:currently\s+)?)tenant[- ]occupied\b", "מאוכלס בשוכר"),
                 (r"\bcurrently\s+(?:owner[- ]occupied)\b", "מאוכלס בבעלים"),
                 (r"\b(?:currently vacant|home is vacant|property is vacant)\b", "פנוי")]
        for pattern, value in rules:
            m = re.search(pattern, description, re.I)
            if m and not re.search(r"\b(?:not|no longer|previously|formerly)\s*$", description[max(0, m.start()-35):m.start()], re.I):
                values["occupancy"] = value
                break
    # Only photographs before the subject h1 whose alt names the subject. Never og images.
    photos = []
    subject_h1 = next((h for h in soup.find_all("h1") if expected[0] in norm(h.get_text(" ", strip=True))), None)
    if subject_h1:
        for img in subject_h1.find_all_previous("img"):
            if expected[0] not in norm(img.get("alt", "")):
                continue
            value = img.get("src") or img.get("data-src")
            if photo_url(value) and value not in [item["url"] for item in photos]:
                photos.append({"url": value, "source_url": source_url(p), "source": "Redfin / MLS",
                               "retrieved_at": checked, "capture_date": None})
            if len(photos) >= 3:
                break
        photos.reverse()
    return {"schema": 1, "version": VERSION, "property_id": str(p.get("id")),
            "identity": expected, "source_url": source_url(p), "listing_id": listing_id(p),
            "retrieved_at": checked, "source_updated_at": None,
            "status": "published" if values or description or photos else "no_published_fields",
            "facts": {k: fact(v, p, checked) for k, v in values.items()},
            "description": description, "description_kind": "original_listing_text",
            "photos": photos, "method": "public_listing_html"}


class SourceRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if source_url({"url": newurl}) != source_url({"url": req.full_url}):
            raise ValueError("redirected_source_mismatch")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_html(url):
    req = Request(url, headers={"User-Agent": "PA-Property-SourceReader/1.0",
                               "Accept": "text/html,application/xhtml+xml"})
    # No proxy, cookies, challenge bypass, retries, or alternate paid source.
    with build_opener(SourceRedirect()).open(req, timeout=15) as response:
        final = response.geturl()
        if source_url({"url": final}) != url:
            raise ValueError("redirected_source_mismatch")
        if "html" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("source_not_html")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("source_page_too_large")
        return data.decode("utf-8", errors="replace")


def read_record(p, root=Path(".")):
    key = cache_key(p)
    if not key:
        return None
    path = Path(root) / DETAILS_DIR / (key + ".json")
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if matches(record, p) else None


def write_record(p, record, root=Path(".")):
    path = Path(root) / DETAILS_DIR / (cache_key(p) + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def attach(p, record):
    if not matches(record, p):
        return False
    p["listing_source_details"] = {key: deepcopy(record.get(key)) for key in
                                   ("identity", "source_url", "listing_id", "retrieved_at",
                                    "source_updated_at", "status", "method", "last_attempt", "description_kind")}
    if not isinstance(p.get("technical_facts"), dict):
        p["technical_facts"] = {}
    facts = p["technical_facts"]
    for field, entry in record.get("facts", {}).items():
        if (field in FIELDS and isinstance(entry, dict) and clean(entry.get("value"))
                and entry.get("source_url") == record.get("source_url")
                and entry.get("listing_id") == record.get("listing_id")):
            # An inspection/contractor/user fact wins over a listing claim.
            previous = facts.get(field) or {}
            if not isinstance(previous, dict):
                previous = {}
            if previous.get("source") and (previous.get("method") not in {"listing_label", "listing_description_claim", "source_page_review"}
                    and not str(previous.get("source")).startswith("Redfin")):
                continue
            facts[field] = deepcopy(entry)
    if record.get("description"):
        p["source_listing_description"] = record["description"]
    if record.get("photos"):
        p["listing_photos"] = deepcopy(record["photos"])
    return True


def enrich(p, root=Path("."), refresh=False, allow_network=True):
    key = cache_key(p)
    if not key:
        return "unsupported_source"
    record = read_record(p, root)
    if record:
        attach(p, record)
    if not refresh and record and fresh(record) and record.get("status") == "published":
        return "cache_used"
    if not allow_network:
        return "cache_used" if record else "not_requested"
    # Blocked/failed requests have a separate timestamp; it cannot freshen old facts.
    if not refresh and record and fresh({"retrieved_at": (record.get("last_attempt") or {}).get("checked_at")}, days=1):
        return (record.get("last_attempt") or {}).get("status", "cached_failure")
    try:
        new_record = parse_listing(get_html(source_url(p)), p)
        new_record["last_attempt"] = {"status": new_record["status"], "checked_at": now()}
        write_record(p, new_record, root)
        attach(p, new_record)
        return new_record["status"]
    except HTTPError as exc:
        status = "source_blocked" if exc.code in {401, 403, 429} else "source_unavailable"
        info = {"status": status, "checked_at": now(), "http_status": exc.code}
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        info = {"status": "source_identity_mismatch" if "mismatch" in str(exc) else "source_unavailable",
                "checked_at": now(), "reason": str(exc)[:160]}
    if not record:
        record = {"schema": 1, "version": VERSION, "property_id": str(p.get("id")),
                  "identity": identity(p), "source_url": source_url(p), "listing_id": listing_id(p),
                  "retrieved_at": None, "status": info["status"], "facts": {}, "photos": []}
    record["last_attempt"] = info
    write_record(p, record, root)
    attach(p, record)
    return info["status"]


def enrich_rows(rows, root=Path("."), limit=20, allow_network=True):
    """At most 50 unique detail pages, after investment filters. Scope is explicit."""
    result = {"version": VERSION, "new_rentcast_calls": 0, "statuses": {}, "rows": 0}
    id_counts = {}
    for p in rows:
        ident = str(p.get("id"))
        id_counts.setdefault(ident, set()).add(tuple(identity(p)))
    budget = max(0, min(50, int(limit)))
    for p in rows:
        if len(id_counts[str(p.get("id"))]) != 1:
            continue
        if norm(str(p.get("county") or "").removesuffix(" County")) not in {"ALLEGHENY", "ERIE"}:
            continue
        if not cache_key(p):
            continue
        cached = read_record(p, root)
        use_net = allow_network and budget > 0
        if use_net and not (cached and fresh(cached) and cached.get("status") == "published"):
            budget -= 1
        status = enrich(p, root, allow_network=use_net)
        result["rows"] += 1
        result["statuses"][status] = result["statuses"].get(status, 0) + 1
        # Stop a batch on access blocks; do not hammer a blocked provider.
        if status == "source_blocked":
            allow_network = False
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--property-id", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    path = Path("properties.json")
    rows = json.loads(path.read_text(encoding="utf-8"))
    selected = [r for r in rows if str(r.get("id")) == args.property_id] if args.property_id else list(rows)
    if args.property_id and len(selected) != 1:
        raise ValueError("property_id_not_unique_or_missing")
    if args.property_id:
        if not cache_key(selected[0]):
            raise ValueError("unsupported_source")
        status = enrich(selected[0], refresh=args.refresh)
        result = {"version": VERSION, "new_rentcast_calls": 0, "rows": 1, "statuses": {status: 1}}
    else:
        selected.sort(key=lambda p: (read_record(p) or {}).get("retrieved_at") or "")
        result = enrich_rows(selected, limit=args.limit)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    summary = Path("COMPS_REPORTS/basic_source_status.json")
    summary.parent.mkdir(exist_ok=True)
    result["checked_at"] = now()
    result["property_id"] = args.property_id
    result["request_id"] = os.environ.get("SOURCE_REQUEST_ID", "")[:80]
    summary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if result["statuses"].get("source_blocked"):
        print("::warning::Redfin blocked a direct source read. Saved facts remain dated; no bypass or RentCast fallback.")


if __name__ == "__main__":
    main()
