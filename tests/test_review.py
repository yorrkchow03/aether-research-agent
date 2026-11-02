from fixtures import project_fixture
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from research_agent.review import check_review, evidence_text, review_targets,exact_source_quote,quote_index,hydrate_review
from research_agent.reports import ReportDraft
from research_agent.runtime import AgentRuntime
from research_agent.tools import ResearchTools
from research_agent.settings import Settings
from research_agent.review import EvidenceReviewer

class ReviewTests(unittest.TestCase):
 def setUp(self):
  self.draft=ReportDraft.model_validate({'title':'fixture','summary':'A sourced test summary','observations':[{'statement':'price','evidence_ids':['test-id']}],'judgment':'insufficient_evidence','judgment_reason':'limited coverage','limitations':['fixture']})
  self.targets=review_targets(self.draft);self.texts={'test-id':'saved price scope is one exchange'}
 def result(self,status='supported'):
  return {'verdicts':[{'claim_id':t['claim_id'],'status':status,'reason':'fixture','quotes':[{'record_id':'test-id','text':'one exchange'}]} for t in self.targets]}
 def test_exact_source_quotes_and_eligible_ids_are_required(self):
  good=self.result();self.assertEqual(check_review(good,self.targets,self.texts)['status'],'reviewed')
  good['verdicts'][0]['quotes'][0]['text']='invented original excerpt'
  with self.assertRaises(ValueError):check_review(good,self.targets,self.texts)
  good=self.result();good['verdicts'][0]['quotes'][0]['record_id']='wrong-record'
  with self.assertRaises(ValueError):check_review(good,self.targets,self.texts)
 def test_missing_duplicate_and_conditional_fact_verdicts_rejected(self):
  good=self.result();good['verdicts'].pop()
  with self.assertRaises(ValueError):check_review(good,self.targets,self.texts)
  good=self.result();good['verdicts'][0]['claim_id']='S1'
  with self.assertRaises(ValueError):check_review(good,self.targets,self.texts)
  good=self.result();good['verdicts'][0]['status']='conditional'
  with self.assertRaises(ValueError):check_review(good,self.targets,self.texts)
 def test_unsupported_claim_is_retained_as_revision_not_passed(self):
  good=self.result();good['verdicts'][0].update(status='insufficient',quotes=[],correction='remove unsupported claim')
  result=check_review(good,self.targets,self.texts)
  self.assertEqual(result['status'],'needs_revision');self.assertEqual(result['unsupported'],1)
 def test_publication_blocks_failed_semantic_review_and_saves_draft(self):
  with tempfile.TemporaryDirectory() as tmp:
   runtime=AgentRuntime(root=project_fixture(Path(tmp)/'project'),state_dir=Path(tmp)/'runtime');session=runtime.create_session(model_mode='current_conversation_no_api')
   w=runtime._session_dir(session['session_id'])/'workspace';tools=ResearchTools(Path(session['data_run']),w,session['cutoff'])
   calc=tools.analyze('ETH',7)
   d=ReportDraft.model_validate({'title':'fixture','summary':'fixture','observations':[{'statement':'wrong causal assertion','evidence_ids':[calc['record_id']]}],'judgment':'insufficient_evidence','judgment_reason':'fixture','limitations':['fixture']})
   class RejectingReviewer:
    def review(self,*args):return {'review_id':'fixture-review','status':'needs_revision','revision_required':[{'claim_id':'O1','status':'insufficient','reason':'no causal support','correction':'remove causal assertion'}]}
   tools.reviewer=RejectingReviewer()
   with self.assertRaises(ValueError):tools.publish(d)
   self.assertTrue(list((w/'drafts').glob('*.json')));self.assertTrue((w/'reviews/fixture-review.json').exists());self.assertFalse(list((w/'reports').glob('*')))
 def test_saved_evidence_context_preserves_units_and_dates(self):
  text=evidence_text({'record_id':'r','values':{'rate_fraction':'0.01'},'units':{'rate_fraction':'fraction'},'observed_at':'2026-10-14T00:00:00Z','retrieved_at':'2026-10-15T00:00:00Z','series':list(range(30))})
  obj=json.loads(text);self.assertEqual(obj['units']['rate_fraction'],'fraction');self.assertEqual(len(obj['series_latest_observations']),10)
 def test_actual_multiline_source_quote_is_not_confused_with_json_escaping(self):
  s=json.dumps({'text':'升级状态\n在开发网上进行测试\n日期尚未确认'},ensure_ascii=False)
  self.assertTrue(exact_source_quote('在开发网上进行测试\n日期尚未确认',s))
  self.assertFalse(exact_source_quote('已经在主网上激活',s))
  self.assertFalse(exact_source_quote('  ',s))
 def test_indexed_quotes_are_hydrated_from_source_and_unknown_ids_rejected(self):
  texts={'r':evidence_text({'record_id':'r','values':{'price_usd':'42'},'text':'官方原文\n尚未确认主网日期'})};idx=quote_index(texts)
  for q,text in idx['r'].items():self.assertTrue(exact_source_quote(text,texts['r']))
  q=next(k for k,v in idx['r'].items() if v=='尚未确认主网日期')
  obj={'verdicts':[{'claim_id':'O1','status':'supported','reason':'fixture','quotes':[{'source_id':'r','excerpt_id':q}]}]}
  self.assertEqual(hydrate_review(obj,idx)['verdicts'][0]['quotes'][0]['text'],'尚未确认主网日期')
  obj['verdicts'][0]['quotes'][0]['excerpt_id']='invented'
  with self.assertRaises(ValueError):hydrate_review(obj,idx)
 def test_monitoring_action_may_be_conditional_but_facts_still_need_quotes(self):
  target=[{'claim_id':'T1','kind':'research_action','evidence_ids':[]}]
  obj={'verdicts':[{'claim_id':'T1','status':'conditional','reason':'future monitoring proposal','quotes':[]}]}
  self.assertEqual(check_review(obj,target,{})['status'],'reviewed')
  obj['verdicts'][0]['status']='supported'
  with self.assertRaises(ValueError):check_review(obj,target,{})
 def test_short_source_alias_preserves_actual_record_identity(self):
  obj={'verdicts':[{'claim_id':'O1','status':'supported','reason':'fixture','quotes':[{'source_id':'R1','excerpt_id':'Q1'}]}]}
  r=hydrate_review(obj,{'R1':{'Q1':'saved exact excerpt'}},{'R1':'actual-long-record-hash'})
  self.assertEqual(r['verdicts'][0]['quotes'][0],{'record_id':'actual-long-record-hash','text':'saved exact excerpt'})
 def test_reviewer_repairs_its_own_format_without_author_edit_or_false_pass(self):
  row={'record_id':'test-id','values':{'price_usd':'42'},'units':{'price_usd':'USD'}}
  idx=quote_index({'R1':evidence_text(row)});qid=next(k for k,v in idx['R1'].items() if 'price_usd' in v and '42' in v)
  base={'verdicts':[{'claim_id':t['claim_id'],'status':'supported','reason':'fixture','quotes':[{'source_id':'R1','excerpt_id':qid}]} for t in self.targets]}
  bad=json.loads(json.dumps(base));bad['verdicts'][0]['quotes'][0]['source_id']='mistyped-long-hash'
  requests=[];closed=[]
  class Fake:
   http_client=SimpleNamespace(close=lambda:closed.append(True))
   def invoke(self,messages,**kwargs):
    requests.append(messages[:]);content=bad if len(requests)==1 else base
    return SimpleNamespace(content=json.dumps(content),response_metadata={'model_name':'fixture-pro'},usage_metadata={'total_tokens':10})
  with tempfile.TemporaryDirectory() as tmp,patch('research_agent.review.create_ark_model',return_value=Fake()):
   reviewer=EvidenceReviewer(Settings(),diagnostic_dir=tmp)
   result=reviewer.review(self.draft,lambda rid:row)
   self.assertEqual(result['status'],'reviewed');self.assertEqual(result['format_repairs'],1);self.assertEqual(reviewer.calls,2)
   self.assertEqual(len(list(Path(tmp).glob('*.json'))),2);self.assertTrue(closed)
   self.assertTrue(any('source_id' in str(m.content) for m in requests[0]))

if __name__=='__main__':unittest.main()
