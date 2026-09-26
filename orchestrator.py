import os
import sys
import json
import re
import csv
import io
import random
import subprocess
import tempfile
import hashlib
import html
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from copy import deepcopy
from datetime import datetime, timedelta

import pytz
import requests

EST_TZ = pytz.timezone("US/Eastern")
PROPERTIES_FILE = "properties.json"
CONFIG_FILE = "scan_config.json"
SCAN_LOG_FILE = "scan_log.json"
GEO_CATALOG_FILE = "geo_catalog.json"
SHERIFF_FILE = "sheriff_listings.json"
OFF_MARKET_MISS_THRESHOLD = 2
ORCHESTRATOR_VERSION = "2.7.0-scan-audit-20260926"
SCANNER_STATUS_FILE = "scanner_status.json"
SOURCE_LABELS = {"mls": "MLS", "reo": "בנקים וכינוס", "sheriff": "מכירות שריף",
                 "tax": "חובות מס", "06_probate_estates": "עיזבונות ופרטי"}

SHERIFF_PAGE = "https://sheriffalleghenycounty.com/sheriffs-sales/"
SHERIFF_LOCAL_PDF = "sources/allegheny_sheriff.pdf"
# Edition discovered on the official page. It expires; it is not a permanent feed.
SHERIFF_KNOWN_PDF = "https://sheriffalleghenycounty.com/wp-content/uploads/2026/09/October-Sale-List-Updated-9-24.pdf"


class SheriffPDFLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = html.unescape(dict(attrs).get("href", ""))
            url = urljoin(SHERIFF_PAGE, href)
            if valid_sheriff_pdf_url(url):
                self.links.append(url)


def valid_sheriff_pdf_url(url):
    parsed = urlparse(str(url))
    return (parsed.scheme == "https" and
            parsed.hostname in {"sheriffalleghenycounty.com", "www.sheriffalleghenycounty.com"}
            and parsed.path.lower().endswith(".pdf"))


def parse_sheriff_text(body, source_url, imported=False):
    """Parse published facts only; debt/bid amounts are never a property price."""
    body = body.replace('\r', '').replace('\x0c', '\n')
    sale_match = re.search(r"Date of Sale:\s*\w+,\s*(\w+ \d{1,2}, \d{4})", body)
    printed = re.findall(r"Printed:\s*(\d{1,2}/\d{1,2}/\d{4})", body)
    if not sale_match or not printed or not re.search(r"SHERIFF.*SALE.*PROPERTY LISTING", body, re.I):
        raise ValueError("מבנה PDF לא מוכר: חסרים כותרת, תאריך מכירה או תאריך הפקה")
    sale_day = datetime.strptime(sale_match.group(1), "%B %d, %Y").date()
    printed_day = max(datetime.strptime(x, "%m/%d/%Y").date() for x in printed)
    today = now_est().date()
    if not 0 <= (sale_day - today).days <= 75:
        raise ValueError(f"תאריך מכירה אינו בטווח עתידי: {sale_day}")
    if not 0 <= (today - printed_day).days <= 21:
        raise ValueError(f"המסמך אינו עדכני מספיק: הופק ב-{printed_day}")
    blocks = re.split(r"Status\s+Tracts", body, flags=re.I)[1:]
    if not blocks:
        raise ValueError("לא זוהו בלוקים של נכסים במסמך")
    rows, seen = [], set()
    skipped_active = 0
    recognized = 0
    for block in blocks:
        facts = block.split('Comments:')[0]
        status = re.search(r"(?m)^\s*(Active|Stayed|Postponed[^\n]*|Cancelled|Canceled|Sold|Continued[^\n]*|Withdrawn|Settled)[ \t]*$", facts, re.I)
        if not status:
            continue
        recognized += 1
        if status.group(1).casefold() != "active":
            continue
        docket = re.search(r"\b(?:GD|MG|AR)-\d{2}-\d{5,6}\b", facts, re.I)
        # The address immediately precedes the postal city. No assumptions about
        # a street suffix or municipality based on a Pittsburgh mailing address.
        address = re.search(r"(?m)^[ \t]*(\d[\w .,'/#&()-]*\S)[ \t]*\n[ \t]*([A-Z][A-Z .'-]+),[ \t]*PA[ \t]+(\d{5})(?:-\d{4})?\b", facts, re.I)
        if not docket or not address:
            skipped_active += 1
            continue
        street, city, zip_code = (x.strip() for x in address.groups())
        key = docket.group().upper() + ':' + normalize_addr_key(street, city, zip_code)
        if key in seen:
            continue
        seen.add(key)
        sale_type = re.search(r"Sale Type\s*\n([^\n]+)", facts, re.I)
        type_text = sale_type.group(1).strip() if sale_type else ""
        rows.append({
            "id": "PA-SHERIFF-" + hashlib.sha256(key.encode()).hexdigest()[:20],
            "docket_id": docket.group().upper(), "address": street.title(),
            "city": city.title(), "county": "Allegheny", "zip": zip_code,
            "price": None, "sqft": None, "beds": None, "baths": None,
            "deal_type": "Sheriff Sale", "source_type": "sheriff",
            "source": "Allegheny County Sheriff's Office", "source_sale_type": type_text,
            "data_status": "imported_official_document" if imported else "live",
            "market_status": "scheduled_sheriff_sale", "sheriff_status": "Active",
            "sale_date": sale_day.isoformat(), "source_published_date": printed_day.isoformat(),
            "listed_date": printed_day.strftime("%d/%m/%Y"),
            "summary": f"ברשימת השריף מ-{printed_day}: סטטוס Active למכירה ב-{sale_day}. {type_text}. מחיר, שטח וסוג נכס לא אומתו; יש לבדוק עדכון סטטוס במקור.",
            "url": source_url, "last_source_check": iso_now_est(), "deal_score": None,
            "filter_status": "investment_fields_unavailable",
        })
    if recognized != len(blocks) or (not rows and skipped_active):
        raise ValueError(f"פענוח חלקי: זוהו {recognized}/{len(blocks)} סטטוסים; {skipped_active} כתובות פעילות לא זוהו")
    return rows, {"published_date": printed_day.isoformat(), "sale_date": sale_day.isoformat(),
                  "parsed_blocks": len(blocks), "skipped_active_addresses": skipped_active,
                  "mode": "imported" if imported else "live"}


