import json
import requests
import time
import os
from datetime import datetime, timezone

PROPERTIES_FILE = 'properties.json'
ANALYZER_VERSION = '2.0'


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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

    updated_properties = []
    analyzed_count = 0
    geocoded_count = 0

    for prop in properties:
        if not isinstance(prop, dict):
            updated_properties.append(prop)
            continue

        # לא פונים ל-Nominatim אם כבר קיימות קואורדינטות תקינות.
        lat = positive_number(prop.get('lat'))
        try:
            lng = float(prop.get('lng')) if prop.get('lng') is not None else None
        except (TypeError, ValueError):
            lng = None

        if lat is None or lng is None:
            if prop.get('address') and prop.get('city'):
                print(f"📍 מאתר קואורדינטות: {prop.get('address', 'Unknown')}...")
                new_lat, new_lng = get_coordinates(prop.get('address'), prop.get('city'))
                if new_lat is not None and new_lng is not None:
                    prop['lat'] = new_lat
                    prop['lng'] = new_lng
                    prop['geocode_status'] = 'verified_external_service'
                    prop['geocode_source'] = 'OpenStreetMap Nominatim'
                    prop['geocode_updated_at'] = utc_now_iso()
                    geocoded_count += 1
                else:
                    prop['geocode_status'] = 'unavailable'
                time.sleep(1.1)

        if needs_analysis(prop):
            print(f"📊 מחשב אומדנים: {prop.get('address', 'Unknown')}...")
            prop.update(calculate_metrics(prop))
            analyzed_count += 1

        updated_properties.append(prop)

    temp_file = PROPERTIES_FILE + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(updated_properties, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, PROPERTIES_FILE)

    print(
        f"✅ Analyzer V{ANALYZER_VERSION} סיים: "
        f"{analyzed_count} נכסים נותחו/עודכנו, "
        f"{geocoded_count} נכסים קיבלו קואורדינטות."
    )
    print("ℹ️ ARV, Rent, Rehab ו-Neighborhood Class עדיין אומדנים זמניים ולא נתונים מאומתים.")


if __name__ == '__main__':
    run_analyzer()
