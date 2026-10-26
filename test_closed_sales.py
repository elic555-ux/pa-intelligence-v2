import copy,unittest
from datetime import date
import closed_sales as sales

class RecordedSalesTest(unittest.TestCase):
    def setUp(self):
        self.p={'id':'test','property_type':'Single Family','sqft':1500,'beds':3}
        self.point={'parcel_id':'0012E00199000000','lat':40.43,'lng':-79.98,'neighborhood':'South Side Flats'}
        self.row={'PARID':'0012E00198000000','FULL_ADDRESS':'18 Example St','SALEDATE':'2026-10-01','RECORDDATE':'2026-10-05','PRICE':200000,'SALECODE':'0','SALEDESC':'VALID SALE','FINISHEDLIVINGAREA':1500,'BEDROOMS':3,'FULLBATHS':1,'HALFBATHS':0,'YEARBLT':1900,'USEDESC':'SINGLE FAMILY','LAT':40.4301,'LONG':-79.98,'CITY_NEIGHBORHOOD':'South Side Flats','DEEDBOOK':'20000','DEEDPAGE':'100'}
    def normalized(self,rows):return sales.normalize_rows(rows,self.p,self.point,today=date(2026,10,25))
    def test_actual_sale_retains_price_and_deed(self):
        rows,rejected=self.normalized([self.row]);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['amount'],200000)
        self.assertEqual(rows[0]['validation'],'county_validated');self.assertEqual(rows[0]['kind'],'recorded_sale');self.assertIsNone(rows[0]['neighborhood_class_match'])
    def test_pending_county_review_is_never_validated(self):
        row={**self.row,'SALECODE':'AA','SALEDESC':'SALE NOT ANALYZED'}
        rows,_=self.normalized([row]);self.assertEqual(rows[0]['validation'],'pending_county_review')
    def test_non_market_transfers_are_excluded_even_with_high_price(self):
        for code,desc in [('1','SHERIFF SALE'),('3','LOVE&AFFECTION'),('H','MULTI PARCEL SALE'),('',''),('0','SALE NOT ANALYZED')]:
            rows,rejected=self.normalized([{**self.row,'SALECODE':code,'SALEDESC':desc}]);self.assertEqual(rows,[]);self.assertIn('non_market_or_unknown_transfer',rejected)
    def test_dates_radius_size_type_and_subject_are_required(self):
        for update in [{'SALEDATE':'2027-01-01'},{'SALEDATE':'2020-01-01'},{'LAT':41},{'LAT':None},{'FINISHEDLIVINGAREA':4000},{'FINISHEDLIVINGAREA':None},{'BEDROOMS':None},{'USEDESC':'VACANT LAND'},{'PARID':self.point['parcel_id']}]:
            rows,_=self.normalized([{**self.row,**update}]);self.assertEqual(rows,[],update)
    def test_transaction_duplicates_do_not_inflate_comp_count(self):
        rows,rejected=self.normalized([self.row,self.row]);self.assertEqual(len(rows),1);self.assertEqual(rejected['duplicate'],1)
    def test_foreign_parcel_cannot_trigger_a_query(self):
        self.assertIsNone(sales.parcel_point("x' OR 1=1"))
    def test_distance_is_in_miles_and_no_missing_zero(self):
        self.assertAlmostEqual(sales.distance(40,-80,40,-80),0);self.assertIsNone(sales.distance(40,-80,None,-80));self.assertGreater(sales.distance(40,-80,41,-80),60)

if __name__=='__main__':unittest.main()
