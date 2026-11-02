"""Batch observation verification keeps exact source identities and fresh checks."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_agent.collection_sources import read_collection_sources, validate_collection_sources
from research_agent.library_router import LibraryRouter
from research_data.http import dumps
from research_data.library.store import ResearchLibrary, digest, instant
from research_data.library.time import timestamp


class MemoryBatchLineageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = ResearchLibrary(Path(temporary.name)/'library',
            window_start='2026-04-26T00:00:00Z', window_end='2026-10-26T00:00:00Z')
        self.dataset = 'market.synthetic.eth.hourly'
        self.library.register_dataset(self.dataset, provider='synthetic-fixture', asset='ETH',
            frequency='1h', scope='Synthetic unit test, not actual market evidence.', units={'price': 'USD'})
        self.capture = self.library.capture('synthetic-fixture', 'https://example.org/synthetic/candles',
            b'SYNTHETIC source bytes for batch integrity tests.', retrieved_at='2026-04-26T00:00:00Z')
        self.first_id = self.library.put(self.dataset, '2026-04-26T00:00:00Z', {'price': 100},
            self.capture, period_end='2026-04-26T01:00:00Z')

    def rows(self, size):
        """Build a long synthetic page using put()'s verified canonical template."""
        first = self.library.evidence(self.first_id)
        first.pop('raw_path')
        records, inserts = [], []
        for offset in range(size):
            row = deepcopy(first)
            row['observed_at'] = instant(timestamp(first['observed_at'])+timedelta(hours=offset))
            row['period_end'] = instant(timestamp(row['observed_at'])+timedelta(hours=1))
            row['values'] = {'price': str(100+offset)}
            content = digest([self.dataset, row['observed_at'], row['period_end'], row['values'], {}, []])
            row['record_id'] = 'live-'+content[:24]
            records.append(row)
            inserts.append((row['record_id'], self.dataset, row['observed_at'], row['period_end'],
                row['retrieved_at'], content, self.capture, dumps(row, sort_keys=True)))
        with self.library.connect() as connection:
            connection.executemany('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?)', inserts)
            connection.executemany('INSERT OR IGNORE INTO observation_captures VALUES(?,?)',
                [(row['record_id'], self.capture) for row in records])
        return records

    def update_body(self, rid, change):
        with self.library.connect() as connection:
            row = json.loads(connection.execute('SELECT body FROM observations WHERE record_id=?', (rid,)).fetchone()[0])
            change(row)
            connection.execute('UPDATE observations SET body=? WHERE record_id=?', (dumps(row, sort_keys=True), rid))

    def test_long_page_uses_one_snapshot_and_one_raw_hash_with_exact_rows(self):
        rows = self.rows(4392)
        expected = [self.library.evidence(rows[i]['record_id']) for i in (0, 2000, 4391)]
        with patch.object(self.library, 'evidence', side_effect=AssertionError('Unexpected per-row evidence read')):
            packet = validate_collection_sources(self.library, rows, dataset_id=self.dataset,
                start=self.library.window_start, end=self.library.window_end,
                cutoff=self.library.window_end, mode='strict', chunk_size=127)
        self.assertEqual(packet['validation']['read_only_connections'], 1)
        self.assertEqual(packet['validation']['raw_objects_verified'], 1)
        self.assertEqual(packet['validation']['unique_captures'], 1)
        self.assertEqual(packet['validation']['saved_rows_compared'], 4392)
        self.assertEqual([packet['records'][i] for i in (0, 2000, 4391)], expected)

    def test_requested_old_version_is_not_replaced_by_newer_observation(self):
        newer_capture = self.library.capture('synthetic-fixture', 'https://example.org/synthetic/corrected',
            b'New synthetic source.', retrieved_at='2026-04-27T00:00:00Z')
        newer = self.library.put(self.dataset, '2026-04-26T00:00:00Z', {'price': 200},
            newer_capture, period_end='2026-04-26T01:00:00Z')
        self.assertNotEqual(newer, self.first_id)
        self.assertEqual(self.library.series(self.dataset)['records'][0]['record_id'], newer)
        actual = read_collection_sources(self.library, [self.first_id, newer])['records']
        self.assertEqual([row['values']['price'] for row in actual], ['100', '200'])
        self.assertEqual([row['library_capture_id'] for row in actual], [self.capture, newer_capture])

    def test_any_quarantined_row_blocks_the_entire_batch(self):
        rows = self.rows(3)
        with self.library.connect() as connection:
            connection.execute('INSERT INTO relations VALUES(?,?,?,?)',
                (rows[1]['record_id'], 'excluded_from_research', 'synthetic_scope_error', '{}'))
        with self.assertRaisesRegex(ValueError, 'quarantined'):
            read_collection_sources(self.library, [row['record_id'] for row in rows])

    def test_raw_hash_is_rechecked_after_an_earlier_success(self):
        read_collection_sources(self.library, [self.first_id])
        original = self.library.evidence(self.first_id)
        Path(original['raw_path']).write_bytes(b'TAMPERED after previous submission')
        with self.assertRaisesRegex(ValueError, 'hash or byte count mismatch'):
            read_collection_sources(self.library, [self.first_id])

    def test_normalized_values_and_source_hash_cannot_be_rebound(self):
        self.update_body(self.first_id, lambda row: row['source'].update(sha256='0'*64))
        with self.assertRaisesRegex(ValueError, 'source/capture'):
            read_collection_sources(self.library, [self.first_id])
        cap = self.library.capture_info(self.capture)
        self.update_body(self.first_id, lambda row: row['source'].update(sha256=cap['sha256']))
        self.update_body(self.first_id, lambda row: row['values'].update(price='999'))
        with self.assertRaisesRegex(ValueError, 'content hash'):
            read_collection_sources(self.library, [self.first_id])

    def test_capture_metadata_and_object_paths_are_not_trusted_without_checks(self):
        with self.library.connect() as connection:
            connection.execute('UPDATE captures SET url=? WHERE capture_id=?',
                ('https://example.org/rebound', self.capture))
        with self.assertRaisesRegex(ValueError, 'capture identity'):
            read_collection_sources(self.library, [self.first_id])
        with self.library.connect() as connection:
            connection.execute('UPDATE captures SET url=? WHERE capture_id=?',
                ('https://example.org/synthetic/candles', self.capture))
            connection.execute('UPDATE objects SET path=?', ('../../outside-source.bin',))
        with self.assertRaisesRegex(ValueError, 'escaped'):
            read_collection_sources(self.library, [self.first_id])

    def test_strict_checks_acquisition_publication_and_complete_interval(self):
        kwargs = {'cutoff': '2026-04-27T00:00:00Z', 'mode': 'strict'}
        self.assertEqual(read_collection_sources(self.library, [self.first_id], **kwargs)['records'][0]['record_id'], self.first_id)
        future_capture = self.library.capture('synthetic-fixture', 'https://example.org/synthetic/future',
            b'Later captured synthetic data.', retrieved_at='2026-04-28T00:00:00Z')
        acquired = self.library.put(self.dataset, '2026-04-26T01:00:00Z', {'price': 101}, future_capture)
        observed = self.library.put(self.dataset, '2026-04-28T01:00:00Z', {'price': 102}, self.capture)
        for rid in (acquired, observed):
            with self.subTest(record_id=rid), self.assertRaisesRegex(ValueError, 'strict cutoff'):
                read_collection_sources(self.library, [rid], **kwargs)
        self.update_body(self.first_id, lambda row: row.update(published_at='2026-04-28T00:00:00Z'))
        with self.assertRaisesRegex(ValueError, 'strict cutoff'):
            read_collection_sources(self.library, [self.first_id], **kwargs)

    def test_saved_row_units_and_window_must_match_exact_original(self):
        rows = self.rows(2)
        bad = deepcopy(rows)
        bad[1]['units']['price'] = 'USDT'
        common = {'dataset_id': self.dataset, 'start': self.library.window_start,
                  'end': self.library.window_end, 'cutoff': self.library.window_end}
        with self.assertRaisesRegex(ValueError, 'differs from the original'):
            validate_collection_sources(self.library, bad, **common)
        with self.assertRaisesRegex(ValueError, 'dataset/window|query window'):
            validate_collection_sources(self.library, rows, **{**common, 'end': '2026-04-26T01:00:00Z'})

    def test_unknown_adapter_uses_original_evidence_and_primary_ids_do_not_route(self):
        row = self.library.evidence(self.first_id)
        calls = []
        adapter = SimpleNamespace(evidence=lambda rid: calls.append(rid) or row)
        packet = read_collection_sources(adapter, [self.first_id])
        self.assertEqual(calls, [self.first_id])
        self.assertEqual(packet['validation']['method'], 'original-evidence-adapter-fallback')
        missing = 'live-'+'0'*24
        router = LibraryRouter(self.library, [SimpleNamespace(root=Path('/synthetic-fallback'),
            contains=lambda rid: rid == missing, evidence=lambda rid: {'record_id': rid})])
        with self.assertRaisesRegex(KeyError, 'selected primary'):
            read_collection_sources(router, [missing])
        with self.assertRaisesRegex(ValueError, 'unique'):
            read_collection_sources(adapter, [self.first_id, self.first_id])
        self.assertEqual(calls, [self.first_id])

    def test_sql_snapshot_is_consistent_and_later_quarantine_applies_next_call(self):
        rows = self.rows(3)
        connect = self.library.connect
        inserted = False
        @contextmanager
        def hooked(readonly=False):
            nonlocal inserted
            with connect(readonly=readonly) as connection:
                class Proxy:
                    def execute(inner, sql, arguments=()):
                        nonlocal inserted
                        cursor = connection.execute(sql, arguments)
                        if 'SELECT * FROM observations' in sql and not inserted:
                            inserted = True
                            with connect() as writer:
                                writer.execute('INSERT INTO relations VALUES(?,?,?,?)',
                                    (rows[1]['record_id'], 'excluded_from_research', 'later_quarantine', '{}'))
                        return cursor
                yield Proxy()
        with patch.object(self.library, 'connect', side_effect=hooked):
            packet = read_collection_sources(self.library, [row['record_id'] for row in rows], chunk_size=1)
        self.assertEqual(len(packet['records']), 3)
        with self.assertRaisesRegex(ValueError, 'quarantined'):
            read_collection_sources(self.library, [row['record_id'] for row in rows])


if __name__ == '__main__':
    unittest.main()
