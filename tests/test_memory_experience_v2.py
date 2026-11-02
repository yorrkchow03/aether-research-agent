"""Append-only experience lifecycle and bounded paired-check feedback."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
import sqlite3,tempfile,unittest
from unittest.mock import patch
from research_agent.experience_memory import ExperienceMemory

class ExperienceV2Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.receipt=self.root/'review.json';self.receipt.write_text('{"synthetic_review":"date and unit checks"}')
        self.store=ExperienceMemory(self.root/'experience')
        with patch('research_agent.experience_memory.utcnow',return_value='2026-01-01T00:00:00Z'):
            self.old=self.save('date checking')

    def save(self,topic):
        return self.store.record_correction(topic,'A date/unit assumption was wrong.','Check exact dates, units and conditions against originals.',receipts=[self.receipt],applicability=['date checking','numeric report preparation'])

    def lifecycle(self,status,when='2026-01-03T00:00:00Z',**kwargs):
        with patch('research_agent.experience_memory.utcnow',return_value=when):
            return self.store.record_lifecycle(self.old['correction_id'],status=status,reason='Synthetic reviewed revision.',receipts=[self.receipt],**kwargs)

    def outcome(self,with_pass=True,without_pass=False,task='task1',check='check1',when='2026-01-03T00:00:00Z',**overrides):
        obj={'schema':'aether-workflow-memory-feedback-v1','verification':'programmatic_paired_check',
            'correction_id':self.old['correction_id'],'task_id':task,'check_id':check,'check_type':'numeric_units',
            'with_memory_passed':with_pass,'without_memory_passed':without_pass,**overrides}
        path=self.root/(task+'-'+check+'.json');path.write_text(json.dumps(obj))
        with patch('research_agent.experience_memory.utcnow',return_value=when):
            return self.store.record_outcome(self.old['correction_id'],path)

    def recall(self,when='2026-01-06T00:00:00Z',**kwargs):
        return self.store.recall('date checking',cutoff=when,**kwargs)

    def test_retired_experience_is_not_recalled_and_original_stays_immutable(self):
        digest=hashlib.sha256(json.dumps(self.store._entry(self.old['correction_id']),sort_keys=True).encode()).hexdigest()
        self.lifecycle('retired');after=self.recall();self.assertEqual(after['results'],[])
        self.assertEqual(after['excluded'][0]['reason'],'experience_retired')
        self.assertEqual(hashlib.sha256(json.dumps(self.store._entry(self.old['correction_id']),sort_keys=True).encode()).hexdigest(),digest)
        with self.store.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM corrections')
            with self.assertRaises(sqlite3.IntegrityError):c.execute('UPDATE experience_events SET known_at=?',('2026-01-01',))

    def test_later_status_changes_do_not_change_historical_recall(self):
        before=self.recall('2026-01-02T00:00:00Z');self.lifecycle('invalidated')
        self.assertEqual(self.recall('2026-01-02T00:00:00Z'),before)
        self.assertEqual(self.recall()['results'],[])

    def test_reactivation_is_an_append_event_not_an_overwrite(self):
        self.lifecycle('retired');self.lifecycle('active',when='2026-01-04T00:00:00Z')
        self.assertEqual(self.recall()['results'][0]['lifecycle_status'],'active')
        self.assertEqual(self.recall('2026-01-03T12:00:00Z')['results'],[])

    def test_repeated_retirement_after_reactivation_is_a_new_event(self):
        first=self.lifecycle('retired');self.lifecycle('active',when='2026-01-04T00:00:00Z')
        last=self.lifecycle('retired',when='2026-01-05T00:00:00Z')
        self.assertNotEqual(first['event_id'],last['event_id'])
        self.assertEqual(self.recall()['results'],[])

    def test_revision_has_distinct_id_and_supersession_pointer(self):
        with patch('research_agent.experience_memory.utcnow',return_value='2026-01-02T00:00:00Z'):
            new=self.save('date checking revised')
        self.lifecycle('superseded',replacement_id=new['correction_id'])
        row=self.recall();self.assertEqual([r['correction_id'] for r in row['results']],[new['correction_id']])
        self.assertEqual(row['excluded'][0]['replacement_id'],new['correction_id'])
        with self.assertRaises(ValueError):
            self.store.record_lifecycle(new['correction_id'],status='superseded',reason='Cycle',receipts=[self.receipt],replacement_id=self.old['correction_id'])

    def test_lifecycle_requires_real_receipt_reason_and_distinct_known_replacement(self):
        for kwargs in [{'status':'wrong'},{'status':'retired','reason':''},{'status':'superseded','replacement_id':'missing'},
            {'status':'superseded','replacement_id':self.old['correction_id']},{'status':'active','replacement_id':'unexpected'}]:
            params={'status':'retired','reason':'Reviewed','receipts':[self.receipt],**kwargs}
            with self.assertRaises(ValueError):self.store.record_lifecycle(self.old['correction_id'],**params)
        with self.assertRaises(ValueError):self.store.record_lifecycle(self.old['correction_id'],status='retired',reason='Reviewed',receipts=[self.root/'missing.json'])

    def test_only_paired_programmatic_workflow_check_can_update_usefulness(self):
        before=self.recall()['results'][0]
        self.outcome();after=self.recall()['results'][0]
        self.assertEqual(after['workflow_utility']['helpful'],1)
        self.assertGreater(after['selection_score'],before['selection_score'])
        self.assertLessEqual(after['selection_score'],after['relevance_score']*1.15)
        for change in [{'verification':'llm_judge'},{'check_type':'profit'},{'with_memory_passed':'yes'},
            {'correction_id':'different'},{'task_id':None},{'check_id':''}]:
            with self.assertRaises(ValueError):self.outcome(task='invalid',**change)

    def test_future_feedback_does_not_affect_historical_score_or_corpus(self):
        before=self.recall('2026-01-02T00:00:00Z');self.outcome()
        self.assertEqual(self.recall('2026-01-02T00:00:00Z'),before)

    def test_duplicate_paired_task_check_is_not_multiple_successes(self):
        self.outcome();self.outcome();self.outcome(with_pass=False,without_pass=True)
        utility=self.recall()['results'][0]['workflow_utility']
        self.assertEqual(utility['paired_checks'],1);self.assertEqual(utility['harmful'],1)
        self.assertLess(utility['value'],0)

    def test_feedback_can_return_to_earlier_outcome_without_resurrecting_old_time(self):
        first=self.outcome();self.outcome(with_pass=False,without_pass=True,when='2026-01-04T00:00:00Z')
        last=self.outcome(when='2026-01-05T00:00:00Z')
        self.assertNotEqual(first['event_id'],last['event_id'])
        self.assertEqual(self.recall()['results'][0]['workflow_utility']['helpful'],1)

    def test_neutral_success_in_both_conditions_is_not_a_positive_reward(self):
        self.outcome(with_pass=True,without_pass=True)
        row=self.recall()['results'][0]
        self.assertEqual(row['workflow_utility']['neutral'],1)
        self.assertEqual(row['selection_score'],row['relevance_score'])

    def test_source_corrections_do_not_receive_workflow_utility_rewards(self):
        source=ExperienceMemory(self.root/'source',evidence_lookup=lambda rid:{'record_id':rid})
        entry=source.record_correction('source date','Old date','Read new date',receipts=[self.receipt],kind='source_correction',evidence_ids=['page-example'])
        with self.assertRaises(ValueError):source.record_outcome(entry['correction_id'],self.receipt)

    def test_tampered_lifecycle_receipt_quarantines_instead_of_reviving_memory(self):
        event=self.lifecycle('retired');Path(event['receipts'][0]['path']).write_text('Tampered')
        # The same original receipt is used by both entry and event, so either
        # integrity check must exclude it instead of assuming an active status.
        self.assertEqual(self.recall()['results'],[])
        self.assertTrue(self.recall()['excluded'])

    def test_bounded_context_carries_conditions_and_actual_receipt_pointers(self):
        row=self.recall(budget_chars=800)
        self.assertTrue(row['results']);self.assertIn('Apply when:',row['prompt_context'])
        self.assertIn('Receipts:',row['prompt_context']);self.assertLessEqual(row['characters'],800)
        with self.assertRaises(ValueError):self.recall(utility_weight=.5)

if __name__=='__main__':unittest.main()
