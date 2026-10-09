"""Internal property observations and bounded, free county lookups.

Inventory prices are listing observations, never ARV or verified investment
inputs. County records retain source dates. Unit mismatches are not merged.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

SCHEMA = "internal-report-store-1"
COUNTIES = {"allegheny": "Allegheny", "erie": "Erie"}
FACTS = ("address", "city", "county", "state", "zip", "property_type", "type", "beds", "baths",
         "sqft", "year_built", "lot_size", "parcel_id", "municipality", "days_on_market", "lat", "lng")
MARKET = ("price", "source_amount_type", "price_history", "price_drop_history", "listed_date", "price_dropped")
PROVENANCE = ("source", "source_type", "source_url", "url", "last_source_check", "last_seen", "first_seen")


def clock():
    return datetime.now(timezone.utc)


def stamp():
    return clock().isoformat(timespec="seconds")


def norm(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"(?<=[a-z])\.(?=\s|$)", "", value)
    value = re.sub(r"[^a-z0-9# /.-]", " ", value)
    aliases = {"street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "place": "pl",
               "boulevard": "blvd", "lane": "ln", "court": "ct", "north": "n", "south": "s",
               "east": "e", "west": "w", "apartment": "unit", "apt": "unit", "suite": "unit",
               "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th"}
    return " ".join(aliases.get(w, w) for w in value.replace("#", " unit ").split())


def county_name(value):
    return COUNTIES.get(str(value or "").casefold().replace(" county", "").strip())


def key(property_id):
    return hashlib.sha256(str(property_id).encode()).hexdigest()[:32]


def subject_key(p):
    subject = {"address": norm(p.get("address")), "city": norm(p.get("city")),
               "state": str(p.get("state") or "PA").upper(), "zip": str(p.get("zip") or "")[:5]}
    return hashlib.sha256("\n".join(subject[field] for field in ("address", "city", "state", "zip")).encode()).hexdigest()[:32]


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write(path, value):
    path = Path(path)
    content = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)
    return True


def age_days(value):
    try:
        point = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        return (clock()-point).total_seconds()/86400
    except (ValueError, TypeError):
        return None


def same_address(p, q):
    return (norm(p.get("address")) == norm(q.get("address")) and norm(p.get("city")) == norm(q.get("city"))
        and str(p.get("state") or "PA").upper() == str(q.get("state") or "PA").upper()
        and str(p.get("zip") or "")[:5] == str(q.get("zip") or "")[:5])


def build_inventory(repo=Path(".")):
    repo = Path(repo)
    rows = read(repo / "properties.json")
    if not isinstance(rows, list):
        raise ValueError("Inventory must be a JSON array")
    grouped, counts = {}, {name: 0 for name in COUNTIES.values()}
    for row in rows:
        if not isinstance(row, dict):
            continue
        county = county_name(row.get("county"))
        if not county or not row.get("id"):
            continue
        counts[county] += 1
        property_id = str(row["id"])
        observation = {"facts": {field: row[field] for field in FACTS if field in row},
                       "reported_market_data": {field: row[field] for field in MARKET if field in row},
                       "provenance": {field: row[field] for field in PROVENANCE if field in row}}
        observation["facts"].setdefault("state", "PA")
        observation["observed_at"] = row.get("last_source_check") or row.get("last_seen")
        grouped.setdefault(property_id, []).append(observation)
    buckets = {prefix: {} for prefix in "0123456789abcdef"}
    ambiguous = 0
    for property_id, observations in grouped.items():
        ambiguous += len(observations) > 1
        digest = key(property_id)
        buckets[digest[0]][digest] = {"property_id": property_id,
            "identity_status": "ambiguous_multiple_observations" if len(observations) > 1 else "single_inventory_observation",
            "observations": observations}
    root = repo / "COMPS_REPORTS/internal_data"
    for prefix, values in buckets.items():
        path = root / "inventory" / (prefix + ".json")
        previous = read(path)
        if previous is not None and previous != values:
            digest = hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest()[:16]
            write(root / "inventory_history" / (prefix + "-" + digest + ".json"), previous)
        write(path, values)
    summary = {"schema_version": SCHEMA, "observations": sum(counts.values()),
               "properties": len(grouped), "ambiguous_ids": ambiguous, "counties": counts,
               "source_inventory_sha256": hashlib.sha256((repo / "properties.json").read_bytes()).hexdigest(),
               "new_rentcast_calls": 0, "source_dates_preserved": True}
    write(root / "manifest.json", summary)
    return summary


def inventory(p, root=Path("COMPS_REPORTS")):
    digest = key(p.get("id"))
    shard = read(Path(root) / "internal_data/inventory" / (digest[0] + ".json"))
    record = shard.get(digest) if isinstance(shard, dict) else None
    if not isinstance(record, dict) or record.get("property_id") != str(p.get("id")):
        return {"status": "not_indexed", "observations": []}
    if record.get("identity_status") == "ambiguous_multiple_observations":
        return {"status": "ambiguous_identity", "observations": []}
    observations = [item for item in record.get("observations", []) if same_address(p, item.get("facts", {}))]
    return {"status": "matched" if observations else "subject_mismatch", "observations": observations}


def public_get(url, limit=3*1024*1024):
    allowed = {"data.wprdc.org", "public.eriecountypa.gov", "gis.eriecountypa.gov"}
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in allowed:
        raise ValueError("Unexpected county host")
    with urlopen(Request(url, headers={"User-Agent": "PA-Report-County-Lookup/8.15", "Accept": "*/*"}), timeout=15) as response:
        if urlsplit(response.url).hostname not in allowed:
            raise ValueError("Unexpected county redirect")
        content = response.read(limit+1)
        if len(content) > limit:
            raise ValueError("County response too large")
        return content


def num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(str(value).replace(",", "").strip())
        return result if 0 <= result <= 10**8 else None
    except (ValueError, TypeError):
        return None


def unit_address(address):
    return bool(re.search(r"(?:#|\b(?:unit|apt|apartment|suite)\b)", str(address), re.I))


def county_record(p, fields, source, source_url, parcel_id, as_of=None, photos=None):
    return {"status": "matched", "subject": {field: p.get(field) for field in ("address", "city", "state", "zip")},
            "county": county_name(p.get("county")), "parcel_id": parcel_id,
            "fields": {field: value for field, value in fields.items() if value is not None},
            "source_name": source, "source_url": source_url, "source_as_of": as_of,
            "retrieved_at": stamp(), "photos": photos or [],
            "basis": "county_building_record", "after_repair_value_evidence": False}


def allegheny(p):
    if unit_address(p["address"]):
        return {"status": "unit_lookup_requires_exact_parcel"}
    house = re.match(r"^(\d+[A-Za-z]?)\s", p["address"])
    if not house:
        return {"status": "subject_mismatch"}
    params = {"resource_id": "65855e14-549e-4992-b5be-d629afc676fa", "limit": 100,
        "filters": json.dumps({"PROPERTYHOUSENUM": house[1], "PROPERTYZIP": p["zip"][:5]})}
    result = json.loads(public_get("https://data.wprdc.org/api/3/action/datastore_search?" + urlencode(params)))
    data = result.get("result", {})
    if not result.get("success") or num(data.get("total")) is not None and num(data["total"]) > 100:
        return {"status": "county_result_limit"}
    matches = []
    for row in data.get("records", []):
        addr = " ".join(str(row.get(field) or "").strip() for field in ("PROPERTYHOUSENUM", "PROPERTYFRACTION", "PROPERTYADDRESS")).strip()
        if str(row.get("PROPERTYUNIT") or "").strip():
            continue
        q = {"address": addr, "city": row.get("PROPERTYCITY"), "state": row.get("PROPERTYSTATE"), "zip": row.get("PROPERTYZIP")}
        if same_address(p, q):
            matches.append(row)
    if len(matches) != 1:
        return {"status": "ambiguous_identity" if matches else "county_record_not_found"}
    row = matches[0]
    full, half = num(row.get("FULLBATHS")), num(row.get("HALFBATHS"))
    fields = {"beds": num(row.get("BEDROOMS")), "baths": full+half/2 if full is not None and half is not None else None,
              "sqft": num(row.get("FINISHEDLIVINGAREA")), "year_built": num(row.get("YEARBLT")),
              "lot_size": num(row.get("LOTAREA")), "municipality": row.get("MUNIDESC"),
              "style": row.get("STYLEDESC"), "county_use": row.get("USEDESC")}
    return county_record(p, fields, "Allegheny County / WPRDC",
        "https://data.wprdc.org/dataset/property-assessments/resource/65855e14-549e-4992-b5be-d629afc676fa",
        row.get("PARID"), as_of=row.get("ASOFDATE"))


class ErieTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells, self.rows, self.images, self.text, self.in_cell = [], [], [], [], False

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "th"}:
            self.in_cell, self.text = True, []
        if tag == "tr":
            self.cells = []
        if tag == "img":
            source = dict(attrs).get("src", "")
            if source.startswith("/parcelphotos/"):
                self.images.append(source)

    def handle_data(self, data):
        if self.in_cell:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.in_cell:
            self.cells.append(" ".join(" ".join(self.text).split()))
            self.in_cell = False
        if tag == "tr" and len(self.cells) == 2:
            self.rows.append(tuple(self.cells))


def erie(p, root):
    if unit_address(p["address"]):
        return {"status": "unit_lookup_requires_exact_parcel"}
    words = norm(p["address"]).upper().split()
    if not words or not re.fullmatch(r"\d+[A-Z]?", words[0]):
        return {"status": "subject_mismatch"}
    # Fixed field names, bounded results, escaped literal values, and exact
    # address verification after the GIS query. No user-supplied SQL operators.
    pattern = "%".join(word.replace("'", "''") for word in words)
    params = {"f": "json", "where": "UPPER(fullstreet) LIKE '"+pattern+"'",
              "outFields": "taxpin,municipali,fullstreet", "returnGeometry": "false", "resultRecordCount": 30}
    result = json.loads(public_get("https://gis.eriecountypa.gov/server/rest/services/Hosted/ErieCountyParcels_Dec2025/FeatureServer/16/query?"+urlencode(params)))
    if result.get("error") or result.get("exceededTransferLimit"):
        return {"status": "county_result_limit"}
    matches = []
    for feature in result.get("features", []):
        row = feature.get("attributes", {})
        municipality = re.sub(r"^(City of |Borough of )", "", str(row.get("municipali") or ""), flags=re.I)
        if norm(row.get("fullstreet")) == norm(p["address"]) and norm(municipality) == norm(p["city"]):
            matches.append(row)
    if len(matches) != 1:
        return {"status": "ambiguous_identity" if matches else "county_record_not_found"}
    parcel = str(matches[0].get("taxpin") or "")
    if not re.fullmatch(r"\d{10,20}", parcel):
        return {"status": "county_record_not_found"}
    url = "https://public.eriecountypa.gov/property-tax-records/property-records/property-tax-search/parcel-profile/print-view.aspx?parcelid="+parcel
    html = public_get(url).decode("utf-8", errors="replace")
    if "Parcel: "+parcel not in html:
        return {"status": "subject_mismatch"}
    parsed = ErieTable()
    parsed.feed(html)
    rows = dict(parsed.rows)
    if norm(str(rows.get("Address") or "").replace("|", " ")) != norm(p["address"]):
        return {"status": "subject_mismatch"}
    if len(re.findall(r"<strong>\s*Card \d+\s*</strong>", html, re.I)) > 1:
        return {"status": "multiple_building_cards"}
    full, half = num(rows.get("Full Baths")), num(rows.get("Half Baths"))
    fields = {"beds": num(rows.get("Total Bedrooms")), "baths": full+half/2 if full is not None and half is not None else None,
        "sqft": num(rows.get("Total Living Area")), "year_built": num(rows.get("Year Built")),
        "municipality": matches[0].get("municipali"), "style": rows.get("Style"), "county_use": rows.get("Land Use Code")}
    acres = num(rows.get("Acreage"))
    if acres is not None:
        fields["lot_size"] = round(acres*43560)
    photos = []
    # Only one exterior photo per request; no bulk image harvesting.
    for reference in parsed.images[:1]:
        if not re.fullmatch(r"/parcelphotos/"+re.escape(parcel)+r"-\d+\.jpg", reference, re.I):
            continue
        photo_url = urljoin(url, reference)
        try:
            content = public_get(photo_url, limit=750000)
            if not content.startswith(b"\xff\xd8\xff"):
                continue
            digest = hashlib.sha256(content).hexdigest()[:16]
            target = Path(root) / "internal_data/photos" / (parcel+"-"+digest+".jpg")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            relative = str(target.relative_to(Path(root).parent)).replace("\\", "/")
            historical = parcel == "15020035010100" and digest == "d5e971a450799205"
            photos.append({"cache_path": relative, "source_url": photo_url,
                "source_name": "Erie County", "kind": "county_exterior_photo",
                "captured_at": None, "capture_year": 2008 if historical else None,
                "historical": historical, "date_status": "historical" if historical else "unknown",
                "retrieved_at": stamp(), "suitable_for_current_rehab_assessment": False})
        except (OSError, ValueError):
            continue
    return county_record(p, fields, "Erie County Assessment", url, parcel, photos=photos)


def profile(p, root=Path("COMPS_REPORTS"), allow_public=True, refresh=False):
    root = Path(root)
    local = inventory(p, root)
    result = {"version": SCHEMA, "inventory": local, "county_record": None,
              "subject": {field: p.get(field) for field in ("address", "city", "state", "zip")}}
    if local["status"] == "ambiguous_identity":
        return result
    enriched = dict(p)
    if not county_name(enriched.get("county")) and local["observations"]:
        enriched["county"] = local["observations"][0]["facts"].get("county")
    county = county_name(enriched.get("county"))
    path = root / "internal_data/profiles" / (subject_key(p)+".json")
    saved = read(path)
    age = age_days(saved.get("retrieved_at")) if isinstance(saved, dict) else None
    if saved and same_address(p, saved.get("subject", {})) and age is not None and 0 <= age <= 90 and not refresh:
        result["county_record"] = saved
        result["county_record"]["source_mode"] = "internal_cache"
        return result
    if not allow_public or not county:
        result["county_record"] = saved if isinstance(saved, dict) and same_address(p, saved.get("subject", {})) else None
        if result["county_record"]:
            result["county_record"]["source_mode"] = "stale_internal_cache"
        return result
    try:
        record = allegheny(enriched) if county == "Allegheny" else erie(enriched, root)
        record.update(retrieved_at=stamp(), source_mode="free_county_lookup")
        if record.get("status") == "matched":
            write(path, record)
        result["county_record"] = record
    except (OSError, ValueError, TypeError, KeyError):
        # A free-source outage must not invalidate a saved profile or spend API quota.
        if isinstance(saved, dict) and same_address(p, saved.get("subject", {})):
            result["county_record"] = {**saved, "source_mode": "stale_internal_cache"}
        result["public_source_error"] = "county_source_unavailable"
    return result
