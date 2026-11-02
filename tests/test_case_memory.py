"""Condition-aware case navigation keeps time, negative outcomes and provenance."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from research_agent.case_memory import CaseMemory


class CaseMemoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.rows = {
            'page-prices': {'record_id': 'page-prices', 'retrieved_at': '2026-01-02T00:00:00Z',
                            'available_at': '2026-01-01T00:00:00Z', 'text': 'Synthetic declining ETH trend.',
                            'values': {'return': '-0.1'}, 'units': {'return': 'fraction'}},
            'page-news': {'record_id': 'page-news', 'retrieved_at': '2026-01-02T00:00:00Z',
                         'available_at': '2026-01-01T00:00:00Z', 'text': 'Synthetic ETH proposal, not adopted.'},
            'page-outcome': {'record_id': 'page-outcome', 'retrieved_at': '2026-01-05T00:00:00Z',
                            'available_at': '2026-01-04T00:00:00Z', 'text': 'Synthetic hypothesis was not supported.'}}
        self.store = CaseMemory(self.root, evidence_lookup=self.rows.__getitem__)

    def payload(self, **changes):
        return {'title': 'Synthetic ETH trend case', 'asset': 'ETH', 'case_at': '2026-01-01T00:00:00Z',
                'horizon': 'One week', 'horizon_days': 7,
                'hypothesis': {'statement': 'Weak trend may persist if conditions hold.', 'evidence_ids': ['page-prices']},
                'conditions': [{'key': 'trend', 'value': 'down', 'statement': 'Saved trend is declining.', 'evidence_ids': ['page-prices']},
                               {'key': 'event_status', 'value': 'proposed', 'statement': 'Proposal is not adoption.', 'evidence_ids': ['page-news']},
                               {'key': 'liquidity', 'value': None, 'statement': 'Liquidity is not established.'}],
                'evidence_ids': ['page-prices', 'page-news'], 'counterevidence_ids': ['page-news'],
                'invalidation': ['Revisit after a sustained trend reversal.'],
                'limitations': ['Synthetic process fixture, not a profitable strategy.'], **changes}

    def save(self, when='2026-01-03T00:00:00Z', **changes):
        with patch('research_agent.case_memory.utcnow', return_value=when):
            return self.store.record_case(self.payload(**changes))

    def outcome(self, cid, when='2026-01-06T00:00:00Z', **changes):
        with patch('research_agent.case_memory.utcnow', return_value=when):
            return self.store.record_outcome(cid, {'window_end': '2026-01-04T00:00:00Z',
                'assessment': 'not_supported', 'observation': {'statement': 'A synthetic negative observation.',
                'evidence_ids': ['page-outcome']}, 'limitations': ['No causal attribution or investment advantage.'], **changes})

    def recall(self, when='2026-01-07T00:00:00Z', **kwargs):
        return self.store.recall('ETH trend', cutoff=when, **kwargs)

    def test_deduplication_preserves_first_acquisition_and_append_only(self):
        first = self.save()
        again = self.save('2026-01-04T00:00:00Z')
        self.assertEqual(first, again)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM research_cases').fetchone()[0], 1)
            for sql in ['DELETE FROM research_cases', 'UPDATE research_cases SET known_at="old"']:
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(sql)

    def test_callers_cannot_backdate_case_or_outcome(self):
        with self.assertRaises(ValueError):
            self.save(known_at='2025-12-01T00:00:00Z')
        case = self.save()
        with self.assertRaises(ValueError):
            self.outcome(case['case_id'], known_at='2025-12-01T00:00:00Z')

    def test_source_and_case_future_times_cannot_be_recorded(self):
        for changes in [{'case_at': '2026-02-01T00:00:00Z'}, {'case_at': '2026-01-01'}, {'horizon': '', 'horizon_days': None}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(**changes)
        self.rows['page-news']['source'] = {'retrieved_at': '2026-02-01T00:00:00Z'}
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.save()

    def test_strict_historical_recall_excludes_later_acquired_navigation(self):
        case = self.save()
        result = self.recall('2026-01-02T23:59:59Z')
        self.assertEqual(result['results'], [])
        self.assertEqual(result['eligible_count'], 0)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.store.get(case['case_id'], cutoff='2026-01-02T23:59:59Z')

    def test_explicit_reconstruction_is_labeled_and_does_not_claim_historical_possession(self):
        self.save()
        result = self.recall('2026-01-02T12:00:00Z', mode='retrospective')
        self.assertEqual(len(result['results']), 1)
        self.assertFalse(result['results'][0]['historically_known'])
        self.assertIn('RETROSPECTIVE RECONSTRUCTION', result['prompt_context'])
        self.assertEqual(self.recall('2025-12-31T00:00:00Z', mode='retrospective')['results'], [])

    def test_reconstruction_excludes_sources_published_after_cutoff(self):
        self.rows['page-news']['available_at'] = '2026-01-02T00:00:00Z'
        self.save()
        result = self.recall('2026-01-01T12:00:00Z', mode='retrospective')
        self.assertEqual(result['results'], [])
        self.assertTrue(result['excluded'])

    def test_negative_outcomes_are_retained_but_never_change_score(self):
        case = self.save()
        before = self.recall()
        outcome = self.outcome(case['case_id'])
        after = self.recall()
        self.assertEqual(after['results'][0]['outcomes'][0]['outcome']['assessment'], 'not_supported')
        self.assertEqual(after['results'][0]['score'], before['results'][0]['score'])
        self.assertEqual(outcome, self.outcome(case['case_id'], when='2026-01-07T00:00:00Z'))
        with self.store.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute('DELETE FROM case_outcomes')

    def test_future_outcome_never_enters_earlier_context_or_score(self):
        case = self.save()
        before = self.recall('2026-01-04T12:00:00Z')
        self.outcome(case['case_id'])
        after = self.recall('2026-01-04T12:00:00Z')
        self.assertEqual(before, after)
        # A historical reconstruction cannot include a later outcome window.
        self.assertEqual(self.recall('2026-01-03T12:00:00Z', mode='retrospective')['results'][0]['outcomes'], [])

    def test_outcomes_cannot_precede_case_or_extend_into_future(self):
        case = self.save()
        for window in ['2025-12-31T00:00:00Z', '2026-02-01T00:00:00Z', '2026-01-04']:
            with self.subTest(window=window), self.assertRaises(ValueError):
                self.outcome(case['case_id'], window_end=window)

    def test_conditions_and_horizon_rank_similar_case_above_same_keyword_different_regime(self):
        similar = self.save()
        changed = deepcopy(self.payload()['conditions'])
        changed[0]['value'] = 'up'
        other = self.save(title='Synthetic ETH trend case different conditions', conditions=changed, horizon_days=90)
        result = self.recall(context={'asset': 'ETH', 'horizon_days': 7, 'conditions': {'trend': 'down'}})
        self.assertEqual([r['case_id'] for r in result['results']], [similar['case_id'], other['case_id']])
        self.assertEqual(result['results'][1]['match']['mismatching_conditions'][0]['key'], 'trend')
        self.assertIn('invalidation', result['prompt_context'])

    def test_missing_and_mismatching_conditions_are_visible_not_silently_filtered(self):
        self.save()
        result = self.recall(context={'conditions': {'trend': 'up', 'liquidity': 'deep', 'volatility': None}})
        match = result['results'][0]['match']
        self.assertEqual(len(match['mismatching_conditions']), 1)
        self.assertEqual(len(match['unknown_conditions']), 2)
        self.assertEqual(match['matching_conditions'], [])

    def test_future_cases_cannot_change_previous_scores_or_ordering(self):
        self.save()
        before = self.recall('2026-01-04T00:00:00Z')
        self.save('2026-01-10T00:00:00Z', title='ETH trend ETH trend highly matching future case')
        self.assertEqual(before, self.recall('2026-01-04T00:00:00Z'))

    def test_source_mutation_quarantines_instead_of_rebinding(self):
        case = self.save()
        self.rows['page-news']['text'] = 'Now allegedly adopted.'
        result = self.recall()
        self.assertEqual(result['results'], [])
        self.assertIn('fingerprint mismatch', result['excluded'][0]['reason'])
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
            self.store.get(case['case_id'], cutoff='2026-01-07T00:00:00Z')

    def test_derived_lineage_cannot_hide_future_or_mutated_input(self):
        self.rows['page-prices']['input_record_ids'] = ['raw-prices']
        self.rows['raw-prices'] = {'record_id': 'raw-prices', 'retrieved_at': '2026-01-02T00:00:00Z', 'values': {'price': 3}}
        case = self.save()
        self.assertEqual(len(case['sources']), 3)
        self.rows['raw-prices']['values']['price'] = 4
        self.assertEqual(self.recall()['results'], [])
        self.rows['raw-prices']['retrieved_at'] = '2026-02-01T00:00:00Z'
        with self.assertRaises(ValueError):
            self.save()

    def test_acquisition_receipt_required_and_identity_cannot_be_swapped(self):
        self.rows['page-prices'].pop('retrieved_at')
        with self.assertRaisesRegex(ValueError, 'acquisition receipt'):
            self.save()
        self.rows['page-prices']['retrieved_at'] = '2026-01-02T00:00:00Z'
        self.rows['page-prices']['record_id'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.save()

    def test_numeric_hypothesis_still_uses_exact_source_value_and_unit_gate(self):
        hypothesis = {'statement': 'A synthetic decline.', 'evidence_ids': ['page-prices'],
                      'metric': 'return', 'value': '-0.1', 'unit': 'fraction'}
        self.save(hypothesis=hypothesis)
        for value, unit in [('-0.2', 'fraction'), ('-0.1', 'USD')]:
            with self.assertRaises(ValueError):
                self.save(hypothesis={**hypothesis, 'value': value, 'unit': unit})

    def test_no_truncated_case_or_missing_invalidation_to_fit_budget(self):
        self.save()
        result = self.recall(budget_chars=512)
        self.assertEqual(result['results'], [])
        self.assertEqual(result['characters'], 0)
        self.assertEqual(result['budget_skipped'][0]['reason'], 'whole_case_exceeds_remaining_context_budget')
        full = self.recall(budget_chars=16000)
        self.assertLessEqual(full['characters'], 16000)
        self.assertEqual(full['characters'], len(full['prompt_context']))
        self.assertIn('Revisit after a sustained trend reversal.', full['prompt_context'])

    def test_origin_review_receipt_is_bounded_part_of_version_identity(self):
        origin = {'kind': 'reviewed_report', 'id': 'research-synthetic', 'explanation_index': 0,
                  'review_id': 'review-a', 'review_status': 'reviewed'}
        a = self.save(origin=origin)
        b = self.save(origin={**origin, 'id': 'research-different'})
        self.assertNotEqual(a['case_id'], b['case_id'])
        self.assertEqual(a['case']['origin']['review_id'], 'review-a')
        with self.assertRaises(ValueError):
            self.save(origin={**origin, 'review_status': 'unreviewed'})

    def test_bounded_validation_and_explicit_unknown(self):
        for kwargs in [{'limit': 0}, {'budget_chars': 100}, {'mode': 'pretend'}, {'context': {'unexpected': 1}},
                       {'context': {'horizon_days': True}}, {'context': {'conditions': {'trend': 2}}}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.recall(**kwargs)
        conditions = self.payload()['conditions']
        conditions[0]['evidence_ids'] = []
        with self.assertRaises(ValueError):
            self.save(conditions=conditions)
        conditions[0]['value'] = None
        self.save(conditions=conditions)

    def test_repeated_publications_do_not_crowd_out_distinct_research_cases(self):
        first = self.save()
        repeated = []
        for index in range(6):
            repeated.append(self.save('2026-01-04T00:00:00Z', case_at='2026-01-02T00:00:00Z',
                title='Republished title ' + str(index), origin={'kind': 'reviewed_report',
                'id': 'report-' + str(index), 'explanation_index': 0,
                'review_id': 'review-' + str(index), 'review_status': 'reviewed'}))
        distinct = self.save(hypothesis={'statement': 'ETH trend has a distinct competing explanation.',
                                       'evidence_ids': ['page-news']})
        result = self.recall(budget_chars=32000)
        self.assertEqual(result['eligible_count'], 8)
        self.assertEqual(result['distinct_count'], 2)
        self.assertEqual(result['duplicate_suppressed_count'], 6)
        self.assertEqual({item['case_id'] for item in result['results']}, {first['case_id'], distinct['case_id']})
        self.assertEqual(result['duplicate_groups'][0]['kept_case_id'], first['case_id'])
        self.assertEqual(len(result['duplicate_groups'][0]['suppressed_case_ids']), 6)
        # Origin and observation clocks remain immutable/readable for review.
        self.assertEqual(self.store.get(repeated[0]['case_id'], cutoff='2026-01-07T00:00:00Z')['case']['origin']['id'], 'report-0')

    def test_future_duplicate_does_not_change_past_representative_counts_or_scores(self):
        self.save()
        before = self.recall('2026-01-04T00:00:00Z')
        self.save('2026-01-05T00:00:00Z', case_at='2026-01-02T00:00:00Z', origin='Later repeated case')
        self.assertEqual(before, self.recall('2026-01-04T00:00:00Z'))

    def test_condition_or_evidence_changes_are_not_suppressed_as_republications(self):
        self.save()
        conditions = self.payload()['conditions']
        conditions[0]['value'] = 'up'
        self.save(conditions=conditions)
        result = self.recall(budget_chars=32000)
        self.assertEqual(result['distinct_count'], 2)
        self.assertEqual(result['duplicate_suppressed_count'], 0)

    def test_outcome_pages_decode_only_bounded_rows_and_expose_more_without_truncation(self):
        case = self.save()
        all_ids = set()
        for index in range(21):
            row = self.outcome(case['case_id'], observation={'statement': 'Synthetic observation ' + str(index),
                                                           'evidence_ids': ['page-outcome']})
            all_ids.add(row['outcome_id'])
        with patch.object(self.store, '_decode', wraps=self.store._decode) as decode:
            first = self.store.get(case['case_id'], cutoff='2026-01-07T00:00:00Z', outcome_limit=3)
            self.assertEqual(decode.call_count, 4)  # one case, exactly three outcome rows
        self.assertEqual(first['outcome_page']['candidate_count'], 21)
        self.assertTrue(first['outcome_page']['has_more'])
        self.assertEqual(first['outcome_page']['next_offset'], 3)
        default = self.store.get(case['case_id'], cutoff='2026-01-07T00:00:00Z')
        self.assertEqual(len(default['outcomes']), 16)
        final = self.store.get(case['case_id'], cutoff='2026-01-07T00:00:00Z', outcome_offset=16)
        self.assertEqual(len(final['outcomes']), 5)
        self.assertFalse(final['outcome_page']['has_more'])
        self.assertEqual({row['outcome_id'] for row in [*default['outcomes'], *final['outcomes']]}, all_ids)
        self.assertEqual(default['case']['invalidation'], self.payload()['invalidation'])

    def test_outcome_pagination_filters_future_acquisition_at_microsecond_precision(self):
        case = self.save()
        cutoff = '2026-01-06T00:00:00Z'
        before = self.store.get(case['case_id'], cutoff=cutoff)
        self.outcome(case['case_id'], when='2026-01-06T00:00:00.000001Z')
        after = self.store.get(case['case_id'], cutoff=cutoff)
        self.assertEqual(before, after)
        self.assertEqual(after['outcome_page']['candidate_count'], 0)
        now = self.store.get(case['case_id'], cutoff='2026-01-06T00:00:00.000001Z')
        self.assertEqual(now['outcome_page']['candidate_count'], 1)

    def test_large_get_fails_with_pointer_then_returns_whole_case_on_smaller_outcome_page(self):
        case = self.save()
        limitations = ['x' * 1900 + str(index) for index in range(8)]
        for index in range(5):
            self.outcome(case['case_id'], observation={'statement': 'Synthetic large observation ' + str(index),
                                                     'evidence_ids': ['page-outcome']}, limitations=limitations)
        with self.assertRaisesRegex(ValueError, 'Case pointer: ' + case['case_id']):
            self.store.get(case['case_id'], cutoff='2026-01-07T00:00:00Z')
        small = self.store.get(case['case_id'], cutoff='2026-01-07T00:00:00Z', outcome_limit=1)
        self.assertEqual(small['outcomes'][0]['outcome']['limitations'], limitations)
        self.assertEqual(small['case']['conditions'], case['case']['conditions'])
        self.assertLessEqual(len(json.dumps(small, ensure_ascii=False)), 64000)
        for kwargs in [{'outcome_limit': 0}, {'outcome_limit': 17}, {'outcome_offset': -1},
                       {'outcome_offset': True}, {'budget_chars': 0}, {'budget_chars': 300000}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.get(case['case_id'], cutoff='2026-01-07T00:00:00Z', **kwargs)

    def test_private_original_or_derived_lineage_cannot_enter_shared_cases(self):
        self.rows['page-prices']['dataset_id'] = 'local.user_research_parameters'
        with self.assertRaisesRegex(ValueError, 'private portfolio'):
            self.save()
        self.rows['page-prices'].pop('dataset_id')
        self.rows['page-prices']['input_record_ids'] = ['private-child']
        self.rows['private-child'] = {'record_id': 'private-child', 'private': True,
                                     'retrieved_at': '2026-01-02T00:00:00Z'}
        with self.assertRaisesRegex(ValueError, 'private portfolio'):
            self.save()

    def test_observation_period_and_calculation_clocks_also_bound_outcomes(self):
        case=self.save()
        for key in ('observed_at','period_end','calculated_at'):
            with self.subTest(key=key):
                self.rows['page-outcome'][key]='2026-01-08T00:00:00Z'
                with self.assertRaisesRegex(ValueError,'unavailable'):self.outcome(case['case_id'])
                self.rows['page-outcome'].pop(key)
        self.rows['page-news']['source']={'observed_at':'2026-01-09T00:00:00Z'}
        later=self.save('2026-01-10T00:00:00Z')
        with self.assertRaisesRegex(ValueError,'not available'):
            self.store.get(later['case_id'],cutoff='2026-01-05T00:00:00Z',mode='retrospective')


if __name__ == '__main__':
    unittest.main()
