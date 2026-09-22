import argparse
import json
import csv
import io
import os
from datetime import datetime, date
from html import unescape
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

VERSION = "2.5"

CKAN_SEARCH = "https://data.wprdc.org/api/3/action/datastore_search"
ASSESSMENT_RESOURCE_ID = "65855e14-549e-4992-b5be-d629afc676fa"
SALES_RESOURCE_ID = "5bbe6c55-bce6-4edb-9d04-68edeb6bf7b1"

DEFAULT_ADDRESS = "4601 Fifth Ave #621"
DEFAULT_CITY = "Pittsburgh"
DEFAULT_STATE = "PA"
DEFAULT_ZIP = "15213"

OUTPUT_DIR = Path("COMPS_REPORTS")
TIMEOUT = 25
HEADERS = {"User-Agent": "PA-RealEstate-Intelligence-Hub-Comps/2.5"}

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


def redfin_city_sold_rows(max_pages=8, page_size=350):
    """
    Best-effort Redfin downloadable sold-search CSV adapter.
    This is intentionally non-fatal because it is not a stable public API.
    """
    base_url = "https://www.redfin.com/stingray/api/gis-csv"
    all_rows, errors = [], []

    for page in range(1, max_pages + 1):
        params = {
            "al": 1,
            "v": 8,
            "market": "pittsburgh",
            "mpt": 13,
            "include_nearby_homes": "true",
            "num_homes": page_size,
            "ord": "redfin-recommended-asc",
            "page_number": page,
            "start": (page - 1) * page_size,
            "region_id": 15702,
            "region_type": 6,
            "sf": "1,2,3,5,6,7",
            "status": 9,
            "uipt": "1,2,3,4,5,6,7,8",
            "sold_within_days": 365,
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

    headers = list(all_rows[0].keys()) if all_rows else []
    return all_rows, errors, headers
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

        # Strict closed-sale gate. The previous run proved that this endpoint
        # can return Active / MLS Listing rows. Never allow those into comps.
        if norm(raw_status) != "sold" or not sold_date:
            continue

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
            "feed_classification": "verified_closed_sale",
            "verification": "unit_level_closed_sale_verified",
        })

    unique = {}
    for c in comps:
        unique[(norm(c["unit"]), c["sold_date"], c["sold_price"])] = c

    comps = sorted(
        unique.values(),
        key=lambda c: (c["comp_score"], c["sold_date"] or ""),
        reverse=True,
    )
    return comps, errors, len(rows), headers
