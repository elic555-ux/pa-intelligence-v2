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
            if href.lower().endswith(".pdf") and ("sale" in href.lower() or "october" in href.lower() or "sept" in href.lower()):
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
    blocks = re.split(r"(?:\n|^)Sale\s*\n", full_text)

    for block in blocks:
        if "Parcel/Tax ID:" not in block and "Case Number" not in block:
            continue

        try:
            case_match = re.search(r"([A-Z]{2}-\d{2}-\d{6})", block)
            case_number = case_match.group(1) if case_match else ""

            sale_id_match = re.search(r"\b(\d{1,4}[A-Z]{3}\d{2})\b", block)
            sale_id = sale_id_match.group(1) if sale_id_match else ""

            status = "Unknown"
            if re.search(r"\bActive\b", block, re.I):
                status = "Active"
            elif re.search(r"\bPostponed\b", block, re.I):
                status = "Postponed"
            elif re.search(r"\bStayed\b", block, re.I):
                status = "Stayed"
            elif "Third Party" in block:
                status = "Sold (Third Party)"
            elif "PLTF Overbid" in block:
                status = "Sold (Plaintiff Overbid)"

            bid_match = re.search(r"\$([\d,]+\.\d{2})", block)
            opening_bid = f"${bid_match.group(1)}" if bid_match else ""

            parcel_match = re.search(r"Parcel/Tax ID:\s*([A-Za-z0-9\-]+)", block)
            parcel_id = parcel_match.group(1).strip() if parcel_match else ""

            municipality = ""
            address = ""
            if "Property" in block and "Municipality" in block:
                prop_section = block.split("Property")[1]
                lines = [line.strip() for line in prop_section.split("\n") if line.strip()]
                
                muni_match = re.search(r"Municipality\s*\n\s*([A-Za-z\s]+)", prop_section)
                if muni_match:
                    municipality = muni_match.group(1).strip()

                address_candidates = []
                for line in lines:
                    if re.search(r"\d+\s+[A-Za-z0-9\s]+(?:ST|AVE|RD|DR|BLVD|WAY|LANE|CT|PL)", line, re.I):
                        address_candidates.append(line)
                    elif re.search(r"[A-Z\s]+,\s*PA\s*\d{5}", line):
                        address_candidates.append(line)

                address = " ".join(address_candidates[:2]) if address_candidates else ""

            unique_id = f"ALLG-{case_number}" if case_number else f"ALLG-{sale_id or abs(hash(block)) % 10000000}"

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
        except Exception:
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
            resp = requests.get(pdf_url, headers=HEADERS, timeout=40)
            if resp.status_code == 200:
                results = extract_properties_from_pdf(BytesIO(resp.content))

    if not results:
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

    logging.info(f"Success! Found {active_count} ACTIVE deals. Updated properties.json and scanner_status.json.")

if __name__ == "__main__":
    custom_pdf = sys.argv[1] if len(sys.argv) > 1 else None
    run(custom_pdf)
