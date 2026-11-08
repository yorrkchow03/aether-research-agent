import json
import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
from research_agent.report_context import delivery_view,CompleteAfterPublication,ReportContextMiddleware
from research_agent.source_views import focused_source_view
from research_agent.tool_result_views import recommended_view_budget


def result(name,obj,identifier):
    return ToolMessage(name=name,content=json.dumps(obj),tool_call_id=identifier)


class ReportContextTests(unittest.TestCase):
    def test_draft_repair_keeps_source_history_tool_pairs_and_tool_choices(self):
        class Request(SimpleNamespace):
            def override(self,**changes):return Request(**{**vars(self),**changes})
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
