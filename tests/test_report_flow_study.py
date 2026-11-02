"""Whole-report accounting must retain unknowns and every physical provider call.

These tests only write synthetic attempt files. No settings or API client loads.
"""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from research_agent.report_flow_study import summarize_attempt


class ReportFlowAccountingTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.output=Path(temp.name)/'attempt';self.output.mkdir()
        self.session=self.output/'runtime/sessions/session-synthetic'
        self.job=self.session/'jobs/job-synthetic';self.job.mkdir(parents=True)
        self.write(self.output/'ATTEMPT.json',{'case_id':'case-synthetic','arm':'jev_routed','session_id':'session-synthetic'})
        self.write(self.job/'status.json',{'state':'completed','published_reports':['/reports/synthetic.md'],
            'created_at':'2026-10-29T00:00:00Z','finished_at':'2026-10-29T00:01:00Z'})
        self.events=[];self.http=[]

    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value)+'\n')

    def summarize(self,runtime=None):
        (self.job/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.events))
        self.write(self.output/'runtime/http-requests.json',{'requests':self.http})
        return summarize_attempt(self.output,runtime)

    @staticmethod
    def judgment(provider,physical=1,usage=None,**extra):
        return {'provider':provider,'task':'support','status':'ok','model':'jev-1.14.0' if provider=='jev' else 'doubao-seed-2.1-pro',
            'physical_request_count':physical,'billable_request_made':True,'cache_hit':False,
            'usage':usage,'evidence_ids':['page-synthetic'],**extra}

    def test_nested_route_keeps_known_fallback_request_when_primary_count_unknown(self):
        primary=self.judgment('jev',None,{'input_tokens':100,'output_tokens':0})
        fallback=self.judgment('doubao',1,{'input_tokens':200,'output_tokens':20})
        self.events=[{'kind':'evidence_judgment','provider':'routed_jev_doubao','physical_request_count':None,
            'cache_hit':False,'routing':{'trace':[{'role':'primary','result':primary},{'role':'fallback','result':fallback}]}}]
        result=self.summarize()
        self.assertEqual(result['judgment_physical_requests_known'],1)
        self.assertEqual(result['judgment_unknown_physical_counts'],1)
        self.assertEqual(result['pro_physical_requests_known'],1)
        self.assertEqual(result['jev_physical_requests_known'],0)
        self.assertEqual(result['known_pro_input_tokens'],200)
        self.assertEqual(result['known_jev_input_tokens'],100)
        self.assertEqual(len(result['judgment_billing']),2)

    def test_two_nested_unknown_counts_are_two_unknown_provider_outcomes(self):
        self.events=[{'kind':'evidence_judgment','physical_request_count':None,'cache_hit':False,
            'routing':{'trace':[{'result':self.judgment('jev',None)}, {'result':self.judgment('doubao',None)}]}}]
        result=self.summarize()
        self.assertEqual(result['judgment_physical_requests_known'],0)
        self.assertEqual(result['judgment_unknown_physical_counts'],2)
        self.assertGreaterEqual(result['jev_unknown_usage_requests'],1)
        self.assertGreaterEqual(result['pro_unknown_usage_requests'],1)

    def test_missing_submission_metadata_and_usage_cannot_be_quoted_as_free(self):
        event=self.judgment('doubao',None,None);event.pop('billable_request_made')
        self.events=[{'kind':'evidence_judgment',**event}]
        result=self.summarize();quote=result['judgment_billing'][0]
        self.assertIsNone(quote['estimated_afp'])
        self.assertFalse(quote['complete_usage'])
        self.assertIsNone(result['known_estimated_pro_afp'])

    def test_jev_missing_input_usage_is_unknown_not_zero_or_complete_price(self):
        self.events=[{'kind':'evidence_judgment',**self.judgment('jev',1,{'output_tokens':0})}]
        result=self.summarize()
        self.assertIsNone(result['known_jev_input_tokens'])
        self.assertIsNone(result['known_estimated_jev_usd'])
        self.assertEqual(result['jev_unknown_usage_requests'],1)

    def test_present_token_keys_with_null_counts_are_still_unknown_usage(self):
        self.events=[{'kind':'evidence_judgment',**self.judgment('doubao',1,{'input_tokens':None,'output_tokens':2})}]
        result=self.summarize()
        self.assertEqual(result['pro_unknown_usage_requests'],1)
        self.assertIsNone(result['known_pro_input_tokens'])
        self.assertEqual(result['known_pro_output_tokens'],2)
        self.assertIsNone(result['known_estimated_pro_afp'])

    def test_resummary_keeps_persisted_routing_metrics_without_runtime_object(self):
        metrics={'policy_version':'test','logical_calls':2,'providers':{
            'jev':{'known_physical_requests':1,'physical_request_count':None,'unknown_physical_counts':1},
            'doubao':{'physical_request_count':1}}}
        runtime=SimpleNamespace(judge=SimpleNamespace(metrics=lambda:deepcopy(metrics)))
        first=self.summarize(runtime)
        self.assertEqual(first['routing_metrics'],metrics)
        second=self.summarize()
        self.assertEqual(second['routing_metrics'],metrics)
        self.assertEqual(json.loads((self.output/'RESULT.json').read_text())['routing_metrics'],metrics)

    def test_context_summary_shared_http_meter_does_not_duplicate_usage_or_requests(self):
        self.events=[{'kind':'model_finished','usage':{'input_tokens':100,'output_tokens':10}},
            {'kind':'context_summary_started','summary_request_id':'summary-a'},
            {'kind':'context_summary_finished','summary_request_id':'summary-a','usage':{'input_tokens':20,'output_tokens':2}}]
        self.http=[{'stage':'author','request_index':1},{'stage':'author','request_index':2},
            {'stage':'mandatory_source_review','request_index':3}]
        self.write(self.session/'workspace/reviews/attempts/review-a.json',
            {'usage':{'input_tokens':30,'output_tokens':3}})
        result=self.summarize()
        self.assertEqual(result['pro_physical_requests_known'],3)
        self.assertEqual(result['known_pro_input_tokens'],150)
        self.assertEqual(result['known_pro_output_tokens'],15)
        self.assertEqual(result['pro_unknown_usage_requests'],0)
        self.assertEqual(result['author_responses'],1)
        self.assertEqual([r['stage'] for r in result['pro_usage_rows']],['author','context_summary','mandatory_source_review'])
        scope=result.get('http_stage_scope','')
        self.assertTrue(scope)
        self.assertIn('summary',scope.casefold())

    def test_cached_route_history_does_not_create_new_billable_requests(self):
        prior=self.judgment('jev',1,{'input_tokens':100,'output_tokens':0})
        self.events=[{'kind':'evidence_judgment','cache_hit':True,'physical_request_count':0,
            'routing':{'trace':[],'historical_trace':[{'result':prior}]}}]
        result=self.summarize()
        self.assertEqual(result['pro_physical_requests_known'],0)
        self.assertEqual(result['jev_physical_requests_known'],0)
        self.assertEqual(result['judgment_billing'],[])


if __name__=='__main__':unittest.main()
