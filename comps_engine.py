import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

VERSION = "1.2"

CKAN_SEARCH = "https://data.wprdc.org/api/3/action/datastore_search"
ASSESSMENT_RESOURCE_ID = "65855e14-549e-4992-b5be-d629afc676fa"
SALES_RESOURCE_ID = "5bbe6c55-bce6-4edb-9d04-68edeb6bf7b1"

DEFAULT_ADDRESS = "4601 Fifth Ave #621"
DEFAULT_CITY = "Pittsburgh"
DEFAULT_STATE = "PA"
DEFAULT_ZIP = "15213"

OUTPUT_DIR = Path("COMPS_REPORTS")
TIMEOUT = 25
HEADERS = {"User-Agent": "PA-RealEstate-Intelligence-Hub-Comps/1.2"}

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
        "phase": "property_resolution_and_sales_history",
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
            result["resolution_message"] = (
                "נמצאו מספר Parcels חזקים באותה כתובת בניין, אך אין התאמת Unit "
                "נפרדת. המנוע מציג את מועמדי הבניין ואינו בוחר Parcel בניחוש."
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
    print(f"ALLEGHENY COUNTY COMPS ENGINE V{VERSION} - PHASE 1")
    print("=" * 76)
    i = result["input"]
    print(f"Input: {i['address']}, {i['city']}, {i['state']} {i['zip']}")
    print(f"Status: {result['status']}")
    print(f"Resolution type: {result['resolution_type']}")

    print("\nSearch attempts:")
    for a in result.get("search_attempts", []):
        print(f"  - {a['label']}: {a['count']} rows" + (f" | ERROR: {a['error']}" if a['error'] else ""))

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

    # unresolved remains a deliberate non-success so GitHub Actions highlights it.
    if result["status"] != "resolved":
        sys.exit(3)


if __name__ == "__main__":
    main()
