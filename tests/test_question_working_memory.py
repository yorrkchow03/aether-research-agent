from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from langchain_core.messages import AIMessage

from research_agent.working_memory import WorkingMemory


SOURCE = 'page-' + 'a' * 24
SECOND_SOURCE = 'page-' + 'b' * 24
READER = 'c' * 32
CUTOFF = '2026-11-15T00:00:00Z'
QUESTION = 'Which upgrade schedule did the announcement specify?'
QUOTE = 'The November activation target remains provisional.'
DELEGATE = {'subagent_type': 'evidence-reader', 'description': 'Read the public upgrade announcement.'}


class ResearchQuestionMemoryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.root = self.workspace / 'memory' / 'task-state'
        self.sources = {rid: {'record_id': rid} for rid in (SOURCE, SECOND_SOURCE)}
        self.store = WorkingMemory(self.root, evidence_lookup=self.sources.__getitem__)
        self.store.begin('report-1', 'Weekly ETH report', CUTOFF)
        self.store.put_question('report-1', 'Q1', QUESTION)

    def record(self, tool, args, payload, call, *, qid='Q1', status='success'):
        return self.store.record_question_result('report-1', qid, tool, args, payload,
            tool_call_id=call, status=status)

    def reader_result(self, **fields):
        return {'reader_id': READER, 'status': 'completed', 'findings': [{
            'finding': 'The publication says the target is provisional.',
            'citations': [{'evidence_id': SOURCE, 'quote': QUOTE, 'start': 5, 'end': 5 + len(QUOTE),
                           'sha256': hashlib.sha256(QUOTE.encode()).hexdigest()}],
        }], 'gaps': [], 'conflicts': [], **fields}

    def test_reader_command_uses_complete_saved_result_and_keeps_source_qualification(self):
        result = self.reader_result(gaps=['No exact mainnet date.'], conflicts=['Later announcement may differ.'])
        directory = self.workspace / 'memory' / 'readers' / READER
        directory.mkdir(parents=True)
        (directory / 'validated-result.json').write_text(json.dumps(result))
        (directory / 'navigation.json').write_text(json.dumps({'reader_id': READER,
            'opened_sources': {SOURCE: {}, SECOND_SOURCE: {}}}))
        parent = {'messages': [AIMessage(content=json.dumps({'reader_id': READER,
            'status': 'completed', 'findings': [], 'omitted_findings': 1}))]}

        self.record('task', DELEGATE, parent, 'reader-call')
        question = WorkingMemory(self.root).get_question('report-1', 'Q1')
        self.assertEqual(question['status'], 'needs_evidence')
        self.assertEqual(question['evidence_ids'], [])
        self.assertCountEqual(question['opened_evidence_ids'], [SOURCE, SECOND_SOURCE])
        self.assertEqual(question['reader_findings'][0]['citations'][0]['quote'], QUOTE)
        self.assertTrue(question['reader_findings'][0]['file_path'].endswith('/validated-result.json'))
        self.assertEqual(question['conflicts'][0]['description'], result['conflicts'][0])
        self.assertIn('No exact mainnet date.', question['gaps'])
        self.assertEqual(question['attempts'][0]['outcome'], 'evidence')

        view = self.store.context('report-1', budget_chars=4000)
        self.assertIn('Q1', view['text'])
        self.assertIn(SOURCE, view['text'])
        self.assertIn(result['findings'][0]['finding'], view['text'])
        self.assertIn('Later announcement may differ.', view['text'])
        saved = json.loads(Path(view['state_file']).read_text())
        self.assertEqual(saved['research_state']['questions'], [question])
        self.assertEqual(saved['facts'], [])

    def test_only_explicit_sourced_assessment_marks_ready_and_a_new_gap_reopens(self):
        self.record('task', DELEGATE, self.reader_result(), 'reader-call')
        self.assertEqual(self.store.get_question('report-1', 'Q1')['status'], 'open')
        with self.assertRaisesRegex(ValueError, 'sourced conclusion'):
            self.store.put_question('report-1', 'Q1', None, status='ready')
        with self.assertRaisesRegex(ValueError, 'unavailable source'):
            self.store.put_question('report-1', 'Q1', None, status='ready',
                conclusion=QUOTE, evidence_ids=['page-' + 'f' * 24])
        ready = self.store.put_question('report-1', 'Q1', None, status='ready',
            conclusion=QUOTE, evidence_ids=[SOURCE], gaps=[])
        self.assertEqual(ready['status'], 'ready')
        self.record('recall_memory', {'kind': 'news', 'query': 'later mainnet date'},
            {'results': [], 'gaps': [{'reason': 'Later mainnet release not found.'}]}, 'recall')
        self.assertEqual(self.store.get_question('report-1', 'Q1')['status'], 'needs_evidence')
        ready = self.store.put_question('report-1', 'Q1', None, status='ready',
            conclusion=QUOTE, evidence_ids=[SOURCE], gaps=[])
        self.assertEqual(ready['status'], 'ready')
        duplicate = self.record('recall_memory', {'kind': 'news', 'query': 'later mainnet date'},
            {'results': [], 'gaps': [{'reason': 'Later mainnet release not found.'}]}, 'recall')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['question_status'], 'ready')

    def test_candidate_cannot_mark_ready_but_job_shared_read_can(self):
        self.record('recall_memory', {'kind': 'news', 'query': 'upgrade'},
            {'results': [{'record_id': SOURCE}]}, 'candidate')
        with self.assertRaisesRegex(ValueError, 'Read or register'):
            self.store.put_question('report-1', 'Q1', None, status='ready',
                conclusion=QUOTE, evidence_ids=[SOURCE])
        self.store.observe_tool('report-1', 'read_evidence', {'record': {'record_id': SOURCE}}, tool_call_id='shared')
        assessment = self.store.put_question('report-1', 'Q1', None, status='ready',
            conclusion=QUOTE, evidence_ids=[SOURCE])
        self.assertEqual(assessment['status'], 'ready')

    def test_actual_main_results_preserve_candidate_open_and_query_attempt_separately(self):
        self.record('recall_memory', {'kind': 'news', 'query': 'upgrade'},
            {'results': [{'record_id': SOURCE}]}, 'candidate')
        read = json.dumps([{'type': 'text', 'text': json.dumps({'record': {'record_id': SECOND_SOURCE}})}])
        self.record('read_evidence', {'record_id': SECOND_SOURCE}, read, 'opened')
        self.record('execute', {'command': 'synthetic'}, self.reader_result(), 'untrusted-result')

        question = self.store.get_question('report-1', 'Q1')
        self.assertEqual(question['candidate_evidence_ids'], [SOURCE])
        self.assertEqual(question['opened_evidence_ids'], [SECOND_SOURCE])
        self.assertEqual(question['evidence_ids'], [])
        self.assertFalse(question['reader_findings'])
        self.assertEqual([attempt['outcome'] for attempt in question['attempts']], ['evidence', 'evidence', 'unknown'])
        self.assertIn('upgrade', question['attempts'][0]['label'])

    def test_duplicate_concurrent_results_and_cross_question_reuse_are_consistent(self):
        self.store.put_question('report-1', 'Q2', 'Does another source establish a date?')
        args = {'kind': 'news', 'query': 'new release date'}
        payload = {'results': []}
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.record('recall_memory', args, payload, 'one-call'), range(12)))
        self.assertEqual(sum(not row['duplicate'] for row in results), 1)
        self.assertEqual(len(self.store.get_question('report-1', 'Q1')['attempts']), 1)
        with self.assertRaisesRegex(ValueError, 'rebound'):
            self.record('recall_memory', args, payload, 'one-call', qid='Q2')
        self.assertFalse(self.store.get_question('report-1', 'Q2')['attempts'])
        with self.assertRaisesRegex(ValueError, 'different question'):
            self.store.put_question('report-1', 'Q1', 'Changed question')

    def test_explicit_same_scope_continuation_preserves_provenance_and_old_revision(self):
        self.record('read_evidence', {'record_id': SOURCE}, {'record': {'record_id': SOURCE}}, 'read-1')
        before = self.store.state('report-1')
        self.store.begin('report-next', 'Weekly ETH report', CUTOFF)
        self.assertEqual(self.store.carry_questions('report-1', 'report-next'), 1)
        self.assertEqual(self.store.carry_questions('report-1', 'report-next'), 0)
        carried = self.store.get_question('report-next', 'Q1')
        self.assertEqual(carried['carried_from_job_id'], 'report-1')
        self.assertEqual(carried['observations'][0]['job_id'], 'report-1')
        self.assertEqual(carried['attempts'], before['research_state']['questions'][0]['attempts'])
        self.store.record_question_result('report-next', 'Q1', 'read_evidence', {'record_id': SECOND_SOURCE},
            {'record': {'record_id': SECOND_SOURCE}}, tool_call_id='read-1')
        self.assertEqual(self.store.state('report-1'), before)
        self.store.begin('other-scope', 'Weekly ETH report', '2026-11-16T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'different task'):
            self.store.carry_questions('report-1', 'other-scope')


if __name__ == '__main__':
    unittest.main()
