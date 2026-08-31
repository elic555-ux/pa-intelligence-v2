import json
import os
import sys
from datetime import datetime
import pytz

def get_pa_time():
    pa_tz = pytz.timezone('US/Eastern')
    return datetime.now(pa_tz)

def determine_active_scanners(pa_time, manual_sectors=None):
    """
    קובע אילו סורקים ירוצו לפי לוח הזמנים של פנסילבניה או לפי סריקה יזומה
    """
    if manual_sectors:
        print(f"[ON-DEMAND] Running specific sectors: {manual_sectors}")
        return manual_sectors

    weekday = pa_time.weekday()  # 0 = Monday, 1 = Tuesday, ..., 5 = Saturday, 6 = Sunday

    print(f"[SCHEDULER] Local PA Time: {pa_time.strftime('%Y-%m-%d %H:%M:%S %Z')} (Day: {weekday})")

    # Sunday (6) - Rest day
    if weekday == 6:
        print("[SCHEDULER] Sunday - No scheduled scans today (Rest Day).")
        return []

    active_scanners = []

    # 1. MLS is checked Mon-Sat at 12:00 PM EST
    if weekday in [0, 1, 2, 3, 4, 5]:
        active_scanners.append('mls')

    # 2. Full scan on Monday (Sheriff, Tax, Foreclosures, Probate)
    if weekday == 0:  # Monday
        active_scanners.extend(['sheriff', 'tax', 'reo', 'probate'])

    # 3. Thursday mid-week scan (Bank REO & Probate)
    elif weekday == 3:  # Thursday
        active_scanners.extend(['reo', 'probate'])

    # Remove duplicates
    active_scanners = list(dict.fromkeys(active_scanners))
    print(f"[SCHEDULER] Active scanners for today: {active_scanners}")
    return active_scanners

def run_orchestrator(manual_sectors=None):
    pa_now = get_pa_time()
    scanners_to_run = determine_active_scanners(pa_now, manual_sectors)

    if not scanners_to_run:
        print("[INFO] No scanners scheduled to run at this time.")
        return

    print(f"[ORCHESTRATOR] Successfully synchronized with PA Schedule: {scanners_to_run}")

if __name__ == "__main__":
    manual_args = sys.argv[1:] if len(sys.argv) > 1 else None
    run_orchestrator(manual_args)
