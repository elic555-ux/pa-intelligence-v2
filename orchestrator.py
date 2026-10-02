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
import posixpath
import zipfile
import xml.etree.ElementTree as ET
import concurrent.futures
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlencode
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
SHERIFF_PROPERTY_CACHE_FILE = "sheriff_property_cache.json"
OFF_MARKET_MISS_THRESHOLD = 2
ORCHESTRATOR_VERSION = "3.6.3-reo-empty-scope-guard-20261002"
SCANNER_STATUS_FILE = "scanner_status.json"
SOURCE_LABELS = {"mls": "MLS", "reo": "בנקים וכינוס", "sheriff": "מכירות שריף",
                 "tax": "חובות מס", "06_probate_estates": "עיזבונות ופרטי"}

SHERIFF_PAGE = "https://sheriffalleghenycounty.com/sheriffs-sales/"
SHERIFF_LOCAL_PDF = "sources/allegheny_sheriff.pdf"
SHERIFF_BUNDLED_PDF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "October-Sale-List-Updated-9-24.pdf")
# Edition discovered on the official page. It expires; it is not a permanent feed.
SHERIFF_KNOWN_PDF = "https://sheriffalleghenycounty.com/wp-content/uploads/2026/09/October-Sale-List-Updated-9-24.pdf"
ERIE_SHERIFF_URL = "https://public.eriecountypa.gov/sheriffsalelisting/"
LEHIGH_SHERIFF_URL = "https://salesweb.civilview.com/Sales/SalesSearch?countyId=51"
HOMESTEPS_SEARCH_URL = "https://www.homesteps.com/listing/search?search=Pennsylvania"
FANNIE_HOME_PATH_URL = "https://homepath.fanniemae.com/property-finder"
BANK_OF_AMERICA_REO_URL = "https://foreclosures.bankofamerica.com/pennsylvania"
HUD_HOME_STORE_SEARCH_URL = "https://www.hudhomestore.gov/searchresult"
LEHIGH_TAX_SALE_PAGE = "https://www.lehighcountytaxclaim.com/"
ERIE_TAX_SALE_PAGE = "https://eriecountypa.gov/departments/tax-claim-and-revenue/tax-sales/"


def _xlsx_cell_text(cell, shared_strings, ns):
    """Read one XLSX cell using only the Python standard library."""
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", ns)).strip()
    value = cell.find("m:v", ns)
    if value is None or value.text is None:
        return ""
    raw = value.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return ""
    return raw.strip()


def parse_erie_repository_xlsx(content, source_url):
    """Parse Erie County's published repository workbook, excluding unavailable lots."""
    if not content.startswith(b"PK") or len(content) > 10_000_000:
        raise ValueError("Erie repository source is not a valid XLSX or exceeds the size limit")
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
                raise ValueError("XLSX workbook structure is incomplete")
            shared_strings = []
            if "xl/sharedStrings.xml" in names:
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = ["".join(node.text or "" for node in item.findall(".//m:t", ns))
                                  for item in shared_root.findall("m:si", ns)]
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            first_sheet = workbook.find("m:sheets/m:sheet", ns)
            if first_sheet is None:
                raise ValueError("XLSX contains no worksheets")
            relationship_id = first_sheet.attrib.get("{" + ns["r"] + "}id")
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            target = next((item.attrib.get("Target") for item in relationships.findall("rel:Relationship", ns)
                           if item.attrib.get("Id") == relationship_id), None)
            if not target:
                raise ValueError("XLSX first worksheet could not be located")
            sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            if sheet_path not in names:
                raise ValueError("XLSX worksheet file is missing")
            sheet = ET.fromstring(archive.read(sheet_path))
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        raise ValueError(f"Erie repository workbook could not be read: {exc}") from exc

    rows = []
    header_map = None
    for row_node in sheet.findall(".//m:sheetData/m:row", ns):
        values = {}
        for cell in row_node.findall("m:c", ns):
            match = re.match(r"([A-Z]+)", cell.attrib.get("r", ""))
            if not match:
                continue
            column = 0
            for char in match.group(1):
                column = column * 26 + ord(char) - 64
            values[column] = _xlsx_cell_text(cell, shared_strings, ns)
        if not values:
            continue
        if header_map is None:
            normalized = {re.sub(r"\s+", " ", value).strip().casefold(): col
                          for col, value in values.items() if value}
            if "parcel number" in normalized and "property location/description" in normalized:
                header_map = normalized
            continue
        parcel = values.get(header_map.get("parcel number", -1), "").strip()
        location = re.sub(r"\s+", " ", values.get(header_map.get("property location/description", -1), "")).strip()
        docket = values.get(header_map.get("judical sale docket #", -1), "").strip()
        status = values.get(header_map.get("current status", -1), "").strip()
        if not parcel and not location:
            continue
        if not re.fullmatch(r"\d{2}-\d{3}-\d{3}\.\d(?:-\d{3}\.\d{2})?", parcel) or not location:
            continue
        unavailable = re.search(r"pending|held|removed|sold|unavailable", status, re.I)
        unavailable = unavailable or re.search(r"\(\s*HELD\b", location, re.I)
        if unavailable:
            continue
        safe_parcel = re.sub(r"[^A-Za-z0-9]", "", parcel)
        docket = docket or f"Repository-{safe_parcel}"
        rows.append({
            "id": f"tax-erie-repository-{safe_parcel}", "county": "Erie",
            "city": "Erie County", "address": location, "zip": None,
            "parcel_id": parcel, "sale_number": docket, "owner_name": None,
            "source_type": "tax", "deal_type": "Tax Repository Candidate",
            "market_status": "repository_bid_candidate", "tax_sale_type": "repository",
            "opening_bid": None, "minimum_bid": 250.0, "price": None,
            "source_url": source_url, "source_text_quality": "official_county_xlsx",
            "repository_status": status or "לא מצוין בקובץ",
            "description": ("מועמד לרשימת Repository של Erie County; הצעה מינימלית שמצוינת בכותרת המקור: $250, "
                            "אינה מחיר נכס או הצעת רכישה. הרשימה משתנה ויש לאמת זמינות ישירות מול לשכת המס.")
        })
    if header_map is None:
        raise ValueError("Erie repository workbook headers changed; no rows were imported")
    return rows


def fetch_erie_repository_list():
    """Download the current Erie County repository workbook from its official page."""
    headers = {"User-Agent": "PA-Property-Research/1.0"}
    page = requests.get(ERIE_TAX_SALE_PAGE, timeout=35, headers=headers)
    page.raise_for_status()
    if urlparse(page.url).hostname not in {"eriecountypa.gov", "www.eriecountypa.gov"}:
        raise ValueError("Erie Tax Sales page redirected away from the official county site")
    parser = OfficialSaleLinks()
    parser.feed(page.text)
    candidates = [(urljoin(page.url, href), label) for href, label in parser.links
                  if href and "repository list" in label.casefold()
                  and urlparse(urljoin(page.url, href)).path.casefold().endswith(".xlsx")]
    if not candidates:
        raise ValueError("No official Erie County Repository XLSX link was found")
    workbook_url, label = candidates[0]
    parsed = urlparse(workbook_url)
    if parsed.scheme != "https" or parsed.hostname not in {"eriecountypa.gov", "www.eriecountypa.gov"}:
        raise ValueError("Erie Repository workbook is outside the official county domain")
    response = requests.get(workbook_url, timeout=45, headers=headers)
    response.raise_for_status()
    rows = parse_erie_repository_xlsx(response.content, workbook_url)
    if not rows:
        raise ValueError("Erie repository workbook had no eligible rows; existing tax data was left untouched")
    audit = {"status": "success", "rows": len(rows), "source_url": workbook_url,
             "page_url": ERIE_TAX_SALE_PAGE, "list_label": label,
             "excluded_unavailable": True, "note": "Repository candidates only; availability must be confirmed with Erie County."}
    return rows, audit


class OfficialSaleLinks(HTMLParser):
    """Find the current judicial-sale list link from the county's official page."""
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._parts = []

    def handle_data(self, data):
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", " ".join(self._parts)).strip()))
            self._href = None
            self._parts = []


def _lehigh_ocr_row_text(words, anchor_y, x_min, x_max, tolerance):
    selected = [(x, token) for x, y, token in words
                if abs(y - anchor_y) <= tolerance and x_min <= x < x_max]
    return " ".join(token for _, token in sorted(selected))


