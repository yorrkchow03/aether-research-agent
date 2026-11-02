"""Read-only public projection: missing costs are unknown, secrets stay private."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from fixtures import project_fixture
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app
from research_agent.semantic_validation_view import load_semantic_validation, MAX_BYTES


class SemanticValidationViewTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'runtime'
        self.path = self.state / 'semantic-validation/latest.json'
        self.path.parent.mkdir(parents=True)

    def saved(self, **changes):
        result = {'schema': 'semantic-validation-public-v1',
            'created_at': '2026-10-28T10:00:00Z',
            'dataset': {'cases': 48, 'dev_cases': 16, 'confirmation_cases': 32,
                'event_families': 12, 'manifest_sha256': 'a' * 64,
                'planned_dimensions': 84, 'scored_dimensions': 70, 'ambiguous_dimensions': 14},
            'models': {'jev': 'jev-1.14.0', 'doubao': 'doubao-seed-2.1-pro'},
            'review': {'status': 'completed', 'reviewer_kind': 'independent_ai', 'reviewed_cases': 48, 'human_labels': 0},
            'providers': {'jev': {'planned': 48, 'attempted': 48, 'ok': 40, 'abstained': 6, 'errors': 2,
                'planned_dimensions': 84, 'scored_dimensions': 70, 'ambiguous_dimensions': 14,
                'ai_label_agreement': .8, 'usable_coverage': .75,
                'latency_seconds': {'median': .4, 'p90': .8, 'total': 20.0, 'count': 48},
                'usage': {'input_tokens': 96000, 'output_tokens': 2400, 'unknown_responses': 2},
                'cost': {'estimated_usd': '0.004032'},
                'splits': {'dev': {'planned': 16, 'attempted': 16, 'ai_label_agreement': .875, 'usable_coverage': .8},
                    'confirmation': {'planned': 32, 'attempted': 32, 'ai_label_agreement': .75, 'usable_coverage': .7}}}}}
        result.update(changes)
        self.path.write_text(json.dumps(result))
        return result

    def test_empty_read_does_not_invent_measurements_or_create_a_run(self):
        result = load_semantic_validation(self.state, mode='shadow', provider='jev')
        self.assertFalse(result['available']); self.assertEqual(result['reason'], 'not_run')
        self.assertNotIn('providers', result)
        self.assertEqual(len(result['tasks']), 4)
        self.assertEqual(result['current_policy']['mode'], 'shadow')
        self.assertFalse(self.path.exists())

    def test_valid_summary_preserves_partitions_and_unknown_costs(self):
        self.saved()
        result = load_semantic_validation(self.state)
        self.assertTrue(result['available'])
        self.assertEqual(result['dataset']['scored_dimensions'],70)
        self.assertEqual(result['dataset']['ambiguous_dimensions'],14)
        self.assertEqual(result['providers']['jev']['planned_dimensions'],84)
        self.assertEqual(result['providers']['jev']['splits']['confirmation']['planned'], 32)
        self.assertEqual(result['providers']['jev']['cost']['estimated_usd'], .004032)
        self.assertIsNone(result['providers']['jev']['cost']['estimated_afp'])
        self.assertIsNone(result['providers']['jev']['cost']['actual_cash_charge'])
        self.assertEqual(result['review']['human_labels'], 0)
        self.assertIn('not human', result['limitations'][0])

    def test_task_counts_and_poststudy_diagnostics_are_separate_typed_statistics(self):
        raw = self.saved()
        raw['providers']['jev']['tasks'] = {'support': {'planned': 12, 'scored_dimensions': 11,
            'planned_dimensions': 12, 'ambiguous_dimensions': 1, 'matching_dimensions': 9,
            'usable_dimensions': 8, 'usable_coverage': 8 / 11}}
        raw['repairs'] = {'jev_rounding': {'reparsed_cases': 48, 'changed_cases': 1, 'new_provider_requests': 0},
            'doubao_nonstream': {'tested_cases': 4, 'usage_responses': 4, 'estimated_afp': '1.59375'},
            'memory_excerpt': {'queries': 12, 'lexical_span_hits': 12, 'before_span_hits': 8,
                'after_span_hits': 12, 'posthoc_replay': True}}
        self.path.write_text(json.dumps(raw))
        result = load_semantic_validation(self.state)
        self.assertEqual(result['providers']['jev']['tasks']['support']['matching_dimensions'],9)
        self.assertEqual(result['providers']['jev']['tasks']['support']['usable_dimensions'],8)
        self.assertEqual(result['providers']['jev']['attempted'],48)
        self.assertEqual(result['repairs']['doubao_nonstream']['estimated_afp'],1.59375)
        self.assertTrue(result['repairs']['memory_excerpt']['posthoc_replay'])
        self.assertIn('not added',result['repairs']['scope'])

    def test_task_and_repair_values_cannot_expose_arbitrary_text_or_coerce_flags(self):
        raw = self.saved(); secret = 'apikey_never_expose_this_123456'
        raw['providers']['jev']['tasks'] = {'support': {'matching_dimensions':secret,'api_key':secret},
            secret: {'source':secret}}
        raw['repairs'] = {'scope':secret,'raw_requests':[secret],
            'jev_rounding': {'reparsed_cases':secret,'changed_cases':True,'new_provider_requests':-1},
            'memory_excerpt': {'source_text':secret,'posthoc_replay':'true'}}
        self.path.write_text(json.dumps(raw))
        result = load_semantic_validation(self.state)
        self.assertNotIn(secret,json.dumps(result))
        self.assertIsNone(result['providers']['jev']['tasks']['support']['matching_dimensions'])
        self.assertIsNone(result['repairs']['jev_rounding']['changed_cases'])
        self.assertIsNone(result['repairs']['memory_excerpt']['posthoc_replay'])

    def test_raw_requests_credentials_sources_and_arbitrary_urls_are_not_exposed(self):
        raw = self.saved()
        secret = 'apikey_do_not_publish_1234567890'
        raw.update(raw_requests=[secret], api_key=secret, source_text=secret,
            limitations=[secret], pricing_links=[{'url': 'https://evil.invalid/?key=' + secret}])
        raw['models']['jev'] = secret
        raw['dataset']['label_basis'] = secret
        raw['review']['reviewer_kind'] = secret
        raw['providers']['jev'].update(api_key=secret, requests=[secret])
        raw['providers']['jev']['cost']['scope'] = secret
        self.path.write_text(json.dumps(raw))
        result = load_semantic_validation(self.state)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn('evil.invalid', json.dumps(result))
        self.assertIsNone(result['models']['jev'])

    def test_invalid_numeric_types_remain_unknown_without_coercing_to_zero(self):
        raw = self.saved()
        raw['providers']['jev'].update(planned=True, attempted=-1, ai_label_agreement=1.1, usable_coverage='not-a-number')
        raw['providers']['jev']['latency_seconds']['median'] = '-5'
        raw['providers']['jev']['usage']['input_tokens'] = '100'
        self.path.write_text(json.dumps(raw))
        result = load_semantic_validation(self.state)['providers']['jev']
        for key in ('planned', 'attempted', 'ai_label_agreement', 'usable_coverage'):
            self.assertIsNone(result[key])
        self.assertIsNone(result['latency_seconds']['median'])
        self.assertIsNone(result['usage']['input_tokens'])

    def test_bad_json_wrong_schema_and_nonfinite_data_fail_closed(self):
        for text in ('{incomplete', '[]', '{"schema":"unknown"}', '{"schema":"semantic-validation-public-v1","x":NaN}'):
            with self.subTest(text=text):
                self.path.write_text(text)
                result = load_semantic_validation(self.state)
                self.assertFalse(result['available'])
                self.assertEqual(result['reason'], 'invalid_artifact')

    def test_oversized_and_symlinked_results_are_not_read(self):
        self.path.write_bytes(b' ' * (MAX_BYTES + 1))
        self.assertEqual(load_semantic_validation(self.state)['reason'], 'artifact_size_limit')
        self.path.unlink()
        outside = self.root / 'outside.json'; outside.write_text('{}')
        self.path.symlink_to(outside)
        self.assertEqual(load_semantic_validation(self.state)['reason'], 'unsafe_artifact_path')

    def test_route_get_is_read_only_and_never_invokes_a_judge(self):
        self.saved()
        project_fixture(self.root)
        runtime = AgentRuntime(self.root, state_dir=self.state, model_mode='current_conversation_no_api')
        before = self.path.read_bytes()
        with patch('research_agent.evidence_judgments.EvidenceJudgmentService.assess', side_effect=AssertionError('No judge on GET')), \
             patch('research_agent.investment.jev.JevProvider.evaluate', side_effect=AssertionError('No provider on GET')):
            with TestClient(create_app(runtime), base_url='http://127.0.0.1:8765') as client:
                result = client.get('/api/semantic-validation').raise_for_status().json()
                self.assertTrue(result['available'])
                self.assertEqual(result['providers']['jev']['attempted'], 48)
                self.assertEqual(client.post('/api/semantic-validation', headers={'x-local-token': client.app.state.csrf_token}).status_code, 405)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(runtime.sessions(), [])


if __name__ == '__main__': unittest.main()
