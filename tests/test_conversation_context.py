"""Ordinary chat reuses saved material; focused new facts retain source review."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from fixtures import project_fixture
from research_agent.conversation_context import saved_chat_context, validate_chat_links
from research_agent.planning import ResearchPlanningMiddleware
from research_agent.runtime import AgentRuntime
from research_agent.tools import ResearchTools


RID='live-'+'a'*24
REPORT='research-'+'b'*12


class ChatModel(FakeMessagesListChatModel):
    http_client: Any=None
    calls: list=Field(default_factory=list)
    tool_names: list=Field(default_factory=list)

    def bind_tools(self,tools,**kwargs):
        self.tool_names.append([tool.get('name',tool.get('function',{}).get('name')) if isinstance(tool,dict) else tool.name for tool in tools])
        return self

    def _generate(self,messages,*args,**kwargs):
        self.calls.append(messages)
        return super()._generate(messages,*args,**kwargs)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=project_fixture(Path(temp.name))
        self.runtime=AgentRuntime(root=self.root)
        self.runtime.settings=replace(self.runtime.settings,api_key='synthetic-test-key',memory_enabled=False,
                                      judgment_mode='off',search_enabled=False)
        self.meta=self.runtime.create_session(model_mode='ark_plan',research_mode='current')
        self.sid=self.meta['session_id']
        self.workspace=self.runtime._session_dir(self.sid)/'workspace'
        self.row={'record_id':RID,'dataset_id':'live.price.eth','asset':'ETH','observed_at':'2026-01-01T00:00:00Z',
                  'retrieved_at':'2026-01-02T00:00:00Z','available_at':'2026-01-02T00:00:00Z',
                  'values':{'price_usd':'2000.50'},'units':{'price_usd':'USD'},
                  'source':{'name':'SYNTHETIC fixture','url':'https://example.org/synthetic-price'}}
        self.runtime.knowledge.register(self.row)
        self.runtime.knowledge.retain_report(REPORT,self.sid,{'created_at':'2026-01-03T00:00:00Z',
            'review':{'status':'reviewed'},'report':{'title':'SYNTHETIC earlier brief','summary':'A dated synthetic view.',
            'observations':[{'statement':'Synthetic price was $2,000.50.','evidence_ids':[RID]}],
            'limitations':['This is synthetic historical data.']}})

    def service(self):
        return ResearchTools(Path(self.meta['data_run']),self.workspace,self.meta['cutoff'],
                             allow_live=True,knowledge=self.runtime.knowledge,live_data=self.runtime.live_data)

    def test_context_reuses_dates_values_and_report_without_network_and_preserves_budget(self):
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('No collection')):
            context=saved_chat_context(self.service(),[],self.sid,'2027-01-01T00:00:00Z',budget_chars=4000)
        self.assertEqual(context['observations'][0]['values'],{'price_usd':'2000.50'})
        self.assertEqual(context['observations'][0]['observed_at'],'2026-01-01T00:00:00Z')
        self.assertEqual(context['previous_reports'][0]['report_id'],REPORT)
        self.assertLessEqual(len(json.dumps(context,ensure_ascii=False)),4000)
        strict=self.service();strict.mode='strict'
        old=saved_chat_context(strict,[],self.sid,'2026-01-01T00:00:00Z')
        self.assertEqual(old['observations'],[])
        self.assertEqual(old['previous_reports'],[])

    def test_explicit_planning_gate_can_allow_saved_reads_without_relaxing_research(self):
        gate=ResearchPlanningMiddleware('new',allow_saved_context=True)
        marker=object()
        for name in ('read_evidence','recall_memory','read_file'):
            request=SimpleNamespace(tool_call={'name':name,'args':{'file_path':'/reports/saved.json'},'id':'x'},state={})
            self.assertIs(gate.wrap_tool_call(request,lambda _:marker),marker)
        for name in ('search_web','request_public_api','execute','publish_report'):
            request=SimpleNamespace(tool_call={'name':name,'args':{},'id':'x'},state={})
            self.assertEqual(gate.wrap_tool_call(request,lambda _:self.fail('Must plan new work')).status,'error')

    def test_direct_chat_citations_reject_unknown_or_foreign_session_records(self):
        service=self.service()
        good=f'[1](/api/sessions/{self.sid}/evidence/{RID})'
        self.assertEqual(validate_chat_links(good,service,self.sid)['evidence_ids'],[RID])
        for link in (f'[1](/api/sessions/{"f"*32}/evidence/{RID})',
                     f'[1](/api/sessions/{self.sid}/evidence/page-{"f"*24})'):
            with self.assertRaises((ValueError,KeyError,OSError)):
                validate_chat_links(link,service,self.sid)

    def test_normal_market_followup_finishes_in_one_model_turn_without_forced_submission(self):
        reply=f'Synthetic fixture: the saved price on 1 January was $2,000.50. [1](/api/sessions/{self.sid}/evidence/{RID})'
        model=ChatModel(responses=[AIMessage(content=reply)])
        fake_server=SimpleNamespace(start=lambda:'/synthetic-local-socket',close=lambda:None)
        with patch.object(self.runtime,'build_author_model',return_value=model), \
             patch('research_agent.runtime.ResearchSDKServer',return_value=fake_server), \
             patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected fetch')), \
             patch('research_agent.review.EvidenceReviewer.review_claims',side_effect=AssertionError('No new research review')):
            status=self.runtime.start(self.sid,'最近ETH的行情怎么样',request_purpose='chat')
            self.assertEqual(status['response_mode'],'conversation')
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                current=next(job for job in self.runtime.snapshot(self.sid)['jobs'] if job['job_id']==status['job_id'])
                if current['state'] not in ('running','waiting_for_conversation'):break
                time.sleep(.02)
        self.assertEqual(current['state'],'completed',current.get('error'))
        self.assertEqual(current['answer'],reply)
        self.assertEqual(current['published_reports'],[])
        self.assertNotIn('verified_answer',current)
        self.assertEqual(current['citation_validation']['evidence_ids'],[RID])
        self.assertEqual(len(model.calls),1)
        self.assertTrue(any({'task','write_todos'}<=set(names) for names in model.tool_names))
        self.assertIn('SYNTHETIC earlier brief',str(model.calls[0]))
        events=(self.runtime._session_dir(self.sid)/'jobs'/status['job_id']/'events.jsonl').read_text()
        self.assertNotIn('answer_pending',events)
        self.assertNotIn('plan_saved',events)

    def test_explicit_deep_research_selects_research_without_requesting_a_report(self):
        # Inspect host routing without dispatching any model or background work.
        with patch('research_agent.runtime.threading.Thread'):
            status=self.runtime.start(self.sid,'请深入研究ETH本周的网络升级和市场情况，不用生成报告',request_purpose='chat')
        self.assertEqual(status['response_mode'],'research')
        self.assertEqual(status['workflow_selection'],'explicit_research_request')
        self.assertEqual(status['deliverable'],'answer')

    def test_initial_chat_can_choose_topic_reader_and_submit_source_checked_answer(self):
        from langchain_core.runnables import RunnableLambda
        delegated=[]
        def read_topic(state):
            delegated.append(state)
            return {'messages':[AIMessage(content=json.dumps({'status':'completed',
                'findings':[{'statement':'The saved source concerns a testnet only.','evidence_ids':[RID]}]}))]}
        worker={'name':'evidence-reader','mode':'isolated',
            'description':'Read one coherent public-source theme.','runnable':RunnableLambda(read_topic)}
        reply='Synthetic source-checked testnet qualification.'
        model=ChatModel(responses=[
            AIMessage(content='',tool_calls=[{'name':'task','args':{
                'description':'Compare the saved public upgrade notices and keep testnet/mainnet scope distinct.','subagent_type':'evidence-reader'},'id':'reader'}]),
            AIMessage(content='',tool_calls=[{'name':'submit_research_answer','args':{
                'draft':{'facts':[{'statement':reply,'evidence_ids':[RID]}]}},'id':'submit'}]),
            AIMessage(content='Done.'),
        ])
        original=self.runtime.build_research_tools
        def build(*args,**kwargs):
            service=original(*args,**kwargs)
            def submit(draft):
                service.submitted_answers.append({'rendered':reply})
                return {'status':'saved'}
            service.submit_answer=submit
            return service
        fake_server=SimpleNamespace(start=lambda:'/synthetic-local-socket',close=lambda:None)
        with patch.object(self.runtime,'build_author_model',return_value=model), \
             patch.object(self.runtime,'build_research_tools',side_effect=build), \
             patch('research_agent.evidence_reader.build_evidence_reader',return_value=worker), \
             patch('research_agent.runtime.ResearchSDKServer',return_value=fake_server):
            status=self.runtime.start(self.sid,'这里几个升级公告讲的是一回事吗？',request_purpose='chat')
            self.assertEqual(status['response_mode'],'conversation')
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                current=next(job for job in self.runtime.snapshot(self.sid)['jobs'] if job['job_id']==status['job_id'])
                if current['state'] not in ('running','waiting_for_conversation'):break
                time.sleep(.02)
        self.assertEqual(current['state'],'completed',current.get('error'))
        self.assertEqual(len(delegated),1)
        self.assertTrue(current['research_escalated'])
        self.assertEqual(current['answer'],reply)
        self.assertIn('verified_answer',current)

    def test_fresh_collection_still_requires_formal_answer_submission(self):
        plan=[{'content':'Fetch one missing synthetic quote','status':'in_progress'},
              {'content':'Verify and submit the new observation','status':'pending'}]
        reply='Synthetic source-checked observation.'
        model=ChatModel(responses=[
            AIMessage(content='',tool_calls=[{'name':'request_public_api','args':{'provider':'coinbase','path':'/products/ETH-USD/ticker'},'id':'fetch'}]),
            AIMessage(content='Unsubmitted synthetic observation.'),
            AIMessage(content='',tool_calls=[{'name':'submit_research_answer','args':{'draft':{'facts':[{'statement':reply,'evidence_ids':[RID]}]}},'id':'submit'}]),
            AIMessage(content='Done.'),
        ])
        original=self.runtime.build_research_tools
        def build(*args,**kwargs):
            service=original(*args,**kwargs)
            service.public_api=lambda *args,**kwargs:{'status':'ok','record_id':RID}
            def submit(draft):
                service.submitted_answers.append({'rendered':reply})
                return {'status':'saved'}
            service.submit_answer=submit
            return service
        fake_server=SimpleNamespace(start=lambda:'/synthetic-local-socket',close=lambda:None)
        with patch.object(self.runtime,'build_author_model',return_value=model), \
             patch.object(self.runtime,'build_research_tools',side_effect=build), \
             patch('research_agent.runtime.ResearchSDKServer',return_value=fake_server):
            status=self.runtime.start(self.sid,'Fetch a fresh ETH quote',request_purpose='chat')
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                current=next(job for job in self.runtime.snapshot(self.sid)['jobs'] if job['job_id']==status['job_id'])
                if current['state'] not in ('running','waiting_for_conversation'):break
                time.sleep(.02)
        self.assertEqual(current['state'],'completed',current.get('error'))
        self.assertTrue(current['research_escalated'])
        self.assertEqual(current['answer'],reply)
        self.assertIn('verified_answer',current)
        events=(self.runtime._session_dir(self.sid)/'jobs'/status['job_id']/'events.jsonl').read_text()
        self.assertIn('answer_pending',events)


class ConversationViewTests(unittest.TestCase):
    def test_chat_keeps_long_prior_qualification_and_original_tool_pairs(self):
        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import HumanMessage,ToolMessage
        from research_agent.conversation_context import ConversationWindowMiddleware
        old=AIMessage(content='',tool_calls=[{'id':'old','name':'recall_memory','args':{}}])
        earlier='Context '*2000+'The reported date was proposed for a testnet only.'
        messages=[HumanMessage(content='Older question'),old,ToolMessage(content=earlier,tool_call_id='old'),
            AIMessage(content='Earlier reviewed summary'),HumanMessage(content='Explain it.\n\n<saved_reference_data>dated pointers</saved_reference_data>')]
        tools=[{'name':'read_evidence'},{'name':'submit_research_answer'},
               {'type':'function','function':{'name':'publish_report'}},{'name':'task'},{'name':'write_todos'}]
        request=ModelRequest(model=ChatModel(responses=[AIMessage(content='unused')]),messages=messages,tools=tools)
        actual=ConversationWindowMiddleware().wrap_model_call(request,lambda current:current)
        self.assertEqual(actual.messages,messages)
        self.assertEqual(actual.tools,tools)
        self.assertIn('testnet only',actual.messages[2].content)


if __name__=='__main__':unittest.main()