def extract_sheriff_pdf(content):
    if not content.startswith(b"%PDF") or len(content) > 20_000_000:
        raise ValueError("קובץ המקור אינו PDF תקין או גדול מ-20MB")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
        source.write(content)
        source.flush()
        return subprocess.run(["pdftotext", "-raw", source.name, "-"],
                              capture_output=True, text=True, timeout=40, check=True).stdout


def fetch_allegheny_sheriff_listings():
    """One bounded public download, with a clearly marked local import option."""
    if os.path.isfile(SHERIFF_LOCAL_PDF):
        with open(SHERIFF_LOCAL_PDF, "rb") as f:
            body = extract_sheriff_pdf(f.read(20_000_001))
        rows, audit = parse_sheriff_text(body, SHERIFF_LOCAL_PDF, imported=True)
        return rows, SHERIFF_LOCAL_PDF, audit
    headers = {"User-Agent": "PA-RealEstate-Intelligence-Hub/2.7", "Accept": "text/html,application/pdf"}
    links = []
    try:
        page = requests.get(SHERIFF_PAGE, headers=headers, timeout=(5, 15))
        page.raise_for_status()
        parser = SheriffPDFLinks()
        parser.feed(page.text)
        links = [url for url in parser.links if re.search(r"sale[^/]*list", url, re.I)]
    except requests.RequestException as exc:
        print(f"ℹ️ דף השריף אינו זמין: {exc}")
    configured_url = os.environ.get("SHERIFF_PDF_URL", "").strip()
    if configured_url:
        if not valid_sheriff_pdf_url(configured_url):
            raise ValueError("SHERIFF_PDF_URL חייב להיות קישור PDF באתר השריף הרשמי")
        pdf_url = configured_url
    elif links:
        pdf_url = links[0]
    elif now_est().date().isoformat() <= "2026-10-05":
        pdf_url = SHERIFF_KNOWN_PDF
    else:
        raise ValueError("לא נמצא קישור עדכני; אפשר לצרף PDF רשמי ב-sources/allegheny_sheriff.pdf")
    print(f"📄 מנסה PDF שריף ישיר: {pdf_url}")
    response = requests.get(pdf_url, headers=headers, timeout=(5, 30), stream=True)
    try:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > 20_000_000:
                raise ValueError("PDF גדול מ-20MB")
    finally:
        response.close()
    rows, audit = parse_sheriff_text(extract_sheriff_pdf(bytes(content)), pdf_url)
    return rows, pdf_url, audit


PROPERTY_TYPE_ALIASES = {
    "single family": "Single Family",
    "single-family": "Single Family",
    "single family residential": "Single Family",
    "house": "Single Family",
    "townhouse": "Townhouse",
    "townhome": "Townhouse",
    "condo": "Condo",
    "condo/coop": "Condo",
    "condo/co-op": "Condo",
    "co-op": "Condo",
    "coop": "Condo",
    "multi-family": "Multi-Family",
    "multifamily": "Multi-Family",
    "multi-family (5+ unit)": "Multi-Family",
    "duplex": "Duplex / Triplex",
    "triplex": "Duplex / Triplex",
    "multi-family (2-4 unit)": "Duplex / Triplex",
    "land": "Land / Lot",
    "vacant land": "Land / Lot",
    "commercial": "Commercial",
}

