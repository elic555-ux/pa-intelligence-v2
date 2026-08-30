import os
import json
import importlib
import sys

# הוספת תיקיית scrapers לנתיב המערכת
sys.path.append(os.path.join(os.path.dirname(__file__), 'scrapers'))

MODULES = [
    ("01_realtor_mls", "שוק פתוח וירידות מחיר (MLS)"),
    ("02_distressed_reo", "נכסי בנק, כינוס ומצוקה"),
    ("03_philly_sheriff", "מכירות שריף מחוז פילדלפיה"),
    ("04_allegheny_sheriff", "מכירות שריף פיטסבורג ואלגני"),
    ("05_tax_delinquent", "פיגורי מס וארנונה מחוזיים"),
    ("06_probate_estates", "עיזבונות, ירושות ו-FSBO")
]

def run_all_scrapers():
    print("=" * 50)
    print("[PA-Orchestrator] מתחיל סבב סריקה טורי רב-סוכני מורחב...")
    print("=" * 50)
    
    all_properties = []
    
    for module_name, description in MODULES:
        print(f"\n--> מפעיל סוכן: {module_name} ({description})")
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, "run_collector"):
                results = mod.run_collector()
                if isinstance(results, list):
                    all_properties.extend(results)
                    print(f"[PA-Orchestrator] סוכן {module_name} סיים בהצלחה. אותרו: {len(results)} נכסים.")
                else:
                    print(f"[PA-Orchestrator] אזהרה: סוכן {module_name} לא החזיר רשימה.")
            else:
                print(f"[PA-Orchestrator] שגיאה: הפונקציה run_collector חסרה במודול {module_name}.")
        except ModuleNotFoundError:
            # בדיקה אם קיים תחת שם חלופי fsbo
            if module_name == "06_probate_estates":
                try:
                    mod = importlib.import_module("06_probate_fsbo")
                    results = mod.run_collector()
                    all_properties.extend(results)
                    print(f"[PA-Orchestrator] סוכן 06_probate_fsbo סיים בהצלחה. אותרו: {len(results)} נכסים.")
                    continue
                except Exception:
                    pass
            print(f"[PA-Orchestrator] אזהרה: מודול {module_name} טרם נוצר, מדלג לשלב הבא.")
        except Exception as e:
            print(f"[PA-Orchestrator] שגיאה חריגה בהרצת {module_name}: {e}")

    # ניקוי כפילויות לפי מזהה ייחודי
    unique_deals = {}
    for prop in all_properties:
        prop_id = prop.get("id") or prop.get("address")
        if prop_id and prop_id not in unique_deals:
            unique_deals[prop_id] = prop

    final_list = list(unique_deals.values())
    
    with open("properties.json", "w", encoding="utf-8") as f:
        json.dump(final_list, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 50)
    print(f"[PA-Orchestrator] סבב הסריקה הושלם! נשמרו {len(final_list)} נכסים מאומתים ב-properties.json.")
    print("=" * 50)

if __name__ == "__main__":
    run_all_scrapers()
