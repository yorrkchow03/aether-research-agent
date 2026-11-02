"""Portable, public-research-only snapshots; original libraries are never edited."""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import zipfile

from research_data.http import utcnow


SCHEMA='aether-shared-library-v1'
PRIVATE_TABLES=('output_citations','claim_citations','output_claims','output_fts',
                'research_outputs','legacy')
TOKEN_PATTERNS=(re.compile(rb'ark-[a-f0-9-]{25,}',re.I),
    re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|apikey_[0-9a-f]{24,}_[0-9a-f]{24,})\b'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'))


def digest_file(path, *, forbidden=(), references=None):
    digest=hashlib.sha256();tail=b''
    with Path(path).open('rb') as handle:
        while block:=handle.read(1024*1024):
            digest.update(block);scan=tail+block
            if any(value in scan for value in forbidden if value) or any(p.search(scan) for p in TOKEN_PATTERNS):
                raise ValueError('Credential/private literal found; contents withheld: '+Path(path).name)
            if references is not None:
                references.update(m.decode('ascii') for m in re.findall(rb'(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])',scan))
            tail=scan[-4096:]
    return digest.hexdigest()


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def readonly_database(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)


def build_shared_library(source, destination, *, forbidden=()):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError('Use a new destination outside the original snapshot')
    db=source/'research.sqlite'
    wal=source/'research.sqlite-wal'
    if wal.exists() and wal.stat().st_size:raise ValueError('Use a frozen/checkpointed source without pending WAL')
    source_hash=digest_file(db)
    parent=source/'DATASET_MANIFEST.json'
    if parent.exists():
        original=json.loads(parent.read_text())
        if original.get('files',{}).get('research.sqlite')!=source_hash:
            raise ValueError('Original frozen database differs from its manifest')
    destination.mkdir(parents=True)
    with closing(readonly_database(db)) as original:
        if original.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]:
            raise ValueError('Source snapshot still has active collection jobs')
        with closing(sqlite3.connect(destination/'research.sqlite')) as target:original.backup(target)
    changed={};preserved={};retained_hashes=set()
    with closing(sqlite3.connect(destination/'research.sqlite')) as target:
        target.execute('PRAGMA journal_mode=DELETE');target.execute('PRAGMA secure_delete=ON')
        core={r[0] for r in target.execute('SELECT record_id FROM observations UNION SELECT record_id FROM document_versions')}
        legacy={r[0] for r in target.execute('SELECT record_id FROM legacy')}
        outputs={r[0] for r in target.execute('SELECT output_id FROM research_outputs')}
        # No input dependency may be dropped just because it was imported earlier.
        for table in ('observations','document_versions'):
            for (body,) in target.execute('SELECT body FROM '+table):
                row=json.loads(body);refs=set(row.get('input_record_ids',[]))
                parent_id=row.get('library_metadata',{}).get('parent_page_evidence_id')
                if parent_id:refs.add(parent_id)
                if refs-core:raise ValueError('Core data requires a non-core dependency; review before sharing')
            preserved[table]=target.execute('SELECT count(*) FROM '+table).fetchone()[0]
        removed=(legacy-core)|outputs
        target.execute('CREATE TEMP TABLE private_ids(id TEXT PRIMARY KEY)')
        target.executemany('INSERT INTO private_ids VALUES(?)',[(rid,) for rid in sorted(removed)])
        target.execute('DELETE FROM relations WHERE subject IN private_ids OR object IN private_ids')
        for table in PRIVATE_TABLES:
            changed[table]=target.execute('SELECT count(*) FROM '+table).fetchone()[0]
            if table=='output_fts':
                target.execute('DROP TABLE output_fts')
                target.execute('CREATE VIRTUAL TABLE output_fts USING fts5(output_id UNINDEXED,text)')
            else:target.execute('DELETE FROM '+table)
        target.execute("DELETE FROM jobs WHERE job_id='legacy-import'")
        # Referenced source objects and implementation objects remain exact bytes.
        # Exclude the object catalog itself so orphaned personal inputs do not
        # retain themselves simply by appearing in that catalog.
        tables=[r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if not r[0].startswith(('sqlite_','document_fts_','output_fts_')) and r[0]!='objects']
        for table in tables:
            for row in target.execute('SELECT * FROM "'+table+'"'):
                for value in row:
                    if isinstance(value,str):retained_hashes.update(re.findall(r'(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])',value))
        objects=target.execute('SELECT sha256,bytes,path FROM objects').fetchall()
        catalog={row[0]:row for row in objects}
        # Calculation manifests can refer to other objects (for example the
        # implementation source) inside their raw JSON, rather than in SQLite.
        # Follow that dependency graph before removing unreferenced objects.
        needed=[];seen=set();pending=retained_hashes & catalog.keys()
        while pending:
            sha=pending.pop()
            if sha in seen:continue
            seen.add(sha);_,size,name=catalog[sha];path=source/name
            resolved=path.resolve()
            if not resolved.is_relative_to(source/'objects') or path.is_symlink() or not path.is_file():
                raise ValueError('Invalid original object path')
            refs=set()
            if path.stat().st_size!=size or digest_file(path,forbidden=forbidden,references=refs)!=sha:
                raise ValueError('Original raw hash/size mismatch')
            needed.append(catalog[sha]);pending.update((refs & catalog.keys())-seen)
        target.executemany('DELETE FROM objects WHERE sha256=?',[(row[0],) for row in objects if row[0] not in seen])
        target.commit();target.execute('VACUUM')
        if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Shared SQLite integrity failed')
    files={}
    for sha,size,name in needed:
        path=(source/name).resolve()
        if not path.is_relative_to(source/'objects') or path.is_symlink() or not path.is_file():
            raise ValueError('Invalid original object path')
        if path.stat().st_size!=size or digest_file(path,forbidden=forbidden)!=sha:
            raise ValueError('Original raw hash/size mismatch')
        dest=destination/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
        files[name]=sha
    files['research.sqlite']=digest_file(destination/'research.sqlite',forbidden=forbidden)
    from .store import ResearchLibrary
    from .delivery import delivery_metadata
    library=ResearchLibrary(destination,create=False)
    coverage=library.coverage();write_json(destination/'COVERAGE.json',coverage)
    for name,value in delivery_metadata(library).items():write_json(destination/name,value)
    privacy={'schema':SCHEMA,'removed_owner_rows':changed,'preserved_core_rows':preserved,
        'raw_objects_before':len(objects),'raw_objects_shared':len(needed),
        'original_database_sha256':source_hash,
        'original_snapshot_manifest_sha256':digest_file(parent) if parent.exists() else None,
        'transitive_object_dependencies_included':True,
        'source_record_ids_and_raw_bytes_unchanged':True,
        'scope':'Independent derivative: personal outputs/context/legacy records and their unreferenced objects excluded; original snapshot unchanged.'}
    write_json(destination/'PRIVACY_EXPORT.json',privacy)
    for path in destination.rglob('*'):
        if path.is_file() and path.name not in ('research.sqlite-wal','research.sqlite-shm'):
            files[str(path.relative_to(destination))]=digest_file(path,forbidden=forbidden)
    manifest={'schema':SCHEMA,'created_at':utcnow(),'window_start':library.window_start,'window_end':library.window_end,
        'core_complete':coverage['core_complete'],'files':files,
        'privacy':privacy,'dataset_contracts':len(coverage['datasets']),
        'scope':'Available captured research data and original publisher material with explicit gaps; no guarantee of publisher truth or historical system knowledge.'}
    write_json(destination/'SHARE_MANIFEST.json',manifest)
    return manifest


