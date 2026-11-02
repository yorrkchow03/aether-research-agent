"""Investor-facing briefs retain source contracts without forcing a trade view."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from fixtures import fixture_run
from research_agent.reports import ReportDraft, render_report, report_claims, validate_report
from research_agent.tools import ResearchTools


class InvestorReportTests(unittest.TestCase):
    def setUp(self):
        self.rows={
            'source-a':{'record_id':'source-a','values':{'return_fraction':'0.03248045917709286'},
                        'units':{'return_fraction':'fraction'}},
            'source-b':{'record_id':'source-b','values':{},'units':{}}
        }
        self.body={'title':'Synthetic market brief','summary':'A concise source-based summary.',
            'observations':[{'statement':'A reported fixture observation.','evidence_ids':['source-b']}],
            'limitations':['Synthetic input; no claim about actual markets.']}

    def draft(self,**updates):
        body=copy.deepcopy(self.body);body.update(updates)
        return ReportDraft.model_validate(body)

    def test_default_brief_does_not_require_an_investment_judgment(self):
        draft=self.draft()
        self.assertEqual((draft.judgment,draft.judgment_reason),('informational',''))
        self.assertEqual(draft.forecasts,[]);self.assertIsNone(draft.advice)
        self.assertIsNone(draft.purchase_decision)
        checked=validate_report(draft,self.rows.__getitem__)
        self.assertEqual(checked['status'],'passed')
        self.assertEqual(checked['unique_evidence_records'],1)

    def test_neutral_brief_runs_through_the_actual_publisher_without_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);run=fixture_run(root/'project')
            tools=ResearchTools(run,root/'workspace','2026-10-13T00:00:00Z')
            row=tools._rows('market.coingecko.eth.daily',2)[-1]
            draft=ReportDraft(title='Synthetic archived brief',summary='Saved synthetic fixture observation.',
                observations=[{'statement':'The fixture has a saved price.','evidence_ids':[row['record_id']],
                    'metric':'price_usd','value':row['values']['price_usd'],'unit':'USD'}],
                limitations=['Synthetic fixture; publication interface test only.'])
            result=tools.publish(draft)
            saved=json.loads((tools.workspace/result['structured_path'].lstrip('/')).read_text())
            markdown=(tools.workspace/result['report_path'].lstrip('/')).read_text()
            self.assertEqual(result['status'],'saved')
            self.assertEqual(saved['report']['judgment'],'informational')
            self.assertNotIn('## Research judgment',markdown)
            self.assertNotIn('## Conditional scenarios',markdown)

    def test_key_changes_use_the_same_value_unit_and_source_validation(self):
        change={'statement':'The fixture return changed.','evidence_ids':['source-a'],
            'metric':'return_fraction','value':'0.03248045917709286','unit':'fraction'}
        draft=self.draft(key_changes=[change])
        self.assertEqual(validate_report(draft,self.rows.__getitem__)['numeric_claims_checked'],1)
        self.assertIn(draft.key_changes[0],report_claims(draft))
        for update in ({'value':'3.248045917709286'},{'unit':'percent'},{'evidence_ids':['source-b']}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                validate_report(self.draft(key_changes=[{**change,**update}]),self.rows.__getitem__)
        with self.assertRaises(KeyError):
            validate_report(self.draft(key_changes=[{**change,'evidence_ids':['not-saved']}]),self.rows.__getitem__)

    def test_neutral_defaults_do_not_relax_existing_observation_checks(self):
        draft=self.draft(observations=[{'statement':'Wrong fixture value.','evidence_ids':['source-a'],
            'metric':'return_fraction','value':'9','unit':'fraction'}])
        with self.assertRaisesRegex(ValueError,'differs from saved evidence'):
            validate_report(draft,self.rows.__getitem__)

    def test_explicit_judgments_need_reasons_and_directional_views_need_interpretation(self):
        for judgment in ('consider','wait','avoid','insufficient_evidence'):
            with self.subTest(judgment=judgment),self.assertRaises(ValueError):
                self.draft(judgment=judgment,judgment_reason=' \n')
        for judgment in ('consider','wait','avoid'):
            with self.subTest(judgment=judgment),self.assertRaises(ValueError):
                validate_report(self.draft(judgment=judgment,judgment_reason='A test reason.'),self.rows.__getitem__)
        self.assertEqual(validate_report(self.draft(judgment='insufficient_evidence',
            judgment_reason='Limited fixture evidence.'),self.rows.__getitem__)['status'],'passed')
        bypass=self.draft().model_copy(update={'judgment':'wait','judgment_reason':''})
        with self.assertRaisesRegex(ValueError,'nonblank reason'):
            validate_report(bypass,self.rows.__getitem__)

    def test_absent_optional_content_does_not_create_empty_prediction_or_advice_sections(self):
        markdown=render_report(self.draft(),'2026-10-13T00:00:00Z',{},[])
        self.assertIn('## Main facts',markdown)
        for heading in ('## Interpretation','## Recent key changes','## Questions to follow up',
                        '## Conditional outlook','## Conditional scenarios','## Research judgment',
                        '## Decision context','## Conditional research suggestion'):
            self.assertNotIn(heading,markdown)
        self.assertNotIn('No directional forecast is committed',markdown)
        self.assertIn('<summary>Source checks and technical provenance</summary>',markdown)
        self.assertIn('../records/source-b.json',markdown)

    def test_followup_questions_are_rendered_as_open_checks_not_source_facts(self):
        question='Has the announced upgrade actually activated?'
        draft=self.draft(questions_to_watch=[question])
        markdown=render_report(draft,'2026-10-13T00:00:00Z',{},[])
        self.assertIn('## Questions to follow up',markdown)
        self.assertIn('not confirmed developments',markdown)
        self.assertGreater(markdown.index(question),markdown.index('## Questions to follow up'))
        self.assertEqual(len(report_claims(draft)),1)
        self.assertNotIn(question,[c.statement for c in report_claims(draft)])
        with self.assertRaises(ValueError):self.draft(questions_to_watch=[' '])
        with self.assertRaises(ValueError):self.draft(questions_to_watch=['Question']*7)

    def test_numeric_display_and_rendering_leave_exact_claim_values_unchanged(self):
        draft=self.draft(key_changes=[{'statement':'Fixture return.','evidence_ids':['source-a'],
            'metric':'return_fraction','value':'0.03248045917709286','unit':'fraction'}])
        before=draft.model_dump()
        markdown=render_report(draft,'2026-10-13T00:00:00Z',{},[])
        self.assertIn('Approximate percentage: 3.24805%',markdown)
        self.assertIn('0.03248045917709286',markdown)
        self.assertIn('<summary>Exact saved numeric field</summary>',markdown)
        self.assertEqual(draft.model_dump(),before)
        self.assertLess(markdown.index('## Recent key changes'),markdown.index('## Main facts'))

    def test_optional_scenario_preserves_numbered_support_and_counterevidence_links(self):
        draft=self.draft(forecasts=[{'statement':'A synthetic conditional possibility.',
            'horizon_days':7,'assumptions':['A stated condition holds.'],
            'supporting_evidence_ids':['source-b'],'counter_evidence_ids':['source-a'],
            'uncertainty':'This is not a price estimate.'}])
        markdown=render_report(draft,'2026-10-13T00:00:00Z',{},[])
        self.assertIn('## Conditional scenarios',markdown)
        self.assertIn('[1](../records/source-b.json)',markdown)
        self.assertIn('[2](../records/source-a.json)',markdown)
        self.assertNotIn('[Source]',markdown)

    def test_legacy_structure_loads_with_unchanged_values_and_optional_fields_empty(self):
        body={**self.body,'judgment':'insufficient_evidence','judgment_reason':'A limited archived study.'}
        draft=ReportDraft.model_validate(body)
        self.assertEqual(draft.judgment_reason,body['judgment_reason'])
        self.assertEqual(draft.observations[0].model_dump(),{
            'statement':body['observations'][0]['statement'],'evidence_ids':['source-b'],
            'metric':None,'value':None,'unit':None})
        self.assertEqual((draft.key_changes,draft.questions_to_watch),([],[]))
        self.assertEqual(validate_report(draft,self.rows.__getitem__)['status'],'passed')


if __name__=='__main__':unittest.main()
