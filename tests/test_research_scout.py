"""Offline Scout checks with the real source store and a stub Ark transport."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from research_agent.knowledge import KnowledgeStore
from research_agent.research_scout import build_research_scout
from research_agent.tools import ResearchTools
from research_agent.working_memory import WorkingMemory


SCOPE = {'assets': ['ETH'], 'start': '2025-01-01T00:00:00Z', 'end': '2025-02-01T00:00:00Z'}
ARCHIVE_ID = 'page-' + 'a' * 24
ORIGINAL = 'https://example.invalid/ethereum-update#first'


class ScoutDriver(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class SearchTransport:
    settings = SimpleNamespace(model_id='stub-ark')

    def __init__(self, items):
        self.items = items
        self.requests = []

    def search(self, query, max_results, time_range):
        self.requests.append((query, max_results, time_range))
        if isinstance(self.items, Exception):
            raise self.items
        raw = json.dumps(self.items).encode()
        return {'items': self.items, 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
                'retrieved_at': '2025-02-03T00:00:00Z', 'usage': {'input_tokens': 42},
                'provider_status': 'completed', 'note': 'Stub transport, same production research adapter.'}


class ResearchScoutTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        (self.run / 'manifest.json').write_text(json.dumps({'datasets': [], 'sources': []}))
        self.workspace = self.root / 'workspace'
        self.events = []
        self.saved = []
        self.store = KnowledgeStore(self.root / 'knowledge')
        self.memory = WorkingMemory(self.workspace / 'memory' / 'task-state')
        self.memory.begin('scout-job', 'Ethereum January research', SCOPE['end'])

    def emit(self, kind, row):
        self.events.append((kind, row))

    def archive(self):
        text = 'Ethereum announced an upcoming public test on a testnet starting in January 2025.'
        raw = self.root / 'saved-original.txt'
        raw.write_text(text)
        self.store.register({'record_id': ARCHIVE_ID, 'dataset_id': 'fixture.original',
            'text': text, 'url': ORIGINAL, 'title': 'Ethereum archive update',
            'published_at': '2025-01-20T00:00:00Z', 'retrieved_at': '2025-02-02T00:00:00Z',
            'available_at': '2025-02-02T00:00:00Z', 'source': {'url': ORIGINAL,
                'source_id': ARCHIVE_ID, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}}, raw)

    def build(self, transport=None, **options):
        service = ResearchTools(self.run, self.workspace, '2025-02-03T00:00:00Z',
            allow_live=True, web_search=transport, knowledge=self.store, event_sink=self.emit)
        service.working_memory = self.memory
        service.job_id = 'scout-job'
        spec = build_research_scout(service, self.workspace, self.emit,
                                   scope=SCOPE, on_complete=self.saved.append, **options)
        self.assertEqual((spec['name'], spec['mode']), ('research-scout', 'isolated'))
        return spec

    @staticmethod
    def invoke(spec):
        assignment = HumanMessage(content='Search for BTC in 2030 with private text: PRIVATE_TEST_VALUE.')
        result = spec['runnable'].invoke({'messages': [assignment]})
        return json.loads(result['messages'][0].content)

    def test_fixed_scope_runs_real_archive_and_public_adapters_once(self):
        self.archive()
        transport = SearchTransport([
            {'url': 'https://example.invalid/ethereum-update?utm_source=stub#first', 'title': 'Same link',
             'publish_time': '2025-01-20T00:00:00Z', 'summary': 'Duplicate provider excerpt'},
            {'url': 'https://example.invalid/ethereum-update#reply', 'title': 'Original reply url',
             'publish_time': '2025-01-21', 'summary': 'Distinct post fragment'},
            {'url': 'https://example.invalid/new', 'title': 'New link',
             'publish_time': '2025-01-22T00:00:00Z', 'summary': 'Second provider candidate'},
            {'url': 'https://example.invalid/future', 'title': 'Outside the fixed window',
             'publish_time': SCOPE['end'], 'summary': 'Out of scope'},
            {'url': 'https://example.invalid/unknown', 'title': 'Unknown publication date',
             'summary': 'Needs date verification'},
        ])
        runnable = self.build(transport)
        result = self.invoke(runnable)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['scope'], SCOPE)
        self.assertEqual(result['candidate_count'], 4)
        candidates = result['candidates']
        self.assertEqual(candidates[0]['record_id'], ARCHIVE_ID)
        self.assertTrue(all(row['source_status'] == 'candidate' for row in candidates))
        self.assertTrue(all('excerpt' not in row for row in candidates))
        self.assertEqual(result['channels']['web']['outside_window'], 1)
        self.assertEqual(result['channels']['web']['unknown_publication_time'], 1)
        self.assertEqual(candidates[-1]['publication_window'], 'unknown')
        self.assertTrue(all(self.store.contains(row['record_id']) for row in candidates))
        self.assertIn('Ethereum (ETH)', transport.requests[0][0])
        self.assertNotIn('PRIVATE_TEST_VALUE', transport.requests[0][0])
        self.assertEqual(transport.requests[0][1:], (8, 'Any'))
        self.assertEqual(self.invoke(runnable), result)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(len(self.saved), 1)
        receipt = json.loads((self.workspace / result['artifact'].lstrip('/')).read_text())
        self.assertEqual(receipt['tool_receipts']['archive']['result']['results'][0]['record_id'], ARCHIVE_ID)
        self.assertEqual(len(receipt['tool_receipts']['web']['result']['results']), 5)
        self.assertEqual(receipt['tool_receipts']['archive']['arguments']['query'], '')
        observed = self.memory.state('scout-job')['research_state']['observations']
        self.assertEqual({row['tool_name'] for row in observed}, {'recall_memory', 'search_web'})
        self.assertTrue(any(kind == 'search_usage' for kind, _ in self.events))

    def test_archive_only_and_empty_are_distinct_from_failed(self):
        transport = SearchTransport(AssertionError('Archive-only path must not call web'))
        result = self.invoke(self.build(transport, allow_web=False))
        self.assertEqual(result['status'], 'no_evidence')
        self.assertEqual(result['channels']['archive']['status'], 'no_results')
        self.assertEqual(result['channels']['web']['status'], 'unavailable')
        self.assertEqual(transport.requests, [])

        self.archive()
        failure = self.invoke(self.build(SearchTransport(TimeoutError('Provider transport did not respond'))))
        self.assertEqual(failure['status'], 'partial')
        self.assertEqual(failure['channels']['web'], {'status': 'error', 'error_type': 'TimeoutError'})
        self.assertEqual(failure['candidates'][0]['record_id'], ARCHIVE_ID)

    def test_cancelled_or_inaccessible_probes_are_not_empty_retrievals(self):
        cancel = Event()
        cancel.set()
        cancelled = self.invoke(self.build(cancel=cancel))
        self.assertEqual(cancelled['status'], 'incomplete')
        self.assertEqual(cancelled['channels']['archive']['status'], 'cancelled')
        self.assertFalse(any(kind == 'tool_started' for kind, _ in self.events))

        unavailable = build_research_scout(SimpleNamespace(allow_live=False), self.workspace, scope=SCOPE)
        result = self.invoke(unavailable)
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(all(row['status'] == 'unavailable' for row in result['channels'].values()))

    def test_native_task_returns_scout_packet_to_parent(self):
        self.archive()
        spec = self.build(allow_web=False)
        model = ScoutDriver(responses=[
            AIMessage(content='', tool_calls=[{'id': 'scout-task', 'name': 'task',
                'args': {'subagent_type': 'research-scout', 'description': 'Map public source candidates'}}]),
            AIMessage(content='Use the archived original for a focused read.'),
        ])
        lead = create_deep_agent(model, subagents=[spec],
                                 backend=FilesystemBackend(root_dir=self.workspace, virtual_mode=True))
        result = lead.invoke({'messages': [HumanMessage(content='Research this with the Scout.')]})
        handoff = next(message for message in result['messages'] if isinstance(message, ToolMessage))
        self.assertEqual(handoff.tool_call_id, 'scout-task')
        packet = json.loads(handoff.content)
        self.assertEqual(packet['candidates'][0]['record_id'], ARCHIVE_ID)
        self.assertEqual(packet['channels']['archive']['status'], 'success')
        self.assertEqual(len(self.saved), 1)


if __name__ == '__main__':
    unittest.main()
