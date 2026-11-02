"""Synthetic contract cases, not evidence of live advice quality or returns."""
import json
from pathlib import Path
import tempfile
import unittest

from fixtures import fixture_run
from research_agent.advice import ResearchAdvice, HistoricalCase, advice_targets, validate_advice
from research_agent.answers import AnswerDraft, answer_targets, answer_evidence_ids, validate_answer, render_answer
from research_agent.audit import report_evidence_ids
from research_agent.reports import ReportDraft, validate_report, render_report
from research_agent.review import check_review, evidence_text, review_targets
from research_agent.tools import ResearchTools

PRICE = 'live-' + 'a' * 24
HISTORY = 'page-' + 'b' * 24
CONTEXT = 'context-' + 'c' * 24
INPUT = 'live-' + 'd' * 24
CUTOFF = '2026-02-01T00:00:00Z'


def records():
    return {
        PRICE: {'record_id': PRICE, 'observed_at': '2026-01-01T00:00:00Z',
                'retrieved_at': '2026-01-02T00:00:00Z', 'values': {'price_usd': '100'},
                'units': {'price_usd': 'USD'}, 'source': {'url': 'https://example.org/price'},
                'text': 'Synthetic saved market evidence; no forecast is established.'},
        HISTORY: {'record_id': HISTORY, 'retrieved_at': '2026-01-01T00:00:00Z',
                  'source': {'url': 'https://example.org/history'},
                  'text': 'Synthetic announcement was delayed; a later version needs review.'},
        CONTEXT: {'record_id': CONTEXT, 'dataset_id': 'local.user_research_parameters',
                  'profile': {'budget_usd': None, 'existing_crypto_usd': None,
                              'portfolio_total_usd': None, 'max_loss_usd': None,
                              'fee_bps': 10, 'preference': ''}},
    }


def advice_payload(history=True):
    payload = {
        'summary': 'Monitor confirmation before revisiting the research conditions.',
        'rationale': [{'statement': 'The saved snapshot is limited to one observation.', 'evidence_ids': [PRICE]}],
        'conditions': [{'statement': 'Recheck the saved observation before interpreting a change.', 'evidence_ids': [PRICE]}],
        'invalidation': [{'statement': 'Revisit this view if the underlying observation is corrected.', 'evidence_ids': [PRICE]}],
        'missing_information': ['The user has not supplied a personal loss limit.'],
        'review_triggers': ['Check a subsequent official update.'],
    }
    if history:
        payload['historical_cases'] = [{
            'name': 'Synthetic delayed announcement', 'case_end': '2026-01-01T00:00:00Z',
            'evidence_ids': [HISTORY], 'similarities': ['Both require announcement verification.'],
            'differences': ['The present event has not been established as the same process.'],
            'limitations': ['A single case establishes no success rate.'],
        }]
    return payload


def report(advice=None):
    return ReportDraft(title='Synthetic research', summary='A scoped observation.',
                       observations=[{'statement': 'Saved price', 'evidence_ids': [PRICE],
                                      'metric': 'price_usd', 'value': '100', 'unit': 'USD'}],
                       judgment='insufficient_evidence', judgment_reason='Limited inputs.',
                       limitations=['Synthetic contract fixture.'], advice=advice)


