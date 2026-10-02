import json
import requests
import time
import os
from datetime import datetime, timezone, timedelta

PROPERTIES_FILE = 'properties.json'
GEOCODE_CACHE_FILE = 'geocode_cache.json'
CONFIG_FILE = 'scan_config.json'
GEOCODE_RETRY_DAYS = 30
MAX_GEOCODE_REQUESTS_PER_RUN = 20
ANALYZER_VERSION = '2.2-source-record-guard'


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_geocode_key(address, city, state="PA"):
    parts = [str(address or "").strip().lower(), str(city or "").strip().lower(), str(state or "").strip().lower()]
    return " | ".join(" ".join(p.split()) for p in parts)


def normalize_county(value):
    text = " ".join(str(value or "").casefold().replace(",", " ").split())
    if text.endswith(" county"):
        text = text[:-7].strip()
    return text


def load_selected_counties():
    """Return only the counties selected for the current scanner configuration."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        values = config.get('counties', []) if isinstance(config, dict) else []
        return {normalize_county(value) for value in values if normalize_county(value)}
    except (OSError, json.JSONDecodeError, TypeError) as e:
        print(f"⚠️ לא ניתן לקרוא את המחוזות שנבחרו; Analyzer לא יבצע עיבוד נכסים: {e}")
        return set()


def load_geocode_cache():
    if not os.path.exists(GEOCODE_CACHE_FILE):
        return {}
    try:
        with open(GEOCODE_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"⚠️ לא ניתן לקרוא Geocode Cache: {e}")
        return {}


def save_geocode_cache(cache):
    temp_file = GEOCODE_CACHE_FILE + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, GEOCODE_CACHE_FILE)


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def failed_geocode_is_fresh(entry):
    if not isinstance(entry, dict) or entry.get('status') != 'unavailable':
        return False
    attempted = parse_iso(entry.get('attempted_at'))
    return bool(attempted and datetime.now(timezone.utc) - attempted < timedelta(days=GEOCODE_RETRY_DAYS))


def get_coordinates(address, city, state="PA"):
    """מתחבר ל-Nominatim/OpenStreetMap להמרת כתובת לקואורדינטות."""
    if not address or not city:
        return None, None

    clean_addr = str(address).split(',')[0].strip()
    query = f"{clean_addr}, {city}, {state}"
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "us"}
    headers = {"User-Agent": "PA-RealEstate-Intelligence-Hub/2.0"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
        else:
            print(f"⚠️ Nominatim החזיר HTTP {resp.status_code} עבור {query}")
    except Exception as e:
        print(f"⚠️ שגיאת מיקום עבור {query}: {e}")
    return None, None



SOURCE_ONLY_ANALYSIS_FIELDS = (
    "arv", "arv_status", "arv_method", "arv_confidence", "arv_source",
    "flip_rehab", "rental_rehab", "rehab_status", "rehab_method", "rehab_confidence", "rehab_source",
    "mao_flip", "mao_flip_status", "mao_flip_method", "mao_flip_confidence",
    "monthly_rent_est", "rent_status", "rent_method", "rent_confidence", "rent_source",
    "mao_rental", "mao_rental_status", "mao_rental_method", "mao_rental_confidence",
    "neighborhood_class", "neighborhood_class_status", "neighborhood_class_method",
    "neighborhood_class_confidence", "neighborhood_class_source",
    "ai_summary", "summary_status", "summary_method",
)


def is_repository_record(prop):
    return (str(prop.get("source_type") or "").casefold() == "tax"
            and str(prop.get("tax_sale_type") or "").casefold() == "repository")


def is_public_court_record(prop):
    """Public auction/tax records are source evidence, not MLS asking prices."""
    return str(prop.get("source_type") or "").casefold() in {"sheriff", "sheriff_sale", "tax"}


def mark_public_record_source_only(prop):
    """Remove estimates derived from auction bids or court records; preserve source facts."""
    changed = False
    for field in SOURCE_ONLY_ANALYSIS_FIELDS:
        default = "not_applicable" if field.endswith("_status") else None
        if prop.get(field) != default:
            prop[field] = default
            changed = True
    if prop.get("analyzer_version") != ANALYZER_VERSION:
        prop["analyzer_version"] = ANALYZER_VERSION
        changed = True
    if prop.get("analysis_mode") != "source_record_only":
        prop["analysis_mode"] = "source_record_only"
        changed = True
    if prop.get("analysis_is_ai") is not False:
        prop["analysis_is_ai"] = False
        changed = True
    if is_repository_record(prop):
        notice = ("רשומת Repository מתוך רשימת מחוז רשמית. המקור אינו מספק נתוני בית מאומתים; "
                  "לא חושבו שווי, שכירות או שיפוץ. יש לאמת את החלקה והזמינות מול המחוז.")
        skip_reason = "repository_parcel_record_has_no_verified_building_facts"
    else:
        notice = ("רשומת מכרז/חוב מס ממקור ציבורי. סכום פסק דין, Cost & Tax Bid, הצעת פתיחה "
                  "או מינימום Repository אינם מחיר שוק. לא חושבו ARV, שכירות, שיפוץ או MAO.")
        skip_reason = "public_court_record_amount_is_not_market_price"
    if prop.get("analysis_disclaimer") != notice:
        prop["analysis_disclaimer"] = notice
        changed = True
    if prop.get("analysis_skip_reason") != skip_reason:
        prop["analysis_skip_reason"] = skip_reason
        changed = True
    # The official workbook contains legal descriptions, not geocodable street addresses.
    # Remove only coordinates known to have come from the prior OSM lookup.
    if (is_repository_record(prop) and
            (prop.get("geocode_source") == "OpenStreetMap Nominatim" or
             prop.get("geocode_status") == "verified_external_service")):
        for field in ("lat", "lng", "geocode_source", "geocode_updated_at", "geocode_last_attempt_at"):
            if field in prop:
                prop.pop(field, None)
                changed = True
        prop["geocode_status"] = "not_geocoded_repository_description"
        changed = True
    return changed

def positive_number(value):
    """מחזיר מספר חיובי, או None אם הערך חסר/לא תקין."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(',', '').replace('$', '').strip())
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def calculate_metrics(prop):
    """
    מחשב אומדנים ראשוניים בלבד.
    אין כאן AI ואין המצאת Price/SqFt/Beds כאשר נתון חסר.
    """
    price = positive_number(prop.get('price'))
    sqft = positive_number(prop.get('sqft'))
    beds = positive_number(prop.get('beds'))
    city = prop.get('city') or 'Unknown'

    metrics = {
        "analyzer_version": ANALYZER_VERSION,
        "analysis_updated_at": utc_now_iso(),
        "analysis_mode": "preliminary_estimate",
        "analysis_is_ai": False,
        "analysis_disclaimer": "אומדן ראשוני בלבד. אינו תחליף ל-Comps, Rental Comps, בדיקת שיפוץ או בדיקת שכונה מאומתת.",
    }

    if price is not None:
        arv = int(price * 1.6) if price < 100000 else int(price * 1.35)
        metrics.update({
            "arv": arv, "arv_status": "estimated",
            "arv_method": "rule_of_thumb_price_multiplier",
            "arv_confidence": "low",
            "arv_source": "calculated_from_listing_price",
        })
    else:
        arv = None
        metrics.update({
            "arv": None, "arv_status": "unavailable", "arv_method": None,
            "arv_confidence": "none", "arv_source": None,
        })

    if sqft is not None:
        flip_rehab = int(sqft * 45)
        rental_rehab = int(sqft * 25)
        metrics.update({
            "flip_rehab": flip_rehab, "rental_rehab": rental_rehab,
            "rehab_status": "estimated", "rehab_method": "sqft_rule_of_thumb",
            "rehab_confidence": "low", "rehab_source": "calculated_from_sqft",
        })
    else:
        flip_rehab = rental_rehab = None
        metrics.update({
            "flip_rehab": None, "rental_rehab": None,
            "rehab_status": "unavailable", "rehab_method": None,
            "rehab_confidence": "none", "rehab_source": None,
        })

    if arv is not None and flip_rehab is not None:
        mao_flip = int((arv * 0.70) - flip_rehab)
        metrics.update({
            "mao_flip": mao_flip, "mao_flip_status": "calculated_from_estimates",
            "mao_flip_method": "70_percent_rule", "mao_flip_confidence": "low",
        })
    else:
        mao_flip = None
        metrics.update({
            "mao_flip": None, "mao_flip_status": "unavailable",
            "mao_flip_method": None, "mao_flip_confidence": "none",
        })

    if beds is not None:
        monthly_rent = int(950 + (beds * 180))
        metrics.update({
            "monthly_rent_est": monthly_rent, "rent_status": "estimated",
            "rent_method": "bedroom_rule_of_thumb", "rent_confidence": "low",
            "rent_source": "calculated_from_bedrooms",
        })
    else:
        monthly_rent = None
        metrics.update({
            "monthly_rent_est": None, "rent_status": "unavailable",
            "rent_method": None, "rent_confidence": "none", "rent_source": None,
        })

    if monthly_rent is not None and rental_rehab is not None:
        annual_rent = monthly_rent * 12
        noi = annual_rent * 0.74
        mao_rental = int((noi / 0.10) - rental_rehab - 5000)
        metrics.update({
            "mao_rental": mao_rental,
            "mao_rental_status": "calculated_from_estimates",
            "mao_rental_method": "10_percent_cap_rate_with_26_percent_expense_assumption",
            "mao_rental_confidence": "low",
        })
    else:
        mao_rental = None
        metrics.update({
            "mao_rental": None, "mao_rental_status": "unavailable",
            "mao_rental_method": None, "mao_rental_confidence": "none",
        })

    # נשמר זמנית לתאימות לממשק הישן, אבל מסומן במפורש כסימולציה.
    if price is not None:
        if price < 60000:
            hood_class = "C-"
        elif price < 90000:
            hood_class = "C+"
        elif price < 150000:
            hood_class = "B"
        else:
            hood_class = "A-"
        metrics.update({
            "neighborhood_class": hood_class,
            "neighborhood_class_status": "simulated",
            "neighborhood_class_method": "listing_price_bucket",
            "neighborhood_class_confidence": "very_low",
            "neighborhood_class_source": "not_verified_neighborhood_data",
        })
    else:
        hood_class = None
        metrics.update({
            "neighborhood_class": None,
            "neighborhood_class_status": "unavailable",
            "neighborhood_class_method": None,
            "neighborhood_class_confidence": "none",
            "neighborhood_class_source": None,
        })

    summary_parts = [f"נכס ב-{city}."]
    if arv is not None:
        summary_parts.append(f"ARV ראשוני משוער: ${arv:,} (Rule of Thumb בלבד).")
    else:
        summary_parts.append("ARV: אין מספיק נתונים לחישוב.")

    if mao_rental is not None:
        summary_parts.append(f"MAO שכירות ראשוני: ${mao_rental:,}, על בסיס אומדן שכירות והנחות הוצאה קבועות.")
    else:
        summary_parts.append("MAO שכירות: אין מספיק נתונים לחישוב.")

    if mao_flip is not None:
        summary_parts.append(f"MAO פליפ ראשוני: ${mao_flip:,}, לפי כלל 70% ואומדן שיפוץ לפי שטח.")
    else:
        summary_parts.append("MAO פליפ: אין מספיק נתונים לחישוב.")

    if hood_class is not None:
        summary_parts.append(f"דירוג השכונה {hood_class} הוא סימולציה זמנית לפי מחיר הנכס ואינו דירוג שכונה מאומת.")

    summary_parts.append("יש לאמת Comps, שכירות, מצב הנכס והשכונה לפני החלטת השקעה.")

    metrics.update({
        # שם השדה נשמר כדי לא לשבור את index.html הקיים.
        "ai_summary": " ".join(summary_parts),
        "summary_status": "rule_based_not_ai",
        "summary_method": "deterministic_template",
    })
    return metrics


