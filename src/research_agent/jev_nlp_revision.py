"""Inherit untouched development observations into a separately frozen study.

Only public frozen inputs, exact first-attempt/started receipts and matching
stored provider receipts are copied. This module does not import a provider,
load configuration, open a network client or copy a credential/cache directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile


MANIFEST = 'DEVELOPMENT_INHERITANCE.json'
MANIFEST_SHA = 'DEVELOPMENT_INHERITANCE.sha256.json'
REQUIRED = ('PROTOCOL.json', 'DATASET.json', 'DRAFT_CASES.json', 'REFERENCE_REVIEW.json')
TERMINAL = frozenset({'ok', 'abstained', 'error', 'unavailable', 'not_attempted'})


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def _frozen(directory):
    freeze_bytes = (directory/'FREEZE.json').read_bytes()
    freeze = json.loads(freeze_bytes)
    files = freeze.get('files')
    if not isinstance(files, dict) or not set(REQUIRED).issubset(files):
        raise ValueError('Study FREEZE must cover protocol, dataset and original references')
    bodies = {'FREEZE.json': freeze_bytes}
    for name, expected in files.items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory) or path == directory or not path.is_file():
            raise ValueError('Unsafe or missing frozen artifact: '+name)
        body = path.read_bytes()
        if _sha(body) != expected:
            raise ValueError('Frozen artifact changed: '+name)
        bodies[name] = body
    protocol = json.loads(bodies['PROTOCOL.json'])
    for name, expected in protocol.get('implementation', {}).items():
        snapshot = (directory/'implementation'/name).resolve()
        if not snapshot.is_relative_to(directory/'implementation') or not snapshot.is_file() or _sha(snapshot.read_bytes()) != expected:
            raise ValueError('Frozen implementation snapshot changed: '+name)
    return protocol, bodies


def _development(protocol):
    providers = set(protocol.get('models', {}))
    if providers != {'jev', 'doubao'}:
        raise ValueError('Expected the fixed Jev/Pro provider set')
    jobs = protocol.get('jobs', {})
    dev = {key: job for key, job in jobs.items() if job.get('split') == 'development'}
    if not dev:
        raise ValueError('A nonempty development plan is required')
    for key, job in dev.items():
        if job.get('job_id') != key or _sha(_canonical(job['payload'])) != key:
            raise ValueError('Development job identity differs from normalized payload')
    slots = [(slot['provider'], slot['job_id']) for slot in protocol['order'] if slot['split'] == 'development']
    expected = {(provider, key) for provider in providers for key in dev}
    if len(slots) != len(set(slots)) or set(slots) != expected:
        raise ValueError('Development provider slot set is incomplete or duplicated')
    return dev, expected


def _scientific_data(files):
    data = json.loads(files['DATASET.json'])
    if 'EFFECTIVE_CASES.json' in files:
        cases = json.loads(files['EFFECTIVE_CASES.json'])
        if not isinstance(cases, list):
            raise ValueError('Effective cases must be a separately frozen list')
        data['cases'] = cases
    return data


def _slot_files(directory, folder, eligible):
    found = {}
    path = directory/folder
    if not path.exists():
        return found
    for file in path.rglob('*'):
        if file.is_symlink():
            raise ValueError('Receipt symlinks are not eligible for inheritance')
        if not file.is_file():
            continue
        parts = file.relative_to(path).parts
        if len(parts) != 2 or file.suffix != '.json' or (parts[0], file.stem) not in eligible:
            raise ValueError('Parent has a non-development '+folder+' artifact')
        found[(parts[0], file.stem)] = file
    return found


def _empty(directory, name):
    path = directory/name
    if path.is_symlink() or (path.exists() and (not path.is_dir() or any(item.is_file() or item.is_symlink() for item in path.rglob('*')))):
        raise ValueError('Destination '+name+' must be empty')


def _provider_receipts(parent, first):
    exported, notes, databases = {}, [], {}
    for (provider, job), body in sorted(first.items()):
        result = json.loads(body)['result']
        cache_key = result.get('cache_key')
        database = parent/'cache'/provider/'judgments.sqlite'
        if not cache_key:
            notes.append({'provider': provider, 'job_id': job, 'reason': 'no_cache_key_in_original_result'})
            continue
        if not database.is_file() or database.is_symlink():
            notes.append({'provider': provider, 'job_id': job, 'reason': 'original_receipt_database_unavailable'})
            continue
        # Immutable read-only mode cannot create WAL/SHM artifacts in the parent.
        # Refuse an active WAL rather than silently ignoring uncheckpointed rows.
        wal = database.with_name(database.name+'-wal')
        if wal.exists() and wal.stat().st_size:
            raise ValueError('Parent cache has an active WAL; finish and close it before adoption')
        databases.setdefault(str(database.relative_to(parent)), _sha(database.read_bytes()))
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)) as connection:
            row = connection.execute('SELECT body FROM attempts WHERE cache_key=? ORDER BY created_at DESC,attempt_id DESC LIMIT 1', (cache_key,)).fetchone()
        if row is None:
            notes.append({'provider': provider, 'job_id': job, 'reason': 'no_matching_original_cache_key'})
            continue
        raw = row[0].encode('utf-8') if isinstance(row[0], str) else bytes(row[0])
        receipt = json.loads(raw)
        if receipt.get('result', {}).get('cache_key') != cache_key:
            raise ValueError('Stored provider receipt cache identity differs')
        exported['development-parent/receipts/'+provider+'/'+job+'.json'] = raw
    return exported, notes, databases


def adopt_development(parent, destination):
    """Copy exact development receipts after verifying both independent freezes.

    The parent may have completed its original calibration, but must have no
    test submissions/results. The child must be newly prepared and uncalibrated.
    The new implementation/context policy may differ; model/request contracts,
    source/reference data and all development payloads must remain identical.
    """
    parent, destination = Path(parent).resolve(), Path(destination).resolve()
    if parent == destination or parent.is_relative_to(destination) or destination.is_relative_to(parent):
        raise ValueError('Parent and destination must be separate study directories')
    previous, old_files = _frozen(parent)
    revised, new_files = _frozen(destination)
    for name in ('models', 'criterion', 'timeout_seconds', 'max_attempts'):
        if name not in previous or previous[name] != revised.get(name):
            raise ValueError('Provider request contract changed: '+name)
    if _scientific_data(old_files) != _scientific_data(new_files):
        raise ValueError('Frozen source/reference data changed: DATASET.json scientific view')
    for name in ('DRAFT_CASES.json', 'REFERENCE_REVIEW.json'):
        if old_files[name] != new_files[name]:
            raise ValueError('Frozen source/reference data changed: '+name)
    old_jobs, eligible = _development(previous)
    new_jobs, new_eligible = _development(revised)
    if eligible != new_eligible or set(old_jobs) != set(new_jobs) or any(
            _canonical(old_jobs[key]['payload']) != _canonical(new_jobs[key]['payload']) for key in old_jobs):
        raise ValueError('Development slots or normalized payloads changed')
    for name in ('first', 'started'):
        _empty(destination, name)
    if (destination/'development-parent').exists() or (destination/MANIFEST).exists() or (destination/MANIFEST_SHA).exists():
        raise ValueError('Destination already has development inheritance artifacts')
    if list(destination.glob('CALIBRATION*')):
        raise ValueError('Destination must not already be calibrated')
    original_first = _slot_files(parent, 'first', eligible)
    if set(original_first) != eligible:
        raise ValueError('Every planned parent development slot must be terminal')
    markers = _slot_files(parent, 'started', eligible)
    first, copies = {}, {}
    for slot, path in sorted(original_first.items()):
        provider, key = slot
        body = path.read_bytes(); row = json.loads(body)
        if (row.get('job_id') != key or row.get('provider') != provider or row.get('payload_sha256') != key
                or row.get('result', {}).get('status') not in TERMINAL
                or row.get('result', {}).get('provider', provider) != provider):
            raise ValueError('Parent first receipt has a nonterminal or mismatched identity')
        first[slot] = body
        copies['first/'+provider+'/'+key+'.json'] = body
    for (provider, key), path in sorted(markers.items()):
        body = path.read_bytes()
        if json.loads(body).get('payload_sha256') != key:
            raise ValueError('Parent started marker payload identity differs')
        copies['started/'+provider+'/'+key+'.json'] = body
    raw_receipts, missing, databases = _provider_receipts(parent, first)
    copies.update(raw_receipts)
    for name, body in old_files.items():
        copies['development-parent/'+name] = body
    metadata = {
        'schema': 'jev-nlp-development-inheritance-v1',
        'parent_relative_path': os.path.relpath(parent, destination),
        'parent_protocol_sha256': _sha(old_files['PROTOCOL.json']),
        'parent_freeze_sha256': _sha(old_files['FREEZE.json']),
        'destination_protocol_sha256': _sha(new_files['PROTOCOL.json']),
        'destination_freeze_sha256': _sha(new_files['FREEZE.json']),
        'provider_contract': {name: previous[name] for name in ('models', 'criterion', 'timeout_seconds', 'max_attempts')},
        'development_job_ids': sorted(old_jobs),
        'copied_first_slots': len(first), 'copied_started_markers': len(markers),
        'exported_raw_receipts': len(raw_receipts), 'raw_receipts_unavailable': missing,
        'parent_receipt_databases_sha256': databases,
        'copied_files_sha256': {name: _sha(body) for name, body in sorted(copies.items())},
        'provider_requests': 0,
        'accounting': 'Inherited paid usage remains verbatim in each original first result; do not add exported raw receipts as additional calls.',
        'scope': 'Development-only policy revision before test; original parent frozen artifacts and observations remain unchanged.',
    }
    # Complete every validation/read before exposing any inherited child row.
    with tempfile.TemporaryDirectory(prefix='.development-adoption-', dir=destination) as temporary:
        stage = Path(temporary)
        for name, body in copies.items():
            path = stage/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
        manifest_body = _json_bytes(metadata)
        (stage/MANIFEST).write_bytes(manifest_body)
        (stage/MANIFEST_SHA).write_bytes(_json_bytes({'sha256': _sha(manifest_body)}))
        # Guard against parent edits or an unfinished parent writer during read.
        for name, body in old_files.items():
            if (parent/name).read_bytes() != body:
                raise ValueError('Parent frozen artifact changed during adoption')
        for (provider, key), body in first.items():
            if original_first[(provider, key)].read_bytes() != body:
                raise ValueError('Parent first receipt changed during adoption')
        for (provider, key), path in markers.items():
            if path.read_bytes() != copies['started/'+provider+'/'+key+'.json']:
                raise ValueError('Parent started marker changed during adoption')
        for name, body in new_files.items():
            if (destination/name).read_bytes() != body:
                raise ValueError('Destination frozen artifact changed during adoption')
        for relative, expected in databases.items():
            if _sha((parent/relative).read_bytes()) != expected:
                raise ValueError('Parent provider receipt database changed during adoption')
        for name in ('first', 'started'):
            _empty(destination, name)
            path = destination/name
            if path.exists():
                for directory in sorted((item for item in path.rglob('*') if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
                    directory.rmdir()
                path.rmdir()
        for name in ('first', 'started', 'development-parent', MANIFEST, MANIFEST_SHA):
            path = stage/name
            if path.exists():
                path.rename(destination/name)
    return metadata
