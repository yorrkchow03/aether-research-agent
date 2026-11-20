import json
from pathlib import Path
import tempfile
import threading
import unittest
from dataclasses import dataclass, replace

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from research_agent.question_tracking import bind_question, question_result_payload, question_tool, register_plan_questions
from research_agent.runtime import TraceMiddleware
from research_agent.working_memory import WorkingMemory


@dataclass
class Request:
    state: dict
    tool_call: dict

    def override(self, **changes):
        return replace(self, **changes)


class QuestionTrackingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.job = 'test-job'
        self.memory = WorkingMemory(Path(directory.name))
        self.memory.begin(self.job, 'Investigate two questions', '2026-08-01T00:00:00Z')
        self.todos = [{'content':'[Q1] Has the proposal passed?', 'status':'in_progress'},
                      {'content':'[Q2] Has deployment occurred?', 'status':'pending'}]
        register_plan_questions(self.memory, self.job, self.todos)
        self.tracer = TraceMiddleware(lambda *unused: None, working_memory=self.memory, job_id=self.job)

    def test_question_scoping_and_skip_before_execution(self):
        state = {'todos':self.todos}
        count = []
        def lookup(request):
            count.append(request.tool_call['id'])
            return ToolMessage(content='{"results":[]}', tool_call_id=request.tool_call['id'], name='recall_memory')
        for i, qid in enumerate(['Q1','Q1','Q1','Q2']):
            call = {'name':'recall_memory','id':str(i),'args':{'kind':'news','query':'test','question_id':qid}}
            result=self.tracer.wrap_tool_call(Request(state,call), lookup)
            if i==2:
                self.assertEqual(json.loads(result.content)['status'],'no_progress')
        self.assertEqual(count,['0','1','3'])
        self.assertEqual(len(self.memory.get_question(self.job,'Q2')['attempts']),1)
        broad={'name':'recall_memory','args':{'kind':'news','facet_queries':['first','second']}}
        self.assertIsNone(bind_question(self.memory,self.job,broad,state))

    def test_focused_lookup_with_no_active_question_can_correct_its_scope(self):
        mixed = [{'content':'Collect July sources','status':'in_progress'},
                 *[{**t, 'status':'pending'} for t in self.todos]]
        state = {'todos': mixed}
        actual_ids = []
        def lookup(request):
            actual_ids.append(request.tool_call['id'])
            return ToolMessage(content='{"results":[]}', tool_call_id=request.tool_call['id'], name='recall_memory')
        for call_id, kwargs in [('ambiguous', {}), ('whole-period', {'question_id':'shared'}),
                               ('focused', {'question_id':'Q2'})]:
            call = {'id':call_id, 'name':'recall_memory', 'args':{'kind':'news','query':'July', **kwargs}}
            reply = self.tracer.wrap_tool_call(Request(state, call), lookup)
            if call_id == 'ambiguous':
                self.assertEqual(reply.status, 'error')
                self.assertIn('question_id', reply.content)
        self.assertEqual(actual_ids, ['whole-period','focused'])
        self.assertFalse(self.memory.get_question(self.job, 'Q1')['attempts'])
        self.assertEqual(len(self.memory.get_question(self.job, 'Q2')['attempts']), 1)
        exact_url = {'name':'find_saved_news_url','args':{'url':'https://example.org','question_id':'Q2'}}
        self.assertEqual(bind_question(self.memory, self.job, exact_url, state)['question_id'], 'Q2')

    def test_parallel_reopen_uses_checked_source_question_before_active_todo(self):
        rid = 'page-' + 'e' * 24
        self.memory.record_question_result(self.job, 'Q2', 'read_evidence', {'record_id':rid},
            {'record':{'record_id':rid}}, tool_call_id='checked-under-Q2')
        self.memory.record_question_result(self.job, 'Q1', 'recall_memory', {'kind':'news'},
            {'results':[{'record_id':rid}]}, tool_call_id='candidate-for-Q1')
        args = {'record_id':rid}
        call = {'name':'read_evidence','args':args}
        state = {'todos':self.todos}
        self.assertEqual(bind_question(self.memory, self.job, call, state)['question_id'], 'Q2')
        call['args'] = {**args, 'question_id':'Q1'}
        self.assertEqual(bind_question(self.memory, self.job, call, state)['question_id'], 'Q1')

    def test_cancel_after_reader_result_preserves_one_observation(self):
        events = []
        cancel = threading.Event()
        tracer = TraceMiddleware(lambda kind, data: events.append(kind), cancel=cancel,
                                 working_memory=self.memory, job_id=self.job)
        call = {'name':'task','id':'stopped-child',
                'args':{'description':'[Q1] Inspect publication', 'subagent_type':'evidence-reader'}}
        def child(request):
            cancel.set()
            return Command(update={'messages':[ToolMessage(content='{"status":"incomplete","gaps":["Finish reading"]}',
                                                           tool_call_id=call['id'],name='task')]})
        with self.assertRaises(RuntimeError):
            tracer.wrap_tool_call(Request({'todos':self.todos}, call), child)
        row = self.memory.get_question(self.job, 'Q1')
        self.assertEqual(len(row['observations']), 1)
        self.assertIn('Finish reading', row['gaps'])
        self.assertNotIn('working_memory_error', events)

    def test_native_task_return_and_untagged_reader(self):
        call = {'id':'task-a','name':'task','args':{'description':'Check the record','subagent_type':'evidence-reader'}}
        def child(request):
            self.assertTrue(request.tool_call['args']['description'].startswith('[Q1]'))
            return Command(update={'messages':[ToolMessage(content='{"status":"incomplete","gaps":["Check deployment receipt"]}',
                                                           tool_call_id='task-a',name='task')]})
        result=self.tracer.wrap_tool_call(Request({'todos':self.todos},call),child)
        self.assertIn('Check deployment',question_result_payload(call,result))
        row=self.memory.get_question(self.job,'Q1')
        self.assertIn('Check deployment receipt', row['gaps'])
        self.assertEqual(row['status'],'needs_evidence')
        single=question_tool(self.memory,self.job).invoke({'question_id':'Q1'})
        self.assertIn('Check deployment receipt',single)

    def test_repeated_scoped_source_choice_pauses_once_and_preserves_other_tools(self):
        events = []
        tracer = TraceMiddleware(lambda kind, data: events.append((kind, data)),
                                 working_memory=self.memory, job_id=self.job)
        tools = [{'name':'recall_memory'}, {'name':'read_evidence'}, {'name':'submit_research_answer'},
                 {'name':'publish_report'}, {'name':'publish_saved_report'}]
        request = ModelRequest(model=FakeMessagesListChatModel(responses=[]), messages=[], tools=tools,
                               state={'todos':self.todos})
        called = []

        def choose(menu=request):
            captured = []
            def handler(observed):
                captured.extend(observed.tools)
                return ModelResponse(result=[AIMessage(content='Fixture choice')])
            tracer.wrap_model_call(menu, handler)
            return captured

        def lookup(call_id, qid='Q1'):
            args = {'kind':'news', 'query':'source fixture', 'question_id':qid}
            call = {'name':'recall_memory', 'args':args, 'id':call_id}
            def handler(_):
                called.append(call_id)
                return ToolMessage(name='recall_memory', tool_call_id=call_id, content='{"results":[]}')
            return tracer.wrap_tool_call(Request({'todos':self.todos}, call), handler)

        for index in range(2):
            self.assertEqual(choose(), tools)
            lookup(f'actual-{index}')
        self.assertEqual(choose(), tools)
        self.assertEqual(json.loads(lookup('blocked-1').content)['status'], 'no_progress')
        self.assertEqual(json.loads(lookup('blocked-parallel').content)['status'], 'no_progress')
        self.assertEqual(choose(), tools)
        lookup('new-scope-Q2', qid='Q2')
        self.assertEqual(choose(), tools)
        lookup('blocked-2')
        self.assertEqual(choose(), tools)
        lookup('blocked-3')
        self.assertEqual(choose(), tools[1:])
        self.assertEqual(choose(), tools)
        self.assertEqual(called, ['actual-0', 'actual-1', 'new-scope-Q2'])
        changes = [data for kind, data in events if kind == 'question_strategy_changed']
        self.assertEqual([item['question_id'] for item in changes], ['Q1'])

        for index in range(2):
            lookup(f'one-option-{index}')
            self.assertEqual(choose(request.override(tools=tools[:1])), tools[:1])
        self.assertEqual(len([kind for kind,_ in events if kind == 'question_strategy_changed']), 1)
