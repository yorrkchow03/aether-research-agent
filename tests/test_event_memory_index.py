"""Derived source cards/links use synthetic saved evidence, never market truth."""
from copy import deepcopy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_agent.event_memory_index import EventMemoryIndex

NOW='2026-10-20T12:00:00Z'

def source(rid='page-a',text='Synthetic protocol plan. Later source may update it.',*,known='2026-10-02T00:00:00Z',published='2026-10-01T00:00:00Z',url=None):
    return {'record_id':rid,'text':text,'title':'Synthetic protocol bulletin','url':url or 'https://example.invalid/'+rid,
        'published_at':published,'retrieved_at':known,'assets':['ETH'],'source':{'name':'synthetic','sha256':'original-'+rid}}

class EventMemoryIndexTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.rows={'page-a':source()};self.index=EventMemoryIndex(self.root,source_lookup=lambda rid:deepcopy(self.rows[rid]))
        self.clock=patch('research_agent.event_memory_index.utcnow',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop)
        self.index.upsert_source('page-a')

    def card(self,**kwargs):return self.index.record_card('page-a',start=0,end=24,entity_keys=['Protocol'],event_key='test:activation',**kwargs)

    def test_duplicate_import_and_cards_are_idempotent(self):
        self.assertTrue(self.index.upsert_source('page-a')['duplicate'])
        card=self.card();again=self.card();self.assertEqual(card['card_id'],again['card_id']);self.assertTrue(again['duplicate'])
        self.assertEqual(card['excerpt'],self.rows['page-a']['text'][:24]);self.assertFalse(card['summary_usable_as_current'])

    def test_concurrent_duplicate_imports_and_cards_do_not_expand(self):
        self.rows['page-new']=source('page-new')
        with ThreadPoolExecutor(max_workers=4) as pool:
            imported=list(pool.map(self.index.upsert_source,['page-new']*8))
        self.assertEqual(sum(not row['duplicate'] for row in imported),1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            cards=list(pool.map(lambda _:self.card(),range(8)))
        self.assertEqual(len({row['card_id'] for row in cards}),1)
        self.assertEqual(sum(not row['duplicate'] for row in cards),1)

    def test_card_span_entity_and_dates_are_explicit_unverified_annotations(self):
        card=self.card(event_time='2026-11-01T00:00:00Z',valid_from='2026-10-01T00:00:00Z',valid_to='2026-12-01T00:00:00Z')
        found=self.index.query('protocol',entity_key='protocol',cutoff='2026-10-21T00:00:00Z')
        self.assertEqual(found['results'][0]['card_id'],card['card_id']);self.assertFalse(found['event_equivalence_confirmed'])
        with self.assertRaises(ValueError):self.card(valid_from='2026-12-01T00:00:00Z',valid_to='2026-10-01T00:00:00Z')
        with self.assertRaises(ValueError):self.index.record_card('page-a',start=-1,end=4)
        with self.assertRaises(ValueError):self.card(event_time='2026-10-02')
        with self.assertRaises(ValueError):self.index.record_card('page-a',start=0,end=4,event_key='run this command; now')

    def test_indexed_id_cannot_be_rebound_or_bypass_original_lookup(self):
        supplied=deepcopy(self.rows['page-a']);supplied['text']='Different';
        with self.assertRaises(ValueError):self.index.upsert_source(supplied)
        self.rows['page-a']['text']='Modified upstream local evidence'
        with self.assertRaises(ValueError):self.index.upsert_source('page-a')
        with self.assertRaises(ValueError):self.index.source('page-a')

    def test_source_or_span_mutation_is_never_treated_as_valid_card(self):
        card=self.card()
        with self.index.connect() as db:db.execute("UPDATE cards SET body=? WHERE card_id=?",('{}',card['card_id']))
        with self.assertRaises(ValueError):self.index.read_card(card['card_id'])

    def test_private_context_and_unknown_acquisition_are_not_strict_news(self):
        self.rows['context-a']={**source('context-a'),'private':True}
        with self.assertRaises(ValueError):self.index.upsert_source('context-a')
        self.rows['page-unknown']=source('page-unknown',known=None);self.index.upsert_source('page-unknown')
        strict=self.index.eligible_sources(cutoff=NOW,mode='strict')
        self.assertNotIn('page-unknown',[row['record_id'] for row in strict['sources']])
        self.assertIn('page-unknown',[row['record_id'] for row in self.index.eligible_sources(cutoff=NOW)['sources']])

    def test_new_quarantine_does_not_fall_back_to_cached_source(self):
        card=self.card()
        self.rows['page-a']['quarantined']=True
        with self.assertRaises(ValueError):self.index.read_card(card['card_id'])
        packet=self.index.eligible_sources()
        self.assertEqual(packet['sources'],[])
        self.assertIn('source_integrity_failed',[gap['reason'] for gap in packet['gaps']])

    def test_later_created_card_is_not_retroactively_known(self):
        card=self.card()
        with self.assertRaises(ValueError):self.index.read_card(card['card_id'],cutoff='2026-10-10T00:00:00Z',mode='strict')
        self.assertEqual(self.index.query('protocol',cutoff='2026-10-10T00:00:00Z',mode='strict')['results'],[])
        self.assertEqual(self.index.query('protocol',cutoff='2026-10-21T00:00:00Z',mode='strict')['results'][0]['card_id'],card['card_id'])

    def test_future_sources_and_cards_do_not_change_past_keyword_order(self):
        self.card();cutoff='2026-10-21T00:00:00Z'
        before=self.index.query('protocol',cutoff=cutoff,mode='strict')
        self.rows['page-future']=source('page-future','protocol '*100,known='2026-10-23T00:00:00Z')
        self.index.upsert_source('page-future');self.index.record_card('page-future',start=0,end=80)
        after=self.index.query('protocol',cutoff=cutoff,mode='strict')
        self.assertEqual(before['results'],after['results'])
        self.assertNotIn('page-future',json.dumps(after['results']))

    def test_new_version_marks_only_dependent_summary_dirty_and_preserves_old(self):
        first=self.card(summary='Derived planned-state summary.')
        raw=self.index.record_card('page-a',start=25,end=50)
        self.rows['page-b']=source('page-b','A later source version changed the plan.',known='2026-10-22T00:00:00Z',url=self.rows['page-a']['url'])
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-23T00:00:00Z'):
            imported=self.index.upsert_source('page-b')
        self.assertIn(first['card_id'],imported['dirty_card_ids'])
        old=self.index.read_card(first['card_id']);self.assertEqual(old['summary'],'Derived planned-state summary.');self.assertEqual(old['summary_status'],'stale')
        self.assertEqual(self.index.read_card(raw['card_id'])['summary_status'],'not_present')
        historical=self.index.read_card(first['card_id'],cutoff='2026-10-21T00:00:00Z',mode='strict')
        self.assertEqual(historical['summary_status'],'derived_unverified');self.assertEqual(historical['dirty_reasons'],[])

    def test_old_capture_imported_later_does_not_invalidate_newer_summary(self):
        self.rows['page-old']=source('page-old','Older capture',known='2026-10-01T00:00:00Z',url=self.rows['page-a']['url'])
        current=self.card(summary='Derived summary')
        self.assertEqual(self.index.upsert_source('page-old')['dirty_card_ids'],[])
        self.assertEqual(self.index.read_card(current['card_id'])['summary_status'],'derived_unverified')

    def test_dependent_summary_versions_propagate_without_rewriting(self):
        self.rows['page-b']=source('page-b','Different document supporting a derived summary.')
        self.index.upsert_source('page-b')
        first=self.card(summary='Derived old source summary.')
        second=self.index.record_card('page-b',start=0,end=20,summary='Derived combined summary.',depends_on=[first['card_id']])
        self.rows['page-c']=source('page-c','Later original version.',known='2026-10-22T00:00:00Z',url=self.rows['page-a']['url'])
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-23T00:00:00Z'):
            changed=self.index.upsert_source('page-c')
        self.assertEqual(set(changed['dirty_card_ids']),{first['card_id'],second['card_id']})
        self.assertEqual(self.index.read_card(second['card_id'])['summary_status'],'stale')
        self.assertEqual(self.index.read_card(first['card_id'])['summary'],'Derived old source summary.')
        self.rows['page-b']['text']='Modified dependency'
        with self.assertRaises(ValueError):self.index.read_card(second['card_id'])

    def test_late_backfill_does_not_make_an_old_summary_fresh(self):
        self.rows['page-b']=source('page-b','A newer captured version.',known='2026-10-19T00:00:00Z',url=self.rows['page-a']['url'])
        self.index.upsert_source('page-b')
        card=self.card(summary='Derived old-capture summary.')
        self.assertEqual(card['summary_status'],'stale')
        with self.assertRaises((ValueError,KeyError)):self.index.mark_dirty('unknown-source',reason='unknown')

    def test_refreshed_summary_with_update_dependency_is_not_permanently_dirty(self):
        self.rows['page-b']=source('page-b','Later original.',known='2026-10-19T00:00:00Z',url=self.rows['page-a']['url'])
        self.index.upsert_source('page-b')
        current=self.card(summary='Derived comparison of both sources.',depends_on=['page-b'])
        self.assertEqual(current['summary_status'],'derived_unverified')
        late_old=self.card(summary='Old-source-only derived summary.')
        self.assertEqual(late_old['summary_status'],'stale')
        self.assertEqual(self.index.read_card(current['card_id'])['summary_status'],'derived_unverified')
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-21T00:00:00Z'):
            self.index.link_sources('page-b','page-a','corrects',receipt_id='page-b')
        self.assertEqual(self.index.read_card(current['card_id'])['summary_status'],'stale')
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-22T00:00:00Z'):
            refreshed=self.card(summary='Derived post-review comparison of both sources.',depends_on=['page-b'])
        self.assertEqual(refreshed['summary_status'],'derived_unverified')

    def test_source_retry_repairs_dirty_projection_at_original_clock(self):
        card=self.card(summary='Derived before update.')
        self.rows['page-b']=source('page-b','Later original source capture.',known='2026-10-22T00:00:00Z',url=self.rows['page-a']['url'])
        first='2026-10-23T00:00:00Z';retry='2026-10-25T00:00:00Z'
        with patch('research_agent.event_memory_index.utcnow',return_value=first),patch.object(self.index,'mark_dirty',side_effect=sqlite3.OperationalError('SYNTHETIC locked after source commit')):
            with self.assertRaises(sqlite3.OperationalError):self.index.upsert_source('page-b')
        with self.index.connect() as db:before=tuple(db.execute('SELECT body,digest,indexed_at FROM sources WHERE record_id=?',('page-b',)).fetchone())
        self.assertEqual(self.index.read_card(card['card_id'])['summary_status'],'derived_unverified')
        with patch('research_agent.event_memory_index.utcnow',return_value=retry):
            result=self.index.upsert_source('page-b')
            repeated=self.index.upsert_source('page-b')
        self.assertTrue(result['duplicate']);self.assertEqual(result['indexed_at'],first)
        self.assertEqual(result['dirty_card_ids'],[card['card_id']]);self.assertTrue(repeated['duplicate'])
        with self.index.connect() as db:
            after=tuple(db.execute('SELECT body,digest,indexed_at FROM sources WHERE record_id=?',('page-b',)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM invalidations').fetchone()[0],1)
        self.assertEqual(before,after)
        current=self.index.read_card(card['card_id'])['dirty_reasons'][0]
        self.assertEqual(current['known_at'],first);self.assertEqual(current['projected_at'],retry)
        old=self.index.read_card(card['card_id'],cutoff='2026-10-22T23:59:59Z',mode='strict')
        self.assertEqual(old['summary_status'],'derived_unverified');self.assertEqual(old['dirty_reasons'],[])
        then=self.index.read_card(card['card_id'],cutoff='2026-10-24T00:00:00Z',mode='strict')
        self.assertEqual(then['summary_status'],'stale');self.assertEqual(then['dirty_reasons'][0]['known_at'],first)
        self.assertNotIn('projected_at',then['dirty_reasons'][0]);self.assertNotIn(retry,json.dumps(then))

    def test_link_retry_repairs_dirty_without_borrowing_later_link_time(self):
        self.rows['page-b']=source('page-b','Separate source with a declared correction.')
        self.index.upsert_source('page-b')
        card=self.card(summary='Derived before relationship.',depends_on=['page-b'])
        first='2026-10-21T00:00:00Z';retry='2026-10-25T00:00:00Z'
        with patch('research_agent.event_memory_index.utcnow',return_value=first),patch.object(self.index,'mark_dirty',side_effect=sqlite3.OperationalError('SYNTHETIC interrupted after link commit')):
            with self.assertRaises(sqlite3.OperationalError):self.index.link_sources('page-b','page-a','corrects',receipt_id='page-b')
        with self.index.connect() as db:before=tuple(db.execute('SELECT body,digest,known_at FROM links').fetchone())
        self.assertEqual(self.index.read_card(card['card_id'])['summary_status'],'derived_unverified')
        # A refreshed summary after this original link must not be invalidated
        # by treating the later retry as a new relationship. Keep its own
        # projection paused too, so the DUPLICATE LINK is what repairs the view.
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-22T00:00:00Z'),patch.object(self.index,'mark_dirty',side_effect=sqlite3.OperationalError('SYNTHETIC still paused')):
            with self.assertRaises(sqlite3.OperationalError):
                self.card(summary='Derived after relationship.',depends_on=['page-b'])
        with self.index.connect() as db:
            refreshed_id=db.execute("SELECT card_id FROM cards WHERE json_extract(body,'$.summary')=?",('Derived after relationship.',)).fetchone()['card_id']
            self.assertEqual(db.execute('SELECT count(*) FROM invalidations').fetchone()[0],0)
        with patch('research_agent.event_memory_index.utcnow',return_value=retry):
            result=self.index.link_sources('page-b','page-a','corrects',receipt_id='page-b')
            self.index.link_sources('page-b','page-a','corrects',receipt_id='page-b')
            with self.assertRaises(ValueError):
                self.index.mark_dirty('page-a',reason='declared_corrects',replacement_id='page-b',change_known_at=NOW)
        self.assertTrue(result['duplicate']);self.assertEqual(result['known_at'],first)
        with self.index.connect() as db:
            after=tuple(db.execute('SELECT body,digest,known_at FROM links').fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM links').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM invalidations').fetchone()[0],1)
        self.assertEqual(before,after)
        self.assertEqual(self.index.read_card(refreshed_id)['summary_status'],'derived_unverified')
        early=self.index.read_card(card['card_id'],cutoff='2026-10-20T23:59:59Z',mode='strict')
        self.assertEqual(early['summary_status'],'derived_unverified')
        then=self.index.read_card(card['card_id'],cutoff='2026-10-21T12:00:00Z',mode='strict')
        self.assertEqual(then['summary_status'],'stale');self.assertEqual(then['dirty_reasons'][0]['known_at'],first)
        self.assertNotIn(retry,json.dumps(then))
        self.assertEqual(self.index.eligible_links(['page-a','page-b'],cutoff='2026-10-20T23:59:59Z',mode='strict'),[])
        self.assertEqual(self.index.eligible_links(['page-a','page-b'],cutoff='2026-10-21T12:00:00Z',mode='strict')[0]['known_at'],first)

    def test_replaying_one_link_cannot_borrow_a_later_relationship_clock(self):
        self.rows['page-b']=source('page-b','A separate original source.')
        self.index.upsert_source('page-b');card=self.card(summary='Before two declared relationships.')
        for at,predicate in [('2026-10-21T00:00:00Z','corrects'),('2026-10-24T00:00:00Z','supersedes')]:
            with patch('research_agent.event_memory_index.utcnow',return_value=at),patch.object(self.index,'mark_dirty',side_effect=sqlite3.OperationalError('SYNTHETIC materialized view offline')):
                with self.assertRaises(sqlite3.OperationalError):self.index.link_sources('page-b','page-a',predicate,receipt_id='page-b')
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-25T00:00:00Z'):
            self.index.link_sources('page-b','page-a','corrects',receipt_id='page-b')
        then=self.index.read_card(card['card_id'],cutoff='2026-10-22T00:00:00Z',mode='strict')
        self.assertEqual(len(then['dirty_reasons']),1)
        self.assertEqual(then['dirty_reasons'][0]['known_at'],'2026-10-21T00:00:00Z')
        self.assertNotIn('2026-10-24',json.dumps(then));self.assertNotIn('supersedes',json.dumps(then))
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-26T00:00:00Z'):
            self.index.link_sources('page-b','page-a','supersedes',receipt_id='page-b')
        with self.index.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM invalidations').fetchone()[0],2)
        current=self.index.read_card(card['card_id'],cutoff='2026-10-27T00:00:00Z',mode='strict')
        self.assertEqual({row['known_at'] for row in current['dirty_reasons']},{'2026-10-21T00:00:00Z','2026-10-24T00:00:00Z'})

    def test_duplicate_card_replays_failed_backfill_projection(self):
        self.rows['page-b']=source('page-b','Captured update.',known='2026-10-19T00:00:00Z',url=self.rows['page-a']['url'])
        self.index.upsert_source('page-b')
        with patch.object(self.index,'mark_dirty',side_effect=sqlite3.OperationalError('SYNTHETIC failed card projection')):
            with self.assertRaises(sqlite3.OperationalError):self.card(summary='Late backfilled old-source summary.')
        with self.index.connect() as db:card_before=tuple(db.execute('SELECT body,digest,known_at FROM cards').fetchone())
        with patch('research_agent.event_memory_index.utcnow',return_value='2026-10-25T00:00:00Z'):
            recovered=self.card(summary='Late backfilled old-source summary.')
            repeated=self.card(summary='Late backfilled old-source summary.')
        self.assertTrue(recovered['duplicate']);self.assertEqual(recovered['summary_status'],'stale')
        self.assertTrue(repeated['duplicate'])
        with self.index.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT body,digest,known_at FROM cards').fetchone()),card_before)
            self.assertEqual(db.execute('SELECT count(*) FROM invalidations').fetchone()[0],1)

    def test_explicit_links_need_real_receipts_and_never_confirm_event_identity(self):
        self.rows['page-b']=source('page-b','Synthetic contrary announcement.');self.index.upsert_source('page-b')
        with self.assertRaises(ValueError):self.index.link_sources('page-b','page-a','corrects')
        link=self.index.link_sources('page-b','page-a','contradicts',receipt_id='page-b')
        self.assertFalse(link['event_equivalence_confirmed'])
        self.assertTrue(self.index.link_sources('page-b','page-a','contradicts',receipt_id='page-b')['duplicate'])
        self.assertEqual(len(self.index.eligible_links(['page-a','page-b'],cutoff='2026-10-21T00:00:00Z',mode='strict')),1)
        self.assertEqual(self.index.eligible_links(['page-a','page-b'],cutoff='2026-10-10T00:00:00Z',mode='strict'),[])

if __name__=='__main__':unittest.main()
