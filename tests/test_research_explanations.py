"""Synthetic contracts for explanatory reports; not live investment validation."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from research_agent.answers import (AnswerDraft, answer_evidence_ids, answer_targets,
                                   render_answer, validate_answer)
from research_agent.reports import ReportDraft, render_report, validate_report
from research_agent.research_explanations import (ResearchExplanation, explanation_evidence_ids,
                                                explanation_targets, validate_explanations)
from research_agent.review import EvidenceReviewer, check_review, evidence_text, review_targets
from research_agent.settings import Settings


CUTOFF = '2026-10-28T00:00:00Z'
PRICE, EVENT, INPUT = 'live-price', 'page-event', 'live-input'


def records():
    return {
        PRICE: {'record_id': PRICE, 'retrieved_at': '2026-10-27T23:00:00Z',
                'values': {'price_usd': '100'}, 'units': {'price_usd': 'USD'},
                'text': 'Synthetic observation: price is below its saved reference. It does not establish a future return.'},
        EVENT: {'record_id': EVENT, 'retrieved_at': '2026-10-27T22:00:00Z',
                'text': 'Synthetic proposal is under discussion; adoption and its date have not been confirmed.'},
    }


def explanation_payload():
    claim = lambda text, rid=PRICE: {'statement': text, 'evidence_ids': [rid]}
    return {
        'name': 'Weak trend as a working interpretation',
        'observed_basis': [{**claim('Saved price is 100 USD.'),
                            'metric': 'price_usd', 'value': '100', 'unit': 'USD'}],
        'hypothesis': claim('Below-reference price may be consistent with weakness; direction is uncertain.'),
        'horizon': 'The observed snapshot; no validated future return horizon.',
        'applicability': [claim('Interpret only within the saved price reference scope.')],
        'alternative_explanations': [claim('A temporary deviation remains a possible explanation, not an established rebound.')],
        'counterevidence': [claim('The proposal remains unconfirmed.', EVENT)],
        'invalidation': [claim('Revisit the explanation if the underlying saved observation is corrected.')],
        'uncertainties': ['One snapshot does not establish persistence or causal impact.'],
        'asset': 'ETH', 'horizon_days': 7,
        'context': {'price_reference': 'below_reference', 'volatility': None},
    }


def report(explanations=()):
    return ReportDraft(title='Synthetic conditional research', summary='A limited source-grounded observation.',
        observations=[{'statement': 'A price was saved.', 'evidence_ids': [PRICE]}],
        explanations=list(explanations), limitations=['Synthetic contract fixture.'])


class ResearchExplanationTests(unittest.TestCase):
    def setUp(self):
        self.rows = records()
        self.explanation = ResearchExplanation.model_validate(explanation_payload())

    def test_legacy_fact_answers_and_reports_need_no_explanation(self):
        answer = AnswerDraft(facts=[{'statement': 'Saved price.', 'evidence_ids': [PRICE]}])
        self.assertEqual(answer.explanations, [])
        self.assertEqual(report().explanations, [])
        self.assertEqual(validate_report(report(), self.rows.__getitem__, CUTOFF)['explanations_checked'], 0)

    def test_explanation_only_answer_keeps_every_source(self):
        answer = AnswerDraft(explanations=[self.explanation])
        self.assertEqual(set(answer_evidence_ids(answer)), {PRICE, EVENT})
        check = validate_answer(answer, self.rows.__getitem__, CUTOFF)
        self.assertEqual(check['numeric_claims_checked'], 1)
        self.assertEqual(check['unique_evidence_records'], 2)
        self.assertEqual(check['explanations_checked'], 1)

    def test_exact_number_and_unit_checks_apply_to_nested_observations(self):
        for replacement in ({'value': '101'}, {'unit': 'ETH'}, {'evidence_ids': [EVENT]}):
            payload = explanation_payload()
            payload['observed_basis'][0].update(replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                validate_explanations([payload], self.rows.__getitem__, CUTOFF)

    def test_future_capture_and_provider_clock_cannot_hide_in_explanations(self):
        for key in ('retrieved_at', 'available_at', 'calculated_at', 'published_at',
                    'observed_at', 'period_end', 'known_at'):
            for container in ('row', 'source'):
                rows = deepcopy(self.rows)
                target = rows[EVENT] if container == 'row' else rows[EVENT].setdefault('source', {})
                target[key] = '2026-10-28T00:00:00.000001Z'
                with self.subTest(key=key, container=container), self.assertRaisesRegex(ValueError, 'unavailable'):
                    validate_report(report([self.explanation]), rows.__getitem__, CUTOFF)

    def test_explicit_timezone_is_required_for_explanation_cutoff_and_source_clocks(self):
        for bad in ('2026-10-28', '2026-10-28T00:00:00', 123456):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'timezone'):
                validate_explanations([self.explanation], self.rows.__getitem__, bad)
        self.rows[EVENT]['source'] = {'published_at': '2026-10-27T22:00:00'}
        with self.assertRaisesRegex(ValueError, 'timezone'):
            validate_answer(AnswerDraft(explanations=[self.explanation]), self.rows.__getitem__, CUTOFF)
        # The additive layer does not impose a new date contract on legacy drafts.
        self.assertEqual(validate_report(report(), self.rows.__getitem__, '2026-10-28')['status'], 'passed')

    def test_derived_input_availability_is_checked_and_equal_cutoff_is_allowed(self):
        self.rows[PRICE]['input_record_ids'] = [INPUT]
        self.rows[INPUT] = {'record_id': INPUT, 'retrieved_at': CUTOFF}
        validate_explanations([self.explanation], self.rows.__getitem__, CUTOFF)
        self.rows[INPUT]['retrieved_at'] = '2026-10-28T00:00:00.000001Z'
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            validate_explanations([self.explanation], self.rows.__getitem__, CUTOFF)

    def test_competing_hypotheses_are_optional_not_fabricated_requirements(self):
        payload = explanation_payload()
        payload['alternative_explanations'] = []; payload['counterevidence'] = []
        value = ResearchExplanation.model_validate(payload)
        self.assertEqual(value.counterevidence, [])
        self.assertEqual(value.alternative_explanations, [])
        self.assertEqual(value.context['volatility'], None)

    def test_applicability_horizon_invalidation_and_uncertainty_are_required(self):
        for change in ({'observed_basis': []}, {'horizon': ' '}, {'applicability': []},
                       {'invalidation': []}, {'uncertainties': []}, {'uncertainties': [' ']},
                       {'context': {'trend': ' '}}, {'context': {' ': 'up'}},
                       {'context': {str(i): 'value' for i in range(9)}}, {'horizon_days': 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ResearchExplanation.model_validate({**explanation_payload(), **change})

    def test_dictionary_exports_preserve_evidence_ids(self):
        self.assertEqual(explanation_evidence_ids([self.explanation.model_dump()]),
                         explanation_evidence_ids([self.explanation]))

    def test_compound_coherence_and_nested_targets_keep_fields_and_own_source_bindings(self):
        target = explanation_targets([self.explanation])[0]
        self.assertEqual(target['claim_id'], 'E1')
        self.assertEqual(target['temporal_role'], 'conditional_explanation')
        payload = json.loads(target['statement'])
        self.assertEqual(payload['context'], self.explanation.context)
        self.assertIn('uncertainties', payload)
        self.assertEqual(payload['counterevidence'][0]['evidence_ids'], [EVENT])
        targets = review_targets(report([self.explanation]))
        self.assertEqual(len([t for t in targets if t['claim_id'].startswith('E')]), 7)
        mapping = {t['claim_id']: t for t in targets}
        self.assertEqual(mapping['E1O1']['kind'], 'observation')
        self.assertEqual(mapping['E1B1']['evidence_ids'], [EVENT])
        self.assertEqual(mapping['E1C1']['temporal_role'], 'prospective_criterion')
        self.assertIn(EVENT, next(t for t in targets if t['claim_id'] == 'S1')['evidence_ids'])
        self.assertEqual(answer_targets(AnswerDraft(explanations=[self.explanation]))[0], target)

    def test_conditional_explanation_still_requires_exact_eligible_source_quotes(self):
        targets = explanation_targets([self.explanation])[:1]
        texts = {rid: evidence_text(row) for rid, row in self.rows.items()}
        result = {'verdicts': [{'claim_id': 'E1', 'status': 'conditional',
            'reason': 'Qualified scope and uncertainty retained.', 'quotes': []}]}
        with self.assertRaisesRegex(ValueError, 'exact source quote'):
            check_review(result, targets, texts)
        result['verdicts'][0]['quotes'] = [{'record_id': PRICE, 'text': 'Synthetic observation'}]
        self.assertEqual(check_review(result, targets, texts)['status'], 'reviewed')
        result['verdicts'][0]['quotes'][0]['record_id'] = 'unrelated-source'
        with self.assertRaises(ValueError):
            check_review(result, targets, texts)

    def test_one_union_quote_cannot_approve_an_observation_from_another_source(self):
        payload = explanation_payload()
        payload['observed_basis'].append({'statement': 'The proposal has not been adopted.',
                                          'evidence_ids': [EVENT]})
        targets = explanation_targets([payload])
        texts = {rid: evidence_text(row) for rid, row in self.rows.items()}
        snippets = {PRICE: 'Synthetic observation', EVENT: 'Synthetic proposal'}
        verdicts = [{'claim_id': t['claim_id'], 'status': 'supported',
                     'reason': 'Synthetic source-binding check.',
                     'quotes': [{'record_id': t['evidence_ids'][0],
                                 'text': snippets[t['evidence_ids'][0]]}]} for t in targets]
        self.assertEqual(check_review({'verdicts': verdicts}, targets, texts)['status'], 'reviewed')
        # PRICE is eligible for compound E1, but not the distinct EVENT observation.
        foreign = next(v for v in verdicts if v['claim_id'] == 'E1O2')
        foreign['quotes'] = [{'record_id': PRICE, 'text': snippets[PRICE]}]
        with self.assertRaisesRegex(ValueError, 'eligible cited record: E1O2'):
            check_review({'verdicts': verdicts}, targets, texts)
        foreign['quotes'] = [{'record_id': EVENT, 'text': snippets[EVENT]}]
        foreign['status'] = 'conditional'
        with self.assertRaisesRegex(ValueError, 'observation or summary'):
            check_review({'verdicts': verdicts}, targets, texts)
        compound_only = [v for v in verdicts if v['claim_id'] == 'E1']
        with self.assertRaisesRegex(ValueError, 'every target'):
            check_review({'verdicts': compound_only}, targets, texts)

    def test_rendered_report_and_answer_keep_condition_and_counterevidence_links(self):
        text = render_report(report([self.explanation]), CUTOFF, {}, [])
        self.assertIn('Explanations and conditions', text)
        for field in ('Observed basis', 'Applies under these conditions', 'Alternative explanations',
                      'Counterevidence and limitations', 'Revisit this explanation when', 'Uncertainties'):
            self.assertIn(field, text)
        self.assertIn(EVENT + '.json', text)
        answer = render_answer(AnswerDraft(explanations=[self.explanation]), {PRICE: '/price', EVENT: '/event'})
        self.assertIn('/event', answer)
        self.assertIn('volatility=Unknown', answer)

    def test_reviewer_rejects_oversized_target_set_before_a_model_request(self):
        with patch('research_agent.review.create_ark_model') as build:
            reviewer = EvidenceReviewer(Settings())
            with self.assertRaisesRegex(ValueError, '1..90'):
                reviewer.review_claims([{'claim_id': str(i)} for i in range(91)], self.rows.__getitem__)
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
