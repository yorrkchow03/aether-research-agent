"""Check the lead's post-scout route and candidate-only view without a provider."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import HumanMessage, ToolMessage

from research_agent.periodic_workflow import PeriodicWorkflow
from research_agent.question_tracking import question_tool
from research_agent.report_context import ReportContextMiddleware
from research_agent.research_scout import build_research_scout
from research_agent.runtime import TraceMiddleware
from research_agent.working_memory import WorkingMemory


SCOPE = {'assets': ['ETH'], 'start': '2026-10-16T00:00:00Z', 'end': '2026-10-24T00:00:00Z'}
PUBLISHED = '2026-10-17T00:00:00Z'
IDS = [f'page-{n:024x}' for n in range(1, 5)]
CANDIDATES = [{'record_id': rid, 'title': f'Synthetic public news {index}',
               'url': f'https://example.invalid/public/{index}', 'published_at': PUBLISHED}
              for index, rid in enumerate(IDS)]
QUESTION = 'What changed in protocol rollout during the window?'


def passage_packet():
    rows = [{**candidate, 'passage_id': candidate['record_id'] + f':{index}',
             'excerpt': 'FULL-ORIGINAL-MARKER ' * 500}
            for index, candidate in enumerate([CANDIDATES[0], *CANDIDATES[:3]])]
    return {'retrieval_unit': 'passage', 'results': rows, 'gaps': [{'reason': 'synthetic'}]}


class PeriodicReaderRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.memory = WorkingMemory(self.workspace / 'memory/task-state')
        self.memory.begin('period', 'Write an ETH weekly report.', SCOPE['end'])
        self.events = []
        self.workflow = PeriodicWorkflow(
            self.memory, 'period', SCOPE, 'Write an ETH weekly report.', self.emit,
            leads={}, archive_only=True,
        )
        self.workflow._advance('discover', 'test_plan')

    def emit(self, kind, event):
        self.events.append((kind, event))

    def test_broad_parent_news_view_keeps_exact_artifact_and_unique_source_ids(self):
        original = json.dumps(passage_packet())
        trace = TraceMiddleware(self.emit, working_memory=self.memory, job_id='period',
                                workspace_root=self.workspace, periodic_workflow=self.workflow)
        request = SimpleNamespace(tool_call={'name': 'recall_memory', 'id': 'news'},
                                  state={'todos': []})
        request.tool_call['args'] = {'kind': 'news', 'query': 'public topic'}

        def dispatch(_):
            return ToolMessage(content=original, name='recall_memory', tool_call_id='news')

        result = trace.wrap_tool_call(request, dispatch)
        view = json.loads(result.content)
        self.assertEqual(view['view'], 'periodic-candidate-sources-v1')
        self.assertEqual(view['retrieved_passage_count'], 4)
        self.assertEqual([row['record_id'] for row in view['results']], IDS[:3])
        self.assertEqual(view['gap_count'], 1)
        self.assertNotIn('FULL-ORIGINAL-MARKER', result.content)
        self.assertLess(len(result.content), 4000)
        artifact = view['artifact']
        self.assertEqual(artifact['json_pointer'], '/results')
        self.assertEqual((self.workspace / artifact['path'].lstrip('/')).read_text(), original)
        self.assertEqual(self.memory.read_artifact('period', artifact['artifact_id'])['text'], original)
        self.assertEqual(self.events[-2][0], 'periodic_parent_retrieval_view')
        self.assertEqual(self.events[-2][1]['visible_characters'], len(result.content))

    def test_nonperiodic_and_empty_news_results_keep_existing_view(self):
        raw = json.dumps({'retrieval_unit': 'passage', 'results': [],
                          'gaps': [{'reason': 'archive unavailable'}]})
        self.assertIsNone(self.workflow.parent_news_view(raw, {'artifact_id': 'unused',
            'sha256': 'test', 'bytes': 20, 'path': '/tmp/test'}, self.workspace))
        trace = TraceMiddleware(self.emit, working_memory=self.memory, job_id='period')
        request = SimpleNamespace(tool_call={'name': 'recall_memory', 'id': 'standalone',
                                             'args': {'kind': 'news', 'query': 'public topic'}}, state={})
        result = trace.wrap_tool_call(request, lambda _: ToolMessage(
            content=json.dumps(passage_packet()), name='recall_memory', tool_call_id='standalone',
        ))
        self.assertIn('FULL-ORIGINAL-MARKER', result.content)
        self.assertFalse(any(name == 'periodic_parent_retrieval_view' for name, _ in self.events))

    def test_scout_uses_candidate_map_and_handoff_leaves_direct_check_available(self):
        service = SimpleNamespace(knowledge=object(), allow_live=False, working_memory=self.memory,
                                  job_id='period', news_pack=lambda **_: passage_packet())
        scout = build_research_scout(service, self.workspace, self.emit, scope=SCOPE,
                                     on_complete=self.workflow.record_discovery)
        receipt = json.loads(scout['runnable'].invoke({'messages': []})['messages'][0].content)
        path = self.workspace / receipt['next_read']['file_path'].lstrip('/')
        compact = json.loads(path.read_text())
        self.assertEqual([row['record_id'] for row in compact['result']['candidates']], IDS[:3])
        self.assertNotIn('tool_receipts', compact)
        full = (self.workspace / receipt['artifact'].lstrip('/')).read_text()
        self.assertIn('FULL-ORIGINAL-MARKER', full)

        middleware = ReportContextMiddleware(self.emit, SimpleNamespace(), max_model_calls=32,
                                              periodic_workflow=self.workflow)
        request = ModelRequest(model=FakeMessagesListChatModel(responses=[]), tools=[
            {'name': 'task'}, {'name': 'update_research_question'}, {'name': 'read_evidence'},
            {'name': 'recall_memory'},
        ], messages=[HumanMessage(content='Write the ETH weekly report.')])

        def override():
            return middleware.wrap_model_call(request, lambda result: result)

        initial = str(override().system_message.content)
        self.assertIn('open concrete questions before broad', initial)
        self.assertIn(receipt['next_read']['file_path'], initial)
        question_tool(self.memory, 'period', periodic_workflow=self.workflow).invoke(
            {'question_id': 'Q1', 'question': QUESTION, 'candidate_ids': IDS[:3]}
        )

        routed = override()
        text = str(routed.system_message.content)
        self.assertIn('task(subagent_type="evidence-reader"', text)
        self.assertIn('[Q1]', text)
        self.assertIn(QUESTION, text)
        self.assertTrue(all(rid in text for rid in IDS[:3]))
        self.assertIn({'name': 'read_evidence'}, routed.tools)
        self.assertTrue(any(kind == 'periodic_verification_handoff_offered'
                            and event.get('question_id') == 'Q1' for kind, event in self.events))

        self.memory.record_question_result('period', 'Q1', 'task',
            {'description': f'[Q1] {QUESTION}', 'subagent_type': 'evidence-reader'},
            {'status': 'error', 'error': 'fixture unavailable'}, status='error', tool_call_id='reader')
        fallback = override()
        self.assertNotIn('task(subagent_type="evidence-reader"', str(fallback.system_message.content))
        self.assertIn({'name': 'read_evidence'}, fallback.tools)

    def test_single_linked_original_does_not_trigger_delegation(self):
        self.workflow.record_discovery({'status': 'completed', 'candidates': CANDIDATES[:1],
                                        'channels': {}, 'gaps': []})
        question_tool(self.memory, 'period', periodic_workflow=self.workflow).invoke(
            {'question_id': 'Q1', 'question': QUESTION, 'candidate_ids': IDS[:1]}
        )
        self.assertIsNone(self.workflow.verification_handoff())

    def test_long_candidate_titles_keep_bounded_exact_metadata_and_omission_count(self):
        rows = [{'record_id': f'page-{index:024x}', 'title': 'ABCDE' * 48,
                 'published_at': PUBLISHED}
                for index in range(35)]
        payload = {'retrieval_unit': 'passage', 'results': rows}
        receipt = self.memory.record_tool('period', 'recall_memory', payload)
        output = self.workflow.parent_news_view(payload, receipt, self.workspace)
        view = json.loads(output)
        self.assertLessEqual(len(output), 4000)
        self.assertGreater(view['omitted_candidate_count'], 0)
        self.assertEqual(len(view['results']) + view['omitted_candidate_count'], 35)
        self.assertEqual(view['results'], rows[:len(view['results'])])


if __name__ == '__main__':
    unittest.main()