class AdviceTests(unittest.TestCase):
    def setUp(self):
        self.rows = records()
        self.advice = ResearchAdvice.model_validate(advice_payload())

    def test_old_answers_and_reports_do_not_require_advice(self):
        answer = AnswerDraft(facts=[{'statement': 'Saved price', 'evidence_ids': [PRICE]}])
        self.assertIsNone(answer.advice)
        self.assertEqual(validate_answer(answer, self.rows.__getitem__)['unique_evidence_records'], 1)
        self.assertIsNone(report().advice)

    def test_advice_only_answer_retains_all_citations(self):
        answer = AnswerDraft(advice=self.advice)
        check = validate_answer(answer, self.rows.__getitem__, CUTOFF)
        self.assertEqual(check['historical_cases_checked'], 1)
        self.assertEqual(set(answer_evidence_ids(answer)), {PRICE, HISTORY})
        self.assertEqual(check['unique_evidence_records'], 2)

    def test_context_specific_advice_needs_a_saved_context(self):
        payload = advice_payload(); payload['scope'] = 'user_context'
        with self.assertRaises(ValueError):
            ResearchAdvice.model_validate(payload)

    def test_default_fees_and_unknown_amounts_cannot_establish_personal_context(self):
        self.advice.scope = 'user_context'; self.advice.context_evidence_ids = [CONTEXT]
        with self.assertRaisesRegex(ValueError, 'Unknown personal'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_explicit_zero_exposure_is_distinct_from_unknown(self):
        self.rows[CONTEXT]['profile']['existing_crypto_usd'] = 0
        self.advice.scope = 'user_context'; self.advice.context_evidence_ids = [CONTEXT]
        result = validate_advice(self.advice, self.rows.__getitem__, CUTOFF)
        self.assertIn(CONTEXT, result['evidence_ids'])

    def test_market_record_cannot_masquerade_as_user_context(self):
        self.advice.scope = 'user_context'; self.advice.context_evidence_ids = [PRICE]
        with self.assertRaisesRegex(ValueError, 'current research parameters'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_advice_numeric_values_use_the_same_saved_field_gate(self):
        claim = self.advice.rationale[0]
        claim.metric = 'price_usd'; claim.value = '101'; claim.unit = 'USD'
        with self.assertRaises(ValueError):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)
        claim.value = '100'
        self.assertEqual(validate_advice(self.advice, self.rows.__getitem__, CUTOFF)['numeric_claims_checked'], 1)

    def test_empty_numeric_keys_or_nondecimal_values_cannot_bypass_validation(self):
        from research_agent.claims import Claim,validate_claims
        for metric,value,unit in [('', '100', 'USD'), ('price_usd', '', 'USD'), ('price_usd', '100', '')]:
            with self.subTest(metric=metric,value=value,unit=unit),self.assertRaises(ValueError):
                Claim(statement='Invalid synthetic number',evidence_ids=[PRICE],metric=metric,value=value,unit=unit)
        with self.assertRaises(ValueError):
            Claim(statement='Invalid synthetic number',evidence_ids=[PRICE],metric='price_usd',value='not-a-number',unit='USD')
        # The publication gate must also reject callers bypassing model validation.
        claim=Claim.model_construct(statement='Invalid synthetic number',evidence_ids=[PRICE],metric='price_usd',value='not-a-number',unit='USD')
        with self.assertRaises(ValueError):validate_claims([claim],self.rows.__getitem__)
        claim.metric='';claim.value='100'
        with self.assertRaises(ValueError):validate_claims([claim],self.rows.__getitem__)

    def test_unit_from_a_different_record_cannot_validate_an_advice_number(self):
        self.rows[HISTORY].update(values={'price_usd': '200'}, units={'price_usd': 'ETH'})
        claim = self.advice.rationale[0]
        claim.evidence_ids = [PRICE, HISTORY]; claim.metric = 'price_usd'; claim.value = '200'; claim.unit = 'USD'
        with self.assertRaises(ValueError):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_dangling_reference_is_not_replaced_with_another_source(self):
        self.advice.conditions[0].evidence_ids = ['missing']
        with self.assertRaises(KeyError):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_historical_outcome_window_cannot_extend_past_decision(self):
        self.advice.historical_cases[0].case_end = '2026-02-02T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'future outcome'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_future_retrieval_is_blocked_even_when_article_mentions_old_date(self):
        self.rows[HISTORY]['retrieved_at'] = '2026-02-02T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_current_advice_also_cannot_use_a_future_snapshot(self):
        self.rows[PRICE]['retrieved_at'] = '2026-02-01T00:00:00.000001Z'
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_future_provider_receipt_is_not_hidden_by_an_older_top_level_time(self):
        self.rows[HISTORY]['source']['retrieved_at'] = '2026-02-02T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_derived_history_checks_its_inputs(self):
        self.rows[HISTORY]['input_record_ids'] = [INPUT]
        self.rows[INPUT] = {'record_id': INPUT, 'retrieved_at': '2026-03-01T00:00:00Z'}
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_point_in_time_requires_receipt_even_at_equal_cutoff(self):
        case = self.advice.historical_cases[0]
        case.availability = 'point_in_time'; case.case_end = CUTOFF
        self.rows[HISTORY].pop('retrieved_at')
        with self.assertRaisesRegex(ValueError, 'availability receipt'):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_retrospective_capture_is_not_promoted_to_point_in_time(self):
        case = self.advice.historical_cases[0]
        case.case_end = '2025-12-31T00:00:00Z'
        validate_advice(self.advice, self.rows.__getitem__, CUTOFF)
        case.availability = 'point_in_time'
        with self.assertRaises(ValueError):
            validate_advice(self.advice, self.rows.__getitem__, CUTOFF)

    def test_historical_case_requires_explicit_timezone_and_differences(self):
        payload = advice_payload()['historical_cases'][0]
        for changes in [{'case_end': '2026-01-01'}, {'differences': []}, {'limitations': []}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                HistoricalCase.model_validate({**payload, **changes})

    def test_every_advice_component_is_a_review_target_with_its_own_sources(self):
        targets = advice_targets(self.advice)
        self.assertEqual(len({target['claim_id'] for target in targets}), len(targets))
        history = next(target for target in targets if target['claim_id'] == 'VH1')
        self.assertEqual(history['evidence_ids'], [HISTORY])
        self.assertIn('differences', history['statement'])
        self.assertTrue(any(target['claim_id'] == 'VC1' for target in targets))
        self.assertTrue(any(target['claim_id'] == 'VI1' for target in targets))

    def test_review_cannot_quote_an_unrelated_record_for_an_advice_condition(self):
        target = next(t for t in advice_targets(self.advice) if t['claim_id'] == 'VC1')
        verdict = {'verdicts': [{'claim_id': 'VC1', 'status': 'reasonable_inference',
                                'reason': 'Synthetic transport case.',
                                'quotes': [{'record_id': HISTORY, 'text': 'Synthetic announcement'}]}]}
        with self.assertRaises(ValueError):
            check_review(verdict, [target], {rid: evidence_text(row) for rid, row in self.rows.items()})

    def test_prospective_advice_roles_survive_review_without_relaxing_source_checks(self):
        targets = {row['claim_id']: row for row in advice_targets(self.advice)}
        self.assertEqual(targets['VC1']['temporal_role'], 'prospective_criterion')
        self.assertEqual(targets['VI1']['advice_section'], 'invalidation')
        self.assertEqual(targets['VR1']['temporal_role'], 'evidence_based_reasoning')
        target = targets['VI1']
        verdict = {'verdicts': [{'claim_id': 'VI1', 'status': 'conditional',
            'reason': 'Future recheck criterion, not an observed event.', 'quotes': []}]}
        with self.assertRaisesRegex(ValueError, 'exact source quote'):
            check_review(verdict, [target], {rid: evidence_text(row) for rid, row in self.rows.items()})

    def test_export_and_report_summary_include_history_only_references(self):
        draft = report(self.advice)
        self.assertEqual(report_evidence_ids(draft.model_dump()), {PRICE, HISTORY})
        summary = next(t for t in review_targets(draft) if t['claim_id'] == 'S1')
        self.assertIn(HISTORY, summary['evidence_ids'])
        self.assertEqual(validate_report(draft, self.rows.__getitem__, CUTOFF)['numeric_claims_checked'], 1)

    def test_rendered_answer_and_report_preserve_analogy_limits_and_source_links(self):
        links = {PRICE: '/price', HISTORY: '/history'}
        answer = render_answer(AnswerDraft(advice=self.advice), links)
        self.assertIn('Retrospective observed history', answer)
        self.assertIn('Differences:', answer)
        self.assertIn('Limits of transfer:', answer)
        self.assertIn('/history', answer)
        rendered = render_report(report(self.advice), CUTOFF, {}, [])
        self.assertIn('Conditional research suggestion', rendered)
        self.assertIn(HISTORY + '.json', rendered)

    def test_answer_publication_saves_advice_citations_with_mocked_review_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = fixture_run(root / 'project')
            workspace = root / 'workspace'
            class Reviewer:
                def review_claims(self, targets, lookup, payload):
                    return {'status': 'reviewed', 'review_id': 'fixture-advice-review', 'claims_checked': len(targets)}
            tools = ResearchTools(run, workspace, CUTOFF, allow_live=True, reviewer=Reviewer())
            for rid in [PRICE, HISTORY]:
                path = workspace / 'data/live' / (rid + '.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self.rows[rid]))
            result = tools.submit_answer(AnswerDraft(advice=self.advice))
            self.assertEqual(result['status'], 'verified')
            body = json.loads(next((workspace / 'answers').glob('*.json')).read_text())
            self.assertEqual({row['record_id'] for row in body['citations']}, {PRICE, HISTORY})
            self.assertEqual(body['draft']['advice']['scope'], 'general')


if __name__ == '__main__':
    unittest.main()
