"""Exercise the discovery-first report gates against real task and source memory."""

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from fixtures import project_fixture
from research_agent.knowledge import KnowledgeStore
from research_agent.periodic_workflow import PERIOD_STAGES, PeriodicWorkflow
from research_agent.question_tracking import question_tool
from research_agent.report_context import report_checkpoint_tool
from research_agent.research_leads import research_leads_tool
from research_agent.research_scout import build_research_scout
from research_agent.runtime import AgentRuntime, TraceMiddleware
from research_agent.tools import ResearchTools
from research_agent.working_memory import WorkingMemory


SCOPE = {'assets': ['ETH'], 'start': '2025-01-01T00:00:00Z', 'end': '2025-02-01T00:00:00Z'}
SOURCE = 'page-' + 'a' * 24
FAKE = 'page-' + 'b' * 24


def stages(index):
    return {'todos': [{'content': stage, 'status': 'completed' if n < index else 'in_progress' if n == index else 'pending'}
                      for n, stage in enumerate(PERIOD_STAGES)]}


class PeriodicWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source_root = self.root / 'run'
        source_root.mkdir()
        (source_root / 'manifest.json').write_text(json.dumps({'datasets': [], 'sources': []}))
        self.workspace = self.root / 'workspace'
        self.knowledge = KnowledgeStore(self.root / 'knowledge')
        text = 'Ethereum published a public testnet update addressing activation timing.'
        raw = self.root / 'archived-page.txt'
        raw.write_text(text)
        url = 'https://example.invalid/ethereum-testnet-update'
        self.knowledge.register({'record_id': SOURCE, 'title': 'Ethereum testnet update',
            'dataset_id': 'fixture.original', 'text': text, 'url': url, 'published_at': '2025-01-11T00:00:00Z',
            'retrieved_at': '2025-02-01T00:00:00Z', 'source': {'source_id': SOURCE, 'url': url,
            'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}}, raw)
        self.events = []
        self.dispatched = []
        self.call_count = 0
        self.todos = []
        self.service = ResearchTools(source_root, self.workspace, '2025-02-02T00:00:00Z', allow_live=True,
            knowledge=self.knowledge, event_sink=self.emit)
        self.service.require_report_scope = True
        self.service.required_report_window = [SCOPE['start'], SCOPE['end']]
        self.memory = WorkingMemory(self.workspace / 'memory' / 'task-state', evidence_lookup=self.service.lookup)
        self.memory.begin('period-job', 'Research the January ETH testnet status', SCOPE['end'])
        self.service.job_id = 'period-job'
        self.service.working_memory = self.memory
        self.leads = {'user_leads': [], 'questions_to_watch': [], 'previous_report': None}
        self.workflow = PeriodicWorkflow(self.memory, 'period-job', SCOPE,
            'Write an Ethereum period report about testnet activation.', self.emit, leads=self.leads, archive_only=True)
        self.trace = TraceMiddleware(self.emit, working_memory=self.memory, job_id='period-job',
            workspace_root=self.workspace, periodic_workflow=self.workflow)
        self.scout = build_research_scout(self.service, self.workspace, self.emit,
            scope=SCOPE, allow_web=False, on_complete=self.workflow.record_discovery)
        self.tools = {item.name: item for item in self.service.as_tools()}
        self.tools.update(get_research_leads=research_leads_tool(self.leads),
            update_research_question=question_tool(self.memory, 'period-job', periodic_workflow=self.workflow),
            save_report_draft=report_checkpoint_tool(self.service))

    def emit(self, kind, row):
        self.events.append((kind, row))

    def request(self, name, args=None):
        self.call_count += 1
        return SimpleNamespace(tool_call={'name': name, 'args': args or {}, 'id': f'tool-{self.call_count}'},
                               state={'todos': self.todos})

    def dispatch(self, request):
        call = request.tool_call
        name, args, call_id = call['name'], call['args'], call['id']
        self.dispatched.append(name)
        if name == 'write_todos':
            self.todos = args['todos']
            return Command(update={'todos': self.todos, 'messages': [ToolMessage(content='Plan saved.',
                             name=name, tool_call_id=call_id)]})
        if name == 'task':
            response = self.scout['runnable'].invoke({'messages': [HumanMessage(content=args['description'])]})
            return Command(update={'messages': [ToolMessage(content=response['messages'][0].content,
                             name=name, tool_call_id=call_id)]})
        return ToolMessage(content=self.tools[name].invoke(args), name=name, tool_call_id=call_id)

    def turn(self):
        self.workflow.wrap_model_call(SimpleNamespace(state={'todos': self.todos}), lambda _: None)

    def call(self, name, args=None, *, same_turn=False, dispatch=None):
        if not same_turn:
            self.turn()
        request = self.request(name, args)
        return self.trace.wrap_tool_call(request, lambda current: self.workflow.wrap_tool_call(current, dispatch or self.dispatch))

    def blocked(self, name, contains, args=None, *, same_turn=False):
        dispatched = list(self.dispatched)
        result = self.call(name, args, same_turn=same_turn)
        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, 'error')
        self.assertIn(contains, result.content)
        self.assertEqual(self.dispatched, dispatched)

    def scout_and_open(self):
        self.call('get_research_leads')
        self.call('write_todos', stages(0))
        self.call('task', {'subagent_type': 'research-scout', 'description': 'Map January public sources'})
        self.assertEqual(self.workflow.phase(), 'verify')
        return self.call('update_research_question', {'question_id': 'Q1', 'question': 'What did the testnet update say?',
                                                       'candidate_ids': [SOURCE]})

    def test_async_sibling_cannot_save_plan_after_leads_within_same_model_turn(self):
        async def handler(request):
            return self.dispatch(request)

        async def model(request):
            return None

        async def batched():
            await self.workflow.awrap_model_call(SimpleNamespace(state={}), model)
            read = asyncio.Event()

            async def leads_call():
                try:
                    return await self.workflow.awrap_tool_call(self.request('get_research_leads'), handler)
                finally:
                    read.set()

            async def plan_sibling():
                # Force the sibling to be evaluated after the tool has persisted
                # leads, while both still belong to the same model response.
                await read.wait()
                return await self.workflow.awrap_tool_call(self.request('write_todos', stages(0)), handler)

            return await asyncio.gather(leads_call(), plan_sibling())

        lead_result, sibling = asyncio.run(batched())
        self.assertEqual(lead_result.status, 'success')
        self.assertIn('leads', self.memory.state('period-job')['periodic_workflow'])
        self.assertEqual(sibling.status, 'error')
        self.assertIn('get_research_leads first', sibling.content)
        self.assertEqual(self.dispatched, ['get_research_leads'])
        self.assertEqual(self.workflow.phase(), 'prepare')
        self.assertIsInstance(self.call('write_todos', stages(0)), Command)
        self.assertEqual(self.workflow.phase(), 'discover')

    def test_exact_outer_plan_and_actual_scout_receipt_are_required(self):
        opening = {'question_id': 'Q1', 'question': 'Check the activation notice.', 'candidate_ids': [SOURCE]}
        self.blocked('update_research_question', 'save the four-phase plan', opening)
        self.blocked('publish_report', 'save the four-phase plan', {'draft': {}})
        self.call('get_research_leads')
        extra = stages(0)
        extra['todos'].append({'content': '[Q1] Invent a topic', 'status': 'pending'})
        self.blocked('write_todos', 'four exact phase names', extra)
        self.assertFalse(self.memory.state('period-job')['research_state'].get('questions'))
        self.call('write_todos', stages(0))
        self.blocked('update_research_question', 'discovery receipt', opening)

        def no_receipt(request):
            return ToolMessage(content='The model claims discovery is complete.', name='task',
                               tool_call_id=request.tool_call['id'])

        delegation = {'subagent_type': 'research-scout', 'description': 'Survey the public archive'}
        self.call('task', delegation, dispatch=no_receipt)
        self.assertEqual(self.workflow.phase(), 'discover')
        self.assertNotIn('discovery', self.memory.state('period-job')['periodic_workflow'])
        self.blocked('update_research_question', 'discovery receipt', opening)
        self.call('task', delegation)
        self.assertEqual(self.workflow.phase(), 'verify')
        self.blocked('update_research_question', 'discovery receipt', opening, same_turn=True)
        receipt = self.memory.state('period-job')['periodic_workflow']['discovery']
        self.assertEqual([row['record_id'] for row in receipt['candidates']], [SOURCE])
        self.assertTrue((self.workspace / receipt['artifact'].lstrip('/')).is_file())

        failed = self.call('update_research_question', {**opening, 'candidate_ids': [FAKE]})
        self.assertEqual(failed.status, 'error')
        self.assertIn('actual source receipt', failed.content)
        self.assertFalse(self.memory.state('period-job')['research_state'].get('questions'))
        valid = self.call('update_research_question', opening)
        self.assertEqual(valid.status, 'success')
        self.assertEqual(json.loads(valid.content)['origin'], {'kind': 'current_retrieval', 'candidate_ids': [SOURCE]})

    def test_candidate_only_cannot_authorize_drafting_or_same_turn_publication(self):
        self.assertEqual(self.scout_and_open().status, 'success')
        self.call('write_todos', stages(1))
        draft = {'title': 'ETH January evidence note', 'summary': 'An archived testnet update was published.',
                 'scope': SCOPE, 'observations': [{'statement': 'Ethereum published a testnet update.',
                                                    'evidence_ids': [SOURCE]}],
                 'limitations': ['One archived source is covered.']}
        self.assertFalse(self.workflow.has_verified_evidence())
        self.blocked('write_todos', 'Drafting needs an original', stages(2))
        self.blocked('save_report_draft', 'Verify an original', {'draft': draft})
        self.blocked('publish_saved_report', 'Save a substantive draft', {'draft_id': 'a' * 24})

        shared = self.call('read_evidence', {'question_id': 'shared', 'record_id': SOURCE, 'query': 'activation timing'})
        self.assertEqual(shared.status, 'success')
        self.assertFalse(self.workflow.has_verified_evidence())
        read = self.call('read_evidence', {'question_id': 'Q1', 'record_id': SOURCE, 'query': 'activation timing'})
        self.assertEqual(read.status, 'success', read.content)
        self.assertEqual(self.memory.get_question('period-job', 'Q1')['opened_evidence_ids'], [SOURCE])
        self.assertTrue(self.workflow.has_verified_evidence())
        self.blocked('write_todos', 'Drafting needs an original', stages(2), same_turn=True)
        self.assertIsInstance(self.call('write_todos', stages(2)), Command)
        self.assertEqual(self.workflow.phase(), 'draft')
        saved = self.call('save_report_draft', {'draft': draft})
        self.assertEqual(saved.status, 'success', saved.content)
        draft_id = json.loads(saved.content)['draft_id']
        self.assertTrue((self.workspace / 'drafts' / f'{draft_id}.json').is_file())
        self.assertEqual(self.workflow.phase(), 'review')
        self.assertIn(('periodic_stage_changed', 'draft_saved'),
                      [(kind, row.get('trigger')) for kind, row in self.events])
        self.blocked('publish_saved_report', 'Save a substantive draft', {'draft_id': draft_id}, same_turn=True)

    def test_opening_an_actual_search_excerpt_does_not_authorize_drafting(self):
        annotation = {'url': 'https://example.invalid/provider-only-ethereum-update',
            'title': 'Ethereum search candidate', 'publish_time': '2025-01-15T00:00:00Z',
            'summary': 'Search excerpt about Ethereum.'}
        raw = json.dumps(annotation).encode()
        transport = Mock(return_value={'items': [annotation], 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
            'retrieved_at': '2025-02-02T00:00:00Z', 'usage': {}, 'provider_status': 'completed',
            'note': 'Provider annotation only.'})
        self.service.web_search = SimpleNamespace(search=transport, settings=SimpleNamespace(model_id='offline-provider'))
        self.workflow.archive_only = False
        self.scout = build_research_scout(self.service, self.workspace, self.emit,
            scope=SCOPE, on_complete=self.workflow.record_discovery)
        self.call('get_research_leads')
        self.call('write_todos', stages(0))
        self.call('task', {'subagent_type': 'research-scout', 'description': 'Survey the scoped source channels.'})
        transport.assert_called_once()
        receipt = self.memory.state('period-job')['periodic_workflow']['discovery']
        excerpt = next(row['record_id'] for row in receipt['candidates'] if row['channel'] == 'web')
        self.assertTrue(excerpt.startswith('web-'))
        self.assertEqual(self.call('update_research_question', {'question_id': 'Q1',
            'question': 'Find the original behind the Ethereum search result.', 'candidate_ids': [excerpt]}).status,
            'success')
        self.call('write_todos', stages(1))

        read = self.call('read_evidence', {'question_id': 'Q1', 'record_id': excerpt})
        self.assertEqual(read.status, 'success', read.content)
        self.assertEqual(json.loads(read.content)['record']['record_id'], excerpt)
        self.assertEqual(self.memory.get_question('period-job', 'Q1')['opened_evidence_ids'], [excerpt])
        self.assertFalse(self.workflow.has_verified_evidence())
        self.blocked('write_todos', 'Drafting needs an original', stages(2))
        self.blocked('save_report_draft', 'Verify an original', {'draft': {}})

    def test_scheduled_report_keeps_exact_timestamp_and_relative_windows(self):
        runtime = AgentRuntime(project_fixture(self.root / 'scheduled-project'))
        runtime.settings = replace(runtime.settings, api_key='offline-fixture', memory_enabled=True)
        created_at = '2025-02-01T12:34:56Z'
        cases = (
            ('Write an ETH report for 2025-01-04T05:06:07+08:00 through '
             '2025-01-29T14:15:16.123456-05:00, with an exclusive end.',
             '2025-01-03T21:06:07Z', '2025-01-29T19:15:16.123456Z'),
            ('请写一份近30天的以太坊研究月报。', '2025-01-02T00:00:00Z', '2025-02-01T00:00:00Z'),
            ('Write an ETH research report for the last 7 complete days.',
             '2025-01-25T00:00:00Z', '2025-02-01T00:00:00Z'),
        )
        with patch('research_agent.runtime.threading.Thread'), \
                patch('research_agent.runtime.utcnow', return_value=created_at):
            for index, (prompt, start, end) in enumerate(cases):
                with self.subTest(prompt=prompt):
                    session = runtime.create_session(model_mode='ark_plan', research_mode='current')['session_id']
                    schedule_id = f'schedule-{index}'
                    job = runtime.start(session, prompt, 'report', schedule_run_id=schedule_id)
                    status_path = runtime._session_dir(session) / 'jobs' / job['job_id'] / 'status.json'
                    status = json.loads(status_path.read_text())
                    self.assertEqual(status['schedule_run_id'], schedule_id)
                    self.assertEqual(status['periodic_scope'], {'assets': ['ETH'], 'start': start, 'end': end})
                    self.assertEqual(status['required_report_window'], [start, end])
                    self.assertEqual(status['report_window_reference_at'], created_at)

    def test_continuation_keeps_original_scope_and_frozen_leads(self):
        root = project_fixture(self.root / 'runtime-project')
        runtime = AgentRuntime(root)
        runtime.settings = replace(runtime.settings, api_key='offline-fixture', memory_enabled=True)
        session = runtime.create_session(model_mode='ark_plan', research_mode='current')['session_id']
        prompt = '请写一份近30天的以太坊研究月报，关注测试网进展。'
        with patch('research_agent.runtime.threading.Thread'):
            with patch('research_agent.runtime.utcnow', return_value='2025-02-01T12:00:00Z'):
                original = runtime.start(session, prompt, deliverable='report', request_purpose='chat')
            job = runtime._session_dir(session) / 'jobs' / original['job_id'] / 'status.json'
            job.write_text(json.dumps({**original, 'state': 'interrupted'}))
            runtime.active.pop(session)
            with patch('research_agent.runtime.utcnow', return_value='2025-03-01T12:00:00Z'):
                resumed = runtime.continue_job(session, original['job_id'])
        self.assertEqual(resumed['periodic_scope'], original['periodic_scope'])
        self.assertEqual(resumed['required_report_window'], original['required_report_window'])
        self.assertEqual(resumed['report_window_reference_at'], '2025-02-01T12:00:00Z')
        self.assertNotEqual(resumed['created_at'], original['created_at'])

        self.call('get_research_leads')
        self.call('write_todos', stages(0))
        self.memory.begin('resumed-job', 'Same January research', SCOPE['end'])
        changed_leads = {**self.leads, 'questions_to_watch': [{'lead_id': 'report:later:watch:1', 'text': 'Newer lead'}]}
        workflow = PeriodicWorkflow(self.memory, 'resumed-job', SCOPE, self.workflow.prompt, self.emit,
                                    leads=changed_leads, continued_from='period-job')
        self.assertEqual(workflow.phase(), 'discover')
        self.assertEqual(workflow.leads, self.leads)
        self.assertEqual(json.loads(research_leads_tool(workflow.leads).invoke({})), self.leads)
        self.assertEqual(self.memory.state('resumed-job')['periodic_workflow']['leads'], self.leads)


if __name__ == '__main__':
    unittest.main()
