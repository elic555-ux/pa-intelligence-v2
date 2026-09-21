import os
import sys
import json
import re
import csv
import io
import random
from copy import deepcopy
from datetime import datetime, timedelta

import pytz
import requests

EST_TZ = pytz.timezone("US/Eastern")
PROPERTIES_FILE = "properties.json"
CONFIG_FILE = "scan_config.json"
SCAN_LOG_FILE = "scan_log.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

SECTOR_LOOKBACK_DAYS = {
    "mls": 90,
    "reo": 90,
    "sheriff": 45,
    "tax": 45,
    "06_probate_estates": 180,
}

REGION_MAP = {
    "Pittsburgh": {"market": "pittsburgh", "region_id": "15702", "region_type": "6"},
    "Allegheny": {"market": "pittsburgh", "region_id": "2362", "region_type": "5"},
    "Philadelphia": {"market": "philadelphia", "region_id": "15502", "region_type": "6"},
    "Allentown": {"market": "allentown", "region_id": "3144", "region_type": "6"},
    "Reading": {"market": "reading", "region_id": "17387", "region_type": "6"},
    "Erie": {"market": "erie", "region_id": "6758", "region_type": "6"},
    "Scranton": {"market": "scranton", "region_id": "19404", "region_type": "6"},
    "Bethlehem": {"market": "allentown", "region_id": "3531", "region_type": "6"},
    "Lancaster": {"market": "lancaster", "region_id": "11902", "region_type": "6"},
}

DISTRESS_KEYWORDS = [
    "as-is", "as is", "investor", "handyman", "fixer", "tlc", "cash only",
    "rehab", "contractor special", "needs work", "estate sale", "foreclosure",
]

STREET_SUFFIXES = {
    "street": "st", "st.": "st", "avenue": "ave", "ave.": "ave",
    "road": "rd", "rd.": "rd", "boulevard": "blvd", "blvd.": "blvd",
    "drive": "dr", "dr.": "dr", "lane": "ln", "ln.": "ln",
    "court": "ct", "ct.": "ct", "place": "pl", "pl.": "pl",
    "terrace": "ter", "highway": "hwy", "parkway": "pkwy",
}


def now_est():
    """Return a fresh Eastern Time timestamp for every operation."""
    return datetime.now(EST_TZ)


def iso_now_est():
    return now_est().isoformat(timespec="seconds")


def safe_number(value, default=0, number_type=float):
    try:
        if value is None or value == "":
            return default
        return number_type(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def normalize_address(address):
    """Normalize an address conservatively for duplicate detection."""
    if not address:
        return ""
    text = str(address).lower().strip()
    text = re.sub(r"[,.#]", " ", text)
    parts = [p for p in re.split(r"\s+", text) if p]
    parts = [STREET_SUFFIXES.get(p, p) for p in parts]
    return " ".join(parts)


def normalize_addr_key(address, city="", zip_code=""):
    """
    Stable property key used by the scanner.
    Address is primary; city/ZIP are included when available to reduce collisions.
    """
    address_norm = normalize_address(address)
    city_norm = re.sub(r"[^a-z0-9]", "", str(city).lower())
    zip_norm = re.sub(r"[^0-9]", "", str(zip_code))[:5]
    raw = "|".join(part for part in [address_norm, city_norm, zip_norm] if part)
    return re.sub(r"[^a-z0-9|]", "", raw)


def property_key(item):
    if not isinstance(item, dict):
        return ""
    key = normalize_addr_key(item.get("address"), item.get("city"), item.get("zip"))
    if key:
        return key
    return str(item.get("id") or "").strip()


def calculate_deal_score(deal_type, price, margin_est=25):
    # Compatibility score only. It will be replaced later by the full scoring engine.
    score = 50
    dt = (deal_type or "").lower()
    score += min(30, int(margin_est * 0.8))
    if "sheriff" in dt:
        score += 15
    elif "tax" in dt:
        score += 12
    elif "probate" in dt or "fsbo" in dt:
        score += 10
    elif "foreclosure" in dt or "reo" in dt:
        score += 8

    if price and price < 90000:
        score += 5
    elif price and price > 250000:
        score -= 5
    return max(40, min(99, score))


def load_json_file(path, default):
    if not os.path.exists(path):
        return deepcopy(default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"⚠️ לא ניתן לקרוא את {path}: {exc}")
        return deepcopy(default)


def atomic_write_json(path, data):
    """Write JSON safely so an interrupted run does not destroy the main file."""
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)


def load_server_config():
    data = load_json_file(CONFIG_FILE, None)
    return data if isinstance(data, dict) else None


