import json
import requests
import time
import os
import math
import re
from datetime import datetime, timezone, timedelta

PROPERTIES_FILE = 'properties.json'
GEOCODE_CACHE_FILE = 'geocode_cache.json'
CONFIG_FILE = 'scan_config.json'
GEOCODE_RETRY_DAYS = 30
MAX_GEOCODE_REQUESTS_PER_RUN = 20
ANALYZER_VERSION = '2.3-consistent-financials-20261006'


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
    "projected_rent", "gross_yield", "gross_yield_pct", "rehab_scope", "rent_unavailable_reason",
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
    source_type = str(prop.get("source_type") or "").lower()
    deal_type = str(prop.get("deal_type") or "").lower()
    return ('sheriff' in source_type or source_type == 'tax' or 'sheriff' in deal_type
            or any(token in deal_type for token in ('tax', 'פיגורי מס', 'חוב מס')))


def mark_public_record_source_only(prop):
    """Remove estimates derived from auction bids or court records; preserve source facts."""
    changed = False
    verified_neighborhood = (prop.get('neighborhood_class_status') == 'verified'
                             and prop.get('neighborhood_class_source') and prop.get('neighborhood_class'))
    for field in SOURCE_ONLY_ANALYSIS_FIELDS:
        if field.startswith('neighborhood_class') and verified_neighborhood:
            continue
        default = "not_applicable" if field.endswith("_status") else None
        if field not in prop or prop.get(field) != default:
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
    current_inputs = financial_inputs(prop)
    if prop.get('analysis_inputs') != current_inputs or prop.get('financial_model_version') != FINANCIAL_MODEL_VERSION:
        prop['analysis_inputs'] = current_inputs
        prop['financial_model_version'] = FINANCIAL_MODEL_VERSION
        changed = True
    if changed:
        prop['analysis_updated_at'] = utc_now_iso()
    return changed

FINANCIAL_MODEL_VERSION = 'financial-baseline-v1-20261006'


