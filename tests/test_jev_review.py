"""Publication behavior, native transport and saved receipt invariants; no API."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from research_agent.jev_review import JevReportReviewer, verify_saved_jev_review, MODEL
from research_agent.minimal_delivery import MINIMAL_LIMIT, MINIMAL_SUMMARY
from research_agent.reports import ReportDraft
from research_agent.settings import Settings


class JevReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name)
        (self.root/'.env').write_text('TYPESAFE_API_KEY=test-jev-private-key\n')
        self.row = {'record_id':'page-fixture','title':'Upgrade proposal',
                    'text':'An Ethereum upgrade is proposed. It has not been executed.',
                    'published_at':'2026-11-01T00:00:00Z','retrieved_at':'2026-11-01T01:00:00Z'}
        self.draft = ReportDraft(title='Proposal brief',summary='An Ethereum upgrade is proposed.',
            observations=[{'statement':'An Ethereum upgrade is proposed.','evidence_ids':['page-fixture']}],
            limitations=['This source describes a proposal.'])
        self.sent = []

    def tearDown(self):self.temp.cleanup()

    def answer(self, body, *, support='entailed', witness=True, confidence=.9):
        answers = {}
        for key, spec in body['questions'].items():
            chosen = (support if key.endswith('_support') else
                      next((k for k in spec['criteria'] if k != 'none'),'none') if witness else 'none')
            n = len(spec['criteria'])
            probs = {k:(.9 if k == chosen else .1/(n-1)) for k in spec['criteria']}
            if n == 1:probs={chosen:1.0}
            answers[key] = {'type':'choice','choice':chosen,'confidence':confidence,'probabilities':probs}
        return {'model':MODEL,'answers':answers,'usage':{'input_tokens':120,'output_tokens':20}}

    def reviewer(self, mutate=None, **kwargs):
        def dispatch(request):
            self.assertEqual(str(request.url),'https://api.typesafe.ai/v1/systemone')
            body = json.loads(request.content);self.sent.append(body)
            response = self.answer(body)
            if mutate:response = mutate(response,body)
            if isinstance(response,httpx.Response):return response
            return httpx.Response(200,json=response)
        return JevReportReviewer(Settings(),root=self.root,transport=httpx.MockTransport(dispatch),
            diagnostic_dir=self.root/'attempts',report_user_prompt='Review Ethereum proposals in this reporting window.',**kwargs)

    def test_one_batch_preserves_task_sources_native_usage_and_exact_quotes(self):
        reviewer = self.reviewer()
        result = reviewer.review(self.draft,lambda _:self.row,process_context={'deterministic_validation':{'status':'passed'}})
        self.assertEqual(result['status'],'reviewed');self.assertEqual(reviewer.calls,1)
        self.assertEqual(len(self.sent[0]['questions']),6)
        task = self.sent[0]['state']['research_task']
        self.assertIn('reporting window',task['original_user_question'])
        self.assertEqual(task['process_context']['deterministic_validation']['status'],'passed')
        source = self.sent[0]['state']['sources']['page-fixture']
        self.assertEqual(source['text'],self.row['text'])
        self.assertEqual(source['published_at'],self.row['published_at'])
        self.assertEqual(result['request_usages'],[{'input_tokens':120,'output_tokens':20}])
        self.assertEqual(result['review_version'],'support-jev-v1')
        self.assertNotIn('scope_critic',result)
        verify_saved_jev_review(result,self.draft)
        reviewer.review(self.draft,lambda _:self.row,process_context={'deterministic_validation':{'status':'passed'}})
        self.assertEqual(reviewer.calls,1,'unchanged input reuses the exact receipt')
        self.assertEqual(reviewer.rounds,1)
        self.assertNotIn('test-jev-private-key',next((self.root/'attempts').glob('*.json')).read_text())

    def test_low_confidence_has_no_uniform_threshold(self):
        def low(response,body):
            for answer in response['answers'].values():answer['confidence']=.1
            return response
        result = self.reviewer(low).review(self.draft,lambda _:self.row)
        self.assertEqual(result['status'],'reviewed')
        self.assertIsNone(result['policy']['confidence_gate'])

    def test_complete_single_batch_preflight_leaves_all_slots_for_factual_fallback(self):
        reviewer = self.reviewer(max_requests=1)
        reviewer.require_single_batch = True
        with patch('research_agent.jev_review.MAX_TARGETS',1):
            with self.assertRaisesRegex(ValueError,'one complete public Jev batch'):
                reviewer.review(self.draft,lambda _:self.row)
        self.assertEqual(reviewer.calls,0)
        self.assertFalse(self.sent)

    def test_default_transport_preserves_existing_network_configuration(self):
        real_client=httpx.Client
        def dispatch(request):
            return httpx.Response(200,json=self.answer(json.loads(request.content)))
        def configured_client(**kwargs):
            self.assertIsNone(kwargs['transport'])
            self.assertTrue(kwargs['trust_env'])
            kwargs['transport']=httpx.MockTransport(dispatch)
            return real_client(**kwargs)
        reviewer=JevReportReviewer(Settings(),root=self.root)
        with patch('research_agent.jev_review.httpx.Client',side_effect=configured_client):
            self.assertEqual(reviewer.review(self.draft,lambda _:self.row)['status'],'reviewed')

    def test_no_witness_is_not_approval(self):
        def none(response,body):return self.answer(body,witness=False)
        result = self.reviewer(none).review(self.draft,lambda _:self.row)
        self.assertEqual(result['status'],'needs_revision')
        self.assertTrue(all(v['status']=='insufficient' for v in result['verdicts']))

    def test_contradiction_and_needs_context_never_pass(self):
        for status in ('contradicted','insufficient_text','needs_context'):
            with self.subTest(status=status):
                result = self.reviewer(lambda _,b:self.answer(b,support=status)).review(self.draft,lambda _:self.row)
                self.assertEqual(result['status'],'needs_revision')
                self.assertTrue(all(v['status']!='supported' for v in result['verdicts']))

    def test_tie_is_explicit_abstention(self):
        def tie(response,body):
            for key,value in response['answers'].items():
                if key.endswith('_support'):value.update(probabilities={k:.25 for k in value['probabilities']})
            return response
        result = self.reviewer(tie).review(self.draft,lambda _:self.row)
        self.assertEqual(result['status'],'needs_revision')
        self.assertIn('tied',result['verdicts'][0]['reason'])

    def test_near_uniform_support_is_approval_abstention_not_confidence_gate(self):
        def uncertain(response,body):
            for key,value in response['answers'].items():
                if key.endswith('_support'):
                    value.update(confidence=.99,probabilities={'entailed':.26,'contradicted':.25,'insufficient_text':.25,'needs_context':.24})
            return response
        result = self.reviewer(uncertain).review(self.draft,lambda _:self.row)
        self.assertEqual(result['status'],'needs_revision')
        self.assertIn('distribution',result['verdicts'][0]['reason'])

    def test_missing_unknown_or_drift_invalidates_batch_without_fallback(self):
        def missing(r,b):r['answers'].pop(next(iter(r['answers'])));return r
        def extra(r,b):r['answers']['fake']=next(iter(r['answers'].values()));return r
        def drift(r,b):r['model']='jev-1.13.0';return r
        for mutate in (missing,extra,drift):
            with self.subTest(mutate=mutate.__name__):
                reviewer = self.reviewer(mutate)
                result = reviewer.review(self.draft,lambda _:self.row)
                self.assertEqual(result['status'],'needs_revision')
                self.assertEqual(reviewer.calls,1)
                self.assertEqual(result['batches'][0]['status'],'invalid_or_failed')

    def test_failed_http_is_one_attempt_and_unapproved(self):
        reviewer = self.reviewer(lambda r,b:httpx.Response(500,json={'error':'failed'}))
        result = reviewer.review(self.draft,lambda _:self.row)
        self.assertEqual(reviewer.calls,1);self.assertEqual(result['status'],'needs_revision')
        self.assertEqual(result['batches'][0]['sdk_max_retries'],0)

    def test_budget_retains_partial_valid_results_and_marks_others_unassessed(self):
        with patch('research_agent.jev_review.MAX_TARGETS',1):
            reviewer = self.reviewer(max_requests=1)
            result = reviewer.review(self.draft,lambda _:self.row)
            self.assertEqual(reviewer.calls,1);self.assertEqual(len(self.sent),1)
            self.assertEqual(result['status'],'needs_revision')
            self.assertEqual(result['verdicts'][0]['status'],'supported')
            self.assertEqual(len(result['unassessed_targets']),2)
            verify_saved_jev_review(result,self.draft)

    def test_draft_round_limit_is_independent_of_batch_request_limit(self):
        reviewer = self.reviewer(max_rounds=1,max_requests=32)
        first = reviewer.review(self.draft,lambda _:self.row)
        self.assertEqual(first['status'],'reviewed')
        altered = self.draft.model_copy(update={'title':'Another draft title'})
        second = reviewer.review(altered,lambda _:self.row)
        self.assertEqual(reviewer.rounds,1);self.assertEqual(reviewer.calls,1)
        self.assertEqual(second['status'],'needs_revision')
        self.assertEqual(second['request_budget']['max_rounds'],1)
        self.assertTrue(all('draft-round' in t['reason'] for t in second['unassessed_targets']))
        verify_saved_jev_review(second,altered)

    def test_default_two_draft_budget_leaves_unassessed_third_for_final_delivery(self):
        reviewer=self.reviewer()
        self.assertEqual(reviewer.max_requests,16)
        for title in ('First bounded draft','Second bounded draft'):
            draft=self.draft.model_copy(update={'title':title})
            self.assertEqual(reviewer.review(draft,lambda _:self.row)['status'],'reviewed')
        third=self.draft.model_copy(update={'title':'Third draft'})
        result=reviewer.review(third,lambda _:self.row)
        self.assertEqual(reviewer.calls,2);self.assertEqual(reviewer.rounds,2)
        self.assertEqual(result['status'],'needs_revision')
        self.assertTrue(all('draft-round' in target['reason'] for target in result['unassessed_targets']))
        verify_saved_jev_review(result,third)

    def test_oversized_context_is_visible_not_silently_clipped(self):
        reviewer = self.reviewer();reviewer.report_user_prompt='large original question '*8000
        result = reviewer.review(self.draft,lambda _:self.row)
        self.assertEqual(reviewer.calls,0);self.assertEqual(result['status'],'needs_revision')
        self.assertTrue(all(i['reason']=='oversized_context_not_dispatched' for i in result['unassessed_targets']))

    def test_private_input_does_not_dispatch_or_call_another_provider(self):
        reviewer = self.reviewer()
        result = reviewer.review(self.draft,lambda _:{**self.row,'private':True})
        self.assertEqual(reviewer.calls,0);self.assertEqual(self.sent,[])
        self.assertEqual(result['status'],'needs_revision')

    def test_private_task_and_process_never_leave_even_with_public_sources(self):
        for question,context in [('I hold 2 ETH; should I buy more?',None),('请研究ETH，我的预算是1000美元',None),
                                 ('Review Ethereum proposals.',{'note':'My personal wallet contains 3 ETH.'})]:
            with self.subTest(question=question):
                reviewer = self.reviewer();reviewer.report_user_prompt=question
                result = reviewer.review(self.draft,lambda _:self.row,process_context=context)
                self.assertEqual(reviewer.calls,0);self.assertEqual(result['status'],'needs_revision')

    def test_public_institutional_amount_is_not_personal_context(self):
        reviewer = self.reviewer();reviewer.report_user_prompt='Review BitMine buying 100 ETH for its corporate treasury portfolio.'
        result = reviewer.review(self.draft,lambda _:self.row)
        self.assertEqual(reviewer.calls,1);self.assertEqual(result['status'],'reviewed')

    def test_credential_bearing_task_is_not_dispatched_or_retained(self):
        reviewer = self.reviewer();reviewer.report_user_prompt='My key is test-jev-private-key'
        with self.assertRaisesRegex(ValueError,'Credential-bearing'):
            reviewer.review(self.draft,lambda _:self.row)
        self.assertEqual(reviewer.calls,0)
        self.assertFalse((self.root/'attempts').exists())

    def test_minimal_literal_process_fields_are_exactly_controlled(self):
        minimal = self.draft.model_copy(update={'delivery_kind':'minimal','summary':MINIMAL_SUMMARY,'limitations':[MINIMAL_LIMIT]})
        reviewer = self.reviewer()
        result = reviewer.review(minimal,lambda _:self.row)
        self.assertEqual(result['status'],'reviewed')
        self.assertEqual(len(self.sent[0]['state']['targets']),1)
        self.assertEqual(len(result['host_literal_target_ids']),3)
        tampered = minimal.model_copy(update={'summary':MINIMAL_SUMMARY+' Ethereum doubled in price.'})
        with self.assertRaises(ValueError):verify_saved_jev_review(result,tampered)

    def test_receipt_changes_and_omitted_target_are_rejected(self):
        result = self.reviewer().review(self.draft,lambda _:self.row)
        changed = copy.deepcopy(result)
        changed['batches'][0]['raw_response']['answers']['C1_support']['choice']='contradicted'
        with self.assertRaises(ValueError):verify_saved_jev_review(changed,self.draft)
        changed = copy.deepcopy(result);changed['batches']=[]
        with self.assertRaises(ValueError):verify_saved_jev_review(changed,self.draft)
        changed = copy.deepcopy(result);changed['summary_context']['text']='A different premise.'
        with self.assertRaises(ValueError):verify_saved_jev_review(changed,self.draft)
        changed = copy.deepcopy(result);changed['request_budget']['sdk_max_retries']=2
        with self.assertRaises(ValueError):verify_saved_jev_review(changed,self.draft)


if __name__ == '__main__':unittest.main()
