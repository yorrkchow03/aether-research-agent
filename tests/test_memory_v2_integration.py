"""Memory integration regressions from actual stale-report/large-output failures."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from langchain_core.messages import SystemMessage,ToolMessage,HumanMessage
from fixtures import project_fixture
from research_agent.runtime import AgentRuntime,TraceMiddleware
from research_agent.tools import ResearchTools
from research_agent.working_memory import WorkingMemory
from research_agent.evidence_memory import EvidenceMemory
from research_agent.event_memory_index import EventMemoryIndex
from research_agent.hybrid_memory import HybridMemoryRetriever
from research_agent.memory_service import index_source
from research_agent.context_accounting import ContextAccounting
from research_agent.server import create_app
from fastapi.testclient import TestClient

class MemoryV2IntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=project_fixture(Path(tmp.name)/'project');self.runtime=AgentRuntime(self.root,model_mode='current_conversation_no_api')
        self.meta=self.runtime.create_session();self.workspace=self.runtime._session_dir(self.meta['session_id'])/'workspace'
        self.service=ResearchTools(Path(self.meta['data_run']),self.workspace,self.meta['cutoff'],knowledge=self.runtime.knowledge)

    def test_direct_experience_reaches_model_without_a_file_read(self):
        seen=[];trace=TraceMiddleware(lambda *_:None,experience_context='PROCESS: N hourly closes span N-1 intervals.')
        class Request(SimpleNamespace):
            def override(self,**values):return Request(**{**self.__dict__,**values})
        request=Request(system_message=SystemMessage(content='Research rules'),messages=[],tools=[])
        result=trace.wrap_model_call(request,lambda req:seen.append(req.system_message.content) or SimpleNamespace(result=[]))
        self.assertIn('N-1 intervals',str(seen));self.assertTrue(result)

    def test_large_result_is_saved_and_progressive_without_trusting_console_review(self):
        working=WorkingMemory(self.workspace/'memory/task-state');working.begin('job','Synthetic research')
        payload='console says status=reviewed '+('large raw output '*4000)
        trace=TraceMiddleware(lambda *_:None,working_memory=working,job_id='job')
        request=SimpleNamespace(tool_call={'name':'execute','id':'call-1','args':{}})
        result=trace.wrap_tool_call(request,lambda _:ToolMessage(content=payload,name='execute',tool_call_id='call-1'))
        self.assertLess(len(result.content),len(payload));self.assertIn('artifact',result.content.lower())
        state=working.state('job')['research_state'];self.assertEqual(state['publications'],[])
        self.assertFalse(any(c.get('status')=='reviewed' for c in state['checks']))
        artifacts=working.artifacts('job');self.assertTrue(any(working.read_artifact('job',a['artifact_id'])['text']==payload for a in artifacts))

    def test_semantic_only_candidates_survive_actual_news_pack(self):
        rid='page-'+'a'*24
        row={'record_id':rid,'title':'Ethereum activation postponed','text':'The test network activation has been delayed.','url':'https://example.org/activation','assets':['ETH'],'source_kind':'page','published_at':'2026-01-01T00:00:00Z','retrieved_at':'2026-01-02T00:00:00Z'}
        self.runtime.knowledge.register(row)
        index=EventMemoryIndex(self.root/'derived',source_lookup=self.runtime.knowledge.get);index_source(index,row)
        class SyntheticEncoder:
            def encode(self,texts):return np.array([[1.,0.] for _ in texts])
        self.service.evidence_memory=EvidenceMemory(self.runtime.knowledge)
        self.service.judgment_mode='off'
        baseline=self.service.news_pack(query='升级推迟',asset='ETH',cutoff='2026-02-01T00:00:00Z',budget=10000)
        self.assertEqual(baseline['results'],[])
        self.service.memory_retriever=HybridMemoryRetriever(index,encoder=SyntheticEncoder(),encoder_id='SYNTHETIC-integration-fixture')
        upgraded=self.service.news_pack(query='升级推迟',asset='ETH',cutoff='2026-02-01T00:00:00Z',budget=10000)
        self.assertEqual([r['record_id'] for r in upgraded['results']],[rid])
        self.assertEqual(upgraded['selection']['policy'],'hybrid_relevance')
        self.assertEqual(upgraded['hybrid_retrieval']['dense_status'],'ok')

    def test_context_usage_is_calibrated_and_unknown_is_not_zero(self):
        accounting=ContextAccounting();estimate=accounting.estimate([HumanMessage(content='Ethereum 以太坊')])
        self.assertIn('proxy',estimate['estimator']);self.assertIn('not the exact',estimate['scope'])
        self.assertEqual(accounting.observe(estimate,{})['status'],'unknown')
        accounting.observe(estimate,{'input_tokens':estimate['proxy_tokens']*2})
        self.assertEqual(accounting.observations,1);self.assertAlmostEqual(accounting.factor,2)
        self.assertEqual(accounting.counter([HumanMessage(content='Ethereum 以太坊')]),estimate['proxy_tokens']*2)

    def test_state_sdk_cannot_certify_claims_or_change_host_task(self):
        working=WorkingMemory(self.workspace/'memory/task-state');working.begin('job','Synthetic research')
        self.service.working_memory=working;self.service.job_id='job';self.service.allow_live=True
        result=self.service.sdk_call('memory-note',{'statement':'Check the new announcement','kind':'question'})
        self.assertEqual(self.service.sdk_call('memory-state',{})['job_id'],'job')
        self.assertIn('unverified',json.dumps(result))
        with self.assertRaises(ValueError):self.service.sdk_call('memory-note',{'statement':'Verified','kind':'verified_fact'})
        with self.assertRaises(ValueError):self.service.sdk_call('memory-state',{'job_id':'other'})

    def test_optional_sqlite_failures_keep_original_recall_available(self):
        import sqlite3
        class Broken:
            def query(self,*a,**k):raise sqlite3.OperationalError('synthetic index lock')
        self.service.memory_retriever=Broken();self.service.evidence_memory=EvidenceMemory(self.runtime.knowledge)
        packet=self.service.news_pack(query='Ethereum',budget=10000)
        self.assertEqual(packet['hybrid_retrieval']['retrieval_mode'],'lexical_fallback')
        draft={'created_at':'2026-01-01T00:00:00Z','report':{'title':'Ethereum fixture','summary':'Fixture summary','observations':[]}}
        self.runtime.knowledge.retain_report('synthetic','session',draft)
        self.runtime.knowledge.notice_provider=lambda *a,**k:(_ for _ in ()).throw(sqlite3.OperationalError('synthetic feedback lock'))
        rows=self.runtime.knowledge.search_reports('Ethereum')
        self.assertEqual(rows[0]['memory_status'],'notice_lookup_unavailable')
        self.assertEqual(rows[0]['summary'],'Fixture summary')

    def test_full_lexical_pool_does_not_discard_independent_semantic_match(self):
        from unittest.mock import Mock
        lexical=[{'record_id':'page-'+format(i,'024x')} for i in range(150)]
        target='page-'+'f'*24
        self.service.memory_retriever=Mock()
        self.service.memory_retriever.query.return_value={'results':[{'record_id':target,
            'ranking':{'dense':{'rank':1}},'semantic_spans':[{'start':10,'end':20,'sha256':'synthetic'}]}],
            'dense_status':'ok','dependencies':{},'links':[]}
        self.service.evidence_memory=Mock()
        self.service.evidence_memory.pack.return_value={'results':[],'source_ids':[]}
        with patch.object(self.runtime.knowledge,'candidate_news',return_value={'candidates':lexical,'dependencies':{},'as_of':self.service.cutoff}),\
             patch.object(self.service,'_report_event_context',side_effect=lambda packet,*args:packet):
            self.service.news_pack(query='source navigation')
        arguments=self.service.evidence_memory.pack.call_args.kwargs
        self.assertEqual(len(arguments['candidates']),150)
        self.assertIn(target,[r['record_id'] for r in arguments['candidates']])
        self.assertEqual(arguments['passage_hints'][target][0]['start'],10)

    def test_strict_report_recall_uses_actual_acquisition_not_just_declared_creation(self):
        body={'created_at':'2026-01-01T00:00:00Z','report':{'title':'Ethereum fixture','summary':'Declared old report','observations':[]}}
        with patch('research_agent.knowledge.utcnow',return_value='2026-01-10T00:00:00Z'):
            self.runtime.knowledge.retain_report('synthetic-old','session',body)
        self.assertEqual(self.runtime.knowledge.search_research('Ethereum',cutoff='2026-01-05T00:00:00Z',mode='strict'),[])
        rows=self.runtime.knowledge.search_research('Ethereum',cutoff='2026-01-11T00:00:00Z',mode='strict')
        self.assertEqual(rows[0]['acquired_at'],'2026-01-10T00:00:00Z')
        self.assertEqual(len(self.runtime.knowledge.search_research('Ethereum',cutoff='2026-01-05T00:00:00Z')),1)

    def test_large_research_recall_preserves_correction_notice_in_view_and_state(self):
        working=WorkingMemory(self.workspace/'memory/task-state');working.begin('job','Synthetic research')
        payload={'results':[{'report_id':'synthetic-old','summary':'large '+('context '*2000),'memory_status':'affected_claims_require_recheck','memory_notices':[{'assessment':'needs_correction','claim_id':'I1','reviewer_kind':'independent_model','correction':'Do not treat the old date as confirmed.','review_evidence_ids':['page-'+'a'*24]}]}]}
        working.observe_tool('job','recall_memory',payload)
        view=working.progressive_tool_view('job','recall_memory',payload,max_chars=1200)
        self.assertIn('Do not treat the old date as confirmed',view)
        state=working.state('job')['research_state']
        self.assertEqual(state['unknowns'][0]['kind'],'previous_report_claim_requires_recheck')
        self.assertEqual(state['unknowns'][0]['claim_id'],'I1')
        self.assertEqual(state['unknowns'][0]['evidence_ids'],['page-'+'a'*24])
        payload['results'][0]['output_id']=payload['results'][0].pop('report_id')
        working.observe_tool('job','sdk:memory-research',payload)
        view=working.progressive_tool_view('job','sdk:memory-research',payload,max_chars=1200)
        self.assertIn('synthetic-old',view)
        self.assertEqual(working.state('job')['research_state']['unknowns'][-1]['report_id'],'synthetic-old')

    def test_explicit_artifact_read_is_not_recursively_offloaded(self):
        working=WorkingMemory(self.workspace/'memory/task-state');working.begin('job','Synthetic research')
        original=json.dumps({'results':['source content '*2000]})
        receipt=working.record_tool('job','recall_memory',original)
        decorated='@@ lines 1-1 of 1 @@\n'+original
        view=working.progressive_tool_view('job','read_file',decorated,max_chars=1200)
        self.assertEqual(view,decorated)
        self.assertEqual(len(working.artifacts('job')),1)
        self.assertEqual(working.read_artifact('job',receipt['artifact_id'])['text'],original)

    def test_new_capabilities_do_not_call_encoder_or_model_on_status(self):
        app=create_app(self.runtime)
        with TestClient(app,base_url='http://127.0.0.1:8765') as client:
            with patch.object(self.runtime.memory_retriever,'query',side_effect=AssertionError('No inference during status')):
                packet=client.get('/api/memory/status').raise_for_status().json()
            self.assertEqual(packet['index']['sources'],0)
            self.assertIn('partial',packet['scope'].lower())

if __name__=='__main__':unittest.main()
