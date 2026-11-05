from copy import deepcopy
import json
import unittest

from research_agent.jev_nlp_policy import VARIANTS, gate_dimensions, pack_evidence


def item(rid, text, score=.8, **kwargs):
    return {'id': rid, 'text': text, 'score': score, **kwargs}


def edge(source, target, label, confidence=.95, **kwargs):
    return {'source': source, 'target': target, 'label': label,
            'confidence': confidence, **kwargs}


class CachedRelationPolicyTests(unittest.TestCase):
    def test_opposing_evidence_survives_lower_relevance(self):
        rows = [item('a', 'Hoodi upgrade is scheduled.', .99),
                item('b', 'Hoodi upgrade did not occur; operators must wait.', .01),
                item('c', 'Hoodi operator checklist.', .98)]
        result = pack_evidence(rows, [edge('a', 'b', 'contradicts')], limit=2)
        self.assertEqual(result['selected_ids'], ['a', 'b'])
        self.assertTrue(result['relation_coverage'][0]['all_endpoints_selected'])
        ablated = pack_evidence(rows, [edge('a', 'b', 'contradicts')],
                                limit=2, variant='graph_no_protection')
        self.assertEqual(set(ablated['selected_ids']), {'a', 'c'})
        self.assertFalse(ablated['relation_coverage'][0]['all_endpoints_selected'])

    def test_correction_chain_retains_exact_provenance_and_history(self):
        rows = [item('old', 'Original scheduled date.', provenance={'url': 'https://example.test/original'}),
                item('new', 'Correction: the scheduled date is withdrawn.'),
                item('latest', 'Subsequent update: no replacement date exists.')]
        frozen = deepcopy(rows)
        result = pack_evidence(rows, [edge('old', 'new', 'supersedes'),
                                     edge('new', 'latest', 'update')], limit=3)
        self.assertEqual(set(result['selected_ids']), {'old', 'new', 'latest'})
        self.assertTrue(all(row['all_endpoints_selected'] for row in result['relation_coverage']))
        rendered = {row['id']: row for row in map(json.loads, result['context'].splitlines())}
        for row in rows:
            self.assertEqual(rendered[row['id']]['text'], row['text'])
        self.assertEqual(rendered['old']['provenance'], rows[0]['provenance'])
        self.assertEqual(rows, frozen)

    def test_different_time_or_scope_is_not_a_contradiction(self):
        rows = [item('testnet', 'The testnet upgrade occurred in May.'),
                item('mainnet', 'The mainnet upgrade is scheduled for June.')]
        result = pack_evidence(rows, [edge('testnet', 'mainnet', 'different_scope')], limit=1)
        self.assertEqual(result['protected_ids'], [])
        self.assertEqual(result['relation_coverage'], [])
        self.assertEqual(len(result['selected_ids']), 1)
        self.assertEqual(result['status'], 'packed')

    def test_low_confidence_and_failed_judgments_never_drop_sources(self):
        rows = [item('a', 'ETH correction text.', relevance={'score': 0, 'confidence': .2, 'status': 'ok'}),
                item('b', 'ETH opposing evidence.', relevance={'score': 0, 'confidence': .99, 'status': 'error'}),
                item('c', 'ETH unresolved source.', relevance={'score': 0, 'confidence': .99, 'status': 'abstained'})]
        result = pack_evidence(rows, [edge('a', 'b', 'duplicate', confidence=.2),
                                     edge('a', 'c', 'duplicate', status='error')], query='ETH', limit=3)
        self.assertEqual(set(result['selected_ids']), {'a', 'b', 'c'})
        self.assertEqual(result['dropped_duplicates'], {})
        self.assertEqual(sum(g['reason'] == 'relevance_unknown_lexical_fallback' for g in result['gaps']), 3)

    def test_duplicate_group_does_not_multiply_demand_or_remove_protected_version(self):
        rows = [item('a', 'Upgrade scheduled.', .9), item('copy', 'Upgrade scheduled.', .8),
                item('other', 'Independent operator context.', .7)]
        result = pack_evidence(rows, [edge('a', 'copy', 'duplicate')], limit=2)
        self.assertEqual(set(result['selected_ids']), {'a', 'other'})
        self.assertEqual(result['dropped_duplicates'], {'copy': 'a'})
        contradicted = pack_evidence(rows, [edge('a', 'copy', 'duplicate'),
                                           edge('a', 'copy', 'contradicts')], limit=2)
        self.assertEqual(set(contradicted['selected_ids']), {'a', 'copy'})
        self.assertEqual(contradicted['dropped_duplicates'], {})
        self.assertIn('duplicate_group_contains_protected_versions', [g['reason'] for g in contradicted['gaps']])

    def test_impossible_protected_budget_returns_explicit_gap_not_one_sided_context(self):
        rows = [item('a', 'Scheduled upgrade.'), item('b', 'Upgrade is postponed.')]
        complete = pack_evidence(rows, [edge('a', 'b', 'corrects')])
        tiny = pack_evidence(rows, [edge('a', 'b', 'corrects')], budget_chars=complete['context_chars']-1)
        self.assertEqual(tiny['selected_ids'], [])
        self.assertEqual(tiny['context'], '')
        self.assertEqual(tiny['status'], 'insufficient_budget')
        gap = next(g for g in tiny['gaps'] if g['reason'] == 'protected_bundle_does_not_fit')
        self.assertEqual(gap['required_chars'], complete['context_chars'])
        self.assertEqual(gap['evidence_ids'], ['a', 'b'])

    def test_every_variant_counts_exact_serialized_characters_and_optional_tokens(self):
        rows = [item('a', '中文证据。', provenance={'publisher': '公共来源'}),
                item('b', 'English source evidence.')]
        tokenizer = lambda text: len(text.encode('utf-8'))
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                result = pack_evidence(rows, variant=variant, budget_chars=100,
                                       token_counter=tokenizer, token_budget=115)
                self.assertEqual(result['context_chars'], len(result['context']))
                self.assertEqual(result['context_tokens'], tokenizer(result['context']))
                self.assertLessEqual(result['context_chars'], 100)
                self.assertLessEqual(result['context_tokens'], 115)
        with self.assertRaises(ValueError):
            pack_evidence(rows, token_budget=10)

    def test_variants_and_ordering_are_deterministic_without_oracle_labels(self):
        rows = [item('b', 'Source B.'), item('a', 'Source A.'), item('c', 'Source C.')]
        relations = [edge('b', 'c', 'update'), edge('a', 'b', 'duplicate')]
        original = pack_evidence(rows, relations, limit=3)
        self.assertEqual(original, pack_evidence(list(reversed(rows)), list(reversed(relations)), limit=3))
        no_graph = pack_evidence(rows, relations, variant='semantic_no_graph', limit=3)
        self.assertEqual(set(no_graph['selected_ids']), {'a', 'b', 'c'})
        self.assertEqual(no_graph['accepted_relations'], [])

    def test_missing_endpoints_and_source_identity_conflicts_are_visible(self):
        rows = [item('a', 'Source text.')]
        result = pack_evidence(rows, [edge('a', 'missing', 'supersedes')])
        self.assertEqual(result['selected_ids'], ['a'])
        self.assertIn('relation_endpoint_unavailable', [g['reason'] for g in result['gaps']])
        with self.assertRaises(ValueError):
            pack_evidence([*rows, item('a', 'Rebound source text.')])

    def test_reference_metadata_is_neither_used_for_selection_nor_rendered(self):
        rows = [item('a', 'Ethereum notice.', .7), item('b', 'Operator context.', .8)]
        plain = pack_evidence(rows, query='Ethereum', limit=1)
        with_references = [{**row, 'expected': ['supported'], 'gold_relevance': 1-score}
                           for row, score in zip(rows, (.7, .8))]
        tagged = pack_evidence(with_references, query='Ethereum', limit=1)
        self.assertEqual(tagged['selected_ids'], plain['selected_ids'])
        self.assertEqual(tagged['context'], plain['context'])

    def test_record_id_excerpt_input_and_empty_pool_have_consistent_budget_accounting(self):
        row = {'record_id': 'page-a', 'excerpt': '原始公告中保留的证据。', 'score': .9}
        result = pack_evidence([row], query='原始公告')
        self.assertEqual(result['selected_records'], [row])
        self.assertEqual(json.loads(result['context'])['text'], row['excerpt'])
        empty = pack_evidence([], query='Ethereum')
        self.assertEqual(empty['selected_ids'], [])
        self.assertEqual(empty['context_chars'], 0)
        self.assertIsNone(empty['context_tokens'])

    def test_dimension_gate_preserves_usable_asset_when_topic_abstains(self):
        judgment = {'status': 'abstained', 'dimensions': {
            'asset': {'choice': 'ETH', 'confidence': .95},
            'topic': {'choice': 'network_upgrade', 'confidence': .4},
            'event_status': {'choice': 'uncertain', 'confidence': .99}}}
        result = gate_dimensions(judgment)
        self.assertFalse(result['whole_case_accepted'])
        self.assertEqual(set(result['accepted_dimensions']), {'asset'})
        failed = gate_dimensions({**judgment, 'status': 'error'})
        self.assertEqual(failed['accepted_dimensions'], {})


if __name__ == '__main__':
    unittest.main()
