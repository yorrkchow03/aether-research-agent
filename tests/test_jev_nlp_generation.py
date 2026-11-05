"""Fixed-generator execution controls, entirely mocked and offline."""
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

import httpx

from research_agent.jev_nlp_generation import SYSTEM, generate
from research_agent.settings import PLAN_BASE_URL, Settings


class MockGenerator:
    """Exercise the physical-request observer through an in-memory transport."""
    def __init__(self, answer='The proposal remains pending. [page-a]', error=None, usage=True):
        self.answer, self.error = answer, error
        self.response_model = 'doubao-seed-2-1-pro-260915'
        self.finish_reason = 'stop'
        self.usage = {'input_tokens': 100, 'output_tokens': 12, 'total_tokens': 112} if usage else None
        self.options = []
        self.messages = []
        self.http_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
        self.root_client = SimpleNamespace(chat=SimpleNamespace(completions=object()), with_options=self.with_options)
        self.client = None

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self.root_client

    def invoke(self, messages, **kwargs):
        self.messages.append((messages, kwargs))
        self.http_client.post(PLAN_BASE_URL+'/chat/completions', json={'mock': True})
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.answer,
                               response_metadata={'finish_reason': self.finish_reason, 'model_name': self.response_model},
                               usage_metadata=self.usage)


class FixedGeneratorTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root/'study'
        self.output.mkdir()
        self.protocol = {
            'order': [{'provider': 'jev', 'job_id': 'first'}, {'provider': 'doubao', 'job_id': 'first'}],
            'fixed_generator_context_ids': ['c1'], 'fixed_generator_arms': ['lexical'],
            'maximum_generator_requests': 24, 'primary_context_budget_chars': 12000,
        }
        self.data = {'context_sets': [{'context_id': 'c1', 'question': 'What is the proposal status?'}]}
        self.analysis = {'context_selections': []}
        self.settings = Settings(model_mode='ark_plan', api_key='fixture-secret')
        self.models = []
        self.stops = {}
        self.first = {'jev': {'first': {'status': 'ok'}}, 'doubao': {'first': {'status': 'ok'}}}
        self.add_selection('c1', 'lexical')

    def add_selection(self, cid, arm, *, empty=False):
        self.analysis['context_selections'].append({
            'context_id': cid, 'arm': 'rules' if arm == 'lexical' else arm,
            'variant': 'lexical' if arm == 'lexical' else 'semantic_graph',
            'budget_chars': 12000,
            'selection': {'selected_ids': [] if empty else ['page-a'],
                          'context': '' if empty else '{"id":"page-a","text":"The proposal remains pending."}',
                          'status': 'empty' if empty else 'packed'},
        })

    def run_generation(self, factory=None):
        (self.output/'ANALYSIS.json').write_text(json.dumps(self.analysis))
        def default_factory(settings):
            self.assertEqual(settings.model_id, 'doubao-seed-2.1-pro')
            self.assertEqual(settings.base_url, PLAN_BASE_URL)
            self.assertEqual(settings.thinking, 'disabled')
            model = MockGenerator()
            self.models.append(model)
            return model
        with ExitStack() as stack:
            for name, value in (
                ('load', (self.protocol, self.data)), ('_calibration', {}),
                ('_rows', self.first), ('_circuit_state', (Counter(), self.stops)),
            ):
                stack.enter_context(patch('research_agent.jev_nlp_generation.'+name, return_value=value))
            stack.enter_context(patch('research_agent.jev_nlp_generation.Settings.load', return_value=self.settings))
            build = stack.enter_context(patch('research_agent.jev_nlp_generation.create_ark_model', side_effect=factory or default_factory))
            result = generate(self.root, self.root/'unused-config', self.output)
        return result, build

    def saved(self, arm='lexical', cid='c1'):
        return json.loads((self.output/'generation'/(cid+'-'+arm+'.json')).read_text())

    def test_fixed_pro_plan_prompt_transport_and_citation_identity(self):
        summary, build = self.run_generation()
        self.assertEqual(build.call_count, 1)
        self.assertEqual(summary['generated_answers'], 1)
        result = self.saved()['result']
        self.assertEqual(result['physical_request_count'], 1)
        self.assertTrue(result['mechanical_citation_check'])
        self.assertEqual(result['selected_ids'], ['page-a'])
        model = self.models[0]
        self.assertEqual(model.options, [{'timeout': 45, 'max_retries': 0}])
        messages, kwargs = model.messages[0]
        self.assertEqual(kwargs, {'stream': False})
        self.assertEqual(messages[0].content, SYSTEM)
        self.assertIn('at most 200 English words', SYSTEM)
        self.assertEqual(json.loads(messages[1].content)['source_context'], self.saved()['source_context'])
        self.assertTrue(model.http_client.is_closed)

    def test_wrong_requested_model_or_nonplan_endpoint_never_builds_a_generator(self):
        for settings in (Settings(model_mode='ark_plan', model_id='other-model'),
                         Settings(model_mode='ark_plan', base_url='https://invalid.example/v1')):
            self.settings = settings
            factory = Mock(side_effect=AssertionError('Invalid settings must not create model'))
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.run_generation(factory)
            factory.assert_not_called()

    def test_unknown_citation_is_retained_as_mechanical_failure(self):
        model = MockGenerator(answer='The proposal executed. [page-unseen]')
        _, build = self.run_generation(lambda settings: model)
        self.assertEqual(build.call_count, 1)
        result = self.saved()['result']
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['mechanical_citation_check'])
        self.assertEqual(result['unresolved_citation_spans'], ['page-unseen'])
        self.assertTrue(model.http_client.is_closed)

    def test_empty_context_makes_no_request(self):
        self.analysis['context_selections'].clear()
        self.add_selection('c1', 'lexical', empty=True)
        summary, build = self.run_generation()
        build.assert_not_called()
        self.assertEqual(summary['generated_answers'], 0)
        result = self.saved()['result']
        self.assertEqual(result['reason'], 'no_context_within_budget')
        self.assertFalse(result['billable_request_made'])
        self.assertEqual(result['physical_request_count'], 0)

    def test_interrupted_marker_prevents_resubmission_and_preserves_unknown_accounting(self):
        marker = self.output/'generation_started/c1-lexical.json'
        marker.parent.mkdir()
        marker.write_text(json.dumps({'started_at': '2026-11-05T00:00:00Z'}))
        summary, build = self.run_generation()
        build.assert_not_called()
        result = self.saved()['result']
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['reason'], 'interrupted_generation_unknown_no_resubmission')
        self.assertTrue(result['billable_request_made'])
        self.assertIsNone(result['physical_request_count'])
        self.assertIsNone(result['usage'])
        self.assertEqual(summary['accounting']['unknown_physical_counts'], 1)
        self.assertEqual(summary['accounting']['incomplete_cost_responses'], 1)

    def test_saved_first_receipt_is_reused_without_another_model_request(self):
        first, _ = self.run_generation()
        path = self.output/'generation/c1-lexical.json'
        original_bytes = path.read_bytes()
        second, build = self.run_generation(Mock(side_effect=AssertionError('No resubmission')))
        build.assert_not_called()
        self.assertEqual(first['generated_answers'], second['generated_answers'])
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_changed_analysis_pin_refuses_to_mix_generation_contexts(self):
        self.run_generation()
        self.analysis['context_selections'][0]['selection']['context'] = 'A newly altered source view.'
        factory = Mock(side_effect=AssertionError('Frozen generation contexts changed'))
        with self.assertRaises(ValueError):
            self.run_generation(factory)
        factory.assert_not_called()

    def test_wrong_observed_model_is_rejected_without_fallback(self):
        model = MockGenerator()
        model.response_model = 'unrelated-fallback-model'
        _, build = self.run_generation(lambda settings: model)
        self.assertEqual(build.call_count, 1)
        result = self.saved()['result']
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['answer'])
        self.assertTrue(model.http_client.is_closed)

    def test_truncated_response_preserves_reported_usage_and_closes_client(self):
        model = MockGenerator()
        model.finish_reason = 'length'
        _, build = self.run_generation(lambda settings: model)
        self.assertEqual(build.call_count, 1)
        result = self.saved()['result']
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['usage'], model.usage)
        self.assertEqual(result['response_model'], model.response_model)
        self.assertEqual(result['physical_request_count'], 1)
        self.assertTrue(result['billing']['complete_usage'])
        self.assertTrue(model.http_client.is_closed)

    def test_three_consecutive_failures_stop_remaining_arms_without_fallback(self):
        self.protocol['fixed_generator_arms'] = ['lexical', 'doubao', 'jev', 'task_selective']
        for arm in self.protocol['fixed_generator_arms'][1:]:
            self.add_selection('c1', arm)
        def fail(settings):
            model = MockGenerator(error=RuntimeError('fixture-secret provider failed'))
            self.models.append(model)
            return model
        summary, build = self.run_generation(fail)
        self.assertEqual(build.call_count, 3)
        self.assertEqual(summary['completed_slots'], 4)
        self.assertEqual(self.saved('task_selective')['result']['reason'], 'provider_circuit_stop')
        self.assertTrue(all(model.http_client.is_closed for model in self.models))
        for arm in ('lexical', 'doubao', 'jev'):
            result = self.saved(arm)['result']
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('fixture-secret', result['error'])
        # Simulate a crash after the third error receipt, before writing the
        # remaining stopped slot. Reconstructing saved failures must still stop.
        (self.output/'generation/c1-task_selective.json').unlink()
        _, replay = self.run_generation(Mock(side_effect=AssertionError('Stopped receipts cannot retry')))
        replay.assert_not_called()
        self.assertEqual(self.saved('task_selective')['result']['reason'], 'provider_circuit_stop')

    def test_existing_primary_doubao_circuit_stop_prevents_generation(self):
        self.stops = {'doubao': 'consecutive_provider_errors'}
        _, build = self.run_generation()
        build.assert_not_called()
        self.assertEqual(self.saved()['result']['reason'], 'provider_circuit_stop')

    def test_usage_omission_stays_unknown_after_observed_physical_call(self):
        model = MockGenerator(usage=False)
        summary, _ = self.run_generation(lambda settings: model)
        result = self.saved()['result']
        self.assertEqual(result['physical_request_count'], 1)
        self.assertIsNone(result['usage'])
        self.assertIsNone(result['billing']['estimated_afp'])
        self.assertEqual(summary['accounting']['unknown_input_usage_responses'], 1)

    def test_incomplete_primary_collection_prevents_generation(self):
        self.first['jev'].clear()
        factory = Mock(side_effect=AssertionError('Primary collection is incomplete'))
        with self.assertRaises(ValueError):
            self.run_generation(factory)
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
