import unittest
import report_financing as finance

class PublishedTermsTest(unittest.TestCase):
    def test_product_labels_exclude_unrelated_recent_funded_examples(self):
        html='<nav>Interest Rate 1%</nav><main>Current as of Interest Rate 8.5 - 11% Origination fee 1 - 1.75% Loan To Cost (LTC) up to 90% Loan To ARV up to 75% Term up to 18 months Minimum Loan Amount $100,000 Maximum Loan Amount $5,000,000 Minimum FICO 650</main><aside>Recently Funded Interest Rate 3%</aside>'
        r=finance.parse_terms(finance.PRODUCTS[0],html)
        self.assertEqual(r['rate_text'],'8.5 - 11%');self.assertEqual(r['ltc_text'],'up to 90%');self.assertEqual(r['minimum_loan'],'$100,000');self.assertFalse(r['is_personal_quote']);self.assertFalse(r['eligibility_confirmed'])
    def test_kiavi_marketing_minimum_does_not_use_example_calculator_rate(self):
        text='Rates as low as 8.25% Loans from $75K to $5MM Up to 100% of purchase price Up to 80% of after-repair value 12, 18 + 24 month terms Example Interest Rate 7.75%'
        r=finance.parse_terms(finance.PRODUCTS[2],text);self.assertEqual(r['rate_text'],'as low as 8.25%');self.assertIsNone(r['origination_text'])
    def test_changed_page_cannot_produce_old_or_invented_rates(self):
        with self.assertRaises(ValueError):finance.parse_terms(finance.PRODUCTS[0],'Sorry product page removed')
    def test_arbitrary_hosts_are_never_requested(self):
        for u in ['http://newsilver.com/','https://localhost/','https://newsilver.com.evil.test/']:
            with self.assertRaises(ValueError):finance.get_html(u)

if __name__=='__main__':unittest.main()

