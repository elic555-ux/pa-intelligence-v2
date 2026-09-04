import json
import os
import sys
from datetime import datetime

def run(target_county="Allegheny", target_city="Homestead"):
    print(f"[AGENT 09 - PROBATE] Starting scan for County: {target_county}, City/Focus: {target_city}...")
    
    output_path = "properties.json"
    history_path = "history.json"
    
    # מאגר תוצאות המדמה שליפה חיה לפי מחוז/עיר שנבחרה
    all_leads_pool = [
        {
            "id": "homestead_pr_1",
            "address": "412 E 14th Ave",
            "city": "Homestead",
            "state": "PA",
            "zip": "15120",
            "county": "Allegheny",
            "price": 42000,
            "arv": 155000,
            "deal_type": "Probate / Estate",
            "summary": "עיזבון טרי, בית ריק מעל שנה, יורשים מעוניינים בסגירה מהירה במזומן (As-Is).",
            "sqft": 1380,
            "beds": 3,
            "baths": 1.5,
            "status": "Active Lead",
            "docket_id": "PR-2026-0412",
            "margin_estimate": "73%",
            "listed_date": datetime.now().strftime("%d/%m/%Y")
        },
        {
            "id": "homestead_pr_2",
            "address": "228 W 15th Ave",
            "city": "Homestead",
            "state": "PA",
            "zip": "15120",
            "county": "Allegheny",
            "price": 38000,
            "arv": 145000,
            "deal_type": "Probate / Estate",
            "summary": "נכס בירושה, יורשים מחוץ למדינה (Out-of-state), דורש פינוי תכולה ושיפוץ פנים.",
            "sqft": 1250,
            "beds": 3,
            "baths": 1,
            "status": "Active Lead",
            "docket_id": "PR-2026-0228",
            "margin_estimate": "70%",
            "listed_date": datetime.now().strftime("%d/%m/%Y")
        },
        {
            "id": "homestead_pr_3",
            "address": "3808 Main St",
            "city": "Munhall",
            "state": "PA",
            "zip": "15120",
            "county": "Allegheny",
            "price": 58000,
            "arv": 175000,
            "deal_type": "Probate / Estate",
            "summary": "גובל בהומסטד, טאבו עיזבון מאושר (Letters Testamentary), נכס סגור הדורש חידוש מערכות.",
            "sqft": 1520,
            "beds": 4,
            "baths": 2,
            "status": "Active Lead",
            "docket_id": "PR-2026-3808",
            "margin_estimate": "66%",
            "listed_date": datetime.now().strftime("%d/%m/%Y")
        },
        {
            "id": "homestead_pr_4",
            "address": "715 Sarah St",
            "city": "West Homestead",
            "state": "PA",
            "zip": "15120",
            "county": "Allegheny",
            "price": 45000,
            "arv": 160000,
            "deal_type": "Probate / Estate",
            "summary": "יורש יחיד, דורש איטום מרתף ועדכון לוח חשמל, מוטיבציה גבוהה למכירה מהירה.",
            "sqft": 1300,
            "beds": 3,
            "baths": 1,
            "status": "Active Lead",
            "docket_id": "PR-2026-0715",
            "margin_estimate": "71%",
            "listed_date": datetime.now().strftime("%d/%m/%Y")
        }
    ]

    # סינון התוצאות לפי המחוז או העיר שהתקבלו
    scanned_leads = [
        lead for lead in all_leads_pool 
        if target_county.lower() in lead["county"].lower() or target_city.lower() in lead["city"].lower()
    ]

    # 1. טעינת לוג ההיסטוריה המרכזי (כדי לדעת איזה נכסים כבר ראינו בעבר)
    seen_ids = set()
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history_data = json.load(f)
                seen_ids = {item.get("id") for item in history_data}
        except Exception as e:
            print(f"[WARNING] Could not read history.json: {e}")

    # 2. טעינת נכסים פעילים קיימים ב-properties.json
    existing_properties = []
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing_properties = json.load(f)
        except Exception as e:
            print(f"[WARNING] Could not read existing properties.json: {e}")

    # 3. הצלבה: הוספת נכסים חדשים בלבד שאינם מופיעים בהיסטוריה
    new_leads_added = 0
    all_history_records = existing_properties.copy()

    for lead in scanned_leads:
        lead_id = lead.get("id")
        if lead_id not in seen_ids:
            existing_properties.insert(0, lead)
            all_history_records.insert(0, lead)
            seen_ids.add(lead_id)
            new_leads_added += 1
            print(f"[NEW LEAD ADDED] {lead['address']}, {lead['city']}")
        else:
            print(f"[SKIPPED - ALREADY IN HISTORY] {lead['address']}")

    # שמירת קובץ התצוגה המעודכן לממשק
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(existing_properties, f, ensure_ascii=False, indent=4)

    # שמירת קובץ ההיסטוריה המרכזי למניעת כפילויות עתידיות
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(all_history_records, f, ensure_ascii=False, indent=4)

    print(f"[AGENT 09] Scan complete. Added {new_leads_added} new unique probate leads.")

if __name__ == "__main__":
    # אפשר לקבל פרמטרים משורת הפקודה או להשתמש בברירת מחדל
    county_arg = sys.argv[1] if len(sys.argv) > 1 else "Allegheny"
    city_arg = sys.argv[2] if len(sys.argv) > 2 else "Homestead"
    run(target_county=county_arg, target_city=city_arg)
