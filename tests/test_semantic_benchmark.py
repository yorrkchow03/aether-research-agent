from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib,json,unittest
from unittest.mock import patch
from research_agent.semantic_benchmark import score_cases,prepare_run,run,MODELS,implementation_hashes,apply_blind_reference
from research_agent.semantic_benchmark_dataset import saved_excerpt,freeze_dataset

class SemanticScoringTests(unittest.TestCase):
    def case(self):return {'case_id':'c1','task':'classification','expected':{'asset':['ETH'],'topic':['network_upgrade'],'event_status':['planned']}}
    def test_error_and_abstention_retain_full_scorable_denominator(self):
        a=self.case();b=deepcopy(a);b['case_id']='c2'
        rows=[{'case_id':'c1','result':{'status':'abstained','dimensions':{
            'asset':{'choice':'ETH','confidence':.95},'topic':{'choice':'network_upgrade','confidence':.7},
            'event_status':{'choice':'planned','confidence':.99}}}}]
        r=score_cases([a,b],rows)
        self.assertEqual(r['planned_dimensions'],6);self.assertEqual(r['matching_dimensions'],3)
        self.assertEqual(r['ai_label_agreement'],.5);self.assertEqual(r['usable_coverage'],2/6)
        self.assertEqual(r['not_attempted'],1)
    def test_ambiguous_reference_preexcluded_but_draft_and_total_retained(self):
        case=self.case();case['score_excluded_dimensions']=['topic']
        r=score_cases([case],[])
        self.assertEqual(r['planned_dimensions'],3);self.assertEqual(r['scored_dimensions'],2)
        self.assertEqual(r['ambiguous_dimensions'],1);self.assertEqual(r['ai_label_agreement'],0)
    def test_false_supported_and_lost_counter_evidence_are_visible(self):
        cases=[{'case_id':'a','task':'support','expected':{'support':['insufficient']}},
            {'case_id':'b','task':'relevance','expected':{'relevance':['relevant'],'instruction_risk':['absent']}}]
        rows=[{'case_id':'a','result':{'status':'ok','dimensions':{'support':{'choice':'supported','confidence':.99}}}},
            {'case_id':'b','result':{'status':'ok','dimensions':{'relevance':{'choice':'irrelevant','confidence':.99}}}}]
        r=score_cases(cases,rows);self.assertEqual(r['critical_errors']['unsupported_claim_marked_supported'],1)
        self.assertEqual(r['critical_errors']['relevant_or_counter_evidence_marked_irrelevant'],1)
    def test_blind_reference_is_additive_and_consensus_sensitivity_preserved(self):
        data={'cases':[self.case()]};review={'cases':[{'case_id':'c1','task':'classification',
            'labels':{'asset':['ETH'],'topic':['staking'],'event_status':['planned']},'excluded_from_scoring_dimensions':[]}]}
        revised=apply_blind_reference(data,review)
        self.assertEqual(data['cases'][0]['expected']['topic'],['network_upgrade'])
        r=score_cases(revised['cases'],[]);self.assertEqual(r['consensus_reference_dimensions'],2)

class SemanticRunTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        project=Path(__file__).resolve().parents[1]
        for name in implementation_hashes(project):
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture-code')
        original={'record_id':'page-'+'a'*24,'title':'Ethereum notice','text':'The Ethereum upgrade is planned.',
            'published_at':'2026-09-01T00:00:00Z','retrieved_at':'2026-10-01T00:00:00Z'}
        sources={'s':saved_excerpt(original)};originals={original['record_id']:original}
        cases=[];review=[]
        for i in range(5):
            cid='c'+str(i)
            cases.append({'case_id':cid,'task':'support','question':'The upgrade is planned. '+str(i),
                'source_keys':['s'],'split':'development','event_family':'fixture','language':'English',
                'controlled':False,'expected':{'support':['supported']},'label_rationale':'Explicit sentence.'})
            review.append({'case_id':cid,'task':'support','labels':{'support':['supported']},'excluded_from_scoring_dimensions':[]})
        self.dataset=self.root/'dataset';freeze_dataset(self.dataset,sources=sources,originals=originals,cases=cases)
        self.review=self.root/'review.json';self.review.write_text(json.dumps({'cases':review,
            'input_sha256':hashlib.sha256((self.dataset/'reviewer_input.json').read_bytes()).hexdigest()}))
        self.output=self.root/'run';self.protocol=prepare_run(self.root,self.dataset,self.output,review_file=self.review)
    def fake(self,jev_errors=False):
        calls=[]
        class Service:
            def __init__(self,root,path,provider,**kwargs):self.provider=provider
            def assess(self,**kwargs):
                calls.append((self.provider,kwargs['question']))
                error=jev_errors and self.provider=='jev'
                return {'status':'error' if error else 'ok','provider':self.provider,'cache_hit':False,
                    'dimensions':{} if error else {'support':{'choice':'supported','confidence':.95}},
                    'input_hash':hashlib.sha256(kwargs['question'].encode()).hexdigest(),
                    'latency_seconds':.1,'billable_request_made':self.provider!='lexical',
                    'physical_request_count':1,'model':MODELS[self.provider],
                    'usage':None if error else {'input_tokens':100,'output_tokens':10}}
        return Service,calls
    def test_provider_failure_does_not_stop_others_or_hide_unattempted_cases(self):
        service,calls=self.fake(jev_errors=True)
        with patch('research_agent.semantic_benchmark.EvidenceJudgmentService',service):r=run(self.root,self.output)
        self.assertEqual(r['providers']['jev']['attempted'],3)
        self.assertEqual(r['providers']['jev']['planned'],5)
        self.assertEqual(r['providers']['jev']['not_attempted'],2)
        self.assertEqual(r['providers']['doubao']['attempted'],5)
        self.assertIsNone(r['providers']['jev']['cost']['estimated_usd'])
        self.assertEqual(r['providers']['jev']['usage']['unknown_responses'],3)
        with patch('research_agent.semantic_benchmark.EvidenceJudgmentService',service):again=run(self.root,self.output)
        self.assertEqual(again['providers']['jev']['attempted'],3)
    def test_inflight_submission_is_not_automatically_duplicated(self):
        step=self.protocol['order'][0];p=self.output/'started'/step['provider']/(step['case_id']+'.json')
        p.parent.mkdir(parents=True);p.write_text('{}')
        service,calls=self.fake()
        with patch('research_agent.semantic_benchmark.EvidenceJudgmentService',service):r=run(self.root,self.output)
        self.assertEqual(sum(provider==step['provider'] for provider,q in calls),4)
        self.assertIn(step['case_id'],r['unverified_pair_input_case_ids'])
    def test_changed_implementation_or_reference_refuses_resume_before_provider_call(self):
        self.review.write_text('{}');service,calls=self.fake()
        with patch('research_agent.semantic_benchmark.EvidenceJudgmentService',service),self.assertRaises(ValueError):run(self.root,self.output)
        self.assertEqual(calls,[])

if __name__=='__main__':unittest.main()
