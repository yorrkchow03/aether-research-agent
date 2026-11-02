"""Offline ledger safeguards; synthetic fixtures are never real quality labels."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.evaluation.evaluate_report_quality import (
    OFFICIAL, SavedCase, candidate_navigation, default_external_status, digest,
    independent_report_groups, model_labels, must_reject, synthetic_checks,
    verify_context_receipt,
)


class ReportQualityEvaluationTests(unittest.TestCase):
    def test_contract_counterexamples_run_without_model_or_network(self):
        with patch('research_agent.review.create_ark_model',side_effect=AssertionError('No models')) as model, \
             patch('socket.create_connection',side_effect=AssertionError('No network')) as network:
            result=synthetic_checks()
        self.assertEqual(len(result),4)
        self.assertTrue(all(case['provenance']=='synthetic' for case in result))
        self.assertTrue(all(case['label_type']=='programmatic_contract' and case['status']=='passed' for case in result))
        self.assertEqual(len({case['case_id'] for case in result}),4)
        model.assert_not_called();network.assert_not_called()

    def test_replay_and_multiple_checks_do_not_inflate_original_report_groups(self):
        artifacts=[{'report_id':'original','original_sha256':'original-bytes'},
                   {'report_id':'offline-replay','original_sha256':'original-bytes'},
                   {'report_id':'rendered-copy','original_sha256':'original-bytes'},
                   {'report_id':'different-saved-report','original_sha256':'different-original-bytes'}]
        self.assertEqual(independent_report_groups(artifacts),['different-original-bytes','original-bytes'])

    def test_independent_model_confirmed_issue_stays_model_label_not_truth(self):
        raw={'reviewer':'Synthetic independent reviewer','checks':[
            {'report':'saved','claim_id':'I1','status':'confirmed_issue','human_ground_truth':True,'label_type':'human'}],
            'summaries':[{'report':'other','status':'supported'}]}
        result=model_labels(raw,'saved')
        self.assertEqual(len(result),1);self.assertEqual(result[0]['status'],'confirmed_issue')
        self.assertEqual(result[0]['label_type'],'independent_model')
        self.assertFalse(result[0]['human_ground_truth'])
        self.assertTrue(raw['checks'][0]['human_ground_truth'])

    def test_candidate_wording_does_not_turn_navigation_into_semantic_repair(self):
        context={'newer_related_candidates':[{'evidence_id':OFFICIAL,
            'title':'SYNTHETIC cancellation confirmed update!','event_equivalence_confirmed':False,
            'needs_original_review':True}]}
        result=candidate_navigation(context)
        self.assertTrue(result['candidate_surfaced'])
        self.assertFalse(result['cancellation_confirmed'])
        self.assertFalse(result['semantic_report_repair_confirmed'])
        self.assertFalse(result['event_equivalence_confirmed'])

    def test_promoted_or_missing_candidate_is_not_accepted_as_navigation_receipt(self):
        for candidates in ([],[{'evidence_id':OFFICIAL,'event_equivalence_confirmed':True,'needs_original_review':True}],
                           [{'evidence_id':OFFICIAL,'event_equivalence_confirmed':False,'needs_original_review':False}]):
            with self.subTest(candidates=candidates),self.assertRaises(ValueError):
                candidate_navigation({'newer_related_candidates':candidates})

    def context_fixture(self,folder):
        observed={'newer_related_candidates':[{'evidence_id':OFFICIAL,
            'event_equivalence_confirmed':False,'needs_original_review':True}]}
        strict={'availability_mode':'strict','source_cards':[]}
        for name,body in [('actual-context.json',observed),('actual-strict-context.json',strict)]:
            (folder/name).write_text(json.dumps(body))
        receipt={'network_requests':0,'model_calls':0,'writes_to_source_stores':0,
            'observed_source_read_attempts':3,
            'artifacts':{name:digest(folder/name) for name in ('actual-context.json','actual-strict-context.json')}}
        (folder/'acceptance.json').write_text(json.dumps(receipt))
        return receipt

    def test_context_hashes_and_strict_receipt_are_checked_without_research_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);self.context_fixture(folder)
            result=verify_context_receipt(folder)
            self.assertTrue(result['strict_saved_replay_excludes_selected_sources'])
            (folder/'actual-context.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'artifact changed'):
                verify_context_receipt(folder)

    def test_strict_receipt_with_later_source_cannot_pass_even_with_fresh_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);receipt=self.context_fixture(folder)
            path=folder/'actual-strict-context.json'
            path.write_text(json.dumps({'availability_mode':'strict','source_cards':[{'evidence_id':'late'}]}))
            receipt['artifacts'][path.name]=digest(path)
            (folder/'acceptance.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError,'strict replay'):
                verify_context_receipt(folder)

    def test_selected_saved_original_and_record_hashes_detect_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle=root/'bundle';bundle.mkdir()
            (bundle/'records').mkdir();record=bundle/'records/source.json'
            record.write_text(json.dumps({'record_id':'source','text':'SYNTHETIC original.'}))
            report=root/'saved.json';report.write_text(json.dumps({'report':{'title':'SYNTHETIC'}}))
            manifest=bundle/'manifest.json'
            manifest.write_text(json.dumps({'files':[{'path':'report/report.json','sha256':digest(report)},
                {'path':'records/source.json','sha256':digest(record)}]}))
            report.with_suffix('.audit.json').write_text(json.dumps({'path':str(bundle),'manifest_hash':digest(manifest)}))
            saved=SavedCase(root,'saved.json')
            self.assertEqual(saved.record('source')['record_id'],'source')
            record.write_text(json.dumps({'record_id':'source','text':'SYNTHETIC changed!'}))
            with self.assertRaisesRegex(ValueError,'evidence changed'):
                SavedCase(root,'saved.json').record('source')
            report.write_text('{}')
            with self.assertRaisesRegex(ValueError,'Report differs'):
                SavedCase(root,'saved.json')

    def test_external_model_acceptance_initially_pending_human_labels_zero(self):
        status=default_external_status()
        self.assertEqual(status['new_real_pro']['status'],'pending')
        self.assertEqual(status['post_upgrade_independent_model']['status'],'pending')
        self.assertEqual(status['human_review'],{'status':'not_performed','label_count':0})

    def test_negative_counterexample_must_actually_reject(self):
        self.assertTrue(must_reject(lambda:(_ for _ in ()).throw(ValueError('SYNTHETIC rejection'))))
        with self.assertRaisesRegex(ValueError,'invalid synthetic case was accepted'):
            must_reject(lambda:True)


if __name__=='__main__':
    unittest.main()
