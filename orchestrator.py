import os
import sys
import importlib
from datetime import datetime
import pytz

def run_orchestrator():
    # קליטת הפרמטרים שהועברו מהממשק או מברירת המחדל
    scanner_name = os.environ.get("INPUT_SCANNER", "06_probate_estates")
    target_county = os.environ.get("INPUT_COUNTY", "Allegheny")
    target_city = os.environ.get("INPUT_CITY", "Homestead")
    
    print(f"[ORCHESTRATOR] Executing scanner: {scanner_name} | County: {target_county} | City: {target_city}")

    try:
        # טעינה דינמית של מודול הסורק מתוך תיקיית scrapers
        module_path = f"scrapers.{scanner_name}"
        scanner_module = importlib.import_module(module_path)
        
        if hasattr(scanner_module, 'run'):
            # הרצת הסורק עם הפרמטרים
            scanner_module.run(target_county=target_county, target_city=target_city)
        else:
            print(f"[WARNING] Module {scanner_name} does not have a 'run' function.")
        
        print("[ORCHESTRATOR] Execution finished successfully.")
    except Exception as e:
        print(f"[ERROR] Failed during execution of {scanner_name}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_orchestrator()
