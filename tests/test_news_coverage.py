"""Deterministic synthetic coverage/alias checks; fixtures are not market news."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_agent.knowledge import KnowledgeStore,news_coverage,source_content_fingerprint
from research_agent.memory_selection import select_evidence,context_cost


def page(number,*,url=None,text='Ethereum synthetic source.',published='2026-10-01T00:00:00Z',known='2026-10-02T00:00:00Z'):
    return {'record_id':'page-'+format(number,'024x'),'title':'Ethereum synthetic announcement',
        'text':text,'url':url or 'https://synthetic.invalid/'+str(number),'source_kind':'page',
        'published_at':published,'retrieved_at':known,'first_seen_at':known,'assets':['ETH']}


class NewsCoverageTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=KnowledgeStore(Path(tmp.name)/'knowledge')

    def test_explicit_window_adds_breadth_but_topic_search_remains_focused(self):
        rows=[page(1,text='Ethereum Glamsterdam upgrade plan.'),
              page(2,text='Ethereum exchange-traded fund application update.'),
              page(3,text='Ethereum stablecoin protocol risk disclosure.')]
        for row in rows:self.store.register(row)
        narrow=self.store.candidate_news('Glamsterdam',cutoff='2026-10-08T00:00:00Z')
        self.assertEqual([r['record_id'] for r in narrow['candidates']],[rows[0]['record_id']])
        broad=self.store.candidate_news('Glamsterdam',cutoff='2026-10-08T00:00:00Z',
            start='2026-10-01T00:00:00Z',end='2026-10-08T00:00:00Z')
        self.assertEqual({r['record_id'] for r in broad['candidates']},{r['record_id'] for r in rows})
        self.assertTrue(broad['breadth_recall']['enabled'])
        self.assertEqual(broad['coverage']['time_roles']['in_window'],3)

    def test_breadth_keeps_earlier_dates_before_bounded_recent_cap(self):
        for i in range(125):
            self.store.register(page(i+1,published='2026-10-29T00:00:00Z',known='2026-10-30T00:00:00Z'))
        early=page(1000,text='Ethereum different early event.',published='2026-10-01T00:00:00Z')
        self.store.register(early)
        pool=self.store.candidate_news('unmatchedtopic',cutoff='2026-11-01T00:00:00Z',
            start='2026-10-01T00:00:00Z',end='2026-11-01T00:00:00Z')
        self.assertIn(early['record_id'],[r['record_id'] for r in pool['candidates']])
        self.assertLessEqual(len(pool['candidates']),120)
        self.assertTrue(pool['candidate_pool_at_limit'])

    def test_breadth_never_crosses_asset_publication_or_acquisition_cutoff(self):
        eligible=page(1)
        later=page(2,published='2026-10-09T00:00:00Z')
        future=page(3,known='2026-10-09T00:00:00Z')
        other=page(4,text='Bitcoin synthetic event.');other['title']='Bitcoin synthetic event';other['assets']=['BTC']
        for row in (eligible,later,future,other):self.store.register(row)
        pool=self.store.candidate_news('',cutoff='2026-10-08T00:00:00Z',mode='strict',
            start='2026-10-01T00:00:00Z',end='2026-10-08T00:00:00Z')
        self.assertEqual([r['record_id'] for r in pool['candidates']],[eligible['record_id']])

    def test_coverage_distinguishes_outside_and_unknown_dates_without_truth_claims(self):
        rows=[page(1,published='2026-09-30'),page(2,published='2026-10-04'),
              page(3,published='2026-10-08'),page(4,published=None)]
        value=news_coverage(rows,start='2026-10-01',end='2026-10-08')
        self.assertEqual(value['time_roles'],dict(in_window=1,background=1,later_update=1,unknown_time=1,unscoped=0))
        self.assertEqual(value['publisher_host_count'],1)
        self.assertEqual(value['explicit_event_count'],0)
        self.assertIn('not proof of independent reporting',value['scope'])
        self.assertIn('no_captured_in_window_documents',news_coverage(rows[:1],start='2026-10-01',end='2026-10-08')['gaps'])

    def test_large_coverage_summary_has_bounded_previews(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        rows=[page(i,published=(start+timedelta(days=i)).isoformat(),url='https://h'+str(i)+'.invalid/') for i in range(100)]
        value=news_coverage(rows,start='2026-01-01',end='2026-12-01')
        self.assertEqual(value['publisher_host_count'],100)
        self.assertEqual(len(value['publisher_host_preview']),12)
        self.assertEqual(len(value['publication_day_counts']),31)
        self.assertEqual(value['publication_days_omitted'],69)

    def test_library_breadth_uses_disjoint_strata_and_same_cutoff(self):
        from research_data.library import ResearchLibrary
        library=ResearchLibrary(self.store.root/'library')
        library.window_start='2026-10-01T00:00:00Z';library.window_end='2026-11-01T00:00:00Z'
        self.store.library=library
        ids=[]
        for i,day in enumerate((1,4,8,12,16,20,24,29)):
            date=f'2026-10-{day:02d}T00:00:00Z';url='https://library.invalid/'+str(i)
            capture=library.capture('synthetic',url,b'raw fixture '+str(i).encode(),retrieved_at=date)
            ids.append(library.put_document('synthetic',url,'Ethereum synthetic','Synthetic unrelated event '+str(i),capture,published_at=date))
        pool=self.store.candidate_news('doesnotmatch',cutoff='2026-11-01T00:00:00Z',
            start='2026-10-01T00:00:00Z',end='2026-11-01T00:00:00Z')
        self.assertEqual({r['record_id'] for r in pool['candidates']},set(ids))


class ExactSourceGroupingTests(unittest.TestCase):
    def test_canonical_same_origin_complete_duplicates_use_one_slot_and_keep_alias(self):
        a=page(1,url='https://synthetic.invalid/a?utm_source=x')
        b=page(2,url='https://synthetic.invalid/a#fragment')
        b['source_kind']='article'
        independent=page(3,text='Ethereum different event.')
        result=select_evidence([a,b,independent],'Ethereum',policy='relevance',budget=6000,limit=2)
        self.assertEqual({r['record_id'] for r in result['results']},{a['record_id'],independent['record_id']})
        self.assertEqual(result['selection']['exact_duplicate_count'],1)
        self.assertEqual(result['selection']['exact_duplicate_groups'][0]['alias_ids'],[b['record_id']])

    def test_full_content_revision_shared_excerpt_and_independent_origins_stay_separate(self):
        a=page(1,url='https://synthetic.invalid/a',text='Shared introduction. Revision one.')
        b=page(2,url=a['url'],text='Shared introduction. Revision two.')
        c=page(3,text=a['text'])
        rows=[]
        for row in (a,b,c):
            new={k:v for k,v in row.items() if k!='text'}
            new['excerpt']='Shared introduction.';new['source_content_sha256']=source_content_fingerprint(row);rows.append(new)
        result=select_evidence(rows,'Ethereum',policy='relevance',limit=3,budget=6000)
        self.assertEqual(len(result['results']),3)
        self.assertEqual(result['selection']['exact_duplicate_count'],0)
        unknown=[{k:v for k,v in row.items() if k!='source_content_sha256'} for row in rows[:2]]
        self.assertEqual(len(select_evidence(unknown,'Ethereum',policy='relevance',budget=6000)['results']),2)

    def test_alias_dependencies_and_protected_identity_are_preserved(self):
        a=page(1,url='https://synthetic.invalid/a');b=page(2,url=a['url'])
        correction=page(3,text='Ethereum synthetic correction.')
        result=select_evidence([a,b,correction],'Ethereum',policy='relevance',budget=6000,
            limit=2,dependencies={a['record_id']:[correction['record_id']]},required_ids=[b['record_id']])
        self.assertEqual({r['record_id'] for r in result['results']},{b['record_id'],correction['record_id']})
        blocked=select_evidence([a,b,correction],'Ethereum',policy='relevance',budget=6000,
            limit=1,dependencies={a['record_id']:[correction['record_id']]},required_ids=[b['record_id']])
        self.assertEqual(blocked['results'],[])

    def test_same_content_with_changed_publication_metadata_is_not_collapsed(self):
        a=page(1,url='https://synthetic.invalid/a');b=page(2,url=a['url'],published='2026-10-02')
        result=select_evidence([a,b],'Ethereum',policy='relevance',budget=6000)
        self.assertEqual(len(result['results']),2)

    def test_semantic_rerank_changes_order_but_leaves_unjudged_baseline_and_budget(self):
        rows=[page(1),page(2,text='Ethereum relevant synthetic event.'),page(3,text='Ethereum unknown candidate.')]
        scores={row['record_id']:value for row,value in zip(rows,(.9,.5,.7))}
        result=select_evidence(rows,'Ethereum',policy='semantic_rerank',budget=6000,
            retrieval_scores=scores,semantic_scores={rows[0]['record_id']:0,rows[1]['record_id']:1})
        self.assertEqual([r['record_id'] for r in result['results']],[rows[1]['record_id'],rows[2]['record_id'],rows[0]['record_id']])
        self.assertEqual(result['selection']['semantic_scores_used'],2)
        self.assertEqual(result['selection']['cost_estimate'],sum(context_cost(row) for row in result['results']))
        with self.assertRaises(ValueError):select_evidence(rows,'Ethereum',policy='semantic_rerank',semantic_scores={rows[0]['record_id']:float('nan')})


if __name__=='__main__':unittest.main()
