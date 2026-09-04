import json
import os
import sys
import importlib
from datetime import datetime
import pytz

def get_pa_time():
    pa_tz = pytz.timezone('US/Eastern')
    return datetime.now(pa_tz)

def run_orchestrator(manual_sectors=None):
    pa_now = get_pa_time()
    print(f"[ORCHESTRATOR] Local PA Time: {pa_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("[ORCHESTRATOR] Mode: Focused Single-Agent Test -> Probate & Estates (Agent 09)")

    try:
        print("[RUNNER] Executing 06_probate_estates.py...")
        # טעינה דינמית של מודול המתחיל במספר
        probate_agent = importlib.import_module('scrapers.06_probate_estates')
        
        if hasattr(probate_agent, 'run'):
            probate_agent.run()
        else:
            print("[WARNING] 'run()' method not explicitly found, check script structure.")
        
        print("[ORCHESTRATOR] Probate scan completed successfully and saved to properties.json.")
    except Exception as e:
        print(f"[ERROR] Failed during probate scan execution: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_orchestrator()
