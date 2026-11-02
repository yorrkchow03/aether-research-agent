"""Reuse actual captures/text versions without changing their acquisition time.

The high-volume execution-header sweep is deliberately excluded; copying a
monthly aggregate cannot establish a complete longer-window native series.
"""
import hashlib
import json
import shutil
from pathlib import Path

from .store import ResearchLibrary
from .time import timestamp


def reuse_captured_texts(source, target):
    source = source if isinstance(source, ResearchLibrary) else ResearchLibrary(source, create=False)
    if source.root == target.root:
        raise ValueError('Reuse source must be a different library')
    with source.connect(readonly=True) as c:
        captures = [dict(r) for r in c.execute('SELECT * FROM captures')
                    if r['source_id'] != 'ethereum_rpc' and not r['source_id'].startswith('calculation:ethereum-')]
        source_rows = list(c.execute('SELECT * FROM sources'))
        objects = {r['sha256']: dict(r) for r in c.execute('SELECT * FROM objects')}
        capture_ids = {r['capture_id'] for r in captures}
        versions = [dict(r) for r in c.execute('SELECT * FROM document_versions') if r['capture_id'] in capture_ids]
        version_ids = {r['record_id'] for r in versions}
        document_ids = {r['document_id'] for r in versions}
        documents = [tuple(r) for r in c.execute('SELECT * FROM documents') if r['document_id'] in document_ids]
        fts = [tuple(r) for r in c.execute('SELECT * FROM document_fts') if r['record_id'] in version_ids]
        discoveries = [dict(r) for r in c.execute('SELECT * FROM discoveries')]
        relations = [tuple(r) for r in c.execute('SELECT * FROM relations')
                     if r['subject'] in version_ids | document_ids and
                     (r['object'] in version_ids | document_ids or r['predicate'] == 'excluded_from_research')]
    used = {r['sha256'] for r in captures if r['sha256']}
    copied_bytes = 0
    for sha in sorted(used):
        metadata = objects[sha]
        origin = source.root / metadata['path']
        destination = target.root / metadata['path']
        if hashlib.sha256(origin.read_bytes()).hexdigest() != sha:
            raise ValueError('Reuse source object hash mismatch')
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                raise ValueError('Existing destination object differs')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
            copied_bytes += origin.stat().st_size
    with target.connect() as c:
        for row in source_rows:
            body = json.loads(row['body'])
            for key in ('window_start', 'window_end'):
                if key in body:
                    body[key] = getattr(target, key)
            c.execute('INSERT OR IGNORE INTO sources VALUES(?,?)', (row['source_id'], json.dumps(body, sort_keys=True)))
        c.executemany('INSERT OR IGNORE INTO objects VALUES(?,?,?)',
                      [(sha, objects[sha]['bytes'], objects[sha]['path']) for sha in used])
        c.executemany('INSERT OR IGNORE INTO captures VALUES(?,?,?,?,?,?,?,?)',
                      [tuple(r[k] for k in ('capture_id','source_id','url','retrieved_at','status','sha256','error','headers')) for r in captures])
        c.executemany('INSERT OR IGNORE INTO documents VALUES(?,?,?,?)', documents)
        for row in versions:
            existed = c.execute('SELECT 1 FROM document_versions WHERE record_id=?', (row['record_id'],)).fetchone()
            if not existed:
                c.execute('INSERT INTO document_versions VALUES(?,?,?,?,?,?,?)',
                          tuple(row[k] for k in ('record_id','document_id','content_hash','published_at','known_at','capture_id','body')))
                match = next((r for r in fts if r[0] == row['record_id']), None)
                if match:
                    c.execute('INSERT INTO document_fts VALUES(?,?,?)', match)
        c.executemany('INSERT OR IGNORE INTO relations VALUES(?,?,?,?)', relations)
    for row in discoveries:
        metadata = json.loads(row['body']);status=row['status'];reason=row['reason']
        if status == 'excluded' and reason and 'outside_window' in reason and row['published_at'] and target.start <= timestamp(row['published_at']) < target.end:
            status='discovered';reason=None
            metadata.update(reused_discovery_previous_state='outside_monthly_window', reused_from_window=[source.window_start,source.window_end])
        target.discover(row['source_id'],row['url'],published_at=row['published_at'],status=status,reason=reason,**metadata)
    result={'source_window':[source.window_start,source.window_end], 'target_window':[target.window_start,target.window_end],
            'captures_reused':len(captures),'document_versions_reused':len(versions),'raw_objects_reused':len(used),
            'bytes_copied':copied_bytes,'excluded_high_volume_source':'ethereum_rpc',
            'scope':'Independent copied originals and original acquisition timestamps. No monthly completion flags or native aggregates relabelled as half-year coverage.'}
    target.job('reuse:monthly-text-captures','collected',**result)
    return result
