"""Official school context for a matched Allegheny parcel; no invented grades.

PDE publishes performance measures, not an overall neighborhood score. Schools
listed here belong to the parcel's recorded district; attendance assignment is
not asserted. Current crime figures stay unavailable until a reliable feed is
verified (the Pittsburgh WPRDC blotter stopped updating on 2023-11-14).
"""
from __future__ import annotations
import argparse, hashlib, io, json, re, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
import internal_report_store as store

VERSION='official-public-context-1'
EDUCATION_VERSION='pde-performance-1'
PDE_INDEX='https://www.futurereadypa.org/Home/DataFiles'
PDE_HOSTS={'www.futurereadypa.org','futurereadypa.org'}
ASSESSMENT='65855e14-549e-4992-b5be-d629afc676fa'
ASSESSMENT_URL='https://data.wprdc.org/dataset/property-assessments'
CRIME_URL='https://www.ucr.pa.gov/PAUCRSPUBLIC'
TARGET_DISTRICTS={'pittsburgh':'Pittsburgh SD','penn hills':'Penn Hills SD',
                  'riverview':'Riverview SD','baldwin whitehall':'Baldwin-Whitehall SD'}
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}

def clock(): return datetime.now(timezone.utc)
def quarter(t): return f'{t.year}-Q{(t.month-1)//3+1}'
def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def district_key(s):
    text=norm(s)
    return re.sub(r'\s+(?:school district|sd)$','',text)
def read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError): return None
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def current(record,now):
    if not isinstance(record,dict) or record.get('checked_quarter')!=quarter(now):return False
    try:return datetime.fromisoformat(record['observed_at'])<=now
    except (KeyError,ValueError,TypeError):return False

def get_bytes(url,limit=32*1024*1024):
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in PDE_HOSTS or u.username or u.password:
        raise ValueError('Unexpected education host')
    with urlopen(Request(url,headers={'User-Agent':'PA-Intelligence-Official-Education/1'}),timeout=30) as response:
        if urlsplit(response.url).hostname not in PDE_HOSTS:raise ValueError('Unexpected education redirect')
        data=response.read(limit+1)
        if len(data)>limit:raise ValueError('Education response too large')
        return data

def workbook_link(html,kind='performance',period_required=None):
    links=[]
    for href,title in re.findall(r'<a\b[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>',html,re.I|re.S):
        title=re.sub('<[^>]*>',' ',title)
        period=re.search(r'20\d{2}\s*[-–]\s*20\d{2}',title)
        label='future ready performance data' if kind=='performance' else 'district fast facts'
        if label not in title.lower() or not period:continue
        if period_required and period.group().replace(' ','').replace('–','-')!=period_required:continue
        url=urljoin(PDE_INDEX,href.replace('&amp;','&'))
        if urlsplit(url).hostname not in PDE_HOSTS:continue
        links.append((period.group().replace(' ','').replace('–','-'),url))
    if not links:raise ValueError('PDE performance workbook link unavailable')
    return sorted(links,reverse=True)[0]

def parse_district_map(data):
    """Resolve AUNs from the official District Fast Facts workbook."""
    result={};headers={};diagnostics={}
    for sheet,rownum,row in xlsx_rows(data):
        normalized=[norm(x) for x in row]
        ai=next((i for i,x in enumerate(normalized) if x in {'aun','lea aun','district aun'}),None)
        ni=next((i for i,x in enumerate(normalized) if x in {'name','lea name','leaname','district name','districtname'}),None)
        if ai is not None and ni is not None:
            headers[sheet]=(ai,ni);diagnostics[sheet]=row[:30];continue
        if sheet not in headers:continue
        ai,ni=headers[sheet]
        if max(ai,ni)>=len(row):continue
        aun=str(row[ai]).strip();key=district_key(row[ni])
        if not re.fullmatch(r'\d{9}',aun) or key not in TARGET_DISTRICTS:continue
        if aun in result and result[aun]!=key:raise ValueError('Conflicting official district AUN')
        result[aun]=key
    return result,diagnostics

EXPLICIT_MEASURES={
    'PercentProficientorAdvancedonMathematicsAlgebra1_AllStudent':'עמידה במבחני מתמטיקה / אלגברה',
    'PercentProficientorAdvancedonELALiterature_AllStudent':'עמידה במבחני שפה / ספרות',
    'PercentProficientorAdvancedonScienceBiology_AllStudent':'עמידה במבחני מדעים / ביולוגיה',
    'PercentPersistentAttendance_AllStudent':'נוכחות סדירה',
    'PercentGraduation4YearCohort_AllStudent':'סיום לימודים — מסלול ארבע שנים',
}