def _page_text(html):
    text = re.sub(r"(?is)<script.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()






def realtor_sold_index_comps(target, subject):
    """
    Best-effort Realtor.com recently-sold index adapter.
    Unlike the blocked Homes.com pagination, this requests a public sold-results
    page and extracts only same-building cards. No hardcoded comp values.
    A row is accepted only when the card itself contains Sold + price + unit.
    If a sold date is absent from the index card, the property detail URL is
    fetched and must provide the date before the comp becomes verified.
    """
    house = clean(target.get("house_number"))
    street = clean(target.get("street"))
    city = clean(target.get("city"))
    state = clean(target.get("state"))
    zipcode = clean(target.get("zip"))
    target_unit = norm(target.get("unit"))
    headers = {
        "User-Agent": REDFIN_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    errors, comps, seen = [], [], set()

    urls = [
        f"https://www.realtor.com/realestateandhomes-search/{city}_{state}/show-recently-sold",
        f"https://www.realtor.com/realestateandhomes-search/{zipcode}/show-recently-sold",
    ]
    street_norms = {
        norm(street),
        norm(street).replace("FIFTH", "5TH"),
        norm(street).replace("5TH", "FIFTH"),
    }

    for index_url in urls:
        try:
            r = requests.get(index_url, headers=headers, timeout=25)
            if r.status_code >= 400:
                errors.append(f"Realtor sold index HTTP {r.status_code}: {index_url}")
                continue
            raw_html = r.text
            text = _page_text(raw_html)
        except Exception as exc:
            errors.append(f"Realtor sold index error: {type(exc).__name__}: {exc}")
            continue

        # Find local windows around exact building occurrences.
        for m in re.finditer(rf"\b{re.escape(house)}\b", text, flags=re.I):
            window = text[max(0,m.start()-220):min(len(text),m.start()+650)]
            nwin=norm(window)
            if not any(v and v in nwin for v in street_norms):
                continue
            if not re.search(r"\bSold\b",window,re.I):
                continue

            um=re.search(r"(?:Unit|Apt)\s*#?\s*([0-9A-Za-z-]{1,8})",window,re.I)
            if not um:
                continue
            unit=um.group(1)
            if norm(unit)==target_unit:
                continue

            prices=[_num(x) for x in re.findall(r"\$([\d,]{4,})",window)]
            prices=[x for x in prices if x and x>=10000]
            if not prices:
                continue
            sold_price=prices[0]

            sold_date=None
            for pat in (
                r"Sold\s*[-–]?\s*([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
                r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})[^.]{0,50}\bSold\b",
            ):
                dm=re.search(pat,window,re.I)
                if dm:
                    sold_date=_parse_sale_date(dm.group(1))
                    if sold_date: break

            # Extract likely property-detail link containing this unit.
            detail_url=None
            unit_pat=re.escape(str(unit))
            hrefs=re.findall(r'href=["\']([^"\']+)["\']',raw_html,re.I)
            for href in hrefs:
                hnorm=norm(href)
                if house in hnorm and unit_pat and re.search(rf"(?:APT|UNIT)\s*{unit_pat}\b",hnorm,re.I):
                    if href.startswith("/"):
                        href="https://www.realtor.com"+href
                    if href.startswith("http"):
                        detail_url=href
                        break

            # Index cards often omit date. Verify detail page before acceptance.
            beds=baths=sqft=None
            if not sold_date and detail_url:
                try:
                    dr=requests.get(detail_url,headers=headers,timeout=20,allow_redirects=True)
                    if dr.status_code < 400:
                        dtext=_page_text(dr.text)
                        for pat in (
                            r"Sold\s*[-–]?\s*([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
                            r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})[^.]{0,80}\bSold\b",
                            r"Last sold in\s+\d{4}.*?([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
                        ):
                            dm=re.search(pat,dtext,re.I|re.S)
                            if dm:
                                sold_date=_parse_sale_date(dm.group(1))
                                if sold_date: break
                        # Require page to corroborate price.
                        if f"{int(sold_price):,}" not in dtext and str(int(sold_price)) not in dtext:
                            sold_date=None
                        bm=re.search(r"(\d+(?:\.\d+)?)\s*(?:bed|beds|bd)\b",dtext[:10000],re.I)
                        if bm: beds=_num(bm.group(1))
                        bam=re.search(r"(\d+(?:\.\d+)?)\s*(?:bath|baths|ba)\b",dtext[:10000],re.I)
                        if bam: baths=_num(bam.group(1))
                        sm=re.search(r"([\d,]{3,6})\s*(?:sqft|square feet|sq\.?\s*ft)",dtext[:10000],re.I)
                        if sm:
                            sv=_num(sm.group(1))
                            if sv and 300<=sv<=10000: sqft=sv
                except Exception as exc:
                    errors.append(f"Realtor detail error unit {unit}: {type(exc).__name__}: {exc}")

            if not sold_date:
                continue

            key=(norm(unit),sold_date,int(round(sold_price)))
            if key in seen:
                continue
            score=100.0
            if subject.get("beds") and beds==subject.get("beds"): score+=20
            if subject.get("baths") and baths==subject.get("baths"): score+=15
            comps.append({
                "unit":unit,
                "address":f"{house} {street} Unit {unit}, {city}, {state} {zipcode}",
                "sold_price":sold_price,"sold_date":sold_date,
                "beds":beds,"baths":baths,"sqft":sqft,
                "comp_score":score,
                "source":"Realtor.com recently sold + property history",
                "source_url":detail_url or index_url,
                "raw_status":"Sold","raw_sale_type":"public_sold_index",
                "feed_classification":"verified_closed_sale",
                "verification":"unit_level_closed_sale_verified",
                "verification_method":"realtor_sold_card_plus_date_verification",
            })
            seen.add(key)

    comps.sort(key=lambda x:(x.get("sold_date") or "",x.get("comp_score") or 0),reverse=True)
    return comps, errors


