"""Benchmark integrity checks; these do not grade investment performance."""
import json
from pathlib import Path
import tempfile
import unittest

from research_agent.memory_benchmark import freeze_manifest, load_frozen, run_retrieval, OFFLINE_FAMILIES


class MemoryBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def write(path, body):
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(body)
        self.write = write
        for name in ('memory_selection.py', 'memory_benchmark.py', 'knowledge.py', 'advice.py', 'claims.py'):
            write('src/research_agent/' + name, '# frozen fixture\n')
        for name in OFFLINE_FAMILIES:
            write('tests/' + name, '# offline fixture\n')
        write('data/validation/memory_v1/sample.raw', 'raw fixture')
        from research_agent.memory_benchmark import digest
        self.source = {'status': 'captured', 'record_id': 'page-synthetic', 'title': 'Ethereum upgrade',
                       'text': 'Ethereum upgrade is only planned. Mainnet is not confirmed.',
                       'url': 'https://example.org/synthetic', 'retrieved_at': '2026-01-02T00:00:00Z',
                       'raw_sha256': digest(self.root / 'data/validation/memory_v1/sample.raw')}
        write('data/validation/memory_v1/sample.json', json.dumps(self.source))
        self.label = {'source': 'sample', 'event_group': 'synthetic', 'split': 'held_out',
                      'passage': 'Mainnet is not confirmed.', 'question': 'Is Ethereum mainnet confirmed?',
                      'expected': 'No confirmation in this fixture.', 'unsafe_question': 'Guaranteed investment return?',
                      'abstention_reason': 'No investment result exists in this fixture.'}
        self.labels = {'label_author': 'Synthetic test', 'groups': [self.label]}
        self.manifest = self.root / 'data/validation/memory_v1/manifest.json'
        self.write_labels()

    def write_labels(self):
        self.write('examples/evaluation/memory_annotations_v1.json', json.dumps(self.labels))

    def test_freeze_is_immutable_and_includes_test_and_source_hashes(self):
        manifest = freeze_manifest(self.root, self.manifest)
        self.assertIn('tests/test_advice.py', manifest['offline_suites'])
        with self.assertRaises(ValueError):freeze_manifest(self.root, self.manifest)
        self.write('data/validation/memory_v1/sample.json', '{}')
        with self.assertRaisesRegex(ValueError, 'changed'):load_frozen(self.root, self.manifest)

    def test_companion_event_group_cannot_leak_between_splits(self):
        self.labels['groups'].append({**self.label, 'split': 'development'})
        self.write_labels()
        with self.assertRaisesRegex(ValueError, 'cross'):freeze_manifest(self.root, self.manifest)

    def test_invalid_annotations_cannot_silently_pass(self):
        self.labels['groups'][0]['passage'] = 'Not in the actual source'
        self.write_labels()
        with self.assertRaisesRegex(ValueError, 'absent'):freeze_manifest(self.root, self.manifest)

    def test_retrieval_and_time_probes_keep_denominators_and_do_not_claim_model_quality(self):
        freeze_manifest(self.root, self.manifest)
        result = run_retrieval(self.root, self.manifest)
        self.assertEqual(result['unique_questions'], 3)
        self.assertEqual(len(result['cases']), 18)
        self.assertIsNone(result['answer_correctness'])
        self.assertIsNone(result['appropriate_abstention_rate'])
        for row in result['summaries']:
            if row['split'] == 'held_out':
                self.assertEqual(row['questions'], 3)
                self.assertEqual(row['target_capture_blocked'], 1)
                self.assertEqual(row['future_capture_leakage'], 0)


if __name__ == '__main__':unittest.main()
