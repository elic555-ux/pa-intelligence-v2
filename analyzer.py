import json
import requests
import time
import os
import math
import re
from datetime import datetime, timezone, timedelta

PROPERTIES_FILE = 'properties.json'
GEOCODE_CACHE_FILE = 'geocode_cache.json'
CONFIG_FILE = 'scan_config.json'
GEOCODE_RETRY_DAYS = 30
MAX_GEOCODE_REQUESTS_PER_RUN = 20
ANALYZER_VERSION = '2.4-documented-financials-20261007'


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_geocode_key(address, city, state="PA"):
    parts = [str(address or "").strip().lower(), str(city or "").strip().lower(), str(state or "").strip().lower()]
    return " | ".join(" ".join(p.split()) for p in parts)


def normalize_county(value):
    text = " ".join(str(value or "").casefold().replace(",", " ").split())
    if text.endswith(" county"):
        text = text[:-7].strip()
    return text


def load_selected_counties():
    """Return only the counties selected for the current scanner configuration."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        values = config.get('counties', []) if isinstance(config, dict) else []
        return {normalize_county(value) for value in values if normalize_county(value)}
    except (OSError, json.JSONDecodeError, TypeError) as e:
        print(f"⚠️ לא ניתן לקרוא את המחוזות שנבחרו; Analyzer לא יבצע עיבוד נכסים: {e}")
        return set()


def load_geocode_cache():
    if not os.path.exists(GEOCODE_CACHE_FILE):
        return {}
    try:
        with open(GEOCODE_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"⚠️ לא ניתן לקרוא Geocode Cache: {e}")
        return {}


def save_geocode_cache(cache):
    temp_file = GEOCODE_CACHE_FILE + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, GEOCODE_CACHE_FILE)


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def failed_geocode_is_fresh(entry):
    if not isinstance(entry, dict) or entry.get('status') != 'unavailable':
        return False
    attempted = parse_iso(entry.get('attempted_at'))
    return bool(attempted and datetime.now(timezone.utc) - attempted < timedelta(days=GEOCODE_RETRY_DAYS))


def get_coordinates(address, city, state="PA"):
    """מתחבר ל-Nominatim/OpenStreetMap להמרת כתובת לקואורדינטות."""
    if not address or not city:
        return None, None

    clean_addr = str(address).split(',')[0].strip()
    query = f"{clean_addr}, {city}, {state}"
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "us"}
    headers = {"User-Agent": "PA-RealEstate-Intelligence-Hub/2.0"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
        else:
            print(f"⚠️ Nominatim החזיר HTTP {resp.status_code} עבור {query}")
    except Exception as e:
        print(f"⚠️ שגיאת מיקום עבור {query}: {e}")
    return None, None



SOURCE_ONLY_ANALYSIS_FIELDS = (
    "arv", "arv_status", "arv_method", "arv_confidence", "arv_source",
    "flip_rehab", "rental_rehab", "rehab_status", "rehab_method", "rehab_confidence", "rehab_source",
    "mao_flip", "mao_flip_status", "mao_flip_method", "mao_flip_confidence",
    "projected_rent", "gross_yield", "gross_yield_pct", "rehab_scope", "rent_unavailable_reason",
    "monthly_rent_est", "rent_status", "rent_method", "rent_confidence", "rent_source",
    "mao_rental", "mao_rental_status", "mao_rental_method", "mao_rental_confidence",
    "neighborhood_class", "neighborhood_class_status", "neighborhood_class_method",
    "neighborhood_class_confidence", "neighborhood_class_source",
    "ai_summary", "summary_status", "summary_method",
)


def is_repository_record(prop):
    return (str(prop.get("source_type") or "").casefold() == "tax"
            and str(prop.get("tax_sale_type") or "").casefold() == "repository")


def is_public_court_record(prop):
    source = str(prop.get('source_type') or '').lower()
    deal_type = str(prop.get('deal_type') or '').lower()
    return ('sheriff' in source or 'tax' in source or 'sheriff' in deal_type
            or any(token in deal_type for token in ('tax','פיגורי מס','חוב מס'))
            or prop.get('source_amount_type') in ('repository_minimum_bid','court_amount','opening_bid'))


def mark_public_record_source_only(prop):
    """Remove estimates derived from auction bids or court records; preserve source facts."""
    changed = False
    verified_neighborhood = (prop.get('neighborhood_class_status') == 'verified'
                             and prop.get('neighborhood_class_source') and prop.get('neighborhood_class'))
    for field in SOURCE_ONLY_ANALYSIS_FIELDS:
        if field.startswith('neighborhood_class') and verified_neighborhood:
            continue
        default = "not_applicable" if field.endswith("_status") else None
        if field not in prop or prop.get(field) != default:
            prop[field] = default
            changed = True
    if prop.get("analyzer_version") != ANALYZER_VERSION:
        prop["analyzer_version"] = ANALYZER_VERSION
        changed = True
    if prop.get("analysis_mode") != "source_record_only":
        prop["analysis_mode"] = "source_record_only"
        changed = True
    if prop.get("analysis_is_ai") is not False:
        prop["analysis_is_ai"] = False
        changed = True
    if is_repository_record(prop):
        notice = ("רשומת Repository מתוך רשימת מחוז רשמית. המקור אינו מספק נתוני בית מאומתים; "
                  "לא חושבו שווי, שכירות או שיפוץ. יש לאמת את החלקה והזמינות מול המחוז.")
        skip_reason = "repository_parcel_record_has_no_verified_building_facts"
    else:
        notice = ("רשומת מכרז/חוב מס ממקור ציבורי. סכום פסק דין, Cost & Tax Bid, הצעת פתיחה "
                  "או מינימום Repository אינם מחיר שוק. לא חושבו ARV, שכירות, שיפוץ או MAO.")
        skip_reason = "public_court_record_amount_is_not_market_price"
    if prop.get("analysis_disclaimer") != notice:
        prop["analysis_disclaimer"] = notice
        changed = True
    if prop.get("analysis_skip_reason") != skip_reason:
        prop["analysis_skip_reason"] = skip_reason
        changed = True
    # The official workbook contains legal descriptions, not geocodable street addresses.
    # Remove only coordinates known to have come from the prior OSM lookup.
    if (is_repository_record(prop) and
            (prop.get("geocode_source") == "OpenStreetMap Nominatim" or
             prop.get("geocode_status") == "verified_external_service")):
        for field in ("lat", "lng", "geocode_source", "geocode_updated_at", "geocode_last_attempt_at"):
            if field in prop:
                prop.pop(field, None)
                changed = True
        prop["geocode_status"] = "not_geocoded_repository_description"
        changed = True
    current_inputs = financial_inputs(prop)
    if prop.get('analysis_inputs') != current_inputs or prop.get('financial_model_version') != FINANCIAL_MODEL_VERSION:
        prop['analysis_inputs'] = current_inputs
        prop['financial_model_version'] = FINANCIAL_MODEL_VERSION
        changed = True
    if changed:
        prop['analysis_updated_at'] = utc_now_iso()
    return changed

FINANCIAL_MODEL_VERSION = 'documented-financials-v2-20261007'
FINANCIAL_EVIDENCE_RULES = {
    'rent': {'field':'verified_monthly_rent','prefix':'verified_rent','period':'monthly','days':90,'methods':['lease','rent_roll','market_rent_estimate']},
    'arv': {'field':'verified_arv','prefix':'verified_arv','period':'total','days':180,'methods':['appraisal_after_repair','comparable_sales_after_repair']},
    'flipRehab': {'field':'verified_flip_rehab','prefix':'verified_rehab','period':'total','days':90,'methods':['contractor_quote','inspection_scope_estimate']},
    'rentalRehab': {'field':'verified_rental_rehab','prefix':'verified_rehab','period':'total','days':90,'methods':['contractor_quote','inspection_scope_estimate']},
    'operatingExpenses': {'period':'annual','days':90,'methods':['operating_budget','documented_expenses']},
    'vacancyLoss': {'period':'annual','days':90,'methods':['operating_budget','documented_expenses']},
    'acquisitionCosts': {'period':'total','days':90,'methods':['closing_estimate','settlement_statement']},
    'sellingCosts': {'period':'total','days':90,'methods':['selling_quote','closing_estimate']},
    'holdingCosts': {'period':'total','days':90,'methods':['holding_budget','documented_expenses']},
    'financingCosts': {'period':'total','days':90,'methods':['lender_quote','documented_expenses']},
}

def numeric_financial_value(value):
    if isinstance(value, bool) or not isinstance(value, (str,int,float)):
        return None
    if isinstance(value,(int,float)):
        return value if math.isfinite(value) and abs(value) <= 9007199254740991 else None
    text = str(value).replace('$','').replace(',','').strip()
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):
        return None
    number = float(text)
    return number if math.isfinite(number) and abs(number) <= 9007199254740991 else None

def positive_number(value):
    n = numeric_financial_value(value)
    return n if n is not None and n > 0 else None

def financial_url(value):
    from urllib.parse import urlsplit
    try:
        if not isinstance(value,str): return None
        url = value.strip()
        if not re.match(r'^https?://[^/?#]+',url,re.I) or re.search(r'''[\s<>"'\\]''',url): return None
        parts = urlsplit(url)
        _ = parts.port
        return url if parts.scheme in ('https','http') and parts.hostname and not parts.username and not parts.password else None
    except (ValueError,TypeError):
        return None

def financial_date(value):
    text = str(value or '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?',text):
        return None
    try:
        datetime.strptime(text[:10],'%Y-%m-%d')
        d = datetime.fromisoformat(text.replace('Z','+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except ValueError:
        return None

def financial_evidence(prop,key,now=None):
    now = now or datetime.now(timezone.utc)
    rule = FINANCIAL_EVIDENCE_RULES[key]
    collection = prop.get('financial_evidence')
    entry = collection.get(key) if isinstance(collection,dict) else None
    if entry is None and rule.get('field') and rule['field'] in prop:
        prefix = rule['prefix']
        entry = {name:prop.get(prefix+'_'+name) for name in ('source_url','document_ref','observed_at','method','period','status','property_id','zero_cost_confirmed','property_match_confirmed')}
        entry.update({'value':prop.get(rule['field']),'source_name':prop.get(prefix+'_source')})
    if not isinstance(entry,dict):
        return None
    value = numeric_financial_value(entry.get('value'))
    stamp = financial_date(entry.get('observed_at'))
    url = financial_url(entry.get('source_url'))
    document_ref = str(entry.get('document_ref') or '').strip()
    if value is None or value < 0 or (key in ('rent','arv') and value <= 0):
        return None
    if value == 0 and entry.get('zero_cost_confirmed') is not True:
        return None
    if not isinstance(entry.get('source_name'),str) or not entry['source_name'].strip() or (not url and (not isinstance(entry.get('document_ref'),str) or len(document_ref)<5)):
        return None
    if not prop.get('id') or str(entry.get('property_id') or '') != str(prop['id']):
        return None
    if entry.get('status') not in ('documented','verified','provider_estimate') or entry.get('method') not in rule['methods']:
        return None
    if entry.get('property_match_confirmed') is not True:
        return None
    if entry.get('period') != rule['period'] or stamp is None or stamp > now or now-stamp > timedelta(days=rule['days']):
        return None
    return {'value':value,'property_id':str(prop['id']),'source_name':entry['source_name'].strip(),'source_url':url,
        'document_ref':document_ref if isinstance(entry.get('document_ref'),str) else '',
        'observed_at':entry['observed_at'],'method':entry['method'],'period':entry['period'],'status':entry['status'],
        'property_match_confirmed':True,'zero_cost_confirmed':entry.get('zero_cost_confirmed') is True,
        'notes':entry.get('notes') if isinstance(entry.get('notes'),str) else ''}

def financial_target(prop,name,percentage=False):
    targets = prop.get('financial_targets')
    if not isinstance(targets,dict) or targets.get('confirmed') is not True:
        return None
    value = numeric_financial_value(targets.get(name))
    if value is None or value < 0 or (percentage and not 0 < value <= 100):
        return None
    return value

def financial_snapshot(prop,now=None):
    now = now or datetime.now(timezone.utc)
    source_only = is_public_court_record(prop)
    evidence = {key:None if source_only else financial_evidence(prop,key,now) for key in FINANCIAL_EVIDENCE_RULES}
    def val(key):
        return evidence[key]['value'] if evidence[key] else None
    checked = financial_date(prop.get('last_source_check'))
    price = positive_number(prop.get('price')) if not source_only and checked and checked <= now and now-checked <= timedelta(days=30) and financial_url(prop.get('url') or prop.get('source_url')) else None
    rent,arv,rental,flip = [val(k) for k in ('rent','arv','rentalRehab','flipRehab')]
    expenses,vacancy,acquisition,selling,holding,financing = [val(k) for k in ('operatingExpenses','vacancyLoss','acquisitionCosts','sellingCosts','holdingCosts','financingCosts')]
    annual = rent*12 if rent is not None else None
    gross = math.floor(rent*1200/price*10+0.5)/10 if rent is not None and price is not None else None
    noi = annual-expenses-vacancy if all(v is not None for v in (annual,expenses,vacancy)) else None
    target_cap = financial_target(prop,'cap_rate_pct',True)
    target_profit = financial_target(prop,'flip_profit_amount')
    rental_ceiling = math.floor(noi/(target_cap/100)-rental-acquisition) if all(v is not None for v in (noi,rental,acquisition,target_cap)) else None
    flip_ceiling = math.floor(arv-flip-acquisition-selling-holding-financing-target_profit) if all(v is not None for v in (arv,flip,acquisition,selling,holding,financing,target_profit)) else None
    all_in = price+flip+acquisition+holding+financing if all(v is not None for v in (price,flip,acquisition,holding,financing)) else None
    profit = arv-all_in-selling if all(v is not None for v in (arv,all_in,selling)) else None
    targets = {'confirmed':True,'cap_rate_pct':target_cap,'flip_profit_amount':target_profit} if isinstance(prop.get('financial_targets'),dict) and prop['financial_targets'].get('confirmed') is True else None
    inputs = {'model_version':FINANCIAL_MODEL_VERSION,'price':price,'evidence':evidence,'targets':targets}
    return {'inputs':inputs,'evidence':evidence,'mode':'source_record_only' if source_only else 'documented_inputs_only',
        'price':price,'rent':rent,'yield':gross,'arv':arv,'rentalRehab':rental,'flipRehab':flip,'annualRent':annual,
        'operatingExpenses':expenses,'vacancyLoss':vacancy,'acquisitionCosts':acquisition,'sellingCosts':selling,
        'holdingCosts':holding,'financingCosts':financing,'noi':noi,'allIn':all_in,'flipProfit':profit,
        'flipRoi':math.floor(profit/all_in*1000+0.5)/10 if profit is not None and all_in and all_in>0 else None,
        'capRate':math.floor(noi/price*1000+0.5)/10 if noi is not None and price is not None else None,
        'maoRental':rental_ceiling if rental_ceiling is not None and rental_ceiling>0 else None,
        'maoFlip':flip_ceiling if flip_ceiling is not None and flip_ceiling>0 else None,
        'rentalFeasible':rental_ceiling>0 if rental_ceiling is not None else None,'flipFeasible':flip_ceiling>0 if flip_ceiling is not None else None,
        'targetCap':target_cap,'targetProfit':target_profit}

def financial_inputs(prop):
    return financial_snapshot(prop)['inputs']

def format_financial_money(value):
    return 'אין נתון מבוסס' if value is None else '$'+f'{value:,.2f}'.rstrip('0').rstrip('.')

def calculate_rental_metrics(prop):
    f = financial_snapshot(prop)
    rent,gross = f['rent'],f['yield']
    return {'financial_model_version':FINANCIAL_MODEL_VERSION,'monthly_rent_est':rent,
        'projected_rent':format_financial_money(rent)+' / חודש' if rent is not None else None,
        'gross_yield_pct':gross,'gross_yield':f'{gross:.1f}% ברוטו' if gross is not None else None,
        'rent_status':'documented' if rent is not None else 'unavailable',
        'rent_method':'documented_source_input' if rent is not None else None,
        'rent_confidence':'source_documented' if rent is not None else 'none',
        'rent_source':f['evidence']['rent']['source_name'] if rent is not None else None,
        'rent_unavailable_reason':None if rent is not None else 'missing_or_invalid_evidence'}

def calculate_metrics(prop):
    f = financial_snapshot(prop)
    metrics = calculate_rental_metrics(prop)
    rent,gross,arv = f['rent'],f['yield'],f['arv']
    parts = []
    if rent is not None: parts.append('שכירות לפי מקור: '+metrics['projected_rent'])
    if gross is not None: parts.append('תשואה ברוטו: '+metrics['gross_yield'])
    if arv is not None: parts.append('שווי לאחר שיפוץ לפי מקור: '+format_financial_money(arv))
    rehab = f['flipRehab'] if prop.get('strategy')=='value_add' else f['rentalRehab']
    metrics.update({'analyzer_version':ANALYZER_VERSION,'analysis_updated_at':utc_now_iso(),
        'analysis_inputs':f['inputs'],'analysis_mode':f['mode'],'analysis_is_ai':False,'analysis_skip_reason':None,
        'analysis_disclaimer':'מוצגים רק נתונים כספיים עם מקור ותאריך; חסר בנתוני הבסיס מונע את החישוב התלוי בו.',
        'arv':arv,'flip_rehab':f['flipRehab'],'rental_rehab':f['rentalRehab'],
        'mao_flip':f['maoFlip'],'mao_rental':f['maoRental'],'noi':f['noi'],'annual_operating_expenses':f['operatingExpenses'],
        'cap_rate_pct':f['capRate'],'all_in_cost':f['allIn'],'flip_profit':f['flipProfit'],'flip_roi_pct':f['flipRoi'],
        'rehab_scope':('שיפוץ לפי מקור: '+format_financial_money(rehab)) if rehab is not None else 'אין נתון מבוסס',
        'deal_score':None,'margin_estimate':None,'neighborhood_class':None,'neighborhood_class_status':'unavailable',
        'neighborhood_class_method':None,'neighborhood_class_source':None,'neighborhood_class_confidence':'none',
        'summary_status':'documented_data_only','summary_method':'deterministic_template',
        'ai_summary':'. '.join(parts) if parts else 'אין נתונים כספיים מבוססים להצגה.'})
    for key,field in (('arv','arv'),('flipRehab','flip_rehab'),('rentalRehab','rental_rehab')):
        entry = f['evidence'][key]
        metrics.update({field+'_status':'documented' if entry else 'unavailable',field+'_method':entry['method'] if entry else None,
            field+'_confidence':'source_documented' if entry else 'none',field+'_source':entry['source_name'] if entry else None})
    metrics.update({'arv_method':'documented_source_input' if arv is not None else None,
        'rehab_status':'documented' if f['flipRehab'] is not None or f['rentalRehab'] is not None else 'unavailable',
        'rehab_method':'documented_source_input' if f['flipRehab'] is not None or f['rentalRehab'] is not None else None,
        'rehab_source':None,'rehab_confidence':'none'})
    for field,key in (('mao_flip','maoFlip'),('mao_rental','maoRental')):
        metrics.update({field+'_status':'calculated_from_documented_inputs' if f[key] is not None else 'unavailable',
            field+'_method':'documented_costs_and_user_target' if f[key] is not None else None,
            field+'_confidence':'input_dependent' if f[key] is not None else 'none'})
    return metrics

def needs_analysis(prop):
    snapshot = financial_snapshot(prop)
    fields = {'arv':'arv','flip_rehab':'flipRehab','rental_rehab':'rentalRehab','mao_flip':'maoFlip',
              'mao_rental':'maoRental','monthly_rent_est':'rent','gross_yield_pct':'yield','noi':'noi',
              'all_in_cost':'allIn','flip_profit':'flipProfit','cap_rate_pct':'capRate','flip_roi_pct':'flipRoi'}
    return (prop.get('analyzer_version') != ANALYZER_VERSION or prop.get('financial_model_version') != FINANCIAL_MODEL_VERSION
        or prop.get('analysis_inputs') != snapshot['inputs'] or any(k not in prop or prop.get(k)!=snapshot[v] for k,v in fields.items()))


def run_analyzer():
    print(f"🧠 מתחיל Analyzer V{ANALYZER_VERSION} — נתונים כספיים מתועדים בלבד...")

    if not os.path.exists(PROPERTIES_FILE):
        print("❌ קובץ הנכסים לא נמצא.")
        return

    with open(PROPERTIES_FILE, 'r', encoding='utf-8') as f:
        properties = json.load(f)

    if not isinstance(properties, list):
        print("❌ properties.json אינו מכיל רשימת נכסים.")
        return

    selected_counties = load_selected_counties()
    print(f"🎯 Analyzer מוגבל למחוזות שנבחרו: {sorted(selected_counties)}")
    if not selected_counties:
        print("⏭️ לא נבחרו מחוזות; מתבצע ניקוי כספי מקומי בלבד, ללא בקשות מיקום.")

    updated_properties = []
    analyzed_count = 0
    geocoded_count = 0
    geocode_cache_hits = 0
    geocode_skipped_failed = 0
    geocode_requests = 0
    geocode_batch_skipped = 0
    outside_counties_skipped = 0
    source_only_count = 0
    source_records_cleaned = 0
    cache = load_geocode_cache()
    cache_changed = False

    for prop in properties:
        if not isinstance(prop, dict):
            updated_properties.append(prop)
            continue

        # Apply the evidence policy to every stored row, without expanding geocoding scope.
        if needs_analysis(prop):
            prop.update(calculate_metrics(prop))
            analyzed_count += 1

        if is_public_court_record(prop):
            if mark_public_record_source_only(prop):
                # Preserve the established parcel/geocode cleanup, then apply the new financial signature.
                prop.update(calculate_metrics(prop))
                source_records_cleaned += 1
            source_only_count += 1
            updated_properties.append(prop)
            continue

        county_key = normalize_county(prop.get('county'))
        if not county_key or county_key not in selected_counties:
            updated_properties.append(prop)
            outside_counties_skipped += 1
            continue

        # Geocoding:
        # 1) existing coordinates -> no request
        # 2) persistent cache hit -> no request
        # 3) recent failed lookup -> no repeated request for 30 days
        # 4) only selected counties are eligible; cap live calls at 20 per run
        lat = positive_number(prop.get('lat'))
        try:
            lng = float(prop.get('lng')) if prop.get('lng') is not None else None
        except (TypeError, ValueError):
            lng = None

        address = prop.get('address')
        city = prop.get('city')
        if (lat is None or lng is None) and address and city:
            key = normalize_geocode_key(address, city)
            cached = cache.get(key)

            if isinstance(cached, dict) and positive_number(cached.get('lat')) is not None and cached.get('lng') is not None:
                prop['lat'] = float(cached['lat'])
                prop['lng'] = float(cached['lng'])
                prop['geocode_status'] = 'verified_external_service'
                prop['geocode_source'] = cached.get('source') or 'OpenStreetMap Nominatim'
                prop['geocode_updated_at'] = cached.get('attempted_at') or utc_now_iso()
                geocode_cache_hits += 1

            elif failed_geocode_is_fresh(cached):
                prop['geocode_status'] = 'unavailable'
                prop['geocode_last_attempt_at'] = cached.get('attempted_at')
                geocode_skipped_failed += 1

            elif prop.get('geocode_status') == 'unavailable' and not prop.get('geocode_last_attempt_at'):
                # Migration of failures from Analyzer V2.0: do not hammer Nominatim again now.
                attempted_at = utc_now_iso()
                prop['geocode_last_attempt_at'] = attempted_at
                cache[key] = {'status': 'unavailable', 'attempted_at': attempted_at}
                cache_changed = True
                geocode_skipped_failed += 1

            elif geocode_requests >= MAX_GEOCODE_REQUESTS_PER_RUN:
                geocode_batch_skipped += 1

            else:
                print(f"📍 מאתר קואורדינטות: {address}...")
                geocode_requests += 1
                new_lat, new_lng = get_coordinates(address, city)
                attempted_at = utc_now_iso()

                if new_lat is not None and new_lng is not None:
                    prop['lat'] = new_lat
                    prop['lng'] = new_lng
                    prop['geocode_status'] = 'verified_external_service'
                    prop['geocode_source'] = 'OpenStreetMap Nominatim'
                    prop['geocode_updated_at'] = attempted_at
                    prop['geocode_last_attempt_at'] = attempted_at
                    cache[key] = {
                        'status': 'verified',
                        'lat': new_lat,
                        'lng': new_lng,
                        'source': 'OpenStreetMap Nominatim',
                        'attempted_at': attempted_at,
                    }
                    geocoded_count += 1
                else:
                    prop['geocode_status'] = 'unavailable'
                    prop['geocode_last_attempt_at'] = attempted_at
                    cache[key] = {'status': 'unavailable', 'attempted_at': attempted_at}

                cache_changed = True
                time.sleep(1.1)

        if needs_analysis(prop):
            print(f"📊 בודק נתונים מתועדים: {prop.get('address', 'Unknown')}...")
            prop.update(calculate_metrics(prop))
            analyzed_count += 1

        updated_properties.append(prop)

    if cache_changed:
        save_geocode_cache(cache)

    temp_file = PROPERTIES_FILE + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(updated_properties, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, PROPERTIES_FILE)

    print(
        f"✅ Analyzer V{ANALYZER_VERSION} סיים: "
        f"{analyzed_count} נכסים נותחו/עודכנו, "
        f"{geocoded_count} נכסים קיבלו קואורדינטות."
    )
    print(f"🧾 מכרזים/חובות מס: {source_only_count} רשומות הושארו כרשומות מקור בלבד; "
          f"{source_records_cleaned} נוקו מאומדני Analyzer לא מתאימים.")
    print(
        f"🗺️ Geocode QA — בקשות חיצוניות: {geocode_requests}/{MAX_GEOCODE_REQUESTS_PER_RUN} | "
        f"Cache hits: {geocode_cache_hits} | "
        f"דילוג על כשלונות טריים: {geocode_skipped_failed} | "
        f"ממתינים לסבב הבא: {geocode_batch_skipped} | "
        f"מחוץ למחוזות שנבחרו: {outside_counties_skipped}"
    )
    print("ℹ️ תחשיב דורש נתוני בסיס מתועדים; אין שכירות, שווי או שיפוץ לפי קבועי ברירת מחדל.")


if __name__ == '__main__':
    run_analyzer()
