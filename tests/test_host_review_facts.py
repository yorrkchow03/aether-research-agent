"""Host proof cannot be inferred from article text, confidence or a prefix match."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from research_agent.host_review_facts import (
    AVAILABILITY_PREFIX, AVAILABILITY_SUFFIX, is_verified_host_target,
)
from research_agent.minimal_delivery import MINIMAL_LIMIT, MINIMAL_SUMMARY


class HostReviewFactsTests(unittest.TestCase):
    def fixture(self):
        self.a='page-'+'a'*24;self.b='live-'+'b'*24
        self.t1='2026-10-26T01:00:00Z';self.t2='2026-10-27T04:34:44.230651Z'
        texts={self.a:json.dumps({'record_id':self.a,'retrieved_at':self.t1}),
               self.b:json.dumps({'record_id':self.b,'retrieved_at':self.t2,
                                  'source':{'retrieved_at':self.t2}})}
        statement=AVAILABILITY_PREFIX+self.t2+AVAILABILITY_SUFFIX
        body={'observations':[{'evidence_ids':[self.a,self.b]}], 'limitations':[statement]}
        target={'kind':'limitation','statement':statement,'evidence_ids':[self.a,self.b]}
        return target,body,texts

    def test_exact_minimal_process_text_and_sentence_spans_only(self):
        body={'delivery_kind':'minimal','summary':MINIMAL_SUMMARY,'limitations':[MINIMAL_LIMIT],
              'observations':[{'statement':'Synthetic observed fact.','evidence_ids':['fixture']} ]}
        for statement in (MINIMAL_SUMMARY,):
            self.assertTrue(is_verified_host_target({'kind':'summary','statement':statement},body,{}))
        end=MINIMAL_SUMMARY.index(' Unresolved')+1
        for start,stop in ((0,end),(end,len(MINIMAL_SUMMARY))):
            target={'kind':'summary','statement':MINIMAL_SUMMARY[start:stop], 'summary_span':{'start':start,'end':stop}}
            self.assertTrue(is_verified_host_target(target,body,{}))
            target['statement']+=' ETH will rise.'
            self.assertFalse(is_verified_host_target(target,body,{}))
        target={'kind':'limitation','statement':MINIMAL_LIMIT}
        self.assertTrue(is_verified_host_target(target,body,{}))
        for changed in ({**body,'delivery_kind':'full'},{**body,'limitations':[]}):
            self.assertFalse(is_verified_host_target(target,changed,{}))
        self.assertFalse(is_verified_host_target({'kind':'summary','statement':MINIMAL_SUMMARY,
            'summary_span':{'start':False,'end':len(MINIMAL_SUMMARY)}},body,{}))

    def test_minimal_literal_cannot_hide_forecasts_advice_or_other_claims(self):
        body={'delivery_kind':'minimal','summary':MINIMAL_SUMMARY,'limitations':[MINIMAL_LIMIT],
              'observations':[{'statement':'Synthetic observed fact.','evidence_ids':['fixture']} ]}
        targets=[{'kind':'summary','statement':MINIMAL_SUMMARY},
                 {'kind':'limitation','statement':MINIMAL_LIMIT}]
        for field in ('key_changes','interpretations','forecasts','questions_to_watch',
                      'explanations','charts','documents','narrative_evidence_ids'):
            for target in targets:
                with self.subTest(field=field,kind=target['kind']):
                    self.assertFalse(is_verified_host_target(target,{**body,field:['Extra material']},{}))
        for target in targets:
            for override in ({'advice':{'summary':'Buy now.'}}, {'purchase_decision':{}},
                             {'judgment':'consider'}, {'judgment_reason':'Buy now.'},
                             {'observations':[]}):
                with self.subTest(override=override,kind=target['kind']):
                    self.assertFalse(is_verified_host_target(target,{**body,**override},{}))

    def test_global_clock_max_and_exact_text(self):
        target,body,texts=self.fixture()
        self.assertTrue(is_verified_host_target(target,body,texts))
        for statement in (AVAILABILITY_PREFIX+self.t1+AVAILABILITY_SUFFIX,
                          target['statement']+' All sources are true.',
                          target['statement'].replace('cannot establish','establish')):
            changed={**target,'statement':statement};body2={**body,'limitations':[statement]}
            self.assertFalse(is_verified_host_target(changed,body2,texts))

    def test_missing_inconsistent_invalid_or_unbound_metadata_never_approves(self):
        target,body,texts=self.fixture()
        variants=[]
        variants.append({self.a:texts[self.a]})
        for row in ({'record_id':self.b},
                    {'record_id':self.b,'retrieved_at':self.t2,'source':{'retrieved_at':self.t1}},
                    {'record_id':self.b,'retrieved_at':self.t2[:-1]},
                    {'record_id':self.b,'retrieved_at':None},
                    {'record_id':self.b,'retrieved_at':self.t2,'source':'not metadata'},
                    {'record_id':'another-id','retrieved_at':self.t2}):
            variants.append({**texts,self.b:json.dumps(row)})
        variants.append({**texts,self.b:'{"record_id":"'+self.b+'","retrieved_at":"'+self.t2+'","retrieved_at":"'+self.t1+'"}'})
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertFalse(is_verified_host_target(target,body,variant))
        self.assertFalse(is_verified_host_target({**target,'evidence_ids':[self.a]},body,texts))

    def test_article_dates_and_other_clocks_cannot_stand_in_for_acquisition_metadata(self):
        target,body,texts=self.fixture()
        row={'record_id':self.b,'published_at':self.t2,'observed_at':self.t2,'available_at':self.t2,
             'text':'retrieved_at: '+self.t2, 'text_context':{'retrieved_at':self.t2}}
        self.assertFalse(is_verified_host_target(target,body,{**texts,self.b:json.dumps(row)}))
        self.assertFalse(is_verified_host_target(target,body,{**texts,self.b:json.dumps(row)},
                                                process_context={'clocks_verified':True}))

    def test_top_level_or_nested_acquisition_clock_can_prove_statement(self):
        target,body,texts=self.fixture()
        texts[self.b]=json.dumps({'record_id':self.b,'source':{'retrieved_at':self.t2}})
        self.assertTrue(is_verified_host_target(target,body,texts))
        texts[self.b]=json.dumps({'record_id':self.b,'retrieved_at':'2026-10-27T04:34:44.230651+00:00',
                                  'source':{'retrieved_at':self.t2}})
        self.assertTrue(is_verified_host_target(target,body,texts))

    def test_no_online_clocks_or_wrong_target_role_is_not_a_host_proof(self):
        target,body,texts=self.fixture()
        self.assertFalse(is_verified_host_target({**target,'kind':'summary'},body,texts))
        self.assertFalse(is_verified_host_target(target,{**body,'limitations':[]},texts))
        self.assertFalse(is_verified_host_target(target,{'observations':[],'limitations':body['limitations']},{}))
        archived='Archived datasets are reconstructed by observation time; unknown publication times prevent strict point-in-time claims.'
        self.assertFalse(is_verified_host_target({'kind':'limitation','statement':archived},
            {'limitations':[archived]},{}))

    def test_saved_fresh_fourth_round_availability_is_proved_offline(self):
        fixture=Path(__file__).resolve().parents[1]/'local/review-delivery-upgrade/20261108/author-runtime/sessions/eb2c4177b8a04dce9b45c22b8b765228/workspace'
        review_path=fixture/'reviews/review-d342bf72c52e5d3d5607dd85.json'
        if not review_path.is_file():self.skipTest('Local captured live receipt not included in clean checkout')
        review=json.loads(review_path.read_text())
        import hashlib
        body=next(json.loads(p.read_text())['draft'] for p in (fixture/'drafts').glob('*.json')
                  if hashlib.sha256(json.dumps(json.loads(p.read_text())['draft'],ensure_ascii=False,
                     sort_keys=True,separators=(',',':')).encode()).hexdigest()==review['draft_sha256'])
        target=next(t for t in review['targets'] if t['claim_id']=='L4')
        self.assertTrue(is_verified_host_target(target,body,review['evidence_text']))
        changed=deepcopy(target);changed['statement']=changed['statement'].replace('2026-10-27','2026-10-28')
        self.assertFalse(is_verified_host_target(changed,body,review['evidence_text']))


if __name__=='__main__':unittest.main()