def normalize_property_type(raw_value):
    """Normalize source property types to the exact values used by the UI."""
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    key = re.sub(r"\s+", " ", raw.lower()).strip()
    if key in PROPERTY_TYPE_ALIASES:
        return PROPERTY_TYPE_ALIASES[key]
    if "single" in key and "family" in key:
        return "Single Family"
    if "town" in key and ("house" in key or "home" in key):
        return "Townhouse"
    if "condo" in key or "co-op" in key or "coop" in key:
        return "Condo"
    if "duplex" in key or "triplex" in key or "2-4" in key:
        return "Duplex / Triplex"
    if "multi" in key and "family" in key:
        return "Multi-Family"
    if "land" in key or "lot" in key:
        return "Land / Lot"
    if "commercial" in key:
        return "Commercial"
    return None


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
    "Allentown": {"market": "allentown", "region_id": "514", "region_type": "6"},
    "Reading": {"market": "reading", "region_id": "16305", "region_type": "6"},
    "Erie": {"market": "erie", "region_id": "6172", "region_type": "6"},
    "Scranton": {"market": "scranton", "region_id": "17652", "region_type": "6"},
    "Bethlehem": {"market": "allentown", "region_id": "1616", "region_type": "6"},
    "Lancaster": {"market": "lancaster", "region_id": "10496", "region_type": "6"},
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
    if not os.path.exists(PROPERTIES_FILE):
        return {}
    with open(PROPERTIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("properties.json אינו מערך תקין; שמירת המאגר נעצרה")

    prop_dict = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        key = property_key(item)
        if key:
            prop_dict[key] = item
    return prop_dict


def geography_rejection(prop, geo_areas):
    """Return a rejection reason for a LIVE MLS row, or None.

    Selections are scoped to the Redfin query that supplied the row. A county
    row also honors a selected city's narrower location choices so the broad
    county request cannot reintroduce a city row excluded by that choice.
    """
    if not isinstance(geo_areas, dict):
        return None  # Old scan_config.json remains backward compatible.
    area = str(prop.get("source_scan_area") or "").strip()
    city = str(prop.get("city") or "").strip()
    target = REGION_MAP.get(area) or {}
    if str(target.get("region_type")) == "6" and city.casefold() != area.casefold():
        return "city_mismatch"
    location = " ".join(str(prop.get("source_location") or "").split()).casefold()
    scopes = [geo_areas.get(area)]
    if str(target.get("region_type")) == "5" and city != area:
        scopes.append(geo_areas.get(city))
    for scope in scopes:
        if not isinstance(scope, dict) or scope.get("allLocations") is not False:
            continue
        allowed = {" ".join(str(x).split()).casefold()
                   for x in (scope.get("locations") or []) if isinstance(x, str)}
        if not location or location not in allowed:
            return "location"
    return None


def append_scan_log(entry):
    """Keep a bounded audit trail for every scanner execution."""
    log = load_json_file(SCAN_LOG_FILE, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    # Keep the file small while retaining a useful audit trail.
    log = log[-1000:]
    atomic_write_json(SCAN_LOG_FILE, log)
    report = load_json_file(SCANNER_STATUS_FILE, {})
    if not isinstance(report, dict):
        report = {}
    sources = report.get("sources", {})
    if not isinstance(sources, dict):
        sources = {}
    sources.update(entry.get("sources", {}))
    report.update({"version": ORCHESTRATOR_VERSION, "last_event": entry, "sources": sources})
    if entry.get("status") != "skipped":
        report["last_scan"] = entry
    atomic_write_json(SCANNER_STATUS_FILE, report)


def classify_strategy(deal_type, price, beds, summary=""):
    dt = (deal_type or "").lower()
    text = f"{dt} {summary}".lower()
    is_distressed = any(kw in text for kw in DISTRESS_KEYWORDS) or any(
        k in dt for k in ["sheriff", "tax", "probate", "foreclosure", "reo"]
    )
    beds_num = safe_number(beds, None, int)
    projected_rent = None
    gross_yield = None
    if beds_num is not None and price:
        base_rent = 950 + (beds_num * 250)
        projected_rent = max(900, int(base_rent + (safe_number(price, 0, float) * 0.002)))
        annual_rent = projected_rent * 12
        gross_yield = round((annual_rent / max(safe_number(price, 1, float), 1)) * 100, 1)

    if not is_distressed and safe_number(price, 0, float) >= 60000:
        return {
            "strategy": "turnkey",
            "strategy_label": "🔑 Turnkey (מניב מיידי)",
            "projected_rent": f"${projected_rent:,} / חודש" if projected_rent is not None else "לא זמין",
            "gross_yield": f"{gross_yield}% תשואה" if gross_yield is not None else "לא זמין",
        }
    return {
        "strategy": "value_add",
        "strategy_label": "🔨 Value-Add (השבחה ומצוקה)",
        "projected_rent": f"${projected_rent:,} / חודש" if projected_rent is not None else "לא זמין",
        "gross_yield": f"{gross_yield}% תשואה (לאחר שיפוץ)" if gross_yield is not None else "לא זמין",
    }


def fetch_live_mls_for_city(city_name, min_p, max_p, audit=None):
    audit = audit if audit is not None else {}
    audit.update({"area": city_name, "status": "failed", "rows": 0,
                  "coverage": "not_proven_complete"})
    clean_city = city_name.strip()
    target = REGION_MAP.get(clean_city)
    if not target:
        audit["error"] = "unsupported_area"
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
            audit.update({"status": "blocked" if resp.status_code in (401, 403) else "failed",
                          "error": f"HTTP {resp.status_code} or invalid CSV"})
            print(f"⚠️ לא נמשכו נתוני MLS תקינים עבור {clean_city}. HTTP {resp.status_code}")
            return []

        reader = csv.DictReader(io.StringIO(resp.text))
        if not {"ADDRESS", "PRICE", "CITY"}.issubset(set(reader.fieldnames or [])):
            audit["error"] = "unexpected CSV schema"
            return []
        for row in reader:
            addr = row.get("ADDRESS")
            raw_price = row.get("PRICE")
            if not addr or not raw_price:
                continue

            price = safe_number(raw_price, 0, int)
            if price <= 0:
                continue

            dom = max(0, safe_number(row.get("DAYS ON MARKET"), 0, int))

            listed_dt = now_est() - timedelta(days=dom)
            listed_date_str = listed_dt.strftime("%d/%m/%Y")
            beds = safe_number(row.get("BEDS"), None, int)
            baths = safe_number(row.get("BATHS"), None, float)
            sqft = safe_number(row.get("SQUARE FEET"), None, int)
            raw_property_type = row.get("PROPERTY TYPE") or ""
            property_type = normalize_property_type(raw_property_type)
            source_location = (row.get("LOCATION") or "").strip() or None
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
                "type": property_type,
                "property_type": property_type,
                "source_property_type": raw_property_type or None,
                "source_location": source_location,
                "source_state": (row.get("STATE OR PROVINCE") or "").strip(),
                "source_scan_area": clean_city,
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
                "market_status": "active",
            })
    except requests.RequestException as exc:
        audit["error"] = str(exc)
        print(f"⚠️ שגיאת רשת בסריקת {clean_city}: {exc}")
    except Exception as exc:
        audit["error"] = str(exc)
        print(f"⚠️ שגיאה לא צפויה בסריקת {clean_city}: {exc}")

    if "error" not in audit:
        audit.update({"status": "success", "rows": len(discovered),
                      "limit_reached": len(discovered) >= 350})
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
        "type", "property_type", "source_property_type", "source_location",
    ]
    return any(old.get(field) != new.get(field) for field in tracked_fields)