def load_existing_properties():
    data = load_json_file(PROPERTIES_FILE, [])
    if not isinstance(data, list):
        print("⚠️ properties.json אינו מערך תקין. ממשיך עם מאגר ריק כדי לא לקרוס.")
        return {}

    prop_dict = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        key = property_key(item)
        if key:
            prop_dict[key] = item
    return prop_dict


def append_scan_log(entry):
    """Keep a bounded audit trail for every scanner execution."""
    log = load_json_file(SCAN_LOG_FILE, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    # Keep the file small while retaining a useful audit trail.
    log = log[-1000:]
    atomic_write_json(SCAN_LOG_FILE, log)


def classify_strategy(deal_type, price, beds, summary=""):
    dt = (deal_type or "").lower()
    text = f"{dt} {summary}".lower()
    is_distressed = any(kw in text for kw in DISTRESS_KEYWORDS) or any(
        k in dt for k in ["sheriff", "tax", "probate", "foreclosure", "reo"]
    )
    beds_num = safe_number(beds, 3, int)
    base_rent = 950 + (beds_num * 250)
    projected_rent = max(900, int(base_rent + (safe_number(price, 0, float) * 0.002)))
    annual_rent = projected_rent * 12
    gross_yield = round((annual_rent / max(safe_number(price, 1, float), 1)) * 100, 1)

    if not is_distressed and safe_number(price, 0, float) >= 60000:
        return {
            "strategy": "turnkey",
            "strategy_label": "🔑 Turnkey (מניב מיידי)",
            "projected_rent": f"${projected_rent:,} / חודש",
            "gross_yield": f"{gross_yield}% תשואה",
        }
    return {
        "strategy": "value_add",
        "strategy_label": "🔨 Value-Add (השבחה ומצוקה)",
        "projected_rent": f"${projected_rent:,} / חודש",
        "gross_yield": f"{gross_yield}% תשואה (לאחר שיפוץ)",
    }


def fetch_live_mls_for_city(city_name, min_p, max_p):
    clean_city = city_name.strip()
    target = REGION_MAP.get(clean_city)
    if not target:
        print(f"⚠️ האזור '{clean_city}' אינו ממופה ל-Redfin. מדלג כדי לא לסרוק אזור שגוי.")
        return []

    url = "https://www.redfin.com/stingray/api/gis-csv"
    params = {
        "al": "1",
        "market": target["market"],
        "min_price": str(int(min_p)),
        "max_price": str(int(max_p)),
        "num_homes": "350",
        "region_id": target["region_id"],
        "region_type": target["region_type"],
        "status": "9",
        "v": "8",
    }
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/csv,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.5",
    }

    discovered = []
    try:
        print(f"📡 סורק נתונים חיים עבור אזור: {clean_city}...")
        resp = requests.get(url, params=params, headers=headers, timeout=20)
        if resp.status_code != 200 or "ADDRESS" not in resp.text:
            print(f"⚠️ לא נמשכו נתוני MLS תקינים עבור {clean_city}. HTTP {resp.status_code}")
            return []

        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            addr = row.get("ADDRESS")
            raw_price = row.get("PRICE")
            if not addr or not raw_price:
                continue

            price = safe_number(raw_price, 0, int)
            if price <= 0:
                continue

            dom = max(0, safe_number(row.get("DAYS ON MARKET"), 0, int))
            if dom > SECTOR_LOOKBACK_DAYS["mls"]:
                continue

            listed_dt = now_est() - timedelta(days=dom)
            listed_date_str = listed_dt.strftime("%d/%m/%Y")
            beds = safe_number(row.get("BEDS"), 3, int)
            baths = safe_number(row.get("BATHS"), 1.5, float)
            sqft = safe_number(row.get("SQUARE FEET"), 1350, int)
            row_city = row.get("CITY") or clean_city
            zip_code = row.get("ZIP OR POSTAL CODE") or ""
            home_url = row.get(
                "URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)"
            ) or ""
            if home_url and not home_url.startswith("http"):
                home_url = f"https://www.redfin.com{home_url}"

            strategy_data = classify_strategy("MLS", price, beds)
            mls_number = row.get("MLS#") or normalize_addr_key(addr, row_city, zip_code)
            discovered.append({
                "id": f"PA-MLS-{mls_number}",
                "docket_id": f"MLS-{mls_number}",
                "address": addr,
                "city": row_city,
                "county": "Allegheny" if clean_city in ["Pittsburgh", "Allegheny"] else "",
                "zip": zip_code,
                "price": price,
                "deal_type": "MLS (Realtor / Redfin)",
                "source": "Redfin",
                "source_type": "mls",
                "data_status": "live",
                "strategy": strategy_data["strategy"],
                "strategy_label": strategy_data["strategy_label"],
                "gross_yield": strategy_data["gross_yield"],
                "beds": beds,
                "baths": baths,
                "sqft": sqft,
                "year_built": safe_number(row.get("YEAR BUILT"), 0, int) or None,
                "lot_size": row.get("LOT SIZE") or "",
                "projected_rent": strategy_data["projected_rent"],
                "summary": f"עסקה פעילה ב-{row_city} ({dom} ימים בשוק). מחיר מבוקש ${price:,}.",
                "url": home_url,
                "listed_date": listed_date_str,
                "days_on_market": dom,
                "last_source_check": iso_now_est(),
            })
    except requests.RequestException as exc:
        print(f"⚠️ שגיאת רשת בסריקת {clean_city}: {exc}")
    except Exception as exc:
        print(f"⚠️ שגיאה לא צפויה בסריקת {clean_city}: {exc}")

    return discovered


