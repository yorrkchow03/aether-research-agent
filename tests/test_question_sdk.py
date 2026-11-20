"""Offline socket exercises for terminal calls scoped to a research question."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_agent.research_sdk import ResearchClient
from research_agent.sdk_bridge import ResearchSDKServer
from research_agent.settings import Settings
from research_agent.tools import ResearchTools
from research_agent.working_memory import WorkingMemory
from research_data.library import ResearchLibrary


class QuestionSDKTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        run = root / 'run'
        run.mkdir()
        (run / 'manifest.json').write_text(json.dumps({'datasets': [], 'sources': []}))
        library = ResearchLibrary(root / 'library')
        self.page = self.archive_page(library, 'first', 'The activation was proposed.')
        self.other = self.archive_page(library, 'second', 'The activation was delayed.')
        library.register_dataset('market.fixture', provider='fixture', frequency='1d',
            scope='synthetic archive', asset='ETH', units={'count': 'count'})
        capture = library.capture('fixture', 'https://example.invalid/count', b'{"count":7}',
                                  retrieved_at='2026-10-02')
        self.market = library.put('market.fixture', '2026-10-01', {'count': 7}, capture)

        self.events = []
        self.memory = WorkingMemory(root / 'memory' / 'task-state')
        self.memory.begin('socket-job', 'Trace an archived activation')
        self.memory.put_question('socket-job', 'Q1', 'What date did the proposal name?')
        self.memory.put_question('socket-job', 'Q2', 'What did the later announcement say?')
        self.service = ResearchTools(run, root / 'workspace', '2026-10-04T00:00:00Z',
            allow_live=True, library=library, event_sink=lambda kind, row: self.events.append((kind, row)))
        self.service.working_memory = self.memory
        self.service.job_id = 'socket-job'
        server = ResearchSDKServer(self.service, Settings())
        self.addCleanup(server.close)
        self.client = ResearchClient(server.start())

    @staticmethod
    def archive_page(library, name, body):
        url = 'https://example.invalid/' + name
        capture = library.capture('fixture', url, body.encode(), retrieved_at='2026-10-02')
        return library.put_document('fixture', url, name, body, capture, published_at='2026-10-01')

    def read(self, **fields):
        return self.client.call('library-evidence', record_id=fields.pop('record_id', self.page), **fields)

    def question(self, question_id='Q1'):
        return self.memory.get_question('socket-job', question_id)

    def global_observations(self):
        return self.memory.state('socket-job')['research_state']['observations']

    def test_repeated_archive_reads_skip_only_the_same_question_and_arguments(self):
        for _ in range(2):
            self.assertEqual(self.read(question_id='Q1')['record']['record_id'], self.page)
        blocked = self.read(question_id='Q1')
        self.assertEqual((blocked['status'], blocked['reason']), ('no_progress', 'repeated_evidence'))
        self.assertEqual(self.question()['opened_evidence_ids'], [self.page])
        self.assertEqual(len(self.question()['attempts']), 2)
        self.assertEqual(len(self.global_observations()), 2)

        self.assertEqual(self.read(question_id='Q2')['record']['record_id'], self.page)
        self.assertEqual(self.read(question_id='Q1', record_id=self.other)['record']['record_id'], self.other)
        self.assertEqual(self.read()['record']['record_id'], self.page)
        self.assertEqual(self.read(question_id='shared')['record']['record_id'], self.page)
        self.assertEqual([row['status'] for kind, row in self.events if kind == 'tool_finished'],
                         ['success', 'success', 'skipped', 'success', 'success', 'success', 'success'])
        self.assertEqual(len(self.question()['attempts']), 3)
        self.assertEqual(len(self.question('Q2')['attempts']), 1)
        self.assertEqual(len(self.global_observations()), 6)

    def test_real_errors_are_observed_and_failed_inputs_can_change(self):
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'Invalid evidence ID'):
                self.read(question_id='Q1', record_id='bad id')
        self.assertEqual(self.read(question_id='Q1', record_id='bad id')['reason'], 'repeated_error')
        self.assertEqual(self.read(question_id='Q1')['record']['record_id'], self.page)
        self.assertEqual([row['outcome'] for row in self.question()['attempts']], ['error', 'error', 'evidence'])
        self.assertEqual([row['status'] for row in self.global_observations()], ['error', 'error', 'success'])

    def test_question_receipt_failure_preserves_global_observation_and_source_response(self):
        with patch.object(self.memory, 'record_question_result', side_effect=OSError), self.subTest('scoped fails'):
            self.assertEqual(self.read(question_id='Q1')['record']['record_id'], self.page)
        self.assertEqual(len(self.global_observations()), 1)
        self.assertEqual(self.question()['observations'], [])
        with patch.object(self.memory, 'observe_tool', side_effect=OSError), self.subTest('global fails'):
            self.assertEqual(self.read(question_id='Q1')['record']['record_id'], self.page)
        self.assertEqual(len(self.question()['observations']), 1)

    def test_source_binding_does_not_extend_sdk_note_operations(self):
        with self.assertRaisesRegex(ValueError, 'only with SDK source operations'):
            self.client.call('memory-note', question_id='Q1', statement='Save a question', kind='question')
        with self.assertRaisesRegex(ValueError, 'only with SDK source operations'):
            self.client.call('memory-note', question_id=None, statement='Save a question', kind='question')
        with self.assertRaisesRegex(ValueError, 'Unknown research question'):
            self.read(question_id='Q3')
        self.assertFalse(self.question()['observations'])
        self.assertFalse(self.memory.state('socket-job')['research_state']['derived_notes'])

    def test_scoped_numeric_series_records_real_reads_without_suppressing_them(self):
        for _ in range(3):
            result = self.client.call('library-series', question_id='Q1', dataset_id='market.fixture')
            self.assertEqual(result['records'][0]['record_id'], self.market)
        self.assertEqual(len(self.question()['observations']), 3)
        self.assertTrue(all(row['tool'] == 'sdk:library-series' for row in self.question()['observations']))
        self.assertFalse(any(kind == 'question_no_progress' for kind, _ in self.events))


if __name__ == '__main__':
    unittest.main()
