import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from research_agent.working_memory import WorkingMemory
from research_agent.tool_result_views import recommended_view_budget


A = 'page-' + 'a' * 24
B = 'page-' + 'b' * 24


class ToolResultViewsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = WorkingMemory(self.root, redact=lambda text: text.replace('fixture-secret', '[REDACTED]'))
        self.store.begin('job', 'Source-grounded weekly ETH report')

    def compact(self, name, payload, budget=6000, status='success'):
        rendered = self.store.progressive_tool_view('job', name, payload, max_chars=budget, status=status)
        self.assertLessEqual(len(rendered), budget)
        view = json.loads(rendered)
        original = self.store.read_artifact('job', view['artifact']['artifact_id'])['text']
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), view['artifact']['sha256'])
        return view, json.loads(original)

    def test_large_event_appendix_preserves_source_excerpt_and_relation_without_history_copy(self):
        excerpt = 'Exact source passage with a date and 12.345 ETH.'
        payload = {'results': [{'record_id': A, 'published_at': '2026-10-31T00:00:00Z',
            'url': 'https://example.org/notice', 'excerpt': excerpt,
            'source_slices': [{'start': 0, 'end': len(excerpt),
                'sha256': hashlib.sha256(excerpt.encode()).hexdigest()}]}],
            'event_context': {'source_cards': [{'record_id': A, 'text': 'copied history ' * 3000}],
                'event_groups': [{'evidence_ids': [A, B], 'recorded_updates': ['large update ' * 1000],
                    'source_relations': [{'subject': A, 'object': B, 'predicate': 'corrects',
                        'known_at': '2026-11-01T00:00:00Z', 'basis': 'saved_source_relationship'}]}],
                'newer_related_candidates': [], 'gaps': [], 'prompt_context': 'redundant ' * 1000}}
        view, restored = self.compact('recall_memory', payload, 2400)
        rendered = json.dumps(view)
        self.assertIn(excerpt, rendered)
        self.assertIn('corrects', rendered)
        self.assertIn(A, rendered); self.assertIn(B, rendered)
        self.assertNotIn('copied history', rendered)
        self.assertNotIn('redundant redundant', rendered)
        self.assertEqual(view['metadata']['event_context_counts']['source_cards'], 1)
        self.assertEqual(restored, payload)

    def test_passage_view_keeps_exact_text_clocks_and_context_pointer_without_ranking_diagnostics(self):
        rows=[]
        next_read={'tool':'read_evidence','arguments':{'record_id':A,'full_text':True},
            'adjacent_range_hint':{'start':0,'end':2000}}
        for index,rid in enumerate((A,B)):
            text=('Exact captured source '+str(index)+'。')*70
            digest=hashlib.sha256(text.encode()).hexdigest()
            rows.append({'record_id':rid,'passage_id':rid+':span:0:'+str(len(text)),
                'excerpt':text,'input_excerpt_sha256':digest,
                'source_slices':[{'start':0,'end':len(text),'sha256':digest}],
                'ranking':{'baseline_rank':index+1,'final_rank':index+1,
                    'keyword':{'rank':index+1,'score':-123.456},'rrf_score':.032},
                'relevance_hint':{'status':'abstained','label':'needs_context','confidence':.55,
                    'decision':'retain_baseline_uncertain','next_read':next_read,
                    'subquestion':{'choice':'needs_context','confidence':.45,
                        'probabilities':{'direct_evidence':.3,'useful_background':.1,'unrelated':.1,'needs_context':.5},
                        'probability_scope':'Verbose provider diagnostics '*150},
                    'scope':'Verbose per-passage scope '*150}})
        clocks={'publication_start_inclusive':'2026-10-17T00:00:00Z',
            'publication_end_exclusive':'2026-10-24T00:00:00Z',
            'acquisition_cutoff':'2026-11-07T12:00:00Z','acquisition_cutoff_source':'explicit','mode':'observed'}
        payload={'retrieval_unit':'passage','results':rows,'source_ids':[A,B],
            'source_catalog':{A:{'record_id':A},B:{'record_id':B}},
            'selection':{'selected_count':2,'time_policy':clocks,'jev_reranking':{
                'strategy_version':'passage-task-use-slot-rrf-v3','accepted_count':0,'judgment_count':2,
                'accepted_ranks':[{'passage_id':A,'debug':'full ranking trace '*500}]}}}
        budget=recommended_view_budget(payload)
        view,restored=self.compact('recall_memory',payload,budget)
        self.assertEqual(view['selection']['time_policy'],clocks)
        self.assertEqual(view['source_ids'],[A,B]);self.assertEqual(set(view['source_catalog']),{A,B})
        for original,shown in zip(rows,view['results']):
            self.assertEqual(shown['excerpt'],original['excerpt'])
            self.assertEqual(shown['source_slices'],original['source_slices'])
            self.assertEqual(shown['input_excerpt_sha256'],hashlib.sha256(shown['excerpt'].encode()).hexdigest())
            self.assertEqual(set(shown['ranking']),{'baseline_rank','final_rank'})
            hint=shown['relevance_hint']
            self.assertEqual(hint['next_read'],next_read)
            self.assertEqual(hint['subquestion'],{'choice':'needs_context','confidence':.45})
            self.assertEqual((hint['status'],hint['label'],hint['confidence'],hint['decision']),
                ('abstained','needs_context',.55,'retain_baseline_uncertain'))
        self.assertNotIn('accepted_ranks',view['selection']['jev_reranking'])
        self.assertEqual(view['selection']['jev_reranking']['diagnostics_pointer'],'/selection/jev_reranking/accepted_ranks')
        self.assertEqual(restored,payload)

    def test_large_numeric_result_keeps_values_and_units_together_with_data_pointer(self):
        values = {'price': '0.1234567890123456789', **{'volume-' + str(i): str(i) for i in range(500)}}
        units = {key: 'USD' if key == 'price' else 'ETH' for key in values}
        payload = {'record': {'record_id': 'series-' + 'a' * 24,
            'data_file': 'data/eth-hourly.json', 'record_count': 720,
            'values': values, 'units': units, 'text': 'large data ' * 3000}}
        view, restored = self.compact('request_public_api', payload, 1800)
        metadata = view['metadata']
        self.assertEqual(metadata['data_file'], 'data/eth-hourly.json')
        self.assertEqual(metadata['record_count'], 720)
        self.assertEqual(metadata['values']['price'], values['price'])
        self.assertEqual(metadata['units']['price'], 'USD')
        self.assertEqual(set(metadata['values']), set(metadata['units']))
        self.assertTrue(metadata['numeric_view_incomplete'])
        self.assertLess(len(metadata['values']), 500)
        self.assertEqual(restored, payload)

    def test_review_returns_actionable_rejections_and_counts_not_all_success_quotes(self):
        payload = {'status': 'needs_revision', 'review': {'status': 'needs_revision', 'verdicts': [
            *[{'claim_id': 'O' + str(i), 'status': 'supported', 'reason': 'Supported.',
                'quotes': [{'record_id': A, 'text': 'Success quote ' * 100}]} for i in range(25)],
            {'claim_id': 'I2', 'status': 'contradicted', 'reason': 'Announcement is dated outside the requested week.',
                'correction': 'Label this as later_update, not in-window evidence.',
                'quotes': [{'record_id': B, 'text': 'Exact publisher date.'}]}]}}
        view, restored = self.compact('publish_report', payload, 2400)
        rendered = json.dumps(view)
        self.assertEqual(view['metadata']['review_status_counts'], {'supported': 25, 'contradicted': 1})
        self.assertIn('I2', rendered)
        self.assertIn('Label this as later_update, not in-window evidence.', rendered)
        self.assertIn(B, rendered)
        self.assertNotIn('Success quote', rendered)
        self.assertEqual(restored, payload)

    def test_failure_keeps_exact_error_and_complete_stderr_lines_before_verbose_log(self):
        payload = {'status': 'failed', 'exit_code': 7, 'error_type': 'HttpRateLimit',
            'error': 'RPC provider returned HTTP 429.', 'repair': 'Retry with bounded exponential backoff.',
            'stderr': '429 Too Many Requests\n' + ('verbose diagnostic line\n' * 1000) + 'Retry-After: 3\n',
            'stdout': 'fixture-secret ' + ('unrelated output\n' * 1000)}
        view, restored = self.compact('execute', payload, 1800, 'error')
        self.assertEqual(view['status'], 'error')
        self.assertEqual(view['metadata']['exit_code'], 7)
        self.assertEqual(view['metadata']['error'], payload['error'])
        self.assertIn('Retry-After: 3', json.dumps(view))
        self.assertNotIn('fixture-secret', json.dumps(view))
        self.assertEqual(restored['stderr'], payload['stderr'])
        self.assertIn('[REDACTED]', restored['stdout'])

    def test_oversized_quote_is_a_pointer_instead_of_a_sliced_source(self):
        quote = 'Sentence ' * 900
        payload = {'results': [{'record_id': A, 'excerpt': quote, 'published_at': '2026-10-31T00:00:00Z'}]}
        view, restored = self.compact('recall_memory', payload, 900)
        self.assertNotIn('Sentence', json.dumps(view))
        self.assertIn('/results', json.dumps(view))
        self.assertEqual(restored['results'][0]['excerpt'], quote)

    def test_budget_holds_for_unicode_long_path_and_null_diagnostics(self):
        nested = WorkingMemory(self.root / ('a' * 100) / ('b' * 100))
        nested.begin('job', 'Test')
        payload = {'status': 'needs_revision', 'error': '未知 ' * 2000,
            'review': {'verdicts': [{'claim_id': 'I1', 'status': 'insufficient', 'quotes': None}]},
            'event_context': {'event_groups': None}}
        rendered = nested.progressive_tool_view('job', 'review_report', payload, max_chars=800)
        self.assertLessEqual(len(rendered), 800)
        view = json.loads(rendered)
        self.assertIn('relative_path', view['artifact'])
        self.assertIn('未知', nested.read_artifact('job', view['artifact']['artifact_id'])['text'])

    def test_repeated_compaction_reuses_one_immutable_artifact(self):
        payload = {'results': [{'record_id': A, 'text': 'x' * 10000}]}
        one, _ = self.compact('recall_memory', payload, 1600)
        two, _ = self.compact('recall_memory', payload, 1600)
        self.assertEqual(one['artifact'], two['artifact'])
        self.assertEqual(len(self.store.artifacts('job')), 1)
        self.assertEqual(self.store.state('job')['facts'], [])

    def test_small_result_and_explicit_file_expansion_are_unchanged(self):
        payload = {'status': 'ok', 'record_id': A}
        self.assertEqual(json.loads(self.store.progressive_tool_view('job', 'read_evidence', payload)), payload)
        expansion = '@@ lines 1-4 of 4 @@\n' + 'source data ' * 2000
        self.assertEqual(self.store.progressive_tool_view('job', 'read_file', expansion), expansion)
        self.assertEqual(len(self.store.artifacts('job')), 0)


if __name__ == '__main__':
    unittest.main()