def numeric_financial_value(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(',', '').replace('$', '').strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def positive_number(value):
    number = numeric_financial_value(value)
    return number if number is not None and number > 0 else None


def financial_inputs(prop):
    return {
        'model_version': FINANCIAL_MODEL_VERSION,
        'price': None if is_public_court_record(prop) else positive_number(prop.get('price')),
        'beds': positive_number(prop.get('beds')),
        'sqft': positive_number(prop.get('sqft')),
        'property_type': str(prop.get('property_type') or prop.get('type') or '').strip(),
        'source_type': str(prop.get('source_type') or '').lower(),
        'deal_type': str(prop.get('deal_type') or '').lower(),
        'verified_monthly_rent': positive_number(prop.get('verified_monthly_rent')),
        'verified_rent_source': str(prop.get('verified_rent_source') or '').strip(),
        'verified_arv': positive_number(prop.get('verified_arv')),
        'verified_arv_source': str(prop.get('verified_arv_source') or '').strip(),
        'verified_flip_rehab': numeric_financial_value(prop.get('verified_flip_rehab')),
        'verified_rental_rehab': numeric_financial_value(prop.get('verified_rental_rehab')),
        'verified_rehab_source': str(prop.get('verified_rehab_source') or '').strip(),
    }


def format_financial_money(number):
    if number is None:
        return 'אין נתון'
    # Match the display precision of moneyData() in the browser.
    return '$' + f'{number:,.3f}'.rstrip('0').rstrip('.')


def calculate_rental_metrics(prop):
    """Shared by the scanner and analyzer; never infer rent from listing price."""
    inputs = financial_inputs(prop)
    source_only = is_public_court_record(prop)
    multi_unit = bool(re.search(r'multi|duplex|triplex|quadplex|apartment|two family|two-family|2-4',
                               inputs['property_type'].lower()))
    verified = inputs['verified_monthly_rent'] is not None and bool(inputs['verified_rent_source'])
    rent = (inputs['verified_monthly_rent'] if verified else
            int(950 + inputs['beds'] * 180) if inputs['beds'] is not None and not multi_unit else None)
    if source_only:
        rent = None
    gross_yield = (math.floor((rent * 1200 / inputs['price']) * 10 + 0.5) / 10
                   if rent is not None and inputs['price'] is not None else None)
    method = ('verified_source_input' if verified else 'bedroom_rule_of_thumb') if rent is not None else None
    return {
        'financial_model_version': FINANCIAL_MODEL_VERSION,
        'monthly_rent_est': rent,
        'projected_rent': format_financial_money(rent) + ' / חודש' if rent is not None else None,
        'gross_yield_pct': gross_yield,
        'gross_yield': f'{gross_yield:.1f}% ברוטו' if gross_yield is not None else None,
        'rent_status': 'not_applicable' if source_only else 'source_input' if verified else 'estimated' if rent is not None else 'unavailable',
        'rent_method': method,
        'rent_confidence': 'source_supplied' if rent is not None and verified else 'low' if rent is not None else 'none',
        'rent_source': inputs['verified_rent_source'] if rent is not None and verified else 'calculated_from_bedrooms' if rent is not None else None,
        'rent_unavailable_reason': ('public_court_record' if source_only else
                                    'multi_unit_requires_unit_rents' if multi_unit and not verified else
                                    'missing_bedrooms' if rent is None else None),
    }


def calculate_metrics(prop):
    """Transparent scenarios based on the current inputs; no fabricated neighborhood grade."""
    if is_public_court_record(prop):
        clean = dict(prop)
        mark_public_record_source_only(clean)
        fields = (*SOURCE_ONLY_ANALYSIS_FIELDS, 'analyzer_version', 'financial_model_version',
                  'analysis_inputs', 'analysis_updated_at', 'analysis_mode', 'analysis_is_ai',
                  'analysis_disclaimer', 'analysis_skip_reason')
        return {key: clean.get(key) for key in fields}
    inputs = financial_inputs(prop)
    metrics = calculate_rental_metrics(prop)
    price, sqft, rent = inputs['price'], inputs['sqft'], metrics['monthly_rent_est']
    verified_arv = inputs['verified_arv'] is not None and bool(inputs['verified_arv_source'])
    arv = (inputs['verified_arv'] if verified_arv else
           int(price * (1.6 if price < 100000 else 1.35)) if price is not None else None)
    verified_rehab = bool(inputs['verified_rehab_source'])
    flip = (inputs['verified_flip_rehab'] if verified_rehab and inputs['verified_flip_rehab'] is not None
            and inputs['verified_flip_rehab'] >= 0 else int(sqft * 45) if sqft is not None else None)
    rental = (inputs['verified_rental_rehab'] if verified_rehab and inputs['verified_rental_rehab'] is not None
              and inputs['verified_rental_rehab'] >= 0 else int(sqft * 25) if sqft is not None else None)
    mao_flip = int(arv * 0.70 - flip) if arv is not None and flip is not None else None
    mao_rental = int(rent * 12 * 0.74 / 0.10 - rental - 5000) if rent is not None and rental is not None else None
    metrics.update({
        'analyzer_version': ANALYZER_VERSION, 'analysis_updated_at': utc_now_iso(),
        'analysis_inputs': inputs, 'analysis_mode': 'preliminary_estimate', 'analysis_is_ai': False,
        'analysis_skip_reason': None,
        'analysis_disclaimer': 'חישוב לפי נתוני הנכס והנחות מפורשות; נתונים ממקור מזוהה מוצגים בנפרד מתרחישים.',
        'arv': arv, 'arv_status': 'source_input' if verified_arv else 'scenario' if arv is not None else 'unavailable',
        'arv_method': 'verified_source_input' if verified_arv else 'asking_price_scenario' if arv is not None else None,
        'arv_confidence': 'source_supplied' if verified_arv else 'low' if arv is not None else 'none',
        'arv_source': inputs['verified_arv_source'] if verified_arv else 'calculated_from_listing_price' if arv is not None else None,
        'flip_rehab': flip, 'rental_rehab': rental,
        'rehab_status': 'source_input_or_estimated' if verified_rehab else 'estimated' if flip is not None else 'unavailable',
        'rehab_method': 'source_input_or_sqft_rule' if verified_rehab else 'sqft_rule_of_thumb',
        'rehab_confidence': 'low' if flip is not None or rental is not None else 'none',
        'rehab_source': inputs['verified_rehab_source'] if verified_rehab else 'calculated_from_sqft' if sqft is not None else None,
        'rehab_scope': 'תקציב שיפוץ משוער: ' + format_financial_money(flip if prop.get('strategy') == 'value_add' else rental),
        'mao_flip': mao_flip, 'mao_flip_status': 'calculated_from_estimates' if mao_flip is not None else 'unavailable',
        'mao_flip_method': '70_percent_rule' if mao_flip is not None else None,
        'mao_flip_confidence': 'low' if mao_flip is not None else 'none',
        'mao_rental': mao_rental, 'mao_rental_status': 'calculated_from_estimates' if mao_rental is not None else 'unavailable',
        'mao_rental_method': '10_percent_cap_rate_with_26_percent_expense_assumption' if mao_rental is not None else None,
        'mao_rental_confidence': 'low' if mao_rental is not None else 'none',
        'summary_status': 'rule_based_not_ai', 'summary_method': 'deterministic_template',
        'ai_summary': f"שכירות: {metrics['projected_rent'] or 'אין נתון'}. תשואה: {metrics['gross_yield'] or 'אין נתון'}. "
                      f"MAO להשכרה (תרחיש): {format_financial_money(mao_rental)}. MAO לפליפ (תרחיש): {format_financial_money(mao_flip)}.",
    })
    if prop.get('strategy') in ('turnkey', 'value_add'):
        metrics['strategy_label'] = 'בחינה להשכרה' if prop['strategy'] == 'turnkey' else 'בחינה להשבחה'
    if not (prop.get('neighborhood_class_status') == 'verified' and prop.get('neighborhood_class_source') and prop.get('neighborhood_class')):
        metrics.update({'neighborhood_class': None, 'neighborhood_class_status': 'unavailable',
                        'neighborhood_class_method': None, 'neighborhood_class_confidence': 'none', 'neighborhood_class_source': None})
    return metrics


def needs_analysis(prop):
    """A changed price, size, bedrooms or sourced input invalidates cached calculations."""
    return (prop.get('analyzer_version') != ANALYZER_VERSION
            or prop.get('financial_model_version') != FINANCIAL_MODEL_VERSION
            or prop.get('analysis_inputs') != financial_inputs(prop)
            or prop.get('analysis_mode') != ('source_record_only' if is_public_court_record(prop) else 'preliminary_estimate')
            or any(field not in prop for field in ('mao_flip', 'mao_rental', 'monthly_rent_est', 'gross_yield_pct')))


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
    print("ℹ️ שכירות, תשואה ותרחישי שווי/MAO חושבו באותה נוסחה; דירוג שכונה אינו נגזר ממחיר.")


if __name__ == '__main__':
    run_analyzer()
