import argparse
import json
import csv
import io
import os
from datetime import datetime, date
from html import unescape
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

VERSION = "1.8"

CKAN_SEARCH = "https://data.wprdc.org/api/3/action/datastore_search"
ASSESSMENT_RESOURCE_ID = "65855e14-549e-4992-b5be-d629afc676fa"
SALES_RESOURCE_ID = "5bbe6c55-bce6-4edb-9d04-68edeb6bf7b1"

DEFAULT_ADDRESS = "4601 Fifth Ave #621"
DEFAULT_CITY = "Pittsburgh"
DEFAULT_STATE = "PA"
DEFAULT_ZIP = "15213"

OUTPUT_DIR = Path("COMPS_REPORTS")
TIMEOUT = 25
HEADERS = {"User-Agent": "PA-RealEstate-Intelligence-Hub-Comps/1.8"}

SUFFIXES = {
    "AVENUE": "AVE", "AV": "AVE", "AVE": "AVE",
    "STREET": "ST", "ST": "ST",
    "ROAD": "RD", "RD": "RD",
    "DRIVE": "DR", "DR": "DR",
    "LANE": "LN", "LN": "LN",
    "BOULEVARD": "BLVD", "BLVD": "BLVD",
    "COURT": "CT", "CT": "CT",
    "PLACE": "PL", "PL": "PL",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "PARKWAY": "PKWY", "PKWY": "PKWY",
    "TERRACE": "TER", "TER": "TER",
}

