"""Isolated integration contracts; synthetic models/data, no network or services."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from pydantic import Field
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
import deepagents.graph as deepagent_factory
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite import SqliteSaver

from research_agent.context_runtime import make_context_middleware
from research_agent.evidence_memory import EvidenceMemory
from research_agent.knowledge import KnowledgeStore
from research_agent.runtime import AgentRuntime, TraceMiddleware, write_json
from research_agent.settings import Settings
from research_agent.tools import ResearchTools
from research_agent.working_memory import WorkingMemory


class SyntheticSummaryModel(BaseChatModel):
    """Shared call capture survives model_copy without making external requests."""
    calls: list[Any] = Field(default_factory=list, exclude=True)
    max_tokens: int = 4096

    @property
    def _llm_type(self):
        return 'memory-contract-synthetic'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append([message.content for message in messages])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content='SYNTHETIC continuation. Source page-aaaaaaaaaaaaaaaaaaaaaaaa and conflict remain pending.',
            usage_metadata={'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20},
            response_metadata={'model_name': 'synthetic-contract', 'finish_reason': 'stop'}))])


def source(letter, *, known='2026-01-04T00:00:00Z', published='2026-01-01T00:00:00Z',
           observed=None, kind='page'):
    rid = ('page-' if kind == 'page' else 'web-') + letter*24
    row = {'record_id': rid, 'title': 'Ethereum upgrade synthetic contract fixture',
        'text': 'Ethereum upgrade synthetic source, not actual market evidence.',
        'url': 'https://example.org/synthetic/'+letter, 'retrieved_at': known,
        'published_at': published, 'source_kind': kind, 'source': {'url': 'https://example.org/synthetic/'+letter}}
    if observed:
        row['observed_at'] = observed
    return row


class MemoryRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = KnowledgeStore(self.root/'knowledge')
        self.cutoff = '2026-01-05T00:00:00Z'

    def service(self, *, mode='observed', memory=True, allow_live=True):
        # Isolate the actual news/lookup methods without production startup or
        # unrelated dataset/model construction.
        service = ResearchTools.__new__(ResearchTools)
        service.knowledge = self.store
        service.mode = mode
        service.cutoff = self.cutoff
        service.workspace = self.root/'workspace'
        service.workspace.mkdir(exist_ok=True)
        service.evidence_memory = EvidenceMemory(self.store) if memory else None
        service.retrieval_judgments = None
        service.judgment_mode = 'off'
        service.lock = threading.RLock()
        service.used_records = set()
        service.library = None
        service.allow_live = allow_live
        service.context_record = {'record_id': 'context-'+'0'*24}
        return service

    def test_legacy_default_is_strict_and_observed_allows_later_capture(self):
        row = source('a', known='2026-01-10T00:00:00Z')
        self.store.register(row)
        self.assertEqual(self.store.select_news('Ethereum', cutoff=self.cutoff)['results'], [])
        observed = self.store.select_news('Ethereum', cutoff=self.cutoff, mode='observed')
        self.assertEqual([row['record_id']], [r['record_id'] for r in observed['results']])
        self.assertEqual(observed['mode'], 'observed')

    def test_material_only_keeps_originals_and_feed_but_not_search_excerpt(self):
        rows = [source('a'), source('b', kind='feed_item'), source('c', kind='search_excerpt')]
        for row in rows:
            self.store.register(row)
        packet = self.store.select_news('Ethereum', cutoff=self.cutoff, material_only=True, budget=10000)
        self.assertEqual({r['record_id'] for r in packet['results']}, {r['record_id'] for r in rows[:2]})

    def test_memory_disabled_fallback_preserves_material_and_availability_contract(self):
        row = source('a', known='2026-01-10T00:00:00Z')
        self.store.register(row)
        service = self.service(memory=False)
        packet = service.news_pack(query='Ethereum', cutoff=self.cutoff, material_only=True)
        self.assertEqual(packet['mode'], 'observed')
        self.assertEqual([r['record_id'] for r in packet['results']], [row['record_id']])
        service.mode = 'strict'
        packet = service.news_pack(query='Ethereum', cutoff='2027-01-01T00:00:00Z', material_only=True)
        self.assertEqual(packet['as_of'], self.cutoff)
        self.assertEqual(packet['results'], [])

    def test_strict_source_read_matches_acquisition_publication_and_observation_cutoff(self):
        valid = source('a')
        invalid = [source('b', known='2026-01-06T00:00:00Z'),
                   source('c', published='2026-01-06T00:00:00Z'),
                   source('d', observed='2026-01-06T00:00:00Z')]
        for row in [valid, *invalid]:
            self.store.register(row)
        service = self.service(mode='strict')
        self.assertEqual(service.lookup(valid['record_id'])['text'], valid['text'])
        for row in invalid:
            with self.subTest(record_id=row['record_id']):
                with self.assertRaises(ValueError):
                    service.lookup(row['record_id'])

    def test_historical_no_live_service_still_rejects_current_knowledge(self):
        row = source('a')
        self.store.register(row)
        with self.assertRaises(ValueError):
            self.service(mode='strict', allow_live=False).lookup(row['record_id'])

    def test_trace_adds_one_fresh_working_view_without_mutating_original_prompt(self):
        working = WorkingMemory(self.root/'working')
        working.begin('task-a', 'Synthetic source research', self.cutoff)
        request = ModelRequest(model=SyntheticSummaryModel(), messages=[HumanMessage(content='Continue')],
                               system_message=SystemMessage(content='Original system instruction'))
        captured = []
        middleware = TraceMiddleware(lambda *_: None, working_memory=working, job_id='task-a')
        def handler(actual):
            captured.append(actual)
            return SimpleNamespace(result=[AIMessage(content='Synthetic response')])
        middleware.wrap_model_call(request, handler)
        middleware.wrap_model_call(request, handler)
        self.assertEqual(request.system_message.content, 'Original system instruction')
        self.assertTrue(all(str(r.system_message.content).count('Working task memory') == 1 for r in captured))

    def test_native_summary_replaces_default_meters_usage_and_retains_checkpoint_originals(self):
        model = SyntheticSummaryModel()
        events = []
        backend = FilesystemBackend(root_dir=self.root, virtual_mode=True)
        summary = make_context_middleware(model, backend, lambda kind, data: events.append((kind, data)), trigger_tokens=200)
        connection = sqlite3.connect(self.root/'checkpoints.sqlite', check_same_thread=False)
        self.addCleanup(connection.close)
        with patch.object(deepagent_factory, 'create_agent', wraps=deepagent_factory.create_agent) as create:
            graph = create_deep_agent(model, system_prompt='Synthetic test, no external services.',
                backend=backend, middleware=[summary], checkpointer=SqliteSaver(connection))
        summaries = [m for m in create.call_args.kwargs['middleware'] if m.name == summary.name]
        self.assertEqual(summaries, [summary])
        messages = [HumanMessage(content=f'MARKER-{i} '+('synthetic evidence text '*50)) for i in range(25)]
        config = {'configurable': {'thread_id': 'synthetic-summary-contract'}}
        result = graph.invoke({'messages': messages}, config=config)
        self.assertEqual(len(model.calls), 2)  # one summary and one ordinary model
        self.assertIn('Extract a compact working-memory summary', str(model.calls[0]))
        self.assertNotIn('{messages}', str(model.calls[0]))
        self.assertNotIn('MARKER-0 ', str(model.calls[-1]))
        state = graph.get_state(config)
        self.assertEqual(len(state.values['messages']), 26)
        self.assertEqual(len(result['messages']), 26)
        self.assertTrue(any('MARKER-0 ' in str(m.content) for m in state.values['messages']))
        histories = list((self.root/'conversation_history').glob('*.md'))
        self.assertEqual(len(histories), 1)
        self.assertIn('MARKER-0 ', histories[0].read_text())
        self.assertEqual([kind for kind, _ in events], ['context_summary_started', 'context_summary_finished'])
        self.assertEqual(events[-1][1]['usage']['total_tokens'], 20)
        self.assertEqual(events[-1][1]['actual_model'], 'synthetic-contract')
        # Rebuild the graph/model/saver as after a host restart; do not rely on
        # the first graph's in-memory request or synthetic model call capture.
        connection.close()
        reopened = sqlite3.connect(self.root/'checkpoints.sqlite', check_same_thread=False)
        self.addCleanup(reopened.close)
        restarted_model = SyntheticSummaryModel()
        restarted_summary = make_context_middleware(restarted_model, backend, lambda *_: None, trigger_tokens=200)
        restarted = create_deep_agent(restarted_model, system_prompt='Synthetic continuation.',
            backend=backend, middleware=[restarted_summary], checkpointer=SqliteSaver(reopened))
        resumed = restarted.invoke({'messages': [HumanMessage(content='RESUME-MARKER: continue the saved plan.')]}, config=config)
        self.assertEqual(len(resumed['messages']), 28)
        self.assertTrue(any('MARKER-0 ' in str(m.content) for m in restarted.get_state(config).values['messages']))
        self.assertIn('RESUME-MARKER', str(restarted_model.calls[-1]))

    def test_optional_working_store_initialization_failure_does_not_abort_business_graph(self):
        # A synthetic runtime job, entirely local; no production process is used.
        sid, job_id = 'a'*32, 'b'*32
        directory = self.root/'sessions'/sid
        workspace = directory/'workspace'
        workspace.mkdir(parents=True)
        job_dir = directory/'jobs'/job_id
        job_dir.mkdir(parents=True)
        settings = Settings(api_key='synthetic-fixture-key', search_enabled=False, judgment_mode='off')
        meta = {'session_id': sid, 'cutoff': self.cutoff, 'mode': 'observed', 'model_mode': 'ark_plan',
                'model_id': settings.model_id, 'research_mode': 'current', 'data_run': str(self.root)}
        write_json(directory/'session.json', meta)
        write_json(job_dir/'status.json', {'job_id': job_id, 'session_id': sid, 'state': 'running', 'deliverable': 'answer'})
        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.root = self.root
        runtime.settings = settings
        runtime.experience = None
        runtime.knowledge = self.store
        runtime.state_dir = self.root/'runtime'
        runtime.library = runtime.live_data = runtime.investment = runtime.audit_builder = None
        runtime.software_fingerprint = 'synthetic'
        runtime.lock = threading.RLock()
        runtime.active = {sid: (job_id, threading.Event())}
        fake_model = SimpleNamespace(http_client=None)
        fake_service = SimpleNamespace(as_tools=lambda: [], published_reports=[],
            submitted_answers=[{'rendered': 'Synthetic verified-answer fixture'}], last_report=None)
        fake_graph = SimpleNamespace(invoke=lambda *a, **k: {'messages': [AIMessage(content='Synthetic final')]})
        fake_server = SimpleNamespace(start=lambda: '/synthetic-local-socket', close=lambda: None)
        with patch('research_agent.runtime.create_ark_model', return_value=fake_model), \
             patch('research_agent.runtime.ResearchShell', return_value=SimpleNamespace(attach_research_sdk=lambda *_: None)), \
             patch('research_agent.runtime.install_skills'), \
             patch('research_agent.runtime.ResearchTools', return_value=fake_service), \
             patch('research_agent.runtime.ResearchSDKServer', return_value=fake_server), \
             patch('research_agent.runtime.create_deep_agent', return_value=fake_graph) as create, \
             patch('research_agent.working_memory.WorkingMemory', side_effect=sqlite3.DatabaseError('Synthetic corrupt task notes')):
            runtime._run(directory, job_dir, 'Synthetic source research', threading.Event())
        status = json.loads((job_dir/'status.json').read_text())
        self.assertEqual(status['state'], 'completed', status)
        self.assertEqual(create.call_count, 1)


if __name__ == '__main__':
    unittest.main()
