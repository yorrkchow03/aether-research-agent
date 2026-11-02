"""Manual, resumable derived indexing of an explicit saved library inventory.

No credentials, provider calls, schedules or writes to the source library. The
inventory preserves every eligible captured version, including superseded text.
Metadata-only items and exclusions remain an auditable separate denominator.
"""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import sqlite3

from research_data.http import utcnow
from research_data.library.store import instant
from .event_memory_index import _sha, _json


def library_inventory(library):
    items=[];skipped=[]
    with library.connect(readonly=True) as db:
        rows=db.execute('''SELECT v.record_id,v.document_id,v.body,d.source_id,d.kind
            FROM document_versions v JOIN documents d USING(document_id)
            WHERE v.published_at>=? AND v.published_at<? ORDER BY v.record_id''',
            (instant(library.window_start),instant(library.window_end))).fetchall()
        excluded={r[0] for r in db.execute("SELECT subject FROM relations WHERE predicate='excluded_from_research'")}
    for row in rows:
        body=json.loads(row['body']);rid=row['record_id'];reason=None
        if rid in excluded or row['document_id'] in excluded:reason='excluded_from_research'
        elif not rid.startswith('page-'):reason='not_original_page'
        elif body.get('source_kind') in ('search_excerpt','metadata_only'):reason='excerpt_or_metadata_only'
        elif not (body.get('text') or '').strip():reason='no_saved_body'
        elif body.get('quarantined') or body.get('exclusion_reasons') or 'quarantined' in (body.get('quality_flags') or []):reason='quarantined'
        item={'record_id':rid,'document_id':row['document_id'],'source_id':row['source_id'],
            'kind':row['kind'],'published_at':body.get('published_at'),
            'acquired_at':body.get('available_at') or body.get('retrieved_at'),
            'body_sha256':_sha(body)}
        (skipped if reason else items).append(item|({'reason':reason} if reason else {}))
    return {'schema':'memory-library-inventory-v1','library':str(library.root),
        'window':[library.window_start,library.window_end],'items':items,'skipped':skipped,
        'eligible_versions':len(items),'skipped_versions':len(skipped),
        'source_counts':dict(Counter(r['source_id'] for r in items)),
        'scope':'Captured original versions in selected publication window; not all news or historically known content.'}


def index_inventory(index,library,inventory,directory,*,max_items=10000,retry_errors=False,progress=None):
    if not 1<=max_items<=10000:raise ValueError('Use a bounded manual batch')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    manifest=directory/'INVENTORY.json';journal=directory/'ITEMS.jsonl'
    digest=_sha(inventory)
    if manifest.exists():
        if _sha(json.loads(manifest.read_text()))!=digest:raise ValueError('Saved inventory changed; use a new batch directory')
    else:manifest.write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
    completed={}
    if journal.exists():
        # Append-only valid lines plus a possibly interrupted final write.
        lines=journal.read_text().splitlines()
        for i,line in enumerate(lines):
            try:row=json.loads(line)
            except json.JSONDecodeError:
                if i!=len(lines)-1:raise ValueError('Corrupt index journal')
                with journal.open('rb+') as file:
                    file.truncate(sum(len((v+'\n').encode()) for v in lines[:i]))
                break
            if row.get('inventory_sha256')!=digest:raise ValueError('Index journal inventory mismatch')
            completed[row['record_id']]=row
        if journal.stat().st_size and not journal.read_bytes().endswith(b'\n'):
            with journal.open('ab') as file:file.write(b'\n')
    with index.connect() as db:
        committed={row['record_id'] for row in db.execute('SELECT record_id,body,digest FROM sources') if _sha(row['body'])==row['digest']}
    attempted=0
    for item in inventory['items']:
        rid=item['record_id'];old=completed.get(rid)
        if old and old['status']=='indexed' and rid in committed:continue
        if old and old['status']=='error' and not retry_errors:continue
        if attempted>=max_items:break
        attempted+=1
        try:
            original=library.evidence(rid)
            if _sha({k:v for k,v in original.items() if k not in ('raw_path','library_exclusion_reasons')})!=item['body_sha256']:
                # Some saved records already contain raw_path. Check the exact
                # catalog body instead; library.evidence above verified raw data.
                with library.connect(readonly=True) as db:
                    body=db.execute('SELECT body FROM document_versions WHERE record_id=?',(rid,)).fetchone()
                if not body or _sha(json.loads(body[0]))!=item['body_sha256']:raise ValueError('Inventory source changed')
            result=index.upsert_source(rid)
            row={'record_id':rid,'status':'indexed','duplicate':result['duplicate']}
        except (ValueError,KeyError,OSError,sqlite3.Error) as exc:
            row={'record_id':rid,'status':'error','error_type':type(exc).__name__,'detail':str(exc)[:180]}
        row.update(inventory_sha256=digest,at=utcnow())
        with journal.open('a') as file:file.write(_json(row)+'\n');file.flush()
        completed[rid]=row
        if progress and attempted%100==0:progress({'attempted':attempted,'recorded':len(completed),'total':len(inventory['items'])})
    summary={'schema':'memory-library-index-result-v1','inventory_sha256':digest,
        'eligible_versions':len(inventory['items']),'attempted_this_run':attempted,
        'indexed':sum(v['status']=='indexed' for v in completed.values()),
        'errors':[v for v in completed.values() if v['status']=='error'],
        'remaining':len(inventory['items'])-len(completed),'provider_calls':0,
        'completed_at':utcnow(),'scope':inventory['scope']}
    summary['status']='complete_selected_inventory' if not summary['remaining'] and not summary['errors'] else 'incomplete'
    (directory/'RESULT.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


def import_version_links(index,library,inventory):
    """Copy only already-declared content revisions, dated at actual import.

    Metadata revisions, similarities, topic proximity and repetitions never
    become correction links. Even content revisions do not prove a correction.
    """
    ids={row['record_id'] for row in inventory['items']};saved=[];gaps=[]
    with library.connect(readonly=True) as db:
        links=db.execute("SELECT subject,object FROM relations WHERE predicate='content_revision_of' ORDER BY subject,object").fetchall()
    for row in links:
        if row['subject'] not in ids or row['object'] not in ids:continue
        try:
            link=index.link_sources(row['subject'],row['object'],'content_revision_of',receipt_id=row['subject'])
            saved.append({k:link[k] for k in ('link_id','subject','object','known_at','duplicate')})
        except (ValueError,KeyError,OSError,sqlite3.Error) as exc:
            gaps.append({'subject':row['subject'],'object':row['object'],'reason':type(exc).__name__})
    return {'links':saved,'gaps':gaps,'scope':'Existing catalog content revisions, not authenticated corrections; link clocks begin at actual import.'}
