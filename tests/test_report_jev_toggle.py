"""Per-report opt-in must control all optional semantic work and survive resume."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock,patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from fixtures import project_fixture
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app


RID='page-'+'d'*24


class ToggleModel(FakeMessagesListChatModel):
    http_client:Any=None


class ReportJevToggleTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=project_fixture(Path(tmp.name)/'project')
        self.runtime=AgentRuntime(root=self.root)
        self.runtime.settings=replace(self.runtime.settings,api_key='SYNTHETIC-OFFLINE-KEY',
            memory_enabled=True,memory_hybrid_enabled=False,judgment_mode='assist',judgment_policy='selective',
            search_enabled=False,news_reranking='off',evidence_reader_enabled=False)
        self.runtime.memory_retriever=None
        self.meta=self.runtime.create_session(model_mode='ark_plan',research_mode='current')
        self.sid=self.meta['session_id'];self.directory=self.runtime._session_dir(self.sid)
        self.runtime.knowledge.register({'record_id':RID,'title':'Ethereum synthetic status',
            'text':'Ethereum synthetic source status is unconfirmed.','source_kind':'page',
            'dataset_id':'live.webpage','url':'https://synthetic.invalid/notice',
            'published_at':'2026-10-01T00:00:00Z','retrieved_at':'2026-10-02T00:00:00Z'})

    def start_without_thread(self,*,prompt='Generate an ETH report',**kwargs):
        with patch('research_agent.runtime.threading.Thread'):
            return self.runtime.start(self.sid,prompt,request_purpose='chat',**kwargs)

    def test_explicit_off_overrides_default_on_and_normal_answers_cannot_enable_report_sidecar(self):
        self.runtime.settings=replace(self.runtime.settings,news_reranking='jev')
        status=self.start_without_thread(jev_assistance=False)
        self.assertFalse(status['jev_assistance_requested'])
        self.runtime.active.pop(self.sid)
        status=self.start_without_thread(prompt='Explain an existing source',jev_assistance=True)
        self.assertEqual(status['deliverable'],'answer')
        self.assertFalse(status['jev_assistance_requested'])

    def test_explicit_on_survives_continuation_when_default_is_off(self):
        first=self.start_without_thread(jev_assistance=True)
        self.assertTrue(first['jev_assistance_requested'])
        first['state']='interrupted'
        path=self.directory/'jobs'/first['job_id']/'status.json';path.write_text(json.dumps(first))
        self.runtime.active.pop(self.sid)
        with patch('research_agent.runtime.threading.Thread'):
            resumed=self.runtime.continue_job(self.sid,first['job_id'])
        self.assertTrue(resumed['jev_assistance_requested'])
        self.assertEqual(resumed['deliverable'],'report')
        self.assertEqual(resumed['continued_from_job_id'],first['job_id'])
        self.assertEqual(resumed['prompt'],first['prompt'])

    def test_explicit_off_survives_continuation_when_default_is_later_enabled(self):
        first=self.start_without_thread(jev_assistance=False)
        first['state']='failed'
        (self.directory/'jobs'/first['job_id']/'status.json').write_text(json.dumps(first))
        self.runtime.active.pop(self.sid)
        self.runtime.settings=replace(self.runtime.settings,news_reranking='jev')
        with patch('research_agent.runtime.threading.Thread'):
            resumed=self.runtime.continue_job(self.sid,first['job_id'])
        self.assertFalse(resumed['jev_assistance_requested'])

    def test_api_preserves_strict_boolean_and_rejects_string_or_numeric_coercion(self):
        app=create_app(self.runtime)
        with TestClient(app,base_url='http://127.0.0.1:8765') as client,patch('research_agent.runtime.threading.Thread'):
            route=f'/api/sessions/{self.sid}/messages';headers={'X-Local-Token':app.state.csrf_token}
            for value in ('false',0,1,'true'):
                response=client.post(route,headers=headers,json={'prompt':'Generate an ETH report','jev_assistance':value})
                self.assertEqual(response.status_code,422)
            response=client.post(route,headers=headers,json={'prompt':'Generate an ETH report','jev_assistance':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['jev_assistance_requested'])

    def run_report(self,enabled):
        status=self.start_without_thread(jev_assistance=enabled)
        job_dir=self.directory/'jobs'/status['job_id'];services=[];packets=[]
        build=self.runtime.build_research_tools
        def make_service(*args,**kwargs):
            service=build(*args,**kwargs);services.append(service);return service
        def invoke(*args,**kwargs):
            service=services[-1]
            packets.append(service.news_pack(query='Ethereum synthetic status',budget=6000,
                start='2026-10-01T00:00:00Z',end='2026-10-08T00:00:00Z'))
            service.published_reports.append('/reports/SYNTHETIC-TEST.md')
            return {'messages':[AIMessage(content='Synthetic finished report') ]}
        judgment=Mock()
        judgment.assess.return_value={'status':'ok','task':'relevance','label':'direct_evidence',
            'confidence':.95,'scores':{'direct_evidence':.95},'model':'jev-1.14.0','provider':'jev',
            'physical_request_count':1,'billable_request_made':True,
            'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110},'evidence_ids':[RID]}
        fake_server=SimpleNamespace(start=lambda:'/SYNTHETIC-OFFLINE-SOCKET',close=lambda:None)
        fake_model=ToggleModel(responses=[AIMessage(content='unused offline response')])
        fake_graph=SimpleNamespace(invoke=invoke)
        with patch.object(self.runtime,'build_author_model',return_value=fake_model), \
             patch.object(self.runtime,'build_judgment_service',side_effect=AssertionError('Legacy optional sidecar forbidden on reports')), \
             patch.object(self.runtime,'build_research_tools',side_effect=make_service), \
             patch.object(self.runtime,'user_research_context',return_value=''), \
             patch('research_agent.runtime.ResearchSDKServer',return_value=fake_server), \
             patch('research_agent.runtime.create_deep_agent',return_value=fake_graph), \
             patch('research_agent.evidence_judgments.EvidenceJudgmentService',return_value=judgment) as constructor:
            self.runtime._run(self.directory,job_dir,status['prompt'],Event())
        final=json.loads((job_dir/'status.json').read_text())
        self.assertEqual(final['state'],'completed',final.get('error'))
        return final,constructor,judgment,services[-1],packets[-1]

    def test_off_report_never_constructs_or_calls_optional_jev_even_when_global_shadow_is_enabled(self):
        final,constructor,judgment,service,packet=self.run_report(False)
        self.assertEqual(final['state'],'completed',final.get('error'))
        constructor.assert_not_called();judgment.assess.assert_not_called()
        self.assertIsNone(service.retrieval_judgments)
        self.assertIsNone(service.report_judgments)
        self.assertEqual(packet['selection']['semantic_calls'],0)

    def test_on_report_uses_only_new_relevance_reranker_and_keeps_publication_reviewer(self):
        final,constructor,judgment,service,packet=self.run_report(True)
        self.assertEqual(final['state'],'completed',final.get('error'))
        self.assertEqual(constructor.call_count,1)
        self.assertEqual(constructor.call_args.kwargs['provider'],'jev')
        self.assertEqual(constructor.call_args.kwargs['requested_model'],'jev-1.14.0')
        self.assertGreater(judgment.assess.call_count,0)
        assessed=judgment.assess.call_args.kwargs
        self.assertEqual(assessed['research_task']['original_user_question'],'Generate an ETH report')
        self.assertEqual(assessed['retrieval_subquestion'],'Ethereum synthetic status')
        self.assertTrue(assessed['research_task']['knowledge_cutoff'])
        self.assertEqual(packet['selection']['time_policy']['acquisition_cutoff_source'],'snapshot_at_retrieval')
        self.assertEqual({call.args[0] for call in judgment.assess.call_args_list},{'relevance'})
        self.assertIsNone(service.retrieval_judgments)
        self.assertIsNone(service.report_judgments)
        self.assertIsNotNone(service.reviewer)
        self.assertEqual(packet['selection']['policy'],'passage_hybrid_jev_rank')
        self.assertEqual(packet['selection']['semantic_scores_used'],1)
        self.assertTrue(service.run_metadata['jev_assistance_requested'])


if __name__=='__main__':unittest.main()