def append_status_event(history, status, timestamp, scan_id, reason=""):
    """Append a status transition only when the status actually changes."""
    if not isinstance(history, list):
        history = []
    last_status = history[-1].get("status") if history and isinstance(history[-1], dict) else None
    if last_status != status:
        event = {"date": timestamp, "status": status, "scan_id": scan_id}
        if reason:
            event["reason"] = reason
        history.append(event)
    return history


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
        merged["missing_scan_count"] = 0
        merged["market_status"] = incoming.get("market_status") or "active"
        merged["price_history"] = [{"date": timestamp, "price": incoming.get("price"), "source": incoming.get("source", "")}]
        merged["status_history"] = append_status_event([], merged["market_status"], timestamp, scan_id, "first discovery")
        return merged, "new"

    previous_market_status = existing.get("market_status") or "active"
    changed = comparable_changed(existing, incoming) or previous_market_status != "active"
    old_price = existing.get("price")
    new_price = incoming.get("price")

    merged = deepcopy(existing)
    merged.update(incoming)
    merged["first_seen"] = existing.get("first_seen") or timestamp
    merged["last_seen"] = timestamp
    merged["last_scan_id"] = scan_id
    merged["seen_count"] = safe_number(existing.get("seen_count"), 0, int) + 1
    merged["missing_scan_count"] = 0
    merged["market_status"] = "active"
    merged["scan_status"] = "updated" if changed else "unchanged"

    price_history = existing.get("price_history")
    if not isinstance(price_history, list):
        price_history = []
    if not price_history and old_price is not None:
        price_history.append({"date": existing.get("first_seen") or timestamp, "price": old_price, "source": existing.get("source", "")})
    if new_price is not None and old_price != new_price:
        price_history.append({"date": timestamp, "price": new_price, "source": incoming.get("source", "")})
    merged["price_history"] = price_history

    status_history = existing.get("status_history")
    if not isinstance(status_history, list):
        status_history = []
        status_history = append_status_event(status_history, previous_market_status, existing.get("last_seen") or timestamp, scan_id, "history initialized")
    reason = "reappeared in live MLS" if previous_market_status != "active" else "confirmed in live MLS"
    merged["status_history"] = append_status_event(status_history, "active", timestamp, scan_id, reason)

    return merged, "updated" if changed else "unchanged"