def get_placeholder_sector_results(active_sectors):
    """
    The old orchestrator injected hard-coded REO/Sheriff/Tax/Probate properties.
    They are intentionally disabled in LIVE mode. Each sector will be connected
    to a verified source in a later controlled step.
    """
    pending = [s for s in active_sectors if s != "mls"]
    if pending:
        print("ℹ️ הסקטורים הבאים עדיין אינם מחוברים למקור LIVE ולכן לא יוזרקו נתוני דמה: " + ", ".join(pending))
    return []


def comparable_changed(old, new):
    """Detect meaningful source changes without treating timestamps as updates."""
    tracked_fields = [
        "price", "deal_type", "beds", "baths", "sqft", "year_built",
        "lot_size", "url", "days_on_market", "listed_date", "source_type",
    ]
    return any(old.get(field) != new.get(field) for field in tracked_fields)


def merge_property(existing, incoming, scan_id):
    """
    Merge source data into an existing property without deleting enrichment,
    notes, analysis or other fields added by later parts of the system.
    """
    timestamp = iso_now_est()
    if existing is None:
        merged = deepcopy(incoming)
        merged["first_seen"] = timestamp
        merged["last_seen"] = timestamp
        merged["last_scan_id"] = scan_id
        merged["scan_status"] = "new"
        merged["seen_count"] = 1
        merged["price_history"] = [{"date": timestamp, "price": incoming.get("price"), "source": incoming.get("source", "")}]
        return merged, "new"

    changed = comparable_changed(existing, incoming)
    old_price = existing.get("price")
    new_price = incoming.get("price")

    merged = deepcopy(existing)
    merged.update(incoming)
    merged["first_seen"] = existing.get("first_seen") or timestamp
    merged["last_seen"] = timestamp
    merged["last_scan_id"] = scan_id
    merged["seen_count"] = safe_number(existing.get("seen_count"), 0, int) + 1
    merged["scan_status"] = "updated" if changed else "unchanged"

    price_history = existing.get("price_history")
    if not isinstance(price_history, list):
        price_history = []
    if not price_history and old_price is not None:
        price_history.append({"date": existing.get("first_seen") or timestamp, "price": old_price, "source": existing.get("source", "")})
    if new_price is not None and old_price != new_price:
        price_history.append({"date": timestamp, "price": new_price, "source": incoming.get("source", "")})
    merged["price_history"] = price_history

    return merged, "updated" if changed else "unchanged"


