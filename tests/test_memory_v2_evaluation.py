"""Evaluation protocol contracts using synthetic records only; no model download."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('memory_v2_eval',Path(__file__).resolve().parents[1]/'scripts/evaluation/evaluate_memory_v2.py')
evalmod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(evalmod)
A='page-'+'a'*24
B='page-'+'b'*24


def source(rid,text,known='2026-01-01T00:00:00Z'):
 return {'record_id':rid,'title':'Ethereum synthetic notice','text':text,'asset':'ETH','assets':['ETH'],
  'published_at':'2026-01-01T00:00:00Z','retrieved_at':known,'url':'https://example.org/'+rid,
  'source_kind':'page','source':{'url':'https://example.org/'+rid}}

class MemoryV2EvaluationTests(unittest.TestCase):
 def setUp(self):
  self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
  self.output=Path(self.temporary.name)/'frozen'
  self.rows=[source(A,'Ethereum Atlas launch remains planned. Read the original.'),source(B,'Ethereum Atlas launch postponed. New date is unknown.')]
  self.cases=[{'case_id':'synthetic-query','query':'Ethereum Atlas postponed','label_scope':'synthetic_unit_fixture',
   'asset':'ALL','required_passages':[{'record_id':B,'text':'launch postponed.'}]}]

 def freeze(self):return evalmod.freeze_rows(self.output,self.rows,self.cases)

 def test_freeze_precedes_queries_and_is_not_overwritten(self):
  manifest=self.freeze()
  self.assertEqual(manifest['source_count'],2)
  self.assertFalse(manifest['human_ground_truth']);self.assertFalse(manifest['heldout'])
  self.assertFalse((self.output/'runs').exists())
  with self.assertRaises(ValueError):self.freeze()

 def test_manifest_and_source_tampering_fail_closed(self):
  self.freeze();path=self.output/'sources.json';path.write_text(path.read_text()+' ')
  with self.assertRaisesRegex(ValueError,'Frozen input changed'):evalmod.load_frozen(self.output)

 def test_missing_exact_span_cannot_be_frozen_as_a_positive_label(self):
  self.cases[0]['required_passages'][0]['text']='Fabricated source claim.'
  with self.assertRaises(ValueError):self.freeze()
  self.assertFalse(self.output.exists())

 def test_span_check_uses_selected_original_excerpt_not_a_model_summary(self):
  self.freeze();case=evalmod.load_frozen(self.output)[2][0]
  missing=evalmod.span_scores(case,{'results':[{'record_id':B,'excerpt':'A useful summary of an alleged delay'}]})
  self.assertTrue(missing['all_required_sources_in_pack']);self.assertFalse(missing['all_required_spans_in_pack'])
  exact=evalmod.span_scores(case,{'results':[{'record_id':B,'excerpt':self.rows[1]['text']}]})
  self.assertTrue(exact['all_required_spans_in_pack'])

 def test_no_encoder_explicitly_reports_dense_unavailable_and_common_denominator(self):
  self.freeze();receipt=evalmod.evaluate(self.output)
  self.assertEqual(receipt['encoder_status'],'dense_not_available')
  self.assertEqual(receipt['denominators']['real_queries'],1)
  self.assertEqual(receipt['denominators']['real_query_policy_outputs'],2)
  self.assertEqual(receipt['policy_results']['lexical']['query_denominator'],1)
  self.assertEqual(receipt['policy_results']['hybrid']['query_denominator'],1)
  self.assertEqual(receipt['model_provider_calls'],0);self.assertEqual(receipt['network_requests'],0)
  self.assertFalse(receipt['semantic_answer_accuracy_measured'])
  self.assertEqual(receipt['synthetic_passed'],8)
  self.assertEqual(receipt['strict_source_probes_passed'],2)
  self.assertEqual(receipt['derived_card_clock_probe']['strict_card_count'],0)

 def test_existing_runs_cannot_be_overwritten_or_hidden(self):
  self.freeze();evalmod.evaluate(self.output)
  with self.assertRaisesRegex(ValueError,'Preserve prior'):evalmod.evaluate(self.output)

 def test_synthetic_denominator_and_scope_are_separate(self):
  results=evalmod.synthetic_diagnostics()
  self.assertEqual(len(results),8)
  self.assertTrue(all(r['passed'] for r in results),results)
  self.assertTrue(all(r['kind']=='controlled_synthetic_contract' for r in results))

 def test_previously_viewed_queries_remain_marked_development(self):
  dev=[row for row in evalmod.QUERY_SPECS if row[3]=='development_previously_seen']
  self.assertEqual({r[0] for r in dev},{'hoodi-delay','zkapi-chinese'})
  self.assertFalse(evalmod.PROTOCOL['new_independent_samples_claimed'])

 def test_early_source_failure_is_retained_in_policy_denominator(self):
  self.freeze()
  from unittest.mock import patch
  from research_agent.tools import ResearchTools
  with patch.object(ResearchTools,'news_pack',side_effect=ValueError('Synthetic runtime adapter failure')):
   receipt=evalmod.evaluate(self.output,run_name='failed-adapter')
  self.assertEqual(receipt['policy_results']['lexical']['query_denominator'],1)
  self.assertEqual(receipt['policy_results']['lexical']['errors'],1)
  self.assertEqual(receipt['policy_results']['hybrid']['errors'],1)
  self.assertEqual(receipt['policy_results']['hybrid']['all_required_sources'],0)

 def test_configured_but_broken_encoder_is_not_reported_as_dense_success(self):
  class BrokenEncoder:
   model_id='synthetic-unavailable-encoder'
   fingerprint='synthetic-no-weights'
   def encode(self,texts):raise ValueError('Synthetic local payload unavailable')
  self.freeze();receipt=evalmod.evaluate(self.output,encoder=BrokenEncoder(),run_name='broken-encoder')
  self.assertEqual(receipt['encoder_status'],'dense_unavailable')
  self.assertEqual(receipt['dense_statuses'],{'unavailable':1})
  self.assertEqual(receipt['policy_results']['hybrid']['completed'],1)
  self.assertEqual(receipt['model_provider_calls'],0)

if __name__=='__main__':unittest.main()
