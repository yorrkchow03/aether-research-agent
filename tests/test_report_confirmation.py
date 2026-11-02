"""Frozen confirmation scheduling and recovery; no credentials/provider I/O."""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_agent.live import atomic
from research_agent.report_confirmation import freeze_protocol,run_protocol,ProtocolChanged,UncertainAttempt,_run_lock
from research_agent.report_study_dataset import SCHEMA,PAYLOAD_FIELDS
from research_agent.report_study_runtime import ARMS,RECOVERED_POLICY


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'project';(self.root/'src').mkdir(parents=True)
        (self.root/'src/control.py').write_text('value = 1\n')
        self.dataset=self.root/'local/dataset';self.dataset.mkdir(parents=True)
        cases=[]
        for index in range(12):
            split='development' if index<2 else 'confirmation'
            case={'case_id':('report-dev-' if index<2 else 'report-confirm-')+str(index+1).zfill(2),
                'split':split,'question':'Describe saved source '+str(index),'cutoff':'2026-10-28T23:59:59Z',
                'source_ids':['page-'+format(index*2+number,'024x') for number in (1,2)],
                'language':'English','research_context':{'query_terms':['upgrade'],'availability_mode':'observed'},
                'expected':{'NEVER_PASS_REFERENCE':'FORBIDDEN_LABEL_'+str(index)}}
            cases.append(case)
        files={'cases.json':cases,'model_inputs.json':[{key:c[key] for key in PAYLOAD_FIELDS} for c in cases],
            'sources.json':{},'protocol.json':{'scope':'Offline scheduler fixture'}}
        for name,body in files.items():atomic(self.dataset/name,body)
        manifest={'schema':SCHEMA,'case_count':12,'files':{name:self.sha(self.dataset/name) for name in files}}
        atomic(self.dataset/'MANIFEST.json',manifest)
        (self.dataset/'MANIFEST.sha256').write_text(self.sha(self.dataset/'MANIFEST.json')+'\n')
        self.output=self.root/'local/confirmation';self.protocol=None;self.calls=[]

    @staticmethod
    def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def freeze(self,workers=1):
        self.protocol=freeze_protocol(self.root,self.dataset,self.output,workers)
        return self.protocol

    def make_result(self,output,case_id,arm,state='completed',versions=None,policy=None):
        output=Path(output);output.mkdir(parents=True,exist_ok=True)
        slot=next(s for s in self.protocol['slots'] if s['case_id']==case_id and s['arm']==arm)
        sid=hashlib.md5((case_id+arm).encode()).hexdigest()
        receipt={'case_id':case_id,'arm':arm,'source_ids':slot['source_ids'],'session_id':sid,
            'dataset_manifest_sha256':self.protocol['dataset_manifest_sha256'],
            'software_fingerprint':self.protocol['software_fingerprint'],
            'study_policy':(policy or RECOVERED_POLICY).public()}
        atomic(output/'ATTEMPT.json',receipt)
        workspace=output/'runtime/sessions'/sid/'workspace'
        reports=[]
        for index,valid in enumerate(versions if versions is not None else ([True] if state=='completed' else [])):
            path='/reports/research-'+str(index)+'.md';md=workspace/path.lstrip('/');md.parent.mkdir(parents=True,exist_ok=True)
            md.write_text('A saved, source-backed note '+str(index))
            atomic(md.with_suffix('.json'),{'report':{'title':'Saved note'},'validation':{},
                'review':{'status':'reviewed' if valid else 'needs_revision'}})
            atomic(md.with_suffix('.audit.json'),{'verification':{'status':'passed'},'report_hash':self.sha(md)})
            reports.append(path)
        result={'case_id':case_id,'arm':arm,'state':state,'published_reports':reports,
            'wall_seconds':10,'preparation_seconds':2,'end_to_end_seconds':12,
            'pro_physical_requests_known':2,'jev_physical_requests_known':1 if arm=='jev_routed' else 0,
            'pro_unknown_physical_counts':0,'jev_unknown_physical_counts':0,
            'pro_unknown_usage_requests':0,'jev_unknown_usage_requests':0,
            'known_estimated_pro_afp':'.1','known_estimated_jev_usd':'.00001' if arm=='jev_routed' else None,
            'error':'401 from first author request' if state=='failed' else None}
        atomic(output/'RESULT.json',result)
        return result

    def fake_runner(self,root,dataset_dir,output,case_id,arm,*,policy):
        self.calls.append((case_id,arm))
        self.assertEqual(policy.public(),self.protocol['study_policy'])
        return self.make_result(output,case_id,arm)

    def run_fake(self,side_effect=None):
        with patch('research_agent.report_confirmation.run_attempt',side_effect=side_effect or self.fake_runner),redirect_stdout(io.StringIO()):
            return run_protocol(self.root,self.output)

    def test_freeze_thirty_input_only_slots_and_rotated_launch_order(self):
        protocol=self.freeze(workers=3)
        self.assertEqual(protocol['primary_slots'],30);self.assertEqual(protocol['question_count'],10)
        self.assertEqual(protocol['workers'],3)
        self.assertNotIn('FORBIDDEN_LABEL',json.dumps(protocol));self.assertNotIn('expected',json.dumps(protocol))
        for i in range(10):
            group=protocol['slots'][3*i:3*i+3]
            self.assertEqual([s['arm'] for s in group],list(ARMS[i%3:]+ARMS[:i%3]))
            self.assertEqual(len({s['case_id'] for s in group}),1)
            self.assertTrue(all('report-confirm-' in s['case_id'] for s in group))
        self.assertEqual(protocol['request_bounds']['arm_nominal_physical_http'],{'original_off':56,'pro_checks':68,'jev_routed':84})
        self.assertEqual(self.calls,[])

    def test_freeze_rejects_overwrite_and_worker_or_output_escape(self):
        self.freeze()
        with self.assertRaises(ValueError):freeze_protocol(self.root,self.dataset,self.output)
        for workers in (0,4,True):
            with self.assertRaises(ValueError):freeze_protocol(self.root,self.dataset,self.root/'local/new',workers)
        with self.assertRaises(ValueError):freeze_protocol(self.root,self.dataset,self.root.parent/'outside')

    def test_original_frozen_bytes_and_model_input_corruption_are_rejected(self):
        data=json.loads((self.dataset/'model_inputs.json').read_text());data[0]['question']='Changed'
        atomic(self.dataset/'model_inputs.json',data)
        with self.assertRaises(ProtocolChanged):self.freeze()
        self.assertFalse(self.output.exists())

    def test_all_primary_slots_finish_and_resource_preparation_is_included(self):
        self.freeze(workers=3);result=self.run_fake()
        self.assertEqual(len(self.calls),30);self.assertEqual(len(set(self.calls)),30)
        self.assertEqual(result['attempted_slots'],30);self.assertTrue(result['all_slots_attempted'])
        self.assertEqual(result['finished_slots'],30);self.assertEqual(result['completed_tasks'],30)
        self.assertEqual(result['resource_totals']['preparation_seconds']['known_sum'],'60')
        self.assertEqual(result['resource_totals']['end_to_end_seconds']['known_sum'],'360')
        self.assertEqual(result['resource_totals']['known_estimated_pro_afp']['known_sum'],'3.0')
        self.assertEqual(result['resource_totals']['known_estimated_jev_usd']['unknown_slots'],0)
        self.assertEqual(result['resource_totals']['known_estimated_jev_usd']['known_sum'],'0.00010')
        self.assertIsNone(result['actual_cash_debit']);self.assertEqual(result['human_labels'],0)

    def test_completed_and_failed_terminal_attempts_are_not_retried(self):
        self.freeze()
        first=self.protocol['slots'][0];self.make_result(self.output/first['attempt_path'],first['case_id'],first['arm'],state='failed')
        result=self.run_fake();self.assertEqual(len(self.calls),29)
        self.assertEqual(result['completed_tasks'],29);self.assertEqual(result['attempted_slots'],30)
        self.assertTrue(result['all_slots_attempted'])
        before=len(self.calls);again=self.run_fake()
        self.assertEqual(len(self.calls),before);self.assertEqual(again['completed_tasks'],29)
        self.assertGreaterEqual(again['orchestration_elapsed_seconds'],result['orchestration_elapsed_seconds'])

    def test_uncertain_attempt_is_retained_and_other_twenty_nine_continue(self):
        self.freeze();first=self.protocol['slots'][0];attempt=self.output/first['attempt_path']
        atomic(attempt/'ATTEMPT.json',{'status':'running','case_id':first['case_id']})
        result=self.run_fake()
        self.assertEqual(len(self.calls),29);self.assertEqual(result['attention_required_slots'],1)
        self.assertEqual(result['finished_slots'],29);self.assertTrue(result['all_slots_attempted'])
        state=json.loads((self.output/'STATE.json').read_text());record=state['slots'][first['slot_id']]
        self.assertEqual(record['error_type'],'UncertainAttempt');self.assertTrue(record['provider_execution_unknown'])
        self.assertIn('do not restart',record['reason'])
        self.run_fake();self.assertEqual(len(self.calls),29)

    def test_runner_exception_does_not_block_independent_slots_or_trigger_replacement(self):
        self.freeze();first=self.protocol['slots'][0]
        def failing(*args,**kwargs):
            if (args[3],args[4])==(first['case_id'],first['arm']):
                self.calls.append((args[3],args[4]));raise RuntimeError('SYNTHETIC_PRIVATE_MESSAGE')
            return self.fake_runner(*args,**kwargs)
        result=self.run_fake(failing)
        self.assertEqual(len(self.calls),30);self.assertEqual(result['finished_slots'],29)
        self.assertEqual(result['attempted_slots'],30);self.assertEqual(result['attention_required_slots'],1)
        self.assertNotIn('SYNTHETIC_PRIVATE_MESSAGE',(self.output/'STATE.json').read_text())
        self.run_fake();self.assertEqual(len(self.calls),30)

    def test_existing_invocation_marker_without_result_is_not_restarted(self):
        self.freeze();slot=self.protocol['slots'][0]
        atomic(self.output/'invocations'/(slot['case_id']+'--'+slot['arm']+'.json'),{'state':'business_runner_invoked'})
        result=self.run_fake()
        self.assertEqual(len(self.calls),29);self.assertEqual(result['attention_required_slots'],1)

    def test_changed_software_or_policy_stops_before_provider_calls(self):
        self.freeze();(self.root/'src/control.py').write_text('value = 2\n')
        with self.assertRaises(ProtocolChanged):self.run_fake()
        self.assertEqual(self.calls,[])
        (self.root/'src/control.py').write_text('value = 1\n')
        changed=replace(RECOVERED_POLICY,memory_budget=6500)
        with patch('research_agent.report_confirmation.RECOVERED_POLICY',changed),self.assertRaises(ProtocolChanged):self.run_fake()
        self.assertEqual(self.calls,[])

    def test_software_change_during_a_slot_stops_new_slots(self):
        self.freeze()
        def edit(*args,**kwargs):
            result=self.fake_runner(*args,**kwargs);(self.root/'src/control.py').write_text('value = 3\n');return result
        with self.assertRaises(ProtocolChanged):self.run_fake(edit)
        self.assertEqual(len(self.calls),1)
        state=json.loads((self.output/'STATE.json').read_text())
        self.assertEqual(state['state'],'protocol_changed')
        self.assertFalse(state['summary']['all_slots_attempted'])

    def test_wrong_receipt_policy_cannot_be_reused(self):
        self.freeze();slot=self.protocol['slots'][0]
        self.make_result(self.output/slot['attempt_path'],slot['case_id'],slot['arm'],policy=replace(RECOVERED_POLICY,memory_budget=6500))
        with self.assertRaises(ProtocolChanged):self.run_fake()
        self.assertEqual(self.calls,[])

    def test_result_mutation_after_completion_is_not_silently_reinterpreted(self):
        self.freeze();self.run_fake();self.assertEqual(len(self.calls),30)
        slot=self.protocol['slots'][0];path=self.output/slot['attempt_path']/'RESULT.json'
        row=json.loads(path.read_text());row['state']='failed';atomic(path,row)
        with self.assertRaises(ProtocolChanged):self.run_fake()
        self.assertEqual(len(self.calls),30)

    def test_first_verified_publication_is_primary_and_all_versions_are_retained(self):
        self.freeze();first=self.protocol['slots'][0]
        self.make_result(self.output/first['attempt_path'],first['case_id'],first['arm'],versions=[False,True,True])
        self.run_fake();state=json.loads((self.output/'STATE.json').read_text());slot=state['slots'][first['slot_id']]
        self.assertEqual(slot['primary_publication'],'/reports/research-1.md')
        self.assertEqual([v['valid'] for v in slot['versions']],[False,True,True])

    def test_completed_task_without_a_valid_audit_is_not_a_success(self):
        self.freeze();first=self.protocol['slots'][0]
        self.make_result(self.output/first['attempt_path'],first['case_id'],first['arm'],versions=[False])
        result=self.run_fake();self.assertEqual(result['completed_tasks'],29)
        self.assertEqual(result['published_tasks'],29);self.assertEqual(result['finished_slots'],30)

    def test_result_with_nonterminal_state_refuses_a_duplicate_runner(self):
        self.freeze();first=self.protocol['slots'][0]
        self.make_result(self.output/first['attempt_path'],first['case_id'],first['arm'],state='running')
        result=self.run_fake();self.assertEqual(len(self.calls),29)
        self.assertEqual(result['attention_required_slots'],1)

    def test_protocol_content_tampering_and_duplicate_active_runner_are_rejected(self):
        self.freeze();path=self.output/'PROTOCOL.json';row=json.loads(path.read_text());row['workers']=3;atomic(path,row)
        with self.assertRaises(ProtocolChanged):self.run_fake()
        with _run_lock(self.output),self.assertRaises(UncertainAttempt):self.run_fake()

    def test_unknown_previous_orchestration_duration_remains_unknown(self):
        self.freeze();path=self.output/'STATE.json';state=json.loads(path.read_text())
        state['runs'].append({'started_at':'2026-10-29T00:00:00Z','pid':12345});atomic(path,state)
        result=self.run_fake()
        self.assertIsNone(result['orchestration_elapsed_seconds']);self.assertEqual(result['unknown_orchestration_runs'],1)
        self.assertGreater(result['known_orchestration_elapsed_seconds'],0)

    def test_progress_inventory_cannot_change_the_common_denominator(self):
        self.freeze();path=self.output/'STATE.json';state=json.loads(path.read_text())
        del state['slots'][self.protocol['slots'][0]['slot_id']];atomic(path,state)
        with self.assertRaises(ProtocolChanged):self.run_fake()
        self.assertEqual(self.calls,[])

    def test_submitted_request_without_usage_is_unknown_while_no_request_is_zero(self):
        self.freeze();slot=next(s for s in self.protocol['slots'] if s['arm']=='jev_routed')
        attempt=self.output/slot['attempt_path'];row=self.make_result(attempt,slot['case_id'],slot['arm'])
        row['known_estimated_jev_usd']=None;row['jev_unknown_usage_requests']=1;atomic(attempt/'RESULT.json',row)
        result=self.run_fake()
        self.assertEqual(result['resource_totals']['known_estimated_jev_usd']['unknown_slots'],1)
        self.assertEqual(result['resource_totals']['jev_unknown_usage_requests']['known_sum'],'1')

    def test_private_configuration_is_hashed_without_plaintext_and_drift_stops_calls(self):
        env=self.root/'.env';env.write_text('SYNTHETIC_KEY=DO_NOT_USE_THIS_TEST_VALUE\nMODEL_OPTION=disabled\n')
        protocol=self.freeze()
        self.assertEqual(protocol['private_configuration_sha256'],self.sha(env))
        self.assertNotIn('DO_NOT_USE_THIS_TEST_VALUE',(self.output/'PROTOCOL.json').read_text())
        env.write_text('SYNTHETIC_KEY=DO_NOT_USE_THIS_TEST_VALUE\nMODEL_OPTION=changed\n')
        with self.assertRaises(ProtocolChanged):self.run_fake()
        self.assertEqual(self.calls,[])

    def test_private_configuration_appearance_or_removal_is_drift(self):
        self.freeze();self.assertIsNone(self.protocol['private_configuration_sha256'])
        (self.root/'.env').write_text('SYNTHETIC_KEY=ONLY_A_TEST\n')
        with self.assertRaises(ProtocolChanged):self.run_fake()
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
