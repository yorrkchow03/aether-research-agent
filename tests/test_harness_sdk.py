"""Test actual terminal transport, source lineage and the reduced agent interface."""
import json
import os
import shlex
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures import project_fixture
from research_agent.runtime import AgentRuntime
from research_agent.research_sdk import ResearchClient
from research_agent.sdk_bridge import ResearchSDKServer
from research_agent.settings import Settings
from research_agent.terminal import ResearchShell
from research_agent.tools import ResearchTools


class HarnessSDKTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = project_fixture(Path(self.tmp.name) / 'project')
        self.runtime = AgentRuntime(self.root)
        self.meta = self.runtime.create_session()
        self.workspace = self.runtime._session_dir(self.meta['session_id']) / 'workspace'
        self.events = []
        self.settings = Settings(api_key='fixture-private-key')
        self.cancel = threading.Event()
        self.shell = ResearchShell(self.workspace, self.settings, self.cancel)
        self.service = ResearchTools(Path(self.meta['data_run']), self.workspace,
            self.meta['cutoff'], allow_live=True, live_data=self.runtime.live_data,
            shell=self.shell, knowledge=self.runtime.knowledge,
            investment=self.runtime.investment,
            event_sink=lambda kind, row: self.events.append((kind, row)))

    def server(self):
        server = ResearchSDKServer(self.service, self.settings, self.cancel)
        self.addCleanup(server.close)
        self.shell.attach_research_sdk(server.start())
        return server

    def test_current_model_has_only_twelve_business_tools(self):
        self.service.web_search = SimpleNamespace()
        self.service.reviewer = SimpleNamespace()
        names = {item.name for item in self.service.as_tools()}
        self.assertEqual(names, {'list_data_sources', 'request_public_api', 'search_web',
            'fetch_webpage', 'read_evidence', 'register_calculation', 'get_private_portfolio',
            'record_news_event', 'submit_research_answer', 'publish_report',
            'get_research_context', 'recall_memory'})

    def test_terminal_sdk_capture_custom_calculation_and_source_gate(self):
        server = self.server()
        row = {'record_id': 'live-' + 'e' * 24, 'observed_at': self.meta['cutoff'],
               'retrieved_at': '2026-10-14T00:00:00Z',
               'values': {'count': '7'}, 'units': {'count': 'count'}}
        with patch.object(self.service, 'fetch_live',
                          side_effect=lambda metric, asset: self.service.retain_live(row)):
            result = self.shell.execute("python -m research_agent.research_sdk fetch --args '{\"metric\":\"fixture\",\"asset\":\"ETH\"}'")
        self.assertEqual(result.exit_code, 0, result.output)
        captured = json.loads(result.output)
        self.assertEqual(captured['record_id'], row['record_id'])
        self.assertTrue(self.runtime.knowledge.contains(row['record_id']))
        self.assertEqual(os.stat(server.path).st_mode & 0o777, 0o600)
        self.assertTrue(any(value.get('name') == 'sdk:fetch' for _, value in self.events))
        code = ('import json,sys\nr=json.load(open(sys.argv[1]))\n'
                'print(json.dumps({"values":{"triple":int(r["values"]["count"])*3},'
                '"units":{"triple":"count"}}))\n')
        command = "python -c " + shlex.quote("from pathlib import Path; Path('notes/custom.py').write_text(" + repr(code) + ")")
        self.assertEqual(self.shell.execute(command).exit_code, 0)
        calc = self.service.register_calculation('/notes/custom.py', [row['record_id']], 'triple fixture count')
        self.assertEqual(calc['values']['triple'], '21')
        self.assertEqual(self.service.lookup(calc['record_id'])['input_record_ids'], [row['record_id']])
        draft = {'title': 'Synthetic SDK test', 'summary': 'fixture',
            'observations': [{'statement': 'Synthetic triple count', 'evidence_ids': [calc['record_id']],
                'metric': 'triple', 'value': '21', 'unit': 'count'}],
            'judgment': 'insufficient_evidence', 'judgment_reason': 'fixture', 'limitations': ['Synthetic test']}
        self.assertEqual(self.service.publish(draft)['validation']['status'], 'passed')
        draft['observations'][0]['value'] = '999'
        with self.assertRaises(ValueError):
            self.service.publish(draft)

    def test_sdk_cannot_publish_or_leak_provider_key_and_closes(self):
        server = self.server()
        client = ResearchClient(server.path)
        with self.assertRaises(ValueError):
            client.call('publish_report', draft={})
        with patch.object(self.service, 'sdk_call', side_effect=ValueError('fixture-private-key')):
            with self.assertRaisesRegex(ValueError, r'\[REDACTED\]'):
                client.call('sources')
        result = self.shell.execute("python -c 'import os; print(os.getenv(\"ARK_API_KEY\", \"absent\"))'")
        self.assertEqual(result.output.strip(), 'absent')
        self.cancel.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            client.call('sources')
        server.close()
        self.assertFalse(Path(server.path).exists())

    def test_recipe_paths_accept_session_absolute_but_reject_escape(self):
        row = {'record_id': 'live-' + 'f' * 24, 'observed_at': self.meta['cutoff'],
               'retrieved_at': '2026-10-14T00:00:00Z',
               'values': {'count': '7'}, 'units': {'count': 'count'}}
        self.service.retain_live(row)
        recipe = self.workspace / 'notes/path.py'
        recipe.write_text('import json,sys\nr=json.load(open(sys.argv[1]))\n'
            'print(json.dumps({"values":{"count":r["values"]["count"]},"units":{"count":"count"}}))\n')
        for path in ('/notes/path.py', './notes/path.py', str(recipe.resolve())):
            result = self.service.register_calculation(path, [row['record_id']], 'Identity path regression')
            self.assertEqual(result['values']['count'], '7')
        outside = Path(self.tmp.name) / 'outside.py'
        outside.write_text('raise AssertionError("Outside recipe executed")')
        escape = self.workspace / 'notes/escape.py'
        escape.symlink_to(outside)
        for path in (str(outside), '../outside.py', '/notes/escape.py', '/etc/outside.py'):
            with self.assertRaisesRegex(ValueError, 'session notes directory'):
                self.service.register_calculation(path, [row['record_id']], 'Must reject')

    def test_archive_sdk_does_not_request_current_chain_data(self):
        with patch.object(self.service, 'fetch_live', side_effect=AssertionError('Unexpected live request')):
            packet = self.service.sdk_call('archive-chain', {'metric': 'etf_flows'})
        self.assertEqual(packet['datasets'][0]['cutoff'], self.meta['cutoff'])

    def test_memory_modes_keep_dates_and_filter_saved_output_cutoff(self):
        tools = {item.name: item for item in self.service.as_tools()}
        recall = tools['recall_memory']
        packet = json.loads(recall.invoke({'kind': 'news', 'as_of': self.meta['cutoff']}))
        self.assertEqual(packet['results'], [])
        self.assertEqual(packet['as_of'], self.meta['cutoff'])
        self.assertEqual(json.loads(recall.invoke({'kind': 'events', 'asset': 'ALL'}))['results'], [])
        packet=json.loads(recall.invoke({'kind': 'research', 'as_of': self.meta['cutoff']}))
        self.assertEqual(packet['results'],[])


if __name__ == '__main__':
    unittest.main()
