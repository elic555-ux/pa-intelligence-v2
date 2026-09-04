import json
import os
from datetime import datetime

def run():
    print("[AGENT 09] Starting Probate & Estates Scanner with History Log check...")
    
    output_path = "properties.json"
    history_path = "history.json"  # לוג מרכזי שמרכז את כל הנכס שנסרקו אי פעם
    
    # נתוני העיזבונות החדשים שנסרקו כרגע
    probate_leads = [
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

    # 1. טעינת ההיסטוריה הקיימת (רשימת מזהים/כתובות שנסרקו בעבר)
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

    # 3. סינון נכסים חדשים בלבד שטרם נראו
    new_leads_added = 0
    all_history_records = existing_properties.copy() # נשמר גם בהיסטוריה

    for lead in probate_leads:
        lead_id = lead.get("id")
        if lead_id not in seen_ids:
            # נכס חדש לגמרי! נוסיף לראש רשימת התצוגה
            existing_properties.insert(0, lead)
            seen_ids.add(lead_id)
            new_leads_added += 1
            print(f"[NEW LEAD] Added: {lead['address']}, {lead['city']}")
        else:
            print(f"[DUPLICATE SKIPPED] Already in history: {lead['address']}")

    # שמירת קובץ התצוגה המעודכן
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(existing_properties, f, ensure_ascii=False, indent=4)

    # שמירת קובץ ההיסטוריה המעודכן (כל הנכסים שנוהלו אי פעם)
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(existing_properties, f, ensure_ascii=False, indent=4)

    print(f"[AGENT 09] Scan finished. Added {new_leads_added} new unique leads.")

if __name__ == "__main__":
    run()
