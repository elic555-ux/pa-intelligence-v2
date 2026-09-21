import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============================================================
# PA Real Estate Intelligence Hub
# Allegheny County Comps Engine - Phase 1
#
# מטרה בשלב זה:
# 1. לזהות Parcel/Unit לפי כתובת.
# 2. להחזיר נתוני Assessment רשמיים.
# 3. להחזיר Sales History רשמי עבור ה-Parcel.
# 4. לשמור JSON שקוף עם מקור לכל נתון.
#
# בשלב זה בכוונה:
# - לא בוחר Sold Comps.
# - לא מחשב ARV.
# - לא משנה properties.json / analyzer.py / orchestrator.py.
# ============================================================

VERSION = "1.0"

CKAN_BASE = "https://data.wprdc.org/api/3/action/datastore_search"
ASSESSMENT_RESOURCE_ID = "65855e14-549e-4992-b5be-d629afc676fa"
SALES_RESOURCE_ID = "5bbe6c55-bce6-4edb-9d04-68edeb6bf7b1"

DEFAULT_ADDRESS = "4601 Fifth Ave #621"
DEFAULT_CITY = "Pittsburgh"
DEFAULT_STATE = "PA"
DEFAULT_ZIP = "15213"

OUTPUT_DIR = Path("COMPS_REPORTS")
TIMEOUT = 20