def xlsx_rows(data):
    """Read cell values and percent styles without adding an Excel dependency."""
    if not zipfile.is_zipfile(io.BytesIO(data)):raise ValueError('Expected official XLSX workbook')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist())>350*1024*1024:raise ValueError('Expanded workbook too large')
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            for _,elem in ET.iterparse(z.open('xl/sharedStrings.xml'),events=('end',)):
                if elem.tag==f'{{{NS["m"]}}}si':
                    shared.append(''.join(elem.itertext()));elem.clear()
        percent_styles=set()
        if 'xl/styles.xml' in z.namelist():
            styles=ET.fromstring(z.read('xl/styles.xml'))
            formats={int(x.attrib['numFmtId']):x.attrib.get('formatCode','') for x in styles.findall('m:numFmts/m:numFmt',NS)}
            for i,style in enumerate(styles.findall('m:cellXfs/m:xf',NS)):
                fmt=int(style.attrib.get('numFmtId',0))
                if fmt in {9,10} or '%' in formats.get(fmt,''):percent_styles.add(i)
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets={r.attrib['Id']:r.attrib['Target'] for r in rels}
        book=ET.fromstring(z.read('xl/workbook.xml'))
        for sheet in book.findall('m:sheets/m:sheet',NS):
            target=targets[sheet.attrib[f'{{{NS["r"]}}}id']]
            path=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            if '..' in path.split('/'):raise ValueError('Unexpected worksheet path')
            for _,row in ET.iterparse(z.open(path),events=('end',)):
                if row.tag!=f'{{{NS["m"]}}}row':continue
                cells={}
                for cell in row.findall('m:c',NS):
                    letters=re.match(r'[A-Z]+',cell.attrib.get('r',''))
                    if not letters:continue
                    pos=0
                    for c in letters.group():pos=pos*26+ord(c)-64
                    kind=cell.attrib.get('t');node=cell.find('m:v',NS)
                    raw=node.text if node is not None else ''
                    if kind=='s':value=shared[int(raw)] if raw else ''
                    elif kind=='inlineStr':value=''.join(cell.find('m:is',NS).itertext())
                    else:value=raw or ''
                    if kind not in {'s','inlineStr','str','e','b'} and int(cell.attrib.get('s',0)) in percent_styles:
                        try:value=str(float(value)*100)+'%'
                        except ValueError:pass
                    cells[pos-1]=value
                count=max(cells,default=-1)+1
                yield sheet.attrib['name'],int(row.attrib.get('r',0)),[cells.get(i,'') for i in range(count)]
                row.clear()

def metric_label(text):
    s=norm(text)
    if any(w in s for w in ('standard','target','goal','color','colour','status','statewide','gap','growth')):return None
    if set(s.split()) & {'count','counts','number','numerator','denominator'}:return None
    if 'attendance' in s:return 'נוכחות סדירה'
    if ('graduation' in s and ('4' in s or 'four' in s)) or s=='graduation rate':return 'סיום לימודים — מסלול ארבע שנים'
    if 'proficient' in s or 'proficiency' in s or 'achievement' in s:
        if any(x in s for x in ('mathematics','algebra','math')):return 'עמידה במבחני מתמטיקה / אלגברה'
        if any(x in s for x in ('literature','english language arts','ela','reading')):return 'עמידה במבחני שפה / ספרות'
        if any(x in s for x in ('science','biology')):return 'עמידה במבחני מדעים / ביולוגיה'
    return None

def percent(value):
    raw=str(value or '').strip()
    if not re.fullmatch(r'\d+(?:\.\d+)?\s*%?',raw):return None
    n=float(raw.rstrip('%').strip())
    return n if 0<=n<=100 else None

