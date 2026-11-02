"""Publication/retrieval integration, using isolated synthetic receipts only."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from fixtures import project_fixture
from research_agent.runtime import AgentRuntime
from research_agent.tools import ResearchTools
from research_agent.reports import ReportDraft
from research_agent.review import review_targets, check_review
from research_agent.audit import report_evidence_ids
from research_agent.evidence_memory import EvidenceMemory
from fastapi.testclient import TestClient
from research_agent.server import create_app

class ReportQualityIntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.runtime=AgentRuntime(project_fixture(Path(tmp.name)/'project'),model_mode='current_conversation_no_api')
        session=self.runtime.create_session()
        self.service=ResearchTools(Path(session['data_run']),self.runtime._session_dir(session['session_id'])/'workspace',session['cutoff'],knowledge=self.runtime.knowledge)

    def test_key_changes_enter_gate_audit_and_all_review_targets(self):
        calc=self.service.analyze('ETH',7)
        base={'statement':'Synthetic saved result','evidence_ids':[calc['record_id']]}
        draft=ReportDraft(title='Synthetic brief',summary='Saved result',observations=[base],key_changes=[{**base,'metric':'return_fraction','value':calc['values']['return_fraction'],'unit':'fraction'}],questions_to_watch=['Check later official announcements.'],limitations=['Synthetic'])
        targets=review_targets(draft)
        self.assertEqual({t['claim_id'] for t in targets},{'O1','K1','S1','M1'})
        self.assertEqual(next(t for t in targets if t['claim_id']=='M1')['kind'],'research_action')
        self.assertEqual(report_evidence_ids(draft.model_dump()),{calc['record_id']})
        result=self.service.publish(draft)
        self.assertEqual(result['validation']['numeric_claims_checked'],1)
        draft.key_changes[0].value='999'
        with self.assertRaises(ValueError):self.service.publish(draft)

    def test_news_recall_adds_source_context_without_new_tools_or_models(self):
        rid='page-'+'a'*24
        self.runtime.knowledge.register({'record_id':rid,'title':'Ethereum fixture','assets':['ETH'],'text':'Ethereum fixture dated original.','retrieved_at':'2026-01-01T00:00:00Z','published_at':'2026-01-01T00:00:00Z','url':'https://example.org/a'})
        self.service.evidence_memory=EvidenceMemory(self.runtime.knowledge)
        result=self.service.news_pack(query='Ethereum',related_queries=['Ethereum'],cutoff='2026-01-02T00:00:00Z',budget=6000)
        self.assertEqual(result['results'][0]['record_id'],rid)
        self.assertEqual(result['event_context']['source_cards'][0]['evidence_id'],rid)
        with self.assertRaises(ValueError):self.service.news_pack(query='Ethereum',relations=[])
        with patch('research_agent.report_event_memory.build_report_event_context',side_effect=OSError('fixture')):
            fallback=self.service.news_pack(query='Ethereum',budget=6000)
        self.assertTrue(fallback['results'])
        self.assertIn('event_context_unavailable',[g['reason'] for g in fallback['gaps']])

    def test_monitor_questions_cannot_smuggle_an_unsupported_fact(self):
        draft=ReportDraft(title='Synthetic',summary='Fixture',observations=[{'statement':'Fixture','evidence_ids':['test']}],questions_to_watch=['Future check'],limitations=['Synthetic'])
        targets=review_targets(draft)
        response={'verdicts':[{'claim_id':t['claim_id'],'status':'conditional' if t['kind']=='research_action' else 'supported','reason':'Fixture','quotes':[] if t['kind']=='research_action' else [{'record_id':'test','text':'Fixture'}]} for t in targets]}
        self.assertEqual(check_review(response,targets,{'test':'Fixture'})['status'],'reviewed')
        response['verdicts'][-1]['status']='supported'
        with self.assertRaises(ValueError):check_review(response,targets,{'test':'Fixture'})

    def test_genuine_failed_qualitative_annotation_is_rejected_before_publication(self):
        from research_agent.claims import Claim
        with self.assertRaisesRegex(ValueError,'qualitative'):
            Claim(statement='The date is not set.',evidence_ids=['test'],metric='new activation date',value='not set')
        with self.assertRaises(ValueError):
            Claim(statement='Synthetic number',evidence_ids=['test'],metric='price',value='3')
        self.assertIsNone(Claim(statement='The date is not set.',evidence_ids=['test']).metric)

    def test_reader_concern_api_requires_token_preserves_report_and_stays_unverified(self):
        draft=ReportDraft(title='Synthetic',summary='Fixture',observations=[{'statement':'Fixture','evidence_ids':['test']}],limitations=['Synthetic'])
        original={'created_at':'2026-01-01T00:00:00Z','report':draft.model_dump()}
        self.runtime.knowledge.retain_report('test-report','synthetic-session',original)
        app=create_app(self.runtime)
        with TestClient(app,base_url='http://127.0.0.1:8765') as client:
            url='/api/reports/test-report/feedback';body={'note':'I cannot understand the date.'}
            self.assertEqual(client.post(url,json=body).status_code,403)
            headers={'x-local-token':app.state.csrf_token}
            saved=client.post(url,json=body,headers=headers).raise_for_status().json()
            self.assertEqual(saved['status'],'unverified')
            self.assertTrue(client.post(url,json=body,headers=headers).json()['duplicate'])
            self.assertEqual(client.post(url,json={**body,'claim_id':'I999'},headers=headers).status_code,400)
            rows=client.get(url).json()['items'];self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['latest_review'],None)
            self.assertEqual(client.post('/api/feedback/'+saved['feedback_id']+'/resolve',json={'replacement_report_id':'missing'},headers=headers).status_code,400)
            self.assertEqual(self.runtime.knowledge.report('test-report'),original)

if __name__=='__main__':unittest.main()
