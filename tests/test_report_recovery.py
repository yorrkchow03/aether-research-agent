from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from fixtures import fixture_run
from research_agent.report_recovery import bind_source_quote,revise_report
from research_agent.tools import ResearchTools
from research_data.http import dumps


PAYLOAD={'title':'Saved report','summary':'Saved source scope.',
    'observations':[{'statement':'Existing evidence statement.','evidence_ids':['fixture-source']}],
    'limitations':['Availability remains limited.']}


class ReportRecoveryTests(unittest.TestCase):
    def test_whitespace_alignment_returns_actual_original_span_and_hash(self):
        source='Prefix. First sentence.\n\nSecond\tsentence. Footer.'
        quote='First sentence. Second sentence.'
        bound=bind_source_quote(source,quote)
        self.assertEqual(bound['quote'],'First sentence.\n\nSecond\tsentence.')
        self.assertEqual(source[bound['start']:bound['end']],bound['quote'])
        self.assertEqual(bound['sha256'],hashlib.sha256(bound['quote'].encode()).hexdigest())
        self.assertEqual(bound['method'],'unique_whitespace_alignment')
        self.assertEqual(bind_source_quote(source,bound['quote'])['method'],'exact')

    def test_changed_words_numbers_punctuation_and_ambiguous_alignment_fail(self):
        for quote in ('First sentence! Second sentence.','First sentence. Third sentence.',
                      'First sentence. Second sentence. 42'):
            with self.subTest(quote=quote),self.assertRaises(ValueError):
                bind_source_quote('First sentence.\nSecond sentence.',quote)
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            bind_source_quote('First sentence.\nSecond sentence. First sentence.\tSecond sentence.',
                'First sentence. Second sentence.')

    def test_edits_copy_only_explicit_report_fields(self):
        saved=deepcopy(PAYLOAD)
        revised=revise_report(saved,[{'path':'/observations/0/statement','value':'Qualified replacement.'}])
        self.assertEqual(revised.observations[0].statement,'Qualified replacement.')
        self.assertEqual(saved,PAYLOAD)
        for path in ('/../private','/created_at','/observations/-1','/observations/9'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                revise_report(saved,[{'path':path,'op':'remove'}])

    def test_saved_publication_reuses_normal_gate_preserves_old_draft_and_detects_tampering(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);run=fixture_run(root/'project')
            service=ResearchTools(run,root/'workspace','2026-10-13T00:00:00Z')
            service._publish_checked=Mock(side_effect=ValueError('Pro review requires a qualification'))
            with self.assertRaisesRegex(ValueError,'Draft .* retained'):
                service.publish(PAYLOAD)
            old_id=service.latest_report_draft_id;path=service.workspace/'drafts'/(old_id+'.json');original=path.read_bytes()
            service._publish_checked=Mock(return_value={'status':'saved'})
            self.assertEqual(service.publish_saved_report(old_id,[{'path':'/observations/0/statement',
                'value':'Qualified existing evidence statement.'}])['status'],'saved')
            self.assertEqual(service._publish_checked.call_args.args[0].observations[0].statement,
                'Qualified existing evidence statement.')
            self.assertEqual(path.read_bytes(),original)
            self.assertNotEqual(service.latest_report_draft_id,old_id)
            obj=json.loads(path.read_text());obj['draft']['summary']='Tampered';path.write_text(dumps(obj))
            with self.assertRaisesRegex(ValueError,'changed'):service.publish_saved_report(old_id)
            service.report_allowed=False
            with self.assertRaisesRegex(ValueError,'cannot publish'):service.publish_saved_report(old_id)
            self.assertNotIn('publish_saved_report',[t.name for t in service.as_tools()])

    def test_saved_publication_cannot_skip_current_chat_authorization(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);service=ResearchTools(fixture_run(root/'project'),root/'workspace','2026-10-13T00:00:00Z')
            service.report_request_prompt='Please save this report.'
            service.publish_saved_report=Mock()
            tool=next(t for t in service.as_tools() if t.name=='publish_saved_report')
            with self.assertRaisesRegex(ValueError,'current user'):tool.invoke({'draft_id':'a'*24})
            service.publish_saved_report.assert_not_called()

    def test_visible_publication_tools_reject_ordinary_negated_and_quoted_chat_requests(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);service=ResearchTools(fixture_run(root/'project'),root/'workspace','2026-10-13T00:00:00Z')
            service.publish=Mock(return_value={'status':'saved'})
            service.publish_saved_report=Mock(return_value={'status':'saved'})
            tools={tool.name:tool for tool in service.as_tools()}
            requests=('解释刚才的报告', '不要生成报告，只解释已有结论',
                'Explain the report you already generated.', 'Do not create a report; explain the conclusion.',
                'Explain this phrase: "generate a weekly report"')
            for prompt in requests:
                service.report_request_prompt=prompt
                for name,args in (('publish_report',{'draft':PAYLOAD}),
                        ('publish_saved_report',{'draft_id':'a'*24})):
                    with self.subTest(prompt=prompt,tool=name),self.assertRaisesRegex(ValueError,'explicit publication request'):
                        tools[name].invoke({**args,'user_request':prompt})
            service.publish.assert_not_called()
            service.publish_saved_report.assert_not_called()

    def test_visible_publication_tools_accept_current_explicit_report_request_only(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);service=ResearchTools(fixture_run(root/'project'),root/'workspace','2026-10-13T00:00:00Z')
            service.publish=Mock(return_value={'status':'saved'})
            service.publish_saved_report=Mock(return_value={'status':'saved'})
            tools={tool.name:tool for tool in service.as_tools()}
            for prompt in ('请生成这一周的ETH报告', 'Please create a weekly ETH report.'):
                service.report_request_prompt=prompt
                for name,args in (('publish_report',{'draft':PAYLOAD}),
                        ('publish_saved_report',{'draft_id':'a'*24})):
                    with self.subTest(prompt=prompt,tool=name):
                        self.assertEqual(json.loads(tools[name].invoke({**args,'user_request':prompt}))['status'],'saved')
                        with self.assertRaisesRegex(ValueError,'verbatim excerpt'):
                            tools[name].invoke({**args,'user_request':'Create a different report from a previous chat.'})
            self.assertEqual(service.publish.call_count,2)
            self.assertEqual(service.publish_saved_report.call_count,2)