def parse_lehigh_judicial_sale_tsv(tsv, source_url, sale_date):
    """Parse the scanned official list by OCR coordinates; reject incomplete rows."""
    lines = tsv.splitlines()
    if not lines:
        return []
    try:
        header = lines[0].split("\t")
        page_width = int(next(row.split("\t")[8] for row in lines[1:]
                              if row.split("\t")[0] == "1"))
        page_height = int(next(row.split("\t")[9] for row in lines[1:]
                               if row.split("\t")[0] == "1"))
    except (StopIteration, ValueError, IndexError):
        return []

    words = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        try:
            left, top, width, height = map(int, cols[6:10])
        except ValueError:
            continue
        token = cols[11].strip()
        if not token:
            continue
        # PDF page 3 is rotated. Transform the word centers into upright coordinates.
        x_upright = top + height / 2
        y_upright = page_width - (left + width / 2)
        words.append((x_upright, y_upright, token))

    anchors = []
    for x, y, token in words:
        cleaned = token.rstrip(".,:;]")
        if 0.10 * page_width <= x <= 0.20 * page_width and re.fullmatch(r"25-\d{4}", cleaned):
            anchors.append((y, cleaned))
    anchors.sort()
    if len(anchors) < 1 or len(anchors) > 100:
        return []
    gaps = [anchors[i + 1][0] - anchors[i][0] for i in range(len(anchors) - 1)
            if anchors[i + 1][0] > anchors[i][0]]
    tolerance = max(18, min(45, (sorted(gaps)[len(gaps) // 2] * 0.48) if gaps else 36))

    rows = []
    for y, sale_number in anchors:
        def col(lo, hi):
            return _lehigh_ocr_row_text(words, y, lo * page_height, hi * page_height, tolerance)
        municipality = col(.16, .33).replace(" TOWNSHIP", " Township").replace(" CITY OF ", "City of ")
        parcel_text = col(.33, .50).replace("]", "1").replace("[", "1")
        owner = col(.50, .68)
        address = col(.68, .84)
        bid_text = col(.84, 1.05)
        parcel_match = re.search(r"\b\d{2}-\d{12,14}-\d{1,2}\b", parcel_text)
        # Keep only plausible street addresses and a valid numeric opening bid.
        address = re.sub(r"\s+", " ", address).strip(" ,.;")
        bid_match = re.search(r"\$?\s*(\d{1,3}(?:,\d{3})*\.\d{2})", bid_text)
        if not municipality or not parcel_match or not re.match(r"^\d{1,6}\s+\S+", address) or not bid_match:
            continue
        municipality_clean = re.sub(r"\s+", " ", municipality).strip()
        municipality_clean = re.sub(r"^LOWER Township MACUNGIE$", "LOWER MACUNGIE Township", municipality_clean, flags=re.I)
        municipality_clean = re.sub(r"^UPPER Township MACUNGIE$", "UPPER MACUNGIE Township", municipality_clean, flags=re.I)
        city = municipality_clean
        if municipality_clean.casefold().startswith("city of "):
            city = municipality_clean[8:].strip().title()
        else:
            city = municipality_clean.title()
        rows.append({
            "id": f"tax-lehigh-{sale_number}", "county": "Lehigh", "city": city,
            "municipality": municipality_clean, "address": address.title(), "zip": None,
            "parcel_id": parcel_match.group(0), "sale_number": sale_number,
            "owner_name": re.sub(r"\s+", " ", owner).strip(),
            "source_type": "tax", "deal_type": "Tax Sale Candidate",
            "market_status": "scheduled_tax_sale", "tax_sale_type": "judicial",
            "sale_date": sale_date.isoformat(), "opening_bid": float(bid_match.group(1).replace(",", "")),
            "price": None, "source_url": source_url,
            "source_text_quality": "ocr_from_official_scanned_pdf",
            "description": "מועמד למכירה שיפוטית; מחיר הפתיחה אינו מחיר רכישה סופי. יש לאמת מול המסמך הרשמי ולבדוק שעבודים, מיסים, מצב הנכס ועלויות נוספות.",
        })
    return rows


def fetch_lehigh_judicial_tax_list(today=None):
    """Read only the current, future-dated Lehigh Judicial Sale list."""
    page = requests.get(LEHIGH_TAX_SALE_PAGE, timeout=35, headers={"User-Agent": "PA-Property-Research/1.0"})
    page.raise_for_status()
    if urlparse(page.url).hostname not in {"www.lehighcountytaxclaim.com", "lehighcountytaxclaim.com"}:
        raise ValueError("Lehigh tax source redirected away from the official county contractor")
    html_text = page.text
    text = html.unescape(re.sub(r"<[^>]+>", " ", html_text))
    text = re.sub(r"\s+", " ", text)
    notice = re.search(r"Judicial Sale Continued to ([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text, re.I)
    if not notice:
        raise ValueError("Official page does not publish a recognizable current Judicial Sale date")
    sale_date = datetime.strptime(" ".join(notice.groups()), "%B %d %Y").date()
    today = today or now_est().date()
    if sale_date < today:
        return [], {"status": "success", "rows": 0, "reason": "official_sale_date_not_future",
                    "sale_date": sale_date.isoformat(), "source_url": LEHIGH_TAX_SALE_PAGE}

    parser = OfficialSaleLinks()
    parser.feed(html_text)
    candidates = [(urljoin(LEHIGH_TAX_SALE_PAGE, href), label)
                  for href, label in parser.links
                  if href and re.search(r"Judicial Sale\s*List as of", label, re.I)
                  and urlparse(urljoin(LEHIGH_TAX_SALE_PAGE, href)).path.casefold().endswith(".pdf")]
    if not candidates:
        raise ValueError("No official current Judicial Sale List PDF link found")
    pdf_url, pdf_label = candidates[0]
    parsed_url = urlparse(pdf_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "www.lehighcountytaxclaim.com":
        raise ValueError("Judicial Sale PDF link is outside the official Lehigh source")
    pdf_response = requests.get(pdf_url, timeout=45, headers={"User-Agent": "PA-Property-Research/1.0"})
    pdf_response.raise_for_status()
    if not pdf_response.content.startswith(b"%PDF-") or len(pdf_response.content) > 15_000_000:
        raise ValueError("Official Judicial Sale document is not a valid PDF or exceeds the safe size limit")

    rows = []
    with tempfile.TemporaryDirectory(prefix="lehigh-tax-") as temp_dir:
        pdf_path = os.path.join(temp_dir, "sale.pdf")
        with open(pdf_path, "wb") as handle:
            handle.write(pdf_response.content)
        subprocess.run(["pdftoppm", "-png", "-r", "220", "-f", "1", "-l", "5", pdf_path,
                        os.path.join(temp_dir, "page")], check=True, capture_output=True, text=True, timeout=60)
        pages = sorted(name for name in os.listdir(temp_dir) if name.endswith(".png"))
        for image_path in pages:
            ocr = subprocess.run(["tesseract", os.path.join(temp_dir, image_path), "stdout", "tsv", "--psm", "11"],
                                 check=True, capture_output=True, text=True, timeout=60)
            rows.extend(parse_lehigh_judicial_sale_tsv(ocr.stdout, pdf_url, sale_date))
    unique = {}
    for row in rows:
        unique[row["sale_number"]] = row
    rows = list(unique.values())
    if not rows:
        raise ValueError("OCR found no complete sale rows; existing tax data was left untouched")
    audit = {"status": "success", "rows": len(rows), "sale_date": sale_date.isoformat(),
             "list_label": pdf_label, "source_url": pdf_url, "ocr": True,
             "note": "Opening bids only; OCR-derived fields require checking against the linked official PDF."}
    return rows, audit


class JsonLdScripts(HTMLParser):
    """Collect JSON-LD script bodies without depending on BeautifulSoup."""
    def __init__(self):
        super().__init__()
        self.scripts = []
        self._collect = False
        self._parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._collect = True
            self._parts = []

    def handle_data(self, data):
        if self._collect:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._collect:
            self.scripts.append("".join(self._parts))
            self._parts = []
            self._collect = False


def _jsonld_walk(value):
    """Yield dictionaries from JSON-LD graphs and arrays."""
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_walk(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in value:
                yield from _jsonld_walk(value[key])


def _schema_types(value):
    raw = value.get("@type", []) if isinstance(value, dict) else []
    return {str(item).casefold() for item in (raw if isinstance(raw, list) else [raw])}


def parse_homesteps_listings(page_html, source_url=HOMESTEPS_SEARCH_URL):
    """Parse only official, active Freddie Mac HomeSteps listings in PA."""
    parser = JsonLdScripts()
    parser.feed(page_html)
    found, rows, seen = 0, [], set()
    status_counts = {}
    for body in parser.scripts:
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            continue
        for listing in _jsonld_walk(payload):
            if "realestatelisting" not in _schema_types(listing):
                continue
            found += 1
            status = ""
            for prop in listing.get("additionalProperty", []) or []:
                if isinstance(prop, dict) and str(prop.get("name", "")).strip().casefold() == "status":
                    status = str(prop.get("value") or "").strip()
                    break
            status_counts[status or "UNKNOWN"] = status_counts.get(status or "UNKNOWN", 0) + 1
            if status.casefold() != "active":
                continue

            loc = listing.get("@location") or listing.get("location") or {}
            address_obj = loc.get("address") or {}
            if not isinstance(address_obj, dict):
                continue
            street = re.sub(r"\s+", " ", str(address_obj.get("streetAddress") or "")).strip()
            city = re.sub(r"\s+", " ", str(address_obj.get("addressLocality") or "")).strip()
            state = str(address_obj.get("addressRegion") or "").strip().upper()
            zip_code = str(address_obj.get("postalCode") or "").strip()
            canonical_url = str(listing.get("url") or "").strip()
            if not street or not city or state not in {"PA", "PENNSYLVANIA"} or not re.fullmatch(r"\d{5}(?:-\d{4})?", zip_code):
                continue
            if canonical_url:
                parsed_url = urlparse(canonical_url)
                if parsed_url.scheme != "https" or parsed_url.hostname not in {"www.homesteps.com", "homesteps.com"}:
                    continue
            key = normalize_addr_key(street, city, zip_code)
            if not key or key in seen:
                continue
            seen.add(key)

            offer = listing.get("offers") or {}
            item = offer.get("itemOffered") or {}
            if isinstance(item, list):
                item = item[0] if item else {}
            raw_price = str(offer.get("price") or "")
            price = safe_number(re.sub(r"[^0-9.]", "", raw_price), None, float)
            if price is None or price <= 0:
                continue
            beds = safe_number(item.get("numberOfBedrooms"), None, int)
            baths = safe_number(item.get("numberOfBathroomsTotal"), None, float)
            floor_size = item.get("floorSize") or listing.get("floorSize") or {}
            if isinstance(floor_size, dict):
                sqft_raw = floor_size.get("value") or floor_size.get("minValue")
            else:
                sqft_raw = floor_size
            sqft = safe_number(sqft_raw, None, int)
            raw_type = item.get("accommodationCategory") or item.get("@type") or ""
            property_type = normalize_property_type(raw_type)
            city_county = {
                "York": "York", "Philadelphia": "Philadelphia", "Pittsburgh": "Allegheny",
                "Erie": "Erie", "Allentown": "Lehigh", "Reading": "Berks",
                "Scranton": "Lackawanna", "Bethlehem": "Northampton", "Lancaster": "Lancaster",
            }.get(city)
            county = (city_county + " County") if city_county else ""
            row_id = "PA-REO-FREDDIEMAC-" + hashlib.sha256(key.encode()).hexdigest()[:20]
            rows.append({
                "id": row_id, "docket_id": row_id, "address": street, "city": city,
                "county": county, "zip": zip_code, "price": price,
                "deal_type": "Freddie Mac REO", "source": "Freddie Mac HomeSteps",
                "source_type": "reo", "data_status": "live",
                "type": property_type, "property_type": property_type,
                "source_property_type": raw_type or None,
                "beds": beds, "baths": baths, "sqft": sqft,
                "url": canonical_url or source_url,
                "summary": f"נכס REO פעיל שמופיע באתר Freddie Mac HomeSteps. מחיר מבוקש ${price:,.0f}.",
                "market_status": "active", "reo_provider": "freddie_mac_homesteps",
                "last_source_check": iso_now_est(),
            })
    return rows, {"jsonld_listings": found, "status_counts": status_counts,
                  "active_rows": len(rows), "source_url": source_url}


def fetch_homesteps_reo():
    headers = {"User-Agent": USER_AGENTS[0], "Accept": "text/html,application/xhtml+xml"}
    response = requests.get(HOMESTEPS_SEARCH_URL, headers=headers, timeout=30)
    response.raise_for_status()
    if len(response.content) > 8_000_000:
        raise ValueError("HomeSteps response exceeded 8 MB safety limit")
    rows, audit = parse_homesteps_listings(response.text, response.url)
    page_text = response.text.casefold()
    valid_empty_result_page = ("homesteps" in page_text and
                               "search our homes" in page_text and
                               urlparse(response.url).hostname in {"www.homesteps.com", "homesteps.com"})
    if audit["jsonld_listings"] == 0 and not valid_empty_result_page:
        raise ValueError("HomeSteps result schema changed: no RealEstateListing JSON-LD records")
    # One provider is a verified start, not complete coverage of bank-owned stock.
    audit["provider"] = "freddie_mac_homesteps"
    audit["coverage"] = "partial_single_provider"
    audit["status"] = "partial"
    return rows, audit


class HUDHomeStoreResultsParser(HTMLParser):
    """Read HUD's public search-result JSON embedded in its official page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.available_properties = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input":
            return
        attrs = dict(attrs)
        if attrs.get("id") == "available_prop":
            self.available_properties = attrs.get("value")


def parse_hud_homestore_listings(page_html, county, source_url):
    """Parse active, priced HUD-owned listings for a single PA county."""
    parser = HUDHomeStoreResultsParser()
    parser.feed(page_html)
    if not parser.available_properties:
        raise ValueError("HUD Home Store response has no available_prop result payload")
    try:
        records = json.loads(parser.available_properties)
    except (TypeError, ValueError) as exc:
        raise ValueError("HUD Home Store available_prop payload is invalid JSON") from exc
    if not isinstance(records, list):
        raise ValueError("HUD Home Store available_prop payload is not a list")

    today = now_est().date()
    rows, seen, excluded = [], set(), {"wrong_county_or_state": 0, "missing_required_fields": 0,
                                      "past_bid_deadline": 0, "invalid_price": 0}
    for item in records:
        if not isinstance(item, dict):
            continue
        item_county = re.sub(r"\s+", " ", str(item.get("propertyCounty") or "")).strip()
        state = str(item.get("propertyState") or "").strip().upper()
        if state != "PA" or item_county.casefold() != county.casefold():
            excluded["wrong_county_or_state"] += 1
            continue

        case_number = re.sub(r"\s+", "", str(item.get("propertyCaseNumber") or ""))
        street = re.sub(r"\s+", " ", str(item.get("propertyAddress") or "")).strip()
        city = re.sub(r"\s+", " ", str(item.get("propertyCity") or "")).strip()
        zip_code = str(item.get("propertyZip") or "").strip()
        if not case_number or not street or not city or not re.fullmatch(r"\d{5}(?:-\d{4})?", zip_code):
            excluded["missing_required_fields"] += 1
            continue

        deadline_text = str(item.get("periodDeadlineDate") or "").strip()
        deadline = None
        if deadline_text:
            try:
                deadline = datetime.strptime(deadline_text, "%m/%d/%Y").date()
            except ValueError:
                deadline = None
        status = re.sub(r"\s+", " ", str(item.get("propertyStatusDesc") or item.get("propertyStatus") or "")).strip()
        if deadline is not None and deadline < today:
            excluded["past_bid_deadline"] += 1
            continue
        if deadline is None and status.casefold() not in {"new listing", "price reduced", "pending bid opening", "showcase"}:
            excluded["missing_required_fields"] += 1
            continue

        price = safe_number(re.sub(r"[^0-9.]", "", str(item.get("listPrice") or "")), None, float)
        if price is None or price <= 0:
            excluded["invalid_price"] += 1
            continue
        key = case_number.casefold()
        if key in seen:
            continue
        seen.add(key)

        property_type = normalize_property_type(item.get("propertyType") or "")
        listing_url = "https://www.hudhomestore.gov/propertydetails?caseNumber=" + case_number
        row_id = "PA-REO-HUD-" + hashlib.sha256(key.encode()).hexdigest()[:20]
        beds = safe_number(item.get("bedrooms"), None, int)
        baths = safe_number(item.get("bathroomsdecimal") or item.get("bathrooms"), None, float)
        sqft = safe_number(item.get("squareFootage"), None, int)
        rows.append({
            "id": row_id, "docket_id": case_number, "address": street, "city": city,
            "county": item_county + " County", "zip": zip_code, "price": price,
            "deal_type": "HUD REO", "source": "HUD Home Store",
            "source_type": "reo", "data_status": "live",
            "type": property_type, "property_type": property_type,
            "beds": beds, "baths": baths, "sqft": sqft,
            "year_built": safe_number(item.get("yearBuilt"), None, int),
            "url": listing_url, "source_url": source_url,
            "summary": (f"נכס HUD REO פעיל. מחיר מבוקש ${price:,.0f}. "
                        f"מועד אחרון להצעה: {deadline_text or 'לא פורסם'}. "
                        f"מספר תיק HUD: {case_number}."),
            "market_status": "active", "reo_provider": "hud_home_store",
            "hud_case_number": case_number, "hud_listing_status": status or None,
            "hud_listing_period": str(item.get("listingPeriod") or "").strip() or None,
            "hud_bid_deadline": deadline_text or None,
            "hud_fha_financing": str(item.get("fhaFinancing") or "").strip() or None,
            "last_source_check": iso_now_est(),
        })
    return rows, {"provider": "hud_home_store", "county": county,
                  "source_url": source_url, "status": "success",
                  "result_payload_rows": len(records), "active_rows": len(rows),
                  "excluded": excluded}


def fetch_hud_homestore_reo(counties):
    """Fetch free official HUD Home Store listings county by county."""
    rows, audits = [], {}
    county_names = sorted({str(value).strip().removesuffix(" County").strip()
                           for value in counties if str(value).strip()}, key=str.casefold)
    if not county_names:
        return rows, {"provider": "hud_home_store", "status": "not_connected",
                      "counties": {}, "rows": 0, "source_url": HUD_HOME_STORE_SEARCH_URL,
                      "reason": "no selected Pennsylvania county to query"}
    headers = {"User-Agent": USER_AGENTS[0], "Accept": "text/html,application/xhtml+xml"}
    session = requests.Session()
    for county in county_names:
        search_url = requests.Request("GET", HUD_HOME_STORE_SEARCH_URL,
                                      params={"citystate": f"{county} County, PA"}).prepare().url
        try:
            response = session.get(search_url, headers=headers, timeout=25)
            response.raise_for_status()
            if len(response.content) > 8_000_000:
                raise ValueError("HUD Home Store response exceeded 8 MB safety limit")
            county_rows, audit = parse_hud_homestore_listings(response.text, county, response.url)
            rows.extend(county_rows)
            audits[county] = audit
        except (requests.RequestException, OSError, ValueError) as exc:
            audits[county] = {"provider": "hud_home_store", "county": county,
                              "source_url": search_url, "status": "failed", "rows": 0,
                              "error": str(exc)}
    return rows, {"provider": "hud_home_store", "status": "success" if audits and all(
        item.get("status") == "success" for item in audits.values()) else "partial" if any(
        item.get("status") == "success" for item in audits.values()) else "failed",
        "counties": audits, "rows": len(rows),
        "source_url": HUD_HOME_STORE_SEARCH_URL}


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
    # pdftotext's -raw order is "Tracts / Status / 1" (not "Status / Tracts").
    # Split on each tract header so every sale remains one record.
    blocks = re.split(r"(?m)^\s*Tracts\s*$", body, flags=re.I)[1:]
    if not blocks:
        raise ValueError("לא זוהו בלוקים של נכסים במסמך")
    rows, seen = [], set()
    skipped_active = 0
    recognized = 0
    unrecognized_blocks = 0
    for block in blocks:
        facts = block.split('Comments:')[0]
        status = re.search(r"(?m)^\s*(Active|Stayed|Postponed[^\n]*|Cancelled|Canceled|Sold|Continued[^\n]*|Withdrawn|Settled|Money Made)[ \t]*$", facts, re.I)
        if not status:
            unrecognized_blocks += 1
            continue
        recognized += 1
        if status.group(1).casefold() != "active":
            continue
        docket = re.search(r"\b(?:GD|MG|AR)-\d{2}-\d{5,6}\b", facts, re.I)
        # The address immediately precedes the postal city. No assumptions about
        # a street suffix or municipality based on a Pittsburgh mailing address.
        address = re.search(r"(?m)^\s*([A-Z][A-Z .'-]+),\s*PA\s+(\d{5})(?:-\d{4})?\b", facts, re.I)
        if not docket or not address:
            skipped_active += 1
            continue
        city, zip_code = (x.strip() for x in address.groups())
        raw_lines = facts.splitlines()
        city_line_index = next((i for i, line in enumerate(raw_lines)
                                if line.strip() and address.group(0).strip().casefold() in line.strip().casefold()), None)
        sale_label_index = next((i for i, line in enumerate(raw_lines[:city_line_index or 0])
                                 if line.strip().casefold() == "sale type"), None)
        if city_line_index is None or sale_label_index is None:
            skipped_active += 1
            continue
        # The PDF may wrap the address over multiple lines. Skip the sale type
        # value and join every remaining line up to city/state/ZIP; taking the
        # last line alone can return fragments such as "VACANT LAND".
        address_lines = [line.strip() for line in raw_lines[sale_label_index + 2:city_line_index] if line.strip()]
        if len(address_lines) < 1:
            skipped_active += 1
            continue
        street = re.sub(r"\s+", " ", " ".join(address_lines)).strip()
        # Some sheriff entries append a land-use note to the street line.
        # Keep it out of the address sent to the county parcel matcher.
        street = re.sub(r"\s+-\s*(?:VACANT|AGRICULTURAL)\b.*$", "", street, flags=re.I).strip()
        if re.search(r"\b(?:Sale Type|Case Number|Parcel/Tax ID|Plaintiff|Attorney|Cost & Tax Bid)\b", street, re.I):
            skipped_active += 1
            continue
        key = docket.group().upper() + ':' + normalize_addr_key(street, city, zip_code)
        if key in seen:
            continue
        seen.add(key)
        sale_type = re.search(r"Sale Type\s*\n([^\n]+)", facts, re.I)
        type_text = sale_type.group(1).strip() if sale_type else ""
        plaintiff_defendant = re.search(
            r"Plaintiff\(s\):\s*Defendant\(s\):\s*\n(.*?)\nCase Number\b", facts, re.I | re.S)
        plaintiff = defendant = None
        if plaintiff_defendant:
            parties = [re.sub(r"\s+", " ", line).strip() for line in plaintiff_defendant.group(1).splitlines() if line.strip()]
            if parties:
                plaintiff = parties[0]
                defendant = " ".join(parties[1:]) or None
        case_line = re.search(r"Case Number\s*\n([^\n]+)", facts, re.I)
        case_detail = case_line.group(1).strip() if case_line else ""
        bid_match = re.search(r"\$\s*([\d,]+\.\d{2})", case_detail)
        cost_tax_bid = bid_match.group(1).replace(',', '') if bid_match else None
        sale_id_match = re.search(r"(?m)^\s*(\d{1,4}[A-Z]{3}\d{2})\s*$", facts, re.I)
        tract_match = re.search(r"(?m)^\s*(\d{1,4})\s*$", block)
        property_line = re.search(
            re.escape(zip_code) + r"(?:-\d{4})?\s*\n\s*([^\n]+)", facts, re.I)
        municipality_line = property_line.group(1).strip() if property_line else ""
        parcel_match = re.search(r"\b(\d{1,4}[A-Z]?-[A-Z0-9]+(?:-[A-Z0-9]+)?)\s*$", municipality_line, re.I)
        municipality = municipality_line[:parcel_match.start()].strip() if parcel_match else municipality_line
        rows.append({
            "id": "PA-SHERIFF-" + hashlib.sha256(key.encode()).hexdigest()[:20],
            "docket_id": docket.group().upper(), "address": street.title(),
            "city": city.title(), "county": "Allegheny", "zip": zip_code,
            "price": None, "sqft": None, "beds": None, "baths": None,
            "deal_type": "Sheriff Sale", "source_type": "sheriff",
            "source": "Allegheny County Sheriff's Office", "source_sale_type": type_text,
            "sheriff_tract": tract_match.group(1) if tract_match else None,
            "plaintiff": plaintiff,
            "defendant": defendant,
            "case_cost_tax_bid": cost_tax_bid,
            "sale_id": sale_id_match.group(1).upper() if sale_id_match else None,
            "parcel_id": parcel_match.group(1) if parcel_match else None,
            "municipality": municipality or None,
            "data_status": "imported_official_document" if imported else "live",
            "market_status": "scheduled_sheriff_sale", "sheriff_status": "Active",
            "sale_date": sale_day.isoformat(), "source_published_date": printed_day.isoformat(),
            "listed_date": printed_day.strftime("%d/%m/%Y"),
            "summary": f"ברשימת השריף מ-{printed_day}: סטטוס Active למכירה ב-{sale_day}. {type_text}. מחיר, שטח וסוג נכס לא אומתו; יש לבדוק עדכון סטטוס במקור.",
            "url": source_url, "last_source_check": iso_now_est(), "deal_score": None,
            "filter_status": "investment_fields_unavailable",
        })
    if skipped_active or recognized < int(len(blocks) * 0.95) or not rows:
        raise ValueError(f"פענוח חלקי: זוהו {recognized}/{len(blocks)} סטטוסים; {skipped_active} כתובות פעילות לא זוהו")
    return rows, {"published_date": printed_day.isoformat(), "sale_date": sale_day.isoformat(),
                  "parsed_blocks": len(blocks), "skipped_active_addresses": skipped_active,
                  "unrecognized_blocks": unrecognized_blocks,
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
    """Use the live official PDF when available, then a validated bundled copy."""
    local_errors = []
    if os.path.isfile(SHERIFF_LOCAL_PDF):
        try:
            with open(SHERIFF_LOCAL_PDF, "rb") as f:
                body = extract_sheriff_pdf(f.read(20_000_001))
            rows, audit = parse_sheriff_text(body, SHERIFF_LOCAL_PDF, imported=True)
            return rows, SHERIFF_LOCAL_PDF, audit
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            local_errors.append(f"{SHERIFF_LOCAL_PDF}: {exc}")
            print(f"ℹ️ PDF מקומי בנתיב המקור לא נטען: {exc}")

    headers = {"User-Agent": "PA-RealEstate-Intelligence-Hub/3.1", "Accept": "text/html,application/pdf"}
    links = []
    try:
        page = requests.get(SHERIFF_PAGE, headers=headers, timeout=(5, 15))
        page.raise_for_status()
        parser = SheriffPDFLinks()
        parser.feed(page.text)
        links = [url for url in parser.links if re.search(r"sale[^/]*list", url, re.I)]
    except requests.RequestException as exc:
        print(f"ℹ️ דף השריף אינו זמין; אנסה את הקישור הידוע ואת ה־PDF המצורף: {exc}")

    configured_url = os.environ.get("SHERIFF_PDF_URL", "").strip()
    if configured_url:
        if not valid_sheriff_pdf_url(configured_url):
            raise ValueError("SHERIFF_PDF_URL חייב להיות קישור PDF באתר השריף הרשמי")
        candidate_urls = [configured_url]
    else:
        candidate_urls = links[:3]
        if now_est().date().isoformat() <= "2026-10-05" and SHERIFF_KNOWN_PDF not in candidate_urls:
            candidate_urls.append(SHERIFF_KNOWN_PDF)

    download_errors = []
    for pdf_url in candidate_urls:
        print(f"📄 מנסה PDF שריף ישיר: {pdf_url}")
        try:
            response = requests.get(pdf_url, headers=headers, timeout=(5, 30), stream=True)
            try:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(65536):
                    if chunk:
                        content.extend(chunk)
                    if len(content) > 20_000_000:
                        raise ValueError("PDF גדול מ-20MB")
            finally:
                response.close()
            body = extract_sheriff_pdf(bytes(content))
            rows, audit = parse_sheriff_text(body, pdf_url)
            return rows, pdf_url, audit
        except (requests.RequestException, OSError, ValueError, subprocess.SubprocessError) as exc:
            download_errors.append(f"{pdf_url}: {exc}")
            print(f"ℹ️ קישור PDF לא שמיש, ממשיך למקור הבא: {exc}")

    # This checked-in official PDF is a bounded fallback. The parser independently
    # verifies its printed date and upcoming sale date before accepting any records.
    if os.path.isfile(SHERIFF_BUNDLED_PDF):
        try:
            with open(SHERIFF_BUNDLED_PDF, "rb") as f:
                body = extract_sheriff_pdf(f.read(20_000_001))
            rows, audit = parse_sheriff_text(body, SHERIFF_BUNDLED_PDF, imported=True)
            audit["fallback_reason"] = "; ".join(download_errors or local_errors) or "live source unavailable"
            print(f"✅ שימוש ב־PDF הרשמי המצורף: {len(rows)} רשומות פעילות")
            return rows, SHERIFF_BUNDLED_PDF, audit
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            local_errors.append(f"{SHERIFF_BUNDLED_PDF}: {exc}")

    details = "; ".join(download_errors + local_errors)
    raise ValueError("לא ניתן לקרוא PDF שריף עדכני" + (f": {details}" if details else ""))


class ErieSheriffTableParser(HTMLParser):
    """Read the public Erie County sheriff listing by its published headers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_erie_sheriff_html(page_html, source_url=ERIE_SHERIFF_URL):
    """Map active Erie sheriff notices; judgment is never represented as price."""
    parser = ErieSheriffTableParser()
    parser.feed(page_html)
    required = {"case no", "case participants", "attorney", "property address", "judgment", "status"}
    header_index = None
    columns = {}
    for row_index, row in enumerate(parser.rows):
        normalized = [re.sub(r"[^a-z0-9]+", " ", cell.casefold()).strip() for cell in row]
        present = {name: normalized.index(name) for name in required if name in normalized}
        if len(present) == len(required):
            header_index, columns = row_index, present
            break
    if header_index is None:
        raise ValueError("Erie sheriff listing schema changed: expected case, address, judgment and status columns")

    rows, seen = [], set()
    skipped = 0
    canceled = 0
    incomplete = 0
    invalid_zip_rows = 0
    for cells in parser.rows[header_index + 1:]:
        if len(cells) <= max(columns.values()):
            continue
        status = re.sub(r"\s+", " ", cells[columns["status"]]).strip()
        if not status:
            continue
        if not status.casefold().startswith("active"):
            canceled += 1
            continue
        case_no = re.sub(r"\s+", " ", cells[columns["case no"]]).strip()
        participants = re.sub(r"\s+", " ", cells[columns["case participants"]]).strip()
        attorney = re.sub(r"\s+", " ", cells[columns["attorney"]]).strip()
        judgment_text = re.sub(r"\s+", " ", cells[columns["judgment"]]).strip()
        address_lines = [re.sub(r"\s+", " ", line).strip() for line in
                         cells[columns["property address"]].replace("\r", "\n").split("\n")]
        address_lines = [line for line in address_lines if line]
        street = address_lines[0] if address_lines else ""
        city = zip_code = municipality = upi = None
        raw_city_zip = None
        for line in address_lines[1:]:
            # Keep an active case even when the county portal has a malformed
            # ZIP (the current portal has published a 4-digit ZIP on one row).
            # A bad ZIP must never be silently repaired or guessed.
            city_match = re.match(r"^(.+?),?\s+PA\s+(\d{1,5}(?:-\d{4})?)$", line, re.I)
            if city_match:
                city = city_match.group(1).strip().rstrip(",")
                raw_city_zip = city_match.group(2)
                zip_code = raw_city_zip if re.fullmatch(r"\d{5}(?:-\d{4})?", raw_city_zip) else None
            elif re.search(r"\b(?:township|borough|boro|city)\b", line, re.I):
                municipality = line
            elif re.match(r"UPI\s*#?\s*:", line, re.I):
                upi = line
        if not case_no or not street or not city:
            skipped += 1
            continue
        if not zip_code:
            incomplete += 1
            invalid_zip_rows += 1
        key = case_no.casefold() + ":" + normalize_addr_key(street, city, zip_code)
        if key in seen:
            continue
        seen.add(key)
        money = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", judgment_text)
        judgment_amount = float(money.group(1).replace(",", "")) if money else None
        uid = "ERIE-SHERIFF-" + hashlib.sha256(key.encode()).hexdigest()[:20]
        rows.append({
            "id": uid, "docket_id": case_no, "address": street.title(),
            "city": city.title(), "county": "Erie", "zip": zip_code,
            "source_address_raw": " | ".join(address_lines),
            "address_quality": "verified_zip" if zip_code else "invalid_or_missing_zip",
            "price": None, "judgment_amount": judgment_amount,
            "judgment_text": judgment_text or None,
            "sqft": None, "beds": None, "baths": None,
            "deal_type": "Sheriff Sale", "source_type": "sheriff",
            "source": "Erie County Sheriff Sale Listing", "sheriff_status": status,
            "market_status": "scheduled_sheriff_sale", "participants": participants or None,
            "attorney": attorney or None, "municipality": municipality,
            "erie_upi_raw": upi, "data_status": "live",
            "filter_status": "investment_fields_unavailable",
            "summary": (f"רישום שריף פעיל במחוז Erie. סכום פסק הדין במסמך: "
                        f"{judgment_text or 'לא צוין'}; אין לראות בו מחיר נכס או הצעת פתיחה."
                        + (f" המיקוד במקור אינו תקין ({raw_city_zip}); יש לאמת ידנית." if not zip_code else "")),
            "url": source_url, "last_source_check": iso_now_est(), "deal_score": None,
        })
    return rows, {"parsed_rows": len(parser.rows) - header_index - 1,
                  "active_rows": len(rows), "skipped_active_addresses": skipped,
                  "incomplete_active_rows": incomplete, "invalid_zip_rows": invalid_zip_rows,
                  "non_active_rows": canceled, "mode": "live", "source_url": source_url}



class LehighSheriffTableParser(HTMLParser):
    """Collect table rows from the public Lehigh County sheriff sales portal."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_lehigh_sheriff_html(page_html, source_url=LEHIGH_SHERIFF_URL, today=None):
    """Parse upcoming, dated sale notices; a judgment is not a property price."""
    parser = LehighSheriffTableParser()
    parser.feed(page_html)
    required = {"sheriff #", "sales date", "plaintiff", "defendant", "address",
                "attorney name", "parcel #", "court case #"}
    columns = None
    header_index = None
    for i, row in enumerate(parser.rows):
        normalized = [re.sub(r"[^a-z0-9#]+", " ", cell.casefold()).strip() for cell in row]
        found = {name: normalized.index(name) for name in required if name in normalized}
        if len(found) == len(required):
            columns, header_index = found, i
            break
    if columns is None:
        raise ValueError("Lehigh sheriff portal schema changed: required listing columns were not found")

    today = today or now_est().date()
    rows, seen = [], set()
    expired, skipped = 0, 0
    for cells in parser.rows[header_index + 1:]:
        if len(cells) <= max(columns.values()):
            continue
        sale_raw = cells[columns["sales date"]].strip()
        try:
            sale_day = datetime.strptime(sale_raw, "%m/%d/%Y").date()
        except ValueError:
            try:
                sale_day = datetime.strptime(sale_raw, "%Y-%m-%d").date()
            except ValueError:
                skipped += 1
                continue
        # Portal pages may retain past notices. Only accept upcoming dates within
        # six months, so stale or unusually distant rows do not look active.
        if sale_day < today or (sale_day - today).days > 180:
            expired += 1
            continue
        sheriff_no = re.sub(r"\s+", " ", cells[columns["sheriff #"]]).strip()
        plaintiff = re.sub(r"\s+", " ", cells[columns["plaintiff"]]).strip()
        defendant = re.sub(r"\s+", " ", cells[columns["defendant"]]).strip()
        address = re.sub(r"\s+", " ", cells[columns["address"]]).strip()
        attorney = re.sub(r"\s+", " ", cells[columns["attorney name"]]).strip()
        parcel = re.sub(r"\s+", " ", cells[columns["parcel #"]]).strip()
        court_case = re.sub(r"\s+", " ", cells[columns["court case #"]]).strip()
        # The portal prints street and USPS city in a single uppercase cell.
        # Split on a known Lehigh County mailing locality; a generic regex can
        # mistakenly treat the street name as the city.
        locality_names = (
            "Fountain Hill", "New Tripoli", "Center Valley", "Laurys Station",
            "Trexlertown", "Breinigsville", "Germansville", "Catasauqua",
            "Schnecksville", "Whitehall", "Allentown", "Bethlehem", "Slatington",
            "Coopersburg", "Fogelsville", "Macungie", "Wescosville", "Alburtis",
            "East Texas", "Orefield", "Zionsville", "Emmaus", "Coplay", "Ironton",
        )
        match = None
        for locality in sorted(locality_names, key=len, reverse=True):
            candidate = re.match(
                rf"^(.+?)\s+({re.escape(locality)})\s+PA\s+(\d{{5}})(?:-\d{{4}})?$",
                address, re.I,
            )
            if candidate:
                match = candidate
                break
        if not sheriff_no or not match:
            skipped += 1
            continue
        street, city, zip_code = match.group(1).strip(), match.group(2).strip(), match.group(3)
        street = re.sub(r"\s+(?:VACANT LAND|LAND)$", "", street, flags=re.I).strip()
        if not street or not city:
            skipped += 1
            continue
        key = "|".join((sheriff_no.casefold(), court_case.casefold(), parcel.casefold()))
        if key in seen:
            continue
        seen.add(key)
        uid = "LEHIGH-SHERIFF-" + hashlib.sha256(key.encode()).hexdigest()[:20]
        rows.append({
            "id": uid, "docket_id": sheriff_no, "address": street.title(),
            "city": city.title(), "county": "Lehigh", "zip": zip_code,
            "price": None, "judgment_amount": None, "sqft": None,
            "beds": None, "baths": None, "deal_type": "Sheriff Sale",
            "source_type": "sheriff", "source": "Lehigh County Sheriff Sales Listing",
            "sheriff_status": "Scheduled", "market_status": "scheduled_sheriff_sale",
            "sale_date": sale_day.isoformat(), "plaintiff": plaintiff or None,
            "defendant": defendant or None, "attorney": attorney or None,
            "parcel_id": parcel or None, "court_case": court_case or None,
            "data_status": "live", "filter_status": "investment_fields_unavailable",
            "summary": (f"מכירת שריף מתוכננת במחוז Lehigh ל-{sale_day.isoformat()}. "
                        "מחיר, מצב הנכס ותוצאת המכירה לא אומתו; יש לבדוק עדכון במקור."),
            "url": source_url, "last_source_check": iso_now_est(), "deal_score": None,
        })
    if not rows:
        raise ValueError("Lehigh sheriff portal returned no validated upcoming sale rows")
    return rows, {"status": "success", "mode": "live", "parsed_rows": len(parser.rows) - header_index - 1,
                  "active_rows": len(rows), "expired_or_out_of_window_rows": expired,
                  "skipped_rows": skipped, "coverage": "official_portal_upcoming_notices",
                  "disclaimer": "County portal says information is summary-only and not warranted for accuracy/completeness/timeliness.",
                  "source_url": source_url}


def fetch_lehigh_sheriff_listings():
    headers = {"User-Agent": "PA-RealEstate-Intelligence-Hub/3.3", "Accept": "text/html,application/xhtml+xml"}
    response = requests.get(LEHIGH_SHERIFF_URL, headers=headers, timeout=(5, 25))
    response.raise_for_status()
    if len(response.content) > 8_000_000:
        raise ValueError("Lehigh sheriff response exceeded 8 MB safety limit")
    page_text = re.sub(r"<[^>]+>", " ", response.text, flags=re.S)
    if "Lehigh County" not in page_text or "Foreclosure Sales Listing" not in page_text:
        raise ValueError("Lehigh sheriff source identity check failed")
    return parse_lehigh_sheriff_html(response.text, response.url)

def fetch_erie_sheriff_listings():
    response = requests.get(
        ERIE_SHERIFF_URL,
        headers={"User-Agent": "PA-RealEstate-Intelligence-Hub/2.9", "Accept": "text/html"},
        timeout=(5, 20),
    )
    response.raise_for_status()
    rows, audit = parse_erie_sheriff_html(response.text, ERIE_SHERIFF_URL)
    if audit["skipped_active_addresses"] or audit.get("incomplete_active_rows"):
        audit["status"] = "partial"
    else:
        audit["status"] = "success"
    return rows, audit


class CountyTableParser(HTMLParser):
    """Collect table cell text from the County assessment portal without JS."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            value = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def county_pin_from_sheriff_parcel(parcel_id):
    """Convert Sheriff parcel IDs to the County's ward/section/lot PIN.

    Sheriff PDFs use both ward-section-lot (556-G-276) and
    section-lot-ward (R-123-1133) layouts. The latter is confirmed by the
    official 509 5th Ave record: 1133-R-00123-0000-00.
    """
    parts = re.findall(r"[A-Za-z]+|\d+", str(parcel_id or "").strip())
    if len(parts) < 3:
        return None
    if parts[0].isalpha() and parts[1].isdigit() and parts[2].isdigit():
        section, lot, ward = parts[:3]
    elif parts[0].isdigit() and parts[1].isalpha() and parts[2].isdigit():
        ward, section, lot = parts[:3]
    else:
        return None
    return f"{int(ward):04d}{section.upper()}{int(lot):05d}000000"


def fetch_county_building_data(parcel_id, address=None, municipality=None):
    pin = county_pin_from_sheriff_parcel(parcel_id)
    if not pin:
        return None
    address_match = re.match(r"^\s*(\d+[A-Za-z]?)\s+(.+?)\s*$", str(address or ""))
    house_number = address_match.group(1) if address_match else ""
    street = address_match.group(2) if address_match else ""
    # The County's search page asks for the street name without its suffix.
    street = re.sub(r"\s+(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|BLVD|BOULEVARD|LN|LANE|CT|COURT|PL|PLACE|WAY|TER|TERRACE)\.?$", "", street, flags=re.I).strip()
    search_muni = re.sub(r"\s+(?:BOROUGH|BORO|TOWNSHIP|TWP)\.?$", "", str(municipality or ""), flags=re.I).strip()
    query = urlencode({
        "ID": pin, "SearchType": "2", "CurrRow": "0", "SearchName": "",
        "SearchStreet": street, "SearchNum": house_number, "SearchMuni": search_muni,
        "SearchParcel": "", "pin": pin,
    })
    # The portal redirects bare parcel links to its search page. Retaining the
    # source address and municipality makes the County resolve the abbreviated PIN.
    url = f"https://realestate.alleghenycounty.us/BuildingInfo?{query}"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 PA-RealEstate-Intelligence",
                                          "Accept": "text/html"}, timeout=(5, 12))
    response.raise_for_status()
    if urlparse(getattr(response, "url", url)).path.rstrip("/").lower() == "/search":
        raise ValueError(f"County portal did not resolve parcel {parcel_id} from its address")
    parser = CountyTableParser()
    parser.feed(response.text)
    fields = {}
    for row in parser.rows:
        for i in range(len(row) - 1):
            label = row[i].rstrip(":").strip().lower()
            if label:
                fields[label] = row[i + 1].strip()
    def get(*labels):
        for label in labels:
            value = fields.get(label.lower())
            if value and value not in ("-", "N/A"):
                return value
        return None
    living = get("living area")
    living_num = re.search(r"[\d,]+", living or "")
    lot = get("lot area")
    lot_num = re.search(r"[\d,]+", lot or "")
    full_baths = get("full baths")
    half_baths = get("half baths")
    def numeric(value):
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        return float(match.group()) if match else None
    baths = (numeric(full_baths) or 0) + (numeric(half_baths) or 0) * 0.5
    result = {
        "pin": pin,
        "parcel_id": get("parcel id"),
        "address": get("address"),
        "use_code": get("use code"),
        "total_rooms": numeric(get("total rooms")),
        "beds": numeric(get("bedrooms")),
        "baths": baths or None,
        "sqft": int(living_num.group().replace(",", "")) if living_num else None,
        "year_built": numeric(get("year built")),
        "style": get("style"),
        "condition": get("condition"),
        "stories": numeric(get("stories")),
        "basement": get("basement"),
        "heating_cooling": get("heating/cooling"),
        "roof_type": get("roof type"),
        "lot_size": lot_num.group().replace(",", "") if lot_num else None,
        "source": "Allegheny County Real Estate Portal",
        "source_url": url,
        "retrieved_at": iso_now_est(),
    }
    return result if any(result.get(k) is not None for k in ("beds", "sqft", "year_built", "use_code")) else None


def enrich_sheriff_rows_from_county(rows, max_lookups=50):
    """Enrich Sheriff rows from County parcel records, preserving Sheriff facts separately."""
    cache = {}
    if os.path.isfile(SHERIFF_PROPERTY_CACHE_FILE):
        try:
            with open(SHERIFF_PROPERTY_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            cache = {}
    if not isinstance(cache, dict):
        cache = {}
    parcels_by_pin = {}
    for row in rows:
        parcel = row.get("parcel_id")
        if parcel:
            key = county_pin_from_sheriff_parcel(parcel)
            if key:
                parcels_by_pin[key] = parcel
    row_by_pin = {county_pin_from_sheriff_parcel(row.get("parcel_id")): row for row in rows}
    cache_meta = cache.setdefault("_meta", {})
    backfill_complete = bool(cache_meta.get("initial_backfill_complete"))
    # Backfill only parcels never requested before. The 50-record cap applies
    # only until the current Sheriff roster has been checked once.
    backfill = {pin: parcel for pin, parcel in parcels_by_pin.items() if pin not in cache}
    if not backfill_complete:
        todo = dict(list(backfill.items())[:max(0, int(max_lookups))])
    else:
        # After initial backfill, new records are processed without a cap.
        # Retry failed lookups hourly so corrected addresses can recover quickly;
        # stable no-data responses remain cached for 30 days.
        now = datetime.now(EST_TZ)
        todo = {}
        for pin, parcel in parcels_by_pin.items():
            cached = cache.get(pin)
            if isinstance(cached, dict) and cached.get("source"):
                continue
            checked = cached.get("checked_at") or cached.get("retrieved_at") if isinstance(cached, dict) else None
            try:
                checked_at = datetime.fromisoformat(checked) if checked else None
                if checked_at and checked_at.tzinfo is None:
                    checked_at = EST_TZ.localize(checked_at)
            except (TypeError, ValueError):
                checked_at = None
            cooldown = timedelta(days=30) if isinstance(cached, dict) and cached.get("no_data") else timedelta(hours=1)
            if checked_at is None or now - checked_at >= cooldown:
                todo[pin] = parcel
    attempted_pins = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_county_building_data, parcel,
                               row_by_pin.get(pin, {}).get("address"),
                               row_by_pin.get(pin, {}).get("municipality")): (pin, parcel)
                   for pin, parcel in todo.items()}
        attempted_pins.update(pin for pin, _ in futures.values())
        for future, (pin, parcel) in futures.items():
            try:
                result = future.result()
                cache[pin] = result if result else {"no_data": True, "checked_at": iso_now_est()}
            except (requests.RequestException, ValueError, OSError) as exc:
                cache[pin] = {"lookup_error": str(exc), "checked_at": iso_now_est()}
    for row in rows:
        pin = county_pin_from_sheriff_parcel(row.get("parcel_id"))
        data = cache.get(pin) if pin else None
        if isinstance(data, dict) and data.get("source"):
            row["county_property_data"] = data
            for field in ("beds", "baths", "sqft", "year_built", "lot_size"):
                if not row.get(field) and data.get(field) is not None:
                    row[field] = data[field]
            row["county_property_status"] = "matched"
        else:
            if not pin:
                row["county_property_status"] = "invalid_parcel_id"
            elif pin in backfill and pin not in attempted_pins:
                row["county_property_status"] = "pending_backfill"
            elif isinstance(data, dict) and data.get("lookup_error"):
                row["county_property_status"] = "lookup_failed"
            else:
                row["county_property_status"] = "no_county_building_data"
    if not backfill_complete and all(pin in cache for pin in parcels_by_pin):
        cache_meta["initial_backfill_complete"] = True
        cache_meta["initial_backfill_completed_at"] = iso_now_est()
    atomic_write_json(SHERIFF_PROPERTY_CACHE_FILE, cache)
    return sum(row.get("county_property_status") == "matched" for row in rows)


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

PA_COUNTIES = [
    "Adams", "Allegheny", "Armstrong", "Beaver", "Bedford", "Berks", "Blair", "Bradford", "Bucks", "Butler",
    "Cambria", "Cameron", "Carbon", "Centre", "Chester", "Clarion", "Clearfield", "Clinton", "Columbia", "Crawford",
    "Cumberland", "Dauphin", "Delaware", "Elk", "Erie", "Fayette", "Forest", "Franklin", "Fulton", "Greene",
    "Huntingdon", "Indiana", "Jefferson", "Juniata", "Lackawanna", "Lancaster", "Lawrence", "Lebanon", "Lehigh",
    "Luzerne", "Lycoming", "McKean", "Mercer", "Mifflin", "Monroe", "Montgomery", "Montour", "Northampton",
    "Northumberland", "Perry", "Philadelphia", "Pike", "Potter", "Schuylkill", "Snyder", "Somerset", "Sullivan",
    "Susquehanna", "Tioga", "Union", "Venango", "Warren", "Washington", "Wayne", "Westmoreland", "Wyoming", "York",
]

# Redfin publishes Pennsylvania county pages under IDs 2361-2427 in alphabetical order.
# Eight counties have a known metro market slug; other counties use the statewide slug.
COUNTY_MARKETS = {
    "Allegheny": "pittsburgh", "Philadelphia": "philadelphia", "Lehigh": "allentown",
    "Berks": "reading", "Erie": "erie", "Lackawanna": "scranton",
    "Northampton": "allentown", "Lancaster": "lancaster",
}

REGION_MAP = {
    "Pittsburgh": {"market": "pittsburgh", "region_id": "15702", "region_type": "6"},
    "Philadelphia": {"market": "philadelphia", "region_id": "15502", "region_type": "6"},
    "Allentown": {"market": "allentown", "region_id": "514", "region_type": "6"},
    "Reading": {"market": "reading", "region_id": "16305", "region_type": "6"},
    "Erie": {"market": "erie", "region_id": "6172", "region_type": "6"},
    "Scranton": {"market": "scranton", "region_id": "17652", "region_type": "6"},
    "Bethlehem": {"market": "allentown", "region_id": "1616", "region_type": "6"},
    "Lancaster": {"market": "lancaster", "region_id": "10496", "region_type": "6"},
}
COUNTY_REGION_KEYS = {}
for _index, _county in enumerate(PA_COUNTIES, start=2361):
    _market = COUNTY_MARKETS.get(_county, "pennsylvania")
    _key = f"{_county} County"
    COUNTY_REGION_KEYS[_county] = _key
    REGION_MAP[_key] = {
        "market": _market,
        "region_id": str(_index),
        "region_type": "5",
        "county_name": _county,
        "catalog_area": _county,
    }
# Backward-compatible hidden UI scope previously labeled "Allegheny".
REGION_MAP["Allegheny"] = REGION_MAP["Allegheny County"]

CITY_COUNTY = {
    "Pittsburgh": "Allegheny", "Allegheny": "Allegheny",
    "Philadelphia": "Philadelphia", "Allentown": "Lehigh", "Reading": "Berks",
    "Erie": "Erie", "Scranton": "Lackawanna", "Bethlehem": "Northampton",
    "Lancaster": "Lancaster",
}


def build_mls_scan_areas(cities, counties):
    """Use county-wide MLS queries for fully selected counties, city feeds otherwise."""
    selected_counties = {str(county).strip().casefold() for county in counties or []}
    areas, selected_names = [], set()
    for county, region_key in COUNTY_REGION_KEYS.items():
        if county.casefold() in selected_counties:
            areas.append(region_key)
            selected_names.add(county.casefold())
    for city in cities or []:
        name = str(city).strip()
        parent = CITY_COUNTY.get(name)
        if parent and parent.casefold() in selected_names:
            continue
        if name and name not in areas:
            areas.append(name)
    return areas

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
                "county": target.get("county_name") or CITY_COUNTY.get(clean_city) or "",
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
    if audit.get("limit_reached"):
        audit.update({"status": "partial", "error": "Redfin result cap reached (350); coverage may be incomplete"})
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
        incoming_price_history = incoming.get("price_history")
        if isinstance(incoming_price_history, list) and incoming_price_history:
            price_history = deepcopy(incoming_price_history)
        else:
            price_history = [{"date": timestamp, "price": incoming.get("price"), "source": incoming.get("source", "")}]
        merged["price_history"] = price_history

        source_text = " ".join(str(incoming.get(field) or "") for field in ("source", "source_type", "deal_type")).lower()
        is_mls_listing = any(token in source_text for token in ("mls", "redfin", "realtor", "zillow"))
        price_drop_history = incoming.get("price_drop_history")
        if not isinstance(price_drop_history, list):
            price_drop_history = []
        if is_mls_listing:
            for previous_entry, current_entry in zip(price_history, price_history[1:]):
                if not isinstance(previous_entry, dict) or not isinstance(current_entry, dict):
                    continue
                previous_price = safe_number(previous_entry.get("price"), None)
                current_price = safe_number(current_entry.get("price"), None)
                if previous_price is None or current_price is None or previous_price <= 0 or current_price <= 0 or current_price >= previous_price:
                    continue
                drop_date = current_entry.get("date") or timestamp
                already_recorded = any(
                    isinstance(item, dict)
                    and safe_number(item.get("previous_price"), None) == previous_price
                    and safe_number(item.get("current_price"), None) == current_price
                    and item.get("date") == drop_date
                    for item in price_drop_history
                )
                if already_recorded:
                    continue
                drop_amount = round(previous_price - current_price, 2)
                price_drop_history.append({
                    "date": drop_date,
                    "scan_id": scan_id if drop_date == timestamp else None,
                    "source": current_entry.get("source") or incoming.get("source") or "MLS",
                    "previous_price": previous_price,
                    "current_price": current_price,
                    "drop_amount": drop_amount,
                    "drop_percent": round((drop_amount / previous_price) * 100, 2),
                })
        merged["price_drop_history"] = price_drop_history
        merged["price_dropped"] = bool(incoming.get("price_dropped") or price_drop_history)
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

    # Track price reductions on an existing MLS listing. New listings establish a
    # baseline; only a lower price for the same existing listing counts as a drop.
    old_price_num = safe_number(old_price, None)
    new_price_num = safe_number(new_price, None)
    source_text = " ".join(str(incoming.get(field) or existing.get(field) or "") for field in ("source", "source_type", "deal_type")).lower()
    is_mls_listing = any(token in source_text for token in ("mls", "redfin", "realtor", "zillow"))
    price_drop_history = existing.get("price_drop_history")
    if not isinstance(price_drop_history, list):
        price_drop_history = []

    # Backfill drops from existing history as well as recording this scan's change.
    # This repairs records whose old price history existed before automatic flagging.
    if is_mls_listing:
        for previous_entry, current_entry in zip(price_history, price_history[1:]):
            if not isinstance(previous_entry, dict) or not isinstance(current_entry, dict):
                continue
            previous_price = safe_number(previous_entry.get("price"), None)
            current_price = safe_number(current_entry.get("price"), None)
            if previous_price is None or current_price is None or previous_price <= 0 or current_price <= 0 or current_price >= previous_price:
                continue
            drop_date = current_entry.get("date") or timestamp
            already_recorded = any(
                isinstance(item, dict)
                and safe_number(item.get("previous_price"), None) == previous_price
                and safe_number(item.get("current_price"), None) == current_price
                and item.get("date") == drop_date
                for item in price_drop_history
            )
            if already_recorded:
                continue
            drop_amount = round(previous_price - current_price, 2)
            price_drop_history.append({
                "date": drop_date,
                "scan_id": scan_id if drop_date == timestamp else None,
                "source": current_entry.get("source") or incoming.get("source") or existing.get("source") or "MLS",
                "previous_price": previous_price,
                "current_price": current_price,
                "drop_amount": drop_amount,
                "drop_percent": round((drop_amount / previous_price) * 100, 2),
            })

    merged["price_drop_history"] = price_drop_history
    merged["price_dropped"] = bool(
        existing.get("price_dropped")
        or incoming.get("price_dropped")
        or price_drop_history
    )

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
        today_est = now_est().date()
        expired_tax_keys = [key for key, row in existing_props_dict.items()
                            if isinstance(row, dict) and row.get("source_type") == "tax"
                            and row.get("sale_date")
                            and str(row.get("sale_date"))[:10] < today_est.isoformat()]
        for key in expired_tax_keys:
            existing_props_dict.pop(key, None)
        if expired_tax_keys:
            log_entry["expired_tax_candidates_removed"] = len(expired_tax_keys)
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
    configured_counties = server_config.get("counties")
    counties_list = configured_counties if isinstance(configured_counties, list) else []
    mls_scan_areas = build_mls_scan_areas(cities_list, counties_list)
    selected_neighborhoods = [
        str(v).strip()
        for v in (server_config.get("neighborhoods") or [])
        if str(v).strip()
    ]
    selected_neighborhoods = list(dict.fromkeys(selected_neighborhoods))

    log_entry["active_sectors"] = active_sectors_now
    log_entry["cities"] = cities_list
    log_entry["counties"] = counties_list
    log_entry["mls_scan_areas"] = mls_scan_areas
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
                           "status": "pending" if sector in ("mls", "sheriff", "reo", "tax") else "not_connected",
                           "rows": 0}
    log_entry["sources"] = sources
    # Probate remains disabled until a verified feed is mapped. Tax is connected below for Lehigh.
    sources.setdefault("tax", {"label": SOURCE_LABELS["tax"], "status": "not_connected", "rows": 0})
    sources.setdefault("06_probate_estates", {"label": SOURCE_LABELS["06_probate_estates"], "status": "not_connected", "rows": 0})

    sheriff_rows = []
    sheriff_county_results = {}
    if "sheriff" in active_sectors_now:
        requested_counties = []
        for county in counties_list:
            name = str(county).strip()
            if name in COUNTY_REGION_KEYS and name not in requested_counties:
                requested_counties.append(name)
        for area in cities_list:
            mapped = CITY_COUNTY.get(str(area).strip())
            if mapped and mapped not in requested_counties:
                requested_counties.append(mapped)
        for county in requested_counties:
            if county not in {"Allegheny", "Erie", "Lehigh"}:
                sheriff_county_results[county] = {
                    "status": "not_connected", "rows": 0,
                    "error": f"אין עדיין מתאם מקור שריף מאומת למחוז {county}",
                }

        for county in [name for name in requested_counties if name in {"Allegheny", "Erie", "Lehigh"}]:
            try:
                if county == "Allegheny":
                    county_rows, sheriff_pdf, sheriff_audit = fetch_allegheny_sheriff_listings()
                    county_matches = enrich_sheriff_rows_from_county(county_rows, max_lookups=50)
                    sheriff_audit.update({
                        "county_property_matches": county_matches,
                        "county_property_pending": sum(row.get("county_property_status") == "pending_backfill" for row in county_rows),
                        "county_property_lookup_failed": sum(row.get("county_property_status") == "lookup_failed" for row in county_rows),
                        "county_property_no_data": sum(row.get("county_property_status") == "no_county_building_data" for row in county_rows),
                        "status": "imported" if sheriff_audit["mode"] == "imported" else "success",
                        "source_url": sheriff_pdf,
                    })
                    if sheriff_audit.get("skipped_active_addresses") or sheriff_audit.get("unrecognized_blocks"):
                        sheriff_audit["status"] = "partial"
                    print(f"🏠 Allegheny building matches: {county_matches}/{len(county_rows)}")
                    print(f"⚖️ שריף Allegheny: {len(county_rows)} רשומות פעילות; מקור: {sheriff_pdf}")
                elif county == "Erie":
                    county_rows, sheriff_audit = fetch_erie_sheriff_listings()
                    print(f"⚖️ שריף Erie: {len(county_rows)} רשומות פעילות; מקור: {ERIE_SHERIFF_URL}")
                else:
                    county_rows, sheriff_audit = fetch_lehigh_sheriff_listings()
                    print(f"⚖️ שריף Lehigh: {len(county_rows)} רשומות עתידיות; מקור: {LEHIGH_SHERIFF_URL}")
                sheriff_rows.extend(county_rows)
                sheriff_county_results[county] = {
                    "status": sheriff_audit.get("status", "success"),
                    "rows": len(county_rows), **sheriff_audit,
                }
            except (requests.RequestException, OSError, ValueError, subprocess.SubprocessError) as exc:
                response = getattr(exc, "response", None)
                blocked = response is not None and response.status_code in (401, 403)
                sheriff_county_results[county] = {
                    "status": "blocked" if blocked else "failed", "rows": 0, "error": str(exc),
                }
                log_entry["errors"].append(f"sheriff {county} source unavailable: {exc}")
                print(f"⚠️ מקור השריף במחוז {county} לא עודכן: {exc}")

        if requested_counties:
            try:
                cached_sheriff_rows = load_json_file(SHERIFF_FILE, [])
                if not isinstance(cached_sheriff_rows, list):
                    cached_sheriff_rows = []
                persisted_sheriff_rows = []
                for cached in cached_sheriff_rows:
                    county = str(cached.get("county") or "").strip()
                    if county not in requested_counties:
                        persisted_sheriff_rows.append(cached)
                        continue
                    county_status = sheriff_county_results.get(county, {}).get("status")
                    # Replace a county's cache only after a clean fetch. Keep old
                    # rows during failed/partial requests so a flaky feed does not
                    # erase notices the user already has.
                    if county_status not in ("success", "imported"):
                        persisted_sheriff_rows.append(cached)
                persisted_keys = {property_key(row) for row in persisted_sheriff_rows}
                for fresh in sheriff_rows:
                    key = property_key(fresh)
                    if key not in persisted_keys:
                        persisted_sheriff_rows.append(fresh)
                        persisted_keys.add(key)
                atomic_write_json(SHERIFF_FILE, persisted_sheriff_rows)
            except OSError as exc:
                log_entry["errors"].append(f"sheriff listing save failed: {exc}")
                for item in sheriff_county_results.values():
                    item["status"] = "partial"
                    item["save_error"] = str(exc)
            statuses = [item.get("status") for item in sheriff_county_results.values()]
            aggregate_status = ("success" if statuses and all(s in ("success", "imported") for s in statuses)
                                else "partial" if any(s in ("success", "imported", "partial") for s in statuses)
                                else "not_connected" if statuses and all(s == "not_connected" for s in statuses)
                                else "failed")
            sources["sheriff"].update({
                "status": aggregate_status, "rows": len(sheriff_rows),
                "counties": sheriff_county_results, "investment_filters": "unavailable",
            })
            log_entry["sheriff_active_lots"] = len(sheriff_rows)
            log_entry["sheriff_sources_by_county"] = sheriff_county_results
        else:
            sources["sheriff"].update({
                "status": "unsupported_area",
                "error": "חיבורי שריף חיים זמינים כרגע רק למחוזות Allegheny, Erie ו-Lehigh",
            })

    tax_rows = []
    if "tax" in active_sectors_now:
        requested_tax_counties = {str(name).strip().removesuffix(" County").casefold()
                                  for name in counties_list if str(name).strip()}
        # Map selected target cities into their county; tax requests stay within the selected geography.
        requested_tax_counties.update(str(CITY_COUNTY.get(str(area).strip(), "")).strip().casefold()
                                        for area in cities_list if CITY_COUNTY.get(str(area).strip()))
        county_names = {"allegheny": "Allegheny", "erie": "Erie", "lehigh": "Lehigh"}
        county_audits = {}
        for county_key in sorted(requested_tax_counties):
            county_name = county_names.get(county_key, county_key.title())
            try:
                if county_key == "erie":
                    county_rows, tax_audit = fetch_erie_repository_list()
                    tax_rows.extend(county_rows)
                    county_audits[county_name] = {**tax_audit, "rows": len(county_rows)}
                    print(f"🧾 Erie Repository: {len(county_rows)} רשומות מועמדות; מקור רשמי: {tax_audit.get('source_url')}")
                elif county_key == "lehigh":
                    county_rows, tax_audit = fetch_lehigh_judicial_tax_list()
                    tax_rows.extend(county_rows)
                    county_audits[county_name] = {**tax_audit, "rows": len(county_rows)}
                    print(f"🧾 Lehigh Judicial Sale: {len(county_rows)} רשומות מועמדות; sale date {tax_audit.get('sale_date')}")
                else:
                    county_audits[county_name] = {
                        "status": "not_connected", "rows": 0,
                        "reason": "no verified current tax feed connected for this county",
                    }
            except (requests.RequestException, OSError, ValueError, subprocess.SubprocessError) as exc:
                response = getattr(exc, "response", None)
                blocked = response is not None and response.status_code in (401, 403)
                county_audits[county_name] = {"status": "blocked" if blocked else "failed", "rows": 0, "error": str(exc)}
                log_entry["errors"].append(f"tax {county_name} source unavailable: {exc}")
                print(f"⚠️ מקור חובות המס במחוז {county_name} לא עודכן: {exc}")

        county_statuses = [item.get("status", "success") for item in county_audits.values()]
        connected_statuses = [status for status in county_statuses if status != "not_connected"]
        if not county_audits or not connected_statuses:
            tax_status = "not_connected"
        elif all(status == "success" for status in county_statuses):
            tax_status = "success"
        elif any(status in ("success", "partial") for status in county_statuses):
            tax_status = "partial"
        else:
            tax_status = "failed"
        sources["tax"].update({"status": tax_status, "rows": len(tax_rows), "counties": county_audits})

    reo_rows = []
    if "reo" in active_sectors_now:
        selected_county_names = {str(value).strip().removesuffix(" County").strip()
                                 for value in counties_list if str(value).strip()}
        for area in cities_list:
            parent_county = CITY_COUNTY.get(str(area).strip())
            if parent_county:
                selected_county_names.add(parent_county)
        # An empty selection is not permission to expand to statewide coverage.
        # Leave this sector unscanned and preserve the manual portal links.
        manual_reo_portals = [
            {
                "id": "fannie_mae_homepath",
                "label": "Fannie Mae HomePath (בדיקה ידנית)",
                "url": FANNIE_HOME_PATH_URL,
                "status": "manual_link_only",
            },
            {
                "id": "bank_of_america_reo",
                "label": "Bank of America REO בפנסילבניה (בדיקה ידנית)",
                "url": BANK_OF_AMERICA_REO_URL,
                "status": "manual_link_only",
            },
        ]
        if not selected_county_names:
            sources["reo"].update({
                "status": "unsupported_area", "rows": 0, "providers": {},
                "provider_count": 0, "coverage": "not_scanned_no_county_selected",
                "scope": "no_county_selected", "manual_sources": manual_reo_portals,
                "note": "לא נבחרו מחוזות או ערים מזוהים; לא בוצעה סריקת REO. יש לבחור מחוזות לפני הריצה. קישורי Fannie Mae ו-Bank of America הם לבדיקה ידנית בלבד.",
            })
            print("⚠️ לא נבחרו מחוזות לסריקת REO; דילוג ללא הרחבה לכל פנסילבניה")
        else:
            selected_county_keys = {name.casefold() for name in selected_county_names}
            all_counties_selected = {name.casefold() for name in PA_COUNTIES}.issubset(selected_county_keys)
            reo_provider_audits = {}

            # Freddie Mac HomeSteps: keep only results whose county can be verified
            # against the user's selected geography.
            try:
                homesteps_rows, homesteps_audit = fetch_homesteps_reo()
                geo_dropped = 0
                scoped_homesteps_rows = []
                for row in homesteps_rows:
                    county_name = str(row.get("county") or "").removesuffix(" County").strip().casefold()
                    if county_name and county_name in selected_county_keys:
                        scoped_homesteps_rows.append(row)
                    else:
                        geo_dropped += 1
                reo_rows.extend(scoped_homesteps_rows)
                reo_provider_audits["freddie_mac_homesteps"] = {
                    **homesteps_audit, "status": "success", "rows": len(scoped_homesteps_rows),
                    "geography_dropped": geo_dropped,
                    "scope": "all_selected_pa_counties" if all_counties_selected else "selected_counties_only",
                }
                print(f"🏦 HomeSteps/Freddie Mac: {len(scoped_homesteps_rows)} נכסים במחוזות שנבחרו")
            except (requests.RequestException, OSError, ValueError) as exc:
                reo_provider_audits["freddie_mac_homesteps"] = {
                    "provider": "freddie_mac_homesteps", "status": "failed", "rows": 0,
                    "source_url": HOMESTEPS_SEARCH_URL, "error": str(exc),
                }
                log_entry["errors"].append(f"REO HomeSteps source unavailable: {exc}")
                print(f"⚠️ HomeSteps/Freddie Mac לא עודכן: {exc}")

            # HUD Home Store: query only selected counties and import the official
            # current-price, bid-deadline and property-detail records.
            hud_rows, hud_audit = fetch_hud_homestore_reo(selected_county_names)
            reo_rows.extend(hud_rows)
            reo_provider_audits["hud_home_store"] = hud_audit
            for county_name, county_audit in hud_audit.get("counties", {}).items():
                if county_audit.get("status") == "failed":
                    log_entry["errors"].append(
                        f"REO HUD Home Store {county_name} unavailable: {county_audit.get('error', 'unknown error')}")
            for county_name, county_audit in hud_audit.get("counties", {}).items():
                print(f"🏛️ HUD Home Store {county_name}: {county_audit.get('active_rows', county_audit.get('rows', 0))} נכסים פעילים")

            success_count = sum(audit.get("status") in {"success", "partial"}
                                for audit in reo_provider_audits.values())
            aggregate_status = "partial" if success_count else "failed"
            sources["reo"].update({
                "status": aggregate_status, "rows": len(reo_rows),
                "providers": reo_provider_audits,
                "provider_count": len(reo_provider_audits),
                "coverage": "partial_multi_provider",
                "scope": "all_selected_pa_counties" if all_counties_selected else "selected_counties_only",
                "manual_sources": manual_reo_portals,
                "note": "סריקה אוטומטית: Freddie Mac HomeSteps ו-HUD בלבד. קישורי Fannie Mae ו-Bank of America מוצגים לבדיקה ידנית ואינם נסרקים או נספרים; יש להרחיב כיסוי רק באמצעות פיד/API רשמי ומורשה.",
            })

    live_results = []
    if "mls" in active_sectors_now:
        mls_audits = []
        for city in mls_scan_areas:
            area_audit = {}
            live_results.extend(fetch_live_mls_for_city(city, min_price, max_price, area_audit))
            mls_audits.append(area_audit)
        good = sum(a.get("status") == "success" for a in mls_audits)
        usable = sum(a.get("status") in ("success", "partial") for a in mls_audits)
        sources["mls"].update({"status": "success" if good == len(mls_audits) and good else "partial" if usable else "failed",
                                 "rows": len(live_results), "areas": mls_audits,
                                 "coverage": "not_proven_complete"})
        for item in mls_audits:
            if item.get("error"):
                log_entry["errors"].append(f"MLS {item['area']}: {item['error']}")

    # Geography QA: prove what each configured Redfin region actually returned.
    geography_qa = {}
    for area in mls_scan_areas:
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
    for area in mls_scan_areas:
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

    combined = live_results + sheriff_rows + tax_rows + reo_rows + get_placeholder_sector_results(
        [s for s in active_sectors_now if s not in ("mls", "sheriff", "reo", "tax")]
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

        # Official auction candidate rows often have no verified asking price/property facts;
        # do not drop them because MLS investment filters cannot apply.
        if prop.get("source_type") in {"sheriff", "tax"}:
            final_filtered.append(prop)
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
    for sector, source in sources.items():
        if sector in {"mls", "reo", "sheriff", "tax", "06_probate_estates"}:
            source["passed_filters"] = sum(1 for prop in final_filtered if prop.get("source_type") == sector)
    log_entry["source_passed_filters"] = {
        sector: source.get("passed_filters", 0) for sector, source in sources.items()
    }
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

        existing = existing_props_dict.get(key)
        # Sheriff addresses can be corrected when the PDF line wrapping is
        # parsed properly. Reconcile only on the exact docket + parcel pair and
        # only if it identifies one old record, preserving its stable ID.
        if existing is None and deal.get("source_type") == "tax":
            sale_number = str(deal.get("sale_number") or "").strip().upper()
            parcel = re.sub(r"[^a-z0-9]", "", str(deal.get("parcel_id") or "").lower())
            candidates = []
            if sale_number and parcel:
                for old_key, old in existing_props_dict.items():
                    if old_key == key or not isinstance(old, dict) or old.get("source_type") != "tax":
                        continue
                    old_sale = str(old.get("sale_number") or "").strip().upper()
                    old_parcel = re.sub(r"[^a-z0-9]", "", str(old.get("parcel_id") or "").lower())
                    if old_sale == sale_number and old_parcel == parcel:
                        candidates.append((old_key, old))
            if len(candidates) == 1:
                old_key, existing = candidates[0]
                deal["id"] = existing.get("id") or deal.get("id")
                existing_props_dict.pop(old_key, None)
        if existing is None and deal.get("source_type") == "sheriff":
            docket = str(deal.get("docket_id") or "").strip().upper()
            parcel = re.sub(r"[^a-z0-9]", "", str(deal.get("parcel_id") or "").lower())
            candidates = []
            if docket and parcel:
                for old_key, old in existing_props_dict.items():
                    if old_key == key or not isinstance(old, dict) or old.get("source_type") != "sheriff":
                        continue
                    old_docket = str(old.get("docket_id") or "").strip().upper()
                    old_parcel = re.sub(r"[^a-z0-9]", "", str(old.get("parcel_id") or "").lower())
                    if old_docket == docket and old_parcel == parcel:
                        candidates.append((old_key, old))
            if len(candidates) == 1:
                old_key, existing = candidates[0]
                deal["id"] = existing.get("id") or deal.get("id")
                existing_props_dict.pop(old_key, None)
        seen_keys.add(key)
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


def repository_analysis_cleanup_needed(properties):
    """Return whether repository records still carry analyzer estimates or OSM coordinates."""
    if not isinstance(properties, list):
        return False
    metrics = ("arv", "flip_rehab", "rental_rehab", "mao_flip", "monthly_rent_est",
               "mao_rental", "neighborhood_class", "ai_summary")
    return any(
        isinstance(prop, dict)
        and prop.get("source_type") == "tax"
        and prop.get("tax_sale_type") == "repository"
        and (prop.get("analysis_mode") != "source_record_only"
             or prop.get("geocode_source") == "OpenStreetMap Nominatim"
             or any(prop.get(field) is not None for field in metrics))
        for prop in properties
    )


if __name__ == "__main__":
    run_orchestrator()
    # Analyzer runs only after an MLS scan that actually changed properties.
    if os.environ.get("GITHUB_OUTPUT"):
        latest = load_json_file(SCANNER_STATUS_FILE, {}).get("last_event", {})
        stored_properties = load_json_file(PROPERTIES_FILE, [])
        repository_cleanup_needed = repository_analysis_cleanup_needed(stored_properties)
        analyze = bool(latest.get("status") in ("success", "partial") and
                       ((latest.get("new", 0) or latest.get("updated", 0)) or repository_cleanup_needed))
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"analyze={'true' if analyze else 'false'}\n")
