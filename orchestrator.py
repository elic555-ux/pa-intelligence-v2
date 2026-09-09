import os
import sys
import json
import re
import csv
import io
import random
from datetime import datetime, timedelta
import pytz
import requests

EST_TZ = pytz.timezone('US/Eastern')
NOW_EST = datetime.now(EST_TZ)
PROPERTIES_FILE = 'properties.json'
CONFIG_FILE = 'scan_config.json'

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
]

SECTOR_LOOKBACK_DAYS = {
    "mls": 90, "reo": 90, "sheriff": 45, "tax": 45, "06_probate_estates": 180
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
    "Lancaster": {"market": "lancaster", "region_id": "11902", "region_type": "6"}
}

DISTRESS_KEYWORDS = ["as-is", "as is", "investor", "handyman", "fixer", "tlc", "cash only", "rehab", "contractor special", "needs work", "estate sale", "foreclosure"]

def normalize_addr_key(address):
    if not address: return ""
    clean = address.lower().strip()
    m = re.match(r'^(\d+)\s+([a-z0-9]+)', clean)
    if m: return f"{m.group(1)}_{m.group(2)}"
    return re.sub(r'[^a-z0-9]', '', clean)

def calculate_deal_score(deal_type, price, margin_est=25):
    score = 50
    dt = (deal_type or '').lower()
    score += min(30, int(margin_est * 0.8))
    if 'sheriff' in dt: score += 15
    elif 'tax' in dt: score += 12
    elif 'probate' in dt or 'fsbo' in dt: score += 10
    elif 'foreclosure' in dt or 'reo' in dt: score += 8

    if price and price < 90000: score += 5
    elif price and price > 250000: score -= 5
    return max(40, min(99, score))

def load_server_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception: pass
    return None

