"""Passage ranking/provenance invariants, not model effectiveness claims."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest

from research_agent.event_memory_index import EventMemoryIndex
from research_agent.hybrid_memory import HybridMemoryRetriever
from research_agent.passage_retrieval import retrieve_passages, retrieval_limits


CUTOFF='2026-11-07T10:00:00Z'


def source(rid,text,published='2026-11-01T00:00:00Z',known='2026-11-02T00:00:00Z'):
    return {'record_id':rid,'text':text,'title':'','url':'https://example.invalid/'+rid,
        'published_at':published,'retrieved_at':known,'assets':['ETH'],'source_kind':'article'}


class Encoder:
    def __init__(self):self.calls=[]
    def encode(self,texts):
        self.calls.append(list(texts));result=[]
        for text in texts:
            if 'future-secret' in text:raise AssertionError('Future source reached encoder')
            low=text.lower()
            result.append([1.,0.,.01] if any(t in low for t in ('rainfall','precipitation')) else
                [0.,1.,.01] if 'validator' in low else [0.,0.,1.])
        return result


class PassageRetrievalTests(unittest.TestCase):
    def engine(self,rows):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        index=EventMemoryIndex(Path(tmp.name),source_lookup=lambda rid:deepcopy(rows[rid]))
        encoder=Encoder()
        return HybridMemoryRetriever(index,encoder,'synthetic-fixture',max_document_chars=100000),encoder

    def test_same_source_returns_multiple_independent_exact_slices(self):
        text=('ETH validator plans '+('A '*650))+('ETH validator concern '+('B '*650))+('ETH validator delayed '+('C '*650))
        row=source('page-a',text)
        result=retrieve_passages([row],'ETH validator',limit=5,cutoff=CUTOFF)
        self.assertGreater(len(result['results']),1)
        self.assertEqual(result['source_ids'],['page-a'])
        self.assertEqual(result['retrieval_unit'],'passage')
        self.assertEqual(len(result['passage_ids']),len(set(result['passage_ids'])))
        for part in result['results']:
            self.assertEqual(part['record_id'],'page-a')
            self.assertEqual(part['excerpt'],text[part['start']:part['end']])
            self.assertEqual(part['sha256'],hashlib.sha256(part['excerpt'].encode()).hexdigest())
            self.assertEqual(len(part['source_slices']),1)

    def test_dense_is_passage_level_independent_of_keyword_hit(self):
        rows={'page-a':source('page-a','Validator update. '+('A '*700)+' Precipitation tomorrow.'),
              'page-b':source('page-b','Validator clients have a security patch.')}
        engine,encoder=self.engine(rows)
        result=retrieve_passages(list(rows.values()),'rainfall',hybrid=engine,limit=1,cutoff=CUTOFF)
        part=result['results'][0]
        self.assertEqual(result['hybrid_retrieval']['lexical_passages'],0)
        self.assertEqual(result['hybrid_retrieval']['dense_status'],'ok')
        self.assertEqual(part['record_id'],'page-a')
        self.assertGreater(part['start'],0)
        self.assertIn('Precipitation',part['excerpt'])
        again=retrieve_passages(list(rows.values()),'rainfall',hybrid=engine,limit=1,cutoff=CUTOFF)
        self.assertEqual(again['hybrid_retrieval']['embedding']['encoded_passages'],0)
        self.assertGreater(again['hybrid_retrieval']['embedding']['cache_hits'],0)

    def test_future_and_outside_window_never_enter_encoder_or_ranks(self):
        rows={'page-a':source('page-a','ETH validator update.'),
            'page-future':source('page-future','future-secret validator',known='2026-11-09T00:00:00Z'),
            'page-outside':source('page-outside','future-secret outside',published='2026-10-20T00:00:00Z')}
        engine,encoder=self.engine(rows)
        result=retrieve_passages(list(rows.values()),'validator',hybrid=engine,mode='strict',
            cutoff=CUTOFF,start='2026-10-30T00:00:00Z',end='2026-11-06T00:00:00Z')
        self.assertEqual(result['source_ids'],['page-a'])
        self.assertFalse(any('future-secret' in text for batch in encoder.calls for text in batch))
        self.assertEqual({g['reason'] for g in result['gaps']},{'not_known_by_cutoff','outside_publication_window'})

    def test_window_requires_publication_clock_and_retrospective_capture_has_own_cutoff(self):
        rows={'page-a':source('page-a','ETH validator retrospective.',known='2026-11-06T00:00:00Z'),
            'page-unknown':source('page-unknown','future-secret undated validator',published=None),
            'page-late':source('page-late','future-secret acquired too late',known='2026-11-08T00:00:00Z'),
            'page-end':source('page-end','future-secret exclusive boundary',published='2026-11-02T00:00:00Z')}
        engine,encoder=self.engine(rows)
        result=retrieve_passages(list(rows.values()),'validator',hybrid=engine,cutoff=CUTOFF,
            acquisition_cutoff=CUTOFF,start='2026-11-01T00:00:00Z',end='2026-11-02T00:00:00Z')
        self.assertEqual(result['source_ids'],['page-a'])
        self.assertEqual({g['reason'] for g in result['gaps']},{'publication_time_unknown_for_window',
            'not_acquired_by_knowledge_cutoff','outside_publication_window'})
        self.assertFalse(any('future-secret' in text for batch in encoder.calls for text in batch))
        strict=retrieve_passages([rows['page-a']],'validator',mode='strict',cutoff='2026-11-02T00:00:00Z',
            acquisition_cutoff=CUTOFF,start='2026-11-01T00:00:00Z',end='2026-11-02T00:00:00Z')
        self.assertEqual(strict['source_ids'],[])

    def test_independent_dense_source_recall_applies_acquisition_cutoff_before_encoding(self):
        rows={'page-a':source('page-a','ETH validator retrospective.',known='2026-11-06T00:00:00Z'),
            'page-late':source('page-late','future-secret validator',known='2026-11-08T00:00:00Z')}
        engine,encoder=self.engine(rows)
        for rid in rows:engine.index.upsert_source(rid)
        result=engine.query('validator',cutoff=CUTOFF,acquisition_cutoff=CUTOFF,
            start='2026-11-01T00:00:00Z',end='2026-11-02T00:00:00Z')
        self.assertEqual(result['source_ids'],['page-a'])
        self.assertFalse(any('future-secret' in text for batch in encoder.calls for text in batch))

    def test_full_report_use_and_subquestion_are_separate_and_context_does_not_reorder(self):
        rows=[source('page-'+str(i),'ETH validator update '+'sample '*i) for i in range(3)]
        baseline=retrieve_passages(rows,'validator',limit=3,cutoff=CUTOFF)
        order=baseline['selection']['baseline_passage_ids'];seen=[]
        task={'original_user_question':'Prepare a weekly ETH report including institutional demand.',
            'asset':'ETH','knowledge_cutoff':CUTOFF}
        def judge(_,**kwargs):
            seen.append(kwargs)
            label={order[0]:'unrelated',order[1]:'needs_context',order[2]:'direct_evidence'}[kwargs['evidence'][0]['passage_id']]
            return {'status':'ok','label':label,'confidence':.95,'dimensions':{
                'relevance':{'choice':label},'subquestion_relevance':{'choice':'unrelated'}}}
        result=retrieve_passages(rows,'validator',limit=3,cutoff=CUTOFF,judge=judge,research_task=task)
        self.assertEqual(result['selection']['ranked_passage_ids'],[order[2],order[1],order[0]])
        self.assertEqual(seen[0]['question'],task['original_user_question'])
        self.assertEqual(seen[0]['retrieval_subquestion'],'validator')
        context=next(row for row in result['results'] if row['passage_id']==order[1])
        self.assertEqual(context['relevance_hint']['next_read']['arguments'],{'record_id':context['record_id'],'full_text':True})
        useful=next(row for row in result['results'] if row['passage_id']==order[2])
        self.assertEqual(useful['relevance_hint']['label'],'direct_evidence')
        self.assertEqual(useful['relevance_hint']['subquestion']['choice'],'unrelated')

    def test_new_rubric_low_confidence_negative_keeps_baseline_and_its_raw_label(self):
        rows=[source('page-'+str(i),'ETH validator update '+'sample '*i) for i in range(3)]
        task={'original_user_question':'Weekly ETH report'}
        baseline=retrieve_passages(rows,'validator',cutoff=CUTOFF)
        def judge(*_,**__):return {'status':'abstained','label':None,'confidence':.28,
            'dimensions':{'relevance':{'choice':'unrelated'},'subquestion_relevance':{'choice':'needs_context'}}}
        result=retrieve_passages(rows,'validator',cutoff=CUTOFF,judge=judge,research_task=task)
        self.assertEqual(result['passage_ids'],baseline['passage_ids'])
        self.assertEqual(result['results'][0]['relevance_hint']['label'],'unrelated')
        self.assertEqual(result['results'][0]['relevance_hint']['decision'],'retain_baseline_uncertain')

    def test_metadata_size_does_not_spend_body_budget(self):
        row=source('page-a','ETH validator update.')
        row['title']='Very long source title '*1000
        row['source']={'sha256':'a'*64,'url':row['url'],'name':'publisher'}
        result=retrieve_passages([row],'validator',limit=1,budget_chars=400,cutoff=CUTOFF)
        self.assertEqual(len(result['results']),1)
        self.assertEqual(result['selection']['cost_estimate'],len(row['text']))
        self.assertEqual(set(result['source_catalog']),{'page-a'})

    def test_chinese_unspaced_query_has_keyword_matches(self):
        rows=[source('page-a','以太坊验证者质押规模有所增加。'),source('page-b','与查询无关的其他信息。')]
        result=retrieve_passages(rows,'以太坊质押',cutoff=CUTOFF)
        self.assertEqual(result['source_ids'],['page-a'])
        self.assertGreater(result['hybrid_retrieval']['lexical_passages'],0)

    def test_legacy_asset_inference_matches_canonical_knowledge_policy(self):
        a=source('page-legacy','Ethereum validator upgrade.')
        a.pop('assets')
        b=source('page-unknown','Unrelated information.')
        b.pop('assets')
        result=retrieve_passages([a,b],'validator',asset='ETH',cutoff=CUTOFF)
        self.assertEqual(result['source_ids'],['page-legacy'])
        self.assertIn({'record_id':'page-unknown','reason':'outside_asset_scope'},result['gaps'])

    def test_jev_keeps_raw_source_hash_separate_from_passage_hash(self):
        row=source('page-a','ETH validator update.')
        row['source']={'sha256':'a'*64,'url':row['url']}
        seen=[]
        def judge(task,**kwargs):
            seen.extend(kwargs['evidence'])
            return {'status':'ok','label':'relevant','confidence':.95}
        result=retrieve_passages([row],'validator',cutoff=CUTOFF,judge=judge)
        self.assertEqual(seen[0]['source']['sha256'],'a'*64)
        self.assertEqual(result['source_catalog']['page-a']['source_sha256'],'a'*64)
        self.assertNotEqual(seen[0]['sha256'],seen[0]['source']['sha256'])

    def test_all_abstained_or_failed_jev_exactly_preserves_baseline_results(self):
        rows=[source('page-'+str(i),'ETH validator update '+'sample '*i) for i in range(4)]
        baseline=retrieve_passages(rows,'ETH validator',limit=4,cutoff=CUTOFF)
        for status in ('abstained','error','unavailable'):
            def judge(*args,**kwargs):return {'status':status,'label':None,'confidence':.4}
            result=retrieve_passages(rows,'ETH validator',limit=4,cutoff=CUTOFF,judge=judge)
            self.assertEqual(result['results'],baseline['results'])
            self.assertEqual(result['selection']['ranked_passage_ids'],baseline['selection']['ranked_passage_ids'])

    def test_partial_abstention_stays_in_its_exact_baseline_slot(self):
        rows=[source('page-'+str(i),'ETH validator update '+'sample '*i) for i in range(3)]
        baseline=retrieve_passages(rows,'ETH validator',limit=3,cutoff=CUTOFF)
        order=baseline['selection']['baseline_passage_ids']
        calls=[]
        def judge(task,**kwargs):
            calls.append(kwargs['evidence'][0]['passage_id'])
            index=len(calls)-1
            return {'status':'abstained' if index==1 else 'ok',
                'label':None if index==1 else 'irrelevant' if index==0 else 'relevant',
                'confidence':.5 if index==1 else .95}
        result=retrieve_passages(rows,'ETH validator',limit=3,cutoff=CUTOFF,judge=judge)
        ranked=result['selection']['ranked_passage_ids']
        self.assertEqual(ranked,[order[2],order[1],order[0]])
        self.assertEqual(result['selection']['jev_reranking']['accepted_count'],2)
        self.assertTrue(result['selection']['jev_reranking']['unjudged_positions_unchanged'])
        self.assertEqual(set(result['passage_ids']),set(order))

    def test_same_label_uses_relevance_score_but_missing_and_ties_keep_baseline(self):
        rows=[source('page-'+str(i),'ETH validator update '+'sample '*i) for i in range(4)]
        baseline=retrieve_passages(rows,'ETH validator',limit=4,cutoff=CUTOFF)
        order=baseline['selection']['baseline_passage_ids']
        probabilities={order[0]:.82,order[1]:None,order[2]:.97,order[3]:.97}
        def judge(task,**kwargs):
            value=probabilities[kwargs['evidence'][0]['passage_id']]
            return {'status':'ok','label':'relevant','confidence':.99,
                'scores':{} if value is None else {'relevant':value}}
        result=retrieve_passages(rows,'ETH validator',limit=4,cutoff=CUTOFF,judge=judge)
        rerank=result['selection']['jev_reranking']
        accepted={row['passage_id']:row for row in rerank['accepted_ranks']}
        # Relevance-ranked Jev positions move the .97 passages ahead of .82;
        # the missing-score item retains slot 2, and .97 ties keep their order.
        self.assertEqual([accepted[pid]['jev_accepted_rank'] for pid in order],[4,2,1,3])
        self.assertEqual(result['selection']['ranked_passage_ids'],[order[2],order[1],order[0],order[3]])
        self.assertEqual(accepted[order[2]]['jev_relevance_score'],.97)
        self.assertIsNone(accepted[order[1]]['jev_relevance_score'])
        self.assertEqual(rerank['strategy_version'],'passage-jev-slot-rrf-v2')

    def test_known_conflict_includes_both_sources_even_without_keyword_hit(self):
        rows=[source('page-a','ETH validator activation confirmed.'),source('page-b','The previously stated plan has been withdrawn.')]
        relationship={'subject':'page-b','object':'page-a','predicate':'corrects','known_at':'2026-11-02T00:00:00Z'}
        result=retrieve_passages(rows,'validator',limit=2,cutoff=CUTOFF,relations=[relationship])
        self.assertEqual(set(result['source_ids']),{'page-a','page-b'})
        self.assertEqual(result['conflicts'][0]['evidence_ids'],['page-a','page-b'])
        self.assertTrue(result['conflicts'][0]['all_endpoints_selected'])
        tiny=retrieve_passages(rows,'validator',limit=1,cutoff=CUTOFF,relations=[relationship])
        self.assertEqual(tiny['results'],[])
        self.assertIn('related_evidence_group_exceeds_context',[g['reason'] for g in tiny['gaps']])
        def judge(*_,**__):return {'status':'ok','label':'unrelated','confidence':.99}
        assisted=retrieve_passages(rows,'validator',limit=2,cutoff=CUTOFF,relations=[relationship],
            judge=judge,research_task={'original_user_question':'Weekly ETH report'})
        self.assertEqual(set(assisted['source_ids']),{'page-a','page-b'})
        self.assertTrue(assisted['conflicts'][0]['all_endpoints_selected'])

    def test_later_relationship_cannot_change_strict_selection(self):
        rows=[source('page-a','ETH validator activation.'),source('page-b','Plan withdrawn.')]
        before=retrieve_passages(rows,'validator',limit=1,mode='strict',cutoff=CUTOFF)
        after=retrieve_passages(rows,'validator',limit=1,mode='strict',cutoff=CUTOFF,
            relations=[{'subject':'page-b','object':'page-a','predicate':'contradicts','known_at':'2026-11-10T00:00:00Z'}])
        self.assertEqual(before['results'],after['results'])

    def test_empty_query_is_source_breadth_and_does_not_invoke_models(self):
        rows={'page-a':source('page-a','ETH '+'x '*1500),'page-b':source('page-b','Second source.')}
        engine,encoder=self.engine(rows)
        def forbidden(*args,**kwargs):raise AssertionError('Empty query must not invoke Jev')
        result=retrieve_passages(list(rows.values()),'',hybrid=engine,judge=forbidden,limit=2,cutoff=CUTOFF)
        self.assertEqual(set(result['source_ids']),set(rows))
        self.assertEqual(encoder.calls,[])
        self.assertEqual(result['selection']['semantic_calls'],0)

    def test_same_url_different_versions_are_not_erased(self):
        a=source('page-old','ETH validator activation scheduled.')
        b=source('page-new','ETH validator activation postponed.')
        b['url']=a['url']
        result=retrieve_passages([a,b],'ETH validator',limit=2,cutoff=CUTOFF)
        self.assertEqual(set(result['source_ids']),{'page-old','page-new'})

    def test_scope_defaults_and_invalid_bounds(self):
        self.assertEqual(retrieval_limits()[0],8)
        self.assertEqual(retrieval_limits('2026-10-30T00:00:00Z','2026-11-06T00:00:00Z'),(20,28000))
        self.assertEqual(retrieval_limits('2026-10-01T00:00:00Z','2026-11-01T00:00:00Z'),(32,44800))
        with self.assertRaises(ValueError):retrieval_limits(limit=True)
        with self.assertRaises(ValueError):retrieve_passages([],mode='future')
        with self.assertRaises(ValueError):retrieve_passages([],start=CUTOFF,end='2026-11-01T00:00:00Z')


if __name__=='__main__':unittest.main()