def bing_rss_verified_comps(target, subject, max_results=30):
    """
    Bing RSS is used only for URL discovery. A search snippet is NEVER treated
    as a verified sale. Each discovered property page is fetched separately
    and must itself contain: exact building + different unit + Sold event +
    sold date + sold price. This avoids relying on blocked ZIP index pages.
    """
    house = clean(target.get("house_number"))
    street = clean(target.get("street"))
    city = clean(target.get("city"))
    state = clean(target.get("state"))
    zipcode = clean(target.get("zip"))
    target_unit = norm(target.get("unit"))
    query = f'"{house} {street}" "{city}" "{zipcode}" sold'
    rss_url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)

    headers = {
        "User-Agent": REDFIN_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    errors, discovered = [], []
    try:
        r = requests.get(rss_url, headers=headers, timeout=25)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for item in root.findall(".//item"):
            link = clean(item.findtext("link"))
            if link and link not in discovered:
                discovered.append(link)
    except Exception as exc:
        return [], [f"Bing RSS discovery error: {type(exc).__name__}: {exc}"], 0

    allowed = (
        "realtor.com", "compass.com", "homes.com",
        "coldwellbankerhomes.com", "redfin.com"
    )
    discovered = [u for u in discovered if any(d in u.lower() for d in allowed)]

    comps, seen = [], set()
    street_norms = {
        norm(street),
        norm(street).replace("FIFTH", "5TH"),
        norm(street).replace("5TH", "FIFTH"),
    }

    for u in discovered[:max_results]:
        try:
            rr = requests.get(u, headers=headers, timeout=20, allow_redirects=True)
            if rr.status_code >= 400:
                errors.append(f"Property page HTTP {rr.status_code}: {u}")
                continue
            text = _page_text(rr.text)
        except Exception as exc:
            errors.append(f"Property page error {type(exc).__name__}: {u}")
            continue

        ntext = norm(text)
        if house not in ntext or not any(x and x in ntext for x in street_norms):
            continue

        # Unit can be expressed as Unit 326, Apt 326 or #326.
        unit = None
        for pat in (
            r"(?:Unit|Apt)\s*#?\s*([0-9A-Za-z-]{1,8})",
            r"#\s*([0-9A-Za-z-]{1,8})",
        ):
            m = re.search(pat, text[:5000], flags=re.I)
            if m:
                unit = m.group(1)
                break
        if not unit or norm(unit) == target_unit:
            continue

        # Search compact local windows around explicit SOLD occurrences.
        sale = None
        for sm in re.finditer(r"\bSold\b", text, flags=re.I):
            window = text[max(0, sm.start()-220): min(len(text), sm.start()+420)]

            date = None
            for pat in (
                r"(?:Sold(?:\s+on)?|Date Sold)\s*[:\-]?\s*"
                r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
                r"(\d{1,2}/\d{1,2}/\d{2,4})\s*\|?\s*Sold",
                r"Sold\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            ):
                dm = re.search(pat, window, flags=re.I)
                if dm:
                    date = _parse_sale_date(dm.group(1))
                    if date:
                        break

            price = None
            for pat in (
                r"Sold(?:\s+for)?\s*[:\-]?\s*\$([\d,]{4,})",
                r"(?:Last Sold Price|Last sale price)\s*\$([\d,]{4,})",
                r"\$([\d,]{4,})[^$]{0,100}\bSold\b",
                r"\bSold\b[^$]{0,100}\$([\d,]{4,})",
            ):
                pm = re.search(pat, window, flags=re.I)
                if pm:
                    price = _num(pm.group(1))
                    if price and price >= 10000:
                        break
            if date and price:
                sale=(date,price)
                break

        if not sale:
            continue
        sold_date, sold_price = sale
        key=(norm(unit),sold_date,int(round(sold_price)))
        if key in seen:
            continue

        beds=baths=sqft=None
        bm=re.search(r"(\d+(?:\.\d+)?)\s*(?:Beds?|bd)\b",text[:8000],re.I)
        if bm: beds=_num(bm.group(1))
        bam=re.search(r"(\d+(?:\.\d+)?)\s*(?:Baths?|ba)\b",text[:8000],re.I)
        if bam: baths=_num(bam.group(1))
        sqm=re.search(r"([\d,]{3,6})\s*(?:Sq\.?\s*Ft|sqft|square feet)",text[:8000],re.I)
        if sqm:
            sv=_num(sqm.group(1))
            if sv and 300 <= sv <= 10000: sqft=sv

        score=100.0
        if subject.get("beds") and beds == subject.get("beds"): score += 20
        if subject.get("baths") and baths == subject.get("baths"): score += 15

        comps.append({
            "unit":unit,
            "address":f"{house} {street} Unit {unit}, {city}, {state} {zipcode}",
            "sold_price":sold_price,
            "sold_date":sold_date,
            "beds":beds,"baths":baths,"sqft":sqft,
            "comp_score":score,
            "source":"independent public property page",
            "source_url":rr.url,
            "raw_status":"Sold",
            "raw_sale_type":"public_property_history",
            "feed_classification":"verified_closed_sale",
            "verification":"unit_level_closed_sale_verified",
            "verification_method":"bing_url_discovery_then_direct_page_verification",
        })
        seen.add(key)

    comps.sort(key=lambda x:(x.get("sold_date") or "",x.get("comp_score") or 0),reverse=True)
    return comps, errors, len(discovered)


def homes_sold_index_comps(target, subject, max_pages=12):
    """
    Discover same-building closed sales from Homes.com's public ZIP sold index.
    No sale is hardcoded. Each accepted row must contain the exact building,
    a unit number, SOLD + date, and a price in the same local text window.
    """
    zipcode = clean(target.get("zip"))
    street = clean(target.get("street"))
    house = clean(target.get("house_number"))
    target_unit = norm(target.get("unit"))
    headers = {
        "User-Agent": REDFIN_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    errors, comps, seen = [], [], set()

    # Homes uses /sold/ and /sold/pN/ pagination.
    urls = [f"https://www.homes.com/pittsburgh-pa/{zipcode}/sold/"] + [
        f"https://www.homes.com/pittsburgh-pa/{zipcode}/sold/p{i}/"
        for i in range(2, max_pages + 1)
    ]

    # Accept Fifth/5th spelling variants.
    street_variants = {norm(street), norm(street.replace("FIFTH", "5TH"))}
    street_variants |= {s.replace("FIFTH", "5TH") for s in list(street_variants)}

    for page_url in urls:
        try:
            r = requests.get(page_url, headers=headers, timeout=25)
            if r.status_code >= 400:
                errors.append(f"Homes sold page HTTP {r.status_code}: {page_url}")
                continue
            text = _page_text(r.text)
        except Exception as exc:
            errors.append(f"Homes sold page error: {type(exc).__name__}: {exc}")
            continue

        # Split around every occurrence of the house number; inspect bounded
        # windows so a price/date from another card cannot be attached.
        for m in re.finditer(rf"\b{re.escape(house)}\b", text, flags=re.I):
            window = text[max(0, m.start()-180): min(len(text), m.start()+520)]
            nwin = norm(window)
            if not any(s and s in nwin for s in street_variants):
                continue

            um = re.search(
                rf"{re.escape(house)}\s+(?:Fifth|5th)\s+(?:Ave|Avenue)\s+(?:Unit|Apt)\s+([0-9A-Za-z-]+)",
                window, flags=re.I
            )
            if not um:
                continue
            unit = um.group(1)
            if norm(unit) == target_unit:
                continue

            dm = re.search(
                r"\bSold\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),\s+(\d{4})",
                window, flags=re.I
            )
            if not dm:
                continue
            sold_date = _parse_sale_date(f"{dm.group(1)} {dm.group(2)}, {dm.group(3)}")
            if not sold_date:
                continue

            # Price must be in this card/window. Choose the nearest plausible
            # dollar amount before the SOLD phrase when possible.
            prefix = window[:dm.start()]
            prices = re.findall(r"\$([\d,]{4,})", prefix)
            if not prices:
                prices = re.findall(r"\$([\d,]{4,})", window)
            if not prices:
                continue
            sold_price = _num(prices[-1])
            if not sold_price or sold_price < 10000:
                continue

            key=(norm(unit),sold_date,int(round(sold_price)))
            if key in seen:
                continue

            beds=baths=sqft=None
            bm=re.search(r"(\d+(?:\.\d+)?)\s+Beds?\b",window,re.I)
            if bm: beds=_num(bm.group(1))
            bam=re.search(r"(\d+(?:\.\d+)?)\s+Baths?\b",window,re.I)
            if bam: baths=_num(bam.group(1))
            sm=re.search(r"([\d,]{3,6})\s+Sq\s*Ft\b",window,re.I)
            if sm:
                v=_num(sm.group(1))
                if v and 300 <= v <= 10000: sqft=v

            score=100.0
            if subject.get("beds") and beds == subject.get("beds"): score += 20
            if subject.get("baths") and baths == subject.get("baths"): score += 15

            comps.append({
                "unit":unit,
                "address":f"{house} {street} Unit {unit}, {target.get('city')}, {target.get('state')} {zipcode}",
                "sold_price":sold_price,
                "sold_date":sold_date,
                "beds":beds,"baths":baths,"sqft":sqft,
                "comp_score":score,
                "source":"Homes.com public sold index",
                "source_url":page_url,
                "raw_status":"Sold",
                "raw_sale_type":"public_sold_index",
                "feed_classification":"verified_closed_sale",
                "verification":"unit_level_closed_sale_verified",
                "verification_method":"same_card_exact_building_unit_sold_date_price",
            })
            seen.add(key)

    comps.sort(key=lambda x:(x.get("sold_date") or "",x.get("comp_score") or 0),reverse=True)
    return comps, errors


def search_engine_same_building_sold_comps(target, subject, max_units=12):
    """
    Best-effort second-source discovery using public search-result HTML.
    This does NOT hardcode any comp. It searches the exact building address,
    extracts candidate property URLs, fetches those public pages, and accepts
    only pages that independently contain the same building, a unit, a SOLD
    event, a sale date and a sale price.

    Failure is safe: returns no comps and ARV remains unavailable.
    """
    base = clean(target.get("street"))
    city = clean(target.get("city"))
    state = clean(target.get("state"))
    zipcode = clean(target.get("zip"))
    query = f'"{base}" "{city}" {state} {zipcode} sold unit'
    url = "https://www.google.com/search?q=" + quote_plus(query) + "&num=20"

    headers = {
        "User-Agent": REDFIN_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    errors = []
    try:
        r = requests.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        return [], [f"search_error: {type(exc).__name__}: {exc}"]

    # Extract only known real-estate property-page destinations.
    hrefs = re.findall(r'href=["\'](?:/url\?q=)?(https?://[^"&\']+)', html, flags=re.I)
    allowed = ("realtor.com", "homes.com", "compass.com", "redfin.com", "coldwellbankerhomes.com")
    urls = []
    for u in hrefs:
        u = unescape(u)
        if not any(d in u.lower() for d in allowed):
            continue
        if u not in urls:
            urls.append(u)

    comps = []
    seen_units = set()
    for u in urls[:40]:
        try:
            rr = requests.get(u, headers=headers, timeout=20, allow_redirects=True)
            if rr.status_code >= 400:
                continue
            text = _page_text(rr.text)
        except Exception:
            continue

        # Require exact building identity in page text.
        ntext = norm(text)
        if norm(base) not in ntext and norm(base.replace("FIFTH", "5TH")) not in ntext:
            continue

        # Unit extraction from URL/title/text.
        unit = None
        for pat in (
            r"(?:unit|apt)[-_ /#]*(\d{1,4}[A-Za-z]?)",
            r"#\s*(\d{1,4}[A-Za-z]?)",
        ):
            m = re.search(pat, u + " " + text[:1200], flags=re.I)
            if m:
                unit = m.group(1)
                break
        if not unit or norm(unit) == norm(target.get("unit")) or norm(unit) in seen_units:
            continue

        # Require explicit SOLD language.
        if not re.search(r"\b(?:sold|last sold|sold for|date sold)\b", text, flags=re.I):
            continue

        # Parse a date close to sold language.
        sold_date = None
        for pat in (
            r"(?:Sold|Date Sold|Last sold)(?:\s*(?:on|:|-))?\s*([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
            r"(?:Sold|Date Sold|Last sold)(?:\s*(?:on|:|-))?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            r"(\d{1,2}/\d{1,2}/\d{2,4})\s+(?:Sold|SOLD)",
        ):
            m = re.search(pat, text)
            if m:
                sold_date = _parse_sale_date(m.group(1))
                if sold_date:
                    break
        if not sold_date:
            continue

        # Parse price near sold language first.
        sold_price = None
        for pat in (
            r"(?:Sold for|Last sold for|Last Sold Price|Sold)\s*\$([\d,]{4,})",
            r"\$([\d,]{4,})\s*(?:Last Sold Price|Sold)",
        ):
            m = re.search(pat, text, flags=re.I)
            if m:
                sold_price = _num(m.group(1))
                if sold_price:
                    break
        if not sold_price:
            continue

        beds = None
        baths = None
        sqft = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:bed|beds|bd)\b", text, flags=re.I)
        if m: beds = _num(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:bath|baths|ba)\b", text, flags=re.I)
        if m: baths = _num(m.group(1))
        m = re.search(r"([\d,]{3,6})\s*(?:sq\.?\s*ft|sqft|square feet)", text, flags=re.I)
        if m:
            candidate_sqft = _num(m.group(1))
            if candidate_sqft and 300 <= candidate_sqft <= 10000:
                sqft = candidate_sqft

        score = 100.0
        if subject.get("beds") and beds == subject.get("beds"): score += 20
        if subject.get("baths") and baths == subject.get("baths"): score += 15

        comps.append({
            "unit": unit,
            "address": f"{base} Unit {unit}, {city}, {state} {zipcode}",
            "sold_price": sold_price,
            "sold_date": sold_date,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "comp_score": score,
            "source": "public property page",
            "source_url": rr.url,
            "raw_status": "Sold",
            "raw_sale_type": "public_property_history",
            "feed_classification": "verified_closed_sale",
            "verification": "unit_level_closed_sale_verified",
            "verification_method": "independent_public_property_page",
        })
        seen_units.add(norm(unit))
        if len(comps) >= max_units:
            break

    comps.sort(key=lambda x: (x.get("sold_date") or "", x.get("comp_score") or 0), reverse=True)
    return comps, errors


def merge_verified_comps(primary, secondary):
    """Deduplicate by unit + sold date + price, preferring richer records."""
    merged = {}
    for comp in list(primary or []) + list(secondary or []):
        if comp.get("verification") != "unit_level_closed_sale_verified":
            continue
        key = (
            norm(comp.get("unit")),
            comp.get("sold_date"),
            int(round(comp.get("sold_price") or 0)),
        )
        old = merged.get(key)
        richness = sum(comp.get(k) is not None for k in ("beds", "baths", "sqft", "source_url"))
        old_richness = sum(old.get(k) is not None for k in ("beds", "baths", "sqft", "source_url")) if old else -1
        if old is None or richness > old_richness:
            merged[key] = comp
    return sorted(
        merged.values(),
        key=lambda x: (x.get("sold_date") or "", x.get("comp_score") or 0),
        reverse=True,
    )


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



# ---------------------------------------------------------------------------
# RentCast V2.5 - official API adapter (cache-first; max 1 call per run)
# ---------------------------------------------------------------------------
RENTCAST_BASE = "https://api.rentcast.io/v1"
RENTCAST_CACHE_DIR = OUTPUT_DIR / "rentcast_cache"
RENTCAST_MAX_CALLS_PER_RUN = 1

def _rentcast_full_address(target):
    a = f"{clean(target.get('house_number'))} {clean(target.get('street'))}"
    if clean(target.get("unit")):
        a += f" #{clean(target.get('unit'))}"
    return f"{a}, {clean(target.get('city'))}, {clean(target.get('state'))}, {clean(target.get('zip'))}"

def _rentcast_cache_path(target):
    key = re.sub(r"[^a-z0-9_]+", "", norm(_rentcast_full_address(target)).lower().replace(" ","_"))[:140]
    return RENTCAST_CACHE_DIR / f"{key}_value.json"

def _rentcast_read_cache(target):
    p=_rentcast_cache_path(target)
    if not p.exists(): return None,p
    try:
        x=json.loads(p.read_text(encoding="utf-8"))
        if isinstance(x,dict) and x.get("response"): return x,p
    except Exception: pass
    return None,p

def _rentcast_write_cache(target,response):
    RENTCAST_CACHE_DIR.mkdir(parents=True,exist_ok=True)
    p=_rentcast_cache_path(target)
    tmp=p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cached_at":utc_now(),"endpoint":"/avm/value","address":_rentcast_full_address(target),"response":response},ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(p)
    return p

def _rentcast_api_get(path,params):
    key=clean(os.getenv("RENTCAST_API_KEY"))
    if not key:
        return None,{"status":"api_key_missing","http_status":None,"message":"RENTCAST_API_KEY is not available."}
    try:
        r=requests.get(RENTCAST_BASE+path,params=params,headers={"Accept":"application/json","X-Api-Key":key,"User-Agent":HEADERS["User-Agent"]},timeout=TIMEOUT)
        if r.status_code>=400:
            return None,{"status":"authentication_failed" if r.status_code==401 else "http_error","http_status":r.status_code,"message":clean(r.text)[:500]}
        return r.json(),{"status":"success","http_status":r.status_code,"message":None}
    except Exception as exc:
        return None,{"status":"request_error","http_status":None,"message":f"{type(exc).__name__}: {exc}"}

def _rentcast_comp_address(c):
    if clean(c.get("formattedAddress")): return clean(c.get("formattedAddress"))
    return ", ".join(x for x in [clean(c.get("addressLine1") or c.get("address")),clean(c.get("city")),clean(c.get("state")),clean(c.get("zipCode") or c.get("zip"))] if x)

def _rentcast_iso_date(v):
    v=clean(v)
    if not v: return None
    p=_parse_sale_date(v[:10])
    if p: return p
    try: return datetime.fromisoformat(v.replace("Z","+00:00")).date().isoformat()
    except Exception: return None

def _rentcast_closed_sale(c):
    sold_date=None
    for k in ("lastSaleDate","soldDate","saleDate"):
        sold_date=_rentcast_iso_date(c.get(k))
        if sold_date: break
    sold_price=None
    for k in ("lastSalePrice","soldPrice","salePrice"):
        v=_num(c.get(k))
        if v and v>=10000: sold_price=v; break
    status=norm(c.get("status"))
    return bool(sold_date and sold_price and ("SOLD" in status or sold_price)),sold_date,sold_price

def rentcast_value_estimate(target,subject):
    cached,cache_path=_rentcast_read_cache(target)
    if cached:
        payload=cached["response"]; mode="cache"; calls=0
        diag={"status":"cache_hit","http_status":None,"message":None}
    else:
        params={"address":_rentcast_full_address(target),"maxRadius":5,"daysOld":730,"compCount":15,"lookupSubjectAttributes":"true"}
        if subject.get("beds") is not None: params["bedrooms"]=subject["beds"]
        if subject.get("baths") is not None: params["bathrooms"]=subject["baths"]
        if subject.get("sqft") is not None: params["squareFootage"]=subject["sqft"]
        payload,diag=_rentcast_api_get("/avm/value",params)
        calls=1 if diag.get("http_status") is not None else 0
        mode="api"
        if payload is None:
            return {"status":diag["status"],"api_calls":calls,"cache_path":str(cache_path),"provider_estimate":None,"verified_comps":[],"raw_comp_count":0,"diagnostic":diag}
        _rentcast_write_cache(target,payload)

    estimate={"value":_num(payload.get("price")),"range_low":_num(payload.get("priceRangeLow")),"range_high":_num(payload.get("priceRangeHigh")),"status":"provider_avm_estimate","provider":"RentCast","source_mode":mode,"not_a_closed_sale":True}
    verified=[]
    target_unit=norm(target.get("unit"))
    for c in payload.get("comparables") or []:
        if not isinstance(c,dict): continue
        addr=_rentcast_comp_address(c)
        if not addr or not same_building(addr,target): continue
        unit=extract_unit(addr)
        if not unit or norm(unit)==target_unit: continue
        ok,sd,sp=_rentcast_closed_sale(c)
        if not ok: continue
        sqft=_plausible_sqft(c.get("squareFootage"))
        verified.append({"unit":unit,"address":addr,"sold_price":sp,"sold_date":sd,"beds":_num(c.get("bedrooms")),"baths":_num(c.get("bathrooms")),"sqft":sqft,"price_per_sqft":round(sp/sqft,2) if sqft else None,"comp_score":round(float(c.get("correlation") or 0)*100,1),"match_reasons":["same_building","different_unit","rentcast_closed_sale_evidence"],"source":"RentCast API","source_url":None,"raw_status":c.get("status"),"raw_sale_type":c.get("listingType"),"feed_classification":"verified_closed_sale","verification":"unit_level_closed_sale_verified","verification_method":"rentcast_explicit_sale_date_and_sale_price"})
    return {"status":"success","api_calls":calls,"cache_path":str(cache_path),"provider_estimate":estimate,"subject_property":payload.get("subjectProperty"),"verified_comps":merge_verified_comps(verified,[]),"raw_comp_count":len(payload.get("comparables") or []),"diagnostic":diag}

def attach_rentcast_to_result(result):
    target=result.get("parsed_input") or {}
    if not target:
        result["rentcast"]={"status":"skipped_no_target","api_calls":0}; return result
    subject={"beds":_num(os.getenv("TARGET_BEDS","2")),"baths":_num(os.getenv("TARGET_BATHS","1")),"sqft":_num(os.getenv("TARGET_SQFT"))}
    rc=rentcast_value_estimate(target,subject)
    result["rentcast"]=rc
    if rc.get("provider_estimate"): result["rentcast_avm"]=rc["provider_estimate"]
    merged=merge_verified_comps(result.get("comps") or [],rc.get("verified_comps") or [])
    if merged:
        result["comps"]=merged
        result["comps_status"]="verified_closed_sales_available"
        arv,method,confidence=conservative_arv_from_comps(merged,subject)
        if arv is not None:
            result["arv"]=arv; result["arv_status"]="calculated_from_verified_closed_sales"; result["arv_method"]=method; result["arv_confidence"]=confidence
    result["version"]=VERSION
    result["rentcast_safety"]={"cache_first":True,"max_api_calls_per_run":1,"provider_avm_is_estimate":True,"listing_price_not_treated_as_closed_sale":True}
    return result


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
                verification_warnings = []

                # V2.1 fallback: if the strict Redfin feed cannot supply enough
                # verified same-building sales, discover and verify public
                # unit property-history pages. No comp is hardcoded.
                realtor_comps = []
                realtor_errors = []
                bing_comps = []
                bing_errors = []
                bing_urls_discovered = 0
                secondary_comps = []
                secondary_errors = []

                if len(sold_comps) < 3:
                    realtor_comps, realtor_errors = realtor_sold_index_comps(
                        target, subject
                    )
                    sold_comps = merge_verified_comps(sold_comps, realtor_comps)

                if len(sold_comps) < 3:
                    bing_comps, bing_errors, bing_urls_discovered = bing_rss_verified_comps(
                        target, subject
                    )
                    sold_comps = merge_verified_comps(sold_comps, bing_comps)

                # Final best-effort fallback. Still requires direct property-page
                # verification and therefore cannot manufacture a comp.
                if len(sold_comps) < 3:
                    secondary_comps, secondary_errors = search_engine_same_building_sold_comps(
                        target, subject
                    )
                    sold_comps = merge_verified_comps(sold_comps, secondary_comps)

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
                    "realtor_adapter": "Realtor.com recently-sold index + property-history verification",
                    "realtor_verified_rows": len(realtor_comps),
                    "realtor_errors": realtor_errors,
                    "bing_adapter": "Bing RSS URL discovery + direct property-page verification",
                    "bing_urls_discovered": bing_urls_discovered,
                    "bing_verified_rows": len(bing_comps),
                    "bing_errors": bing_errors,
                    "secondary_adapter": "public_property_page_search",
                    "secondary_verified_rows": len(secondary_comps),
                    "secondary_errors": secondary_errors,
                    "search_scope": "Strict Redfin sold feed + Realtor recently-sold/property-history + Bing discovery; exact same-building filter",
                    "sold_within_days": 365,
                    "verification_warnings": verification_warnings,
                    "stability": "best_effort_undocumented_endpoint",
                }
                result["sold_comps_source_requirement"] = [
                    "same_building_unit_level_closed_sale",
                    "verifiable_sale_price",
                    "unit_number",
                    "sale_date_required_for_arv",
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
    result = attach_rentcast_to_result(result)
    return result


def safe_filename(address):
    return re.sub(r"[^A-Z0-9_\-]", "", norm(address).replace(" ", "_")) or "PROPERTY"


def print_summary(result):
    print("\n" + "=" * 76)
    print(f"ALLEGHENY COUNTY COMPS ENGINE V{VERSION} - PHASE 2 - REALTOR SOLD INDEX ADAPTER")
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
        print(f"Realtor verified rows: {source.get('realtor_verified_rows', 0)}")
        for err in source.get("realtor_errors", []):
            print(f"  Realtor source warning: {err}")
        print(f"Bing property URLs discovered: {source.get('bing_urls_discovered', 0)}")
        print(f"Bing/direct-page verified rows: {source.get('bing_verified_rows', 0)}")
        for err in source.get("bing_errors", []):
            print(f"  Bing/direct-page warning: {err}")
        print(f"Secondary verified rows: {source.get('secondary_verified_rows', 0)}")
        for err in source.get("secondary_errors", []):
            print(f"  Secondary source warning: {err}")
        print("Strict sold gate: Status=Sold AND SOLD DATE required")
        print(f"CSV headers: {source.get('headers', [])}")
        for err in source.get("errors", []):
            print(f"  Source warning: {err}")
        for warning in source.get("verification_warnings", []):
            print(f"  Verification warning: {warning}")
        for comp in result.get("sold_comps", [])[:20]:
            label = (
                "VERIFIED COMP"
                if comp.get("verification") == "unit_level_closed_sale_verified"
                else "UNVERIFIED ROW"
            )
            print(
                f"  {label} Unit {comp.get('unit')} | "
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