def parse_workbook(data,period,url,now,district_map=None):
    districts={k:{'district':v,'schools':[]} for k,v in TARGET_DISTRICTS.items()}
    headers={};diagnostics={};recognized={};school_map={}
    for sheet,rownum,row in xlsx_rows(data):
        normalized=[norm(x) for x in row]
        if normalized[:3]==['aun','schl','name']:
            headers[sheet]=(row,'aun_schema',2);diagnostics[sheet]=row[:35];continue
        # A school/district identifier is mandatory; numeric sheets alone cannot be attributed.
        district_col=next((i for i,x in enumerate(normalized) if x in {'lea name','district name','school district','school district name','district'}),None)
        school_col=next((i for i,x in enumerate(normalized) if x in {'school name','building name','school'}),None)
        if district_col is not None and school_col is not None:
            headers[sheet]=(row,district_col,school_col)
            diagnostics[sheet]=row[:35]
            continue
        if sheet not in headers:continue
        head,di,si=headers[sheet]
        if di=='aun_schema':
            if len(row)<3:continue
            dk=(district_map or {}).get(str(row[0]).strip())
            if dk not in districts:continue
            recognized[dk]=recognized.get(dk,0)+1
            name=str(row[2] or '').strip()
            if not name:continue
            key=(dk,name);record=school_map.setdefault(key,{'school_name':name,'school_id':str(row[1]),'district_aun':str(row[0]),'metrics':[]})
            for i,code in enumerate(head):
                if code not in EXPLICIT_MEASURES or i>=len(row):continue
                label=EXPLICIT_MEASURES[code]
                if any(m['label']==label for m in record['metrics']):continue
                record['metrics'].append({'label':label,'value':percent(row[i]),'unit':'percent',
                    'source_value':str(row[i]),'source_measure':code,'source_sheet':sheet,
                    'source_row':rownum,'student_group':'All Students'})
            continue
        if max(di,si)>=len(row):continue
        dk=district_key(row[di])
        if dk not in districts:continue
        recognized[dk]=recognized.get(dk,0)+1
        cols={norm(name):row[i] if i<len(row) else '' for i,name in enumerate(head)}
        group=next((cols[k] for k in ('student group','student group name','student subgroup','subgroup',
                    'reporting group','reporting group name','student population','group') if k in cols),None)
        # Unknown row scope cannot be promoted to an all-students result. Any
        # genuinely aggregate wide-table schema must be verified explicitly.
        if group is None or norm(group) not in {'all students','all student','all','all student group'}:continue
        name=str(row[si] or '').strip()
        if not name:continue  # No school-level or explicit named district result.
        metrics=[]
        subject=next((cols[k] for k in ('subject','subject area','measure','indicator') if cols.get(k)), '')
        for i,label in enumerate(head):
            if i>=len(row):continue
            description=str(label)
            # Supports both wide tables and subject-specific worksheets.
            combined=' '.join((str(sheet),str(subject),description))
            label_he=metric_label(description) or metric_label(combined)
            if not label_he:continue
            # A value must be a published percentage column, not an institution ID/count.
            if not any(w in norm(description) for w in ('percent','rate','value','performance','attendance','proficient','graduation')):continue
            val=percent(row[i])
            metrics.append({'label':label_he,'value':val,'unit':'percent','source_value':str(row[i]),
                'source_measure':description,'source_sheet':sheet,'source_row':rownum,
                'student_group':str(group or 'not specified in source row')})
        if not metrics:continue
        key=(dk,name);record=school_map.setdefault(key,{'school_name':name,'school_id':str(cols.get('school number') or cols.get('school code') or ''),'metrics':[]})
        for m in metrics:
            if not any(x['label']==m['label'] for x in record['metrics']):record['metrics'].append(m)
    for (dk,name),school in school_map.items():districts[dk]['schools'].append(school)
    return {'version':EDUCATION_VERSION,'source_name':'Pennsylvania Department of Education — Future Ready PA',
        'source_url':url,'source_period':period,'observed_at':now.isoformat(timespec='seconds'),
        'source_period_basis':'PDE_workbook_release_not_all_indicator_years',
        'checked_quarter':quarter(now),'districts':districts,
        'diagnostics':{'sheet_headers':diagnostics,'recognized_district_rows':recognized}}

def education_data(root,allow_public,now):
    path=Path(root)/'official_education'/'performance.json';cached=read(path)
    if cached and cached.get('version')==EDUCATION_VERSION and current(cached,now):return cached
    if not allow_public:return {**(cached or {}),'status':'cached_stale' if cached else 'lookup_disabled'}
    try:
        index=get_bytes(PDE_INDEX,2*1024*1024).decode('utf-8');period,url=workbook_link(index)
        _,district_url=workbook_link(index,'district',period)
        district_map,map_headers=parse_district_map(get_bytes(district_url))
        result=parse_workbook(get_bytes(url),period,url,now,district_map)
        result['district_mapping_source_url']=district_url
        result['diagnostics']['district_map_headers']=map_headers
        if not any(d['schools'] for d in result['districts'].values()):
            return {**result,'status':'source_schema_unavailable'}
        result['status']='available';write(path,result);return result
    except (OSError,ValueError,KeyError,ET.ParseError,zipfile.BadZipFile) as exc:
        return {**(cached or {}),'status':'cached_stale' if cached else 'source_unavailable','error_type':type(exc).__name__}

