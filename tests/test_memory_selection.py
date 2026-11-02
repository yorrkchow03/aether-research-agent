"""Frozen synthetic selector cases; these are not independent market events."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_agent.memory_selection import select_evidence, context_cost
from research_agent.knowledge import KnowledgeStore

AS_OF = '2026-02-01T00:00:00Z'


def item(rid, text, url=None, first_seen='2026-01-01T00:00:00Z'):
    return {'record_id': rid, 'title': 'Ethereum research', 'excerpt': text,
            'first_seen_at': first_seen, 'url': url or 'https://example.org/' + rid}


class MemorySelectionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [item('a', 'Ethereum upgrade planned; execution testing remains incomplete.'),
                     item('duplicate', 'Ethereum upgrade planned; execution testing remains incomplete.'),
                     item('b', 'Ethereum upgrade transaction fees and execution costs remain uncertain.'),
                     item('c', 'Ethereum upgrade postponed; the revised announcement corrects the old plan.')]

    def test_context_budget_is_never_exceeded_for_any_policy(self):
        budget = context_cost(self.rows[0]) + context_cost(self.rows[2])
        for policy in ('coverage', 'relevance', 'mmr', 'recent'):
            with self.subTest(policy=policy):
                result = select_evidence(self.rows, 'Ethereum upgrade', budget=budget,
                                         policy=policy, as_of=AS_OF)
                self.assertLessEqual(sum(context_cost(row) for row in result['results']), budget)
                self.assertEqual(sum(context_cost(row) for row in result['results']), result['selection']['cost_estimate'])

    def test_coverage_does_not_spend_second_slot_on_an_exact_restatement(self):
        result = select_evidence(self.rows[:3], 'Ethereum upgrade execution costs', limit=2, as_of=AS_OF)
        texts = {row['excerpt'] for row in result['results']}
        self.assertEqual(len(texts), 2)

    def test_linked_correction_is_packed_with_the_selected_old_evidence(self):
        result = select_evidence(self.rows, 'execution testing', limit=3, as_of=AS_OF,
                                 dependencies={'a': ['c'], 'duplicate': ['c']})
        ids = {row['record_id'] for row in result['results']}
        self.assertTrue({'a', 'duplicate'}.intersection(ids))
        self.assertIn('c', ids)

    def test_parent_is_excluded_if_its_required_correction_does_not_fit(self):
        result = select_evidence(self.rows, 'execution testing', limit=1, as_of=AS_OF,
                                 dependencies={'a': ['c'], 'duplicate': ['c']})
        self.assertFalse({'a', 'duplicate'}.intersection(row['record_id'] for row in result['results']))

    def test_unavailable_dependency_does_not_allow_an_incomplete_bundle(self):
        result = select_evidence([self.rows[0]], 'Ethereum', as_of=AS_OF,
                                 dependencies={'a': ['missing']})
        self.assertEqual(result['results'], [])
        self.assertEqual(result['selection']['unavailable_dependency_count'], 1)

    def test_dependency_cycles_terminate_with_unique_evidence(self):
        result = select_evidence([self.rows[0], self.rows[2]], 'Ethereum', as_of=AS_OF,
                                 dependencies={'a': ['b'], 'b': ['a']})
        self.assertEqual({row['record_id'] for row in result['results']}, {'a', 'b'})
        self.assertEqual(len(result['results']), 2)

    def test_future_capture_is_excluded_despite_old_publication_time(self):
        row = item('future', 'Ethereum future correction', first_seen='2026-02-01T00:00:00.000001Z')
        row['published_at'] = '2025-01-01T00:00:00Z'
        result = select_evidence([row], 'Ethereum', as_of=AS_OF)
        self.assertEqual(result['results'], [])
        self.assertEqual(result['selection']['excluded'][0]['reason'], 'not_known_by_cutoff')

    def test_frozen_selection_does_not_guess_an_unknown_capture_time(self):
        row = item('unknown', 'Ethereum upgrade'); row.pop('first_seen_at')
        result = select_evidence([row], 'Ethereum', as_of=AS_OF)
        self.assertEqual(result['results'], [])

    def test_future_publication_or_invalid_timestamp_is_not_used(self):
        for value in ('2026-03-01T00:00:00Z', 'not a timestamp'):
            with self.subTest(value=value):
                row = deepcopy(self.rows[0]); row['published_at'] = value
                self.assertEqual(select_evidence([row], 'Ethereum', as_of=AS_OF)['results'], [])

    def test_repeated_record_ids_cannot_hide_mutated_evidence(self):
        duplicate = {**self.rows[0], 'excerpt': 'Different facts under the same ID.'}
        with self.assertRaises(ValueError):
            select_evidence([self.rows[0], duplicate], 'Ethereum', as_of=AS_OF)

    def test_identical_repeated_record_is_counted_once(self):
        result = select_evidence([self.rows[0], self.rows[0]], 'Ethereum', as_of=AS_OF)
        self.assertEqual(len(result['results']), 1)

    def test_no_query_overlap_returns_a_gap_instead_of_random_evidence(self):
        result = select_evidence(self.rows, 'zzzzqqqq', as_of=AS_OF)
        self.assertEqual(result['results'], [])
        self.assertEqual(result['selection']['reason'], 'no_lexical_evidence_match')

    def test_tiny_budget_returns_no_partial_record(self):
        result = select_evidence(self.rows, 'Ethereum', budget=1, as_of=AS_OF)
        self.assertEqual(result['results'], [])

    def test_cost_and_method_are_explicitly_estimated_not_provider_token_usage(self):
        result = select_evidence(self.rows, 'Ethereum', as_of=AS_OF)
        self.assertIn('not provider tokens', result['selection']['cost_unit'])
        self.assertIn('do not prove truth', result['selection']['scope'])

    def test_identical_inputs_give_identical_selected_records_and_trace(self):
        self.assertEqual(select_evidence(self.rows, 'Ethereum', as_of=AS_OF),
                         select_evidence(deepcopy(self.rows), 'Ethereum', as_of=AS_OF))

    def test_input_bounds_and_policy_are_checked(self):
        for kwargs in ({'limit': 0}, {'budget': 0}, {'policy': 'invented'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                select_evidence(self.rows, **kwargs)
        with self.assertRaises(ValueError):
            select_evidence([item(str(i), 'text') for i in range(151)])

    def test_store_expands_explicit_reviewed_event_updates_before_packing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(Path(tmp) / 'knowledge')
            old_id = 'page-' + 'a' * 24; new_id = 'page-' + 'b' * 24
            for rid, text, date, url in [
                (old_id, 'Ethereum upgrade testing initially planned.', '2026-01-01T00:00:00Z', 'https://example.org/old'),
                (new_id, 'Ethereum official status now postponed.', '2026-01-10T00:00:00Z', 'https://example.org/new')]:
                store.register({'record_id': rid, 'title': text, 'text': text, 'url': url,
                                'retrieved_at': date, 'source': {'url': url}})
            event = {'title': 'Ethereum upgrade', 'summary': 'Initial plan.', 'asset': 'ETH',
                     'status': 'planned', 'evidence_ids': [old_id], 'review_id': 'synthetic-review'}
            with patch('research_agent.knowledge.utcnow', return_value='2026-01-02T00:00:00Z'):
                first = store.record_event(event, store.get)
            event.update(event_id=first['event_id'], summary='Delayed.', status='delayed', evidence_ids=[new_id])
            with patch('research_agent.knowledge.utcnow', return_value='2026-01-11T00:00:00Z'):
                store.record_event(event, store.get)
            result = store.select_news('testing', cutoff=AS_OF, budget=6000)
            self.assertEqual({row['record_id'] for row in result['results']}, {old_id, new_id})
            earlier = store.select_news('testing', cutoff='2026-01-05T00:00:00Z', budget=6000)
            self.assertEqual([row['record_id'] for row in earlier['results']], [old_id])
            self.assertEqual(len(store.event_history('ALL', AS_OF)), 2)

            # An older matching event must not fall out of the recent timeline.
            with store.connect() as c:
                for i in range(155):
                    other = {**event, 'event_id': format(i, '024x'), 'version': 1,
                             'available_at': '2026-01-25T00:00:00Z'}
                    import json
                    c.execute('INSERT INTO events VALUES(?,?,?,?)',
                              (other['event_id'], 1, json.dumps(other), other['available_at']))
            self.assertEqual({row['record_id'] for row in store.select_news('testing', cutoff=AS_OF, budget=6000)['results']},
                             {old_id, new_id})


if __name__ == '__main__':
    unittest.main()
