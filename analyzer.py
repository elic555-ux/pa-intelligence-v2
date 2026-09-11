import json
import requests
import time
import os

PROPERTIES_FILE = 'properties.json'

def get_coordinates(address, city, state="PA"):
    """ מתחבר לשירות מפות חינמי להמרת כתובת לקואורדינטות """
    if not address or not city: return None, None
    
    # ניקוי הכתובת כדי למנוע שגיאות חיפוש
    clean_addr = address.split(',')[0].strip()
    query = f"{clean_addr}, {city}, {state}"
    url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
    headers = {'User-Agent': 'PA-RealEstate-Hub/1.0'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        print(f"⚠️ שגיאת מיקום עבור {query}: {e}")
    return None, None

def calculate_metrics(prop):
    """ מנוע הפיננסים והשכונות - מחשב MAO, ARV ותקציר מנהלים """
    price = prop.get('price') or 50000
    sqft = prop.get('sqft') or 1300
    beds = prop.get('beds') or 3
    city = prop.get('city') or 'Unknown'

    # 1. חישוב ARV משוער (לפי חוקי אצבע למצוקה)
    arv = int(price * 1.6) if price < 100000 else int(price * 1.35)
    
    # 2. עלויות שיפוץ (Rehab)
    flip_rehab = sqft * 45
    rental_rehab = sqft * 25
    
    # 3. MAO לפליפ (כלל 70%)
    mao_flip = int((arv * 0.70) - flip_rehab)
    
    # 4. MAO לשכירות (10% Cap Rate)
    monthly_rent = 950 + (beds * 180)
    annual_rent = monthly_rent * 12
    noi = annual_rent * 0.74 # הפחתת 26% הוצאות
    mao_rental = int((noi / 0.10) - rental_rehab - 5000) # 5k הוצאות סגירה

    # 5. דירוג שכונה (Neighborhood Class - סימולציה)
    if price < 60000: hood_class = "C-"
    elif price < 90000: hood_class = "C+"
    elif price < 150000: hood_class = "B"
    else: hood_class = "A-"

    # 6. יצירת תקציר AI חכם
    summary = f"נכס ב-{city} (שכונת Class {hood_class}). ARV משוער של ${arv:,}. "
    summary += f"כדי להרוויח 10% תשואה בשכירות, ה-MAO המומלץ הוא ${mao_rental:,}. "
    summary += f"לאסטרטגיית פליפ, אין לשלם מעל ${mao_flip:,} (בהנחת שיפוץ יסודי של ${flip_rehab:,})."

    return {
        "arv": arv,
        "flip_rehab": flip_rehab,
        "rental_rehab": rental_rehab,
        "mao_flip": mao_flip,
        "mao_rental": mao_rental,
        "monthly_rent_est": monthly_rent,
        "neighborhood_class": hood_class,
        "ai_summary": summary
    }

def run_analyzer():
    print("🧠 מתחיל ריצת אנליסט מתקדמת (Analyzer)...")
    if not os.path.exists(PROPERTIES_FILE):
        print("❌ קובץ הנכסים לא נמצא.")
        return

    with open(PROPERTIES_FILE, 'r', encoding='utf-8') as f:
        properties = json.load(f)

    updated_properties = []
    analyzed_count = 0

    for prop in properties:
        # בדיקה האם הנכס כבר נותח כדי לא לבזבז קריאות שרת חינם
        if 'lat' not in prop or 'mao_flip' not in prop:
            print(f"מנתח את: {prop.get('address', 'Unknown')}...")
            
            # שלב א': מציאת קואורדינטות למפה
            lat, lng = get_coordinates(prop.get('address'), prop.get('city'))
            if lat and lng:
                prop['lat'] = lat
                prop['lng'] = lng
            
            # שלב ב': מתמטיקה ותקציר
            metrics = calculate_metrics(prop)
            prop.update(metrics)
            
            analyzed_count += 1
            
            # השהייה של שנייה כדי לא להיחסם על ידי שרת המפות החינמי
            time.sleep(1.1)
            
        updated_properties.append(prop)

    with open(PROPERTIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(updated_properties, f, ensure_ascii=False, indent=2)
        
    print(f"✅ האנליסט סיים עבודה! הועשרו {analyzed_count} נכסים בקואורדינטות, דירוג ו-MAO.")

if __name__ == '__main__':
    run_analyzer()
