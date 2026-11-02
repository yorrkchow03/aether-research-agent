"""Source-preserving passage handoff and large manual-index failure contracts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib,json,unittest
from unittest.mock import patch

from research_agent.event_memory_index import EventMemoryIndex
from research_agent.hybrid_memory import HybridMemoryRetriever
from research_agent.evidence_memory import EvidenceMemory,exact_excerpts
from research_agent.knowledge import KnowledgeStore
from research_agent.memory_library_index import library_inventory,index_inventory,import_version_links
from research_data.library import ResearchLibrary


class SmallEncoder:
    def __init__(self):self.calls=[]
    def encode(self,texts):
        if len(texts)>128:raise ValueError('Bound every encoder call')
        self.calls.append(texts)
        return [[1.,0.] if any(x in text.lower() for x in ('precipitation','rainfall')) else [0.,1.] for text in texts]


class PassageTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.rows={};self.index=EventMemoryIndex(self.root/'index',source_lookup=lambda rid:deepcopy(self.rows[rid]))
    def add(self,rid,text,known='2026-10-01T00:00:00Z'):
        self.rows[rid]={'record_id':rid,'title':'Source','text':text,'retrieved_at':known,
            'published_at':'2026-09-30T00:00:00Z','assets':['ETH'],'url':'https://example.invalid/'+rid}
        self.index.upsert_source(rid)
    def test_nonleading_cross_language_equivalent_match_hands_off_exact_span(self):
        self.add('page-target','Unrelated protocol introduction. '*200+'Precipitation exceeded the earlier measurement. '+('Other context. '*200))
        self.add('page-noise','Unrelated protocol introduction.')
        result=HybridMemoryRetriever(self.index,SmallEncoder(),'synthetic').query('rainfall',limit=1)
        self.assertEqual(result['source_ids'],['page-target']);row=result['results'][0]
        hint=row['semantic_spans'][0];self.assertGreater(hint['start'],0)
        text=self.rows['page-target']['text']
        self.assertEqual(hashlib.sha256(text[hint['start']:hint['end']].encode()).hexdigest(),hint['sha256'])
        packed=EvidenceMemory(None).pack('rainfall',candidates=[self.rows['page-target']],
            passage_hints={'page-target':row['semantic_spans']},budget=6000,
            policy='hybrid_relevance',retrieval_scores={'page-target':.8})
        self.assertIn('Precipitation exceeded',packed['results'][0]['excerpt'])
        for span in packed['results'][0]['source_slices']:
            self.assertEqual(hashlib.sha256(text[span['start']:span['end']].encode()).hexdigest(),span['sha256'])
    def test_bad_hash_or_offsets_never_injects_hinted_text(self):
        text='Original evidence. '*200
        for hint in [{'start':0,'end':200,'sha256':'bad'}, {'start':True,'end':200,'sha256':'bad'}, {'start':-1,'end':100,'sha256':'bad'}]:
            self.assertEqual(exact_excerpts(text,'evidence',hints=[hint]),exact_excerpts(text,'evidence'))
    def test_encoder_batches_bound_more_than_128_uncached_sources(self):
        for i in range(131):self.add('page-'+str(i),'Precipitation bulletin.')
        encoder=SmallEncoder();engine=HybridMemoryRetriever(self.index,encoder,'synthetic')
        first=engine.query('rainfall',limit=2);again=engine.query('rainfall',limit=2)
        self.assertEqual(first['embedding']['encoded_documents'],131)
        self.assertTrue(all(len(batch)<=128 for batch in encoder.calls))
        self.assertEqual(again['embedding']['encoded_passages'],0)
    def test_character_cap_and_changed_preprocessing_remain_explicit(self):
        self.add('page-long','Background. '*400+'Precipitation update.')
        encoder=SmallEncoder()
        short=HybridMemoryRetriever(self.index,encoder,'same',cache_fingerprint='same',max_document_chars=1000).query('rainfall')
        long=HybridMemoryRetriever(self.index,encoder,'same',cache_fingerprint='same',max_document_chars=10000).query('rainfall')
        self.assertIn('semantic_document_character_cap',[g['reason'] for g in short['gaps']])
        self.assertNotEqual(short['embedding']['cache_namespace'],long['embedding']['cache_namespace'])
        self.assertGreater(long['results'][0]['semantic_spans'][0]['start'],1000)
    def test_library_exclusion_is_checked_before_encoding(self):
        self.add('page-one','Precipitation bulletin.')
        self.rows['page-one']['library_exclusion_reasons']=['bad_date']
        encoder=SmallEncoder();result=HybridMemoryRetriever(self.index,encoder,'synthetic').query('rainfall')
        self.assertEqual(result['source_ids'],[]);self.assertEqual(encoder.calls,[])


class LibraryIndexTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.library=ResearchLibrary(self.root/'library')
        cap=self.library.capture('p','https://example.invalid/notice',b'original',retrieved_at='2026-10-02')
        self.a=self.library.put_document('p','https://example.invalid/notice','Notice','Original account.',cap,published_at='2026-10-01')
        cap2=self.library.capture('p','https://example.invalid/notice',b'revision',retrieved_at='2026-10-03')
        self.b=self.library.put_document('p','https://example.invalid/notice','Notice','Revised account.',cap2,published_at='2026-10-01')
        self.index=EventMemoryIndex(self.root/'index',source_lookup=self.library.evidence)
    def test_all_versions_resume_without_rebinding_clocks_or_duplicates(self):
        inventory=library_inventory(self.library);self.assertEqual(inventory['eligible_versions'],2)
        first=index_inventory(self.index,self.library,inventory,self.root/'batch',max_items=1)
        self.assertEqual(first['remaining'],1)
        second=index_inventory(self.index,self.library,inventory,self.root/'batch',max_items=1)
        self.assertEqual(second['status'],'complete_selected_inventory')
        third=index_inventory(self.index,self.library,inventory,self.root/'batch')
        self.assertEqual(third['attempted_this_run'],0)
        with self.index.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],2)
    def test_partial_last_journal_line_recovers_and_changed_inventory_rejected(self):
        inventory=library_inventory(self.library);batch=self.root/'batch'
        index_inventory(self.index,self.library,inventory,batch,max_items=1)
        with (batch/'ITEMS.jsonl').open('a') as file:file.write('{interrupted')
        result=index_inventory(self.index,self.library,inventory,batch)
        self.assertEqual(result['status'],'complete_selected_inventory')
        changed=deepcopy(inventory);changed['scope']='changed'
        with self.assertRaises(ValueError):index_inventory(self.index,self.library,changed,batch)
    def test_restored_index_replays_completed_journal_entries_missing_in_database(self):
        inventory=library_inventory(self.library);batch=self.root/'batch'
        index_inventory(self.index,self.library,inventory,batch)
        with self.index.connect() as db:db.execute('DELETE FROM sources WHERE record_id=?',(self.a,))
        repaired=index_inventory(self.index,self.library,inventory,batch)
        self.assertEqual(repaired['attempted_this_run'],1)
        self.assertEqual(repaired['status'],'complete_selected_inventory')
    def test_one_source_failure_does_not_stop_independent_sources(self):
        inventory=library_inventory(self.library)
        actual=self.index.upsert_source
        with patch.object(self.index,'upsert_source',side_effect=lambda rid:(_ for _ in ()).throw(OSError('missing raw')) if rid==self.a else actual(rid)):
            result=index_inventory(self.index,self.library,inventory,self.root/'batch')
        self.assertEqual(result['indexed'],1);self.assertEqual(len(result['errors']),1)
        retried=index_inventory(self.index,self.library,inventory,self.root/'batch',retry_errors=True)
        self.assertEqual(retried['status'],'complete_selected_inventory')

    def test_bulk_read_matches_scalar_and_revalidates_raw_on_every_call(self):
        before=self.library.evidence_many([self.a,self.b,'page-missing'])
        self.assertEqual(before['records'][self.a],self.library.evidence(self.a))
        self.assertEqual(before['errors']['page-missing'],'KeyError')
        Path(before['records'][self.a]['raw_path']).write_bytes(b'changed')
        after=self.library.evidence_many([self.a,self.b])
        self.assertNotIn(self.a,after['records']);self.assertIn(self.b,after['records'])

    def test_bulk_index_validation_excludes_corruption_and_future_sources_before_lookup(self):
        inventory=library_inventory(self.library);index_inventory(self.index,self.library,inventory,self.root/'batch')
        calls=[]
        def bulk(ids):calls.append(ids);return self.library.evidence_many(ids)
        self.index.source_lookup_many=bulk
        early=self.index.eligible_sources(cutoff='2026-10-02T12:00:00Z',mode='strict')
        self.assertEqual([r['record_id'] for r in early['sources']],[self.a]);self.assertEqual(calls,[[self.a]])
        Path(self.library.evidence(self.a)['raw_path']).write_bytes(b'changed')
        late=self.index.eligible_sources()
        self.assertEqual([r['record_id'] for r in late['sources']],[self.b])
        self.assertIn('source_integrity_failed',[g['reason'] for g in late['gaps']])

    def test_declared_content_revision_import_has_actual_clock_not_publication_clock(self):
        inventory=library_inventory(self.library);index_inventory(self.index,self.library,inventory,self.root/'batch')
        with self.library.connect() as db:
            db.execute('INSERT INTO relations VALUES(?,?,?,?)',(self.b,'content_revision_of',self.a,'{}'))
        result=import_version_links(self.index,self.library,inventory)
        self.assertEqual(len(result['links']),1)
        self.assertGreater(result['links'][0]['known_at'],'2026-10-03T00:00:00Z')
        self.assertEqual(self.index.eligible_links([self.a,self.b],cutoff='2026-10-04T00:00:00Z',mode='strict'),[])

    def test_router_never_masks_primary_missing_capture_with_a_fallback(self):
        from research_agent.library_router import LibraryRouter
        fallback=ResearchLibrary(self.root/'fallback')
        # A record belongs to primary even when its associated capture vanished.
        with self.library.connect() as db:db.execute('DELETE FROM captures')
        router=LibraryRouter(self.library,[fallback]);result=router.evidence_many([self.a])
        self.assertNotIn(self.a,result['records']);self.assertEqual(result['errors'][self.a],'KeyError')

if __name__=='__main__':unittest.main()