HEADERS = {
    "User-Agent": "PA-RealEstate-Intelligence-Hub-Comps/1.0"
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def normalize(value):
    value = clean_text(value).upper()
    value = value.replace(".", "")
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_street(value):
    text = normalize(value)
    replacements = {
        " AVENUE": " AVE",
        " STREET": " ST",
        " ROAD": " RD",
        " DRIVE": " DR",
        " LANE": " LN",
        " BOULEVARD": " BLVD",
        " COURT": " CT",
        " PLACE": " PL",
        " HIGHWAY": " HWY",
        " PARKWAY": " PKWY",
        " TERRACE": " TER",
    }
    for old, new in replacements.items():
        if text.endswith(old):
            text = text[:-len(old)] + new
    return text


def parse_address(address):
    """
    מפרק כתובת בסיסית:
    4601 Fifth Ave #621
    4601 Fifth Ave Unit 621
    4601 Fifth Ave Apt 621
    """
    raw = clean_text(address)

    unit = ""
    unit_match = re.search(
        r"(?:#|UNIT\s+|APT\s+|APARTMENT\s+|SUITE\s+)([A-Za-z0-9\-]+)\s*$",
        raw,
        flags=re.IGNORECASE,
    )
    if unit_match:
        unit = clean_text(unit_match.group(1))
        raw = raw[:unit_match.start()].strip(" ,")

    match = re.match(r"^\s*(\d+[A-Za-z]?)\s+(.+?)\s*$", raw)
    if not match:
        raise ValueError(
            "לא הצלחתי לפרק את הכתובת. השתמש בפורמט כגון: "
            "'4601 Fifth Ave #621'"
        )

    house = match.group(1)
    street = match.group(2)

    return {
        "house_number": house,
        "street": street,
        "unit": unit,
    }


def ckan_search(resource_id, filters=None, q=None, limit=100):
    params = {
        "resource_id": resource_id,
        "limit": limit,
    }

    if filters:
        params["filters"] = json.dumps(filters, separators=(",", ":"))

    if q:
        params["q"] = q

    response = requests.get(
        CKAN_BASE,
        params=params,
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError("WPRDC API החזיר success=false")

    result = payload.get("result") or {}
    return result.get("records") or []


def assessment_candidates(house_number, city, zip_code):
    """
    חיפוש ראשוני רחב יחסית לפי מספר בית + עיר.
    ZIP נוסף כאשר הוא קיים.
    """
    filters = {
        "PROPERTYHOUSENUM": str(house_number),
        "PROPERTYCITY": city,
    }
    if zip_code:
        filters["PROPERTYZIP"] = str(zip_code)

    records = ckan_search(
        ASSESSMENT_RESOURCE_ID,
        filters=filters,
        limit=500,
    )

    # Fallback: אם פורמט ZIP/City במאגר שונה, ננסה מספר בית בלבד
    # ואז נסנן מקומית.
    if not records:
        records = ckan_search(
            ASSESSMENT_RESOURCE_ID,
            filters={"PROPERTYHOUSENUM": str(house_number)},
            limit=500,
        )

    return records


def score_candidate(record, target):
    score = 0
    reasons = []

    rec_house = normalize(record.get("PROPERTYHOUSENUM"))
    rec_street = normalize_street(record.get("PROPERTYADDRESS"))
    rec_city = normalize(record.get("PROPERTYCITY"))
    rec_zip = normalize(record.get("PROPERTYZIP"))
    rec_unit = normalize(record.get("PROPERTYUNIT"))

    target_house = normalize(target["house_number"])
    target_street = normalize_street(target["street"])
    target_city = normalize(target["city"])
    target_zip = normalize(target["zip"])
    target_unit = normalize(target["unit"])

    if rec_house == target_house:
        score += 30
        reasons.append("house_number_exact")

    if rec_street == target_street:
        score += 40
        reasons.append("street_exact")
    elif target_street and (
        target_street in rec_street or rec_street in target_street
    ):
        score += 25
        reasons.append("street_partial")

    if target_city and rec_city == target_city:
        score += 10
        reasons.append("city_exact")

    if target_zip and rec_zip == target_zip:
        score += 10
        reasons.append("zip_exact")

    if target_unit:
        if rec_unit == target_unit:
            score += 50
            reasons.append("unit_exact")
        elif rec_unit:
            score -= 20
            reasons.append("unit_mismatch")
        else:
            score -= 10
            reasons.append("unit_missing_in_county_record")

    return score, reasons


def resolve_property(address, city, state, zip_code):
    parsed = parse_address(address)

    target = {
        **parsed,
        "city": clean_text(city),
        "state": clean_text(state),
        "zip": clean_text(zip_code),
    }

    candidates = assessment_candidates(
        target["house_number"],
        target["city"],
        target["zip"],
    )

    scored = []
    for record in candidates:
        score, reasons = score_candidate(record, target)
        if score > 0:
            scored.append({
                "score": score,
                "reasons": reasons,
                "record": record,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)

    if not scored:
        return target, None, []

    best = scored[0]

    # דורשים התאמה חזקה. ביחידה/Condo חשוב במיוחד לא לקבל Parcel שגוי.
    if best["score"] < 70:
        return target, None, scored[:10]

    return target, best, scored[:10]


def sales_history_for_parcel(parcel_id):
    if not parcel_id:
        return []

    records = ckan_search(
        SALES_RESOURCE_ID,
        filters={"PARID": str(parcel_id)},
        limit=500,
    )

    def date_key(record):
        return clean_text(record.get("SALEDATE"))

    return sorted(records, key=date_key, reverse=True)


def assessment_public_view(record):
    if not record:
        return None

    fields = [
        "PARID",
        "PROPERTYHOUSENUM",
        "PROPERTYFRACTION",
        "PROPERTYADDRESS",
        "PROPERTYCITY",
        "PROPERTYSTATE",
        "PROPERTYUNIT",
        "PROPERTYZIP",
        "MUNIDESC",
        "SCHOOLDESC",
        "NEIGHCODE",
        "NEIGHDESC",
        "CLASS",
        "CLASSDESC",
        "USECODE",
        "USEDESC",
        "SALEDATE",
        "SALEPRICE",
        "SALECODE",
        "SALEDESC",
        "PREVSALEDATE",
        "PREVSALEPRICE",
        "PREVSALEDATE2",
        "PREVSALEPRICE2",
        "COUNTYBUILDING",
        "COUNTYLAND",
        "COUNTYTOTAL",
        "FAIRMARKETBUILDING",
        "FAIRMARKETLAND",
        "FAIRMARKETTOTAL",
        "STYLE",
        "STYLEDESC",
        "STORIES",
        "YEARBLT",
        "GRADE",
        "GRADEDESC",
        "CONDITION",
        "CONDITIONDESC",
        "TOTALROOMS",
        "BEDROOMS",
        "FULLBATHS",
        "HALFBATHS",
        "FINISHEDLIVINGAREA",
        "TAXYEAR",
        "ASOFDATE",
    ]
    return {field: record.get(field) for field in fields}


def sale_public_view(record):
    fields = [
        "PARID",
        "FULL_ADDRESS",
        "PROPERTYHOUSENUM",
        "PROPERTYADDRESSDIR",
        "PROPERTYADDRESSSTREET",
        "PROPERTYADDRESSSUF",
        "PROPERTYADDRESSUNITDESC",
        "PROPERTYUNITNO",
        "PROPERTYCITY",
        "PROPERTYSTATE",
        "PROPERTYZIP",
        "MUNIDESC",
        "RECORDDATE",
        "SALEDATE",
        "PRICE",
        "SALECODE",
        "SALEDESC",
        "DEEDBOOK",
        "DEEDPAGE",
        "INSTRTYP",
        "INSTRTYPDESC",
    ]
    return {field: record.get(field) for field in fields}


def build_result(address, city, state, zip_code):
    target, best, alternatives = resolve_property(
        address, city, state, zip_code
    )

    result = {
        "engine": "Allegheny County Comps Engine",
        "version": VERSION,
        "phase": "property_resolution_and_sales_history",
        "generated_at": utc_now(),
        "input": {
            "address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
        },
        "parsed_input": target,
        "status": "not_found",
        "arv": None,
        "arv_status": "not_calculated_phase_1",
        "comps": [],
        "comps_status": "not_selected_phase_1",
        "sources": {
            "assessment": {
                "publisher": "Allegheny County / WPRDC",
                "resource_id": ASSESSMENT_RESOURCE_ID,
                "data_type": "official_county_assessment",
            },
            "sales": {
                "publisher": "Allegheny County / WPRDC",
                "resource_id": SALES_RESOURCE_ID,
                "data_type": "official_county_sales_transactions",
                "validation_note": (
                    "SALECODE/SALEDESC must be evaluated before a sale "
                    "is used as a market comparable."
                ),
            },
        },
    }

    if not best:
        result["resolution"] = {
            "matched": False,
            "message": (
                "לא נמצאה התאמת Parcel חזקה מספיק. "
                "לא בוצע ניחוש אוטומטי."
            ),
            "candidate_count": len(alternatives),
            "top_candidates": [
                {
                    "score": item["score"],
                    "reasons": item["reasons"],
                    "PARID": item["record"].get("PARID"),
                    "PROPERTYADDRESS": item["record"].get("PROPERTYADDRESS"),
                    "PROPERTYUNIT": item["record"].get("PROPERTYUNIT"),
                    "PROPERTYCITY": item["record"].get("PROPERTYCITY"),
                    "PROPERTYZIP": item["record"].get("PROPERTYZIP"),
                }
                for item in alternatives
            ],
        }
        return result

    record = best["record"]
    parcel_id = record.get("PARID")
    sales = sales_history_for_parcel(parcel_id)

    result["status"] = "resolved"
    result["resolution"] = {
        "matched": True,
        "score": best["score"],
        "reasons": best["reasons"],
        "parcel_id": parcel_id,
        "unit": record.get("PROPERTYUNIT"),
        "county_address": " ".join(
            part for part in [
                clean_text(record.get("PROPERTYHOUSENUM")),
                clean_text(record.get("PROPERTYADDRESS")),
            ] if part
        ),
    }
    result["assessment"] = assessment_public_view(record)
    result["sales_history"] = [sale_public_view(x) for x in sales]
    result["sales_history_count"] = len(sales)

    return result


def safe_filename(address):
    name = normalize(address).replace(" ", "_")
    return re.sub(r"[^A-Z0-9_\-]", "", name) or "PROPERTY"


def print_summary(result):
    print("\n" + "=" * 72)
    print("ALLEGHENY COUNTY COMPS ENGINE - PHASE 1")
    print("=" * 72)
    print(f"Input: {result['input']['address']}, "
          f"{result['input']['city']}, {result['input']['state']} "
          f"{result['input']['zip']}")
    print(f"Status: {result['status']}")

    resolution = result.get("resolution") or {}
    if not resolution.get("matched"):
        print("❌ Parcel לא זוהה בוודאות מספקת.")
        for item in resolution.get("top_candidates", [])[:5]:
            print(
                f"  Candidate: PARID={item.get('PARID')} | "
                f"Unit={item.get('PROPERTYUNIT')} | "
                f"Score={item.get('score')}"
            )
        print("לא חושב ARV ולא נבחרו Comps.")
        return

    assessment = result.get("assessment") or {}
    print(f"✅ PARID: {resolution.get('parcel_id')}")
    print(f"County Unit: {resolution.get('unit')}")
    print(f"Use: {assessment.get('USEDESC')}")
    print(f"Beds: {assessment.get('BEDROOMS')}")
    print(f"Full Baths: {assessment.get('FULLBATHS')}")
    print(f"Half Baths: {assessment.get('HALFBATHS')}")
    print(f"Living Area: {assessment.get('FINISHEDLIVINGAREA')}")
    print(f"Year Built: {assessment.get('YEARBLT')}")
    print(f"Neighborhood: {assessment.get('NEIGHDESC')}")
    print(f"Sales history rows: {result.get('sales_history_count', 0)}")

    for sale in result.get("sales_history", [])[:10]:
        print(
            "  • "
            f"{sale.get('SALEDATE')} | "
            f"${sale.get('PRICE')} | "
            f"Code={sale.get('SALECODE')} | "
            f"{sale.get('SALEDESC')}"
        )

    print("\nℹ️ Phase 1: ARV לא מחושב ו-Comps עדיין לא נבחרים.")


def main():
    parser = argparse.ArgumentParser(
        description="Allegheny County property resolver and sales-history tester."
    )
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--city", default=DEFAULT_CITY)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--zip", dest="zip_code", default=DEFAULT_ZIP)
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Folder for JSON test reports.",
    )
    args = parser.parse_args()

    try:
        result = build_result(
            args.address,
            args.city,
            args.state,
            args.zip_code,
        )
    except requests.RequestException as exc:
        print(f"❌ שגיאת תקשורת מול WPRDC: {exc}")
        sys.exit(2)
    except Exception as exc:
        print(f"❌ שגיאה: {exc}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / (
        f"{safe_filename(args.address)}_county_test.json"
    )
    temp_file = output_file.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    temp_file.replace(output_file)

    print_summary(result)
    print(f"\n📄 JSON נשמר: {output_file}")

    if result["status"] != "resolved":
        sys.exit(3)


if __name__ == "__main__":
    main()
