from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from research_agent.working_memory import WorkingMemory

A='page-'+'a'*24
B='page-'+'b'*24

class WorkingMemoryV2Tests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=WorkingMemory(self.root,redact=lambda text:str(text).replace('fixture-secret','[REDACTED]'))
        self.store.begin('job-a','ETH source report','2026-01-05T00:00:00Z')

    def test_observation_survives_restart_and_same_call_is_idempotent(self):
        payload={'record':{'record_id':A,'published_at':'2026-01-01T00:00:00Z','text':'Fixture'}}
        first=self.store.observe_tool('job-a','read_evidence',payload,tool_call_id='call-1')
        reopened=WorkingMemory(self.root)
        again=reopened.observe_tool('job-a','read_evidence',payload,tool_call_id='call-1')
        self.assertTrue(again['duplicate'])
        self.assertEqual(first['research_state_revision'],again['research_state_revision'])
        self.assertEqual(len(reopened.state('job-a')['research_state']['observations']),1)
        with self.assertRaisesRegex(ValueError,'rebound'):
            reopened.observe_tool('job-a','read_evidence',{'record':{'record_id':B}},tool_call_id='call-1')

    def test_parallel_same_call_retains_one_observation(self):
        payload={'record':{'record_id':A}}
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda _:self.store.observe_tool('job-a','read_evidence',payload,tool_call_id='same-call'),range(5)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        self.assertEqual(len(self.store.state('job-a')['research_state']['sources']),1)

    def test_failure_and_conflict_get_priority_over_a_long_plan(self):
        self.store.update('job-a',plan=['unimportant '*1000],todos=['later '*500])
        self.store.observe_tool('job-a','request_public_api',{'error':'RPC historic access denied'},status='error')
        self.store.observe_tool('job-a','recall_memory',{'conflicts':[{'evidence_ids':[A,B],'relation':'contradicts'}]})
        view=self.store.context('job-a',budget_chars=3000)
        self.assertIn('RPC historic access denied',view['text'])
        self.assertIn(A,view['text']);self.assertIn(B,view['text'])
        self.assertTrue(view['truncated']);self.assertIn('plan',view['field_truncations'])
        full=json.loads(Path(view['state_file']).read_text())
        self.assertEqual(full['research_state']['conflicts'][0]['evidence_ids'],[A,B])
        self.assertLessEqual(view['characters'],3000)

    def test_minimal_view_points_to_complete_revision_without_split_ids(self):
        self.store.observe_tool('job-a','recall_memory',{'conflicts':[{'evidence_ids':[A,B],'relation':'unresolved_conflict'}]})
        view=self.store.context('job-a',budget_chars=256)
        self.assertLessEqual(view['characters'],256)
        self.assertIn('states/job-a/revision-',view['text'])
        self.assertTrue(view['truncated'])
        full=json.loads(Path(view['state_file']).read_text())
        self.assertEqual(full['research_state']['conflicts'][0]['evidence_ids'],[A,B])
        self.assertEqual(view['text'].count(A),view['text'].count(B))

    def test_explicit_model_note_never_certifies_its_statement(self):
        self.store.evidence_lookup=lambda rid:{'record_id':rid}
        note=self.store.add_note('job-a','Source may indicate postponement.',[A])
        self.assertEqual(note['status'],'unverified_derived_note')
        self.assertTrue(note['references_checked'])
        self.assertTrue(self.store.add_note('job-a','Source may indicate postponement.',[A])['duplicate'])
        with self.assertRaises(ValueError):self.store.add_note('job-a','Unsupported claim.')
        with self.assertRaises(ValueError):self.store.add_note('job-a','Unsupported kind.',[A],kind='verified_fact')
        self.assertEqual(self.store.state('job-a')['facts'],[])

    def test_progressive_view_archives_redacted_exact_output_with_clocks(self):
        payload={'record':{'record_id':A,'published_at':'2026-01-01T00:00:00Z',
            'retrieved_at':'2026-01-04T00:00:00Z','values':{'price':'0.123456789'},'units':{'price':'fraction'},
            'text':'Long fixture source. '*2000+'fixture-secret'}}
        view=json.loads(self.store.progressive_tool_view('job-a','read_evidence',payload,max_chars=1800))
        self.assertTrue(view['read_before_citing']);self.assertTrue(view['preview_is_not_full_source'])
        self.assertEqual(view['metadata']['published_at'],'2026-01-01T00:00:00Z')
        self.assertEqual(view['metadata']['values']['price'],'0.123456789')
        self.assertEqual(view['metadata']['units']['price'],'fraction')
        restored=self.store.read_artifact('job-a',view['artifact']['artifact_id'])['text']
        self.assertEqual(json.loads(restored)['record']['text'],payload['record']['text'].replace('fixture-secret','[REDACTED]'))
        self.assertNotIn('fixture-secret',restored)

    def test_review_rejection_and_failed_outputs_are_not_compacted_away(self):
        payload={'status':'needs_revision','error':'Important condition. '*500}
        text=self.store.progressive_tool_view('job-a','publish_report',payload,max_chars=800)
        self.assertEqual(json.loads(text),payload)
        failed=self.store.progressive_tool_view('job-a','execute',{'error':'x'*6000},status='error',max_chars=800)
        self.assertEqual(json.loads(failed)['error'],'x'*6000)

    def test_untrusted_console_results_do_not_populate_review_state(self):
        self.store.observe_tool('job-a','execute',{'status':'saved','review':{'status':'reviewed'}})
        research=self.store.state('job-a')['research_state']
        self.assertEqual(research['checks'],[]);self.assertEqual(research['publications'],[])

    def test_revision_snapshot_is_immutable_and_hash_checked(self):
        one=self.store.state_snapshot('job-a')
        self.store.update('job-a',unknowns=['Need original source'])
        two=self.store.state_snapshot('job-a')
        self.assertNotEqual(one['path'],two['path'])
        self.assertTrue(Path(one['path']).exists())
        Path(two['path']).write_text('Changed')
        with self.assertRaises(ValueError):self.store.state_snapshot('job-a')

    def test_invalid_observation_status_is_not_persisted_as_state(self):
        with self.assertRaises(ValueError):self.store.observe_tool('job-a','read_evidence',{},status='verified')
        self.assertEqual(self.store.state('job-a')['revision'],1)
        # A redacted raw artifact can still be retained for diagnostics.
        self.assertEqual(len(self.store.artifacts('job-a')),1)

if __name__=='__main__':unittest.main()
