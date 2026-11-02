import unittest
from research_agent.semantic_costs import estimate_cost

class SemanticCostTests(unittest.TestCase):
    def result(self,provider='jev',**updates):
        return {'provider':provider,'model':'jev-1.14.0','billable_request_made':True,
            'physical_request_count':1,'usage':{'input_tokens':2000,'output_tokens':100}}|updates
    def test_jev_shared_questions_do_not_multiply_tokens_and_output_free(self):
        r=estimate_cost(self.result())
        self.assertEqual(r['estimated_usd'],'0.000084')
        self.assertTrue(r['complete_usage']);self.assertIsNone(r['actual_account_debit'])
    def test_pro_is_afp_not_cash_and_cache_tokens_not_subtracted(self):
        r=estimate_cost(self.result('doubao',usage={'input_tokens':2000,'output_tokens':100,'input_token_details':{'cache_read':1700}}))
        self.assertEqual(r['estimated_afp'],'0.525')
        self.assertIsNone(r['estimated_usd']);self.assertEqual(r['cache_token_details']['cache_read'],1700)
    def test_missing_or_malformed_usage_is_never_zero_fee(self):
        for usage in [None,{}, {'input_tokens':True},{'input_tokens':float('nan')},{'input_tokens':-3}]:
            r=estimate_cost(self.result(usage=usage));self.assertIsNone(r['estimated_usd']);self.assertFalse(r['complete_usage'])
    def test_cache_hit_means_no_new_request_and_does_not_charge_old_usage(self):
        r=estimate_cost(self.result(cache_hit=True,physical_request_count=0))
        self.assertEqual(r['estimated_usd'],'0');self.assertFalse(r['new_request_submitted'])
    def test_unknown_version_does_not_borrow_unverified_price(self):
        r=estimate_cost(self.result(model='jev-2.0.0'))
        self.assertIsNone(r['estimated_usd']);self.assertFalse(r['complete_usage'])
    def test_retry_or_unobserved_physical_request_is_not_full_cost(self):
        for count in [2,None]:
            r=estimate_cost(self.result(physical_request_count=count))
            self.assertEqual(r['estimated_usd'],'0.000084');self.assertFalse(r['complete_usage'])

if __name__=='__main__':unittest.main()
