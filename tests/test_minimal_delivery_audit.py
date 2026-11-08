"""Independent regressions for canonical draft recovery and final commit safety."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fixtures import fixture_run
from research_agent.audit import BundleBuilder
from research_agent.minimal_delivery import evidence_gap_notice
from research_agent.report_presentation import ReportScope
from research_agent.replay import replay_bundle
from research_agent.reports import ReportDraft
from research_agent.review import review_targets
from research_agent.settings import Settings
from research_agent.tools import ResearchTools


class MinimalDeliveryAuditTests(unittest.TestCase):
    def service(self, root):
        return ResearchTools(fixture_run(root/'project'),root/'workspace',
                             '2026-10-13T00:00:00Z',
                             audit_builder=BundleBuilder(root/'project',root/'bundles'))

    def test_stop_received_after_bundle_build_prevents_report_commit(self):
        with TemporaryDirectory() as tmp:
            service=self.service(Path(tmp));service.cancel=Event();events=[]
            def stop_on_bundle(kind,data):
                events.append(kind)
                if kind=='bundle_created':service.cancel.set()
            service.event_sink=stop_on_bundle
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                service.publish(evidence_gap_notice())
            self.assertIn('bundle_created',events)
            self.assertFalse(service.published_reports)
            self.assertFalse(list((service.workspace/'reports').glob('research-*.json')))
            self.assertIsNone(service.last_report)

    def test_scoped_and_unscoped_notices_cold_replay_as_notice_never_approval(self):
        for scoped in (False,True):
            with self.subTest(scoped=scoped),TemporaryDirectory() as tmp:
                service=self.service(Path(tmp));service.report_user_prompt='Synthetic fixture: assess available ETH sources.'
                scope=ReportScope(assets=['ETH'],start='2026-10-06T00:00:00Z',end='2026-10-13T00:00:00Z') if scoped else None
                saved=service.publish(evidence_gap_notice(scope))
                receipt=saved['audit_package']
                replay=replay_bundle(receipt['path'],Settings(),expected=receipt)
                self.assertEqual(saved['review']['status'],'notice')
                self.assertEqual(replay['status'],'passed')
                self.assertEqual(replay['review_quote_validation']['semantic_status'],'notice')
                self.assertEqual(replay['review_quote_validation']['claims_checked'],0)

    def test_canonical_bound_draft_is_recovered_for_the_reserved_final_audit(self):
        with TemporaryDirectory() as tmp:
            service=self.service(Path(tmp));rid='page-'+'c'*24
            source={'record_id':rid,'text':'Synthetic fixture: the upgrade\nwas delayed.',
                    'dataset_id':'live.webpage','retrieved_at':'2026-10-12T00:00:00Z',
                    'source':{'name':'Synthetic fixture'}}
            service.lookup=lambda _:source
            draft=ReportDraft(title='Synthetic fixture',summary='A synthetic scope statement.',
                observations=[{'statement':'The upgrade was delayed.','evidence_ids':[rid]}],
                documents=[{'evidence_id':rid,'quote':'the upgrade was delayed.',
                            'relevance':{'statement':'A saved status notice.','evidence_ids':[rid]}}],
                limitations=['Synthetic fixture only.'])
            captured=[]
            def interrupted(bound,bindings):
                captured.append(bound)
                raise RuntimeError('Synthetic author interruption before completion')
            with patch.object(service,'_publish_checked',side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError,'Synthetic author interruption'):
                    service.publish(draft)
            bound=captured[0]
            self.assertIn('\n',bound.documents[0].quote)
            saved=json.loads((service.workspace/'drafts'/(service.latest_report_draft_id+'.json')).read_text())['draft']
            self.assertEqual(saved,bound.model_dump())
            service.last_report_review={'draft':bound.model_dump(),'review':{
                'targets':review_targets(bound),
                'verdicts':[{'claim_id':'O1','status':'supported','scope_alignment':'aligned',
                             'quotes':[{'record_id':rid,'text':'the upgrade\nwas delayed.'}]}],
                'revision_required':[{'claim_id':'S1'}]}}
            service.reviewer=SimpleNamespace(calls=6,max_requests=8)
            final=SimpleNamespace(calls=2,max_requests=2)
            service.minimal_reviewer_factory=Mock(return_value=final)
            # The fake final audit is explicit: this test verifies which exact
            # draft is handed to it, not semantic certification of fixture text.
            with patch.object(service,'publish',return_value={'delivery_kind':'minimal'}) as publish:
                result=service.finalize_minimal_delivery(reason='synthetic_author_interruption')
            self.assertEqual(result['delivery_kind'],'minimal')
            service.minimal_reviewer_factory.assert_called_once()
            submitted=publish.call_args.args[0]
            self.assertEqual(submitted.observations,[bound.observations[0]])
            self.assertEqual(submitted.delivery_kind,'minimal')
            self.assertEqual(service.delivery_metadata['retained_target_ids'],['O1'])

    def test_draft_round_exhaustion_enters_final_delivery_even_with_unused_request_budget(self):
        with TemporaryDirectory() as tmp:
            service=self.service(Path(tmp))
            draft=ReportDraft(title='Synthetic round limit',summary='Unresolved synthetic summary.',
                observations=[{'statement':'A synthetic observation.','evidence_ids':['fixture']}],
                limitations=['Synthetic fixture only.'])
            service.lookup=lambda _:{}
            review={'review_id':'review-synthetic-round-limit','status':'needs_revision',
                    'targets':review_targets(draft),'verdicts':[],
                    'revision_required':[{'claim_id':'S1','status':'insufficient','reason':'Synthetic unresolved summary.'}]}
            service.reviewer=SimpleNamespace(calls=4,max_requests=32,rounds=4,max_rounds=4,
                                            review=lambda *_:review)
            # This simulates Jev's four small single-batch rounds. Plenty of
            # physical slots remain, but a fifth author draft cannot be audited.
            with patch.object(service,'finalize_minimal_delivery',return_value={'synthetic_final_started':True}) as finalize:
                result=service._publish_checked(draft,[])
            self.assertTrue(result['synthetic_final_started'])
            finalize.assert_called_once()
            self.assertEqual(finalize.call_args.kwargs['draft'],draft)
            self.assertEqual(service.last_report_review['review'],review)


if __name__=='__main__':unittest.main()
