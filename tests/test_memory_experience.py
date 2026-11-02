from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_agent.experience_memory import ExperienceMemory


class ExperienceMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.receipt = self.root / 'review.json'
        self.receipt.write_text('{"error":"wrong year","token":"fixture-secret"}')
        self.store = ExperienceMemory(self.root/'experience', redact=lambda value: value.replace('fixture-secret', '[REDACTED]'))

    def save(self, topic='date checking'):
        return self.store.record_correction(topic, 'Draft date differed from original.', 'Compare each date to its cited original.',
            receipts=[self.receipt], applicability=['dates in reports'])

    def test_missing_or_credential_receipt_cannot_create_experience(self):
        with self.assertRaises(ValueError):
            self.store.record_correction('x', 'y', 'z', receipts=[self.root/'missing.json'])
        with self.assertRaises(ValueError):
            self.store.record_correction('x', 'y', 'z', receipts=[self.root/'.env'])
        with self.assertRaises(ValueError):
            self.store.record_correction('x', 'y', 'z', receipts=[self.receipt], kind='trading_rule')

    def test_correction_deduplicates_and_preserves_snapshot_when_original_changes(self):
        a = self.save(); b = self.save()
        self.assertEqual(a['correction_id'], b['correction_id'])
        saved = Path(a['receipts'][0]['path'])
        self.assertNotIn('fixture-secret', saved.read_text())
        self.receipt.write_text('A later version of the review')
        result = self.store.recall('date checking')
        self.assertEqual(len(result['results']), 1)
        self.assertIn('not a financial fact', result['results'][0]['scope'])
        saved.write_text('changed saved bytes')
        self.assertEqual(self.store.recall('date checking')['results'], [])

    def test_cutoff_precedes_acquisition_and_future_rows_do_not_change_rank(self):
        with patch('research_agent.experience_memory.utcnow', return_value='2026-01-02T00:00:00Z'):
            saved = self.save()
        self.assertEqual(self.store.recall('date', cutoff='2026-01-01T00:00:00Z')['results'], [])
        before = self.store.recall('date', cutoff='2026-01-03T00:00:00Z')
        for index in range(12):
            with patch('research_agent.experience_memory.utcnow', return_value='2026-01-05T00:00:00Z'):
                self.save('future date '+str(index))
        after = self.store.recall('date', cutoff='2026-01-03T00:00:00Z')
        self.assertEqual(before, after)
        self.assertEqual(after['results'][0]['correction_id'], saved['correction_id'])

    def test_source_correction_requires_resolvable_evidence(self):
        with self.assertRaises(ValueError):
            self.store.record_correction('x', 'y', 'z', receipts=[self.receipt], evidence_ids=['missing'], kind='source_correction')
        checked = []
        store = ExperienceMemory(self.root/'source-linked', evidence_lookup=lambda rid: checked.append(rid) or {'record_id': rid})
        store.record_correction('x', 'y', 'z', receipts=[self.receipt], evidence_ids=['page-a'], kind='source_correction')
        self.assertEqual(checked, ['page-a'])


if __name__ == '__main__':unittest.main()
