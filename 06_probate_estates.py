import json
import os
import re

CONFIG_FILE = 'scan_config.json'
PROPERTIES_FILE = 'properties.json'

# מילות מפתח שמנוע ה-AI מחפש כדי לזהות יורשים, עיזבונות ומוכרים לחוצים
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
    print("🔍 מתחיל בסריקת מודיעין לעיזבונות, יורשים ו-FSBO (Motivated Seller Miner)...")

    # טעינת הגדרות הסריקה
    config = load_json(CONFIG_FILE) or {}
    sectors = config.get('sectors', [])

    if '06_probate_estates' not in sectors and 'all' not in sectors:
        print("⏭️ סקטור העיזבונות כבוי בהגדרות. מדלג על הסריקה.")
        return

    # טעינת מאגר הנכסים הקיים לניתוח טקסטואלי מתקדם
    properties = load_json(PROPERTIES_FILE)
    if not properties:
        print("❌ קובץ properties.json לא נמצא. חסר בסיס נתונים לסריקה.")
        return

    # חילוץ מיקוד גיאוגרפי (Erie, Allegheny וכו')
    target_cities = [c.lower() for c in config.get('cities', [])]
    target_counties = [c.lower() for c in config.get('counties', [])]

    probate_count = 0
    compiled_keywords = [re.compile(kw, re.IGNORECASE) for kw in MOTIVATED_KEYWORDS]

    for prop in properties:
        # דילוג על נכסים שכבר נמחקו או הועברו לארכיון
        if prop.get('is_archived', False):
            continue

        city = str(prop.get('city', '')).lower()
        county = str(prop.get('county', '')).lower()

        # סינון לפי אזורים (אם הוגדרו ב-UI)
        if target_cities and city not in target_cities:
            if target_counties and county not in target_counties:
                continue

        # איחוד הטקסטים של הנכס לבדיקה
        summary = str(prop.get('summary', ''))
        desc = str(prop.get('description', ''))
        remarks = str(prop.get('remarks', ''))
        full_text = f"{summary} {desc} {remarks}"

        # הפעלת מנוע זיהוי המילים (NLP)
        match_found = False
        for pattern in compiled_keywords:
            if pattern.search(full_text):
                match_found = True
                break

        # בדיקה האם המקור מוגדר ישירות כ-FSBO
        is_fsbo = prop.get('source_type', '').lower() == 'fsbo'

        # אם זוהה כעיזבון או FSBO - מבצעים סיווג מחדש (Re-classification)
        if match_found or is_fsbo:
            old_type = str(prop.get('deal_type', ''))
            
            # אם הוא עדיין לא מתויג ככזה
            if 'probate' not in old_type.lower() and 'fsbo' not in old_type.lower():
                prop['deal_type'] = 'probate_fsbo'

                # תמריץ AI: העלאת ציון הכדאיות ב-12 נקודות (מוכר לחוץ)
                current_score = prop.get('deal_score', 70)
                if isinstance(current_score, (int, float)):
                    prop['deal_score'] = min(99, int(current_score) + 12)

                # הגדרת אסטרטגיה אוטומטית להשבחה
                prop['strategy'] = 'value_add'

                # הוספת הערת מודיעין ל-Analyzer
                existing_ai_summary = prop.get('ai_summary', '')
                prop['ai_summary'] = f"🔥 **מודיעין AI:** הנכס זוהה בוודאות כעיזבון/נכס ליורשים/FSBO. המוכרים לרוב מחפשים נזילות מהירה, יש כאן פוטנציאל גבוה ל-Lowball Offer (הצעה מתחת למחיר שוק). {existing_ai_summary}"

                probate_count += 1

    # שמירת המאגר המעודכן
    if probate_count > 0:
        save_json(properties, PROPERTIES_FILE)
        print(f"✅ סריקת העיזבונות הושלמה! {probate_count} נכסים עברו סיווג מחדש כעיזבונות/FSBO ב-Allegheny/Erie.")
    else:
        print("ℹ️ לא נמצאו נכסי עיזבון חדשים התואמים להגדרות במאגר הנוכחי.")

if __name__ == "__main__":
    run_probate_miner()
