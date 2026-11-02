"""Read-only library classification sidecar with a bounded resumable queue.

Default is inventory only. --run explicitly permits provider requests; labels
are saved separately and never alter original documents or publication gates.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from research_agent.evidence_judgments import EvidenceJudgmentService, sha
from research_agent.live import atomic
from research_data.http import utcnow
from research_data.library.store import ResearchLibrary

ROOT = Path(__file__).resolve().parents[2]


def prepare_sample(library_path, output, *, size=24):
    """Pin a small public-original sample without writing to its source library."""
    if not 1 <= size <= 24:
        raise ValueError('The fixed integration sample is bounded to1..24 documents')
    library = ResearchLibrary(library_path, create=False)
    path = Path(output).resolve(); path.mkdir(parents=True, exist_ok=True)
    target = path/'sample.json'
    if target.exists():
        raise ValueError('A fixed sample already exists; reuse it rather than resample')
    with library.connect(readonly=True) as connection:
        sources = [r[0] for r in connection.execute('SELECT DISTINCT source_id FROM documents ORDER BY source_id')]
    total = library.search_documents('', limit=1)['total']
    queues, excluded = {}, Counter()
    for source in sources:
        rows = library.search_documents('', source=source, limit=100)['results']
        queues[source] = []
        for row in rows:
            if row.get('source_kind') not in ('article','external_report','external_report_pdf','forum_post','official_release'):
                excluded['not_publisher_original'] += 1; continue
            queues[source].append(row)
    selected, seen = [], set()
    while len(selected) < size and any(queues.values()):
        for source in sources:
            while queues[source]:
                item = queues[source].pop(0)
                record = library.evidence(item['record_id'])  # verifies raw object hash
                dataset = str(record.get('dataset_id',''))
                if (record.get('private') or record.get('library_exclusion_reasons') or
                        record.get('record_id','').startswith('context-') or not dataset.startswith('library.documents.')):
                    excluded['private_or_excluded'] += 1; continue
                text = record.get('text','')
                if len(text.strip()) < 200:
                    excluded['short_body'] += 1; continue
                text_hash = hashlib.sha256(text.encode()).hexdigest()
                duplicate_hash = hashlib.sha256(re.sub(r'\s+',' ',text).strip().casefold().encode()).hexdigest()
                if duplicate_hash in seen:
                    excluded['duplicate_text'] += 1; continue
                seen.add(duplicate_hash)
                selected.append({'record_id':record['record_id'],'source':source,'url':record.get('url'),
                    'published_at':record.get('published_at'),'source_kind':record.get('source_kind'),
                    'source_sha256':record.get('source',{}).get('sha256'),'text_sha256':text_hash,
                    'normalized_text_sha256':duplicate_hash,'text_characters':len(text)})
                break
            if len(selected) >= size: break
    sample = {'schema':'jev-document-integration-sample-v1','created_at':utcnow(),
        'library':str(Path(library_path).resolve()),'library_original_documents_at_selection':total,
        'selection':'Round robin over sorted source names; latest100 eligible original versions/source; exact public full bodies >=200 characters; normalized-text deduplication. Integration sample, not representative accuracy benchmark.',
        'excluded_counts':dict(excluded),'selected':selected,'source_counts':dict(Counter(r['source'] for r in selected)),
        'human_accuracy':None,'human_labels':'pending','source_mutated':False}
    atomic(target,sample)
    return sample


def process(library_path, output, *, run=False, provider='jev', limit=20,
            criterion_version='document-index-v1', source=None, cache_only=False,
            sample_file=None, timeout_seconds=None, max_attempts=2):
    if not 1 <= limit <= 50:
        raise ValueError('Use an explicit bounded batch of1..50 new items')
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    library = ResearchLibrary(library_path, create=False)
    sample = json.loads(Path(sample_file).read_text()) if sample_file else None
    if sample and sample['library'] != str(Path(library_path).resolve()):
        raise ValueError('Fixed sample belongs to a different library')
    service = EvidenceJudgmentService(ROOT, output/'cache', provider, cache_only=cache_only,
                                     timeout_seconds=timeout_seconds,max_attempts=max_attempts) if run else None
    generation = {'provider': provider, 'requested_model': service.requested_model if service else None,
                  'criterion_version': criterion_version, 'library': str(Path(library_path).resolve()), 'source': source}
    if sample: generation['fixed_sample_sha256'] = sha(sample)
    state_file = output/'state.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {'created_at': utcnow(), 'completed': {}, 'pending': {}, 'generation': generation}
    if run and state['generation'] != generation:
        raise ValueError('Queue model/criteria/library changed; use a new sidecar output')
    processed = []; offset = 0; failures = 0; total = 0; access_stop = False
    while len(processed) < limit:
        packet = {'total':len(sample['selected']),'results':sample['selected'][offset:offset+100]} if sample else library.search_documents('', source=source, limit=100, offset=offset)
        total = packet['total']; rows = packet['results']
        if not rows:
            break
        for row in rows:
            rid = row['record_id']
            if rid in state['completed']:
                continue
            if not run:
                processed.append({'record_id': rid, 'source': row.get('source_id'), 'url': row.get('url')})
            else:
                record = library.evidence(rid)
                if sample and (hashlib.sha256(record.get('text','').encode()).hexdigest()!=row['text_sha256'] or
                        record.get('source',{}).get('sha256')!=row['source_sha256']):
                    raise ValueError('Pinned source content changed; no replacement version will be sent')
                result = service.assess('classification', evidence=[record], criterion_version=criterion_version)
                annotation = {'record_id': rid, 'classification': result, 'source_mutated': False,
                    'scope': 'Versioned derived index metadata; original evidence remains authoritative.'}
                atomic(output/'annotations'/(rid+'.json'), annotation)
                if result['status'] in ('ok','abstained'):
                    state['completed'][rid] = {'cache_key': result['cache_key'], 'model': result['model'],
                        'input_hash': result['input_hash'], 'status': result['status']}
                    state['pending'].pop(rid, None)
                else:
                    failures += 1
                    state['pending'][rid] = {'status': result['status'], 'error': result.get('error')}
                state.update(updated_at=utcnow(), generation=generation)
                atomic(state_file,state)
                processed.append({'record_id': rid, 'status': result['status'], 'cache_hit': result['cache_hit'],
                    'model':result.get('model'),'label':result.get('label'),'labels':result.get('labels',[]),
                    'usage':result.get('usage'),'latency_seconds':result.get('latency_seconds'),
                    'billable_request_made':result.get('billable_request_made'),
                    'error_type':result.get('error_type'),'http_status':result.get('http_status')})
                if result.get('http_status') in (401,402,403) or result['status']=='unavailable':
                    state['stop_reason']='provider_access_unavailable_no_further_requests'
                    atomic(state_file,state)
                    access_stop=True
            if len(processed) >= limit or failures >= 3 or access_stop:
                break
        if failures >= 3 or access_stop:
            break
        offset += len(rows)
    result = {'mode': 'explicit_provider_batch' if run else 'inventory_only', 'library_original_documents': sample['library_original_documents_at_selection'] if sample else total,
        'batch_items': processed, 'successful_or_abstained_cursor_items': len(state['completed']),
        'pending_failure_items': len(state['pending']), 'bounded_failures_this_batch': failures,
        'provider_requests_allowed': run and not cache_only, 'source_mutated': False,
        'fixed_sample_size':len(sample['selected']) if sample else None,
        'status_counts':dict(Counter(r.get('status','not_requested') for r in processed)),
        'provider_requests_this_batch':sum(bool(r.get('billable_request_made')) for r in processed),
        'usage_unknown_items':sum(r.get('usage') is None for r in processed) if run else None,
        'scope': 'Manual finite batch, no daemon or schedule. Failed judgments remain pending; rerun the same command to resume. Use a new output when changing model/criteria or intentionally reclassifying old versions.'}
    atomic(output/('last_batch.json' if run else 'inventory.json'), result)
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,default=ROOT/'data/datasets/aether-month-20260926-20261026-v4')
    p.add_argument('--output',type=Path,default=ROOT/'local/halfyear/jev/document-index')
    p.add_argument('--run',action='store_true');p.add_argument('--cache-only',action='store_true')
    p.add_argument('--provider',choices=['jev','doubao','lexical'],default='jev')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--source');p.add_argument('--criterion-version',default='document-index-v1')
    p.add_argument('--prepare-sample',action='store_true');p.add_argument('--sample-size',type=int,default=24)
    p.add_argument('--sample-file',type=Path);p.add_argument('--timeout-seconds',type=float);p.add_argument('--max-attempts',type=int,default=2)
    a=p.parse_args()
    if a.prepare_sample:
        if a.run: p.error('--prepare-sample is read-only inventory and cannot be combined with --run')
        r=prepare_sample(a.library,a.output,size=a.sample_size)
    else:r=process(a.library,a.output,run=a.run,provider=a.provider,limit=a.limit,source=a.source,criterion_version=a.criterion_version,cache_only=a.cache_only,sample_file=a.sample_file,timeout_seconds=a.timeout_seconds,max_attempts=a.max_attempts)
    print(json.dumps(r,indent=2))