def parcel_school_district(county,root,allow_public,now):
    parcel=county.get('parcel_id')
    if not re.fullmatch(r'[A-Z0-9]{16}',str(parcel or '')):return None
    path=Path(root)/'official_education'/'parcels'/(parcel+'.json');cached=read(path)
    if cached and cached.get('parcel_id')==parcel and current(cached,now):return cached
    if not allow_public:return None
    url='https://data.wprdc.org/api/3/action/datastore_search?'+urlencode({
        'resource_id':ASSESSMENT,'filters':json.dumps({'PARID':parcel}),'fields':'PARID,SCHOOLDESC','limit':2})
    payload=json.loads(store.public_get(url));rows=(payload.get('result') or {}).get('records') or []
    if payload.get('success') is not True or len(rows)!=1 or rows[0].get('PARID')!=parcel:return None
    key=district_key(rows[0].get('SCHOOLDESC'))
    if key not in TARGET_DISTRICTS:return None
    result={'district':TARGET_DISTRICTS[key],'district_key':key,'parcel_id':parcel,
        'source_url':ASSESSMENT_URL,'source_name':'Allegheny County property assessments — SCHOOLDESC',
        'observed_at':now.isoformat(timespec='seconds'),'checked_quarter':quarter(now)}
    write(path,result);return result

def collect(p,county,point,root=Path('COMPS_REPORTS'),allow_public=True):
    now=clock();pid=str(p.get('id') or '')
    result={'version':VERSION,'property_id':pid,'property':{k:p.get(k) for k in ('id','address','city','state','zip')},
        'parcel_id':county.get('parcel_id'),'generated_at':now.isoformat(timespec='seconds'),'status':'partial'}
    county_match=county.get('status')=='matched' and county.get('county')=='Allegheny' and store.same_address(p,county.get('subject') or {})
    municipality=(county.get('fields') or {}).get('municipality') if county_match else None
    if not municipality and county_match and point.get('parcel_id')==county.get('parcel_id'):municipality=point.get('municipality')
    result['municipality']=municipality
    result['parcel_location']=dict(point) if county_match and point.get('parcel_id')==county.get('parcel_id') else None
    result['education']={'status':'district_unavailable','district':None,'parcel_id':county.get('parcel_id'),
        'scope':'school_district_schools_not_assignment','assignment_verified':False,'schools':[],
        'source_name':'Pennsylvania Department of Education — Future Ready PA','source_url':PDE_INDEX}
    result['crime']={'status':'not_collected','scope':'municipality','municipality':municipality,
        'source_name':'Pennsylvania State Police — UCR','source_url':CRIME_URL,'observed_at':None,
        'reason':'no_verified_automated_current_feed','metrics':[],
        'historical_source_url':'https://data.wprdc.org/dataset/police-incident-blotter',
        'historical_source_stopped_at':'2023-11-14'}
    if not county_match:return result
    try:district=parcel_school_district(county,root,allow_public,now)
    except (OSError,ValueError,KeyError,TypeError):district=None
    if not district:return result
    data=education_data(root,allow_public,now);matched=(data.get('districts') or {}).get(district['district_key']) or {}
    schools=matched.get('schools') or []
    status='available' if schools and data.get('status')=='available' else data.get('status') or 'source_unavailable'
    if status=='available' and not schools:status='district_data_unavailable'
    result['education'].update(status=status,district=district['district'],
        district_match_basis='county_parcel_school_district',district_source_url=district['source_url'],
        district_observed_at=district['observed_at'],source_url=data.get('source_url') or PDE_INDEX,
        source_period=data.get('source_period'),observed_at=data.get('observed_at'),schools=schools)
    return result

def live_check():
    now=clock();index=get_bytes(PDE_INDEX).decode('utf-8');period,url=workbook_link(index)
    _,district_url=workbook_link(index,'district',period)
    district_bytes=get_bytes(district_url);district_map,map_headers=parse_district_map(district_bytes)
    if not district_map:
        samples={}
        for sheet,rownum,row in xlsx_rows(district_bytes):
            if len(samples.setdefault(sheet,[]))<5:samples[sheet].append({'row':rownum,'cells':row[:40]})
        print('DISTRICT_MAP_SAMPLE '+json.dumps(samples,ensure_ascii=False))
    result=parse_workbook(get_bytes(url),period,url,now,district_map)
    counts={k:len(v['schools']) for k,v in result['districts'].items()}
    numeric={k:sum(m['value'] is not None for s in v['schools'] for m in s['metrics']) for k,v in result['districts'].items()}
    print(json.dumps({'source_url':url,'source_period':period,'school_counts':counts,
        'numeric_metric_counts':numeric,'district_map':district_map,'district_map_source_url':district_url,
        'district_map_headers':map_headers,'diagnostics':result['diagnostics']},ensure_ascii=False))
    if not all(numeric[k]>0 for k in ('pittsburgh','penn hills','riverview')):raise SystemExit('Official PDE workbook needs parser review; no invented school values published')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live-check',action='store_true');args=parser.parse_args()
    if args.live_check:live_check()
