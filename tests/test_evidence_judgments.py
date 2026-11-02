import json
import hashlib
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from research_agent.evidence_judgments import EvidenceJudgmentService, QUESTIONS, normalize_pro_answers


class EvidenceJudgmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.env').write_text('TYPESAFE_API_KEY=typesafe-fixture-secret\nARK_API_KEY=ark-fixture-secret\n')
        self.source = {'record_id': 'page-' + 'a' * 24, 'text': 'The Ethereum Hoodi upgrade is postponed. No replacement date is set.',
                       'published_at': '2026-10-22T00:00:00Z', 'retrieved_at': '2026-10-27T00:00:00Z',
                       'source': {'url': 'https://example.test/update', 'sha256': 'a' * 64}}

    def response(self, body, selected=None, confidence=.95):
        selected = selected or {}
        answers = {}
        for name, spec in body['questions'].items():
            label = selected.get(name, next(iter(spec['criteria'])))
            alternatives = len(spec['criteria']) - 1
            answers[name] = {'type': 'choice', 'choice': label, 'confidence': confidence,
                'probabilities': {k: .9 if k == label else .1 / alternatives for k in spec['criteria']}}
        return {'model': 'jev-1.14.0', 'answers': answers, 'usage': {'input_tokens': 100, 'output_tokens': 20}}

    def service(self, handler, **kwargs):
        return EvidenceJudgmentService(self.root, transport=httpx.MockTransport(handler), **kwargs)

    def test_multi_question_request_cache_and_no_credential_receipt(self):
        seen = []
        def handler(request):
            seen.append(request)
            self.assertEqual(request.headers['Authorization'], 'Bearer typesafe-fixture-secret')
            body = json.loads(request.content)
            self.assertEqual(set(body['questions']), {'asset', 'topic', 'event_status'})
            return httpx.Response(200, json=self.response(body, {'asset': 'ETH', 'topic': 'network_upgrade', 'event_status': 'delayed'}))
        service = self.service(handler)
        result = service.assess('classification', evidence=[self.source])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['model'], 'jev-1.14.0')
        self.assertIn('event_status:delayed', result['labels'])
        self.assertNotEqual(result['input_hash'], self.source['source']['sha256'])
        second = service.assess('classification', evidence=[self.source])
        self.assertEqual(len(seen), 1)
        self.assertTrue(second['cache_hit'])
        self.assertFalse(second['billable_request_made'])
        self.assertEqual(second['usage']['input_tokens'], 0)
        self.assertEqual(second['original_usage']['input_tokens'], 100)
        serialized = json.dumps(service.receipt(result['cache_key']))
        self.assertNotIn('typesafe-fixture-secret', serialized)
        self.assertNotIn('ark-fixture-secret', serialized)

    def test_question_excerpt_source_hash_and_criterion_invalidate_cache(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=self.response(json.loads(request.content)))
        service = self.service(handler)
        base = service.assess('support', question='It was postponed.', evidence=[self.source])
        variants = [dict(question='It activated.', evidence=[self.source]),
            dict(question='It was postponed.', evidence=[{**self.source, 'text': 'A newly corrected excerpt.'}]),
            dict(question='It was postponed.', evidence=[{**self.source, 'source': {**self.source['source'], 'sha256': 'b'*64}}]),
            dict(question='It was postponed.', evidence=[self.source], criterion_version='v2')]
        for values in variants:
            result = service.assess('support', **values)
            self.assertNotEqual(base['input_hash'], result['input_hash'])
        self.assertEqual(len(seen), 5)

    def test_uncertain_or_low_confidence_is_not_a_negative_verdict(self):
        service = self.service(lambda r: httpx.Response(200, json=self.response(json.loads(r.content), {'support': 'insufficient'}, .3)))
        result = service.assess('support', question='Activation occurred.', evidence=[self.source])
        self.assertEqual(result['status'], 'abstained')
        self.assertIsNone(result['label'])
        self.assertEqual(result['dimensions']['support']['choice'], 'insufficient')

    def test_private_context_and_exact_credential_input_never_sent(self):
        service = self.service(lambda r: self.fail('No network allowed'))
        for row in [{**self.source, 'record_id': 'context-'+'b'*24},
                    {**self.source, 'dataset_id': 'local.private_portfolio'},
                    {**self.source, 'data_category': 'private_portfolio'},
                    {**self.source, 'text': 'typesafe-fixture-secret'}]:
            with self.assertRaises(ValueError):
                service.assess('support', evidence=[row])
        self.assertEqual(service.assess('support', evidence=[])['status'], 'abstained')
        self.assertEqual(service.assess('relation', evidence=[self.source])['status'], 'abstained')

    def test_unconfigured_and_offline_cache_miss_do_not_call_provider(self):
        (self.root / '.env').write_text('')
        service = self.service(lambda r: self.fail('No provider request'))
        self.assertEqual(service.assess('support', evidence=[self.source])['status'], 'unavailable')
        offline = self.service(lambda r: self.fail('Offline cache miss'), cache_only=True)
        self.assertEqual(offline.assess('support', evidence=[self.source])['status'], 'unavailable')

    def test_invalid_response_or_http_error_preserves_failure_not_fake_label(self):
        service = self.service(lambda r: httpx.Response(200, json={'model': 'not-jev', 'answers': {}}))
        result = service.assess('support', evidence=[self.source])
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['label'])
        self.assertEqual(service.receipt(result['cache_key'])['result']['status'], 'error')
        service = self.service(lambda r: httpx.Response(403, json={'error': 'denied'}))
        result = service.assess('support', evidence=[self.source])
        self.assertEqual(result['status'], 'error')
        self.assertIn('403', result['error'])
        self.assertEqual(result['request_count'], 1)
        self.assertIsNone(result['usage'])

    def test_retry_is_bounded_and_response_choices_validated(self):
        seen = []
        def handler(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=self.response(json.loads(request.content)))
        with patch('research_agent.evidence_judgments.time.sleep'):
            result = self.service(handler).assess('support', evidence=[self.source])
        self.assertEqual(result['request_count'], 2)
        self.assertEqual(len(seen), 2)

    def test_transport_bounds_validate_before_provider_or_storage(self):
        for invalid in (True, 0, -1, 121, float('nan'), float('inf'), '12'):
            with self.subTest(timeout=invalid), self.assertRaises(ValueError):
                EvidenceJudgmentService(self.root, timeout_seconds=invalid)
        for invalid in (True, 0, -1, 4, 1.5, '1'):
            with self.subTest(attempts=invalid), self.assertRaises(ValueError):
                EvidenceJudgmentService(self.root, max_attempts=invalid)

    def test_short_shadow_transport_single_http_failure_has_status_and_policy(self):
        seen = []
        def handler(request):
            seen.append(request)
            self.assertEqual(request.extensions['timeout']['read'], 12)
            return httpx.Response(402, json={'error': 'payment required'})
        service = self.service(handler, timeout_seconds=12, max_attempts=1)
        result = service.assess('support', evidence=[self.source])
        self.assertEqual(len(seen), 1)
        self.assertEqual(result['error_type'], 'HTTPStatusError')
        self.assertEqual(result['http_status'], 402)
        self.assertFalse(result['timed_out'])
        self.assertEqual(result['transport_policy']['max_attempts'], 1)
        receipt = service.receipt(result['cache_key'])
        self.assertEqual(receipt['transport_attempts'][0]['timeout_seconds'], 12)
        self.assertIsNone(result['usage'])

    def test_timeout_preserves_error_type_and_does_not_retry_or_cache_success(self):
        seen = []
        def handler(request):
            seen.append(request)
            raise httpx.ReadTimeout('fixture timeout', request=request)
        service = self.service(handler, timeout_seconds=12, max_attempts=1)
        result = service.assess('support', evidence=[self.source])
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error_type'], 'ReadTimeout')
        self.assertTrue(result['timed_out'])
        self.assertIsNone(result['http_status'])
        self.assertIsNone(result['usage'])
        self.assertTrue(service.receipt(result['cache_key'])['transport_attempts'][0]['timed_out'])
        replay = self.service(lambda r: self.fail('offline'), cache_only=True)
        self.assertEqual(replay.assess('support', evidence=[self.source])['status'], 'unavailable')
        self.assertEqual(len(seen), 1)

    def test_single_attempt_suppresses_retry_and_policy_does_not_invalidate_success_cache(self):
        seen = []
        def unavailable(request):
            seen.append(request)
            return httpx.Response(429)
        with patch('research_agent.evidence_judgments.time.sleep') as sleep:
            result = self.service(unavailable, timeout_seconds=12, max_attempts=1).assess('support', evidence=[self.source])
        self.assertEqual(result['request_count'], 1)
        sleep.assert_not_called()
        service = self.service(lambda r: httpx.Response(200, json=self.response(json.loads(r.content))))
        original = service.assess('support', evidence=[self.source])
        short = self.service(lambda r: self.fail('cached judgment must not call'), timeout_seconds=12, max_attempts=1)
        cached = short.assess('support', evidence=[self.source])
        self.assertTrue(cached['cache_hit'])
        self.assertEqual(original['cache_key'], cached['cache_key'])
        self.assertEqual(cached['original_transport_policy']['timeout_seconds'], 45)
        self.assertEqual(cached['transport_policy']['timeout_seconds'], 12)

    def test_explicit_pro_transport_options_reconfigure_client_without_changing_semantics(self):
        options = []
        completions = object()
        class Client:
            chat = SimpleNamespace(completions=completions)
            def with_options(self, **kwargs):
                options.append(kwargs)
                return self
        def invoke(messages, **kwargs):
            self.assertIs(kwargs['stream'],False)
            payload = self.response(json.loads(messages[1].content))
            payload.pop('model'); payload.pop('usage')
            return SimpleNamespace(content=json.dumps(payload), response_metadata={'model_name': 'doubao-seed-2.1-pro', 'finish_reason': 'stop'}, usage_metadata=None)
        model = SimpleNamespace(root_client=Client(), client=None, invoke=invoke,
                                http_client=SimpleNamespace(close=lambda: None))
        with patch('research_agent.evidence_judgments.create_ark_model', return_value=model):
            result = EvidenceJudgmentService(self.root, provider='doubao', timeout_seconds=12, max_attempts=1).assess('support', evidence=[self.source])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(options, [{'timeout': 12, 'max_retries': 0}])
        self.assertIs(model.client, completions)
        self.assertIsNone(result['usage'])

    def test_confidence_override_validates_before_any_storage_or_request(self):
        for threshold in (True,-.1,1.1,float('nan'),float('inf'),'0.8'):
            with self.subTest(threshold=threshold),self.assertRaises(ValueError):
                EvidenceJudgmentService(self.root,minimum_confidence=threshold)

    def test_pro_physical_http_counter_does_not_mistake_wrapper_for_retry_count(self):
        client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})))
        def invoke(messages,**kwargs):
            for _ in range(2):client.post('https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions',json={})
            payload=self.response(json.loads(messages[1].content));payload.pop('model');payload.pop('usage')
            return SimpleNamespace(content=json.dumps(payload),response_metadata={'model_name':'doubao-seed-2.1-pro','finish_reason':'stop'},usage_metadata={'input_tokens':100,'output_tokens':10})
        model=SimpleNamespace(invoke=invoke,http_client=client)
        with patch('research_agent.evidence_judgments.create_ark_model',return_value=model):
            service=EvidenceJudgmentService(self.root,provider='doubao')
            result=service.assess('support',evidence=[self.source])
        self.assertEqual(result['request_count'],1);self.assertEqual(result['physical_request_count'],2)
        self.assertFalse(result['billing']['complete_usage'])
        self.assertEqual(len(service.receipt(result['cache_key'])['physical_http_attempts']),2)

    def test_alias_expiry_keeps_offline_replay_available(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=self.response(json.loads(request.content)))
        service = self.service(handler, cache_ttl_seconds=0)
        result = service.assess('support', evidence=[self.source])
        replay = self.service(lambda r: self.fail('Offline'), cache_only=True, cache_ttl_seconds=0)
        cached = replay.assess('support', evidence=[self.source])
        self.assertTrue(cached['cache_hit'])
        self.assertTrue(cached['cache_stale'])
        self.assertEqual(len(seen), 1)
        service.assess('support', evidence=[self.source])
        self.assertEqual(len(seen), 2)

    def test_literal_word_relevance_and_nonsemantic_abstention(self):
        service = EvidenceJudgmentService(self.root, provider='lexical')
        result = service.assess('relevance', question='ETH', evidence=[{**self.source, 'text': 'Whether the issue is settled remains unknown.'}])
        self.assertEqual(result['label'], 'uncertain')
        self.assertIsNone(result['confidence'])
        self.assertEqual(service.assess('support', evidence=[self.source])['status'], 'abstained')

    def test_doubao_has_same_questions_without_typesafe_key_and_records_actual_model(self):
        def invoke(messages, **kwargs):
            request = json.loads(messages[1].content)
            payload = self.response(request, {'support': 'contradicted'})
            payload.pop('model'); payload.pop('usage')
            return SimpleNamespace(content=json.dumps(payload), response_metadata={'model_name': 'doubao-seed-2.1-pro', 'finish_reason': 'stop'}, usage_metadata={'input_tokens': 200, 'output_tokens': 40})
        model = SimpleNamespace(invoke=invoke, http_client=SimpleNamespace(close=lambda: None))
        with patch('research_agent.evidence_judgments.create_ark_model', return_value=model):
            result = EvidenceJudgmentService(self.root, provider='doubao').assess('support', question='It activated.', evidence=[self.source])
        self.assertEqual(result['model'], 'doubao-seed-2.1-pro')
        self.assertEqual(result['label'], 'contradicted')
        self.assertEqual(result['usage']['input_tokens'], 200)

    def test_truncated_doubao_payload_is_retained_but_not_a_judgment(self):
        model = SimpleNamespace(invoke=lambda *a, **k: SimpleNamespace(content='{"answers":',
            response_metadata={'model_name': 'doubao-seed-2.1-pro', 'finish_reason': 'length'},
            usage_metadata={'input_tokens': 100, 'output_tokens': 4096}), http_client=SimpleNamespace(close=lambda: None))
        with patch('research_agent.evidence_judgments.create_ark_model', return_value=model):
            service = EvidenceJudgmentService(self.root, provider='doubao')
            result = service.assess('support', evidence=[self.source])
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['label'])
        self.assertEqual(result['usage']['output_tokens'], 4096)
        self.assertIn('unparsed_content', service.receipt(result['cache_key'])['response'])

    def test_pro_bare_question_map_is_wrapped_without_guessing_missing_scores(self):
        questions = QUESTIONS['support']
        raw = {'support': {'choice': 'contradicted', 'probabilities':
            {'supported': .01, 'contradicted': .95, 'insufficient': .03, 'conditional': .01}, 'confidence': .9}}
        normalized, metadata = normalize_pro_answers(raw, questions)
        self.assertEqual(normalized['answers']['support']['choice'], raw['support']['choice'])
        self.assertEqual(normalized['answers']['support']['probabilities'], raw['support']['probabilities'])
        self.assertFalse(metadata['semantic_values_changed'])
        self.assertEqual(metadata['constant_type_inserted'], ['support'])
        with self.assertRaises(ValueError):
            normalize_pro_answers({'support': {'choice': 'supported'}}, questions)
        with self.assertRaises(ValueError):
            normalize_pro_answers({**raw, 'unrequested': {}}, questions)

    def test_pro_self_reported_choice_probability_inconsistency_remains_error(self):
        payload = {'answers': {'support': {'choice': 'supported', 'confidence': .9,
            'probabilities': {'supported': .1, 'contradicted': .8, 'insufficient': .05, 'conditional': .05}}}}
        model = SimpleNamespace(invoke=lambda *a, **k: SimpleNamespace(content=json.dumps(payload),
            response_metadata={'model_name': 'doubao-seed-2.1-pro', 'finish_reason': 'stop'},
            usage_metadata=None), http_client=SimpleNamespace(close=lambda: None))
        with patch('research_agent.evidence_judgments.create_ark_model',return_value=model):
            result=EvidenceJudgmentService(self.root,provider='doubao').assess('support',evidence=[self.source])
        self.assertEqual(result['status'],'error')
        self.assertIsNone(result['label'])
        self.assertEqual(result['model'],'doubao-seed-2.1-pro')
        self.assertIsNone(result['usage'])

    def test_document_sidecar_inventory_no_calls_and_resume_skips_completed(self):
        namespace=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/evaluation/classify_jev_documents.py'))
        process=namespace['process'];calls=[]
        rows=[{'record_id':'page-a','url':'https://example.test/a'},{'record_id':'page-b','url':'https://example.test/b'}]
        class Library:
            def __init__(self,*a,**k):pass
            def search_documents(self,*a,offset=0,**k):return {'total':2,'results':rows if offset==0 else []}
            def evidence(self,rid):return {'record_id':rid,'text':'public'}
        class Service:
            requested_model='jev-test'
            def __init__(self,*a,**k):pass
            def assess(self,task,*,evidence,**k):
                rid=evidence[0]['record_id'];calls.append(rid)
                return {'status':'ok','cache_key':rid,'model':'jev-test','input_hash':rid,'cache_hit':False}
        with patch.dict(process.__globals__,{'ResearchLibrary':Library,'EvidenceJudgmentService':Service}):
            output=self.root/'sidecar'
            self.assertEqual(process('fixture',output,limit=1)['mode'],'inventory_only')
            self.assertEqual(calls,[])
            self.assertEqual(process('fixture',output,run=True,limit=1)['successful_or_abstained_cursor_items'],1)
            self.assertEqual(process('fixture',output,run=True,limit=1)['successful_or_abstained_cursor_items'],2)
            self.assertEqual(process('fixture',output,run=True,limit=1)['batch_items'],[])
            self.assertEqual(calls,['page-a','page-b'])

    def test_fixed_document_sample_resumes_and_access_failure_stops_after_one_call(self):
        namespace=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/evaluation/classify_jev_documents.py'))
        process=namespace['process'];calls=[]
        sample={'library':str((self.root/'library').resolve()),'library_original_documents_at_selection':42,
                'selected':[{'record_id':rid,'text_sha256':hashlib.sha256(b'public').hexdigest(),'source_sha256':'abc'}
                            for rid in ('page-a','page-b')]}
        path=self.root/'fixed.json';path.write_text(json.dumps(sample))
        class Library:
            def __init__(self,*a,**k):pass
            def search_documents(self,*a,**k):raise AssertionError('Fixed sample must not rediscover changing corpus')
            def evidence(self,rid):return {'record_id':rid,'text':'public','source':{'sha256':'abc'}}
        class Service:
            requested_model='jev-test'
            def __init__(self,*a,**k):pass
            def assess(self,task,*,evidence,**k):
                calls.append(evidence[0]['record_id'])
                return {'status':'error','http_status':402,'cache_key':'x','model':None,'cache_hit':False,
                        'billable_request_made':True,'usage':None}
        with patch.dict(process.__globals__,{'ResearchLibrary':Library,'EvidenceJudgmentService':Service}):
            inventory=process(self.root/'library',self.root/'sample-queue',sample_file=path,limit=2)
            self.assertEqual(calls,[])
            self.assertEqual(inventory['library_original_documents'],42)
            result=process(self.root/'library',self.root/'sample-queue',sample_file=path,run=True,limit=2)
            self.assertEqual(calls,['page-a'])
            self.assertEqual(result['bounded_failures_this_batch'],1)
            self.assertEqual(result['provider_requests_this_batch'],1)
            self.assertEqual(result['usage_unknown_items'],1)

    def test_nonfinite_provider_json_is_saved_as_error_instead_of_crashing_receipt(self):
        service=self.service(lambda r:httpx.Response(200,content=b'{"model":"jev-test","answers":{"support":{"confidence":NaN}}}'))
        result=service.assess('support',evidence=[self.source])
        self.assertEqual(result['status'],'error')
        self.assertIn('Non-finite',result['error'])
        self.assertEqual(service.receipt(result['cache_key'])['result']['status'],'error')


if __name__ == '__main__':
    unittest.main()
