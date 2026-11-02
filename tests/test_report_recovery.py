"""Execution/recovery regressions from a real report's wasted author budget.

Synthetic local commands and fake model responses only; no provider/network call.
"""
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import httpx
from openai import APITimeoutError
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage,HumanMessage,SystemMessage,ToolMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from langchain_core.tools import tool
from pydantic import PrivateAttr

from research_agent.runtime import TraceMiddleware,retain_completed_publications,AgentRuntime
from research_agent.report_study_runtime import CompleteAfterPublication,StudyPolicy,RECOVERED_POLICY,ReportStudyRuntime
from research_agent.settings import Settings
from research_agent.terminal import ResearchShell


class Request(SimpleNamespace):
    def override(self,**values):return Request(**{**self.__dict__,**values})


class OneCallAuthor(BaseChatModel):
    _calls:int=PrivateAttr(default=0)
    @property
    def _llm_type(self):return 'synthetic-one-call-publication-author'
    def bind_tools(self,*arguments,**options):return self
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self._calls+=1
        if self._calls>1:raise AssertionError('A second provider call was attempted after saved publication')
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',
            tool_calls=[{'name':'save_report','args':{},'id':'save-1','type':'tool_call'}]))])


class RetryingPublicationAuthor(OneCallAuthor):
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self._calls+=1
        if self._calls>2:raise AssertionError('The saved publication unnecessarily called the author again')
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',
            tool_calls=[{'name':'publish_report','args':{},'id':f'publication-{self._calls}','type':'tool_call'}]))])


