import copy,json,unittest
from unittest.mock import patch
from datetime import date,datetime,timezone
import rehab_pricebook as prices

class RehabPricebookTests(unittest.TestCase):
    def setUp(self):
        self.p={'id':'qa-1'}
        self.book=prices.catalog(today=date(2026,10,27))
        self.analysis={'status':'partial','property_id':'qa-1','complete':False,'items':[
            {'name':'למינציה','task_code':'laminate_floor','status':'observed'}]}
        self.quantity={'value':200,'unit':'floor_sqft','property_id':'qa-1',
            'status':'documented_measurement','source_name':'Measured listing floor plan',
            'source_url':'https://example.com/measured-floor-plan','observed_at':datetime.now(timezone.utc).date().isoformat()}
    def test_catalog_is_sourced_national_and_not_a_local_quote(self):
        self.assertEqual(len(self.book['entries']),11);self.assertEqual(self.book['geography'],'US_national')
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
        self.assertEqual(result['status'],'source_unavailable');self.assertEqual(len(result['pricebook']['entries']),11)

    def test_wall_and_cabinet_references_never_price_even_with_a_surface_quantity(self):
        for code in ['wall_paint','cabinet_refinish']:
            self.analysis['items'][0].update(task_code=code,quantity_evidence={**self.quantity,'unit':'surface_sqft'})
            result=prices.attach(self.analysis,self.p,self.book)
            self.assertIsNone(result['total_high'])
            self.assertEqual(result['items'][0]['pricing_reason'],'technical_scope_not_confirmed')
            self.assertEqual(result['items'][0]['price_observed_at'],'2026-10-27')

    def test_new_references_do_not_relabel_previous_source_observations(self):
        self.assertEqual(self.book['review_due_at'],'2027-01-24')
        self.assertEqual(self.book['updated_at'],'2026-10-27')
        result=prices.attach(self.analysis,self.p,self.book)
        self.assertEqual(result['items'][0]['price_observed_at'],'2026-10-26')

    def test_future_dated_reference_is_rejected(self):
        raw=copy.deepcopy(self.book);raw['entries'][0]['observed_at']='2026-10-28'
        with patch.object(prices.Path,'read_text',return_value=json.dumps(raw)):
            with self.assertRaisesRegex(ValueError,'invalid_entry_observation_date'):
                prices.catalog(today=date(2026,10,27))


class SavedPricebookRefreshTests(unittest.TestCase):
    def setUp(self):
        self.book=prices.catalog(today=date(2026,10,27))
        self.p={'id':'qa-1','address':'18 Main St','city':'Pittsburgh','state':'PA','zip':'15203'}
        self.rehab={'property_id':'qa-1','status':'partial','observed_at':'2026-10-25',
            'photo_count':19,'interior_assessments':[{'finding':'preserve this observation'}],
            'items':[],'complete':False,'total_low':None,'total_high':None}
        self.receipt={'property_id':'qa-1','property':self.p,'generated_at':'2026-10-22',
            'sources':{'rent':{'observed_at':'2026-10-22','estimate':1820}},
            'report_analysis':{'property_id':'qa-1','property':self.p,'generated_at':'2026-10-25',
                'property_taxes':{'county_face_amount':372.23,'observed_at':'2026-10-26'},'rehab':self.rehab}}

    def run_refresh(self,receipt):
        import internal_report_store as store
        with patch.object(store,'read',side_effect=[[self.p],receipt]),patch.object(store,'write') as write:
            result=prices.refresh_saved('qa-1',book=self.book)
            return result,write

    def test_refresh_preserves_photo_rent_tax_and_receipt_dates_without_providers(self):
        result,write=self.run_refresh(self.receipt)
        saved=write.call_args.args[1]
        self.assertTrue(result['changed']);self.assertEqual(result['provider_calls'],0)
        self.assertEqual(saved['sources'],self.receipt['sources'])
        self.assertEqual(saved['generated_at'],self.receipt['generated_at'])
        self.assertEqual(saved['report_analysis']['generated_at'],'2026-10-25')
        self.assertEqual(saved['report_analysis']['property_taxes'],self.receipt['report_analysis']['property_taxes'])
        rehab=saved['report_analysis']['rehab']
        self.assertEqual(rehab['interior_assessments'],self.rehab['interior_assessments'])
        self.assertEqual(rehab['observed_at'],'2026-10-25');self.assertEqual(rehab['photo_count'],19)
        self.assertIsNone(rehab['total_high']);self.assertFalse(rehab['complete'])
        self.assertNotIn('pricebook',self.receipt['report_analysis']['rehab'])

    def test_same_catalog_is_idempotent(self):
        first,write=self.run_refresh(self.receipt)
        saved=write.call_args.args[1]
        result,second_write=self.run_refresh(saved)
        self.assertFalse(result['changed']);second_write.assert_not_called()

    def test_foreign_id_or_address_in_any_nested_source_is_not_written(self):
        for container,field,value in [('report','property_id','other'),('analysis','property_id','other'),
            ('rehab','property_id','other'),('report','property',{**self.p,'address':'99 Other Rd'}),
            ('analysis','property',{**self.p,'zip':'00000'})]:
            receipt=copy.deepcopy(self.receipt)
            target=receipt if container=='report' else receipt['report_analysis'] if container=='analysis' else receipt['report_analysis']['rehab']
            target[field]=value
            import internal_report_store as store
            with patch.object(store,'read',side_effect=[[self.p],receipt]),patch.object(store,'write') as write:
                with self.assertRaisesRegex(ValueError,'report_subject_mismatch'):
                    prices.refresh_saved('qa-1',book=self.book)
                write.assert_not_called()

if __name__=='__main__':unittest.main()
