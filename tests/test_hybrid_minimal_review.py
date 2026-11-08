import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_agent.hybrid_minimal_review import MinimalEvidenceReviewer,verify_saved_minimal_review
from research_agent.minimal_delivery import MINIMAL_LIMIT,MINIMAL_SUMMARY
from research_agent.reports import ReportDraft
from research_agent.settings import Settings


class StubModel:
    def __init__(self,mutate=None):
        self.payloads=[];self.mutate=mutate;self.http_client=SimpleNamespace(close=lambda:None)

    def invoke(self,messages,**kwargs):
        payload=None
        for message in messages:
            try:value=json.loads(message.content)
            except (ValueError,TypeError):continue
            if isinstance(value,dict) and 'targets' in value:payload=value;break
        self.payloads.append(payload)
        verdicts=[]
        for target in payload['targets']:
            source=target['evidence_ids'][0]
            qid=next(k for k,v in payload['quote_index'][source].items() if 'The upgrade is proposed' in v)
            verdicts.append(dict(claim_id=target['claim_id'],status='supported',reason='Explicit saved source',
                scope_alignment='aligned',premise_assessment='supported',quotes=[{'source_id':source,'excerpt_id':qid}]))
        result={'verdicts':verdicts}
        if self.mutate:result=self.mutate(result,len(self.payloads))
        return SimpleNamespace(content=json.dumps(result),response_metadata={'model_name':'doubao-seed-2.1-pro',
            'token_usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}},usage_metadata=None)


class MinimalReviewTests(unittest.TestCase):
    def setUp(self):
        self.draft=ReportDraft(title='Limited evidence report',delivery_kind='minimal',
            summary=MINIMAL_SUMMARY,limitations=[MINIMAL_LIMIT],
            observations=[{'statement':'The upgrade is proposed.','evidence_ids':['page-fixture']}])
        self.row={'record_id':'page-fixture','text':'The upgrade is proposed. It has not been executed.'}

    def run_review(self,draft=None,mutate=None,context=None):
        model=StubModel(mutate);reviewer=MinimalEvidenceReviewer(Settings())
        with patch('research_agent.review.EvidenceReviewer.build_model',return_value=model):
            result=reviewer.review(draft or self.draft,lambda _:self.row,process_context=context)
        return result,reviewer,model

    def test_only_exact_process_boilerplate_is_host_checked(self):
        result,reviewer,model=self.run_review()
        self.assertEqual(result['status'],'reviewed')
        self.assertEqual(result['review_version'],'support-hybrid-minimal-v1')
        self.assertEqual([t['claim_id'] for t in model.payloads[0]['targets']],['O1'])
        self.assertEqual(set(result['host_literal_target_ids']),{'S1.1','S1.2','L1'})
        self.assertEqual(len(result['targets']),4)
        self.assertEqual(result['model_review']['targets'][0]['claim_id'],'O1')
        self.assertEqual(reviewer.calls,1);self.assertEqual(reviewer.max_requests,2)
        self.assertEqual(reviewer.rounds,1);self.assertEqual(reviewer.max_rounds,1)
        self.assertEqual(result['request_usages'],result['model_review']['request_usages'])
        self.assertNotIn('scope_critic',result['model_review'])
        verify_saved_minimal_review(result,self.draft)

    def test_custom_summary_and_limit_are_sent_to_model(self):
        draft=self.draft.model_copy(update={'summary':'The upgrade is proposed.','limitations':['The source does not establish execution.']})
        result,_,model=self.run_review(draft)
        self.assertEqual({t['claim_id'] for t in model.payloads[0]['targets']},{'O1','S1','L1'})
        self.assertEqual(result['host_literal_target_ids'],[])
        self.assertEqual(result['status'],'reviewed')

    def test_material_negative_still_blocks_reduced_report(self):
        def reject(value,attempt):
            value['verdicts'][0].update(status='contradicted',quotes=[],reason='Source explicitly says not executed',correction='Remove the execution assertion.')
            return value
        result,_,_=self.run_review(mutate=reject)
        self.assertEqual(result['status'],'needs_revision')
        self.assertEqual(result['contradicted'],1)
        verify_saved_minimal_review(result,self.draft)

    def test_format_repair_uses_reserved_two_request_limit(self):
        def malformed_once(value,attempt):return {'verdicts':[]} if attempt==1 else value
        result,reviewer,model=self.run_review(mutate=malformed_once)
        self.assertEqual(result['status'],'reviewed');self.assertEqual(reviewer.calls,2)
        self.assertEqual(result['format_repairs'],1)
        self.assertEqual(len(result['request_usages']),2)
        self.assertEqual(len(model.payloads),2)

    def test_scope_and_process_context_preserved(self):
        process={'deterministic_validation':{'status':'passed','checks':['numeric_values_and_units']}}
        result,_,model=self.run_review(context=process)
        self.assertEqual(result['process_context'],process)
        self.assertEqual(model.payloads[0]['process_context'],process)
        self.assertEqual(result['summary_context']['text'],MINIMAL_SUMMARY)

    def test_availability_metadata_proof_is_host_checked_and_replayed(self):
        from research_agent.host_review_facts import AVAILABILITY_PREFIX,AVAILABILITY_SUFFIX
        clock='2026-11-08T00:00:00Z'
        self.row={**self.row,'retrieved_at':clock}
        draft=self.draft.model_copy(update={'limitations':[MINIMAL_LIMIT,AVAILABILITY_PREFIX+clock+AVAILABILITY_SUFFIX]})
        result,_,model=self.run_review(draft)
        self.assertEqual([t['claim_id'] for t in model.payloads[0]['targets']],['O1'])
        self.assertIn('L2',result['host_literal_target_ids'])
        self.assertEqual(result['status'],'reviewed')
        verify_saved_minimal_review(result,draft)
        changed=copy.deepcopy(result)
        changed['evidence_text']['page-fixture']=changed['evidence_text']['page-fixture'].replace(clock,'2026-11-09T00:00:00Z')
        with self.assertRaises(ValueError):verify_saved_minimal_review(changed,draft)

    def test_literal_minimal_claim_is_not_host_true_when_other_material_remains(self):
        draft=self.draft.model_copy(update={'interpretations':[self.draft.observations[0]]})
        result,_,model=self.run_review(draft)
        self.assertEqual(result['host_literal_target_ids'],[])
        self.assertEqual({t['claim_id'] for t in model.payloads[0]['targets']},{'O1','I1','S1.1','S1.2','L1'})

    def test_cache_reuses_inner_receipt_without_another_round(self):
        model=StubModel();reviewer=MinimalEvidenceReviewer(Settings())
        with patch('research_agent.review.EvidenceReviewer.build_model',return_value=model):
            first=reviewer.review(self.draft,lambda _:self.row)
            second=reviewer.review(self.draft,lambda _:self.row)
        self.assertEqual(first['review_id'],second['review_id'])
        self.assertEqual(reviewer.calls,1);self.assertEqual(reviewer.rounds,1)

    def test_full_report_cannot_enter_minimal_protocol(self):
        full=self.draft.model_copy(update={'delivery_kind':'full'})
        with self.assertRaises(ValueError):MinimalEvidenceReviewer(Settings()).review(full,lambda _:self.row)
        with self.assertRaises(ValueError):MinimalEvidenceReviewer(Settings(),max_requests=3)

    def test_saved_draft_host_set_and_quotes_cannot_change(self):
        result,_,_=self.run_review()
        variants=[]
        changed=copy.deepcopy(result);changed['host_literal_target_ids'].append('O1');variants.append(changed)
        changed=copy.deepcopy(result);changed['model_review']['verdicts'][0]['quotes'][0]['text']='Invented evidence';variants.append(changed)
        changed=copy.deepcopy(result);changed['model_review']['targets']=[];variants.append(changed)
        changed=copy.deepcopy(result);changed['evidence_text']['page-fixture']='different source';variants.append(changed)
        changed=copy.deepcopy(result);changed['verdicts']=changed['verdicts'][:-1];variants.append(changed)
        changed=copy.deepcopy(result);changed['request_budget']['requests_used']=3;variants.append(changed)
        for i,variant in enumerate(variants):
            with self.subTest(i=i),self.assertRaises(ValueError):verify_saved_minimal_review(variant,self.draft)
        draft=self.draft.model_copy(update={'summary':MINIMAL_SUMMARY+' The upgrade executed.'})
        with self.assertRaises(ValueError):verify_saved_minimal_review(result,draft)


if __name__=='__main__':unittest.main()
