"""Research reasoning is recallable context, never a new factual source."""
from pathlib import Path
import tempfile
import unittest

from research_agent.knowledge import KnowledgeStore
from research_agent.answers import AnswerDraft


class ResearchMemoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = KnowledgeStore(Path(tmp.name))
        self.answer = {'answer_id': 'answer-synthetic', 'created_at': '2026-01-02T00:00:00Z',
            'draft': AnswerDraft(facts=[{'statement': 'Synthetic ETH observation.',
                'evidence_ids': ['page-'+'a'*24]}]).model_dump(), 'review': {'status': 'reviewed'}}

    def test_reviewed_answer_recall_preserves_original_evidence_and_is_not_a_fact_record(self):
        self.store.retain_answer('answer-synthetic', 'session-a', self.answer)
        rows = self.store.search_research('ETH')
        self.assertEqual(rows[0]['source_evidence_ids'], ['page-'+'a'*24])
        self.assertEqual(rows[0]['kind'], 'answer')
        self.assertIn('not a new factual source', rows[0]['scope'])
        self.assertFalse(self.store.contains('answer-synthetic'))
        self.assertEqual(self.store.search_reports(), [])

    def test_unreviewed_answer_and_identity_mutation_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.retain_answer('answer-synthetic', 's', {**self.answer, 'review': {'status': 'failed'}})
        self.store.retain_answer('answer-synthetic', 's', self.answer)
        with self.assertRaises(ValueError):
            self.store.retain_answer('answer-synthetic', 's', {**self.answer, 'created_at': '2026-01-03T00:00:00Z'})

    def test_reports_and_answers_share_bounded_date_order_without_changing_report_list(self):
        self.store.retain_answer('answer-synthetic', 'a', self.answer)
        self.store.retain_report('r', 'b', {'created_at': '2026-01-01T00:00:00Z',
            'report': {'title': 'ETH older report', 'summary': 'ETH source context'}})
        self.assertEqual([r['kind'] for r in self.store.search_research('ETH')], ['answer', 'report'])
        self.assertEqual(len(self.store.search_research('ETH', 1)), 1)
        self.assertEqual(self.store.search_research('nonmatching'), [])
        self.assertEqual(len(self.store.search_reports()), 1)
        with self.assertRaises(ValueError):self.store.search_research(limit=0)


if __name__ == '__main__':unittest.main()
