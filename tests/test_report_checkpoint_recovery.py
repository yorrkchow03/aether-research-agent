"""Synthetic-only recovery checks. No real config, dataset or provider calls."""
from copy import deepcopy
import hashlib,importlib.util,json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_agent.knowledge import KnowledgeStore
from research_agent.live import atomic

from research_agent import report_recovery as recovery
from research_agent.reports import ReportDraft

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.protocol=self.root/'local/confirmation';self.protocol.mkdir(parents=True)
        self.source_id='page-'+'1'*24;self.calc_id='live-'+'a'*24;self.calls=[]
        self.slots=[]
        for i in range(10):
            for arm in ('original_off','pro_checks','jev_routed'):
                cid='report-confirm-'+str(i+1).zfill(2)
                self.slots.append({'slot_id':cid+':'+arm,'case_id':cid,'arm':arm,'source_ids':[self.source_id],
                    'attempt_path':'attempts/'+cid+'/'+arm+'/attempt-01'})
        proto={'slots':self.slots,'primary_slots':30,'software_fingerprint':'f'*64,
            'dataset_manifest_sha256':'d'*64,'dataset_dir':str(self.root/'synthetic-dataset')}
        atomic(self.protocol/'PROTOCOL.json',proto)
        (self.protocol/'PROTOCOL.sha256').write_text(recovery.sha(self.protocol/'PROTOCOL.json')+'\n')
        atomic(self.protocol/'STATE.json',{'state':'finished','protocol_sha256':recovery.sha(self.protocol/'PROTOCOL.json')})
        for index,slot in enumerate(self.slots):
            p=self.protocol/slot['attempt_path'];sid=hashlib.md5(slot['slot_id'].encode()).hexdigest()
            atomic(p/'ATTEMPT.json',{'case_id':slot['case_id'],'arm':slot['arm'],'source_ids':slot['source_ids'],
                'session_id':sid,'software_fingerprint':'f'*64,'dataset_manifest_sha256':'d'*64})
            atomic(p/'RESULT.json',{'case_id':slot['case_id'],'arm':slot['arm'],'state':'failed' if index==0 else 'completed',
                'error':'Publish metric mismatch' if index==0 else None,'published_reports':[]})
            atomic(p/'runtime/http-requests.json',{'requests':[{'stage':'author','http_status':200}]})
        self.slot=self.slots[0];self.primary=self.protocol/self.slot['attempt_path']
        self.primary_receipt=json.loads((self.primary/'ATTEMPT.json').read_text())
        self.old_workspace=self.primary/'runtime/sessions'/self.primary_receipt['session_id']/'workspace'
        self.old_knowledge=KnowledgeStore(self.primary/'runtime/knowledge')
        raw=self.primary/'raw-source.bin';raw.write_bytes(b'real saved source test bytes')
        self.source={'record_id':self.source_id,'dataset_id':'live.webpage','text':'A proposal requests a budget, not executed funding.',
            'observed_at':'2026-10-28T00:00:00Z','retrieved_at':'2026-10-28T00:00:00Z',
            'source':{'source_id':'official','url':'https://example.test/proposal','sha256':recovery.sha(raw)},'raw_path':str(raw)}
        self.old_knowledge.register(self.source)
        recipe=self.primary/'recipe.py';recipe.write_text('print("recompute from argv JSON inputs")\n')
        self.calc={'record_id':self.calc_id,'dataset_id':'live.calculation','observed_at':'2026-10-28T00:00:00Z',
            'retrieved_at':'2026-10-29T00:00:00Z','input_record_ids':[self.source_id],
            'recipe_path':str(recipe),'recipe_sha256':recovery.sha(recipe),'values':{'rate':'1.00001'},'units':{'rate':'bps'},
            'quality_flags':['derived','recipe_reexecuted']}
        self.old_knowledge.register(self.calc)
        atomic(self.old_workspace/'drafts/prior-draft.json',{'draft':{'title':'Prior model draft SYNTHETIC_SECRET',
            'observations':[{'statement':'Check exact precision','evidence_ids':[self.calc_id]}]}})
        atomic(self.old_workspace/'reviews/attempts/pro-review.json',{'status':'needs_revision','response_content':'Read exact numeric field; SYNTHETIC_SECRET'})
        job=self.old_workspace.parent/'jobs/old-job';job.mkdir(parents=True)
        (job/'events.jsonl').write_text(json.dumps({'kind':'tool_failed','name':'publish_report','error':'value does not equal recorded rate'})+'\n')
        self.output=self.protocol/'recovery'/self.slot['case_id']/self.slot['arm']/'attempt-01'
        self.check=patch.object(recovery,'_check_software');self.check.start();self.addCleanup(self.check.stop)

    def fake_prepare(self,root,dataset,output,cid,arm,*,policy):
        self.calls.append(('prepare',cid,arm,policy))
        output=Path(output);sid='e'*32;sessions=output/'runtime/sessions';workspace=sessions/sid/'workspace'
        workspace.mkdir(parents=True);new=KnowledgeStore(output/'runtime/knowledge');new.register(self.source)
        receipt={'case_id':cid,'arm':arm,'session_id':sid,'status':'prepared'};atomic(output/'ATTEMPT.json',receipt)
        class Runtime:
            software_fingerprint='f'*64
            settings=SimpleNamespace(redact=lambda value:value.replace('SYNTHETIC_SECRET','[REDACTED]'))
            knowledge=new;sessions_dir=sessions
            def start(inner,session,prompt,deliverable):
                self.calls.append(('start',prompt));return {'job_id':'new-job'}
            def snapshot(inner,session):return {'active_job_id':None}
        case={'case_id':cid,'question':'Explain the proposal','cutoff':'2026-10-28T23:59:59Z','language':'English',
            'source_ids':[self.source_id],'research_context':{'availability_mode':'observed'}}
        return Runtime(),case,receipt

    def fake_summary(self,output,runtime):
        result={'state':'completed','published_reports':['/reports/recovered.md'],
            'pro_physical_requests_known':2,'jev_physical_requests_known':0,'known_estimated_pro_afp':'.1','known_estimated_jev_usd':None}
        atomic(Path(output)/'RESULT.json',result);return result

    def run_recovery(self):
        with patch.object(recovery,'prepare_attempt',side_effect=self.fake_prepare),patch.object(recovery,'summarize_attempt',side_effect=self.fake_summary):
            return recovery.recover(self.root,self.protocol,self.slot['slot_id'],self.output)

    def test_requires_all_thirty_terminal_and_finished_controller_before_preparing(self):
        slot=self.slots[-1];path=self.protocol/slot['attempt_path']/'RESULT.json'
        row=json.loads(path.read_text());row['state']='running';atomic(path,row)
        with self.assertRaises(ValueError):self.run_recovery()
        self.assertEqual(self.calls,[]);self.assertFalse(self.output.exists())

    def test_recovery_is_new_bounded_derived_memory_with_primary_unchanged(self):
        before=recovery.primary_hashes(self.primary);result=self.run_recovery()
        self.assertEqual(result['state'],'completed');self.assertEqual(before,recovery.primary_hashes(self.primary))
        policy=self.calls[0][3];self.assertEqual(policy.version,'report-recovery-v2');self.assertEqual(policy.author_limit,12)
        self.assertEqual(policy.review_limit,4);self.assertEqual(policy.http_timeout,120);self.assertEqual(policy.http_max_retries,1)
        receipt=json.loads((self.output/'RECOVERY.json').read_text())
        self.assertTrue(receipt['primary_unchanged']);self.assertFalse(receipt['primary_replaced'])
        self.assertEqual(receipt['primary_state'],'failed');self.assertEqual(receipt['host_http_cap'],32)
        self.assertEqual(receipt['tool_sha256'],recovery.sha(self.output/'recovery-tool/recover_report.py'))
        workspace=self.output/'runtime/sessions'/('e'*32)/'workspace'
        copied=''.join(p.read_text() for p in (workspace/'memory/recovery').rglob('*.json'))
        self.assertNotIn('SYNTHETIC_SECRET',copied);self.assertIn('[REDACTED]',copied)
        self.assertFalse((workspace/'checkpoints.sqlite').exists());self.assertFalse((workspace/'data/live').exists())
        prompt=self.calls[1][1];self.assertIn('First save a fresh plan',prompt);self.assertIn('not source evidence',prompt)
        self.assertEqual(receipt['actual_prompt_sha256'],hashlib.sha256(prompt.encode()).hexdigest())
        self.assertEqual(json.loads((self.output/'ATTEMPT.json').read_text())['prompt_sha256'],receipt['actual_prompt_sha256'])

    def test_second_recovery_is_refused_without_new_business_request(self):
        self.run_recovery();before=len(self.calls)
        with self.assertRaises((ValueError,FileExistsError)):self.run_recovery()
        self.assertEqual(before,len(self.calls))

    def test_completed_slot_and_noncanonical_output_are_refused(self):
        other=self.slots[1]
        with self.assertRaises(ValueError):
            recovery.recover(self.root,self.protocol,other['slot_id'],self.protocol/'recovery'/other['case_id']/other['arm']/'attempt-01')
        with self.assertRaises(ValueError):recovery.recover(self.root,self.protocol,self.slot['slot_id'],self.primary)
        self.assertEqual(self.calls,[])

    def test_calculation_transfer_preserves_full_precision_and_hashes_without_old_db_writes(self):
        before=recovery.primary_hashes(self.primary);new=KnowledgeStore(self.root/'new-knowledge');new.register(self.source)
        work=self.root/'new-work';work.mkdir()
        result=recovery.migrate_calculations(self.primary,{self.source_id},{self.calc_id},new,work,lambda text:text)
        self.assertEqual(len(result['imported']),1);self.assertEqual(result['gaps'],[])
        transferred=new.get(self.calc_id);original=self.old_knowledge.get(self.calc_id)
        self.assertEqual(transferred['values'],original['values']);self.assertEqual(transferred['units'],original['units'])
        self.assertEqual(transferred['recipe_sha256'],original['recipe_sha256'])
        self.assertNotEqual(transferred['recipe_path'],original['recipe_path'])
        self.assertEqual(before,recovery.primary_hashes(self.primary))

    def test_unlisted_leaf_and_recipe_mismatch_are_gaps_not_fabricated_transfers(self):
        new=KnowledgeStore(self.root/'new-knowledge');new.register(self.source);work=self.root/'new-work';work.mkdir()
        recipe=Path(self.old_knowledge.get(self.calc_id)['recipe_path']);recipe.write_text('modified')
        result=recovery.migrate_calculations(self.primary,{self.source_id},{self.calc_id},new,work,lambda text:text)
        self.assertEqual(result['imported'],[]);self.assertEqual(len(result['gaps']),1)
        self.assertFalse(new.contains(self.calc_id))
        recipe.write_text('print("recompute from argv JSON inputs")\n')
        result=recovery.migrate_calculations(self.primary,set(),{self.calc_id},new,work,lambda text:text)
        self.assertEqual(result['imported'],[]);self.assertEqual(len(result['gaps']),1)

    def test_old_user_context_is_not_transferred_as_a_fact(self):
        new=KnowledgeStore(self.root/'new-knowledge');new.register(self.source);work=self.root/'new-work';work.mkdir()
        rid='context-'+'b'*24;self.old_knowledge.register({'record_id':rid,'dataset_id':'local.user_research_parameters','profile':{'budget':100}})
        result=recovery.migrate_calculations(self.primary,{self.source_id},{rid},new,work,lambda text:text)
        self.assertEqual(result['imported'],[]);self.assertEqual(len(result['gaps']),1);self.assertFalse(new.contains(rid))

    def test_failed_prepare_consumes_reservation_without_mutating_primary_or_auto_retry(self):
        before=recovery.primary_hashes(self.primary)
        with patch.object(recovery,'prepare_attempt',side_effect=RuntimeError('synthetic private failure')):
            with self.assertRaises(RuntimeError):recovery.recover(self.root,self.protocol,self.slot['slot_id'],self.output)
        self.assertEqual(before,recovery.primary_hashes(self.primary))
        with self.assertRaises(FileExistsError):self.run_recovery()
        self.assertEqual(self.calls,[])

    def feedback_fixture(self,statement='The transfer remains proposed.',*,clear=True,created='2026-10-29T12:00:00Z',name='draft-current',unit=None,limitation='Execution is not confirmed.'):
        if clear:
            for folder in ('drafts','reviews/attempts'):
                for path in (self.old_workspace/folder).glob('*.json'):path.unlink()
        claim={'statement':statement,'evidence_ids':[self.source_id]}
        if unit:claim.update(metric='rate',value='1.00001',unit=unit)
        draft=ReportDraft(title='Frozen proposal',summary=statement,observations=[claim],limitations=[limitation]).model_dump()
        atomic(self.old_workspace/'drafts'/f'{name}.json',{'draft':draft,'created_at':created})
        return draft

    def write_review(self,draft,*,name='review-current',attempt=1,reason='The text states a proposal, not an executed transfer.',correction='Retain the condition: funding moves only if the separate vote passes.',response=None):
        targets=recovery.review_targets(ReportDraft.model_validate(draft))
        verdicts=[{'claim_id':t['claim_id'],'status':'insufficient' if t['claim_id']=='O1' else 'supported',
            'reason':reason if t['claim_id']=='O1' else 'Positive AI approval is not a source.',
            'correction':correction if t['claim_id']=='O1' else '',
            'quotes':[{'source_id':'R1','excerpt_id':'Q1'}]} for t in targets]
        diagnostic={'review_id':'review-synthetic','attempt':attempt,'status':'needs_revision','targets':targets,
            'source_aliases':{'R1':self.source_id},'evidence_text':{'R1':'LARGE_SAVED_SOURCE_'*15000},
            'response_content':json.dumps({'verdicts':verdicts}) if response is None else response}
        path=self.old_workspace/'reviews/attempts'/f'{name}.json';atomic(path,diagnostic)
        return path

    def make_memory(self,budget=12000):
        workspace=self.root/'analysis-workspace';workspace.mkdir(exist_ok=True)
        index,_=recovery.copy_derived(self.primary,self.primary_receipt,workspace,lambda text:text,hot_budget=budget)
        base=workspace/'memory/recovery'
        return index,json.loads((base/'ACTIONABLE_FEEDBACK.json').read_text()),json.loads((base/'cold/ALL_FEEDBACK.json').read_text()),base

    def test_hot_contains_whole_bound_negative_feedback_sources_but_no_evidence_blob(self):
        draft=self.feedback_fixture();self.write_review(draft)
        index,hot,full,base=self.make_memory()
        text=json.dumps(hot);self.assertNotIn('LARGE_SAVED_SOURCE_',text)
        self.assertNotIn('Positive AI approval is not a source.',text)
        issue=next(i for i in hot['issues'] if i['kind']=='internal_pro_negative_verdict')
        self.assertTrue(issue['applies_to_latest_draft']);self.assertEqual(issue['source_ids'],[self.source_id])
        self.assertIn('only if the separate vote passes',issue['correction'])
        self.assertEqual(issue['draft_content_sha256'],index['latest_draft']['draft_content_sha256'])
        self.assertEqual(full['positive_verdicts_not_carried_forward'],1)
        latest=base.parent.parent/ index['latest_draft']['path'].lstrip('/')
        self.assertTrue(latest.is_file())

    def test_different_draft_same_claim_id_or_statement_never_inherits_old_verdict(self):
        old=self.feedback_fixture(unit='bps',name='older',created='2026-10-29T11:00:00Z');self.write_review(old)
        self.feedback_fixture(clear=False,unit='percent',name='newer',created='2026-10-29T12:00:00Z')
        index,hot,full,_=self.make_memory()
        issue=next(i for i in full['issues'] if i['kind']=='internal_pro_negative_verdict')
        self.assertFalse(issue['applies_to_latest_draft'])
        self.assertNotEqual(issue['draft_content_sha256'],index['latest_draft']['draft_content_sha256'])
        self.assertEqual(index['latest_draft']['created_at'],'2026-10-29T12:00:00Z')
        self.assertEqual(index['latest_draft']['selection_basis'],'created_at')

    def test_ambiguous_review_target_match_stays_unknown_with_readable_cold_pointer(self):
        draft=self.feedback_fixture(name='one');self.write_review(draft)
        self.feedback_fixture(clear=False,name='two',created='2026-10-29T13:00:00Z',limitation='Another explicit limitation.')
        _,hot,full,base=self.make_memory()
        issue=next(i for i in full['issues'] if i['kind']=='internal_pro_negative_verdict')
        self.assertIsNone(issue['draft_content_sha256']);self.assertIsNone(issue['applies_to_latest_draft'])
        self.assertFalse(hot['complete_feedback_inline']);self.assertGreater(hot['unresolved_diagnostics_count'],0)
        self.assertTrue((base/'cold/ALL_FEEDBACK.json').is_file())

    def test_soft_budget_uses_cold_reference_instead_of_clipping_conditions(self):
        draft=self.feedback_fixture();correction='ONLY IF votes pass; retain both contrary views. '*200
        self.write_review(draft,correction=correction)
        _,hot,full,_=self.make_memory(budget=2500)
        issue=next(i for i in full['issues'] if i['kind']=='internal_pro_negative_verdict')
        self.assertEqual(issue['correction'],correction);self.assertEqual(issue['source_ids'],[self.source_id])
        self.assertGreater(hot['omitted_issue_count'],0);self.assertFalse(hot['complete_feedback_inline'])
        self.assertNotIn('ONLY IF',json.dumps(hot))
        self.assertEqual(hot['cold_feedback']['path'],'/memory/recovery/cold/ALL_FEEDBACK.json')

    def test_semantic_duplicate_errors_are_deduplicated_and_cold_text_is_exact(self):
        draft=self.feedback_fixture();first=self.write_review(draft,name='one');self.write_review(draft,name='two',attempt=2)
        (first.parent/'exact-copy.json').write_bytes(first.read_bytes())
        index,hot,full,base=self.make_memory()
        negatives=[i for i in full['issues'] if i['kind']=='internal_pro_negative_verdict']
        self.assertEqual(len(negatives),1);self.assertEqual(len(negatives[0]['occurrences']),2)
        self.assertEqual(full['deduplicated_occurrences'],1)
        manifest=json.loads((base/'cold/MANIFEST.json').read_text())
        matching=[f for f in manifest['files'] if f['primary_file_sha256']==recovery.sha(first)]
        self.assertEqual(len(matching),1);self.assertEqual(len(matching[0]['primary_occurrences']),2)
        cold=self.root/'analysis-workspace'/matching[0]['path'].lstrip('/')
        self.assertEqual(cold.read_text(),first.read_text())

    def test_unparsed_response_and_critical_tool_error_remain_available_without_guessing(self):
        draft=self.feedback_fixture();self.write_review(draft,response='NOT VALID JSON')
        job=next((self.old_workspace.parent/'jobs').glob('*'))
        error='Metric '+self.calc_id+' differs; retain the counterexample and do not round it.'
        (job/'events.jsonl').write_text(json.dumps({'kind':'tool_failed','name':'publish_report','error':error})+'\n')
        _,hot,full,base=self.make_memory()
        self.assertFalse(hot['complete_feedback_inline']);self.assertEqual(len(full['unresolved_diagnostics']),1)
        self.assertFalse(any(i['kind']=='internal_pro_negative_verdict' for i in full['issues']))
        tool=next(i for i in hot['issues'] if i['kind']=='tool_error')
        self.assertEqual(tool['feedback']['error'],error);self.assertEqual(tool['source_ids'],[self.calc_id])
        pointer=full['unresolved_diagnostics'][0]['path']
        cold=self.root/'analysis-workspace'/pointer.lstrip('/')
        self.assertIn('NOT VALID JSON',cold.read_text())

if __name__=='__main__':unittest.main()
