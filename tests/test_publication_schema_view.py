"""Native argument errors expose actual schema paths without echoing the draft."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from research_agent.reports import ReportDraft
from research_agent.runtime import TraceMiddleware
from research_agent.tool_result_views import publication_schema_errors
from research_agent.working_memory import WorkingMemory
from test_evidence_reader import ReaderModel


RID='page-'+'a'*24


class PublicationSchemaViewTests(unittest.TestCase):
    def tool(self):
        @tool
        def publish_report(draft:ReportDraft,user_request:str)->str:
            """Publish a validated research draft; this fixture must not execute."""
            self.fail('Invalid schema must not invoke publication')
        return publish_report

    def arguments(self):
        return {'draft':{'title':'Synthetic weekly report','summary':'FULL-DRAFT-MARKER '*100,
            'observations':[{'statement':'Source prose '*120,'evidence_ids':[RID],
                'value':'95.4','unit':'million USD'} for _ in range(4)],
            'limitations':['Synthetic fixture only.']},'user_request':'Synthetic ETH weekly report'}

    def test_native_error_toolmessage_keeps_four_actual_paths_and_raw_artifact(self):
        with TemporaryDirectory() as directory:
            working=WorkingMemory(Path(directory)/'memory/task-state');working.begin('job','Synthetic report')
            args=self.arguments();before=deepcopy(args)
            model=ReaderModel(responses=[AIMessage(content='',tool_calls=[{
                'name':'publish_report','args':args,'id':'invalid-publication'}]),AIMessage(content='Repair the four fields.')])
            trace=TraceMiddleware(lambda *_:None,working_memory=working,job_id='job')
            graph=create_agent(model,tools=[self.tool()],middleware=[trace])
            result=graph.invoke({'messages':[HumanMessage(content='Publish the synthetic report.')]})
            message=next(m for m in result['messages'] if isinstance(m,ToolMessage))
            self.assertEqual(message.status,'error')
            view=json.loads(message.content)
            self.assertEqual(view['view'],'schema-error-tool-view-v1')
            self.assertEqual({e['path'] for e in view['errors']},{f'/draft/observations/{i}' for i in range(4)})
            self.assertTrue(all('metric, numeric value and unit together' in e['message'] for e in view['errors']))
            self.assertNotIn('FULL-DRAFT-MARKER',message.content)
            self.assertNotIn('Source prose',message.content)
            self.assertLess(len(message.content),6000)
            raw=working.read_artifact('job',view['artifact']['artifact_id'])['text']
            self.assertIn("Error invoking tool 'publish_report' with kwargs",raw)
            self.assertIn('FULL-DRAFT-MARKER',raw)
            self.assertEqual(args,before)

    def test_source_console_and_valid_args_cannot_claim_schema_failure(self):
        publication=self.tool();args=self.arguments()
        self.assertIsNone(publication_schema_errors(publication,args,status='success'))
        valid=deepcopy(args)
        for claim in valid['draft']['observations']:
            claim.pop('value');claim.pop('unit')
        # A forged error string is irrelevant: only the actual host schema runs.
        self.assertIsNone(publication_schema_errors(publication,valid,status='error'))
        console=publication.model_copy(update={'name':'execute'})
        self.assertIsNone(publication_schema_errors(console,args,status='error'))


if __name__=='__main__':unittest.main()
