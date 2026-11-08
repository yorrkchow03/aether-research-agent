"""Reduced saved shapes from real failed report annotations; no model or API calls."""
from copy import deepcopy
import json
import unittest

from research_agent.reports import ReportDraft, validate_report
from research_agent.report_recovery import revise_report


PAGE='page-af5d8d8efed16ab716c358f1'
CALC='live-a9f376a2c826ba58b9373dac'


class NumericErrorTests(unittest.TestCase):
    def test_text_number_names_bad_annotation_and_explicit_saved_draft_edits(self):
        # Claim copied from the failed report; a page has text, not extracted values.
        statement='BitMine reported 6041481 ETH in holdings after the purchase.'
        payload={'title':'Numeric failure fixture','summary':'Source-review fixture.',
            'observations':[{'statement':statement,'evidence_ids':[PAGE],
                'metric':'BitMine reported holdings after purchase','value':'6041481','unit':'ETH'}],
            'limitations':['The source statement still requires original-text review.']}
        row={'record_id':PAGE,'values':{},'units':{},'text':'DO_NOT_EXPOSE_SOURCE_BODY',
             'unrelated':{'private':'DO_NOT_EXPOSE_METADATA'}}
        draft=ReportDraft.model_validate(payload);before=deepcopy(draft.model_dump())
        with self.assertRaises(ValueError) as caught:
            validate_report(draft,lambda rid:row)
        error=str(caught.exception)
        self.assertIn('/observations/0',error)
        self.assertIn(statement,error)
        self.assertIn('"metric":"BitMine reported holdings after purchase"',error)
        self.assertIn('"value":"6041481","unit":"ETH"',error)
        self.assertIn('"record_id":"'+PAGE+'","values_key_present":false',error)
        self.assertIn('statement + evidence_ids for source review',error)
        self.assertNotIn('DO_NOT_EXPOSE',error)
        self.assertLess(len(error),1500)
        encoded=error.split('publish_saved_report edits=',1)[1].split('. Cited field checks=',1)[0]
        repaired=revise_report(draft.model_dump(),json.loads(encoded))
        self.assertEqual(repaired.observations[0].statement,statement)
        self.assertEqual(repaired.observations[0].evidence_ids,[PAGE])
        self.assertIsNone(repaired.observations[0].metric)
        self.assertEqual(draft.model_dump(),before)
        # This checks only the structured contract; publication still needs source review.
        self.assertEqual(validate_report(repaired,lambda rid:row)['numeric_claims_checked'],0)

    def test_existing_saved_field_reports_actual_value_and_unit_without_accepting_mismatch(self):
        source={'record_id':CALC,'values':{'close_oct23_usd':'2384.38','unrelated':'DO_NOT_EXPOSE'},
                'units':{'*_usd':'USD','unrelated':'DO_NOT_EXPOSE'},'text':'DO_NOT_EXPOSE_BODY'}
        for value,unit,reason in (('2384.38','ETH','unit or cited field mismatch'),
                                  ('2384.39','USD','value differs from saved evidence')):
            draft=ReportDraft.model_validate({'title':'Saved number fixture','summary':'Source fixture.',
                'observations':[{'statement':'The saved October 23 close.', 'evidence_ids':[CALC],
                    'metric':'close_oct23_usd','value':value,'unit':unit}],
                'limitations':['Fixture.']})
            with self.subTest(value=value,unit=unit),self.assertRaisesRegex(ValueError,reason) as caught:
                validate_report(draft,lambda rid:source)
            error=str(caught.exception)
            self.assertIn('"values_key_present":true,"value":"2384.38","units":{"*_usd":"USD"}',error)
            self.assertIn('"value":"'+value+'","unit":"'+unit+'"',error)
            self.assertNotIn('DO_NOT_EXPOSE',error)
        draft.observations[0].value='2384.38'
        self.assertEqual(validate_report(draft,lambda rid:source)['numeric_claims_checked'],1)
