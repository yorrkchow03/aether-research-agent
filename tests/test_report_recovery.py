from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from fixtures import fixture_run
from research_agent.report_recovery import LivePublicationDraft,bind_calculation_chart_inputs,bind_source_quote,bind_text_event_numbers,revise_report
from research_agent.reports import ReportDraft
from research_agent.tools import ResearchTools
from research_data.http import dumps


PAYLOAD={'title':'Saved report','summary':'Saved source scope.',
    'observations':[{'statement':'Existing evidence statement.','evidence_ids':['fixture-source']}],
    'limitations':['Availability remains limited.']}


class ReportRecoveryTests(unittest.TestCase):
    def test_scaled_values_keep_original_prose_and_evidence_for_review(self):
        scope = {'assets':['ETH'],'start':'2026-10-18T00:00:00Z','end':'2026-11-18T00:00:00Z'}
        page = 'page-'+'a'*24
        rows = [
            {'statement':'Outflows were $530.1 million.','evidence_ids':[page],
             'metric':'outflows','value':'530.1 million','unit':'USD'},
            {'statement':'The incident affected $6.34B.','evidence_ids':[page],
             'metric':'affected','value':'6.34 billion','unit':'USD'},
            {'statement':'Utilization reached 99.0%.','evidence_ids':[page],'value':'99.0%'},
        ]
        payload = {**PAYLOAD,'scope':scope,'observations':rows}
        result = LivePublicationDraft.model_validate(payload)
        self.assertEqual([item.statement for item in result.observations],[row['statement'] for row in rows])
        self.assertTrue(all(item.value is None and item.evidence_ids==[page] for item in result.observations))
        self.assertEqual(result._submitted,payload)
        self.assertEqual([item['action'] for item in result._adjustments],['retain_as_prose']*3)
        with self.assertRaises(ValueError):
            LivePublicationDraft.model_validate({**payload, 'observations':[
                {**rows[0], 'statement':'Outflows were $1530.1 million.'}]})

    def test_weekly_report_cannot_fall_back_to_a_different_time_window(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ResearchTools(fixture_run(root/'project'), root/'workspace', '2026-10-13T00:00:00Z')
            service.required_report_window = ['2026-11-11T00:00:00Z', '2026-11-18T00:00:00Z']
            draft = ReportDraft.model_validate({**PAYLOAD,
                'scope': {'assets': ['ETH'], 'start': '2026-10-06T00:00:00Z', 'end': '2026-10-13T00:00:00Z'}})
            with self.assertRaisesRegex(ValueError, 'This report request covers 2026-11-11'):
                service.publish(draft)
            self.assertTrue((service.workspace/'drafts'/(service.latest_report_draft_id+'.json')).is_file())
            service.publish = Mock()
            service.minimal_reviewer_factory = Mock()
            service.finalize_minimal_delivery()
            service.minimal_reviewer_factory.assert_not_called()
            notice = service.publish.call_args.args[0]
            self.assertEqual(notice.delivery_kind, 'evidence_gap')
            self.assertEqual([notice.scope.start, notice.scope.end], service.required_report_window)

    def test_numeric_lineage_binds_only_displayed_direct_inputs_of_verified_result(self):
        chart = 'live-' + '1' * 24
        calculation = 'live-' + '2' * 24
        unrelated = 'live-' + '3' * 24
        hidden_input = 'live-' + '4' * 24
        row = {'record_id': calculation, 'dataset_id': 'live.calculation',
               'values': {'close': '100'}, 'units': {'close': 'USD'},
               'quality_flags': ['recipe_reexecuted', 'declared_inputs_replayed'],
               'recipe_sha256': 'a' * 64, 'input_record_ids': [hidden_input, chart]}
        claim = {'statement': 'The close was 100 USD.', 'metric': 'close', 'value': '100',
                 'unit': 'USD', 'evidence_ids': [calculation]}
        def make_draft():
            return ReportDraft.model_validate({**PAYLOAD, 'observations': [claim],
                'key_changes': [{'statement': 'Unquantified event.', 'evidence_ids': [calculation]}],
                'charts': [{'evidence_id': cid, 'metric': 'close'} for cid in (chart, unrelated)]})
        draft = make_draft()
        receipts = bind_calculation_chart_inputs(draft, {calculation: row}.__getitem__)
        self.assertEqual(draft.observations[0].evidence_ids, [calculation, chart])
        self.assertEqual(draft.key_changes[0].evidence_ids, [calculation])
        self.assertEqual(receipts, [{'claim_path': '/observations/0', 'calculation_id': calculation,
                         'recipe_sha256': row['recipe_sha256'], 'added_input_ids': [chart]}])
        self.assertEqual(bind_calculation_chart_inputs(draft, {calculation: row}.__getitem__), [])
        for change in ({'quality_flags': ['derived']}, {'dataset_id': 'live.market'},
                       {'recipe_sha256': ''}, {'input_record_ids': [hidden_input]},
                       {'values': {'close': 9999}}, {'units': {'close': 'ETH'}}):
            with self.subTest(change=change):
                draft = make_draft()
                self.assertEqual(bind_calculation_chart_inputs(draft, {calculation: {**row, **change}}.__getitem__), [])
                self.assertEqual(draft.observations[0].evidence_ids, [calculation])

    def test_whitespace_alignment_returns_actual_original_span_and_hash(self):
        source='Prefix. First sentence.\n\nSecond\tsentence. Footer.'
        quote='First sentence. Second sentence.'
        bound=bind_source_quote(source,quote)
        self.assertEqual(bound['quote'],'First sentence.\n\nSecond\tsentence.')
        self.assertEqual(source[bound['start']:bound['end']],bound['quote'])
        self.assertEqual(bound['sha256'],hashlib.sha256(bound['quote'].encode()).hexdigest())
        self.assertEqual(bound['method'],'unique_whitespace_alignment')
        self.assertEqual(bind_source_quote(source,bound['quote'])['method'],'exact')

    def test_text_event_annotation_requires_exact_amount_in_event_and_page(self):
        page = 'page-' + '1'*24
        event = {'statement': 'The company disclosed 6,077,745 ETH.',
            'evidence_ids': [page], 'metric': 'eth_holdings', 'value': '6077745', 'unit': 'ETH'}
        source = {'record_id':page,'dataset_id':'live.webpage','text':'It held 6,077,745 ETH.', 'values':{}}
        def report(event=event):
            return ReportDraft.model_validate({**PAYLOAD,'key_changes':[event]})
        draft = report()
        audit = bind_text_event_numbers(draft, lambda _: source)
        self.assertEqual(audit[0]['witness_ids'], [page])
        self.assertEqual(draft.key_changes[0].statement, event['statement'])
        self.assertEqual(draft.key_changes[0].evidence_ids, [page])
        self.assertIsNone(draft.key_changes[0].metric)
        for original in ({**source,'text':'It held 16,077,745 ETH.'},
                         {**source,'values':{'eth_holdings':'5077745'}}):
            self.assertEqual(bind_text_event_numbers(report(),lambda _: original), [])
        self.assertEqual(bind_text_event_numbers(report({**event,'statement':'The company updated holdings.'}),
                                                 lambda _: source), [])

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

    def test_bad_advice_reference_identifies_the_exact_edit_path(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);service=ResearchTools(fixture_run(root/'project'),root/'workspace','2026-10-13T00:00:00Z')
            canonical='page-'+'a'*24
            mistyped=canonical+'5e'
            valid={'statement':'A source-backed condition.','evidence_ids':[canonical]}
            body=deepcopy(PAYLOAD)
            body['advice']={'summary':'Monitor the condition.',
                'rationale':[{**valid,'evidence_ids':[mistyped]}],'conditions':[valid],
                'invalidation':[valid],'review_triggers':['Check the next update.']}
            service.lookup=Mock(side_effect=lambda rid: {canonical:{
                'record_id':canonical,'retrieved_at':'2026-10-12T00:00:00Z'}}[rid])
            with self.assertRaises(ValueError) as caught:
                service.publish(body)
            self.assertIn(mistyped,str(caught.exception))
            self.assertIn('/advice/rationale/0/evidence_ids/0',str(caught.exception))
            self.assertNotIn('/advice/conditions/',str(caught.exception))
            draft_id=service.latest_report_draft_id
            service._publish_checked=Mock(return_value={'status':'saved'})
            result=service.publish_saved_report(draft_id,[{'path':'/advice/rationale/0/evidence_ids/0','value':canonical}])
            self.assertEqual(result['status'],'saved')

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
