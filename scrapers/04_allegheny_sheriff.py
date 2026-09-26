import os
import re
import sys
import json
import logging
from io import BytesIO
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ALLEGHENY-SHERIFF] %(levelname)s: %(message)s")

BASE_PAGE_URL = "https://sheriffalleghenycounty.com/sheriffs-sales/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def find_latest_pdf_url() -> Optional[str]:
    try:
        resp = requests.get(BASE_PAGE_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text().strip().lower()
            if "sale" in href.lower() and href.lower().endswith(".pdf"):
                if "listing" in href.lower() or "listing" in text:
                    return href
            # Added more months to keep it future-proof
            if href.lower().endswith(".pdf") and any(x in href.lower() for x in ["sale", "october", "sept", "nov", "dec", "jan"]):
                return href
    except Exception as e:
        logging.error(f"Error discovering PDF link: {e}")
    return None

def extract_properties_from_pdf(pdf_stream_or_path) -> List[Dict[str, Any]]:
    logging.info("Starting PDF text extraction...")
    reader = PdfReader(pdf_stream_or_path)
    total_pages = len(reader.pages)
    logging.info(f"Loaded PDF with {total_pages} pages.")

    full_text = ""
    for idx, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        full_text += f"\n--- PAGE {idx+1} ---\n" + page_text

    properties: List[Dict[str, Any]] = []
    
    # במקום לחתוך לפי מילה שבירה כמו Sale, נחפש את מספרי התיקים (תמיד מתחילים בשתי אותיות, מקף, ספרות)
    # ונחלץ את הטקסט שביניהם כ"בלוק" של נכס
    matches = list(re.finditer(r"([A-Z]{2}-\d{2}-\d{6})", full_text))
    
    if not matches:
        logging.warning("לא זוהו מספרי תיקים במסמך. ייתכן שפורמט המסמך השתנה לחלוטין.")
        return properties

    # איסוף הבלוקים וסינון כפילויות של מספרי תיק (נשמור את הבלוק הארוך ביותר לכל תיק)
    blocks_dict = {}
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i+1].start() if i + 1 < len(matches) else len(full_text)
        block = full_text[start:end]
        case_number = match.group(1)
        
        if case_number not in blocks_dict or len(block) > len(blocks_dict[case_number]):
            blocks_dict[case_number] = block

    # מעבר על כל בלוק וחילוץ השדות
    for case_number, block in blocks_dict.items():
        if len(block.strip()) < 30:
            continue

        try:
            sale_id_match = re.search(r"\b(\d{1,4}[A-Z]{3}\d{2})\b", block, re.I)
            sale_id = sale_id_match.group(1) if sale_id_match else ""

            status = "Active" # ברירת מחדל לרשימות חיות
            if re.search(r"\bPostponed\b", block, re.I):
                status = "Postponed"
            elif re.search(r"\bStayed\b", block, re.I):
                status = "Stayed"
            elif re.search(r"Third Party", block, re.I):
                status = "Sold (Third Party)"
            elif re.search(r"PLTF Overbid", block, re.I):
                status = "Sold (Plaintiff Overbid)"

            bid_match = re.search(r"\$\s*([\d,]+\.\d{2})", block)
            opening_bid = f"${bid_match.group(1)}" if bid_match else ""

            # זיהוי גמיש למספר חלקה
            parcel_match = re.search(r"(?:Parcel/Tax ID|Parcel ID|Block\s*(?:and|&)\s*Lot|Tax ID)[\s:]*([A-Za-z0-9\-]+)", block, re.I)
            parcel_id = parcel_match.group(1).strip() if parcel_match else ""

            municipality = ""
            muni_match = re.search(r"(?:Municipality)[\s:]*([A-Za-z\s]+?)(?=\n|Property|Parcel|Sale|$)", block, re.I)
            if muni_match:
                municipality = muni_match.group(1).strip()
            
            if not municipality:
                 # Fallback
                 muni_match_fallback = re.search(r"Municipality\s*\n\s*([A-Za-z\s]+)", block)
                 if muni_match_fallback:
                     municipality = muni_match_fallback.group(1).strip()

            address = ""
            address_candidates = []
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            for line in lines:
                # זיהוי רחוב אופייני או מיקוד בפנסילבניה
                if re.search(r"^\s*\d+\s+[A-Za-z0-9\s]+(?:ST|AVE|RD|DR|BLVD|WAY|LANE|LN|CT|PL|ROAD|STREET|AVENUE)\b", line, re.I):
                    address_candidates.append(line)
                elif re.search(r"\bPA\s*\d{5}\b", line, re.I):
                    address_candidates.append(line)

            address = ", ".join(address_candidates[:2]) if address_candidates else ""

            unique_id = f"ALLG-{case_number}"

            if address or parcel_id:
                properties.append({
                    "id": unique_id,
                    "sale_id": sale_id,
                    "case_number": case_number,
                    "status": status,
                    "opening_bid": opening_bid,
                    "parcel_id": parcel_id,
                    "address": address or "Address in comments / verify parcel",
                    "city": municipality or "Allegheny County",
                    "state": "PA",
                    "county": "Allegheny",
                    "source": "allegheny_sheriff",
                    "source_type": "sheriff_sale",
                    "scraped_at": datetime.utcnow().isoformat()
                })
        except Exception as e:
            logging.debug(f"Error parsing case {case_number}: {e}")
            continue

    return properties

def run(local_pdf_path: Optional[str] = None):
    results = []
    if local_pdf_path and os.path.exists(local_pdf_path):
        with open(local_pdf_path, "rb") as f:
            results = extract_properties_from_pdf(f)
    else:
        pdf_url = find_latest_pdf_url()
        if pdf_url:
            logging.info(f"Downloading PDF from: {pdf_url}")
            resp = requests.get(pdf_url, headers=HEADERS, timeout=40)
            if resp.status_code == 200:
                results = extract_properties_from_pdf(BytesIO(resp.content))

    if not results:
        logging.warning("No properties extracted from the Sheriff PDF.")
        return

    base_dir = os.path.join(os.path.dirname(__file__), "..")
    output_path = os.path.join(base_dir, "properties.json")
    status_path = os.path.join(base_dir, "scanner_status.json")

    existing = []
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    for item in existing:
        if item.get("source") == "allegheny_sheriff_pdf":
            item["source"] = "allegheny_sheriff"

    existing_ids = {p.get("id") for p in existing}
    added_count = 0
    active_count = 0

    for prop in results:
        if prop["status"] == "Active":
            active_count += 1
        if prop["id"] not in existing_ids:
            existing.append(prop)
            existing_ids.add(prop["id"])
            added_count += 1
        else:
            for ex in existing:
                if ex.get("id") == prop["id"]:
                    ex.update(prop)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    # עדכון אוטומטי של קובץ הסטטוס עבור הממשק
    status_data = {}
    if os.path.exists(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                status_data = json.load(f)
        except Exception:
            status_data = {}

    current_time_str = datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
    status_data["sheriff"] = {
        "status": "עודכן",
        "count": active_count,
        "last_update": current_time_str
    }
    if "sources" in status_data and isinstance(status_data["sources"], dict):
        status_data["sources"]["allegheny_sheriff"] = {
            "status": "active",
            "count": active_count,
            "last_updated": current_time_str
        }

    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    logging.info(f"Success! Found {len(results)} total deals ({active_count} ACTIVE). Updated properties.json and scanner_status.json.")

if __name__ == "__main__":
    custom_pdf = sys.argv[1] if len(sys.argv) > 1 else None
    run(custom_pdf)
