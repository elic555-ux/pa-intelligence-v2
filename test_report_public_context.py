import io,json,tempfile,unittest,zipfile
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape
import report_public_context as context

def workbook(rows):
    data=io.BytesIO()
    def col(n):
        out=''
        while n:n,r=divmod(n-1,26);out=chr(65+r)+out
        return out
    xml='<worksheet xmlns="'+context.NS['m']+'"><sheetData>'
    for i,row in enumerate(rows,1):
        xml+=f'<row r="{i}">'
        for j,value in enumerate(row,1):xml+=f'<c r="{col(j)}{i}" t="inlineStr"><is><t>'+escape(str(value))+'</t></is></c>'
        xml+='</row>'
    xml+='</sheetData></worksheet>'
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="'+context.NS['m']+'" xmlns:r="'+context.NS['r']+'"><sheets><sheet name="Academic performance" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',xml)
    return data.getvalue()

class PublicContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.now=datetime(2026,10,31,tzinfo=timezone.utc)
        self.p={'id':'1','address':'18 Main St','city':'Verona','state':'PA','zip':'15147'}
        self.county={'status':'matched','county':'Allegheny','parcel_id':'0012E00199000000','subject':self.p,'fields':{'municipality':'Penn Hills'}}
        self.point={'parcel_id':self.county['parcel_id'],'municipality':'Penn Hills'}
    def fixture(self):
        return workbook([
            ['LEA Name','School Number','School Name','Student Group','Percent Proficient or Advanced Mathematics/Algebra 1','Percent Proficient or Advanced ELA/Literature','Percent Regular Attendance'],
            ['Penn Hills SD','1','School A','All Students','42.5','*','87.0%'],
            ['Penn Hills SD','1','School A','Economically Disadvantaged','30.0','20','70'],
            ['Riverview SD','2','School B','All Students','80','79','90'],
            ['Pittsburgh SD','3','School C','All Students','44','43','75'],
        ])
    def test_workbook_extracts_official_rows_without_grade_or_subgroup_average(self):
        r=context.parse_workbook(self.fixture(),'2024-2025',context.PDE_INDEX,self.now)
        school=r['districts']['penn hills']['schools'][0]
        self.assertEqual(school['school_name'],'School A')
        self.assertEqual([m['value'] for m in school['metrics']],[42.5,None,87])
        self.assertNotIn('grade',school)
    def test_unsupported_workbook_fails_without_inventing_values(self):
        r=context.parse_workbook(workbook([['school','score'],['School A','100']]),'2024-2025',context.PDE_INDEX,self.now)
        self.assertTrue(all(not d['schools'] for d in r['districts'].values()))
    def test_count_columns_under_one_hundred_are_never_percentages(self):
        data=workbook([
            ['LEA Name','School Name','Student Group Name','Number of Students Proficient Mathematics','Percent Proficient Mathematics Denominator','Percent Proficient Mathematics'],
            ['Penn Hills SD','School A','All Students','45','90','50']])
        r=context.parse_workbook(data,'2024-2025',context.PDE_INDEX,self.now)
        metrics=r['districts']['penn hills']['schools'][0]['metrics']
        self.assertEqual(len(metrics),1);self.assertEqual(metrics[0]['value'],50)
    def test_unknown_student_scope_is_not_promoted_to_all_students(self):
        data=workbook([['LEA Name','School Name','Percent Regular Attendance'],['Penn Hills SD','School A','98']])
        r=context.parse_workbook(data,'2024-2025',context.PDE_INDEX,self.now)
        self.assertEqual(r['districts']['penn hills']['schools'],[])
    def test_reporting_group_and_student_population_aliases(self):
        for header in ('Reporting Group','Student Population'):
            data=workbook([['LEA Name','School Name',header,'Percent Regular Attendance'],['Penn Hills SD','School A','All Students','98']])
            r=context.parse_workbook(data,'2024-2025',context.PDE_INDEX,self.now)
            self.assertEqual(r['districts']['penn hills']['schools'][0]['metrics'][0]['value'],98)
    def test_link_discovery_uses_newest_official_performance_workbook_only(self):
        html='<a href="/home/getdatafile?id=60">Future Ready Performance Data for SY 2024-2025</a><a href="https://evil.example/data">Future Ready Performance Data for SY 2025-2026</a><a href="/home/getdatafile?id=53">Future Ready Performance Data for SY 2023-2024</a>'
        self.assertEqual(context.workbook_link(html),('2024-2025','https://www.futurereadypa.org/home/getdatafile?id=60'))
    def test_district_comes_from_matched_parcel_not_postal_verona(self):
        response={'success':True,'result':{'records':[{'PARID':self.county['parcel_id'],'SCHOOLDESC':'Penn Hills'}]}}
        data=context.parse_workbook(self.fixture(),'2024-2025',context.PDE_INDEX,self.now);data['status']='available'
        with patch.object(context,'clock',return_value=self.now),patch.object(context.store,'public_get',return_value=json.dumps(response)),patch.object(context,'education_data',return_value=data):
            result=context.collect(self.p,self.county,self.point,self.root)
        self.assertEqual(result['education']['district'],'Penn Hills SD')
        self.assertFalse(result['education']['assignment_verified'])
        self.assertEqual(result['education']['parcel_id'],self.county['parcel_id'])
        self.assertEqual(result['crime']['metrics'],[])
    def test_foreign_county_subject_never_contacts_sources(self):
        self.county['subject']={**self.p,'address':'99 Other St'}
        with patch.object(context.store,'public_get',side_effect=AssertionError('network')),patch.object(context,'get_bytes',side_effect=AssertionError('network')):
            result=context.collect(self.p,self.county,self.point,self.root)
        self.assertEqual(result['education']['status'],'district_unavailable')
    def test_disabled_lookup_never_contacts_sources(self):
        with patch.object(context.store,'public_get',side_effect=AssertionError('network')),patch.object(context,'get_bytes',side_effect=AssertionError('network')):
            result=context.collect(self.p,self.county,self.point,self.root,allow_public=False)
        self.assertEqual(result['education']['schools'],[])
    def test_same_quarter_cache_reused_and_failed_refresh_keeps_old_date(self):
        cached=context.parse_workbook(self.fixture(),'2024-2025',context.PDE_INDEX,self.now);cached['status']='available'
        context.write(self.root/'official_education/performance.json',cached)
        with patch.object(context,'get_bytes',side_effect=AssertionError('network')):
            self.assertEqual(context.education_data(self.root,True,self.now),cached)
        with patch.object(context,'get_bytes',side_effect=OSError('offline')):
            result=context.education_data(self.root,True,datetime(2027,1,2,tzinfo=timezone.utc))
        self.assertEqual(result['status'],'cached_stale');self.assertEqual(result['observed_at'],cached['observed_at'])
    def test_school_district_mismatch_rejects_returned_parcel(self):
        response={'success':True,'result':{'records':[{'PARID':'different','SCHOOLDESC':'Penn Hills'}]}}
        with patch.object(context.store,'public_get',return_value=json.dumps(response)):
            self.assertIsNone(context.parcel_school_district(self.county,self.root,True,self.now))

if __name__=='__main__':unittest.main()
