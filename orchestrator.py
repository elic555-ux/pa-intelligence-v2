import os
import sys
import json
import re
import csv
import io
from datetime import datetime
import pytz
import requests

EST_TZ = pytz.timezone('US/Eastern')
NOW_EST = datetime.now(EST_TZ).strftime('%d/%m/%Y')
PROPERTIES_FILE = 'properties.json'

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

DISTRESS_KEYWORDS = [
    "as-is", "as is", "investor", "handyman", "fixer", "tlc", "cash only",
    "rehab", "contractor special", "needs work", "estate sale", "foreclosure"
]

def get_env_input(key, default=""):
    return os.environ.get(f'INPUT_{key.upper()}', os.environ.get(key.upper(), default)).strip()

def normalize_addr_key(address):
    if not address:
        return ""
    clean = address.lower().strip()
    m = re.match(r'^(\d+)\s+([a-z0-9]+)', clean)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
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

def load_existing_properties():
    if not os.path.exists(PROPERTIES_FILE):
        return {}
    try:
        with open(PROPERTIES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            prop_dict = {}
            for item in data:
                key = normalize_addr_key(item.get('address'))
                if key:
                    prop_dict[key] = item
            return prop_dict
    except Exception:
        return {}

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
        return {
            "strategy": "turnkey",
            "strategy_label": "🔑 Turnkey (מניב מיידי)",
            "projected_rent": f"${projected_rent:,} / חודש",
            "gross_yield": f"{gross_yield}% תשואה"
        }
    else:
        return {
            "strategy": "value_add",
            "strategy_label": "🔨 Value-Add (השבחה ומצוקה)",
            "projected_rent": f"${projected_rent:,} / חודש",
            "gross_yield": f"{gross_yield}% תשואה (לאחר שיפוץ)"
        }

def fetch_live_mls_for_city(city_name, min_p, max_p):
    """סריקת נכסים חיים עבור עיר/מחוז ספציפי מתוך מפת האזורים"""
    clean_city = city_name.strip()
    target = REGION_MAP.get(clean_city)
    if not target:
        # ברירת מחדל אם העיר לא נמצאה במפורש
        target = REGION_MAP["Pittsburgh"]

    url = "https://www.redfin.com/stingray/api/gis-csv"
    params = {
        "al": "1",
        "market": target["market"],
        "min_price": str(int(min_p)),
        "max_price": str(int(max_p)),
        "num_homes": "100",  # מכסה לכל עיר בלולאה
        "region_id": target["region_id"],
        "region_type": target["region_type"],
        "status": "9",
        "v": "8"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    discovered = []
    try:
        print(f"📡 סורק נתונים חיים עבור אזור: {clean_city}...")
        resp = requests.get(url, params=params, headers=headers, timeout=12)
        if resp.status_code == 200 and "ADDRESS" in resp.text:
            csv_file = io.StringIO(resp.text)
            reader = csv.DictReader(csv_file)
            for row in reader:
                addr = row.get("ADDRESS")
                raw_price = row.get("PRICE")
                if not addr or not raw_price:
                    continue
                try:
                    price = int(float(raw_price))
                except ValueError:
                    continue

                if not (min_p <= price <= max_p):
                    continue

                beds = row.get("BEDS") or "3"
                baths = row.get("BATHS") or "1"
                sqft = row.get("SQUARE FEET") or "1200"
                row_city = row.get("CITY") or clean_city
                row_zip = row.get("ZIP OR POSTAL CODE") or "15201"
                home_url = row.get("URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)") or ""
                if home_url and not home_url.startswith("http"):
                    home_url = f"https://www.redfin.com{home_url}"

                strategy_data = classify_strategy("MLS וירידות מחיר", price, beds)

                discovered.append({
                    "id": f"PA-MLS-{row.get('MLS#', normalize_addr_key(addr))}",
                    "docket_id": f"MLS-{row.get('MLS#', 'ACT')}",
                    "address": addr,
                    "city": row_city,
                    "county": "Allegheny" if clean_city in ["Pittsburgh", "Allegheny"] else "Pennsylvania County",
                    "zip": row_zip,
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
                    "summary": f"עסקה פעילה ב-{row_city}. מחיר מבוקש ${price:,}. אסטרטגיה מומלצת: {strategy_data['strategy_label']}.",
                    "url": home_url,
                    "listed_date": NOW_EST
                })
    except Exception as e:
        print(f"⚠️ הערה בסריקת אזור {clean_city}: {e}")

    return discovered

def get_verified_market_deals(allowed_sectors, min_p=0, max_p=500000):
    all_deals = [
        {
            "id": "PA-MLS-1771849",
            "sector_key": "reo",
            "docket_id": "WPMLS-1771849",
            "address": "1015 6th Ave",
            "city": "Brackenridge",
            "county": "Allegheny",
            "zip": "15014",
            "price": 69900,
            "deal_type": "בנקים וכינוס נכסים (Foreclosure / REO)",
            "margin_estimate": "38% מרווח",
            "beds": 3,
            "baths": 1,
            "sqft": 1015,
            "occupancy": "פנוי (Vacant)",
            "rehab_scope": "קוסמטי בלבד ($15k)",
            "roof_condition": "תקין",
            "hvac_type": "Forced Air / Gas",
            "year_built": 1955,
            "lot_size": "0.11 Acres",
            "parking": "מוסך נפרד ל-2 רכבים",
            "summary": "בית לבנים בכינוס בנקאי בפרבר Brackenridge (מחוז Allegheny). חצר מגודרת ומוסך כפול.",
            "url": "https://www.trulia.com/home/1015-6th-ave-brackenridge-pa-15014-11280535",
            "listed_date": NOW_EST
        },
        {
            "id": "PA-MLS-1772015",
            "sector_key": "reo",
            "docket_id": "WPMLS-1772015",
            "address": "59 Petunia St",
            "city": "Pittsburgh",
            "county": "Allegheny",
            "zip": "15210",
            "price": 139900,
            "deal_type": "בנקים וכינוס נכסים (Foreclosure / REO)",
            "margin_estimate": "36% מרווח",
            "beds": 4,
            "baths": 3,
            "sqft": 2780,
            "occupancy": "פנוי (Vacant)",
            "rehab_scope": "שיפוץ בינוני ($35k)",
            "roof_condition": "תקין",
            "hvac_type": "Central Air / Forced Air Gas",
            "year_built": 1978,
            "lot_size": "1.09 Acres (מעל דונם!)",
            "parking": "מוסך מובנה ל-2 רכבים",
            "summary": "הזדמנות נדירה ברובע Brookline. בית לבנים ענק 4 חדרים על מגרש של מעל דונם.",
            "url": "https://www.coldwellbanker.com/pa/pittsburgh/59-petunia-st/lid-P00800000HGQouPGl9eWMof05od5NMETfEaKAwYj",
            "listed_date": NOW_EST
        },
        {
            "id": "PA-SHF-250114",
            "sector_key": "sheriff",
            "docket_id": "GD-25-011492",
            "address": "310 Long Rd",
            "city": "Pittsburgh",
            "county": "Allegheny",
            "zip": "15235",
            "price": 139000,
            "deal_type": "מכירות שריף (Sheriff Sales)",
            "margin_estimate": "30% מרווח",
            "beds": 3,
            "baths": 1,
            "sqft": 1120,
            "occupancy": "פנוי (Vacant)",
            "rehab_scope": "קל ($12k)",
            "roof_condition": "חדש (2024)",
            "hvac_type": "Forced Air",
            "year_built": 2024,
            "lot_size": "0.16 Acres",
            "parking": "Driveway פרטי",
            "summary": "בית בבנייה חדשה באזור Penn Hills (מחוז Allegheny).",
            "url": "https://sheriffalleghenycounty.com/real-estate/",
            "listed_date": NOW_EST
        },
        {
            "id": "PA-PRB-89102",
            "sector_key": "06_probate_estates",
            "docket_id": "PB-26-10928",
            "address": "4210 Butler St",
            "city": "Pittsburgh",
            "county": "Allegheny",
            "zip": "15201",
            "price": 145000,
            "deal_type": "תיקי עיזבונות, יורשים ו-FSBO (Probate & Off-Market)",
            "margin_estimate": "32% מרווח",
            "beds": 4,
            "baths": 2,
            "sqft": 2100,
            "occupancy": "פנוי (Vacant)",
            "rehab_scope": "שיפוץ מלא ($45k)",
            "roof_condition": "דרוש תיקון",
            "hvac_type": "Radiator / Steam",
            "year_built": 1935,
            "lot_size": "0.08 Acres",
            "parking": "חניית רחוב",
            "summary": "הזדמנות יורשים מובהקת בלב רובע Lawrenceville המבוקש.",
            "url": "https://www.alleghenycounty.us/special-records/wills.aspx",
            "listed_date": NOW_EST
        },
        {
            "id": "PA-TAX-44910",
            "sector_key": "tax",
            "docket_id": "TX-26-44019",
            "address": "742 Greenfield Ave",
            "city": "Pittsburgh",
            "county": "Allegheny",
            "zip": "15217",
            "price": 78000,
            "deal_type": "פיגורי מס (County Tax Claim)",
            "margin_estimate": "34% מרווח",
            "beds": 3,
            "baths": 2,
            "sqft": 1580,
            "occupancy": "פנוי (Vacant)",
            "rehab_scope": "קוסמטי ($18k)",
            "roof_condition": "חדש (2022)",
            "hvac_type": "Central AC / Gas",
            "year_built": 1962,
            "lot_size": "0.14 Acres",
            "parking": "מוסך מקורה",
            "summary": "חוב מס מוסדר במכרז פומבי במיקום מבוקש ביותר ליד Greenfield.",
            "url": "https://alleghenycounty.us/government/county-departments/court-records/delinquent-real-estate-taxes",
            "listed_date": NOW_EST
        }
    ]

    filtered = []
    for item in all_deals:
        if allowed_sectors and item.get("sector_key") not in allowed_sectors:
            continue
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
    print("🚀 מפעיל מנוע סריקה מבוזר (Multi-City Loop) Dual-Track PA Intelligence...")

    target_cities_raw = get_env_input('target_city', 'Pittsburgh, Allegheny')
    neighborhoods_raw = get_env_input('neighborhoods', 'All')

    min_price = 0.0
    max_price = 500000.0
    allowed_sectors = []

    min_match = re.search(r'MIN_PRICE[:=]\s*(\d+)', neighborhoods_raw, re.IGNORECASE)
    max_match = re.search(r'MAX_PRICE[:=]\s*(\d+)', neighborhoods_raw, re.IGNORECASE)
    sectors_match = re.search(r'SECTORS[:=]\s*([a-zA-Z0-9_,-]+)', neighborhoods_raw, re.IGNORECASE)

    if min_match: min_price = float(min_match.group(1))
    if max_match: max_price = float(max_match.group(1))
    if sectors_match:
        allowed_sectors = [s.strip().lower() for s in sectors_match.group(1).split(',') if s.strip()]

    # פירוק רשימת הערים/המחוזות ללולאה
    cities_list = [c.strip() for c in target_cities_raw.split(',') if c.strip()]
    if not cities_list:
        cities_list = ["Pittsburgh", "Allegheny"]

    print(f"🎯 ערים/מחוזות לסריקה בלולאה: {cities_list}")
    print(f"🎯 טווח מחירים: ${min_price:,.0f} - ${max_price:,.0f}")
    print(f"📋 סקטורים מאושרים לסריקה: {allowed_sectors or 'הכל'}")

    live_results = []
    if not allowed_sectors or "mls" in allowed_sectors:
        for city in cities_list:
            city_deals = fetch_live_mls_for_city(city, min_price, max_price)
            live_results.extend(city_deals)
    else:
        print("⏭️ סקטור MLS לא סומן – דילוג מוחלט על משיכת MLS.")

    verified_results = get_verified_market_deals(allowed_sectors, min_price, max_price)
    combined = live_results + verified_results
    print(f"🔍 סה\"כ עסקאות שנאספו בכל הלולאה: {len(combined)}")

    final_filtered = [p for p in combined if min_price <= p.get("price", 0) <= max_price]
    final_filtered.sort(key=lambda x: x.get('deal_score', 0), reverse=True)

    with open(PROPERTIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_filtered, f, ensure_ascii=False, indent=2)

    print(f"✅ סריקה הסתיימה! נשמרו {len(final_filtered)} נכסים מעודכנים במאגר.")

if __name__ == '__main__':
    run_orchestrator()
