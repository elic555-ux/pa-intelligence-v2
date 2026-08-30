import os
import json
import importlib

# רשימת הסוכנים להרצה טורית מבוקרת
AGENT_CHAIN = [
    {"name": "01_realtor_mls", "module": "scrapers.01_realtor_mls", "desc": "שוק פתוח וירידות מחיר (MLS)"},
    {"name": "02_distressed_reo", "module": "scrapers.02_distressed_reo", "desc": "נכסי בנק, כינוס ומצוקה"},
    {"name": "03_philly_sheriff", "module": "scrapers.03_philly_sheriff", "desc": "מכירות שריף מחוז פילדלפיה"},
    {"name": "04_allegheny_sheriff", "module": "scrapers.04_allegheny_sheriff", "desc": "מכירות שריף פיטסבורג ואלגני"},
    {"name": "05_tax_delinquent", "module": "scrapers.05_tax_delinquent", "desc": "פיגורי מס וארנונה מחוזיים"},
    {"name": "06_probate_estates", "module": "scrapers.06_probate_estates", "desc": "עיזבונות, ירושות וצוואות"}
]

def run_pipeline():
    master_records = {}
    print("==================================================")
    print("[PA-Orchestrator] מתחיל סבב סריקה טורי רב-סוכני...")
    print("==================================================")

    for step in AGENT_CHAIN:
        agent_name = step["name"]
        agent_desc = step["desc"]
        print(f"\n--> מפעיל סוכן: {agent_name} ({agent_desc})")

        try:
            mod = importlib.import_module(step["module"])
            leads = mod.run_collector() or []
            print(f"[PA-Orchestrator] סוכן {agent_name} סיים בהצלחה. אותרו: {len(leads)} נכסים.")

            # איחוד וניקוי כפילויות לפי כתובת ומזהה
            for item in leads:
                key = (item.get("address", "") + "_" + item.get("zip", "")).strip().lower()
                if not key or key == "_":
                    key = item.get("id", str(len(master_records)))
                
                # אם הנכס כבר קיים, נשמור את הסיווג המשמעותי ביותר
                if key not in master_records:
                    master_records[key] = item
                else:
                    if item.get("deal_type") not in ["Active MLS", "Residential"]:
                        master_records[key]["deal_type"] = item["deal_type"]

        except ModuleNotFoundError:
            print(f"[PA-Orchestrator] אזהרה: מודול {agent_name} טרם נוצר, מדלג לשלב הבא.")
        except Exception as e:
            print(f"[PA-Orchestrator] שגיאה בהרצת {agent_name}: {e}. ממשיך לסוכן הבא...")

    # שמירת קובץ הנתונים המרכזי
    final_list = list(master_records.values())
    with open("properties.json", "w", encoding="utf-8") as f:
        json.dump(final_list, f, indent=2, ensure_ascii=False)

    print("\n==================================================")
    print(f"[PA-Orchestrator] סבב הסריקה הושלם! נשמרו {len(final_list)} נכסים מאומתים ב-properties.json.")
    print("==================================================")

if __name__ == "__main__":
    run_pipeline()
