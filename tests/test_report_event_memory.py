"""Synthetic counterexamples; none of these fixture events is market evidence."""
import copy
import json
import unittest

from research_agent.report_event_memory import build_report_event_context


AS_OF = '2026-10-25T00:00:00Z'


def page(rid, *, published='2026-10-01T00:00:00Z', acquired='2026-10-02T00:00:00Z', url=None, **extra):
    return {'record_id':rid, 'dataset_id':'library.documents.synthetic',
        'title':'SYNTHETIC fixture upgrade', 'text':'SYNTHETIC fixture source text.',
        'url':url or 'https://synthetic.invalid/'+rid, 'source_kind':'article',
        'published_at':published, 'available_at':acquired, 'retrieved_at':acquired,
        'source':{'name':'synthetic', 'sha256':'fixture-sha-'+rid, 'retrieved_at':acquired}, **extra}


class FakeLibrary:
    window_start='2026-04-26T00:00:00Z'
    window_end='2026-10-26T00:00:00Z'

    def __init__(self, rows, links=None, search=None):
        self.rows=rows; self.links=links or {}; self.search=search or []
        self.search_calls=[]; self.link_calls=[]; self.fail_links=set()

    def contains(self, rid):
        return rid in self.rows

    def exclusion_reasons(self, rid):
        return self.rows[rid].get('exclusion_reasons', [])

    def record_links(self, rid, **params):
        self.link_calls.append((rid,params))
        if rid in self.fail_links:
            raise OSError('SYNTHETIC relation read failure')
        return {'results':copy.deepcopy(self.links.get(rid, [])), 'next_offset':None}

    def search_documents(self, query, **params):
        self.search_calls.append((query,params))
        return {'results':copy.deepcopy(self.search), 'next_offset':None}


class FakeKnowledge:
    def __init__(self, rows, events=None, links=None, search=None, reports=None):
        self.rows={row['record_id']:row for row in rows}; self.events=events or []
        self.library=FakeLibrary(self.rows,links,search); self.reports=reports or {}
        self.reads=[]

    def get(self, rid):
        self.reads.append(rid)
        if rid not in self.rows:
            raise ValueError('SYNTHETIC missing raw evidence')
        return copy.deepcopy(self.rows[rid])

    def event_history(self, asset, **kwargs):
        return copy.deepcopy(self.events)

    def report(self, rid):
        if rid not in self.reports:
            raise ValueError('SYNTHETIC missing saved report')
        return copy.deepcopy(self.reports[rid])


def event(version, status, ids, recorded):
    return {'event_id':'synthetic-event-1','version':version,'status':status,
        'evidence_ids':ids,'review_id':'synthetic-review-'+str(version),'available_at':recorded}


def relation(left,right,predicate='content_revision_of',known='2026-10-22T00:00:00Z'):
    return {'subject':left,'object':right,'predicate':predicate,
        'body':{'known_at':known} if known else {}}


