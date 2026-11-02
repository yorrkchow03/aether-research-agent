from fixtures import fixture_run
import tempfile,unittest,json
from pathlib import Path
from research_agent.evaluation import choose_cases,direction,score_draft,EvaluationDraft,freeze_probe
from research_agent.reports import ReportDraft,validate_report
from research_agent.tools import ResearchTools
from research_data.sdk import ResearchData

class EvaluationTests(unittest.TestCase):
 def test_chronological_splits_and_nonoverlapping_future_horizons(self):
  rows=[{'observed_at':str(i)} for i in range(365)];cases=choose_cases(rows)
  self.assertEqual(len(cases),12)
  for split,lower,upper in [('development',0,182),('validation',182,273),('test',273,358)]:
   indices=sorted(c['price_index'] for c in cases if c['split']==split)
   self.assertTrue(all(lower<=i and i+7<upper for i in indices));self.assertTrue(all(b-a>=7 for a,b in zip(indices,indices[1:])))
 def test_missing_citations_and_wrong_numbers_are_counted(self):
  draft=EvaluationDraft.model_validate({'title':'fixture','summary':'fixture','observations':[{'statement':'missing','evidence_ids':[],'metric':'price_usd','value':'42','unit':'USD'},{'statement':'wrong','evidence_ids':['r'],'metric':'price_usd','value':'99','unit':'USD'}],'judgment':'insufficient_evidence','judgment_reason':'fixture','limitations':['fixture']})
  class Sources:
   def lookup(self,rid):return {'values':{'price_usd':'42'},'units':{'price_usd':'USD'}}
  score=score_draft(draft,Sources(),{'status':'review_failed'})
  self.assertEqual(score['citation_coverage'],.5);self.assertEqual(score['numerical_correctness'],0);self.assertIsNone(score['unsupported_rate'])
 def test_equal_number_from_wrong_unit_record_cannot_pass(self):
  rows={'eth':{'values':{'value':'42'},'units':{'value':'ETH'}},'usd':{'values':{'value':'100'},'units':{'value':'USD'}}}
  draft=ReportDraft(title='fixture',summary='fixture',observations=[{'statement':'wrong cross-record number','evidence_ids':['eth','usd'],'metric':'value','value':'42','unit':'USD'}],judgment='insufficient_evidence',judgment_reason='fixture',limitations=['fixture'])
  with self.assertRaises(ValueError):validate_report(draft,rows.__getitem__)
 def test_future_saved_source_is_not_visible_in_real_frozen_sdk(self):
  tmp_root=tempfile.TemporaryDirectory();self.addCleanup(tmp_root.cleanup);run=fixture_run(Path(tmp_root.name)/'project');sdk=ResearchData(run)
  try:rows=sdk.load_dataset('market.coingecko.eth.daily')['records']
  finally:sdk.close()
  with tempfile.TemporaryDirectory() as tmp:
   service=ResearchTools(run,Path(tmp),rows[100]['observed_at'])
   self.assertTrue(freeze_probe(service,rows[101]['record_id'])['future_record_blocked'])
   self.assertEqual(service.lookup(rows[100]['record_id'])['record_id'],rows[100]['record_id'])
 def test_directional_abstention_is_separate_from_flat(self):
  self.assertEqual(direction(.01),'up');self.assertEqual(direction(-.01),'down');self.assertEqual(direction(0),'flat')

if __name__=='__main__':unittest.main()