def mark_missing_mls_candidates(existing_props, raw_source_seen_keys, scanned_cities, scan_id):
    """
    Mark previously scanner-managed MLS properties that disappear from the raw live source feed
    as OFF-MARKET CANDIDATES after repeated misses. This is deliberately not called
    verified off-market: disappearance can also be caused by upstream API limits or
    listing/feed changes.
    """
    timestamp = iso_now_est()
    scanned_city_keys = {str(c).strip().lower() for c in scanned_cities}
    candidates = 0

    for key, prop in existing_props.items():
        if key in raw_source_seen_keys:
            continue
        if prop.get("source_type") != "mls" or prop.get("data_status") != "live":
            continue
        if not prop.get("last_scan_id"):
            # Legacy records are not classified from absence until this scanner has
            # positively seen them at least once. This prevents mass false positives.
            continue
        if str(prop.get("city") or "").strip().lower() not in scanned_city_keys:
            continue

        misses = safe_number(prop.get("missing_scan_count"), 0, int) + 1
        prop["missing_scan_count"] = misses
        prop["last_missing_scan_id"] = scan_id

        if misses >= OFF_MARKET_MISS_THRESHOLD and prop.get("market_status") == "active":
            prop["market_status"] = "off_market_candidate"
            prop["scan_status"] = "updated"
            prop["status_history"] = append_status_event(
                prop.get("status_history"),
                "off_market_candidate",
                timestamp,
                scan_id,
                f"not returned in {misses} consecutive live MLS scans",
            )
            candidates += 1

    return candidates


