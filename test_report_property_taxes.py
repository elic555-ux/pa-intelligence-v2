import copy,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import report_property_taxes as taxes

P={'id':'qa-1','address':'1822 Fox Way','city':'Pittsburgh','state':'PA','zip':'15203'}
C={'status':'matched','county':'Allegheny','subject':P,'parcel_id':'0012E00199000000'}
NOW=datetime(2026,10,26,12,tzinfo=timezone.utc)
# Minimal rendered public bill fixture. No owner, mailing address or payment history.
FIELDS={'InfoPane_parcelIDLbl':'Parcel ID: 0012-E-00199-0000-00',
 'InfoPane_addressLbl':'Address: 1822 FOX WAY<br>PITTSBURGH, PA 15203',
 'parcelID':'0012E00199000000','netTaxDue':'Net Tax Due <br>March 31, 2026',
 'grossTaxDue':'Gross Tax Due <br>April 30, 2026','netTax':'$364.79','grossTax':'$372.23',
 'milageRate':'6.43','taxableValue':'$82,700','lblHomesteadText':'*Homestead exclusion applied'}
def html(fields=None):return ''.join('<span id="MainContent_'+k+'">'+v+'</span>' for k,v in (fields or FIELDS).items())
class FakeReader:
    def __init__(self,raw=None,error=None):self.requests=0;self.raw=raw or html();self.error=error
    def read_bill(self,url):
        self.requests+=2
        if self.error:raise taxes.SourceStop(self.error)
        return self.raw

class CountyBillTests(unittest.TestCase):
    def setUp(self):
        self.saved={}
        self.read=patch.object(taxes.store,'read',side_effect=lambda path:copy.deepcopy(self.saved.get(str(path)))).start()
        patch.object(taxes.store,'write',side_effect=lambda path,value:self.saved.__setitem__(str(path),copy.deepcopy(value))).start()
        self.addCleanup(patch.stopall)
    def test_exact_face_bill_not_history_discount_or_new_owner_forecast(self):
        r=taxes.parse_bill(html()+ '<table>2025 Payment $99.99</table>',P,C,NOW)
        self.assertEqual(r['county_face_amount'],372.23);self.assertEqual(r['county_discount_amount'],364.79)
        self.assertTrue(r['homestead_exclusion_applied']);self.assertFalse(r['forecast_for_new_owner'])
        self.assertIsNone(r['total_property_tax']);self.assertFalse(r['complete']);self.assertFalse(r['operating_expenses_complete'])
    def test_address_zip_and_both_parcels_must_match(self):
        for key,value in [('InfoPane_addressLbl','Address: 1822 FOX WAY<br>PITTSBURGH, PA 15204'),('parcelID','0012E00198000000'),('InfoPane_parcelIDLbl','Parcel ID: 0012-E-00198-0000-00')]:
            with self.subTest(key=key),self.assertRaises(taxes.SourceStop):taxes.parse_bill(html(dict(FIELDS,**{key:value})),P,C,NOW)
    def test_old_year_missing_zero_duplicate_or_challenge_are_never_accepted(self):
        for raw in [html().replace('2026','2025'),html(dict(FIELDS,grossTax='$0.00')),html(dict(FIELDS,grossTax='')),html()+'<span id="MainContent_grossTax">$999.99</span>','<html>Checking your browser</html>']:
            with self.subTest(raw=raw[:40]),self.assertRaises(taxes.SourceStop):taxes.parse_bill(raw,P,C,NOW)
    def test_unmatched_county_never_reads_source(self):
        reader=FakeReader();r=taxes.collect(P,dict(C,status='ambiguous'),reader=reader,now=NOW)
        self.assertEqual(reader.requests,0);self.assertEqual(r['status'],'subject_mismatch')
    def test_disabled_does_not_read(self):
        reader=FakeReader();self.assertEqual(taxes.collect(P,C,allow_public=False,reader=reader,now=NOW)['status'],'lookup_disabled');self.assertEqual(reader.requests,0)
    def test_cache_preserves_observation_and_makes_no_repeat_request(self):
        reader=FakeReader();a=taxes.collect(P,C,reader=reader,now=NOW);b=taxes.collect(P,C,reader=reader,now=NOW+timedelta(days=1))
        self.assertEqual(reader.requests,2);self.assertEqual(b['observed_at'],a['observed_at']);self.assertEqual(b['source_network_requests'],0)
    def test_prior_year_cache_is_not_relabelled_current_year(self):
        taxes.collect(P,C,reader=FakeReader(),now=NOW)
        r=taxes.collect(P,C,reader=FakeReader(),now=NOW.replace(year=2027));self.assertEqual(r['status'],'bill_year_mismatch')
    def test_block_cooldown_is_shared_and_no_automatic_retry(self):
        reader=FakeReader(error='source_http_403');a=taxes.collect(P,C,reader=reader,now=NOW)
        b=taxes.collect(dict(P,id='qa-2'),C,reader=reader,now=NOW+timedelta(minutes=1))
        self.assertEqual(a['status'],'source_http_403');self.assertEqual(b['status'],'source_cooldown');self.assertEqual(reader.requests,2)
    def test_robot_disallow_stops_before_bill(self):
        reader=taxes.PublicReader()
        with patch.object(reader,'get',return_value='User-agent: *\nDisallow: /Tax') as get,self.assertRaises(taxes.SourceStop):reader.read_bill(taxes.source_url(C['parcel_id']))
        self.assertEqual(get.call_count,1)
    def test_no_redirect_pay_or_other_host_url_is_allowed(self):
        reader=taxes.PublicReader()
        for url in ['https://example.com/Tax','https://realestate.alleghenycounty.us/Pay','http://realestate.alleghenycounty.us/Tax']:
            with self.assertRaises(taxes.SourceStop):reader.get(url)
        self.assertEqual(reader.requests,0)

if __name__=='__main__':unittest.main()