ORDINAL_WORDS = {
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH",
    "FIFTH": "5TH", "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH",
    "NINTH": "9TH", "TENTH": "10TH", "ELEVENTH": "11TH", "TWELFTH": "12TH",
    "THIRTEENTH": "13TH", "FOURTEENTH": "14TH", "FIFTEENTH": "15TH",
    "SIXTEENTH": "16TH", "SEVENTEENTH": "17TH", "EIGHTEENTH": "18TH",
    "NINETEENTH": "19TH", "TWENTIETH": "20TH",
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def norm(value):
    value = clean(value).upper().replace(".", "")
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def street_tokens(value):
    tokens = norm(value).split()

    # Normalize spelled-out ordinal street names used by listing sites
    # to the numeric form commonly used by Allegheny County records:
    # FIFTH -> 5TH, FIRST -> 1ST, etc.
    tokens = [ORDINAL_WORDS.get(token, token) for token in tokens]

    if tokens and tokens[-1] in SUFFIXES:
        tokens[-1] = SUFFIXES[tokens[-1]]
    return tokens


def street_core(value):
    tokens = street_tokens(value)
    if tokens and tokens[-1] in set(SUFFIXES.values()):
        tokens = tokens[:-1]
    return " ".join(tokens)


def parse_address(address):
    raw = clean(address)
    unit = ""

    m = re.search(
        r"(?:#|UNIT\s+|APT\s+|APARTMENT\s+|SUITE\s+)([A-Za-z0-9\-]+)\s*$",
        raw, re.I
    )
    if m:
        unit = clean(m.group(1))
        raw = raw[:m.start()].strip(" ,")

    m = re.match(r"^\s*(\d+[A-Za-z]?)\s+(.+?)\s*$", raw)
    if not m:
        raise ValueError("כתובת לא תקינה. לדוגמה: 4601 Fifth Ave #621")

    return {"house_number": m.group(1), "street": m.group(2), "unit": unit}


def ckan_search(resource_id, filters=None, q=None, limit=500):
    params = {"resource_id": resource_id, "limit": limit}
    if filters:
        params["filters"] = json.dumps(filters, separators=(",", ":"))
    if q:
        params["q"] = q

    r = requests.get(CKAN_SEARCH, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError("WPRDC API returned success=false")
    return (payload.get("result") or {}).get("records") or []


def unique_records(records):
    seen = set()
    result = []
    for rec in records:
        key = (
            clean(rec.get("PARID")),
            clean(rec.get("PROPERTYHOUSENUM")),
            clean(rec.get("PROPERTYADDRESS")),
            clean(rec.get("PROPERTYUNIT")),
        )
        if key not in seen:
            seen.add(key)
            result.append(rec)
    return result


def get_candidates(target):
    """
    חיפוש מדורג.
    לא מניח ש-Unit הוא Parcel נפרד (חשוב במיוחד ב-Co-op).
    """
    house = target["house_number"]
    city = target["city"]
    zipcode = target["zip"]
    street = target["street"]

    attempts = []

    def add(label, filters=None, q=None):
        try:
            rows = ckan_search(
                ASSESSMENT_RESOURCE_ID,
                filters=filters,
                q=q,
                limit=500
            )
            attempts.append({"label": label, "count": len(rows), "error": None})
            return rows
        except Exception as exc:
            attempts.append({"label": label, "count": 0, "error": str(exc)})
            return []

    records = []

    # 1. House + City + ZIP
    filters = {"PROPERTYHOUSENUM": house, "PROPERTYCITY": city}
    if zipcode:
        filters["PROPERTYZIP"] = zipcode
    records += add("house_city_zip", filters=filters)

    # 2. House + City
    records += add(
        "house_city",
        filters={"PROPERTYHOUSENUM": house, "PROPERTYCITY": city}
    )

    # 3. House only - then local street filtering
    records += add("house_only", filters={"PROPERTYHOUSENUM": house})

    # 4. Free-text fallback for street core / address variants
    core = street_core(street)
    if core:
        records += add("street_core_text", q=f"{house} {core}")

    records = unique_records(records)

    # Keep candidates whose street is plausibly the requested street.
    target_core = street_core(street)
    plausible = []
    for rec in records:
        rec_core = street_core(rec.get("PROPERTYADDRESS"))
        if target_core and rec_core:
            if target_core == rec_core or target_core in rec_core or rec_core in target_core:
                plausible.append(rec)

    # If the API search returned no street match, retain all house-number
    # candidates for diagnostics, but they will not resolve automatically.
    return (plausible if plausible else records), attempts


def score_candidate(rec, target):
    score = 0
    reasons = []

    if norm(rec.get("PROPERTYHOUSENUM")) == norm(target["house_number"]):
        score += 30
        reasons.append("house_exact")

    rc = street_core(rec.get("PROPERTYADDRESS"))
    tc = street_core(target["street"])
    if rc and tc and rc == tc:
        score += 50
        reasons.append("street_core_exact")
    elif rc and tc and (rc in tc or tc in rc):
        score += 30
        reasons.append("street_core_partial")

    if norm(rec.get("PROPERTYCITY")) == norm(target["city"]):
        score += 10
        reasons.append("city_exact")

    rz = norm(rec.get("PROPERTYZIP"))
    tz = norm(target["zip"])
    if rz and tz and rz == tz:
        score += 10
        reasons.append("zip_exact")

    ru = norm(rec.get("PROPERTYUNIT"))
    tu = norm(target["unit"])
    if tu:
        if ru == tu:
            score += 60
            reasons.append("unit_exact")
        elif ru:
            score -= 25
            reasons.append("different_unit")
        else:
            # Blank unit is NOT fatal: co-op/master-parcel possibility.
            reasons.append("county_unit_blank")

    return score, reasons


def classify_structure(scored, target):
    """
    Safe resolution rules:
    - exact_unit_parcel: exact requested unit is independently assessed.
    - likely_master_or_coop_parcel: exactly one strong same-address blank-unit parcel.
    - building_parcel_candidates: multiple strong parcels at same building address.
      In this case we deliberately do NOT choose one automatically.
    - unresolved: no safe match.
    """
    if not scored:
        return "unresolved", None

    exact_unit = [
        x for x in scored
        if "unit_exact" in x["reasons"] and x["score"] >= 100
    ]
    if exact_unit:
        return "exact_unit_parcel", exact_unit[0]

    same_address = [
        x for x in scored
        if "house_exact" in x["reasons"]
        and "street_core_exact" in x["reasons"]
        and x["score"] >= 90
    ]

    blank_units = [
        x for x in same_address
        if not norm(x["record"].get("PROPERTYUNIT"))
    ]
    nonblank_units = [
        x for x in same_address
        if norm(x["record"].get("PROPERTYUNIT"))
    ]

    if len(blank_units) == 1 and not nonblank_units:
        return "likely_master_or_coop_parcel", blank_units[0]

    # Critical safety rule: if County has several strong parcels for the
    # same building address and none matches Unit 621, expose all candidates.
    # Never guess which parcel represents the requested co-op unit.
    if len(same_address) > 1:
        return "building_parcel_candidates", None

    if nonblank_units:
        return "multiple_unit_parcels_no_exact_unit", None

    return "unresolved", None



def same_address_candidates(scored):
    """Return strong County records for the exact building address."""
    return [
        x for x in scored
        if "house_exact" in x["reasons"]
        and "street_core_exact" in x["reasons"]
        and x["score"] >= 90
    ]


def infer_building_reference(scored, target):
    """
    For unit addresses where County exposes multiple blank-unit parcels,
    identify a *building reference* without pretending the requested unit
    is independently parcelized.

    Residential multi-unit uses are preferred over auxiliary/commercial
    parcels. This is a building-level reference only and is never treated
    as proof of Unit ownership/parcel identity.
    """
    if not norm(target.get("unit")):
        return None

    candidates = same_address_candidates(scored)
    if len(candidates) < 2:
        return None

    ranked = []
    for item in candidates:
        rec = item["record"]
        use = norm(rec.get("USEDESC"))
        bonus = 0
        reasons = []

        residential_markers = (
            "APART", "APARTMENT", "MULTI", "CONDO", "COOP",
            "CO OP", "RESIDENTIAL", "40 UNITS", "40+ UNITS"
        )
        auxiliary_markers = ("AUX", "COMM AUX", "PARKING", "GARAGE")

        if any(marker in use for marker in residential_markers):
            bonus += 40
            reasons.append("residential_multiunit_use")
        if any(marker in use for marker in auxiliary_markers):
            bonus -= 30
            reasons.append("auxiliary_or_nonunit_use")

        ranked.append({
            **item,
            "building_reference_score": item["score"] + bonus,
            "building_reference_reasons": reasons,
        })

    ranked.sort(key=lambda x: x["building_reference_score"], reverse=True)

    if not ranked:
        return None

    best = ranked[0]
    second_score = ranked[1]["building_reference_score"] if len(ranked) > 1 else -999

    # Require affirmative residential evidence and a clear lead.
    if (
        "residential_multiunit_use" in best["building_reference_reasons"]
        and best["building_reference_score"] >= 120
        and best["building_reference_score"] - second_score >= 20
    ):
        return best

    return None


def _num(value):
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text or text.lower() in {"nan", "n/a", "na", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _csv_value(row, *names):
    normalized = {norm(k).replace(" ", "_"): v for k, v in row.items()}
    for name in names:
        key = norm(name).replace(" ", "_")
        if key in normalized and clean(normalized[key]):
            return normalized[key]
    return None


def _split_building_and_unit(address_value):
    raw = clean(address_value).upper()
    unit = ""
    m = re.search(r"(?:#|\bUNIT\s+|\bAPT\s+)([A-Z0-9-]+)\s*$", raw)
    if m:
        unit = m.group(1)
        raw = raw[:m.start()].strip(" ,")
    return raw, unit


def same_building(address_value, target):
    building, _ = _split_building_and_unit(address_value)
    text = norm(building)
    m = re.match(r"^\s*(\d+[A-Z]?)\s+(.+?)\s*$", text)
    if not m:
        return False
    return (
        norm(m.group(1)) == norm(target["house_number"])
        and street_core(m.group(2)) == street_core(target["street"])
    )


def extract_unit(address_value):
    _, unit = _split_building_and_unit(address_value)
    return unit


REDFIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)


def redfin_city_sold_rows(max_pages=2, page_size=350):
    """
    Best-effort Redfin downloadable sold-search CSV adapter.
    This is intentionally non-fatal because it is not a stable public API.
    """
    base_url = "https://www.redfin.com/stingray/api/gis-csv"
    all_rows, errors = [], []

    for page in range(1, max_pages + 1):
        params = {
            "al": 1, "market": "pittsburgh", "num_homes": page_size,
            "ord": "redfin-recommended-asc", "page_number": page,
            "start": (page - 1) * page_size,
            "region_id": 15213, "region_type": 2,
            "sf": "1,2,3,5,6,7", "status": 9,
            "uipt": "1,2,3,4,5,6,7,8", "v": 8,
            "sold_within_days": 730,
        }
        try:
            r = requests.get(
                base_url, params=params,
                headers={
                    "User-Agent": REDFIN_USER_AGENT,
                    "Accept": "text/csv,text/plain,*/*",
                    "Referer": "https://www.redfin.com/city/15702/PA/Pittsburgh/recently-sold",
                },
                timeout=30,
            )
            r.raise_for_status()
            text = r.text.lstrip("\ufeff").strip()
            if not text or "<html" in text[:200].lower():
                errors.append(f"page_{page}: non_csv_response")
                break
            rows = list(csv.DictReader(io.StringIO(text)))
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
        except Exception as exc:
            errors.append(f"page_{page}: {type(exc).__name__}: {exc}")
            break

    return all_rows, errors



def _parse_sale_date(value):
    text = clean(value)
    if not text:
        return None
    formats = (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
        "%b %d, %Y", "%B %d, %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _plausible_sqft(value):
    n = _num(value)
    # Prevent malformed CSV values such as 135 from being treated as
    # living area for a 2-bedroom apartment.
    if n is None:
        return None
    return n if 300 <= n <= 10000 else None


def discover_same_building_sold_comps(target, subject):
    rows, errors, headers = redfin_city_sold_rows()
    comps = []

    for row in rows:
        address = _csv_value(row, "ADDRESS", "PROPERTY ADDRESS")
        if not same_building(address, target):
            continue

        unit = extract_unit(address)
        sold_price = _num(_csv_value(row, "PRICE", "SALE PRICE", "SOLD PRICE"))
        raw_status = clean(_csv_value(row, "STATUS", "PROPERTY STATUS"))
        raw_sale_type = clean(_csv_value(row, "SALE TYPE", "LISTING TYPE"))
        if not unit or norm(unit) == norm(target.get("unit")) or sold_price is None:
            continue

        beds = _num(_csv_value(row, "BEDS", "BEDROOMS"))
        baths = _num(_csv_value(row, "BATHS", "BATHROOMS"))
        sqft = _plausible_sqft(_csv_value(row, "SQUARE FEET", "SQFT", "LIVING AREA"))
        sold_date_raw = _csv_value(
            row, "SOLD DATE", "SALE DATE", "LAST SALE DATE",
            "DATE SOLD", "CLOSE DATE", "CLOSED DATE"
        )
        sold_date = _parse_sale_date(sold_date_raw)
        source_url = clean(_csv_value(
            row,
            "URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)",
            "URL",
        ))

        score = 100.0
        reasons = ["same_building", "different_unit", "sold_search_feed"]

        if subject.get("beds") is not None and beds is not None:
            if beds == subject["beds"]:
                score += 20
                reasons.append("same_beds")
            else:
                score -= 15 * abs(beds - subject["beds"])

        if subject.get("baths") is not None and baths is not None:
            if baths == subject["baths"]:
                score += 15
                reasons.append("same_baths")
            else:
                score -= 10 * abs(baths - subject["baths"])

        if subject.get("sqft") and sqft:
            delta = abs(sqft - subject["sqft"]) / subject["sqft"]
            if delta <= .10:
                score += 20
                reasons.append("sqft_within_10pct")
            elif delta <= .25:
                score += 10
                reasons.append("sqft_within_25pct")
            else:
                score -= 20

        comps.append({
            "unit": unit,
            "address": clean(address),
            "sold_price": sold_price,
            "sold_date": sold_date or None,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "price_per_sqft": round(sold_price / sqft, 2) if sqft else None,
            "comp_score": round(score, 1),
            "match_reasons": reasons,
            "source": "Redfin downloadable sold-search CSV",
            "source_url": source_url or None,
            "raw_status": raw_status or None,
            "raw_sale_type": raw_sale_type or None,
            "verification": (
                "unit_level_closed_sale_verified"
                if sold_date else "unit_level_sale_price_date_unverified"
            ),
        })

    unique = {}
    for c in comps:
        unique[(norm(c["unit"]), c["sold_date"], c["sold_price"])] = c

    comps = sorted(
        unique.values(),
        key=lambda c: (c["comp_score"], c["sold_date"] or ""),
        reverse=True,
    )
    return comps, errors, len(rows)



def _page_text(html):
    text = re.sub(r"(?is)<script.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def verify_redfin_closed_sale(comp):
    """
    Secondary verification against the individual Redfin property page.
    A CSV price is NOT accepted as a sold price unless the property page
    contains a SOLD event with the same price and an identifiable date.
    """
    url = clean(comp.get("source_url"))
    price = comp.get("sold_price")
    if not url or price is None:
        return comp, "missing_property_url_or_price"

    try:
        r = requests.get(
            url,
            headers={"User-Agent": REDFIN_USER_AGENT, "Accept": "text/html,*/*"},
            timeout=25,
        )
        r.raise_for_status()
        text = _page_text(r.text)
    except Exception as exc:
        return comp, f"property_page_error: {type(exc).__name__}: {exc}"

    # Require the page itself to identify the property as sold.
    if not re.search(r"\bSOLD\b", text, flags=re.I):
        return comp, "no_sold_event_on_property_page"

    price_int = int(round(float(price)))
    price_patterns = {
        f"${price_int:,}",
        f"${price_int}",
    }
    if not any(p in text for p in price_patterns):
        return comp, "csv_price_not_confirmed_on_property_page"

    # Prefer explicit sale-history phrasing, then SOLD header phrasing.
    date_patterns = [
        r"(?:Sold|SOLD)\s+(?:on\s+)?([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
        r"(?:Sold|SOLD)\s+(?:on\s+)?(\d{1,2}/\d{1,2}/\d{2,4})",
        r"(\d{1,2}/\d{1,2}/\d{2,4})\s+(?:Sold|SOLD)",
    ]
    verified_date = None
    for pat in date_patterns:
        m = re.search(pat, text)
        if m:
            verified_date = _parse_sale_date(m.group(1))
            if verified_date:
                break

    if not verified_date:
        return comp, "sold_event_found_but_date_not_parsed"

    out = dict(comp)
    out["sold_date"] = verified_date
    out["verification"] = "unit_level_closed_sale_verified"
    out["verification_source"] = "Redfin individual property page"
    out["verification_url"] = url
    return out, None


def verify_candidate_comps(comps):
    verified = []
    warnings = []
    for comp in comps:
        checked, warning = verify_redfin_closed_sale(comp)
        verified.append(checked)
        if warning:
            warnings.append(f"Unit {comp.get('unit')}: {warning}")
    return verified, warnings


def conservative_arv_from_comps(comps, subject):
    verified = [
        c for c in comps
        if c.get("sold_price")
        and c.get("sold_date")
        and c.get("verification") == "unit_level_closed_sale_verified"
    ]
    if len(verified) < 3:
        return None, "insufficient_date_verified_closed_sales", "unavailable"

    # Reject stale records from ARV. Same-building co-op comps may expand
    # to 24 months, but not beyond that without explicit review.
    cutoff = date.today().replace(year=date.today().year - 2)
    verified = [
        c for c in verified
        if datetime.strptime(c["sold_date"], "%Y-%m-%d").date() >= cutoff
    ]
    if len(verified) < 3:
        return None, "insufficient_recent_verified_closed_sales", "unavailable"

    top = verified[:5]

    if subject.get("sqft"):
        ppsf = sorted(c["price_per_sqft"] for c in top if c.get("price_per_sqft"))
        if len(ppsf) >= 3:
            n = len(ppsf)
            med = ppsf[n//2] if n % 2 else (ppsf[n//2-1] + ppsf[n//2]) / 2
            return round(med * subject["sqft"], -3), "median_same_building_ppsf", "medium"

    prices = sorted(c["sold_price"] for c in top)
    n = len(prices)
    med = prices[n//2] if n % 2 else (prices[n//2-1] + prices[n//2]) / 2
    return round(med, -3), "median_same_building_sale_price", "low"

def sales_for_parcel(parcel_id):
    if not parcel_id:
        return []
    rows = ckan_search(SALES_RESOURCE_ID, filters={"PARID": parcel_id}, limit=500)
    return rows


def compact_assessment(rec):
    fields = [
        "PARID", "PROPERTYHOUSENUM", "PROPERTYADDRESS", "PROPERTYUNIT",
        "PROPERTYCITY", "PROPERTYSTATE", "PROPERTYZIP",
        "MUNIDESC", "SCHOOLDESC", "NEIGHCODE", "NEIGHDESC",
        "CLASSDESC", "USECODE", "USEDESC",
        "SALEDATE", "SALEPRICE", "SALECODE", "SALEDESC",
        "PREVSALEDATE", "PREVSALEPRICE", "PREVSALEDATE2", "PREVSALEPRICE2",
        "FAIRMARKETTOTAL", "YEARBLT", "STYLEDESC", "STORIES",
        "TOTALROOMS", "BEDROOMS", "FULLBATHS", "HALFBATHS",
        "FINISHEDLIVINGAREA", "TAXYEAR", "ASOFDATE",
    ]
    return {k: rec.get(k) for k in fields}


def compact_sale(rec):
    # Preserve useful fields even if schema names vary.
    preferred = [
        "PARID", "FULL_ADDRESS", "PROPERTYHOUSENUM",
        "PROPERTYADDRESSSTREET", "PROPERTYADDRESSSUF",
        "PROPERTYUNITNO", "PROPERTYCITY", "PROPERTYSTATE", "PROPERTYZIP",
        "RECORDDATE", "SALEDATE", "PRICE", "SALEPRICE",
        "SALECODE", "SALEDESC", "DEEDBOOK", "DEEDPAGE",
        "INSTRTYP", "INSTRTYPDESC",
    ]
    return {k: rec.get(k) for k in preferred if k in rec}


def build_result(address, city, state, zipcode):
    parsed = parse_address(address)
    target = {
        **parsed,
        "city": clean(city),
        "state": clean(state),
        "zip": clean(zipcode),
    }

    records, attempts = get_candidates(target)
    scored = []
    for rec in records:
        score, reasons = score_candidate(rec, target)
        scored.append({"score": score, "reasons": reasons, "record": rec})
    scored.sort(key=lambda x: x["score"], reverse=True)

    structure, chosen = classify_structure(scored, target)

    result = {
        "engine": "Allegheny County Comps Engine",
        "version": VERSION,
        "phase": "property_resolution_building_reference_and_sales_history",
        "generated_at": utc_now(),
        "input": {"address": address, "city": city, "state": state, "zip": zipcode},
        "parsed_input": target,
        "search_attempts": attempts,
        "resolution_type": structure,
        "status": "unresolved",
        "arv": None,
        "arv_status": "not_calculated_phase_1",
        "comps": [],
        "comps_status": "not_selected_phase_1",
        "source": {
            "publisher": "Allegheny County / WPRDC",
            "assessment_resource_id": ASSESSMENT_RESOURCE_ID,
            "sales_resource_id": SALES_RESOURCE_ID,
        },
        "candidate_count": len(scored),
        "top_candidates": [
            {
                "score": x["score"],
                "reasons": x["reasons"],
                "PARID": x["record"].get("PARID"),
                "PROPERTYHOUSENUM": x["record"].get("PROPERTYHOUSENUM"),
                "PROPERTYADDRESS": x["record"].get("PROPERTYADDRESS"),
                "PROPERTYUNIT": x["record"].get("PROPERTYUNIT"),
                "PROPERTYCITY": x["record"].get("PROPERTYCITY"),
                "PROPERTYZIP": x["record"].get("PROPERTYZIP"),
                "USEDESC": x["record"].get("USEDESC"),
            }
            for x in scored[:20]
        ],
    }

    if not chosen:
        if structure == "building_parcel_candidates":
            building_ref = infer_building_reference(scored, target)
            if building_ref:
                rec = building_ref["record"]
                parcel_id = clean(rec.get("PARID"))
                result["status"] = "building_reference_resolved"
                result["resolution_type"] = "coop_or_multiunit_building_reference"
                result["building_reference"] = {
                    "parcel_id": parcel_id,
                    "scope": "building_only_not_unit",
                    "requested_unit": target["unit"],
                    "county_unit": clean(rec.get("PROPERTYUNIT")),
                    "unit_verified": False,
                    "score": building_ref["building_reference_score"],
                    "reasons": (
                        building_ref["reasons"]
                        + building_ref["building_reference_reasons"]
                    ),
                    "usedesc": rec.get("USEDESC"),
                }
                result["assessment"] = compact_assessment(rec)

                # County parcel sales are intentionally NOT fetched here.
                # A master/building parcel sale must never be presented as
                # the requested co-op unit's sale history.
                result["sales_history"] = []
                result["sales_history_count"] = 0
                result["sales_history_status"] = (
                    "suppressed_for_building_reference_not_unit_history"
                )
                result["resolution_message"] = (
                    "זוהה Parcel מגורים רב-יחידתי כ-Building Reference בלבד. "
                    "ה-Unit המבוקש אינו מאומת כ-Parcel נפרד, ולכן היסטוריית מכירות "
                    "של ה-Parcel אינה מיוחסת ליחידה. Sold Comps ל-Co-op חייבים "
                    "להגיע מעסקאות Unit באותו בניין."
                )
                result["next_comp_strategy"] = {
                    "property_structure": "coop_or_multiunit",
                    "priority": "same_building_sold_units",
                    "unit_parcel_required": False,
                    "county_parcel_role": "building_reference_only",
                    "arv_allowed_from_master_parcel_sales": False,
                }

                # PHASE 2 SAFETY GATE:
                # County/WPRDC sales are parcel-level. Because the requested
                # co-op unit is not independently parcelized here, they cannot
                # prove a sale belongs to Unit 621. Do not manufacture comps.
                subject = {
                    "beds": _num(os.getenv("TARGET_BEDS", "2")),
                    "baths": _num(os.getenv("TARGET_BATHS", "1")),
                    "sqft": _num(os.getenv("TARGET_SQFT")),
                }
                sold_comps, source_errors, source_rows, source_headers = discover_same_building_sold_comps(
                    target, subject
                )
                # Redfin individual property pages return HTTP 405 from GitHub
                # Actions, so V1.8 validates the sold-search CSV itself instead.
                verification_warnings = []
                result["subject_for_comp_matching"] = subject
                result["sold_comps"] = sold_comps
                result["sold_comps_count"] = len(sold_comps)
                result["verified_closed_comps_count"] = sum(
                    1 for c in sold_comps
                    if c.get("verification") == "unit_level_closed_sale_verified"
                )
                verified_count = result["verified_closed_comps_count"]
                result["sold_comps_status"] = (
                    "verified_closed_comps_found"
                    if verified_count
                    else (
                        "same_building_rows_found_without_sold_date"
                        if sold_comps
                        else ("source_unavailable" if source_errors else "no_matching_unit_sales_found")
                    )
                )
                result["sold_comps_source"] = {
                    "name": "Redfin downloadable sold-search CSV",
                    "rows_scanned": source_rows,
                    "headers": source_headers,
                    "errors": source_errors,
                    "search_scope": "ZIP 15213 recently-sold filter",
                    "sold_within_days": 730,
                    "verification_warnings": verification_warnings,
                    "stability": "best_effort_undocumented_endpoint",
                }
                result["sold_comps_source_requirement"] = [
                    "same_building_unit_level_closed_sale",
                    "verifiable_sale_price",
                    "unit_number",
                    "sale_date_preferred",
                    "beds_baths_sqft_when_available",
                ]
                result["comp_search_plan"] = {
                    "address": (
                        f"{target['house_number']} {target['street']}"
                        + (f" #{target['unit']}" if target.get("unit") else "")
                        + f", {target['city']}, {target['state']} {target['zip']}"
                    ),
                    "building_address": (
                        f"{target['house_number']} {target['street']}, "
                        f"{target['city']}, {target['state']} {target['zip']}"
                    ),
                    "requested_unit": target["unit"],
                    "strategy": "same_building_first",
                    "sale_status": "sold_closed_only",
                    "initial_lookback_months": 12,
                    "expanded_lookback_months": 24,
                    "target_beds": None,
                    "target_baths": None,
                    "target_sqft": None,
                    "filters": {
                        "same_building": True,
                        "exclude_active_listings": True,
                        "exclude_pending_listings": True,
                        "exclude_master_parcel_sales": True,
                        "prefer_same_beds": True,
                        "prefer_similar_baths": True,
                        "prefer_sqft_within_pct": 25,
                    },
                }
                arv, arv_method, arv_confidence = conservative_arv_from_comps(
                    sold_comps, subject
                )
                result["arv"] = arv
                result["arv_method"] = arv_method
                result["arv_status"] = (
                    "calculated_from_same_building_unit_sales"
                    if arv is not None
                    else "not_calculated_without_enough_verified_unit_comps"
                )
                result["arv_confidence"] = arv_confidence
                return result

            result["resolution_message"] = (
                "נמצאו מספר Parcels חזקים באותה כתובת בניין, אך אין התאמת Unit "
                "נפרדת ולא ניתן לזהות בבטחה Parcel מגורים ראשי."
            )
        else:
            result["resolution_message"] = (
                "לא נמצאה התאמה יחידה ובטוחה. המנוע לא בוחר Parcel בניחוש."
            )
        return result

    rec = chosen["record"]
    parcel_id = clean(rec.get("PARID"))
    sales = sales_for_parcel(parcel_id)

    result["status"] = "resolved"
    result["resolution"] = {
        "parcel_id": parcel_id,
        "score": chosen["score"],
        "reasons": chosen["reasons"],
        "requested_unit": target["unit"],
        "county_unit": clean(rec.get("PROPERTYUNIT")),
        "unit_verified": "unit_exact" in chosen["reasons"],
        "master_parcel_warning": structure == "likely_master_or_coop_parcel",
    }

    if structure == "likely_master_or_coop_parcel":
        result["resolution_message"] = (
            "נמצאה כתובת County חזקה אך PROPERTYUNIT ריק. "
            "ה-Parcel מטופל כ-master/co-op candidate בלבד; "
            "אין טענה ש-Unit המבוקש הוא Parcel נפרד."
        )
    else:
        result["resolution_message"] = "נמצאה התאמת Unit/Parcel חזקה."

    result["assessment"] = compact_assessment(rec)
    result["sales_history"] = [compact_sale(x) for x in sales]
    result["sales_history_count"] = len(sales)
    return result


def safe_filename(address):
    return re.sub(r"[^A-Z0-9_\-]", "", norm(address).replace(" ", "_")) or "PROPERTY"


def print_summary(result):
    print("\n" + "=" * 76)
    print(f"ALLEGHENY COUNTY COMPS ENGINE V{VERSION} - PHASE 2 - SOLD CSV VALIDATION")
    print("=" * 76)
    i = result["input"]
    print(f"Input: {i['address']}, {i['city']}, {i['state']} {i['zip']}")
    print(f"Status: {result['status']}")
    print(f"Resolution type: {result['resolution_type']}")

    print("\nSearch attempts:")
    for a in result.get("search_attempts", []):
        print(f"  - {a['label']}: {a['count']} rows" + (f" | ERROR: {a['error']}" if a['error'] else ""))

    if result["status"] == "building_reference_resolved":
        b = result["building_reference"]
        a = result.get("assessment") or {}
        print("\n✅ Building Reference identified safely.")
        print(f"Building PARID: {b['parcel_id']}")
        print(f"Scope: {b['scope']}")
        print(f"Requested Unit: {b['requested_unit']}")
        print(f"County Unit: {b['county_unit'] or '[blank]'}")
        print(f"Unit verified: {b['unit_verified']}")
        print(f"Use: {a.get('USEDESC')}")
        print(f"Message: {result.get('resolution_message', '')}")
        print("County parcel sales: SUPPRESSED (not unit-level history)")
        print("Next strategy: SAME-BUILDING SOLD UNITS")
        print(f"Sold comps status: {result.get('sold_comps_status')}")
        print(f"Sold comps found: {result.get('sold_comps_count', 0)}")
        print(f"Verified closed comps: {result.get('verified_closed_comps_count', 0)}")
        source = result.get("sold_comps_source") or {}
        print(f"Source rows scanned: {source.get('rows_scanned', 0)}")
        print(f"CSV headers: {source.get('headers', [])}")
        for err in source.get("errors", []):
            print(f"  Source warning: {err}")
        for warning in source.get("verification_warnings", []):
            print(f"  Verification warning: {warning}")
        for comp in result.get("sold_comps", [])[:10]:
            print(
                f"  COMP Unit {comp.get('unit')} | "
                f"${comp.get('sold_price'):,.0f} | {comp.get('sold_date') or 'date unavailable'} | "
                f"{comp.get('beds')} bd / {comp.get('baths')} ba | "
                f"{comp.get('sqft') or 'sqft unavailable'} sqft | "
                f"Score={comp.get('comp_score')} | "
                f"Status={comp.get('raw_status') or '[blank]'} | "
                f"SaleType={comp.get('raw_sale_type') or '[blank]'} | "
                f"Verification={comp.get('verification')}"
            )
        print(f"ARV status: {result.get('arv_status')}")
        if result.get("arv") is not None:
            print(
                f"ARV: ${result['arv']:,.0f} | "
                f"Method={result.get('arv_method')} | "
                f"Confidence={result.get('arv_confidence')}"
            )
        plan = result.get("comp_search_plan") or {}
        print(
            "Comp window: "
            f"{plan.get('initial_lookback_months')} months "
            f"(expand to {plan.get('expanded_lookback_months')} if needed)"
        )
        if result.get("arv") is None:
            print("\nℹ️ אין ARV עד שיש לפחות 3 עסקאות Unit מתאימות.")
        else:
            print("\n✅ ARV חושב על בסיס עסקאות Unit באותו בניין.")
        return

    if result["status"] != "resolved":
        print("\n❌ לא נמצאה התאמה יחידה ובטוחה.")
        print(f"Message: {result.get('resolution_message', '')}")
        for c in result.get("top_candidates", [])[:10]:
            print(
                f"  Candidate: PARID={c.get('PARID')} | "
                f"{c.get('PROPERTYHOUSENUM')} {c.get('PROPERTYADDRESS')} | "
                f"Unit={c.get('PROPERTYUNIT')} | Score={c.get('score')} | "
                f"Use={c.get('USEDESC')}"
            )
        print("\nלא חושב ARV ולא נבחרו Comps.")
        return

    r = result["resolution"]
    a = result.get("assessment") or {}
    print(f"\n✅ PARID: {r['parcel_id']}")
    print(f"Requested Unit: {r['requested_unit']}")
    print(f"County Unit: {r['county_unit'] or '[blank]'}")
    print(f"Unit verified: {r['unit_verified']}")
    print(f"Master/Co-op warning: {r['master_parcel_warning']}")
    print(f"Message: {result['resolution_message']}")
    print(f"Use: {a.get('USEDESC')}")
    print(f"Beds: {a.get('BEDROOMS')}")
    print(f"Full Baths: {a.get('FULLBATHS')}")
    print(f"Living Area: {a.get('FINISHEDLIVINGAREA')}")
    print(f"Year Built: {a.get('YEARBLT')}")
    print(f"Sales history rows: {result.get('sales_history_count', 0)}")

    for sale in result.get("sales_history", [])[:10]:
        price = sale.get("PRICE", sale.get("SALEPRICE"))
        print(
            f"  • {sale.get('SALEDATE')} | ${price} | "
            f"Code={sale.get('SALECODE')} | {sale.get('SALEDESC')}"
        )

    print("\nℹ️ Phase 1 בלבד: עדיין אין ARV ואין בחירת Sold Comps.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--address", default=DEFAULT_ADDRESS)
    p.add_argument("--city", default=DEFAULT_CITY)
    p.add_argument("--state", default=DEFAULT_STATE)
    p.add_argument("--zip", dest="zipcode", default=DEFAULT_ZIP)
    p.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = p.parse_args()

    try:
        result = build_result(args.address, args.city, args.state, args.zipcode)
    except requests.RequestException as exc:
        print(f"❌ שגיאת תקשורת מול WPRDC: {exc}")
        sys.exit(2)
    except Exception as exc:
        print(f"❌ שגיאה: {exc}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{safe_filename(args.address)}_county_test.json"
    temp = output_file.with_suffix(".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    temp.replace(output_file)

    print_summary(result)
    print(f"\n📄 JSON נשמר: {output_file}")

    # A verified unit parcel OR a safe building-level reference is success.
    # Truly unresolved cases remain exit code 3 so GitHub Actions highlights them.
    if result["status"] not in {"resolved", "building_reference_resolved"}:
        sys.exit(3)


if __name__ == "__main__":
    main()
