import copy,unittest
from datetime import date,datetime,timezone
import rehab_pricebook as prices

class RehabPricebookTests(unittest.TestCase):
    def setUp(self):
        self.p={'id':'qa-1'}
        self.book=prices.catalog(today=date(2026,10,26))
        self.analysis={'status':'partial','property_id':'qa-1','complete':False,'items':[
            {'name':'למינציה','task_code':'laminate_floor','status':'observed'}]}
        self.quantity={'value':200,'unit':'floor_sqft','property_id':'qa-1',
            'status':'documented_measurement','source_name':'Measured listing floor plan',
            'source_url':'https://example.com/measured-floor-plan','observed_at':datetime.now(timezone.utc).date().isoformat()}
    def test_catalog_is_sourced_national_and_not_a_local_quote(self):
        self.assertEqual(len(self.book['entries']),9);self.assertEqual(self.book['geography'],'US_national')
        self.assertFalse(self.book['local_adjustment_applied'])
        self.assertEqual(self.book['status'],'current')
    def test_unknown_quantity_has_unit_price_but_never_a_project_total(self):
        result=prices.attach(self.analysis,self.p,self.book)
        self.assertEqual(result['items'][0]['unit_low'],3.8);self.assertIsNone(result['total_low'])
        self.assertFalse(result['complete']);self.assertEqual(result['unpriced_items_count'],1)
    def test_documented_measurement_times_sourced_rate(self):
        self.analysis['items'][0]['quantity_evidence']=self.quantity
        result=prices.attach(self.analysis,self.p,self.book)
        self.assertEqual((result['total_low'],result['total_high']),(760,960))
        self.assertFalse(result['complete']);self.assertEqual(result['status'],'partial')
    def test_stale_pricebook_never_prices_work(self):
        book=prices.catalog(today=date(2027,1,24));self.analysis['items'][0]['quantity_evidence']=self.quantity
        result=prices.attach(self.analysis,self.p,book)
        self.assertIsNone(result['total_high']);self.assertEqual(result['items'][0]['pricing_reason'],'pricebook_review_due')
    def test_foreign_property_or_wrong_unit_or_invented_measurement_rejected(self):
        for change in [{'property_id':'other'},{'unit':'roof_sqft'},{'status':'ai_guess'},{'value':True},{'observed_at':'2099-01-01'}]:
            self.analysis['items'][0]['quantity_evidence']={**self.quantity,**change}
            self.assertIsNone(prices.attach(self.analysis,self.p,self.book)['total_high'])
    def test_unseen_work_does_not_become_a_replacement_cost(self):
        self.analysis['items'][0].update(status='not_visible',quantity_evidence=self.quantity)
        self.assertIsNone(prices.attach(self.analysis,self.p,self.book)['total_high'])
    def test_ac_example_requires_technical_scope_and_is_reference_only(self):
        self.analysis['items'][0].update(task_code='central_ac_3ton',quantity_evidence={**self.quantity,'unit':'system','value':1})
        result=prices.attach(self.analysis,self.p,self.book)
        self.assertIsNone(result['total_high']);self.assertEqual(result['items'][0]['pricing_reason'],'technical_scope_not_confirmed')
    def test_pricebook_available_even_when_ai_service_unavailable(self):
        result=prices.attach({'status':'source_unavailable','items':[]},self.p,self.book)
        self.assertEqual(result['status'],'source_unavailable');self.assertEqual(len(result['pricebook']['entries']),9)

if __name__=='__main__':unittest.main()
