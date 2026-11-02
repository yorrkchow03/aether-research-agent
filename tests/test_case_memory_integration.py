"""Actual publication/SDK/source boundaries for conditional research cases."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fixtures import project_fixture
from research_agent.audit import report_evidence_ids
from research_agent.case_memory import CaseMemory
from research_agent.research_sdk import ResearchClient
from research_agent.runtime import AgentRuntime
from research_agent.tools import ResearchTools


RID='live-'+'b'*24


def explanation():
    def claim(text):return {'statement':text,'evidence_ids':[RID]}
    return {'name':'Synthetic proposal scope','asset':'ETH','horizon':'Until a confirmed activation update',
        'horizon_days':30,'context':{'event_status':'proposed'},
        'observed_basis':[claim('The synthetic source describes a proposal, not activation.')],
        'hypothesis':claim('An activation conclusion remains premature on this source.'),
        'applicability':[claim('The source is explicitly a proposal.')],
        'invalidation':[claim('Revisit if a later official source confirms activation.')],
        'uncertainties':['A future decision is unknown.']}


class CaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=project_fixture(Path(self.tmp.name)/'project');self.runtime=AgentRuntime(self.root)
        self.meta=self.runtime.create_session(research_mode='current')
        self.workspace=self.runtime._session_dir(self.meta['session_id'])/'workspace'
        class Reviewer:
            def review(self,draft,lookup):return self.review_claims([],lookup,{})
            def review_claims(self,targets,lookup,payload):
                return {'status':'reviewed','review_id':'fixture-review','claims_checked':len(targets),
                    'reviewer_model':'synthetic-review-transport','scope':'Synthetic test transport only'}
        self.service=ResearchTools(Path(self.meta['data_run']),self.workspace,self.meta['cutoff'],
            allow_live=True,knowledge=self.runtime.knowledge,reviewer=Reviewer(),
            run_metadata={'session_id':self.meta['session_id']})
        self.service.case_memory=self.runtime.case_memory
        self.row={'record_id':RID,'asset':'ETH','dataset_id':'synthetic.case_integration',
            'retrieved_at':'2026-01-01T00:00:00Z','published_at':'2026-01-01T00:00:00Z',
            'text':'SYNTHETIC proposal, not activation. Revisit after a later official activation update.',
            'values':{'count':'7'},'units':{'count':'count'}}
        self.service.retain_live(self.row)

    def publish(self,item=None):
        return self.service.publish({'title':'Synthetic case boundary','summary':'Synthetic proposal scope only.',
            'observations':[{'statement':'Synthetic count','evidence_ids':[RID],
                'metric':'count','value':'7','unit':'count'}],
            'limitations':['Synthetic test, not actual market research.'],
            'explanations':[item or explanation()]})

    def test_reviewed_publication_creates_source_linked_case_then_recall_in_new_session(self):
        result=self.publish();self.assertEqual(result['case_memory']['status'],'saved')
        case_id=result['case_memory']['case_ids'][0]
        # A fresh service sees the persistent case, not the original chat text.
        second=self.runtime.create_session(research_mode='current')
        other=ResearchTools(Path(second['data_run']),self.runtime._session_dir(second['session_id'])/'workspace',
            second['cutoff'],allow_live=True,knowledge=self.runtime.knowledge)
        other.case_memory=self.runtime.case_memory
        packet=other.sdk_call('memory-cases',{'query':'proposal',
            'context':{'asset':'ETH','conditions':{'event_status':'proposed'}}})
        self.assertEqual(packet['results'][0]['case_id'],case_id)
        self.assertTrue(packet['results'][0]['match']['matching_conditions'])
        self.assertEqual(packet['results'][0]['case']['origin']['review_status'],'reviewed')
        self.assertEqual(packet['results'][0]['outcomes'],[])

    def test_answer_explanation_is_reviewed_and_retained_with_own_evidence(self):
        result=self.service.submit_answer({'explanations':[explanation()]})
        self.assertEqual(result['case_memory']['status'],'saved')
        saved=json.loads(next((self.workspace/'answers').glob('*.json')).read_text())
        self.assertEqual(saved['citations'][0]['record_id'],RID)
        case=self.runtime.case_memory.get(result['case_memory']['case_ids'][0])
        self.assertEqual(case['case']['origin']['kind'],'reviewed_answer')

    def test_old_report_without_explanations_still_has_no_forced_memory(self):
        result=self.service.publish({'title':'Legacy','summary':'Synthetic.',
            'observations':[{'statement':'Synthetic source','evidence_ids':[RID]}],
            'limitations':['Synthetic fixture']})
        self.assertEqual(result['case_memory']['status'],'not_requested')

    def test_unreviewed_or_rejected_draft_does_not_create_case(self):
        with patch.object(self.service.reviewer,'review',return_value={'status':'needs_revision',
            'review_id':'reject','revision_required':[]}):
            with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.runtime.case_memory.recall()['eligible_count'],0)

    def test_memory_failure_does_not_erase_successful_publication(self):
        with patch.object(self.runtime.case_memory,'record_case',side_effect=ValueError('Synthetic issue')):
            result=self.publish()
        self.assertEqual(result['status'],'saved');self.assertEqual(result['case_memory']['status'],'not_retained')
        self.assertTrue((self.workspace/result['report_path'].lstrip('/')).is_file())

    def test_export_ids_include_explanation_only_sources(self):
        self.assertEqual(report_evidence_ids({'explanations':[explanation()]}),{RID})

    def test_sdk_whole_response_budget_and_actual_id_convenience_path(self):
        result=self.publish();cid=result['case_memory']['case_ids'][0]
        client=ResearchClient('synthetic-no-connection')
        with patch.object(client,'call',side_effect=lambda op,**args:self.service.sdk_call(op,args)):
            self.assertEqual(client.research_cases(case_id=cid)['case_id'],cid)
            with self.assertRaises(ValueError):client.research_cases('conflicting query',case_id=cid)
            for budget in (512,1500,8000):
                packet=client.research_cases('proposal',budget_chars=budget)
                self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),budget)
                self.assertNotIn('prompt_context',packet)

    def test_private_lineage_is_rejected_and_quarantine_blocks_existing_case(self):
        private={**self.row,'record_id':'context-private','profile':{'budget_usd':123},
            'dataset_id':'local.user_research_parameters'}
        rows={RID:self.row,'context-private':private}
        store=CaseMemory(Path(self.tmp.name)/'private-check',evidence_lookup=rows.__getitem__)
        result=self.publish();case=self.runtime.case_memory.get(result['case_memory']['case_ids'][0])['case']
        rows[RID]={**self.row,'input_record_ids':['context-private']}
        with self.assertRaisesRegex(ValueError,'private'):store.record_case(case)
        # Even copied knowledge rows cannot bypass a later library exclusion.
        self.runtime.library=SimpleNamespace(exclusion_reasons=lambda rid:['quarantined'])
        recalled=self.runtime.case_memory.recall('proposal')
        self.assertEqual(recalled['results'],[]);self.assertTrue(recalled['excluded'])

    def test_strict_task_cannot_override_cutoff_or_write_case(self):
        self.publish();self.service.mode='strict';self.service.cutoff='2025-01-01T00:00:00Z'
        self.assertEqual(self.service.sdk_call('memory-cases',{'mode':'retrospective',
            'cutoff':'2099-01-01T00:00:00Z'})['results'],[])
        for op in ('memory-case-write','memory-outcome'):
            with self.assertRaises(ValueError):self.service.sdk_call(op,{})

    def test_case_origin_feedback_is_dated_and_does_not_copy_private_note_text(self):
        result=self.publish();cid=result['case_memory']['case_ids'][0]
        before=self.runtime.case_memory.get(cid)
        origin=before['case']['origin']
        self.runtime.report_feedback.submit(origin['id'],'My personal holdings are private; clarify this source.',
            claim_id='E1',category='unclear')
        later=self.runtime.case_memory.get(cid)
        self.assertEqual(len(later['origin_notices']),1)
        self.assertNotIn('personal holdings',json.dumps(later['origin_notices']))
        self.assertEqual(self.runtime.case_memory.get(cid,cutoff=before['known_at'])['origin_notices'],[])


if __name__=='__main__':unittest.main()
