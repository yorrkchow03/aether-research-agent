"""Condition-shift diagnostics: literal contracts, not AI self-grading."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from research_agent.condition_evaluation import (
    aggregate_attempts, condition_probe, digest, eligible_sources, load_dataset,
    offline_checks, score_prediction,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'examples/evaluation/condition_preservation_v1.json'


class ConditionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(FIXTURE)
        self.cases = {c['case_id']:c for c in self.dataset['cases']}

    def prediction(self, cid):
        case = self.cases[cid]
        required = case['reference']['required_obligation_ids']
        obligations = {o['obligation_id']:o for o in case['obligations']}
        return {'status':'completed', 'label':case['reference']['label'], 'obligation_ids':required,
                'quotes':[{'record_id':obligations[o]['record_id'], 'text':obligations[o]['exact_quote']} for o in required]}

    def attempt(self, arm, cid, **fields):
        return {'arm':arm, 'case_id':cid, 'input_hash':digest(condition_probe(self.cases[cid])),
                **self.prediction(cid), **fields}

    def invalid_dataset(self, data, expected):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'dataset.json'; path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, expected):
                load_dataset(path)

    def test_sixteen_cases_eight_pairs_have_explicit_synthetic_prelabels(self):
        self.assertEqual(len(self.dataset['cases']), 16)
        self.assertEqual(len({c['pair_id'] for c in self.dataset['cases']}), 8)
        self.assertIs(self.dataset['held_out'], False)
        self.assertIn('not human truth', self.dataset['label_author'])
        self.assertEqual(self.dataset['version'], '1.1')
        self.assertIn('not rescored', self.dataset['revision']['prior_results'])

    def test_completed_observation_period_cannot_follow_publication_or_acquisition(self):
        for key in ('observed_at', 'period_end'):
            data = deepcopy(self.dataset)
            case = next(c for c in data['cases'] if c['case_id'] == 'horizon-monthly')
            case['sources'][0][key] = '2026-10-02T00:00:00Z'
            self.invalid_dataset(data, 'later than publication or acquisition')

    def test_observation_metadata_requires_timezone_and_does_not_parse_future_plans_as_observed(self):
        data = deepcopy(self.dataset)
        data['cases'][0]['sources'][0]['observed_at'] = '2026-10-01T00:00:00'
        self.invalid_dataset(data, 'timezone-aware')
        data = deepcopy(self.dataset)
        data['cases'][0]['sources'][0]['event_at'] = '2027-01-01T00:00:00Z'
        # A planned future event is not an observed future outcome.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'fixture.json'; path.write_text(json.dumps(data))
            self.assertEqual(load_dataset(path)['cases'][0]['sources'][0]['event_at'], '2027-01-01T00:00:00Z')

    def test_provider_probe_excludes_reference_labels_and_future_source_bytes(self):
        probe = condition_probe(self.cases['correction-before'])
        self.assertEqual(set(probe), {'question','evidence'})
        self.assertEqual(len(probe['evidence']), 1)
        self.assertNotIn('21 million', json.dumps(probe))
        for name in ('obligations','reference','required_obligation_ids','rationale'):
            self.assertNotIn(name, probe)
        self.assertEqual(condition_probe(self.cases['capture-cutoff-before'])['evidence'], [])

    def test_later_publication_or_provider_receipt_cannot_hide_under_old_top_level_capture(self):
        case = deepcopy(self.cases['event-status-active'])
        for where in ('published_at','available_at'):
            case['sources'][0][where] = '2026-11-01T00:00:00Z'
            self.assertEqual(eligible_sources(case), [])
            case = deepcopy(self.cases['event-status-active'])
        case['sources'][0]['source']['retrieved_at'] = '2026-11-01T00:00:00Z'
        self.assertEqual(eligible_sources(case), [])

    def test_reference_quotes_cannot_be_changed_without_fixture_validation_failure(self):
        data = deepcopy(self.dataset)
        data['cases'][0]['obligations'][0]['exact_quote'] = 'invented source quote'
        self.invalid_dataset(data, 'not an exact source passage')

    def test_expected_cutoff_label_cannot_claim_a_later_capture_is_eligible(self):
        data = deepcopy(self.dataset)
        case = next(c for c in data['cases'] if c['case_id'] == 'capture-cutoff-before')
        case['expected_eligible_ids'] = [case['sources'][0]['record_id']]
        self.invalid_dataset(data, 'cutoff eligibility')

    def test_identical_pair_context_or_changed_question_cannot_qualify(self):
        data = deepcopy(self.dataset); data['cases'][1]['question'] = 'Different question'
        self.invalid_dataset(data, 'claim identical')
        data = deepcopy(self.dataset); data['cases'][1]['sources'] = deepcopy(data['cases'][0]['sources'])
        data['cases'][1]['expected_eligible_ids'] = data['cases'][0]['expected_eligible_ids']
        data['cases'][1]['obligations'] = deepcopy(data['cases'][0]['obligations'])
        self.invalid_dataset(data, 'did not actually change')

    def test_missing_condition_detected_even_when_label_and_some_quote_are_correct(self):
        case = self.cases['eligibility-restricted']; prediction = self.prediction(case['case_id'])
        prediction['quotes'] = [{'record_id':case['sources'][0]['record_id'], 'text':'Other validators are ineligible.'}]
        score = score_prediction(case,prediction)
        self.assertTrue(score['label_agrees_with_ai_reference'])
        self.assertTrue(score['source_quote_integrity'])
        self.assertFalse(score['required_condition_coverage'])
        self.assertIsNone(score['semantic_correctness'])

    def test_correct_obligation_id_does_not_rescue_fabricated_or_unrelated_quote(self):
        case = self.cases['eligibility-restricted']
        for change in ({'text':'All validators are eligible.'}, {'record_id':'unrelated'}):
            prediction = self.prediction(case['case_id']); prediction['quotes'][0].update(change)
            score = score_prediction(case,prediction)
            self.assertFalse(score['source_quote_integrity']); self.assertFalse(score['required_condition_coverage'])

    def test_provider_label_only_does_not_fabricate_quote_or_coverage_success(self):
        score = score_prediction(self.cases['event-status-active'], {'status':'completed','label':'supported'})
        self.assertTrue(score['label_agrees_with_ai_reference'])
        self.assertIsNone(score['source_quote_integrity']); self.assertIsNone(score['condition_coverage'])
        self.assertIsNone(score['required_condition_coverage'])

    def test_abstention_or_error_has_no_synthetic_correct_label(self):
        case = self.cases['event-status-active']
        for status in ('abstained','error','missing'):
            self.assertIsNone(score_prediction(case,{'status':status})['label_agrees_with_ai_reference'])
            with self.assertRaisesRegex(ValueError,'Unusable attempts'):
                score_prediction(case,{'status':status,'label':'supported'})

    def test_full_denominators_preserve_failures_abstentions_and_missing_attempts(self):
        attempts = [self.attempt('jev','event-status-active'),
                    self.attempt('jev','event-status-proposed',status='error',label=None),
                    self.attempt('jev','eligibility-restricted',status='abstained',label=None)]
        summary = aggregate_attempts(self.dataset,attempts)['arms'][0]
        self.assertEqual((summary['planned'],summary['completed'],summary['errors'],summary['abstained'],summary['missing']), (16,1,1,1,13))
        self.assertEqual(summary['reference_agreement_over_planned'],1/16)
        self.assertIsNone(summary['cost_total']); self.assertIsNone(summary['median_recorded_latency_seconds'])

    def test_duplicate_or_modified_input_attempt_cannot_be_scored_as_first_attempt(self):
        attempt = self.attempt('jev','event-status-active')
        with self.assertRaisesRegex(ValueError,'retry or best'):
            aggregate_attempts(self.dataset,[attempt,attempt])
        attempt['input_hash'] = 'different'
        with self.assertRaisesRegex(ValueError,'identical frozen'):
            aggregate_attempts(self.dataset,[attempt])

    def test_missing_context_budget_is_not_repaired_by_truncating_required_condition(self):
        data = deepcopy(self.dataset); data['protocol']['context_budget_characters'] = 100
        self.invalid_dataset(data, 'never truncate conditions')

    def test_mutant_contract_checks_never_call_network_or_models_or_claim_accuracy(self):
        with patch('socket.create_connection',side_effect=AssertionError('No network')), \
             patch('research_agent.model.create_ark_model',side_effect=AssertionError('No models')):
            result = offline_checks(self.dataset)
        self.assertTrue(result['fixture_contracts_passed'])
        self.assertEqual((result['fixture_cases'],result['pairs'],result['provider_calls']), (16,8,0))
        self.assertIsNone(result['semantic_correctness'])
        self.assertTrue(all(p['rejected'] for c in result['checks'] for p in c['mutant_checks']))


if __name__ == '__main__':
    unittest.main()
