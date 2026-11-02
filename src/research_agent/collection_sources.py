"""Per-call, read-only verification of saved library observation identities.

The selected library's primary observation table is read in one SQLite snapshot.
Captured bytes are hashed once per distinct object during this call, never cached
across submissions. A later observation/capture cannot replace a requested ID.
Opaque library adapters retain their original evidence() verification boundary.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter

from research_data.library.store import ResearchLibrary, digest
from research_data.library.time import timestamp


def _chunks(values, size):
    for offset in range(0, len(values), size):
        yield values[offset:offset+size]


def _time_scope(row, cutoff, mode):
    if mode != 'strict':
        return
    before = timestamp(cutoff)
    known = row.get('available_at') or row.get('first_seen_at') or row.get('retrieved_at')
    if not known:
        raise ValueError('Collection source acquisition time is unknown')
    for value in (known, row.get('retrieved_at'), row.get('first_seen_at'),
                  row.get('published_at'), row.get('observed_at'), row.get('period_end')):
        if value and timestamp(value) > before:
            raise ValueError('Collection source is outside the strict cutoff')


def _stat(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def read_collection_sources(library, record_ids, *, cutoff=None, mode='observed', chunk_size=500):
    """Return original evidence rows in request order, with per-call verification.

    Numeric query collections originate in the primary library; this optimized
    path deliberately does not route an absent ID to a different old library.
    Unknown adapters use their ordinary evidence() method instead of assuming a
    compatible SQL schema. Empty/duplicate IDs are rejected before any reads.
    """
    started = perf_counter()
    if mode not in ('observed', 'strict') or (mode == 'strict' and not cutoff):
        raise ValueError('A valid mode and explicit strict cutoff are required')
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or not 1 <= chunk_size <= 900:
        raise ValueError('Collection chunk size must be 1..900')
    if isinstance(record_ids, (str, bytes)):
        raise ValueError('Provide a bounded sequence of observation IDs')
    ids = list(record_ids)
    if not 1 <= len(ids) <= 10000 or any(not isinstance(rid, str) or not rid for rid in ids) or len(set(ids)) != len(ids):
        raise ValueError('Collection requires 1..10000 unique observation IDs')
    primary = getattr(library, 'primary', library)
    if not isinstance(primary, ResearchLibrary):
        if not library or not callable(getattr(library, 'evidence', None)):
            raise ValueError('A library evidence reader is required')
        rows = []
        for rid in ids:
            actual = library.evidence(rid)
            excluded = actual.get('library_exclusion_reasons') or []
            if callable(getattr(library, 'exclusion_reasons', None)):
                excluded = [*excluded, *library.exclusion_reasons(rid)]
            if actual.get('record_id') != rid:
                raise ValueError('Library evidence reader returned a different identity')
            if excluded:
                raise ValueError('Collection source is quarantined: '+rid)
            _time_scope(actual, cutoff, mode)
            rows.append(actual)
        return {'records': rows, 'validation': {'method': 'original-evidence-adapter-fallback',
            'record_count': len(rows), 'evidence_calls': len(rows), 'read_only_snapshot': False,
            'elapsed_seconds': perf_counter()-started,
            'scope': 'Adapter evidence checks retained; no SQL/schema assumptions or persistent verification cache.'}}

    root = primary.root.resolve()
    objects_root = (root/'objects').resolve()
    if not objects_root.is_relative_to(root):
        raise ValueError('Library objects directory escaped the selected library')
    observations, captures, contracts, exclusions = {}, {}, {}, {}
    queries = 0
    with primary.connect(readonly=True) as connection:
        # Explicit BEGIN pins all chunk reads to the same WAL snapshot. Opening
        # a read-only connection alone would allow different SELECT snapshots.
        connection.execute('BEGIN')
        queries += 1
        for chunk in _chunks(ids, chunk_size):
            marks = ','.join('?' for _ in chunk)
            for row in connection.execute(f'SELECT * FROM observations WHERE record_id IN ({marks})', chunk):
                observations[row['record_id']] = dict(row)
            queries += 1
        missing = [rid for rid in ids if rid not in observations]
        if missing:
            raise KeyError('Observation absent from selected primary library: '+missing[0])
        bodies = {rid: json.loads(observations[rid]['body']) for rid in ids}
        subjects = list(dict.fromkeys([*ids, *(row['document_id'] for row in bodies.values() if row.get('document_id'))]))
        for chunk in _chunks(subjects, chunk_size):
            marks = ','.join('?' for _ in chunk)
            for row in connection.execute(f"SELECT subject,object FROM relations WHERE predicate='excluded_from_research' AND subject IN ({marks})", chunk):
                exclusions.setdefault(row['subject'], []).append(row['object'])
            queries += 1
        for rid, row in bodies.items():
            if exclusions.get(rid) or exclusions.get(row.get('document_id')):
                raise ValueError('Collection source is quarantined: '+rid)
        capture_ids = list(dict.fromkeys(observations[rid]['capture_id'] for rid in ids))
        for chunk in _chunks(capture_ids, chunk_size):
            marks = ','.join('?' for _ in chunk)
            for row in connection.execute(f'SELECT c.*,o.path AS object_path,o.bytes AS object_bytes FROM captures c LEFT JOIN objects o USING(sha256) WHERE capture_id IN ({marks})', chunk):
                captures[row['capture_id']] = dict(row)
            queries += 1
        dataset_ids = list(dict.fromkeys(observations[rid]['dataset_id'] for rid in ids))
        for chunk in _chunks(dataset_ids, chunk_size):
            marks = ','.join('?' for _ in chunk)
            for row in connection.execute(f'SELECT dataset_id,body FROM datasets WHERE dataset_id IN ({marks})', chunk):
                contracts[row['dataset_id']] = json.loads(row['body'])
            queries += 1

        verified_objects = {}
        for cid in capture_ids:
            cap = captures.get(cid)
            if not cap or cap['status'] != 200 or cap['error'] or not cap['sha256'] or not cap['object_path']:
                raise ValueError('Collection capture is missing, failed or has no source body')
            if cid != 'capture-'+digest([cap['source_id'], cap['url'], cap['sha256'], cap['retrieved_at'], cap['status'], cap['error']])[:24]:
                raise ValueError('Collection capture identity differs from its source metadata')
            path = (root/cap['object_path']).resolve()
            if not path.is_relative_to(objects_root):
                raise ValueError('Collection raw object path escaped the library objects')
            key = (path, cap['sha256'])
            if key not in verified_objects:
                before = _stat(path)
                raw = path.read_bytes()
                after = _stat(path)
                if before != after:
                    raise ValueError('Collection raw object changed while being verified')
                if hashlib.sha256(raw).hexdigest() != cap['sha256'] or len(raw) != cap['object_bytes']:
                    raise ValueError('Collection source hash or byte count mismatch')
                verified_objects[key] = after
        actuals = []
        for rid in ids:
            sql = observations[rid]
            actual = bodies[rid]
            cap = captures[sql['capture_id']]
            contract = contracts.get(sql['dataset_id'])
            if not contract:
                raise ValueError('Collection dataset contract is missing')
            if any(actual.get(key) != sql[column] for key, column in (
                ('record_id', 'record_id'), ('dataset_id', 'dataset_id'),
                ('observed_at', 'observed_at'), ('period_end', 'period_end'),
                ('retrieved_at', 'known_at'), ('first_seen_at', 'known_at'),
                ('available_at', 'known_at'), ('library_capture_id', 'capture_id'))):
                raise ValueError('Collection observation differs from its indexed identity')
            calculated = digest([actual['dataset_id'], actual['observed_at'], actual['period_end'],
                actual['values'], actual.get('library_metadata') or {}, actual.get('input_record_ids') or []])
            if calculated != sql['content_hash'] or rid != 'live-'+calculated[:24]:
                raise ValueError('Collection observation content hash mismatch')
            for key, contract_key in (('units', 'units'), ('provider', 'provider'),
                                      ('scope', 'scope'), ('sampling', 'frequency'), ('asset', 'asset')):
                if actual.get(key) != contract.get(contract_key):
                    raise ValueError('Collection observation differs from its dataset contract')
            expected_source = {'source_id': cap['capture_id'], 'name': cap['source_id'],
                'url': cap['url'], 'sha256': cap['sha256'], 'retrieved_at': cap['retrieved_at']}
            if actual.get('source') != expected_source or sql['known_at'] != cap['retrieved_at']:
                raise ValueError('Collection observation source/capture metadata mismatch')
            _time_scope(actual, cutoff, mode)
            actual['raw_path'] = str(root/cap['object_path'])
            if actual.get('library_recipe_object'):
                sha = actual['library_recipe_object']
                recipe = root/'objects'/sha[:2]/(sha+'.bin')
                if not recipe.resolve().is_relative_to(objects_root) or hashlib.sha256(recipe.read_bytes()).hexdigest() != sha:
                    raise ValueError('Collection recipe object integrity mismatch')
                actual['recipe_path'] = str(recipe)
            actuals.append(actual)
        # Detect filesystem changes during the remainder of this verification;
        # SQL read snapshots cannot make external object files transactional.
        for (path, _), signature in verified_objects.items():
            if _stat(path) != signature:
                raise ValueError('Collection raw object changed during batch validation')
    return {'records': actuals, 'validation': {'method': 'readonly-primary-observation-snapshot-v1',
        'record_count': len(actuals), 'unique_captures': len(captures),
        'raw_objects_verified': len(verified_objects), 'read_only_connections': 1,
        'read_only_snapshot': True, 'sql_statements': queries, 'chunk_size': chunk_size,
        'elapsed_seconds': perf_counter()-started,
        'scope': 'Exact requested IDs; source/normalized/capture hashes and quarantine checked per invocation. No latest-version substitution or persistent validation cache.'}}


def validate_collection_sources(library, saved_rows, *, dataset_id, start, end, cutoff,
                                mode='observed', chunk_size=500):
    """Verify saved fields exactly against source rows and declared query scope."""
    saved = list(saved_rows)
    packet = read_collection_sources(library, [row['record_id'] for row in saved],
        cutoff=cutoff, mode=mode, chunk_size=chunk_size)
    lower, upper = timestamp(start), timestamp(end)
    if lower >= upper:
        raise ValueError('Collection query window must be increasing')
    for expected, actual in zip(saved, packet['records'], strict=True):
        if any(actual.get(key) != value for key, value in expected.items()):
            raise ValueError('Collection row differs from the original source: '+expected['record_id'])
        if actual['dataset_id'] != dataset_id or not lower <= timestamp(actual['observed_at']) < upper:
            raise ValueError('Collection observation is outside its declared dataset/window')
        if actual.get('period_end') and timestamp(actual['period_end']) > upper:
            raise ValueError('Collection observation interval extends past the query window')
    packet['validation']['saved_rows_compared'] = len(saved)
    return packet
