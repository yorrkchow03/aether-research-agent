from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from research_agent.working_memory import WorkingMemory


class WorkingMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.store = WorkingMemory(Path(temporary.name), redact=lambda value: str(value).replace('fixture-secret', '[REDACTED]'))
        self.store.begin('job-a', 'ETH source-based report', '2026-01-05T00:00:00Z')

    def test_state_restarts_and_same_job_cannot_be_rebound(self):
        self.store.update('job-a', todos=[{'task': 'Check original', 'status': 'pending'}], unknowns=['Missing holdings'])
        reopened = WorkingMemory(self.store.root)
        self.assertEqual(reopened.state('job-a')['unknowns'], ['Missing holdings'])
        with self.assertRaises(ValueError):
            reopened.begin('job-a', 'A different task', '2026-01-05T00:00:00Z')

    def test_large_tool_output_is_exactly_recoverable_redacted_and_not_a_fact(self):
        receipt = self.store.record_tool('job-a', 'read_evidence', {'text': 'x'*20000, 'token': 'fixture-secret'}, ['page-a'])
        restored = self.store.read_artifact('job-a', receipt['artifact_id'])
        self.assertIn('x'*20000, restored['text'])
        self.assertNotIn('fixture-secret', restored['text'])
        self.assertEqual(self.store.state('job-a')['facts'], [])
        self.assertEqual(self.store.record_tool('job-a', 'repeat', {'text': 'x'*20000, 'token': 'fixture-secret'})['artifact_id'], receipt['artifact_id'])
        self.assertEqual(len(self.store.artifacts('job-a')), 1)
        Path(receipt['path']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.store.read_artifact('job-a', receipt['artifact_id'])

    def test_parallel_identical_results_deduplicate_without_lost_provenance(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda index: self.store.record_tool('job-a', 'tool-'+str(index), {'value': 'same'}), range(5)))
        artifacts = self.store.artifacts('job-a')
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(len(artifacts[0]['tools']), 5)

    def test_notes_need_evidence_and_context_marks_truncation(self):
        with self.assertRaises(ValueError):
            self.store.update('job-a', facts=[{'statement': 'An unsupported fact'}])
        self.store.update('job-a', facts=[{'statement': 'Source-linked note', 'evidence_ids': ['page-a']}],
                          conflicts=[{'evidence_ids': ['page-a', 'page-b'], 'status': 'unresolved'}], unknowns=['long '*400])
        context = self.store.context('job-a', budget_chars=500)
        self.assertLessEqual(context['characters'], 500)
        self.assertTrue(context['truncated'])
        self.assertIn('derived', context['text'])

    def test_active_artifacts_plan_redaction_and_field_omissions_are_visible(self):
        self.store.begin('job-b','Check artifacts',plan=[{'step':'fixture-secret'}])
        self.assertNotIn('fixture-secret',str(self.store.state('job-b')))
        self.store.update('job-b',active_artifacts=['review-draft-123'],unknowns=['a'*1800])
        context=self.store.context('job-b',budget_chars=6000)
        self.assertIn('review-draft-123',context['text'])
        self.assertTrue(context['truncated'])
        self.assertEqual(context['field_truncations'],['unknowns'])

    def test_model_view_keeps_complete_tool_groups_and_full_originals(self):
        messages = [SystemMessage(content='Do not invent facts.'), HumanMessage(content='Research ETH.')]
        for index in range(5):
            messages += [AIMessage(content='', tool_calls=[{'id': 'c'+str(index), 'name': 'read', 'args': {'id': index}}]),
                         ToolMessage(content='raw '+str(index)+' '+('x'*9000), tool_call_id='c'+str(index), name='read')]
        result = self.store.compact_messages('job-a', messages, max_tool_chars=500, keep_groups=2, max_history_chars=3000)
        self.assertLess(result['after_characters'], result['before_characters'])
        self.assertTrue(result['archived_artifact_ids'])
        call_ids = set()
        for message in result['messages']:
            if message.type == 'ai':
                call_ids.update(c['id'] for c in message.tool_calls)
            if message.type == 'tool':
                self.assertIn(message.tool_call_id, call_ids)
        self.assertEqual(len(messages), 12)
        self.assertIn('x'*9000, self.store.read_artifact('job-a', self.store.artifacts('job-a', 20)[-1]['artifact_id'])['text'])


if __name__ == '__main__':unittest.main()
