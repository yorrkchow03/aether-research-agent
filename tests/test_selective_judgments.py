"""Offline task selection, conservative fallback, cache scope and accounting."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from research_agent.selective_judgments import SelectiveJudgmentService, POLICY_VERSION

SOURCE = {'record_id':'page-'+'a'*24, 'asset':'ETH', 'text':'The upgrade is proposed, not activated.',
    'available_at':'2026-10-28T00:00:00Z', 'source':{'url':'https://example.test/proposal','sha256':'a'*64}}
DEFAULTS = {
    'classification': {'asset':('ETH',.95),'topic':('network_upgrade',.95),'event_status':('planned',.95)},
    'relevance': {'relevance':('relevant',.95),'instruction_risk':('absent',.95)},
    'relation': {'relation':('update',.95)}, 'support': {'support':('supported',.95)}}


class FakeService:
    """Source-bound local cache and captured raw receipts; no provider clients."""
    def __init__(self, provider, responses=None, cache=None, data_dir=None):
        self.provider = provider
        self.requested_model = 'jev-1.14.0' if provider=='jev' else 'doubao-seed-2.1-pro'
        self.minimum_confidence=.8; self.max_attempts=1
        self.responses=list(responses or []);self.invocations=[];self.calls=[]
        self.cache={} if cache is None else cache
        self.data_dir=Path(data_dir) if data_dir else None

    def assess(self, task, **arguments):
        self.invocations.append((task,deepcopy(arguments)))
        if any(r.get('private') for r in arguments['evidence']):raise ValueError('Do not forward private data')
        digest=hashlib.sha256(json.dumps({'task':task,**arguments},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        key=(self.provider,self.requested_model,digest)
        if key in self.cache:
            result=deepcopy(self.cache[key]);result.update(cache_hit=True,
                physical_request_count=0,billable_request_made=False,
                usage={'input_tokens':0,'output_tokens':0},
                billing={'estimated_usd':'0' if self.provider=='jev' else None,
                    'estimated_afp':'0' if self.provider=='doubao' else None,'complete_usage':True})
            return result
        self.calls.append((task,deepcopy(arguments)))
        override=self.responses.pop(0) if self.responses else {}
        if isinstance(override,Exception):raise override
        dimensions={name:{'choice':choice,'confidence':confidence} for name,(choice,confidence) in DEFAULTS[task].items()}
        main=dimensions['topic' if task=='classification' else task]
        result={'provider':self.provider,'model':self.requested_model,'requested_model':self.requested_model,
            'task':task,'criterion_version':arguments['criterion_version'],'input_hash':digest,
            'status':'ok','label':main['choice'],'labels':[name+':'+r['choice'] for name,r in dimensions.items()],
            'confidence':main['confidence'],'dimensions':dimensions,
            'evidence_ids':[r['record_id'] for r in arguments['evidence']],
            'physical_request_count':1,'billable_request_made':True,'cache_hit':False,
            'usage':{'input_tokens':100,'output_tokens':10},
            'billing':{'provider':self.provider,'estimated_usd':'.0000042' if self.provider=='jev' else None,
                'estimated_afp':'.0275' if self.provider=='doubao' else None,'complete_usage':True}}
        if callable(override):result=override(result,arguments)
        else:result.update(deepcopy(override))
        if result.get('status') in ('ok','abstained'):self.cache[key]=deepcopy(result)
        return result


def uncertain(result,arguments):
    dimension='asset' if result['task']=='classification' else 'relevance'
    result['dimensions'][dimension]={'choice':'uncertain','confidence':.4}
    result['status']='abstained';result['label']=None
    return result


class SelectiveJudgmentTests(unittest.TestCase):
    def make(self,jev_responses=None,pro_responses=None,**options):
        jev=FakeService('jev',jev_responses);pro=FakeService('doubao',pro_responses)
        return SelectiveJudgmentService(jev,pro,**options),jev,pro

    def assess(self,service,task='classification',question='Ethereum upgrade status',source=None,criterion='selective-test-v1'):
        return service.assess(task,question=question,evidence=[deepcopy(source or SOURCE)],criterion_version=criterion)

    def test_classification_and_relevance_keep_conservative_jev_policy(self):
        service,jev,pro=self.make()
        for task in ('classification','relevance'):
            result=self.assess(service,task)
            self.assertEqual(result['status'],'ok')
            self.assertEqual(result['selected_provider'],'jev')
            self.assertEqual(result['routing']['selection'],'jev_then_pro_if_uncertain')
        self.assertEqual([c[0] for c in jev.calls],['classification','relevance'])
        self.assertEqual(pro.calls,[])

    def test_relation_and_support_never_spend_a_jev_request(self):
        service,jev,pro=self.make()
        for task in ('relation','support'):
            result=self.assess(service,task)
            self.assertEqual(result['selected_provider'],'doubao')
            self.assertEqual(result['routing']['selection'],'direct_pro')
            self.assertEqual(result['routing']['trace'][0]['role'],'direct_pro')
            self.assertEqual(result['physical_request_count'],1)
        self.assertEqual(jev.calls,[])
        self.assertEqual([c[0] for c in pro.calls],['relation','support'])
        self.assertEqual(service.metrics()['providers']['doubao']['physical_request_count'],2)

    def test_low_confidence_primary_still_falls_back_with_unchanged_input(self):
        service,jev,pro=self.make([uncertain])
        result=self.assess(service)
        self.assertEqual(result['selected_provider'],'doubao')
        self.assertEqual(jev.calls,pro.calls)
        self.assertEqual(result['physical_request_count'],2)
        self.assertTrue(result['routing']['retain_all_source_evidence'])

    def test_injection_warning_cannot_be_erased_by_pro(self):
        def warning(result,args):
            result['dimensions']['instruction_risk']={'choice':'present','confidence':.95}
            return result
        service,_,_=self.make([warning])
        result=self.assess(service,'relevance')
        self.assertEqual(result['status'],'abstained')
        self.assertIn('instruction_risk_present',result['routing']['alerts'])
        self.assertIsNone(result['label'])

    def test_direct_conflict_and_update_preserve_both_sources(self):
        service,jev,_=self.make()
        sources=[deepcopy(SOURCE),dict(deepcopy(SOURCE),record_id='page-'+'b'*24,text='Correction: activation postponed.')]
        original=deepcopy(sources)
        result=service.assess('relation',question='Compare the updates',evidence=sources)
        self.assertEqual(result['evidence_ids'],[r['record_id'] for r in sources])
        self.assertIn('possible_conflict_or_update_keep_both_sources',result['routing']['alerts'])
        self.assertEqual(sources,original);self.assertEqual(jev.calls,[])

    def test_direct_missing_dimension_is_unknown_not_negative(self):
        service,jev,_=self.make(pro_responses=[{'dimensions':{},'label':'supported'}])
        result=self.assess(service,'support')
        self.assertEqual(result['status'],'abstained');self.assertIsNone(result['label'])
        self.assertEqual(result['routing']['accepted_dimensions'],{})
        self.assertTrue(result['routing']['publication_review_required'])
        self.assertEqual(jev.calls,[])

    def test_failed_direct_result_does_not_certify_dimensions(self):
        service,_,_=self.make(pro_responses=[{'status':'error'}])
        result=self.assess(service,'support')
        self.assertEqual(result['status'],'error');self.assertIsNone(result['label'])
        self.assertEqual(result['routing']['accepted_dimensions'],{})
        self.assertTrue(result['routing']['needs_source_review'])

    def test_direct_identity_drift_fails_closed_without_other_provider(self):
        for override in ({'provider':'jev'},{'task':'relevance'},{'criterion_version':'wrong'}, {'input_hash':'invalid'}):
            with self.subTest(override=override):
                service,jev,_=self.make(pro_responses=[override])
                result=self.assess(service,'support')
                self.assertEqual(result['status'],'error');self.assertIsNone(result['label'])
                self.assertEqual(result['routing']['accepted_dimensions'],{});self.assertEqual(jev.calls,[])

    def test_direct_privacy_exception_does_not_route_to_jev(self):
        service,jev,_=self.make()
        result=self.assess(service,'support',source=dict(SOURCE,private=True))
        self.assertEqual(result['status'],'error');self.assertEqual(jev.calls,[])
        self.assertNotIn('Do not forward',json.dumps(result))
        self.assertIsNone(result['physical_request_count'])
        self.assertIsNone(service.metrics()['providers']['doubao']['input_tokens'])

    def test_one_pro_budget_is_shared_between_fallback_and_direct_checks(self):
        service,jev,pro=self.make([uncertain],pro_limit=1)
        first=self.assess(service)
        self.assertEqual(first['selected_provider'],'doubao')
        second=self.assess(service,'support')
        self.assertEqual(second['status'],'unavailable')
        self.assertIn('pro_budget_exhausted',second['routing']['fallback_reasons'])
        self.assertEqual(second['physical_request_count'],0)
        self.assertEqual(len(pro.calls),1);self.assertEqual(len(jev.calls),1)

    def test_direct_checks_can_exhaust_shared_fallback_budget(self):
        service,jev,pro=self.make([uncertain],pro_limit=1)
        self.assess(service,'support')
        result=self.assess(service)
        self.assertEqual(result['status'],'abstained')
        self.assertIn('fallback_budget_exhausted',result['routing']['fallback_reasons'])
        self.assertEqual(len(pro.calls),1);self.assertEqual(len(jev.calls),1)

    def test_direct_task_cache_does_not_double_count_requests_or_cost(self):
        service,jev,pro=self.make()
        first=self.assess(service,'support');before=service.metrics()
        second=self.assess(service,'support');after=service.metrics()
        self.assertTrue(second['cache_hit']);self.assertEqual(second['physical_request_count'],0)
        self.assertEqual(second['routing']['trace'],[])
        self.assertEqual(len(second['routing']['historical_trace']),1)
        self.assertEqual(second['billing']['estimated_afp'],'0')
        self.assertEqual(before['providers'],after['providers'])
        self.assertEqual(len(pro.calls),1);self.assertEqual(jev.calls,[])
        self.assertEqual(first['cache_key'],second['cache_key'])

    def test_direct_usage_unknown_is_not_a_zero_bill(self):
        service,_,_=self.make(pro_responses=[{'usage':None,'physical_request_count':None,
            'billing':{'estimated_afp':None,'complete_usage':False}}])
        result=self.assess(service,'support');stats=service.metrics()['providers']['doubao']
        self.assertIsNone(result['physical_request_count']);self.assertIsNone(result['billing']['estimated_afp'])
        self.assertIsNone(stats['input_tokens']);self.assertEqual(stats['unknown_physical_counts'],1)
        self.assertEqual(stats['usage_unknown_responses'],1)

    def test_multiple_physical_pro_attempts_are_all_charged_and_overbudget_visible(self):
        service,_,pro=self.make(pro_responses=[{'physical_request_count':2}],pro_limit=1)
        result=self.assess(service,'support');stats=service.metrics()['providers']['doubao']
        self.assertEqual(result['physical_request_count'],2);self.assertTrue(stats['observed_over_budget'])
        self.assess(service,'relation');self.assertEqual(len(pro.calls),1)

    def test_only_classification_uses_injected_reusable_services(self):
        class_jev=FakeService('jev');class_pro=FakeService('doubao')
        service,jev,pro=self.make(classification_primary=class_jev,classification_pro=class_pro)
        for task in ('classification','relevance','relation','support'):self.assess(service,task)
        self.assertEqual([c[0] for c in class_jev.invocations],['classification'])
        self.assertEqual(class_pro.invocations,[])
        self.assertEqual([c[0] for c in jev.invocations],['relevance'])
        self.assertEqual([c[0] for c in pro.invocations],['relation','support'])

    def test_reusable_classification_crosses_sessions_but_dynamic_calls_do_not(self):
        class_cache={};class_jev=FakeService('jev',cache=class_cache);class_pro=FakeService('doubao')
        first,_,_=self.make(classification_primary=class_jev,classification_pro=class_pro)
        self.assess(first);self.assess(first,'relevance');self.assess(first,'support')
        second,second_jev,second_pro=self.make(classification_primary=class_jev,classification_pro=class_pro)
        reused=self.assess(second);self.assess(second,'relevance');self.assess(second,'support')
        self.assertTrue(reused['cache_hit']);self.assertEqual(reused['physical_request_count'],0)
        self.assertEqual(len(class_jev.calls),1)
        self.assertEqual([c[0] for c in second_jev.calls],['relevance'])
        self.assertEqual([c[0] for c in second_pro.calls],['support'])

    def test_source_content_question_and_criterion_changes_cannot_reuse_classification(self):
        cached=FakeService('jev');fallback=FakeService('doubao')
        for question,source,criterion in (
            ('Ethereum upgrade status',SOURCE,'selective-test-v1'),
            ('Has the upgrade activated?',SOURCE,'selective-test-v1'),
            ('Ethereum upgrade status',dict(SOURCE,text='Correction: upgrade cancelled.'),'selective-test-v1'),
            ('Ethereum upgrade status',dict(SOURCE,available_at='2026-10-29T00:00:00Z'),'selective-test-v1'),
            ('Ethereum upgrade status',SOURCE,'revised-criterion')):
            service,_,_=self.make(classification_primary=cached,classification_pro=fallback)
            result=self.assess(service,question=question,source=source,criterion=criterion)
            self.assertFalse(result['cache_hit'])
        self.assertEqual(len(cached.calls),5)

    def test_shared_classification_fallback_uses_same_source_bound_cache(self):
        jev=FakeService('jev',[uncertain]);pro=FakeService('doubao')
        one,_,_=self.make(classification_primary=jev,classification_pro=pro)
        first=self.assess(one)
        two,_,_=self.make(classification_primary=jev,classification_pro=pro)
        second=self.assess(two)
        self.assertEqual(first['physical_request_count'],2)
        self.assertTrue(second['cache_hit']);self.assertEqual(second['physical_request_count'],0)
        self.assertEqual(len(jev.calls),1);self.assertEqual(len(pro.calls),1)

    def test_incompatible_shared_model_and_same_dynamic_store_are_rejected(self):
        primary=FakeService('jev',data_dir='same');pro=FakeService('doubao')
        with self.assertRaises(ValueError):SelectiveJudgmentService(primary,pro,classification_primary=FakeService('jev',data_dir='same'))
        old=FakeService('jev');old.requested_model='jev-1.13.0'
        with self.assertRaises(ValueError):SelectiveJudgmentService(FakeService('jev'),pro,classification_primary=old)

    def test_receipts_metrics_and_callback_contain_selection_and_separate_provider_units(self):
        recorded=[];service,_,_=self.make([uncertain],record=recorded.append)
        first=self.assess(service);self.assess(service,'support')
        self.assertEqual(len(recorded),2)
        self.assertEqual(recorded[0]['routing']['policy_version'],POLICY_VERSION)
        self.assertEqual(service.receipt(first['cache_key'])['routing']['selection'],'jev_then_pro_if_uncertain')
        stats=service.metrics();self.assertEqual(stats['logical_calls'],2)
        self.assertEqual(stats['selection_counts']['classification'],1)
        self.assertEqual(stats['selection_counts']['support'],1)
        self.assertEqual(stats['providers']['jev']['known_estimated_usd'],'0.0000042')
        self.assertEqual(stats['providers']['doubao']['known_estimated_afp'],'0.0550')
        self.assertIsNone(stats['providers']['doubao']['actual_account_debit'])

    def test_invalid_task_and_callback_do_not_invoke_services(self):
        service,jev,pro=self.make()
        for task in ('forecast',''):
            with self.assertRaises(ValueError):self.assess(service,task)
        with self.assertRaises(ValueError):self.assess(service,question='a'*8001)
        self.assertEqual(jev.invocations,[]);self.assertEqual(pro.invocations,[])
        with self.assertRaises(ValueError):self.make(record='not callable')


if __name__=='__main__':unittest.main()
