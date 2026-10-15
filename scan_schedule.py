"""Read the UI's saved schedules. Standard library only; no source requests."""
import argparse
from datetime import datetime, time, timezone
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from completion_wakeup import idle_unchanged

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def read(path, default):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default


def due_slot(schedule, current, last_checked=None, default_zone='America/New_York'):
    if not isinstance(schedule, dict) or schedule.get('enabled') is False:
        return None
    zone = ZoneInfo(schedule.get('timezone') or default_zone)
    local = current.astimezone(zone)
    frequency = schedule.get('frequency') or ('daily' if schedule.get('day', 'Everyday') == 'Everyday' else 'weekly')
    if frequency == 'off':
        return None
    if frequency not in {'daily', 'twice_daily', 'weekly', 'monthly', 'hourly', 'every_3_hours', 'every_15_minutes'}:
        raise ValueError('Unsupported schedule frequency')
    if frequency == 'weekly' and schedule.get('day', 'Wednesday') != DAYS[local.weekday()]:
        return None
    if frequency == 'monthly' and local.day != int(schedule.get('dayOfMonth', 1)):
        return None
    if frequency in {'hourly', 'every_3_hours', 'every_15_minutes'}:
        hour = local.hour if frequency != 'every_3_hours' else local.hour // 3 * 3
        minute = local.minute // 15 * 15 if frequency == 'every_15_minutes' else 0
        # Preserve the repeated hour's fold; compare actual instants below.
        slots = [local.replace(hour=hour, minute=minute, second=0, microsecond=0)]
    else:
        times = schedule.get('times') or [schedule.get('time', '08:00')]
        if not isinstance(times, list) or len(times) != (2 if frequency == 'twice_daily' else 1):
            raise ValueError('Invalid schedule times')
        if len(set(times)) != len(times):
            raise ValueError('Duplicate schedule times')
        slots = []
        for value in times:
            if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):00', value):
                raise ValueError('Schedules support whole hours')
            slots.append(datetime.combine(local.date(), time(int(value[:2])), zone))
    elapsed = [slot for slot in slots if slot.astimezone(timezone.utc) <= current.astimezone(timezone.utc)]
    if not elapsed:
        return None
    latest = max(elapsed)
    try:
        previous = datetime.fromisoformat(str(last_checked).replace('Z', '+00:00'))
        if previous.tzinfo is not None and previous.astimezone(timezone.utc) >= latest.astimezone(timezone.utc):
            return None
    except ValueError:
        pass
    return latest.isoformat()


def scan_sectors(profile, report, current):
    if profile.get('autoScanEnabled') is not True:
        return []
    sources = report.get('sources', {})
    return [sector for sector in profile.get('sectors', []) if sector in {'mls', 'reo', 'sheriff', 'tax'}
            and due_slot(profile.get('schedules', {}).get(sector, {'enabled': False}), current,
                         (sources.get(sector) or {}).get('checked_at'))]


def completion_batch_size(config):
    value = config.get('batchSize', 3)
    # Migrate the previous fixed-size UI setting conservatively.
    if type(value) is int and value == 10:
        return 3
    if type(value) is not int or value not in {3, 4, 5}:
        raise ValueError('Completion batch size must be 3, 4 or 5')
    return value


def completion_daily_limit(config):
    cap = config.get('dailyLimit', 60)
    if type(cap) is not int or cap < 10 or cap > 120 or cap % 10:
        raise ValueError('Invalid daily completion limit')
    return cap


def completion_due(profile, state, current):
    config = profile.get('completionSettings') or {}
    if profile.get('scheduleControlVersion') != 2 or config.get('enabled') is not True:
        return False
    completion_batch_size(config)
    cap = completion_daily_limit(config)
    day = current.astimezone(ZoneInfo(config.get('timezone', 'America/New_York'))).date().isoformat()
    used = int(state.get('daily_attempted', 0)) if state.get('budget_day') == day else 0
    return used < cap and due_slot(config, current, state.get('last_automatic_run')) is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['scan', 'completion'])
    args = parser.parse_args()
    profile = read('scan_config.json', {})
    current = datetime.now(timezone.utc)
    # Old profiles cannot start background work until the new UI is saved.
    due = False
    if profile.get('scheduleControlVersion') == 2:
        due = bool(scan_sectors(profile, read('scanner_status.json', {}), current)) if args.mode == 'scan' else completion_due(
            profile, read('COMPS_REPORTS/automatic_listing_sources/state.json', {}), current)
    if due and args.mode == 'completion':
        state = read('COMPS_REPORTS/automatic_listing_sources/state.json', {})
        if idle_unchanged('.', profile, state, current.date()):
            due = False
            print('השלמת הפרטים בהמתנה לנכסים חדשים או לשינוי נתוני מועמד; אין בקשות מקור')
    print('עבודה ממתינה לפי ההגדרות השמורות' if due else 'אין עבודה מתוזמנת כעת; לא יופעלו מקורות')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('due=' + str(due).lower() + '\n')


if __name__ == '__main__':
    main()