def run_orchestrator():
    scan_started = now_est()
    scan_id = scan_started.strftime("SCAN-%Y%m%d-%H%M%S")
    print(f"🚀 מתחיל ריצת מנוע סריקה מרכזי... {scan_id}")
    with open(__file__, "rb") as f:
        code_hash = hashlib.sha256(f.read()).hexdigest()[:12]
    print(f"🔧 ENGINE {ORCHESTRATOR_VERSION} | FILE {code_hash} | COMMIT {os.environ.get('GITHUB_SHA', 'local')[:12]}")

    github_event = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    is_manual_trigger = github_event == "workflow_dispatch"
    server_config = load_server_config()

    log_entry = {
        "scan_id": scan_id,
        "started_at": scan_started.isoformat(timespec="seconds"),
        "trigger": "manual" if is_manual_trigger else "scheduled",
        "orchestrator_version": ORCHESTRATOR_VERSION,
        "status": "started",
        "active_sectors": [],
        "cities": [],
        "source_results": 0,
        "after_filters": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "off_market_candidates": 0,
        "errors": [],
        "code_sha256": code_hash,
        "github_sha": os.environ.get("GITHUB_SHA", "local"),
        "request_id": server_config.get("requestId") if server_config and is_manual_trigger else None,
    }

    if not server_config:
        log_entry["status"] = "failed"
        log_entry["errors"].append("scan_config.json missing or invalid")
        log_entry["finished_at"] = iso_now_est()
        append_scan_log(log_entry)
        print("⚠️ קובץ תצורה לא נמצא או אינו תקין. מסיים ריצה.")
        return

    try:
        existing_props_dict = load_existing_properties()
    except (OSError, ValueError) as exc:
        log_entry.update({"status": "failed", "finished_at": iso_now_est()})
        log_entry["errors"].append(str(exc))
        append_scan_log(log_entry)
        print(f"❌ הסריקה נעצרה לשמירת המאגר הקיים: {exc}")
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
        previous_report = load_json_file(SCANNER_STATUS_FILE, {})
        previous_sources = previous_report.get("sources", {}) if isinstance(previous_report, dict) else {}

        for sec, sched in schedules.items():
            s_day = sched.get("day", "Everyday")
            s_time = sched.get("time", "08:00")
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", s_time):
                continue
            hour, minute = map(int, s_time.split(":"))
            due = now_est().replace(hour=hour, minute=minute, second=0, microsecond=0)
            last_checked = (previous_sources.get(sec) or {}).get("checked_at")
            try:
                already_attempted = bool(last_checked and datetime.fromisoformat(last_checked) >= due)
            except (ValueError, TypeError):
                already_attempted = False
            if now_est() >= due and not already_attempted and (s_day == "Everyday" or s_day == current_day):
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
    min_baths = safe_number(server_config.get("minBaths"), 0, float)
    if min_price > max_price or min_sqft > max_sqft or min_beds > max_beds:
        log_entry.update({"status": "failed", "finished_at": iso_now_est()})
        log_entry["errors"].append("טווח מסננים לא תקין: מינימום גדול ממקסימום")
        append_scan_log(log_entry)
        print("❌ טווח מסננים לא תקין; הסריקה נעצרה")
        return
    selected_property_types = [
        normalize_property_type(v) or str(v).strip()
        for v in (server_config.get("propertyTypes") or [])
        if str(v).strip()
    ]
    selected_property_types = list(dict.fromkeys(selected_property_types))
    configured_cities = server_config.get("cities")
    cities_list = configured_cities if isinstance(configured_cities, list) else ["Pittsburgh"]
    selected_neighborhoods = [
        str(v).strip()
        for v in (server_config.get("neighborhoods") or [])
        if str(v).strip()
    ]
    selected_neighborhoods = list(dict.fromkeys(selected_neighborhoods))

    log_entry["active_sectors"] = active_sectors_now
    log_entry["cities"] = cities_list
    log_entry["property_types"] = selected_property_types
    log_entry["min_baths"] = min_baths
    log_entry["market_state_basis"] = "raw_live_mls_before_user_filters"
    log_entry["selected_neighborhoods"] = selected_neighborhoods
    selected_geo_areas = server_config.get("geoAreas")
    if isinstance(selected_geo_areas, dict):
        selected_geo_areas = {area: spec for area, spec in selected_geo_areas.items()
                              if area in cities_list and isinstance(spec, dict)}
        log_entry["neighborhood_filter_status"] = "enforced_per_scan_area"
    else:
        selected_geo_areas = None
        log_entry["neighborhood_filter_status"] = "audit_only_legacy_config"

    print(f"🎯 אזורי יעד: {cities_list}")
    print(f"🎯 מחיר: {min_price:g}-{max_price:g} | SqFt: {min_sqft}-{max_sqft} | Beds: {min_beds}-{max_beds} | Baths min: {min_baths:g}")
    print(f"🏠 סוגי נכסים: {selected_property_types or ['הכל']}")
    print(f"🗺️ שכונות שנבחרו בממשק: {len(selected_neighborhoods)} | "
          f"סינון: {log_entry['neighborhood_filter_status']}")
    print(f"📋 סקטורים פעילים: {active_sectors_now}")

    sources = {}
    for sector in active_sectors_now:
        sources[sector] = {"label": SOURCE_LABELS.get(sector, sector), "checked_at": iso_now_est(),
                           "status": "pending" if sector in ("mls", "sheriff") else "not_connected",
                           "rows": 0}
    log_entry["sources"] = sources
    # These sectors have no verified adapter yet, regardless of a checkbox.
    for sector in ("reo", "tax", "06_probate_estates"):
        sources.setdefault(sector, {"label": SOURCE_LABELS[sector], "status": "not_connected", "rows": 0})

    if "sheriff" in active_sectors_now and "Allegheny" in cities_list:
        try:
            sheriff_rows, sheriff_pdf, sheriff_audit = fetch_allegheny_sheriff_listings()
            atomic_write_json(SHERIFF_FILE, sheriff_rows)
            sources["sheriff"].update({"status": "imported" if sheriff_audit["mode"] == "imported" else "success",
                                        "rows": len(sheriff_rows), "source_url": sheriff_pdf,
                                        "investment_filters": "unavailable", **sheriff_audit})
            if sheriff_audit["skipped_active_addresses"]:
                sources["sheriff"]["status"] = "partial"
            log_entry["sheriff_active_lots"] = len(sheriff_rows)
            log_entry["sheriff_source_url"] = sheriff_pdf
            print(f"⚖️ שריף Allegheny: {len(sheriff_rows)} רשומות פעילות מתאריך מכירה עתידי; מקור: {sheriff_pdf}")
        except (requests.RequestException, OSError, ValueError, subprocess.SubprocessError) as exc:
            response = getattr(exc, "response", None)
            blocked = response is not None and response.status_code in (401, 403)
            sources["sheriff"].update({"status": "blocked" if blocked else "failed", "error": str(exc),
                                        "cached_results": True})
            log_entry["errors"].append(f"sheriff list unavailable: {exc}")
            print(f"⚠️ רשימת השריף לא עודכנה: {exc}")
    elif "sheriff" in active_sectors_now:
        sources["sheriff"].update({"status": "unsupported_area", "error": "כרגע חיבור השריף תומך במחוז Allegheny בלבד"})

    live_results = []
    if "mls" in active_sectors_now:
        mls_audits = []
        for city in cities_list:
            area_audit = {}
            live_results.extend(fetch_live_mls_for_city(city, min_price, max_price, area_audit))
            mls_audits.append(area_audit)
        good = sum(a.get("status") == "success" for a in mls_audits)
        sources["mls"].update({"status": "success" if good == len(mls_audits) and good else "partial" if good else "failed",
                                 "rows": len(live_results), "areas": mls_audits,
                                 "coverage": "not_proven_complete"})
        for item in mls_audits:
            if item.get("error"):
                log_entry["errors"].append(f"MLS {item['area']}: {item['error']}")

    # Geography QA: prove what each configured Redfin region actually returned.
    geography_qa = {}
    for area in cities_list:
        area_rows = [p for p in live_results if p.get("source_scan_area") == area]
        target = REGION_MAP.get(area) or {}
        actual_cities = {}
        source_locations = {}
        city_mismatch_count = 0

        for p in area_rows:
            actual_city = str(p.get("city") or "UNKNOWN").strip() or "UNKNOWN"
            actual_cities[actual_city] = actual_cities.get(actual_city, 0) + 1
            loc = str(p.get("source_location") or "UNKNOWN").strip() or "UNKNOWN"
            source_locations[loc] = source_locations.get(loc, 0) + 1

            # region_type 6 is a city query; type 5 (Allegheny) is intentionally broader.
            if str(target.get("region_type")) == "6" and actual_city.lower() != area.lower():
                city_mismatch_count += 1

        geography_qa[area] = {
            "region_type": target.get("region_type"),
            "rows": len(area_rows),
            "city_mismatch_count": city_mismatch_count,
            "actual_cities": actual_cities,
            "source_locations": source_locations,
        }
        print(
            f"🌎 GEO QA — {area}: {len(area_rows)} rows | "
            f"city mismatches: {city_mismatch_count} | actual cities: {actual_cities}"
        )

    log_entry["geography_qa"] = geography_qa

    # Keep the complete set of source LOCATION values seen before investment filters.
    # This catalog only describes the regions that were actually scanned.
    catalog = load_json_file(GEO_CATALOG_FILE, {})
    if not isinstance(catalog, dict):
        catalog = {}
    areas = catalog.get("areas")
    if not isinstance(areas, dict):
        areas = {}
    # Discard catalog entries collected with the old, incorrect city IDs.
    corrected_cities = {"Allentown", "Reading", "Erie", "Scranton", "Bethlehem", "Lancaster"}
    for area in corrected_cities:
        old = areas.get(area)
        if isinstance(old, dict) and old.get("region_id") != REGION_MAP[area]["region_id"]:
            del areas[area]
    for area in cities_list:
        target = REGION_MAP.get(area) or {}
        rows = [p for p in live_results if p.get("source_scan_area") == area
                and (not p.get("source_state") or p["source_state"].upper() == "PA")
                and (str(target.get("region_type")) != "6"
                     or str(p.get("city") or "").strip().casefold() == area.casefold())]
        if not rows:
            continue  # A failed/empty request must not erase previously discovered locations.
        locations = {str(p.get("source_location") or "").strip() for p in rows}
        locations.discard("")
        old = areas.get(area) or {}
        previous = old.get("locations") if isinstance(old, dict) else []
        areas[area] = {
            "locations": sorted(set(previous or []) | locations, key=str.casefold),
            "region_id": target.get("region_id"),
            "last_seen": iso_now_est(),
        }
    if areas:
        try:
            atomic_write_json(GEO_CATALOG_FILE, {"version": 1, "areas": areas})
            print(f"🗺️ קטלוג אזורים עודכן: {sum(len(v['locations']) for v in areas.values())} שמות מהמקור")
        except OSError as exc:
            log_entry["errors"].append(f"geo catalog write failed: {exc}")
            print(f"⚠️ שמירת קטלוג האזורים נכשלה: {exc}")

    combined = live_results + get_placeholder_sector_results(
        [s for s in active_sectors_now if s != "sheriff" or "Allegheny" not in cities_list]
    )
    log_entry["source_results"] = len(combined)

    # Market presence must be based on the raw LIVE MLS response, before the
    # user's investment filters. A filter change must never create fake
    # Off-Market candidates.
    raw_mls_seen_keys = {
        property_key(prop)
        for prop in live_results
        if property_key(prop)
    }
    log_entry["raw_mls_seen"] = len(raw_mls_seen_keys)

    final_filtered = []
    filter_rejections = {
        "price": 0, "sqft": 0, "beds": 0, "baths": 0,
        "property_type": 0, "property_type_unknown": 0,
        "city_mismatch": 0, "location": 0,
    }
    source_type_counts = {}

    for prop in combined:
        p_price = safe_number(prop.get("price"), None, float)
        p_sqft = safe_number(prop.get("sqft"), None, int)
        p_beds = safe_number(prop.get("beds"), None, int)
        p_baths = safe_number(prop.get("baths"), None, float)
        p_type = prop.get("property_type") or prop.get("type")

        raw_type = str(prop.get("source_property_type") or "UNKNOWN").strip() or "UNKNOWN"
        source_type_counts[raw_type] = source_type_counts.get(raw_type, 0) + 1

        if prop.get("source_type") == "mls" and selected_geo_areas is not None:
            reason = geography_rejection(prop, selected_geo_areas)
            if reason:
                filter_rejections[reason] += 1
                continue

        if p_price is None or not (min_price <= p_price <= max_price):
            filter_rejections["price"] += 1
            continue
        if min_sqft > 0 and (p_sqft is None or p_sqft < min_sqft):
            filter_rejections["sqft"] += 1
            continue
        if max_sqft < 99999 and (p_sqft is None or p_sqft > max_sqft):
            filter_rejections["sqft"] += 1
            continue
        if min_beds > 0 and (p_beds is None or p_beds < min_beds):
            filter_rejections["beds"] += 1
            continue
        if max_beds < 99 and (p_beds is None or p_beds > max_beds):
            filter_rejections["beds"] += 1
            continue
        if min_baths > 0 and (p_baths is None or p_baths < min_baths):
            filter_rejections["baths"] += 1
            continue
        if selected_property_types:
            if not p_type:
                filter_rejections["property_type_unknown"] += 1
                continue
            if p_type not in selected_property_types:
                filter_rejections["property_type"] += 1
                continue

        final_filtered.append(prop)

    # A county feed and its city feed overlap. Merge once per property per run,
    # preferring the city query when both passed their geography filters.
    unique_results = {}
    for prop in final_filtered:
        key = property_key(prop)
        old = unique_results.get(key)
        is_city = REGION_MAP.get(prop.get("source_scan_area"), {}).get("region_type") == "6"
        if old is None or is_city:
            unique_results[key] = prop
    log_entry["duplicates_removed"] = len(final_filtered) - len(unique_results)
    final_filtered = list(unique_results.values())
    log_entry["after_filters"] = len(final_filtered)
    log_entry["filter_rejections"] = filter_rejections
    log_entry["source_property_type_counts"] = source_type_counts
    location_counts = {}
    for prop in live_results:
        loc = str(prop.get("source_location") or "UNKNOWN").strip() or "UNKNOWN"
        location_counts[loc] = location_counts.get(loc, 0) + 1
    top_locations = sorted(location_counts.items(), key=lambda x: (-x[1], x[0]))[:25]
    log_entry["redfin_location_top25"] = dict(top_locations)
    print(f"📍 GEO QA — ערכי LOCATION מובילים מ-Redfin: {dict(top_locations)}")
    print(f"👁️ MLS MARKET STATE — נצפו במקור LIVE לפני מסננים: {len(raw_mls_seen_keys)}")
    print(f"🧪 MLS QA — דחיות לפי מסנן: {filter_rejections}")
    print(f"🏷️ MLS QA — סוגי נכס מהמקור: {source_type_counts}")
    print(f"🔍 {len(final_filtered)} תוצאות עברו את כל המסננים. מבצע מיזוג בטוח...")

    seen_keys = set()
    for deal in final_filtered:
        key = property_key(deal)
        if not key:
            print(f"⚠️ תוצאה ללא מזהה/כתובת דולגה: {deal.get('id', 'unknown')}")
            continue

        seen_keys.add(key)
        existing = existing_props_dict.get(key)
        merged, state = merge_property(existing, deal, scan_id)
        existing_props_dict[key] = merged
        log_entry[state] += 1

    # Price-scoped/capped Redfin exports are not evidence that a listing went
    # off market. Only a future explicit listing-status source may do that.
    log_entry["off_market_detection"] = "disabled_without_verified_listing_status"
    for key in raw_mls_seen_keys:
        observed = existing_props_dict.get(key)
        if observed and observed.get("source_type") == "mls":
            observed["missing_scan_count"] = 0
            if observed.get("market_status") == "off_market_candidate":
                observed["market_status"] = "active"
                observed["status_history"] = append_status_event(observed.get("status_history"), "active", iso_now_est(), scan_id, "observed in live source before investment filters")
    log_entry["sources"]["offmarket"] = {
        "label": "OFF MARKET", "status": "needs_verification", "checked_at": iso_now_est(),
        "detail": "הרשימה הקיימת כוללת מועמדים היסטוריים; לא נוצרים מועמדים מהיעדרות בסריקה חלקית"}

    final_merged_list = list(existing_props_dict.values())
    final_merged_list.sort(
        key=lambda x: (safe_number(x.get("deal_score"), 0, int), x.get("last_seen", "")),
        reverse=True,
    )

    try:
        atomic_write_json(PROPERTIES_FILE, final_merged_list)
        source_states = [sources[s].get("status") for s in active_sectors_now if s in sources]
        bad = {"failed", "blocked", "partial", "not_connected", "unsupported_area"}
        log_entry["status"] = "partial" if any(s in bad for s in source_states) else "success"
        if source_states and all(s in {"failed", "blocked", "not_connected", "unsupported_area"} for s in source_states):
            log_entry["status"] = "failed"
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
        f"{log_entry['off_market_candidates']} מועמדי Off Market | "
        f"סה״כ במאגר: {len(final_merged_list)}"
    )
    print(f"🧾 רישום הסריקה נשמר ב-{SCAN_LOG_FILE}")
    print(f"📋 סטטוס כולל: {log_entry['status']} | כפילויות הוסרו: {log_entry['duplicates_removed']}")
    for source in sources.values():
        print(f"   {source['label']}: {source['status']}")
    if log_entry["status"] in ("partial", "failed") and os.environ.get("GITHUB_ACTIONS"):
        print("::warning::One or more selected sources did not complete. See scanner_status.json.")


if __name__ == "__main__":
    run_orchestrator()
    # Analyzer runs only after an MLS scan that actually changed properties.
    if os.environ.get("GITHUB_OUTPUT"):
        latest = load_json_file(SCANNER_STATUS_FILE, {}).get("last_event", {})
        analyze = bool(latest.get("status") in ("success", "partial") and
                       (latest.get("new", 0) or latest.get("updated", 0)))
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"analyze={'true' if analyze else 'false'}\n")
