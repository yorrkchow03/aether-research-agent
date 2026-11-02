"""Synthetic vector semantics test plumbing, not pretrained-model effectiveness."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_agent.event_memory_index import EventMemoryIndex
from research_agent.hybrid_memory import HybridMemoryRetriever

NOW='2026-10-20T12:00:00Z'

def source(rid,text,known='2026-10-01T00:00:00Z'):
    return {'record_id':rid,'text':text,'title':'Synthetic source','url':'https://example.invalid/'+rid,
        'retrieved_at':known,'published_at':'2026-09-30T00:00:00Z','assets':['ETH']}

class SyntheticEncoder:
    """Hand-authored vectors deliberately test independent retrieval only."""
    def __init__(self):self.calls=[]
    def encode(self,texts):
        self.calls.append(texts)
        out=[]
        for text in texts:
            low=text.lower()
            if 'future-secret' in low:raise AssertionError('Future source reached encoder')
            if any(term in low for term in ('rainfall','precipitation')):out.append([1.,0.,0.])
            elif any(term in low for term in ('validator','upgrade')):out.append([0.,1.,0.])
            else:out.append([0.,0.,1.])
        return out

class HybridMemoryTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.rows={'page-a':source('page-a','The validator upgrade plan.'),'page-b':source('page-b','Precipitation affects the synthetic forecast.')}
        self.clock=patch('research_agent.event_memory_index.utcnow',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop)
        self.index=EventMemoryIndex(self.root,source_lookup=lambda rid:deepcopy(self.rows[rid]))
        for rid in self.rows:self.index.upsert_source(rid)
        self.encoder=SyntheticEncoder();self.engine=HybridMemoryRetriever(self.index,self.encoder,'synthetic-test-vectors',cache_fingerprint='fixture-v1')

    def test_dense_candidates_are_independent_of_keyword_pool(self):
        result=self.engine.query('rainfall',limit=1)
        self.assertEqual(result['keyword_candidates'],0)
        self.assertEqual(result['dense_candidates'],2)
        self.assertEqual(result['source_ids'],['page-b'])
        self.assertEqual(result['retrieval_mode'],'hybrid_rrf')
        self.assertNotIn('keyword',result['results'][0]['ranking'])
        self.assertFalse(result['event_equivalence_confirmed'])

    def test_absent_or_failed_encoder_reports_keyword_only(self):
        result=HybridMemoryRetriever(self.index).query('validator',limit=2)
        self.assertEqual(result['retrieval_mode'],'keyword_only');self.assertEqual(result['dense_status'],'not_configured')
        class Broken:
            def encode(self,texts):raise OSError('Synthetic encoder unavailable')
        fallback=HybridMemoryRetriever(self.index,Broken(),'broken').query('validator',limit=2)
        self.assertEqual(fallback['source_ids'],['page-a']);self.assertEqual(fallback['dense_status'],'unavailable')
        self.assertIn('semantic_encoder_unavailable',[g['reason'] for g in fallback['gaps']])

    def test_vector_cache_binds_actual_model_preprocessing_and_content(self):
        first=self.engine.query('rainfall',limit=1);again=self.engine.query('rainfall',limit=1)
        self.assertEqual(first['embedding']['encoded_documents'],2)
        self.assertEqual(again['embedding']['cache_hits'],2);self.assertEqual(again['embedding']['encoded_documents'],0)
        other=HybridMemoryRetriever(self.index,self.encoder,'synthetic-other',cache_fingerprint='fixture-v1').query('rainfall',limit=1)
        self.assertEqual(other['embedding']['encoded_documents'],2)
        changed=HybridMemoryRetriever(self.index,self.encoder,'synthetic-test-vectors',cache_fingerprint='fixture-v2').query('rainfall',limit=1)
        self.assertEqual(changed['embedding']['encoded_documents'],2)
        self.rows['page-c']=source('page-c','A second precipitation bulletin.');self.index.upsert_source('page-c')
        added=self.engine.query('rainfall',limit=2)
        self.assertEqual(added['embedding']['encoded_documents'],1);self.assertEqual(added['embedding']['cache_hits'],2)

    def test_cached_vector_mutation_is_not_silently_used(self):
        self.engine.query('rainfall')
        with self.index.connect() as db:db.execute('UPDATE embeddings SET vector=? WHERE namespace=?',('[999,1,2]',self.engine.namespace))
        result=self.engine.query('validator')
        self.assertEqual(result['retrieval_mode'],'keyword_only');self.assertEqual(result['source_ids'],['page-a'])
        self.assertIn('semantic_encoder_unavailable',[g['reason'] for g in result['gaps']])

    def test_future_data_cannot_change_strict_keyword_rank_or_enter_encoder(self):
        before=self.engine.query('validator',cutoff=NOW,mode='strict')
        self.rows['page-future']=source('page-future','future-secret validator '*100,known='2026-10-25T00:00:00Z')
        self.index.upsert_source('page-future')
        after=self.engine.query('validator',cutoff=NOW,mode='strict')
        self.assertEqual(before['results'],after['results'])
        self.assertEqual(after['dense_candidates'],2)
        self.assertEqual(before['indexed_source_count'],after['indexed_source_count'])
        self.assertFalse(any('future-secret' in text for batch in self.encoder.calls for text in batch))

    def test_explicit_conflict_pair_is_both_or_neither(self):
        self.index.link_sources('page-b','page-a','contradicts',receipt_id='page-b')
        both=self.engine.query('validator',limit=2)
        self.assertEqual(set(both['source_ids']),{'page-a','page-b'})
        self.assertEqual(both['protected_groups'],[['page-a','page-b']])
        tiny=self.engine.query('validator',limit=1)
        self.assertEqual(tiny['source_ids'],[])
        self.assertEqual(tiny['deferred_groups'],[['page-a','page-b']])
        self.assertIn('related_evidence_group_exceeds_limit',[g['reason'] for g in tiny['gaps']])

    def test_later_relationship_cannot_change_strict_selection(self):
        early='2026-10-10T00:00:00Z'
        before=self.engine.query('validator',cutoff=early,mode='strict',limit=1)
        self.index.link_sources('page-b','page-a','contradicts',receipt_id='page-b')
        after=self.engine.query('validator',cutoff=early,mode='strict',limit=1)
        self.assertEqual(before['results'],after['results']);self.assertEqual(after['links'],[])

    def test_entity_filter_is_explicit_unverified_and_card_time_scoped(self):
        self.index.record_card('page-a',start=0,end=20,entity_keys=['synthetic:protocol'])
        match=self.engine.query('validator',entity_key='synthetic:protocol')
        self.assertEqual(match['source_ids'],['page-a'])
        old=self.engine.query('validator',entity_key='synthetic:protocol',cutoff='2026-10-10T00:00:00Z',mode='strict')
        self.assertEqual(old['source_ids'],[])

    def test_invalid_vectors_or_identity_cannot_claim_semantic_retrieval(self):
        with self.assertRaises(ValueError):HybridMemoryRetriever(self.index,self.encoder)
        class Nonfinite:
            def encode(self,texts):return [[float('nan'),1.] for _ in texts]
        result=HybridMemoryRetriever(self.index,Nonfinite(),'nonfinite').query('validator')
        self.assertEqual(result['retrieval_mode'],'keyword_only');self.assertEqual(result['source_ids'],['page-a'])

if __name__=='__main__':unittest.main()
