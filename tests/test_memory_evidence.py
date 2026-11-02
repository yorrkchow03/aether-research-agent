from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from research_agent.evidence_memory import EvidenceMemory, exact_excerpts
from research_agent.knowledge import KnowledgeStore


def source(letter, text, known='2026-01-01T00:00:00Z', published='2026-01-01T00:00:00Z'):
    return {'record_id': 'page-'+letter*24, 'title': 'Ethereum upgrade research', 'text': text,
        'retrieved_at': known, 'published_at': published, 'assets': ['ETH'], 'source_kind': 'page',
        'url': 'https://example.org/'+letter, 'source': {'url': 'https://example.org/'+letter, 'sha256': letter*64}}


class EvidenceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = [source('a', 'Ethereum upgrade is planned for testing.'),
                     source('b', 'Ethereum upgrade is delayed; the prior plan is superseded.'),
                     source('c', 'Ethereum unrelated repeated trading commentary.')]

    def test_exact_query_slices_find_late_passages_without_paraphrase(self):
        text = 'Long generic preface. '*500 + 'The unusual validator activation was postponed indefinitely.'
        pieces = exact_excerpts(text, 'unusual validator activation postponed indefinitely', 1600)
        self.assertTrue(any('postponed indefinitely' in p['text'] for p in pieces))
        self.assertTrue(all(p['text'] == text[p['start']:p['end']] for p in pieces))
        self.assertLessEqual(sum(len(p['text']) for p in pieces), 1600)

    def test_strict_before_capture_blocks_semantic_calls_and_observed_is_retrospective(self):
        future = source('d', 'Ethereum upgrade postponed.', known='2026-01-10T00:00:00Z')
        calls = []
        judge = lambda *a, **k: calls.append(k) or {'status': 'unavailable'}
        engine = EvidenceMemory(None, judge)
        strict = engine.pack('Ethereum', candidates=[future], cutoff='2026-01-05T00:00:00Z', mode='strict')
        self.assertEqual(strict['results'], [])
        self.assertEqual(calls, [])
        self.assertTrue(engine.pack('Ethereum', candidates=[future], cutoff='2026-01-05T00:00:00Z', mode='observed')['results'])

    def test_failed_semantic_provider_keeps_deterministic_result(self):
        plain = EvidenceMemory(None).pack('Ethereum upgrade', candidates=self.rows, budget=6000)
        broken = EvidenceMemory(None, lambda *_a, **_k: {'status': 'unavailable'}).pack(
            'Ethereum upgrade', candidates=self.rows, budget=6000, semantic_mode='apply')
        self.assertEqual(plain['source_ids'], broken['source_ids'])
        self.assertTrue(any(g['reason']=='semantic_judgment_unavailable' for g in broken['gaps']))

    def test_unresolved_conflict_is_both_or_neither_and_remains_inspectable(self):
        def judge(task, **kwargs):
            if task == 'relevance':return {'status': 'ok', 'label': 'relevant', 'scores': {'relevant': .9}}
            return {'status': 'ok', 'label': 'contradicts', 'confidence': .9, 'cache_key': 'fixture'}
        engine = EvidenceMemory(None, judge)
        result = engine.pack('Ethereum upgrade', candidates=self.rows[:2], budget=6000, limit=2, semantic_mode='apply')
        self.assertEqual(set(result['source_ids']), {r['record_id'] for r in self.rows[:2]})
        tiny = engine.pack('Ethereum upgrade', candidates=self.rows[:2], budget=6000, limit=1, semantic_mode='apply')
        self.assertEqual(tiny['results'], [])
        self.assertEqual(tiny['selection']['reason'], 'protected_evidence_bundle_does_not_fit')
        self.assertFalse(tiny['conflicts'][0]['all_endpoints_selected'])

    def test_shadow_judgments_do_not_change_selected_ids(self):
        plain = EvidenceMemory(None).pack('Ethereum upgrade', candidates=self.rows, budget=6000)
        judge = lambda task, **kwargs: {'status': 'ok', 'label': 'contradicts' if task=='relation' else 'irrelevant', 'scores': {'relevant': 0}}
        shadow = EvidenceMemory(None, judge).pack('Ethereum upgrade', candidates=self.rows, budget=6000, semantic_mode='shadow')
        self.assertEqual(plain['source_ids'], shadow['source_ids'])
        self.assertTrue(shadow['conflicts'])

    def test_private_context_is_never_sent_to_semantic_provider(self):
        calls = []
        row = {'record_id':'context-'+'a'*24,'text':'Private holdings and budget','private':True}
        result = EvidenceMemory(None, lambda *a, **k: calls.append(k)).pack('budget', candidates=[row])
        self.assertEqual(result['results'], [])
        self.assertEqual(calls, [])

    def test_short_prompt_is_bounded_and_does_not_duplicate_slice_text(self):
        future=[{**source('d','Ethereum upgrade '+str(i),known='2026-02-01T00:00:00Z'),
                 'record_id':'page-'+format(i+500,'024x')} for i in range(100)]
        blocked=EvidenceMemory(None).pack('Ethereum',candidates=future,mode='strict',
            cutoff='2026-01-05T00:00:00Z',budget=200)
        self.assertEqual(len(blocked['gaps']),100)
        self.assertLessEqual(len(blocked['prompt_context']),768)
        packed=EvidenceMemory(None).pack('Ethereum',candidates=self.rows,budget=6000)
        for row in packed['results']:
            self.assertTrue(all('text' not in item and 'sha256' in item for item in row['source_slices']))

    def test_relationship_must_be_known_before_strict_cutoff(self):
        rows=[source('a','ETH correction old'), source('b','ETH correction replacement')]
        memory=EvidenceMemory(None)
        cutoff='2026-01-05T00:00:00Z'
        pair={'subject':rows[0]['record_id'],'object':rows[1]['record_id'],'predicate':'contradicts'}
        unknown=memory.pack('ETH correction',candidates=rows,mode='strict',cutoff=cutoff,relations=[pair])
        self.assertEqual(unknown['conflicts'],[])
        self.assertIn('relationship_acquisition_time_unknown',[g['reason'] for g in unknown['gaps']])
        future={**pair,'known_at':'2027-01-01T00:00:00Z','object':'future-secret-id'}
        hidden=memory.pack('ETH correction',candidates=rows,mode='strict',cutoff=cutoff,relations=[future])
        self.assertEqual(hidden['conflicts'],[])
        self.assertNotIn('future-secret-id',json.dumps(hidden))
        known=memory.pack('ETH correction',candidates=rows,mode='strict',cutoff=cutoff,
                          relations=[{**pair,'known_at':'2026-01-01T00:00:00Z'}])
        self.assertEqual(len(known['conflicts']),1)

    def test_source_integrity_failure_cannot_fall_back_to_search_snippet(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = KnowledgeStore(Path(temporary))
            store.register(self.rows[0])
            with patch.object(store, 'get', side_effect=ValueError('Source was changed')):
                result = EvidenceMemory(store).pack('Ethereum', candidates=self.rows[:1], budget=6000)
            self.assertEqual(result['results'], [])
            self.assertEqual(result['gaps'][0]['reason'], 'saved_source_unavailable_or_integrity_failed')

    def test_older_exact_source_ranks_before_newer_generic_material_and_future_statistics(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = KnowledgeStore(Path(temporary))
            exact = source('a', 'Hoodi postponement clarification: Ethereum testnet activation is delayed.')
            exact['title'] = 'Hoodi postponement clarification'
            store.register(exact)
            for index in range(80):
                row = source('b', 'Ethereum testing commentary about general network activity.', known='2026-01-02T00:00:00Z')
                row.update(record_id='page-'+format(index+100,'024x'),url='https://example.org/generic/'+str(index),title='Ethereum commentary')
                row['source']={'url':row['url']}
                store.register(row)
            before = store.search_news('Hoodi postponement clarification Ethereum', cutoff='2026-01-05T00:00:00Z', limit=3)
            self.assertEqual(before[0]['record_id'], exact['record_id'])
            for index in range(10):
                row = source('c', 'Hoodi postponement clarification Ethereum', known='2026-02-01T00:00:00Z')
                row.update(record_id='page-'+format(index+1000,'024x'),url='https://example.org/future/'+str(index))
                row['source']={'url':row['url']}
                store.register(row)
            after = store.search_news('Hoodi postponement clarification Ethereum', cutoff='2026-01-05T00:00:00Z', limit=3)
            self.assertEqual(before, after)


if __name__ == '__main__':unittest.main()