def archive_shared_library(source, archive):
    source=Path(source).resolve();archive=Path(archive).resolve()
    if archive.exists():raise ValueError('Archive already exists')
    manifest=json.loads((source/'SHARE_MANIFEST.json').read_text())
    archive.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as target:
        for name in sorted([*manifest['files'],'SHARE_MANIFEST.json']):
            target.write(source/name,name)
    return {'filename':archive.name,'bytes':archive.stat().st_size,'sha256':digest_file(archive),
        'manifest_sha256':digest_file(source/'SHARE_MANIFEST.json')}


def restore_shared_library(archive, destination, *, expected_sha256=None):
    archive=Path(archive).resolve();destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Destination exists; never overwrite a research library')
    if expected_sha256 and digest_file(archive)!=expected_sha256:raise ValueError('Archive checksum mismatch')
    with zipfile.ZipFile(archive) as source:
        entries=source.infolist();names=[entry.filename for entry in entries]
        if len(set(names))!=len(names):raise ValueError('Duplicate ZIP identities')
        manifest=json.loads(source.read('SHARE_MANIFEST.json'))
        if manifest.get('schema')!=SCHEMA:raise ValueError('Unsupported shared dataset')
        if set(names)!=set(manifest['files'])|{'SHARE_MANIFEST.json'}:raise ValueError('Unexpected archive files')
        if sum(e.file_size for e in entries)>10_000_000_000:raise ValueError('Archive exceeds 10 GB safety bound')
        for entry in entries:
            relative=PurePosixPath(entry.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename or stat.S_ISLNK(entry.external_attr>>16):
                raise ValueError('Unsafe archive path')
            if not (destination/entry.filename).resolve().is_relative_to(destination):raise ValueError('Unsafe archive target')
        destination.mkdir(parents=True)
        try:
            for entry in entries:
                path=destination/entry.filename;path.parent.mkdir(parents=True,exist_ok=True)
                with source.open(entry) as src,path.open('wb') as dst:shutil.copyfileobj(src,dst)
                if entry.filename in manifest['files'] and digest_file(path)!=manifest['files'][entry.filename]:
                    raise ValueError('Restored file hash mismatch: '+entry.filename)
            with closing(readonly_database(destination/'research.sqlite')) as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Restored SQLite integrity failed')
            return {'status':'passed','files_checked':len(manifest['files']),
                'window_start':manifest['window_start'],'window_end':manifest['window_end'],
                'core_complete':manifest['core_complete'],'scope':'File hashes and SQLite integrity; no network or model call.'}
        except Exception:
            shutil.rmtree(destination)
            raise
