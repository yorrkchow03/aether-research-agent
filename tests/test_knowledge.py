from fixtures import project_fixture,fixture_run
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_agent.knowledge import KnowledgeStore,canonical_url
from research_agent.tools import ResearchTools
from research_agent.runtime import AgentRuntime,PROJECT_ROOT
from research_agent.answers import AnswerDraft,validate_answer


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=KnowledgeStore(self.root/'knowledge')

    def page(self,letter='a',date='2026-01-02T00:00:00Z',text='Ethereum upgrade is planned.'):
        raw=text.encode();path=self.root/(letter+'.html');path.write_bytes(raw)
        return {'record_id':'page-'+letter*24,'dataset_id':'live.webpage','title':'Ethereum upgrade',
                'url':'https://example.org/upgrade?utm_source=fixture','text':text,'published_at':'2026-01-01T00:00:00Z',
                'observed_at':date,'retrieved_at':date,'values':{},'units':{},'raw_path':str(path),
                'source':{'sha256':hashlib.sha256(raw).hexdigest(),'url':'https://example.org/upgrade','retrieved_at':date}}

    def test_revision_retrieval_deduplicates_and_never_returns_future_correction(self):
        a=self.page();b=self.page('b','2026-01-15T00:00:00Z','Ethereum upgrade was cancelled.')
        c=self.page('c','2026-01-20T00:00:00Z','Ethereum upgrade was cancelled.')
        for row in (a,b,c):self.store.register(row)
        old=self.store.search_news('upgrade','ETH','2026-01-10T00:00:00Z')
        new=self.store.search_news('upgrade','ETH','2026-01-25T00:00:00Z')
        self.assertEqual(old[0]['record_id'],a['record_id']);self.assertEqual(new[0]['record_id'],b['record_id'])
        self.assertEqual(len(new),1);self.assertEqual(new[0]['last_seen_at'],c['retrieved_at'])
        self.assertEqual(self.store.search_news('upgrade','BTC'),[])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0],2)

    def test_normalized_tampering_and_raw_mutation_are_both_detected(self):
        row=self.page();self.store.register(row);saved=self.store.get(row['record_id'])
        changed=dict(saved,text='Ethereum upgrade already activated.')
        with self.assertRaises(ValueError):self.store.verify_local(changed)
        with self.assertRaises(ValueError):self.store.register(changed)
        Path(saved['raw_path']).write_text('changed')
        with self.assertRaises(ValueError):self.store.get(row['record_id'])

    def test_other_session_can_read_source_after_original_file_is_removed(self):
        row=self.page();self.store.register(row);Path(row['raw_path']).unlink()
        run=fixture_run(self.root/'project')
        tools=ResearchTools(run,self.root/'second-session','2026-10-13T00:00:00Z',allow_live=True,knowledge=self.store)
        self.assertEqual(tools.evidence(row['record_id'])['record']['text'],row['text'])
        forbidden=ResearchTools(run,self.root/'historical','2026-01-01T00:00:00Z',knowledge=self.store)
        with self.assertRaises(ValueError):forbidden.lookup(row['record_id'])

    def test_event_revisions_require_review_and_preserve_cutoff(self):
        row=self.page();self.store.register(row)
        event={'title':'Ethereum upgrade','summary':'Planned upgrade','status':'planned','asset':'ETH','evidence_ids':[row['record_id']]}
        with self.assertRaises(ValueError):self.store.record_event(event,self.store.get)
        event['review_id']='review-'+'a'*24
        with patch('research_agent.knowledge.utcnow',return_value='2026-01-03T00:00:00Z'):first=self.store.record_event(event,self.store.get)
        event.update(event_id=first['event_id'],status='cancelled',summary='Cancelled upgrade')
        with patch('research_agent.knowledge.utcnow',return_value='2026-01-20T00:00:00Z'):self.store.record_event(event,self.store.get)
        self.assertEqual(self.store.events('ETH','2026-01-10T00:00:00Z')[0]['status'],'planned')
        self.assertEqual(self.store.events('ETH','2026-01-25T00:00:00Z')[0]['status'],'cancelled')

    def test_answer_reuses_numeric_and_unit_gate(self):
        rid='live-'+'a'*24;row={'values':{'price_usd':'100'},'units':{'price_usd':'USD'}}
        draft=AnswerDraft(facts=[{'statement':'Price is 100 USD','evidence_ids':[rid],'metric':'price_usd','value':'101','unit':'USD'}])
        with self.assertRaises(ValueError):validate_answer(draft,lambda _:row)
        draft.facts[0].value='100';self.assertEqual(validate_answer(draft,lambda _:row)['numeric_claims_checked'],1)

    def test_restart_marks_abandoned_task_without_erasing_evidence(self):
        runtime=AgentRuntime(root=project_fixture(self.root/'project'),state_dir=self.root/'runtime',model_mode='current_conversation_no_api');meta=runtime.create_session()
        directory=runtime._session_dir(meta['session_id']);job=directory/'jobs'/'fixture';job.mkdir(parents=True)
        (job/'status.json').write_text(json.dumps({'state':'running','job_id':'fixture','created_at':'2026-01-01T00:00:00Z'}))
        evidence=directory/'workspace/notes/saved.txt';evidence.write_text('retained input')
        runtime.recover_interrupted_jobs()
        self.assertEqual(json.loads((job/'status.json').read_text())['state'],'interrupted')
        self.assertEqual(evidence.read_text(),'retained input')
    def test_subsecond_cutoff_does_not_leak_a_future_revision(self):
        old=self.page('a','2026-01-02T00:00:00Z')
        future=self.page('b','2026-01-02T00:00:00.500000Z','Ethereum upgrade cancelled')
        for row in (old,future):self.store.register(row)
        self.assertEqual(self.store.search_news('upgrade',cutoff='2026-01-02T00:00:00.100000Z')[0]['record_id'],old['record_id'])
        self.assertEqual(self.store.search_news('upgrade',cutoff='2026-01-02T00:00:00.600000Z')[0]['record_id'],future['record_id'])


if __name__=='__main__':unittest.main()
