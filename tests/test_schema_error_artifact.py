"""Schema error receipts expose model-readable virtual paths, without network calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from research_agent.tool_result_views import schema_error_view
from research_agent.runtime import TraceMiddleware
from research_agent.working_memory import WorkingMemory
from test_evidence_reader import ReaderModel
from test_planning import ScriptedModel
import test_publication_schema_view


class SchemaErrorArtifactTests(unittest.TestCase):
    def test_default_and_overflow_paths_open_via_native_file_tool(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            memory = WorkingMemory(workspace / 'memory/task-state')
            memory.begin('schema', 'Read schema validation error')
            receipt = memory.record_tool('schema', 'publish_report', 'saved native error marker')
            errors = [{'path': '/summary', 'message': 'bad input', 'type': 'validation'}]
            backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            for budget, rows in ((6000, errors), (800, errors * 100)):
                with self.subTest(budget=budget):
                    view = json.loads(schema_error_view('publish_report', rows, receipt,
                        root=memory.root, workspace_root=workspace, max_chars=budget))
                    path = view['artifact']['path']
                    self.assertTrue(path.startswith('/memory/task-state/artifacts/schema/'))
                    self.assertNotIn(str(workspace), path)
                    self.assertLessEqual(len(json.dumps(view, ensure_ascii=False, sort_keys=True)), budget)
                    if budget == 800:
                        self.assertGreater(view['omitted_error_count'], 0)
                    model = ScriptedModel(responses=[
                        AIMessage(content='', tool_calls=[{'id': 'open', 'name': 'read_file',
                            'args': {'file_path': path}}]), AIMessage(content='Done.')])
                    graph = create_deep_agent(model, backend=backend,
                        middleware=[FilesystemMiddleware(backend=backend, tool_token_limit_before_evict=100000)])
                    state = graph.invoke({'messages': [HumanMessage(content='Read the error artifact.')]})
                    result = next(m for m in state['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 'open')
                    self.assertEqual(result.status, 'success')
                    self.assertIn('saved native error marker', result.content)

    def test_unknown_or_outside_workspace_paths_are_not_exposed(self):
        receipt = {'artifact_id': 'artifact-1', 'sha256': 'abc', 'bytes': 1,
                   'path': '/private/unrelated/secret.txt'}
        for workspace in (None, '/tmp/workspace'):
            view = json.loads(schema_error_view('publish_report', [], receipt,
                root='/tmp/memory', workspace_root=workspace))
            self.assertNotIn('path', view['artifact'])
            self.assertNotIn('/private/unrelated', json.dumps(view))

    def test_real_trace_supplies_workspace_root(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            memory = WorkingMemory(workspace / 'memory/task-state')
            memory.begin('schema', 'Invalid draft')
            fixture = test_publication_schema_view.PublicationSchemaViewTests()
            model = ReaderModel(responses=[AIMessage(content='', tool_calls=[{'id': 'invalid',
                'name': 'publish_report', 'args': fixture.arguments()}]), AIMessage(content='Done.')])
            graph = create_agent(model, tools=[fixture.tool()], middleware=[TraceMiddleware(
                lambda *_: None, working_memory=memory, job_id='schema', workspace_root=workspace)])
            state = graph.invoke({'messages': [HumanMessage(content='Publish the fixture.')]})
            result = next(m for m in state['messages'] if isinstance(m, ToolMessage))
            path = json.loads(result.content)['artifact']['path']
            self.assertTrue(path.startswith('/memory/task-state/artifacts/schema/'))
            backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            self.assertTrue(backend._resolve_path(path).is_file())


if __name__ == '__main__':
    unittest.main()