def run_orchestrator():
    scan_started = now_est()
    scan_id = scan_started.strftime("SCAN-%Y%m%d-%H%M%S")
    print(f"🚀 מתחיל ריצת מנוע סריקה מרכזי... {scan_id}")

    github_event = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    is_manual_trigger = github_event == "workflow_dispatch"
    server_config = load_server_config()

    log_entry = {
        "scan_id": scan_id,
        "started_at": scan_started.isoformat(timespec="seconds"),
        "trigger": "manual" if is_manual_trigger else "scheduled",
        "status": "started",
        "active_sectors": [],
        "cities": [],
        "source_results": 0,
        "after_filters": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": [],
    }

    if not server_config:
        log_entry["status"] = "failed"
        log_entry["errors"].append("scan_config.json missing or invalid")
        log_entry["finished_at"] = iso_now_est()
        append_scan_log(log_entry)
        print("⚠️ קובץ תצורה לא נמצא או אינו תקין. מסיים ריצה.")
        return

    is_auto_scan_enabled = server_config.get("autoScanEnabled", True)
    if not is_manual_trigger and not is_auto_scan_enabled:
        log_entry["status"] = "skipped"
        log_entry["skip_reason"] = "auto scan disabled"
        log_entry["finished_at"] = iso_now_est()
        append_scan_log(log_entry)
        print("🛑 הטייס האוטומטי כבוי בממשק האתר. הסריקה המתוזמנת מבוטלת.")
        return

    user_selected_sectors = server_config.get(
        "sectors", ["mls", "reo", "sheriff", "tax", "06_probate_estates"]
    )
    active_sectors_now = []

    if is_manual_trigger:
        print("⚡ פקודת שיגור ידנית התקבלה. סורק את הסקטורים שסומנו בממשק...")
        active_sectors_now = list(user_selected_sectors)
    else:
        schedules = server_config.get("schedules", {})
        current_hour = now_est().strftime("%H:00")
        current_day = now_est().strftime("%A")
        print(f"⏰ השעה בחוף המזרחי: {current_day}, {current_hour}")

        for sec, sched in schedules.items():
            s_day = sched.get("day", "Everyday")
            s_time = sched.get("time", "08:00")
            if s_time == current_hour and (s_day == "Everyday" or s_day == current_day):
                active_sectors_now.append(sec)

        active_sectors_now = [s for s in active_sectors_now if s in user_selected_sectors]
        if not active_sectors_now:
            log_entry["status"] = "skipped"
            log_entry["skip_reason"] = "no sector scheduled for this hour"
            log_entry["finished_at"] = iso_now_est()
            append_scan_log(log_entry)
            print("💤 אין סורקים שמתוזמנים לשעה זו. הריצה נרשמה בלוג ומסתיימת.")
            return

    min_price = safe_number(server_config.get("minPrice"), 0, float)
    max_price = safe_number(server_config.get("maxPrice"), 190000, float)
    min_sqft = safe_number(server_config.get("minSqft"), 0, int)
    max_sqft = safe_number(server_config.get("maxSqft"), 99999, int)
    min_beds = safe_number(server_config.get("minBeds"), 0, int)
    max_beds = safe_number(server_config.get("maxBeds"), 99, int)
    cities_list = server_config.get("cities") or ["Pittsburgh"]

    log_entry["active_sectors"] = active_sectors_now
    log_entry["cities"] = cities_list

    print(f"🎯 אזורי יעד: {cities_list}")
    print(f"🎯 מחיר: {min_price:g}-{max_price:g} | SqFt: {min_sqft}-{max_sqft} | Beds: {min_beds}-{max_beds}")
    print(f"📋 סקטורים פעילים: {active_sectors_now}")

    live_results = []
    if "mls" in active_sectors_now:
        for city in cities_list:
            live_results.extend(fetch_live_mls_for_city(city, min_price, max_price))

    combined = live_results + get_placeholder_sector_results(active_sectors_now)
    log_entry["source_results"] = len(combined)

    final_filtered = []
    for prop in combined:
        p_price = safe_number(prop.get("price"), 0, float)
        p_sqft = safe_number(prop.get("sqft"), 0, int)
        p_beds = safe_number(prop.get("beds"), 0, int)
        if not (min_price <= p_price <= max_price):
            continue
        if not (min_sqft <= p_sqft <= max_sqft):
            continue
        if not (min_beds <= p_beds <= max_beds):
            continue
        final_filtered.append(prop)

    log_entry["after_filters"] = len(final_filtered)
    print(f"🔍 {len(final_filtered)} תוצאות עברו את כל המסננים. מבצע מיזוג בטוח...")

    existing_props_dict = load_existing_properties()
    for deal in final_filtered:
        key = property_key(deal)
        if not key:
            print(f"⚠️ תוצאה ללא מזהה/כתובת דולגה: {deal.get('id', 'unknown')}")
            continue

        existing = existing_props_dict.get(key)
        merged, state = merge_property(existing, deal, scan_id)
        existing_props_dict[key] = merged
        log_entry[state] += 1

    final_merged_list = list(existing_props_dict.values())
    final_merged_list.sort(
        key=lambda x: (safe_number(x.get("deal_score"), 0, int), x.get("last_seen", "")),
        reverse=True,
    )

    try:
        atomic_write_json(PROPERTIES_FILE, final_merged_list)
        log_entry["status"] = "success"
    except OSError as exc:
        log_entry["status"] = "failed"
        log_entry["errors"].append(f"properties write failed: {exc}")
        print(f"❌ שמירת properties.json נכשלה: {exc}")

    log_entry["total_properties"] = len(final_merged_list)
    log_entry["finished_at"] = iso_now_est()
    append_scan_log(log_entry)

    print(
        f"✅ הסריקה הסתיימה: {log_entry['new']} חדשים | "
        f"{log_entry['updated']} עודכנו | {log_entry['unchanged']} ללא שינוי | "
        f"סה״כ במאגר: {len(final_merged_list)}"
    )
    print(f"🧾 רישום הסריקה נשמר ב-{SCAN_LOG_FILE}")


if __name__ == "__main__":
    run_orchestrator()
