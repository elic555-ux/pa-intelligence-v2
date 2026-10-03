import json
import os
import re

CONFIG_FILE = 'scan_config.json'
PROPERTIES_FILE = 'properties.json'

# מילות מפתח שמנוע ה-AI מחפש כדי לזהות יורשים, עיזבונות ומוכרים לחוצים בשוק החופשי
MOTIVATED_KEYWORDS = [
    r'\bestate sale\b', r'\bprobate\b', r'\bexecutor\b', r'\bheirs?\b',
    r'\bcourt approval\b', r'\bsold to settle\b', r'\bsettling estate\b',
    r'\bowner passed\b', r'\binvestor special\b', r'\bhandyman special\b',
    r'\bneeds tlc\b', r'\bsold as-is\b', r'\bas is\b', r'\bmotivated seller\b',
    r'\bfix and flip\b', r'\bneeds total rehab\b', r'\bfsbo\b', r'\bfor sale by owner\b'
]

def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(data, filepath):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def run_probate_miner():
    print("🔍 מתחיל בסריקת מודיעין לעיזבונות, יורשים ו-FSBO בשוק החופשי...")

    config = load_json(CONFIG_FILE) or {}
    sectors = config.get('sectors', [])

    if '06_probate_estates' not in sectors and 'all' not in sectors:
        print("⏭️ סקטור העיזבונות כבוי בהגדרות. מדלג על הסריקה.")
        return

    properties = load_json(PROPERTIES_FILE)
    if not properties:
        print("❌ קובץ properties.json לא נמצא. חסר בסיס נתונים לסריקה.")
        return

    target_cities = [c.lower() for c in config.get('cities', [])]
    target_counties = [c.lower() for c in config.get('counties', [])]

    probate_count = 0
    fixed_count = 0
    compiled_keywords = [re.compile(kw, re.IGNORECASE) for kw in MOTIVATED_KEYWORDS]

    for prop in properties:
        if prop.get('is_archived', False):
            continue

        source_type = str(prop.get('source_type', '')).lower()
        
        # --- מנגנון ריפוי עצמי (Self-Healing) ---
        # מחזיר נכסי שריף/מס/בנקים שתויגו בטעות חזרה לסטטוס המקורי שלהם
        if source_type in ['sheriff', 'tax', 'reo']:
            if prop.get('deal_type') == 'probate_fsbo':
                if source_type == 'sheriff':
                    prop['deal_type'] = 'Sheriff Sale'
                elif source_type == 'tax':
                    prop['deal_type'] = 'Tax Sale Candidate'
                elif source_type == 'reo':
                    prop['deal_type'] = 'Bank REO'
                
                # מחיקת טקסט ה-AI שנוסף בטעות
                if 'ai_summary' in prop:
                    prop['ai_summary'] = prop['ai_summary'].replace("🔥 **מודיעין AI:** הנכס זוהה בוודאות כעיזבון/נכס ליורשים/FSBO. המוכרים לרוב מחפשים נזילות מהירה, יש כאן פוטנציאל גבוה ל-Lowball Offer (הצעה מתחת למחיר שוק). ", "")
                fixed_count += 1
            continue # דילוג! לא מבצעים חיפוש מילות מפתח על נכסי שריף/מס

        # מכאן והלאה: ממשיכים רק אם זה נכס MLS רגיל או FSBO
        city = str(prop.get('city', '')).lower()
        county = str(prop.get('county', '')).lower()

        if target_cities and city not in target_cities:
            if target_counties and county not in target_counties:
                continue

        summary = str(prop.get('summary', ''))
        desc = str(prop.get('description', ''))
        remarks = str(prop.get('remarks', ''))
        full_text = f"{summary} {desc} {remarks}"

        match_found = False
        for pattern in compiled_keywords:
            if pattern.search(full_text):
                match_found = True
                break

        is_fsbo = source_type == 'fsbo'

        if match_found or is_fsbo:
            old_type = str(prop.get('deal_type', ''))
            
            if 'probate' not in old_type.lower() and 'fsbo' not in old_type.lower():
                prop['deal_type'] = 'probate_fsbo'

                current_score = prop.get('deal_score', 70)
                if isinstance(current_score, (int, float)):
                    prop['deal_score'] = min(99, int(current_score) + 12)

                prop['strategy'] = 'value_add'

                existing_ai_summary = prop.get('ai_summary', '')
                prop['ai_summary'] = f"🔥 **מודיעין AI:** הנכס זוהה בוודאות כעיזבון/נכס ליורשים/FSBO. המוכרים לרוב מחפשים נזילות מהירה, יש כאן פוטנציאל גבוה ל-Lowball Offer (הצעה מתחת למחיר שוק). {existing_ai_summary}"

                probate_count += 1

    if probate_count > 0 or fixed_count > 0:
        save_json(properties, PROPERTIES_FILE)
        print(f"✅ פעולת הסוכן הושלמה!")
        print(f"   - {fixed_count} נכסי שריף/מס תוקנו והוסרו מרשימת העיזבונות.")
        print(f"   - {probate_count} נכסי שוק חופשי (MLS) אותרו כעיזבונות/FSBO.")
    else:
        print("ℹ️ לא נמצאו שינויים לביצוע במאגר הנוכחי.")

if __name__ == "__main__":
    run_probate_miner()