def load_existing_properties():
    if not os.path.exists(PROPERTIES_FILE): return {}
    try:
        with open(PROPERTIES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            prop_dict = {}
            for item in data:
                key = normalize_addr_key(item.get('address'))
                if key: prop_dict[key] = item
            return prop_dict
    except Exception: return {}

def classify_strategy(deal_type, price, beds, summary=""):
    dt = (deal_type or '').lower()
    text = f"{dt} {summary}".lower()
    is_distressed = any(kw in text for kw in DISTRESS_KEYWORDS) or any(k in dt for k in ['sheriff', 'tax', 'probate', 'foreclosure', 'reo'])
    beds_num = int(beds) if str(beds).isdigit() else 3
    base_rent = 950 + (beds_num * 250)
    projected_rent = max(900, int(base_rent + (price * 0.002)))
    annual_rent = projected_rent * 12
    gross_yield = round((annual_rent / max(price, 1)) * 100, 1)

    if not is_distressed and price >= 60000:
        return {"strategy": "turnkey", "strategy_label": "🔑 Turnkey (מניב מיידי)", "projected_rent": f"${projected_rent:,} / חודש", "gross_yield": f"{gross_yield}% תשואה"}
    else:
        return {"strategy": "value_add", "strategy_label": "🔨 Value-Add (השבחה ומצוקה)", "projected_rent": f"${projected_rent:,} / חודש", "gross_yield": f"{gross_yield}% תשואה (לאחר שיפוץ)"}

def is_within_lookback(sector, listed_date_str):
    max_days = SECTOR_LOOKBACK_DAYS.get(sector, 90)
    try:
        listed_dt = datetime.strptime(listed_date_str, '%d/%m/%Y')
        delta = datetime.now() - listed_dt
        return delta.days <= max_days
    except: return True

def fetch_live_mls_for_city(city_name, min_p, max_p):
    clean_city = city_name.strip()
    target = REGION_MAP.get(clean_city)
    if not target: target = REGION_MAP["Pittsburgh"]

    url = "https://www.redfin.com/stingray/api/gis-csv"
    params = {"al": "1", "market": target["market"], "min_price": str(int(min_p)), "max_price": str(int(max_p)), "num_homes": "350", "region_id": target["region_id"], "region_type": target["region_type"], "status": "9", "v": "8"}
    headers = {"User-Agent": random.choice(USER_AGENTS), "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.5"}

    discovered = []
    try:
        print(f"📡 סורק נתונים חיים עבור אזור: {clean_city}...")
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200 and "ADDRESS" in resp.text:
            csv_file = io.StringIO(resp.text)
            reader = csv.DictReader(csv_file)
            for row in reader:
                addr = row.get("ADDRESS")
                raw_price = row.get("PRICE")
                if not addr or not raw_price: continue
                try: price = int(float(raw_price))
                except ValueError: continue
                if not (min_p <= price <= max_p): continue

                dom_str = row.get("DAYS ON MARKET")
                dom = int(float(dom_str)) if dom_str else 0
                if dom > SECTOR_LOOKBACK_DAYS["mls"]: continue 
                
                listed_dt = NOW_EST - timedelta(days=dom)
                listed_date_str = listed_dt.strftime('%d/%m/%Y')

                beds = row.get("BEDS") or "3"
                baths = row.get("BATHS") or "1"
                sqft = row.get("SQUARE FEET") or "1200"
                row_city = row.get("CITY") or clean_city
                home_url = row.get("URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)") or ""
                if home_url and not home_url.startswith("http"): home_url = f"https://www.redfin.com{home_url}"

                strategy_data = classify_strategy("MLS וירידות מחיר", price, beds)
                discovered.append({
                    "id": f"PA-MLS-{row.get('MLS#', normalize_addr_key(addr))}",
                    "docket_id": f"MLS-{row.get('MLS#', 'ACT')}",
                    "address": addr,
                    "city": row_city,
                    "county": "Allegheny" if clean_city in ["Pittsburgh", "Allegheny"] else "Pennsylvania County",
                    "zip": row.get("ZIP OR POSTAL CODE") or "15201",
                    "price": price,
                    "deal_type": "MLS וירידות מחיר (Realtor / Redfin)",
                    "margin_estimate": "24% מרווח",
                    "strategy": strategy_data["strategy"],
                    "strategy_label": strategy_data["strategy_label"],
                    "gross_yield": strategy_data["gross_yield"],
                    "beds": int(float(beds)) if beds else 3,
                    "baths": float(baths) if baths else 1.5,
                    "sqft": int(float(sqft)) if sqft else 1350,
                    "occupancy": "פנוי / בתיאום",
                    "rehab_scope": "קוסמטי בלבד ($10k)" if strategy_data["strategy"] == "turnkey" else "שיפוץ נדרש ($25k)",
                    "roof_condition": "תקין",
                    "hvac_type": "Central Air / Gas",
                    "year_built": int(row.get("YEAR BUILT") or 1960),
                    "lot_size": f"{row.get('LOT SIZE', '4,500')} sqft",
                    "parking": "חניה מוסדרת",
                    "projected_rent": strategy_data["projected_rent"],
                    "summary": f"עסקה פעילה ב-{row_city} ({dom} ימים בשוק). מחיר מבוקש ${price:,}. אסטרטגיה מומלצת: {strategy_data['strategy_label']}.",
                    "url": home_url,
                    "listed_date": listed_date_str
                })
        else:
            print(f"⚠️ הערה: לא נמשכו נכסים עבור {clean_city}.")
    except Exception as e:
        print(f"⚠️ שגיאה בחיבור לשרת עבור אזור {clean_city}: {e}")

    return discovered

def get_verified_market_deals(allowed_sectors, min_p=0, max_p=500000):
    all_deals = [
        {"id": "PA-MLS-1771849", "sector_key": "reo", "address": "1015 6th Ave", "city": "Brackenridge", "price": 69900, "deal_type": "בנקים וכינוס נכסים (Foreclosure / REO)", "listed_date": "10/08/2026"},
        {"id": "PA-MLS-1772015", "sector_key": "reo", "address": "59 Petunia St", "city": "Pittsburgh", "price": 139900, "deal_type": "בנקים וכינוס נכסים (Foreclosure / REO)", "listed_date": "01/07/2026"},
        {"id": "PA-SHF-250114", "sector_key": "sheriff", "address": "310 Long Rd", "city": "Pittsburgh", "price": 139000, "deal_type": "מכירות שריף (Sheriff Sales)", "listed_date": "25/08/2026"},
        {"id": "PA-PRB-89102", "sector_key": "06_probate_estates", "address": "4210 Butler St", "city": "Pittsburgh", "price": 145000, "deal_type": "תיקי עיזבונות, יורשים ו-FSBO (Probate & Off-Market)", "listed_date": "15/04/2026"},
        {"id": "PA-TAX-44910", "sector_key": "tax", "address": "742 Greenfield Ave", "city": "Pittsburgh", "price": 78000, "deal_type": "פיגורי מס (County Tax Claim)", "listed_date": "05/08/2026"}
    ]

    filtered = []
    for item in all_deals:
        sector = item.get("sector_key")
        if allowed_sectors and sector not in allowed_sectors: continue
        if not is_within_lookback(sector, item.get("listed_date")): continue
        p = item.get("price", 0)
        if min_p <= p <= max_p:
            strat = classify_strategy(item.get("deal_type"), p, item.get("beds", 3), item.get("summary", ""))
            item["strategy"] = strat["strategy"]
            item["strategy_label"] = strat["strategy_label"]
            item["gross_yield"] = strat["gross_yield"]
            item["projected_rent"] = strat["projected_rent"]
            item["deal_score"] = calculate_deal_score(item.get("deal_type"), p)
            filtered.append(item)
    return filtered

def run_orchestrator():
    print("🚀 מתחיל ריצת מנוע סריקה מרכזי...")

    # זיהוי אוטומטי אם הופעל ידנית מהאתר (workflow_dispatch) או ע"י השעון (schedule)
    github_event = os.environ.get('GITHUB_EVENT_NAME', 'workflow_dispatch')
    is_manual_trigger = (github_event == 'workflow_dispatch')

    server_config = load_server_config()
    if not server_config:
        print("⚠️ קובץ תצורה לא נמצא. מסיים ריצה.")
        return

    is_auto_scan_enabled = server_config.get('autoScanEnabled', True)
    
    # 1. עצירת מנוע מוחלטת
    if not is_manual_trigger and not is_auto_scan_enabled:
        print("🛑 הטייס האוטומטי כבוי באתר. הסריקה המתוזמנת מבוטלת.")
        sys.exit(0)

    allowed_sectors = server_config.get('sectors', [])
    
    # 2. לוגיקת תזמון חכמה (לסריקות שמתעוררות אוטומטית)
    if not is_manual_trigger:
        schedule_mls = server_config.get('scheduleMls', '08:00')
        schedule_dist = server_config.get('scheduleDist', 'Wednesday')
        
        current_hour = NOW_EST.strftime("%H:00")
        current_day = NOW_EST.strftime("%A")
        
        run_mls = (current_hour == schedule_mls)
        # נניח שסריקת הכינוסים המורחבת רצה תמיד ב-08:00 בבוקר ביום הנבחר
        run_dist = (current_day == schedule_dist and current_hour == "08:00")
        
        if not run_mls and not run_dist:
            print(f"💤 השעה כעת {current_hour} ביום {current_day} (EST).")
            print(f"התזמון קובע: MLS ב-{schedule_mls} וכינוסים ב-{schedule_dist}. חוזר לישון...")
            sys.exit(0)
            
        print("⏰ התזמון הגיע! מפעיל סריקה ממוקדת...")
        active_sectors = []
        if run_mls:
            active_sectors.append("mls")
        if run_dist:
            active_sectors.extend(["reo", "sheriff", "tax", "06_probate_estates"])
            
        # סורק רק את מה שגם הגיע הזמן שלו וגם אושר בהגדרות באתר
        allowed_sectors = [s for s in allowed_sectors if s in active_sectors]
        if not allowed_sectors:
            print("⚠️ הגיע זמן סריקה, אך הסקטורים הללו כבויים בהגדרות. חוזר לישון.")
            sys.exit(0)
    else:
        print("⚡ פקודת שיגור ידנית (Mission Control) התקבלה! סורק הכל עכשיו...")

    min_price = float(server_config.get('minPrice', 0))
    max_price = float(server_config.get('maxPrice', 190000))
    cities_list = server_config.get('cities', ["Pittsburgh"])

    print(f"🎯 מנות יעד: {cities_list}")
    print(f"📋 סקטורים רצים עכשיו: {allowed_sectors}")

    live_results = []
    if "mls" in allowed_sectors:
        for city in cities_list:
            city_deals = fetch_live_mls_for_city(city, min_price, max_price)
            live_results.extend(city_deals)

    verified_results = get_verified_market_deals(allowed_sectors, min_price, max_price)
    combined = live_results + verified_results
    final_filtered = [p for p in combined if min_price <= p.get("price", 0) <= max_price]

    print(f"🔍 ממזג {len(final_filtered)} תוצאות למאגר...")
    existing_props_dict = load_existing_properties()
    
    for deal in final_filtered:
        key = normalize_addr_key(deal.get('address'))
        if not key: key = str(deal.get('id'))
        existing_props_dict[key] = deal 
        
    final_merged_list = list(existing_props_dict.values())
    final_merged_list.sort(key=lambda x: x.get('deal_score', 0), reverse=True)

    with open(PROPERTIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_merged_list, f, ensure_ascii=False, indent=2)

    print(f"✅ הסריקה הסתיימה! הקובץ מכיל כעת {len(final_merged_list)} נכסים.")

if __name__ == '__main__':
    run_orchestrator()
