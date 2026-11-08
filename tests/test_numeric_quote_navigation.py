"""Synthetic compound numeric evidence; never uses private research output."""
import json,unittest
from research_agent.jev_review import _bundles,_numeric_anchors
from research_agent.review import quote_index,EvidenceReviewer
from research_agent.settings import Settings

class NumericQuoteNavigationTests(unittest.TestCase):
    def test_values_and_time_boundaries_share_an_exact_quote_bundle(self):
        row={'record_id':'live-synthetic','observed_at':'2025-02-08T00:00:00Z',
             'values':{'return_fraction':'0.25','baseline_close_usd':'80','last_close_usd':'100','bar_count':'7'},
             'units':{'return_fraction':'fraction','baseline_close_usd':'USD','last_close_usd':'USD','bar_count':'count'},
             'period_start':'2025-02-01T00:00:00Z','period_end':'2025-02-08T00:00:00Z',
             'method':'Last completed close divided by baseline close minus one.'}
        texts={'live-synthetic':json.dumps(row,indent=2)}
        target={'evidence_ids':['live-synthetic'],'statement':'The seven completed daily bars returned 0.25, from 80 USD to 100 USD.','metric':'return_fraction','value':'0.25','unit':'fraction'}
        index=quote_index(texts)
        bundle=_bundles(target,texts,scope={'assets':['ETH'],'start':row['period_start'],'end':row['period_end']})['B1']
        combined='\n'.join(index[q['record_id']][q['excerpt_id']] for q in bundle)
        for number in ('"0.25"','"80"','"100"','"7"'):
            self.assertIn(number,combined)
        self.assertEqual({str(n) for n in _numeric_anchors('2025-02-01T00:00:00Z, return 0.25')},{'0.25'})

    def test_normal_review_has_two_rounds_and_minimum_has_one(self):
        self.assertEqual(EvidenceReviewer(Settings(),max_requests=4).max_rounds,2)
        self.assertEqual(EvidenceReviewer(Settings(),max_requests=2).max_rounds,1)