class ReportRecoveryTests(unittest.TestCase):
    def tool_trace(self):
        events=[];working=Mock()
        working.progressive_tool_view.side_effect=lambda job,name,payload,**options:payload
        trace=TraceMiddleware(lambda kind,data:events.append((kind,data)),working_memory=working,job_id='job')
        request=SimpleNamespace(tool_call={'name':'execute','id':'call-1','args':{'command':'synthetic'}})
        return trace,request,working,events

    def test_real_exit_metadata_marks_failure_before_working_memory(self):
        trace,request,working,events=self.tool_trace()
        original=ToolMessage(content='Missing path\n[Command failed with exit code 1]',
            name='execute',tool_call_id='call-1',status='success',artifact={'exit_code':1})
        result=trace.wrap_tool_call(request,lambda _:original)
        self.assertEqual(result.status,'error')
        self.assertEqual(original.status,'success')
        self.assertEqual(working.observe_tool.call_args.kwargs['status'],'error')
        finished=next(data for kind,data in events if kind=='tool_finished')
        self.assertEqual(finished['status'],'error')
        self.assertEqual(finished['exit_code'],1)

    def test_zero_exit_cannot_be_overridden_by_failure_words_in_source_text(self):
        trace,request,working,events=self.tool_trace()
        reply=ToolMessage(content='Publisher wrote: [Command failed with exit code 1]',
            name='execute',tool_call_id='call-1',artifact={'exit_code':0})
        result=trace.wrap_tool_call(request,lambda _:reply)
        self.assertEqual(result.status,'success')
        self.assertEqual(working.observe_tool.call_args.kwargs['status'],'success')
        self.assertEqual(next(data for kind,data in events if kind=='tool_finished')['exit_code'],0)

    def test_missing_exit_metadata_is_not_guessed_from_console(self):
        trace,request,working,events=self.tool_trace()
        reply=ToolMessage(content='[Command failed with exit code 7]',name='execute',tool_call_id='call-1')
        result=trace.wrap_tool_call(request,lambda _:reply)
        self.assertEqual(result.status,'success')
        self.assertEqual(working.observe_tool.call_args.kwargs['status'],'success')
        self.assertIsNone(next(data for kind,data in events if kind=='tool_finished').get('exit_code'))

    def test_timeout_exit_is_error_even_when_content_is_an_offload_pointer(self):
        trace,request,working,events=self.tool_trace()
        reply=ToolMessage(content='Full command output is in an artifact.',name='execute',
            tool_call_id='call-1',artifact={'exit_code':124})
        result=trace.wrap_tool_call(request,lambda _:reply)
        self.assertEqual(result.status,'error')
        self.assertEqual(next(data for kind,data in events if kind=='tool_finished')['exit_code'],124)

    def test_file_tools_do_not_gain_terminal_semantics_from_an_artifact(self):
        trace,request,working,events=self.tool_trace();request.tool_call['name']='read_file'
        reply=ToolMessage(content='Reference data.',name='read_file',tool_call_id='call-1',artifact={'exit_code':1})
        result=trace.wrap_tool_call(request,lambda _:reply)
        self.assertEqual(result.status,'success')
        self.assertEqual(working.observe_tool.call_args.kwargs['status'],'success')

    def test_budget_notice_reaches_model_without_mutating_original_prompt(self):
        events=[];seen=[];trace=TraceMiddleware(lambda kind,data:events.append((kind,data)),settings=Settings(max_model_calls=6))
        trace.model_calls=2
        request=Request(system_message=SystemMessage(content='Original system rules'),messages=[],tools=[])
        result=trace.wrap_model_call(request,lambda actual:seen.append(actual) or SimpleNamespace(result=[AIMessage(content='Proceed')]))
        notice=next(data for kind,data in events if kind=='model_budget_notice')
        self.assertEqual(notice['remaining_author_responses'],4)
        self.assertEqual(notice['limit'],6)
        self.assertIn('4',str(seen[0].system_message.content))
        self.assertIn('Original system rules',str(seen[0].system_message.content))
        self.assertEqual(request.system_message.content,'Original system rules')
        self.assertTrue(result)

    def test_no_early_budget_notice_when_normal_research_budget_remains(self):
        events=[];seen=[];trace=TraceMiddleware(lambda kind,data:events.append((kind,data)),settings=Settings(max_model_calls=24))
        request=Request(system_message=SystemMessage(content='Research rules'),messages=[],tools=[])
        trace.wrap_model_call(request,lambda actual:seen.append(actual) or SimpleNamespace(result=[AIMessage(content='Proceed')]))
        self.assertFalse(any(kind=='model_budget_notice' for kind,_ in events))
        self.assertEqual(seen[0].system_message.content,'Research rules')

    def test_soft_notice_never_bypasses_the_actual_author_limit(self):
        events=[];handler=Mock(return_value=SimpleNamespace(result=[AIMessage(content='Proceed')]))
        trace=TraceMiddleware(lambda kind,data:events.append((kind,data)),settings=Settings(max_model_calls=6))
        request=Request(system_message=SystemMessage(content='Rules'),messages=[],tools=[])
        for _ in range(6):trace.wrap_model_call(request,handler)
        with self.assertRaises(RuntimeError):trace.wrap_model_call(request,handler)
        self.assertEqual(handler.call_count,6)
        self.assertTrue(all(data['remaining_author_responses']>0 for kind,data in events if kind=='model_budget_notice'))

    def test_actual_virtual_path_failure_explains_relative_recovery_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace=Path(directory);(workspace/'data').mkdir();(workspace/'data/local.json').write_text('kept')
            shell=ResearchShell(workspace,Settings())
            code="from pathlib import Path; print(Path('/data/aether-synthetic-missing-3234.json').read_text())"
            result=shell.execute(shlex.join(['python','-c',code]))
            self.assertNotEqual(result.exit_code,0)
            self.assertIn('FileNotFoundError',result.output)
            self.assertIn('./data',result.output)
            self.assertIn('RESEARCH_WORKSPACE',result.output)
            self.assertEqual((workspace/'data/local.json').read_text(),'kept')

    def test_normal_relative_command_is_unchanged_and_has_no_recovery_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace=Path(directory);(workspace/'data').mkdir();(workspace/'data/local.json').write_text('kept')
            shell=ResearchShell(workspace,Settings())
            result=shell.execute(shlex.join(['python','-c',"from pathlib import Path; print(Path('./data/local.json').read_text())"]))
            self.assertEqual(result.exit_code,0)
            self.assertEqual(result.output.strip(),'kept')

    def test_empty_publication_list_never_makes_a_failed_task_successful(self):
        status={'state':'failed','error':'Provider unavailable'};before=dict(status)
        result=retain_completed_publications(status,SimpleNamespace(published_reports=[]))
        self.assertIs(result,status)
        self.assertEqual(status,before)
        self.assertEqual(retain_completed_publications({'state':'failed'},None),{'state':'failed'})

    def test_saved_publication_survives_later_failure_without_relabelling_task(self):
        status={'state':'failed','error':'Author finalize failed'}
        retain_completed_publications(status,SimpleNamespace(published_reports=['/reports/research-abc.md']))
        self.assertEqual(status['state'],'failed')
        self.assertEqual(status['error'],'Author finalize failed')
        self.assertEqual(status['publication_state'],'saved_before_interruption')
        self.assertEqual(status['published_reports'],['/reports/research-abc.md'])

    def test_publication_paths_are_deduplicated_without_dropping_versions(self):
        service=SimpleNamespace(published_reports=['/reports/one.md','/reports/two.md','/reports/one.md'])
        status=retain_completed_publications({'state':'failed'},service)
        self.assertEqual(status['published_reports'],['/reports/one.md','/reports/two.md'])
        self.assertEqual(len(service.published_reports),3)

    def test_host_completion_needs_both_actual_saved_paths_and_report(self):
        for paths,last in (([],None),([],{'draft':{}}),(['/reports/saved.md'],None)):
            with self.subTest(paths=paths,last=last):
                events=[];handler=Mock(return_value='ordinary-author-loop')
                completion=CompleteAfterPublication(SimpleNamespace(published_reports=paths,last_report=last),
                    lambda kind,data:events.append((kind,data)))
                self.assertEqual(completion.wrap_model_call(None,handler),'ordinary-author-loop')
                handler.assert_called_once_with(None)
                self.assertEqual(events,[])

    def test_host_completion_is_explicitly_marked_and_does_not_call_author(self):
        events=[];handler=Mock()
        completion=CompleteAfterPublication(SimpleNamespace(published_reports=['/reports/saved.md'],last_report={'draft':{}}),
            lambda kind,data:events.append((kind,data)))
        response=completion.wrap_model_call(None,handler)
        handler.assert_not_called()
        self.assertTrue(response.result[0].additional_kwargs['aether_host_completion'])
        event=next(data for kind,data in events if kind=='host_publication_completion')
        self.assertEqual(event['provider_requests'],0)
        self.assertEqual(event['published_reports'],['/reports/saved.md'])

    def test_actual_agent_graph_finishes_at_hard_limit_without_another_author_call(self):
        service=SimpleNamespace(published_reports=[],last_report=None);events=[]
        @tool
        def save_report()->str:
            """Synthetic unit fixture recording that the publication gate saved a report."""
            service.published_reports=['/reports/synthetic.md'];service.last_report={'draft':{'title':'Synthetic'}}
            return 'Saved by the synthetic publication fixture.'
        model=OneCallAuthor()
        trace=TraceMiddleware(lambda kind,data:events.append((kind,data)),settings=Settings(max_model_calls=1))
        completion=CompleteAfterPublication(service,lambda kind,data:events.append((kind,data)))
        graph=create_agent(model,tools=[save_report],system_prompt='Synthetic source-aware publication test.',middleware=[completion,trace])
        result=graph.invoke({'messages':[HumanMessage(content='Save a synthetic report.')]})
        self.assertEqual(model._calls,1)
        self.assertEqual(trace.model_calls,1)
        self.assertTrue(result['messages'][-1].additional_kwargs['aether_host_completion'])
        self.assertEqual(sum(kind=='model_finished' for kind,_ in events),1)
        self.assertEqual(sum(kind=='host_publication_completion' for kind,_ in events),1)

    def test_recovered_policy_is_explicit_bounded_and_does_not_replace_legacy_defaults(self):
        legacy=StudyPolicy()
        self.assertEqual((legacy.author_limit,legacy.review_limit),(12,2))
        self.assertFalse(legacy.finish_after_publish)
        self.assertTrue(legacy.duplicate_support_checks)
        self.assertEqual((legacy.http_timeout,legacy.http_max_retries),(60,0))
        self.assertEqual((RECOVERED_POLICY.author_limit,RECOVERED_POLICY.review_limit),(24,4))
        self.assertTrue(RECOVERED_POLICY.finish_after_publish)
        self.assertTrue(RECOVERED_POLICY.selective_judgments)
        self.assertFalse(RECOVERED_POLICY.duplicate_support_checks)
        self.assertEqual((RECOVERED_POLICY.http_timeout,RECOVERED_POLICY.http_max_retries),(120,1))
        self.assertIsNone(AgentRuntime.build_finalization_middleware(None,None,None))
        for invalid in ({'author_limit':33},{'review_limit':5},{'wall_limit':1801},{'memory_budget':12001},
                {'http_timeout':121},{'http_max_retries':2}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):StudyPolicy(**invalid)

    def test_publication_api_timeout_becomes_retryable_tool_error_and_memory_issue(self):
        trace,request,working,events=self.tool_trace()
        request.tool_call['name']='publish_report'
        error=APITimeoutError(request=httpx.Request('POST','https://example.invalid/source-review'))
        result=trace.wrap_tool_call(request,Mock(side_effect=error))
        self.assertIsInstance(result,ToolMessage)
        self.assertEqual(result.status,'error')
        self.assertEqual(result.name,'publish_report')
        self.assertEqual(result.tool_call_id,'call-1')
        self.assertEqual(working.observe_tool.call_args.kwargs['status'],'error')
        payload=working.observe_tool.call_args.args[2]
        self.assertEqual(payload['status'],'error')
        self.assertNotIn('published_reports',payload)
        self.assertIn('tool_failed',[kind for kind,_ in events])

    def test_api_failure_does_not_hide_a_programming_exception(self):
        trace,request,working,events=self.tool_trace()
        request.tool_call['name']='publish_report'
        with self.assertRaisesRegex(TypeError,'Synthetic programming bug'):
            trace.wrap_tool_call(request,Mock(side_effect=TypeError('Synthetic programming bug')))
        self.assertEqual(working.observe_tool.call_args.kwargs['status'],'error')

    def test_real_agent_graph_can_retry_timed_out_publication_without_false_success(self):
        service=SimpleNamespace(published_reports=[],last_report=None);events=[];attempts=[]
        working=Mock();working.context.return_value={'text':'Synthetic continuation notes.'}
        working.progressive_tool_view.side_effect=lambda job,name,payload,**options:payload
        @tool
        def publish_report()->str:
            """Synthetic publication fixture: first source-review timeout, then successful save."""
            attempts.append('attempt')
            if len(attempts)==1:
                if service.published_reports:raise AssertionError('Timeout was falsely marked as published')
                raise APITimeoutError(request=httpx.Request('POST','https://example.invalid/source-review'))
            service.published_reports=['/reports/synthetic-recovered.md'];service.last_report={'draft':{'title':'Synthetic recovered'}}
            return 'Saved only after the synthetic second review.'
        model=RetryingPublicationAuthor()
        trace=TraceMiddleware(lambda kind,data:events.append((kind,data)),settings=Settings(max_model_calls=2),working_memory=working,job_id='job')
        completion=CompleteAfterPublication(service,lambda kind,data:events.append((kind,data)))
        graph=create_agent(model,tools=[publish_report],system_prompt='Synthetic source-aware publication recovery test.',middleware=[completion,trace])
        result=graph.invoke({'messages':[HumanMessage(content='Publish a synthetic source-checked report.') ]})
        self.assertEqual(len(attempts),2)
        self.assertEqual(model._calls,2)
        self.assertEqual(trace.model_calls,2)
        failures=[m for m in result['messages'] if isinstance(m,ToolMessage) and m.status=='error']
        self.assertEqual(len(failures),1)
        self.assertEqual(failures[0].name,'publish_report')
        self.assertEqual(service.published_reports,['/reports/synthetic-recovered.md'])
        self.assertTrue(result['messages'][-1].additional_kwargs['aether_host_completion'])
        self.assertTrue(any(call.kwargs['status']=='error' for call in working.observe_tool.call_args_list))
        self.assertEqual(sum(kind=='host_publication_completion' for kind,_ in events),1)


if __name__=='__main__':unittest.main()
