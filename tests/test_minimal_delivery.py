import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fixtures import fixture_run
from research_agent.minimal_delivery import evidence_gap_notice,minimal_report,notice_review,verify_notice_review
from research_agent.reports import ReportDraft
from research_agent.review import review_targets,ReviewBudgetExhausted
from research_agent.tools import ResearchTools


class MinimalDeliveryTests(unittest.TestCase):
    def draft_and_review(self):
        draft=ReportDraft(title='An unsupported headline',summary='An unsupported summary.',
            observations=[{'statement':'A source-backed observation.','evidence_ids':['r']},
                          {'statement':'An unsupported conclusion.','evidence_ids':['r']}],
            limitations=['An unsupported limitation.'])
        targets=review_targets(draft)
        review={'targets':targets,'verdicts':[{'claim_id':'O1','status':'supported','scope_alignment':'aligned',
            'quotes':[{'record_id':'r','text':'A source-backed observation.'}]}],
            'revision_required':[{'claim_id':'O2'}]}
        return draft,review

    def test_keeps_exact_supported_atoms_and_discards_all_dependent_prose(self):
        draft,review=self.draft_and_review();reduced,ids=minimal_report(draft,review)
        self.assertEqual(ids,['O1']);self.assertEqual(reduced.observations,[draft.observations[0]])
        self.assertNotIn('unsupported',reduced.title);self.assertNotIn('unsupported',reduced.summary)
        self.assertFalse(reduced.interpretations or reduced.forecasts or reduced.advice)

    def test_unassessed_ambiguous_changed_or_unbound_atoms_never_survive(self):
        for mutation in ('no_verdict','ambiguous','changed','unbound','blocked'):
            draft,review=self.draft_and_review()
            if mutation=='no_verdict':review['verdicts']=[]
            if mutation=='ambiguous':review['verdicts'][0]['scope_alignment']='ambiguous'
            if mutation=='changed':draft.observations[0].statement='Changed after review.'
            if mutation=='unbound':review['verdicts'][0]['quotes'][0]['record_id']='elsewhere'
            if mutation=='blocked':review['revision_required'].append({'claim_id':'O1'})
            with self.subTest(mutation=mutation):self.assertIsNone(minimal_report(draft,review)[0])

    def test_notice_cannot_carry_a_fact_and_receipt_is_bound(self):
        notice=evidence_gap_notice();receipt=notice_review(notice);verify_notice_review(receipt,notice)
        self.assertEqual(receipt['status'],'notice');self.assertEqual(receipt['claims_checked'],0)
        for field,value in [('summary','ETH will rise.'),('observations',[{'statement':'A fact','evidence_ids':['r']}])]:
            body=notice.model_dump();body[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):ReportDraft.model_validate(body)
        receipt['status']='reviewed'
        with self.assertRaises(ValueError):verify_notice_review(receipt,notice)

    def test_legacy_report_serialization_is_unchanged(self):
        draft,_=self.draft_and_review();self.assertNotIn('delivery_kind',draft.model_dump())
        with self.assertRaises(ValueError):ReportDraft(title='empty',summary='empty',observations=[],limitations=['gap'])

    def service(self,root):
        return ResearchTools(fixture_run(root/'project'),root/'workspace','2026-10-13T00:00:00Z')

    def test_no_approved_facts_publishes_notice_without_model_call(self):
        with TemporaryDirectory() as tmp:
            s=self.service(Path(tmp));s.reviewer=SimpleNamespace(calls=8,max_requests=8,settings=SimpleNamespace(model_id='fixture'))
            s.minimal_reviewer_factory=Mock(side_effect=AssertionError('No factual candidate'))
            result=s.finalize_minimal_delivery()
            self.assertEqual(result['delivery_kind'],'evidence_gap')
            self.assertEqual(result['review']['status'],'notice');self.assertEqual(len(s.published_reports),1)
            s.minimal_reviewer_factory.assert_not_called()
            with self.assertRaises(ValueError):s.finalize_minimal_delivery()

    def test_minimal_failure_uses_notice_once_and_never_approves_original(self):
        with TemporaryDirectory() as tmp:
            s=self.service(Path(tmp));draft,review=self.draft_and_review()
            s.last_report_review={'draft':draft.model_dump(),'review':review}
            s.reviewer=SimpleNamespace(calls=8,max_requests=8,settings=SimpleNamespace(model_id='fixture'))
            last=SimpleNamespace(calls=2,max_requests=2)
            s.minimal_reviewer_factory=Mock(return_value=last)
            original=s.publish
            def submit(body):
                if body.delivery_kind=='minimal':raise ReviewBudgetExhausted('Final audit unavailable')
                return original(body)
            s.publish=submit
            result=s.finalize_minimal_delivery(draft=draft)
            self.assertEqual(result['delivery_kind'],'evidence_gap')
            self.assertEqual(s.delivery_metadata['normal_requests_used'],8)
            self.assertEqual(s.delivery_metadata['final_requests_used'],2)
            s.minimal_reviewer_factory.assert_called_once()

    def test_stop_at_entry_or_final_commit_never_publishes_notice(self):
        for at_commit in (False,True):
            with TemporaryDirectory() as tmp:
                s=self.service(Path(tmp));s.cancel=threading.Event()
                if at_commit:
                    check=s._check_delivery_cancelled;calls=[]
                    def stop():
                        calls.append(1)
                        if len(calls)>=4:s.cancel.set()
                        check()
                    s._check_delivery_cancelled=stop
                else:s.cancel.set()
                with self.subTest(at_commit=at_commit),self.assertRaises(RuntimeError):s.publish(evidence_gap_notice())
                self.assertFalse(s.published_reports)


if __name__=='__main__':unittest.main()