class ReportEventContextTests(unittest.TestCase):
    def build(self, store, sources, **kwargs):
        return build_report_event_context(store,sources,as_of=AS_OF,related_queries=[],**kwargs)

    def test_saved_reviewed_event_retains_plan_and_delay_without_truth_claim(self):
        store=FakeKnowledge([page('planned'),page('delay',published='2026-10-21T00:00:00Z',acquired='2026-10-22T00:00:00Z')],
            events=[event(1,'planned',['planned'],'2026-10-03T00:00:00Z'),
                    event(2,'delayed',['delay'],'2026-10-22T00:00:00Z')])
        result=self.build(store,['planned'])
        self.assertEqual(result['event_groups'][0]['evidence_ids'],['planned','delay'])
        self.assertEqual(result['event_groups'][0]['group_type'],'reviewed_event')
        self.assertEqual([u['recorded_status'] for u in result['event_groups'][0]['recorded_updates']],['planned','delayed'])
        self.assertTrue(all(u['evidence_complete'] for u in result['event_groups'][0]['recorded_updates']))
        self.assertTrue(result['event_groups'][0]['unverified'])
        self.assertFalse(result['comparison']['semantic_fact_changes_confirmed'])

    def test_explicit_cross_source_conflict_is_not_same_source_update(self):
        store=FakeKnowledge([page('a'),page('b'),page('c')],links={'a':[relation('a','b','contradicts')]})
        store.library.fail_links={'c'}
        result=self.build(store,['a','c'])
        grouped=next(group for group in result['event_groups'] if len(group['evidence_ids'])==2)
        self.assertEqual(grouped['group_type'],'explicit_relation_group')
        self.assertEqual(grouped['source_relations'][0]['predicate'],'contradicts')
        self.assertEqual({row['evidence_id'] for row in result['source_cards']},{'a','b','c'})
        self.assertIn('source_relationships_unavailable',[g['reason'] for g in result['gaps']])

    def test_strict_excludes_late_document_and_late_event_status(self):
        late=page('secret-later',published='2026-10-21T00:00:00Z',acquired='2026-10-27T00:00:00Z')
        store=FakeKnowledge([page('a'),late],events=[event(1,'cancelled',['a','secret-later'],'2026-10-27T00:00:00Z')])
        result=self.build(store,['a','secret-later'],availability_mode='strict')
        self.assertEqual([s['evidence_id'] for s in result['source_cards']],['a'])
        encoded=json.dumps(result)
        self.assertNotIn('secret-later',encoded)
        self.assertNotIn('cancelled',encoded)
        observed=self.build(store,['a','secret-later'])
        self.assertTrue(next(s for s in observed['source_cards'] if s['evidence_id']=='secret-later')['version_acquired_after_as_of'])

    def test_strict_rejects_late_or_undated_relation_without_leaking_endpoint(self):
        store=FakeKnowledge([page('a'),page('future-relation'),page('undated-relation')],
            links={'a':[relation('a','future-relation','corrects','2026-10-26T00:00:00Z'),
                        relation('a','undated-relation','corrects',None)]})
        result=self.build(store,['a'],availability_mode='strict')
        self.assertNotIn('future-relation',json.dumps(result));self.assertNotIn('undated-relation',json.dumps(result))
        self.assertIn('relationship_acquisition_unknown',[g['reason'] for g in result['gaps']])
        self.assertEqual(store.reads,['a'])

    def test_versions_preserved_but_keyword_similarity_and_forum_posts_not_merged(self):
        store=FakeKnowledge([page('old',url='https://synthetic.invalid/upgrade'),
            page('new',url='https://synthetic.invalid/upgrade',published='2026-10-20T00:00:00Z'),
            page('other'),page('post1',url='https://synthetic.invalid/thread#post-1'),
            page('post2',url='https://synthetic.invalid/thread#post-2')])
        result=self.build(store,list(store.rows))
        versions=next(g for g in result['event_groups'] if g['group_type']=='source_updates')
        self.assertEqual(versions['evidence_ids'],['old','new'])
        self.assertEqual(sum(g['group_type']=='unlinked_source' for g in result['event_groups']),3)
        self.assertEqual(len(result['source_cards']),5)

    def test_declared_event_metadata_requires_its_own_acquisition_in_strict_mode(self):
        store=FakeKnowledge([page('a',event_id='synthetic-late-event',event_known_at='2026-10-27T00:00:00Z'),
                             page('b',event_id='synthetic-late-event')])
        strict=self.build(store,['a','b'],availability_mode='strict')
        self.assertEqual(len(strict['event_groups']),2)
        self.assertNotIn('synthetic-late-event',json.dumps(strict))
        observed=self.build(store,['a','b'])
        self.assertEqual(observed['event_groups'][0]['group_type'],'declared_event')

    def test_comparison_only_uses_resolved_saved_report_and_source_membership(self):
        saved={'created_at':'2026-10-04T00:00:00Z','report':{'title':'SYNTHETIC earlier research',
            'facts':[{'statement':'SYNTHETIC old sentence','evidence_ids':['a']}]}}
        store=FakeKnowledge([page('a'),page('b')],reports={'saved':saved})
        result=self.build(store,['a','b'],previous_report={'report_id':'saved','report':{'facts':[]}})
        self.assertEqual(result['comparison']['new_source_ids'],['b'])
        self.assertEqual(result['comparison']['reused_source_ids'],['a'])
        self.assertFalse(result['comparison']['semantic_fact_changes_confirmed'])
        unknown=self.build(store,['a'],previous_report={'created_at':'2026-10-01T00:00:00Z','report':saved})
        self.assertEqual(unknown['comparison']['status'],'no_verified_previous_report')
        store.reports['saved']['created_at']='2026-10-26T00:00:00Z'
        self.assertEqual(self.build(store,['a'],previous_report='saved')['comparison']['status'],'no_verified_previous_report')

    def test_budget_marks_missing_event_counterevidence_without_silently_deleting_it(self):
        store=FakeKnowledge([page('a'),page('b'),page('c')],events=[event(1,'unconfirmed',['a','b','c'],'2026-10-22T00:00:00Z')])
        result=self.build(store,['a'],max_sources=2)
        self.assertEqual(len(store.reads),2);self.assertTrue(result['limits']['source_budget_exhausted'])
        self.assertEqual(result['event_groups'][0]['evidence_ids'],['a','b'])
        self.assertFalse(result['event_groups'][0]['recorded_updates'][0]['evidence_complete'])
        self.assertIn('event_evidence_incomplete',[g['reason'] for g in result['gaps']])

    def test_official_newer_entity_match_is_unverified_candidate_not_event_update(self):
        old=page('old',published='2026-09-28T00:00:00Z')
        newer=page('official',published='2026-10-22T00:00:00Z',source={'name':'ethereum','sha256':'synthetic'},
            text='SYNTHETIC milestone postponed; no new date.')
        unrelated=page('press',published='2026-10-23T00:00:00Z')
        store=FakeKnowledge([old,newer,unrelated],search=[unrelated,newer])
        result=build_report_event_context(store,['old'],query='fixture schedule',as_of=AS_OF,
            related_queries=[{'query':'Hoodi','source':'ethereum'}])
        candidate=result['newer_related_candidates'][0]
        self.assertEqual(candidate['evidence_id'],'official')
        self.assertEqual(candidate['newer_than_selected_source_ids'],['old'])
        self.assertEqual(candidate['publication_lag_seconds']['old'],24*86400)
        self.assertFalse(candidate['event_equivalence_confirmed'])
        self.assertTrue(candidate['needs_original_review'])
        self.assertEqual(len(result['event_groups']),3)
        self.assertEqual(store.library.search_calls[0][1]['source'],'ethereum')
        self.assertLessEqual(result['limits']['related_queries'],2)

    def test_same_entity_search_does_not_bypass_strict_acquisition_cutoff(self):
        newer=page('late-official',published='2026-10-22T00:00:00Z',acquired='2026-10-27T00:00:00Z')
        store=FakeKnowledge([page('a'),newer],search=[newer])
        result=build_report_event_context(store,['a'],query='SYNTHETIC entity',as_of=AS_OF,availability_mode='strict')
        self.assertEqual(result['newer_related_candidates'],[])
        self.assertNotIn('late-official',json.dumps(result))

    def test_missing_original_cannot_fall_back_to_supplied_excerpt_or_private_profile(self):
        private=page('context-user',private=True)
        store=FakeKnowledge([page('a'),private])
        result=self.build(store,[{'record_id':'missing','text':'Forged summary'},'context-user','a'])
        self.assertEqual([s['evidence_id'] for s in result['source_cards']],['a'])
        self.assertNotIn('Forged summary',json.dumps(result))
        self.assertIn('source_unavailable_or_integrity_failed',[g['reason'] for g in result['gaps']])

    def test_group_budget_defers_groups_but_preserves_ids_and_source_cards(self):
        store=FakeKnowledge([page('a'),page('b'),page('c')])
        result=self.build(store,['a','b','c'],max_events=1)
        self.assertEqual(len(result['event_groups']),1);self.assertEqual(len(result['source_cards']),3)
        self.assertEqual(len(result['deferred_source_ids']),2)
        self.assertIn('event_group_budget_exhausted',[g['reason'] for g in result['gaps']])
        self.assertLessEqual(len(result['prompt_context']),2800)

    def test_invalid_budgets_and_query_sequences_fail_before_store_reads(self):
        store=FakeKnowledge([page('a')])
        for kwargs in ({'max_sources':0},{'max_related_queries':3},{'related_queries':'not a list'},
                       {'availability_mode':'future'},{'query':None}):
            with self.assertRaises(ValueError):
                build_report_event_context(store,['a'],**kwargs)
        self.assertEqual(store.reads,[])


if __name__=='__main__':
    unittest.main()
