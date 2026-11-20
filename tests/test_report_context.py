import json
import unittest
from types import SimpleNamespace

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
from research_agent.prompts import research_system_prompt
from research_agent.report_context import delivery_view,CompleteAfterPublication,ReportContextMiddleware,report_checkpoint_tool
from research_agent.source_views import focused_source_view
from research_agent.tool_result_views import recommended_view_budget


def result(name,obj,identifier):
    return ToolMessage(name=name,content=json.dumps(obj),tool_call_id=identifier)


class Request(SimpleNamespace):
    def override(self, **changes):
        return Request(**{**vars(self), **changes})


class ReportContextTests(unittest.TestCase):
    def test_periodic_report_prompt_leaves_method_to_skill(self):
        meta = {'cutoff': '2026-10-25T00:00:00Z', 'mode': 'observed'}
        prompt = research_system_prompt(meta, True, True, periodic=True)
        self.assertIn('/skills/research/SKILL.md', prompt)
        self.assertIn('independent source review', prompt)
        for legacy_instruction in ('fund flows', 'reading order', 'Use date-scoped', 'scope and publish', 'write_todos'):
            self.assertNotIn(legacy_instruction, prompt)
        self.assertIn('For broad period reports follow this reading order', research_system_prompt(meta, True, True))

    def test_periodic_report_tracks_workflow_phases_past_legacy_cutoff(self):
        phase = 'prepare'
        verified = False
        captured = []
        events = []

        def reserve_delivery():
            nonlocal phase
            self.assertEqual(phase, 'verify')
            self.assertTrue(verified)
            phase = 'draft'

        workflow = SimpleNamespace(phase=lambda: phase, has_verified_evidence=lambda: verified,
                                   pending_originals=lambda: captured, reserve_delivery=reserve_delivery)
        service = SimpleNamespace(latest_report_draft_id=None, used_records=('candidate',))
        names = ['get_research_leads', 'write_todos', 'task', 'read_evidence', 'save_report_draft', 'publish_report', 'publish_saved_report']
        request = ModelRequest(model=FakeMessagesListChatModel(responses=[]),
            messages=[HumanMessage(content='Create an ETH weekly report.'), result('register_calculation', {}, 'old')],
            tools=[{'name': name} for name in names])
        middleware = ReportContextMiddleware(lambda kind, event: events.append((kind, event)), service,
                                              max_model_calls=10, periodic_workflow=workflow)

        def turn(expected):
            selected = middleware.wrap_model_call(request, lambda value: value)
            self.assertEqual([tool['name'] for tool in selected.tools], expected)
            self.assertEqual(selected.messages, request.messages)
            return str(selected.system_message.content)

        turn(names[:4])
        phase = 'discover'
        for _ in range(5):
            turn(names[:4])
        phase = 'verify'
        turn(['task', 'read_evidence'])
        captured.append(('Q1', 'page-' + 'a' * 24))
        guidance = turn(['read_evidence'])
        self.assertIn('Q1 page-' + 'a' * 24, guidance)
        verified = True
        turn(['save_report_draft'])
        phase = 'review'
        service.latest_report_draft_id = 'draft-test'
        guidance = turn(['publish_report', 'publish_saved_report'])
        self.assertIn('/drafts/draft-test.json', guidance)
        self.assertIn('publish_saved_report and empty edits', guidance)
        with self.assertRaisesRegex(RuntimeError, 'authoring budget ended'):
            middleware.wrap_model_call(request, lambda value: value)
        self.assertEqual([(kind, data['phase'], data['author_call']) for kind, data in events], [
            ('report_author_phase', 'prepare', 1), ('report_author_phase', 'discover', 2),
            ('report_author_phase', 'verify', 7), ('report_author_phase', 'draft', 9),
            ('report_author_phase', 'review', 10),
        ])

    def test_periodic_repeat_of_deterministic_preflight_targets_exact_optional_row(self):
        original = HumanMessage(content='Create a weekly report.')

        def rejected(path, identifier):
            return ToolMessage(name='publish_saved_report', tool_call_id=identifier, status='error',
                content=f'Source-data validation failed at {path}. Unverified example value 12345.')

        first = rejected('/observations/4', 'first')
        same_row = rejected('/observations/4', 'second')
        other_row = rejected('/observations/2', 'different')
        names = ['publish_report', 'publish_saved_report', 'read_evidence']
        request = ModelRequest(model=FakeMessagesListChatModel(responses=[]), messages=[original, first],
                               tools=[{'name': name} for name in names])
        middleware = ReportContextMiddleware(service=SimpleNamespace(latest_report_draft_id='draft-test'),
            max_model_calls=32, periodic_workflow=SimpleNamespace(phase=lambda: 'review'))

        for history in ([original, first], [original, first, other_row]):
            control = middleware.wrap_model_call(request.override(messages=history), lambda value: value)
            self.assertEqual([tool['name'] for tool in control.tools], names)
            self.assertNotIn('use an exact remove edit', str(control.system_message.content))

        history = [original, first, same_row]
        repair = middleware.wrap_model_call(request.override(messages=history), lambda value: value)
        self.assertEqual([tool['name'] for tool in repair.tools], ['publish_saved_report'])
        self.assertEqual(repair.messages, history)
        guidance = str(repair.system_message.content)
        self.assertIn('use an exact remove edit on /observations/4', guidance)
        self.assertIn('Keep checked rows intact', guidance)
        self.assertNotIn('12345', guidance)

    def test_successful_calculation_offers_early_checkpoint_then_allows_research(self):
        names = ['save_report_draft','read_evidence','search_web','publish_report','publish_saved_report','write_todos']
        request = Request(messages=[HumanMessage(content='A report'),result('register_calculation',{},'calculation')],
                          tools=[{'name': name} for name in names],system_message=None)
        service = SimpleNamespace(latest_report_draft_id=None)
        middleware = ReportContextMiddleware(service=service,max_model_calls=60)
        checkpoint = middleware.wrap_model_call(request,lambda value:value)
        self.assertEqual([tool['name'] for tool in checkpoint.tools],['save_report_draft'])
        service.latest_report_draft_id = 'a'*24
        follow_up = middleware.wrap_model_call(request,lambda value:value)
        self.assertEqual(follow_up.tools,request.tools)
        for _ in range(16):
            delivery = middleware.wrap_model_call(request,lambda value:value)
        self.assertNotIn('search_web',[tool['name'] for tool in delivery.tools])
        for _ in range(11):
            delivery = middleware.wrap_model_call(request,lambda value:value)
        self.assertIn('read_evidence',[tool['name'] for tool in delivery.tools])
        for _ in range(3):
            submission = middleware.wrap_model_call(request,lambda value:value)
            self.assertEqual([tool['name'] for tool in submission.tools],['publish_report','publish_saved_report'])
        with self.assertRaisesRegex(RuntimeError,'authoring budget ended'):
            middleware.wrap_model_call(request,lambda value:value)

    def test_tool_checkpoints_only_authorized_scoped_model_draft(self):
        saved = []
        evidence_id='live-'+'a'*24

        def lookup(record_id):
            if record_id != evidence_id:
                raise ValueError('Unknown saved evidence')
            return {'record_id': record_id}

        service = SimpleNamespace(report_allowed=True,report_request_prompt='给我近一个月的报告',
            require_report_scope=True,_report_window_matches=lambda scope: scope.start=='2026-10-18T00:00:00Z',
            allow_live=True,lookup=lookup,
            _check_delivery_cancelled=lambda:None,_save_report_draft=lambda draft: saved.append(draft) or 'a'*24)
        tool = report_checkpoint_tool(service)
        draft = {'title':'ETH research','summary':'A source-based checkpoint.',
                 'observations':[{'statement':'Saved price observation.','evidence_ids':[evidence_id]}],
                 'scope':{'start':'2026-10-18T00:00:00Z','end':'2026-11-18T00:00:00Z','assets':['ETH']},
                 'judgment':'insufficient_evidence','judgment_reason':'Research in progress.',
                 'limitations':['Coverage remains incomplete.']}
        report_request=service.report_request_prompt
        service.report_request_prompt='之前的报告在哪里？'
        with self.assertRaisesRegex(ValueError,'explicit report request'):
            tool.invoke({'draft':draft,'user_request':report_request})
        service.report_request_prompt=report_request
        invalid={**draft,'observations':[{'statement':'Unknown observation.','evidence_ids':['live-'+'b'*24]}]}
        with self.assertRaisesRegex(ValueError,'Unknown saved evidence'):
            tool.invoke({'draft':invalid,'user_request':service.report_request_prompt})
        self.assertFalse(saved)
        body = json.loads(tool.invoke({'draft':draft}))
        self.assertEqual(body,{'status':'draft_saved','draft_id':'a'*24,'published':False})
        self.assertEqual(len(saved),1)
        self.assertEqual(service.last_report_checkpoint_id,'a'*24)

    def test_bounded_checkpoint_retries_leave_time_for_discovery_and_delivery(self):
        names = ['save_report_draft','read_evidence','recall_memory','publish_report','publish_saved_report','task']
        request = Request(messages=[HumanMessage(content='A report'),result('read_evidence',{},'source')],
                          tools=[{'name': name} for name in names],system_message=None)
        phases = []
        middleware = ReportContextMiddleware(lambda _, phase: phases.append(phase),
                                             SimpleNamespace(),max_model_calls=17)
        for _ in range(2):
            self.assertEqual(middleware.wrap_model_call(request,lambda r:r).tools,request.tools)
        for _ in range(2):
            selected=middleware.wrap_model_call(request,lambda r:r)
            self.assertEqual([tool['name'] for tool in selected.tools],['save_report_draft'])
        self.assertEqual(middleware.wrap_model_call(request,lambda r:r).tools,request.tools)
        failed=ToolMessage(name='publish_report',content='Formatting error',tool_call_id='publish',status='error')
        delivery=middleware.wrap_model_call(request.override(messages=[*request.messages,failed]),lambda r:r)
        self.assertNotIn('task',[tool['name'] for tool in delivery.tools])
        self.assertEqual([(p['phase'],p['author_call']) for p in phases],
                         [('discovery',1),('checkpoint',3),('discovery',5),('delivery',6)])

    def test_draft_repair_keeps_source_history_tool_pairs_and_tool_choices(self):
        publish=AIMessage(content='',tool_calls=[{'name':'publish_report','args':{'draft':{}},'id':'publish'},
            {'name':'write_todos','args':{},'id':'plan'}])
        messages=[HumanMessage(content='Save the requested report.'),
            result('read_file',{'qualification':'Only a testnet proposal, not a mainnet activation.'},'source'),
            publish,ToolMessage(name='publish_report',content='Unsupported claim O2',tool_call_id='publish',status='error'),
            ToolMessage(name='write_todos',content='saved',tool_call_id='plan'),HumanMessage(content='Continue the saved request.')]
        tools=[{'name':n} for n in ('publish_report','publish_saved_report','read_evidence','write_todos')]
        request=Request(messages=messages,system_message=None,tools=tools)
        middleware=ReportContextMiddleware(service=SimpleNamespace(latest_report_draft_id='a'*24))
        actual=middleware.wrap_model_call(request,lambda r:r)
        self.assertEqual(actual.tools,tools)
        self.assertEqual(actual.messages,messages)
        self.assertIn('publish_saved_report',str(actual.system_message.content))
        self.assertIn('/drafts/'+'a'*24+'.json',str(actual.system_message.content))
        self.assertIn('Unsupported claim O2',str(actual.messages))
        self.assertIn('Only a testnet proposal',str(actual.messages))

    def test_failed_publication_keeps_repair_loop_success_ends_without_provider_call(self):
        service=SimpleNamespace(published_reports=[],last_report=None)
        middleware=CompleteAfterPublication(service,lambda *_:None)
        marker=object()
        self.assertIs(middleware.wrap_model_call(None,lambda _:marker),marker)
        service.published_reports=['/reports/saved.md'];service.last_report={'path':service.published_reports[0]}
        result=middleware.wrap_model_call(None,lambda _:self.fail('No extra provider request'))
        self.assertTrue(result.result[0].additional_kwargs['aether_host_completion'])
    def test_reading_and_calculation_do_not_retire_earlier_discovery_qualifications(self):
        recall=result('recall_memory',{'retrieval_unit':'passage','artifact':{'path':'/saved.json','sha256':'actual'},
            'results':[{'excerpt':'The proposed change applies only to the testnet.'}],
            'gaps':['uncovered dates'],'conflicts':[{'evidence_ids':['page-a','page-b']}]},'recall')
        read=result('read_evidence',{'record':{'text':'A second source describes a tentative date.'}},'read')
        calc=result('register_calculation',{'dataset_id':'live.calculation','values':{'return':-1},'units':{'return':'percent'}},'calc')
        worker=result('task',{'status':'completed','findings':[{'quote':'exact worker quote'}]},'worker')
        duplicate=result('read_evidence',json.loads(read.content),'duplicate')
        messages=[recall,read,worker,calc,duplicate]
        actual,_,changed=delivery_view(messages)
        self.assertEqual(actual,messages)
        self.assertEqual(changed,0)
        self.assertIn('applies only to the testnet',actual[0].content)

    def test_source_view_slightly_above_old_6k_limit_keeps_exact_slices(self):
        view=focused_source_view({'record_id':'page-a','text':'exact source '*300},'source')
        obj={'record':view,'note':'context '*300}
        self.assertGreater(recommended_view_budget(obj),len(json.dumps(obj,ensure_ascii=False)))
