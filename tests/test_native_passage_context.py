"""One native Deep Agents graph regression; fake models, no network or UI."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
import deepagents.graph as factory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from research_agent.runtime import AgentRuntime, TraceMiddleware
from research_agent.settings import Settings
from research_agent.working_memory import WorkingMemory


class CaptureModel(BaseChatModel):
    seen: list = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self):return 'native-selected-passage-fixture'

    def bind_tools(self,*args,**kwargs):return self

    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.seen.append(messages)
        if len(self.seen)==1:
            answer=AIMessage(content='',tool_calls=[
                {'id':'selected-passages','name':'recall_memory','args':{}},
                {'id':'large-numeric-table','name':'request_public_api','args':{}}])
        else:answer=AIMessage(content='Synthetic complete.')
        return ChatResult(generations=[ChatGeneration(message=answer)])


class NativePassageContextTests(unittest.TestCase):
    def test_selected_passages_survive_native_fs_while_big_table_keeps_trace_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            backend=FilesystemBackend(root_dir=root,virtual_mode=True)
            permissions=[FilesystemPermission(operations=['write'],paths=['/data/**'],mode='deny')]
            filesystem=AgentRuntime.__new__(AgentRuntime).build_filesystem_middleware(backend,permissions)
            self.assertIs(filesystem.backend,backend)
            self.assertEqual(filesystem._permissions,permissions)
            self.assertEqual(filesystem._tool_token_limit_before_evict,100000)
            working=WorkingMemory(root/'memory/task-state')
            working.begin('native-test','Synthetic selected passage inspection')
            rows=[]
            for index in range(40):
                text=f'PASSAGE-{index}: '+('Exact captured synthetic text. '*76)+f' END-{index}'
                digest=hashlib.sha256(text.encode()).hexdigest()
                rows.append({'record_id':f'page-{index:024x}','passage_id':'passage-'+digest[:24],
                    'excerpt':text,'source_slices':[{'start':0,'end':len(text),'sha256':digest}]})
            packet={'retrieval_unit':'passage','results':rows,
                'source_ids':[row['record_id'] for row in rows],
                'selection':{'selected_count':40,'requested_limit':40,'effective_limit':40}}
            payload=json.dumps(packet)
            self.assertGreater(len(payload),80000)

            @tool
            def recall_memory()->str:
                """Return the selected exact synthetic RAG passages."""
                return payload

            @tool
            def request_public_api()->str:
                """Return a synthetic table without any external request."""
                return json.dumps({'record':{'record_id':'series-'+'a'*24,
                    'data_file':'data/large-numeric-table.json','record_count':3000,
                    'series':[{'observed_at':f'2026-01-01T00:00:{i:04d}Z','value':str(i)} for i in range(3000)]}})

            model=CaptureModel()
            trace=TraceMiddleware(lambda *_:None,settings=Settings(),working_memory=working,job_id='native-test')
            with patch.object(factory,'create_agent',wraps=factory.create_agent) as create:
                graph=create_deep_agent(model,backend=backend,permissions=permissions,
                    tools=[recall_memory,request_public_api],middleware=[trace,filesystem])
            installed=[m for m in create.call_args.kwargs['middleware'] if isinstance(m,FilesystemMiddleware)]
            self.assertEqual(installed,[filesystem])
            graph.invoke({'messages':[HumanMessage(content='Read synthetic selected passages and table.') ]})
            self.assertEqual(len(model.seen),2)
            actual={m.name:m.content for m in model.seen[-1] if isinstance(m,ToolMessage)}
            selected=actual['recall_memory']
            self.assertGreater(len(selected),80000)
            self.assertNotIn('/large_tool_results/',selected)
            returned=json.loads(selected)
            self.assertEqual([row['excerpt'] for row in returned['results']],[row['excerpt'] for row in rows])
            self.assertLessEqual(len(actual['request_public_api']),6000)
            numeric=json.loads(actual['request_public_api'])
            self.assertEqual(numeric['view'],'progressive-tool-view-v3')
            self.assertTrue(numeric['partial'])


if __name__=='__main__':unittest.main()
