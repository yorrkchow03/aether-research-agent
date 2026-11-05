from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_agent.jev_nlp_revision import MANIFEST, MANIFEST_SHA, adopt_development


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


class DevelopmentInheritanceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent, self.child = self.root/'parent', self.root/'child'
        self.payload = {'task': 'support', 'question': 'A predeclared claim.', 'evidence': [{'record_id': 'page-a', 'text': 'Original source text.'}]}
        # Match the implementation's canonical Unicode JSON hashing.
        self.job = sha(json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        self.protocol = {'models': {'jev': 'jev-1.14.0', 'doubao': 'doubao-seed-2.1-pro'},
                         'criterion': 'fixed-criteria', 'timeout_seconds': 45, 'max_attempts': 1,
                         'jobs': {self.job: {'job_id': self.job, 'split': 'development', 'payload': self.payload}},
                         'order': [{'job_id': self.job, 'provider': provider, 'split': 'development'} for provider in ('jev', 'doubao')],
                         'implementation': {'policy.py': 'old-code'}}
        self.freeze(self.parent)
        self.freeze(self.child, implementation='revised-code')
        self.receipts = {}
        for provider, status in (('jev', 'abstained'), ('doubao', 'error')):
            result = {'provider': provider, 'status': status, 'cache_key': 'cache-'+provider,
                      'billable_request_made': True, 'physical_request_count': 1,
                      'usage': {'input_tokens': 111, 'output_tokens': 9}}
            first = {'job_id': self.job, 'provider': provider, 'payload_sha256': self.job, 'result': result}
            self.write(self.parent/'first'/provider/(self.job+'.json'), first)
            self.write(self.parent/'started'/provider/(self.job+'.json'), {'payload_sha256': self.job, 'started_at': '2026-11-05'})
            raw = json.dumps({'result': result, 'response': {'typed': 'original provider body'}}, ensure_ascii=False, indent=1)
            self.receipts[provider] = raw.encode()
            database = self.parent/'cache'/provider/'judgments.sqlite'; database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute('CREATE TABLE attempts(attempt_id TEXT,cache_key TEXT,created_at TEXT,body TEXT)')
                connection.execute('INSERT INTO attempts VALUES(?,?,?,?)', ('attempt', 'cache-'+provider, '2026-11-05', raw))
                connection.commit()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(encoded(value))

    def freeze(self, directory, implementation='old-code', **changes):
        protocol = deepcopy(self.protocol); protocol['implementation'] = {'policy.py': sha(implementation.encode())}; protocol.update(changes)
        code = directory/'implementation/policy.py'; code.parent.mkdir(parents=True, exist_ok=True); code.write_text(implementation)
        self.write(directory/'PROTOCOL.json', protocol)
        self.write(directory/'DATASET.json', {'cases': [{'case_id': 'd1', 'expected': {'support': ['supported']}}]})
        self.write(directory/'DRAFT_CASES.json', [{'case_id': 'd1', 'expected': {'support': ['supported']}}])
        self.write(directory/'REFERENCE_REVIEW.json', {'provider_outputs_seen': False, 'cases': ['d1']})
        self.reseal(directory)

    def reseal(self, directory):
        names = ['PROTOCOL.json', 'DATASET.json', 'DRAFT_CASES.json', 'REFERENCE_REVIEW.json']
        if (directory/'EFFECTIVE_CASES.json').exists():
            names.append('EFFECTIVE_CASES.json')
        self.write(directory/'FREEZE.json', {'files': {name: sha((directory/name).read_bytes()) for name in names}})

    def parent_bytes(self):
        return {str(path.relative_to(self.parent)): path.read_bytes() for path in self.parent.rglob('*') if path.is_file()}

    def test_exact_development_bytes_raw_receipts_and_paid_usage_are_preserved_offline(self):
        before = self.parent_bytes()
        with patch('httpx.Client', side_effect=AssertionError('No API clients')):
            metadata = adopt_development(self.parent, self.child)
        self.assertEqual(metadata['provider_requests'], 0)
        self.assertEqual(metadata['copied_first_slots'], 2)
        self.assertEqual(metadata['exported_raw_receipts'], 2)
        self.assertEqual(self.parent_bytes(), before)
        for provider in ('jev', 'doubao'):
            for folder in ('first', 'started'):
                name = folder+'/'+provider+'/'+self.job+'.json'
                self.assertEqual((self.child/name).read_bytes(), before[name])
            path = self.child/'development-parent/receipts'/provider/(self.job+'.json')
            self.assertEqual(path.read_bytes(), self.receipts[provider])
        self.assertFalse((self.child/'cache').exists())
        manifest = (self.child/MANIFEST).read_bytes()
        self.assertEqual(json.loads((self.child/MANIFEST_SHA).read_text())['sha256'], sha(manifest))
        self.assertNotIn(str(self.root), manifest.decode())

    def test_freeze_or_contract_changes_are_rejected_before_copy(self):
        (self.child/'DATASET.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Frozen artifact changed'):
            adopt_development(self.parent, self.child)
        self.freeze(self.child, timeout_seconds=46)
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            adopt_development(self.parent, self.child)
        self.assertFalse((self.child/'first').exists())

    def test_reference_changes_are_rejected_even_when_child_is_resealed(self):
        self.write(self.child/'DATASET.json', {'cases': [{'case_id': 'd1', 'expected': {'support': ['contradicted']}}]})
        self.reseal(self.child)
        with self.assertRaisesRegex(ValueError, 'reference data changed'):
            adopt_development(self.parent, self.child)

    def test_any_parent_test_receipt_or_started_marker_is_rejected(self):
        for folder in ('first', 'started'):
            extra = self.parent/folder/'jev'/'test-job.json'
            self.write(extra, {'status': 'test'})
            with self.subTest(folder=folder), self.assertRaisesRegex(ValueError, 'non-development'):
                adopt_development(self.parent, self.child)
            extra.unlink()

    def test_missing_or_nonterminal_development_and_occupied_child_are_rejected(self):
        path = self.parent/'first/jev'/(self.job+'.json'); original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'terminal'):
            adopt_development(self.parent, self.child)
        path.write_bytes(original)
        self.write(self.child/'first/jev/old.json', {})
        with self.assertRaisesRegex(ValueError, 'must be empty'):
            adopt_development(self.parent, self.child)

    def test_child_calibration_or_repeated_adoption_is_rejected(self):
        self.write(self.child/'CALIBRATION.json', {})
        with self.assertRaisesRegex(ValueError, 'calibrated'):
            adopt_development(self.parent, self.child)
        (self.child/'CALIBRATION.json').unlink()
        adopt_development(self.parent, self.child)
        with self.assertRaises(ValueError):
            adopt_development(self.parent, self.child)

    def test_not_attempted_and_interrupted_results_need_no_fabricated_raw_receipt(self):
        for provider in ('jev', 'doubao'):
            path = self.parent/'first'/provider/(self.job+'.json')
            row = json.loads(path.read_text()); row['result'].pop('cache_key')
            row['result'].update(status='not_attempted' if provider == 'jev' else 'error', usage=None,
                                 physical_request_count=0 if provider == 'jev' else None)
            self.write(path, row)
        metadata = adopt_development(self.parent, self.child)
        self.assertEqual(metadata['exported_raw_receipts'], 0)
        self.assertEqual(len(metadata['raw_receipts_unavailable']), 2)

    def test_development_payload_or_provider_slots_cannot_change(self):
        changed = deepcopy(self.protocol['jobs'])
        changed[self.job]['payload']['question'] = 'A changed question.'
        self.freeze(self.child, jobs=changed)
        with self.assertRaisesRegex(ValueError, 'payload'):
            adopt_development(self.parent, self.child)

    def test_uncheckpointed_wal_is_refused_without_changing_parent(self):
        wal = self.parent/'cache/jev/judgments.sqlite-wal'
        wal.write_bytes(b'active-uncheckpointed-wal-fixture')
        before = self.parent_bytes()
        with self.assertRaisesRegex(ValueError, 'active WAL'):
            adopt_development(self.parent, self.child)
        self.assertEqual(self.parent_bytes(), before)
        self.assertFalse((self.child/'first').exists())

    def test_tampered_saved_implementation_snapshot_is_rejected(self):
        (self.parent/'implementation/policy.py').write_text('Changed old source snapshot')
        with self.assertRaisesRegex(ValueError, 'implementation snapshot changed'):
            adopt_development(self.parent, self.child)

    def test_legacy_inline_effective_cases_can_adopt_into_separate_effective_file(self):
        parent_data = json.loads((self.parent/'DATASET.json').read_text())
        parent_data['cases'][0]['expected']['support'] = ['insufficient', 'supported']
        self.write(self.parent/'DATASET.json', parent_data)
        self.reseal(self.parent)
        self.write(self.child/'EFFECTIVE_CASES.json', parent_data['cases'])
        self.reseal(self.child)
        original_draft = (self.child/'DATASET.json').read_bytes()
        metadata = adopt_development(self.parent, self.child)
        self.assertEqual(metadata['copied_first_slots'], 2)
        self.assertEqual((self.child/'DATASET.json').read_bytes(), original_draft)

    def test_changed_effective_references_are_rejected(self):
        self.write(self.child/'EFFECTIVE_CASES.json', [{'case_id': 'd1', 'expected': {'support': ['contradicted']}}])
        self.reseal(self.child)
        with self.assertRaisesRegex(ValueError, 'scientific view'):
            adopt_development(self.parent, self.child)


if __name__ == '__main__':
    unittest.main()