def needs_analysis(prop):
    """מריץ מחדש ניתוח ישן כדי להוסיף Metadata של מקור ואמינות."""
    return (
        prop.get('analyzer_version') != ANALYZER_VERSION
        or 'analysis_mode' not in prop
        or 'mao_flip' not in prop
    )


def run_analyzer():
    print(f"🧠 מתחיל Analyzer V{ANALYZER_VERSION} — אומדנים שקופים, ללא AI...")

    if not os.path.exists(PROPERTIES_FILE):
        print("❌ קובץ הנכסים לא נמצא.")
        return

    with open(PROPERTIES_FILE, 'r', encoding='utf-8') as f:
        properties = json.load(f)

    if not isinstance(properties, list):
        print("❌ properties.json אינו מכיל רשימת נכסים.")
        return

    selected_counties = load_selected_counties()
    print(f"🎯 Analyzer מוגבל למחוזות שנבחרו: {sorted(selected_counties)}")
    if not selected_counties:
        print("⏭️ לא נבחרו מחוזות תקינים; אין עיבוד נכסים ולא נשלחות בקשות מיקום.")
        return

    updated_properties = []
    analyzed_count = 0
    geocoded_count = 0
    geocode_cache_hits = 0
    geocode_skipped_failed = 0
    geocode_requests = 0
    geocode_batch_skipped = 0
    outside_counties_skipped = 0
    source_only_count = 0
    source_records_cleaned = 0
    cache = load_geocode_cache()
    cache_changed = False

    for prop in properties:
        if not isinstance(prop, dict):
            updated_properties.append(prop)
            continue

        if is_public_court_record(prop):
            if mark_public_record_source_only(prop):
                source_records_cleaned += 1
            source_only_count += 1
            updated_properties.append(prop)
            continue

        county_key = normalize_county(prop.get('county'))
        if not county_key or county_key not in selected_counties:
            updated_properties.append(prop)
            outside_counties_skipped += 1
            continue

        # Geocoding:
        # 1) existing coordinates -> no request
        # 2) persistent cache hit -> no request
        # 3) recent failed lookup -> no repeated request for 30 days
        # 4) only selected counties are eligible; cap live calls at 20 per run
        lat = positive_number(prop.get('lat'))
        try:
            lng = float(prop.get('lng')) if prop.get('lng') is not None else None
        except (TypeError, ValueError):
            lng = None

        address = prop.get('address')
        city = prop.get('city')
        if (lat is None or lng is None) and address and city:
            key = normalize_geocode_key(address, city)
            cached = cache.get(key)

            if isinstance(cached, dict) and positive_number(cached.get('lat')) is not None and cached.get('lng') is not None:
                prop['lat'] = float(cached['lat'])
                prop['lng'] = float(cached['lng'])
                prop['geocode_status'] = 'verified_external_service'
                prop['geocode_source'] = cached.get('source') or 'OpenStreetMap Nominatim'
                prop['geocode_updated_at'] = cached.get('attempted_at') or utc_now_iso()
                geocode_cache_hits += 1

            elif failed_geocode_is_fresh(cached):
                prop['geocode_status'] = 'unavailable'
                prop['geocode_last_attempt_at'] = cached.get('attempted_at')
                geocode_skipped_failed += 1

            elif prop.get('geocode_status') == 'unavailable' and not prop.get('geocode_last_attempt_at'):
                # Migration of failures from Analyzer V2.0: do not hammer Nominatim again now.
                attempted_at = utc_now_iso()
                prop['geocode_last_attempt_at'] = attempted_at
                cache[key] = {'status': 'unavailable', 'attempted_at': attempted_at}
                cache_changed = True
                geocode_skipped_failed += 1

            elif geocode_requests >= MAX_GEOCODE_REQUESTS_PER_RUN:
                geocode_batch_skipped += 1

            else:
                print(f"📍 מאתר קואורדינטות: {address}...")
                geocode_requests += 1
                new_lat, new_lng = get_coordinates(address, city)
                attempted_at = utc_now_iso()

                if new_lat is not None and new_lng is not None:
                    prop['lat'] = new_lat
                    prop['lng'] = new_lng
                    prop['geocode_status'] = 'verified_external_service'
                    prop['geocode_source'] = 'OpenStreetMap Nominatim'
                    prop['geocode_updated_at'] = attempted_at
                    prop['geocode_last_attempt_at'] = attempted_at
                    cache[key] = {
                        'status': 'verified',
                        'lat': new_lat,
                        'lng': new_lng,
                        'source': 'OpenStreetMap Nominatim',
                        'attempted_at': attempted_at,
                    }
                    geocoded_count += 1
                else:
                    prop['geocode_status'] = 'unavailable'
                    prop['geocode_last_attempt_at'] = attempted_at
                    cache[key] = {'status': 'unavailable', 'attempted_at': attempted_at}

                cache_changed = True
                time.sleep(1.1)

        if needs_analysis(prop):
            print(f"📊 מחשב אומדנים: {prop.get('address', 'Unknown')}...")
            prop.update(calculate_metrics(prop))
            analyzed_count += 1

        updated_properties.append(prop)

    if cache_changed:
        save_geocode_cache(cache)

    temp_file = PROPERTIES_FILE + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(updated_properties, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, PROPERTIES_FILE)

    print(
        f"✅ Analyzer V{ANALYZER_VERSION} סיים: "
        f"{analyzed_count} נכסים נותחו/עודכנו, "
        f"{geocoded_count} נכסים קיבלו קואורדינטות."
    )
    print(f"🧾 מכרזים/חובות מס: {source_only_count} רשומות הושארו כרשומות מקור בלבד; "
          f"{source_records_cleaned} נוקו מאומדני Analyzer לא מתאימים.")
    print(
        f"🗺️ Geocode QA — בקשות חיצוניות: {geocode_requests}/{MAX_GEOCODE_REQUESTS_PER_RUN} | "
        f"Cache hits: {geocode_cache_hits} | "
        f"דילוג על כשלונות טריים: {geocode_skipped_failed} | "
        f"ממתינים לסבב הבא: {geocode_batch_skipped} | "
        f"מחוץ למחוזות שנבחרו: {outside_counties_skipped}"
    )
    print("ℹ️ ARV, Rent, Rehab ו-Neighborhood Class עדיין אומדנים זמניים ולא נתונים מאומתים.")


if __name__ == '__main__':
    run_analyzer()
