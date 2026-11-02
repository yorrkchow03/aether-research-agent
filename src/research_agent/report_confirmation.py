"""Freeze and execute the ten held-out report questions without retuning.

Freezing hashes source files, dataset bytes and private configuration bytes.
Private values are never parsed or logged. Only run_protocol explicitly invokes
the existing authorized business runner; this module creates no clients. A run
cannot silently replace failed/uncertain slots.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from threading import RLock, Event
import time

from research_data.http import utcnow
from .live import atomic
from .report_flow_study import run_attempt
from .report_study_dataset import SCHEMA, PAYLOAD_FIELDS, canonical
from .report_study_runtime import ARMS, RECOVERED_POLICY
from .runtime import freeze_software

SCHEMA_VERSION = 'aether-report-confirmation-v2'
TERMINAL_STATES = frozenset({'completed', 'failed', 'cancelled'})
_SOFTWARE_LOCK = RLock()


class ProtocolChanged(ValueError):pass
class UncertainAttempt(ValueError):pass


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _value_sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def _private_configuration_sha(root):
    path=Path(root)/'.env'
    # Only the digest leaves this function. The protocol remains local and
    # neither credential values nor non-secret private settings are logged.
    return _sha(path) if path.is_file() else None


def _dataset_metadata(directory):
    """Verify the entire frozen envelope; retain only input/split metadata.

    Reference labels are neither evaluated nor passed to the runner. The object
    hook discards the expected field; the model-facing inputs are a separate
    preexisting file with no labels or split.
    """
    directory=Path(directory).resolve();manifest_path=directory/'MANIFEST.json'
    digest=_sha(manifest_path)
    if digest!=(directory/'MANIFEST.sha256').read_text().strip():raise ProtocolChanged('Dataset manifest hash changed')
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('schema')!=SCHEMA:raise ValueError('Unknown frozen dataset schema')
    for name,expected in manifest['files'].items():
        if Path(name).name!=name or _sha(directory/name)!=expected:raise ProtocolChanged('Frozen dataset file changed: '+str(name))
    if not {'cases.json','model_inputs.json'}<=set(manifest['files']):raise ValueError('Missing fixed input metadata')
    def discard_reference(pairs):return {key:value for key,value in pairs if key!='expected'}
    cases=json.loads((directory/'cases.json').read_text(),object_pairs_hook=discard_reference)
    inputs=json.loads((directory/'model_inputs.json').read_text())
    if len(cases)!=manifest['case_count'] or len(cases)!=len(inputs):raise ValueError('Dataset input counts differ')
    by_id={case['case_id']:case for case in inputs}
    if len(by_id)!=len(inputs) or len({case['case_id'] for case in cases})!=len(cases):raise ValueError('Duplicate case identity')
    result=[]
    for case in cases:
        cid=case['case_id']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,99}',cid):raise ValueError('Unsafe case identity')
        payload={key:case[key] for key in PAYLOAD_FIELDS}
        if payload!=by_id.get(cid):raise ValueError('Model input and case metadata differ')
        if case['split']=='confirmation':
            result.append({'case_id':cid,'source_ids':payload['source_ids'],
                'input_sha256':_value_sha(payload)})
    if len(result)!=10:raise ValueError('This confirmation protocol requires exactly ten frozen confirmation questions')
    return digest,sorted(result,key=lambda row:row['case_id'])


def freeze_protocol(root,dataset_dir,output,workers=3):
    """Create a new immutable protocol; never overwrite an earlier study."""
    if isinstance(workers,bool) or not isinstance(workers,int) or not 1<=workers<=3:
        raise ValueError('Use one to three workers')
    root,dataset_dir,output=Path(root).resolve(),Path(dataset_dir).resolve(),Path(output).resolve()
    if output.exists():raise ValueError('Confirmation output already exists; use run to resume the frozen protocol')
    if not output.is_relative_to(root):raise ValueError('Keep the isolated study under the project root')
    dataset_sha,cases=_dataset_metadata(dataset_dir);private_sha=_private_configuration_sha(root)
    output.mkdir(parents=True)
    software,software_sha=freeze_software(root,output/'frozen-software')
    policy=RECOVERED_POLICY.public();slots=[]
    author_review_http=(policy['author_limit']+policy['review_limit'])*(1+policy.get('http_max_retries',0))
    for index,case in enumerate(cases):
        offset=index%len(ARMS);order=ARMS[offset:]+ARMS[:offset]
        for arm in order:
            slots.append({'slot_id':case['case_id']+':'+arm,'case_id':case['case_id'],'arm':arm,
                'launch_index':len(slots),'source_ids':case['source_ids'],'input_sha256':case['input_sha256'],
                'attempt_path':'attempts/'+case['case_id']+'/'+arm+'/attempt-01'})
    protocol={'schema':SCHEMA_VERSION,'frozen_at':utcnow(),'dataset_dir':str(dataset_dir),
        'dataset_manifest_sha256':dataset_sha,'root':str(root),
        'software_fingerprint':software_sha,'software_manifest':str((software/'source_manifest.json').relative_to(output)),
        'private_configuration_sha256':private_sha,
        'study_policy':policy,'study_policy_sha256':_value_sha(policy),'workers':workers,
        'question_count':10,'primary_slots':30,'arms':list(ARMS),'slots':slots,
        'cache_policy':'Cold per-case/arm stores; no warmup, seed receipts, or provider cache discount. Remote caching is unobserved.',
        'run_order':'Sorted fixed case IDs; rotate arm launch order by case index. Global worker cap applies across cases.',
        'publication_rule':'First actually saved, source-reviewed and audit-verified publication is primary. All versions/calls remain retained.',
        'request_bounds':{'author_review_nominal_http':author_review_http,
            'optional_pro_checks_http':12,'selective_jev_primary_http':12,'selective_pro_http':16,
            'arm_nominal_physical_http':{'original_off':author_review_http,'pro_checks':author_review_http+12,'jev_routed':author_review_http+28},
            'scope':'Configured author/review retries plus optional single-attempt services. Compaction shares author transport; all actual HTTP and any host cap are metered, not assumed free.'},
        'retry_policy':'No automatic replacement/retry. Terminal existing results are reused; incomplete/uncertain attempts require explicit attention.',
        'failure_policy':'All thirty slots remain in the denominator, including errors, timeout and missing reports. No provider purchase or model/endpoint substitution.',
        'resource_policy':'Include all initial requests, author/check/review/compaction costs, source preparation and elapsed time. USD and AFP are separate estimates; missing usage is unknown.',
        'latency_limits':'Concurrency, shared-provider contention and remote caching prevent a causal speed claim from this one run.',
        'human_labels':0,'new_automations':0}
    atomic(output/'PROTOCOL.json',protocol)
    (output/'PROTOCOL.sha256').write_text(_sha(output/'PROTOCOL.json')+'\n')
    atomic(output/'STATE.json',{'schema':SCHEMA_VERSION,'state':'frozen_not_started','protocol_sha256':_sha(output/'PROTOCOL.json'),
        'slots':{slot['slot_id']:{'phase':'pending','attempted':False} for slot in slots},'runs':[],
        'provider_calls_submitted_by_freeze':0,'updated_at':utcnow()})
    return protocol


def _load_protocol(root,output):
    output=Path(output).resolve();root=Path(root).resolve();path=output/'PROTOCOL.json'
    if _sha(path)!=(output/'PROTOCOL.sha256').read_text().strip():raise ProtocolChanged('Confirmation protocol changed')
    protocol=json.loads(path.read_text())
    if protocol.get('schema')!=SCHEMA_VERSION or protocol.get('root')!=str(root):raise ProtocolChanged('Wrong confirmation protocol/root')
    if protocol.get('study_policy_sha256')!=_value_sha(RECOVERED_POLICY.public()) or protocol.get('study_policy')!=RECOVERED_POLICY.public():
        raise ProtocolChanged('Recovered policy changed after confirmation freeze')
    digest,cases=_dataset_metadata(protocol['dataset_dir'])
    if digest!=protocol['dataset_manifest_sha256']:raise ProtocolChanged('Confirmation dataset changed')
    expected=[]
    for index,case in enumerate(cases):
        offset=index%len(ARMS)
        for arm in ARMS[offset:]+ARMS[:offset]:expected.append((case['case_id'],arm,case['input_sha256'],case['source_ids']))
    actual=[(s['case_id'],s['arm'],s['input_sha256'],s['source_ids']) for s in protocol['slots']]
    if actual!=expected or len(actual)!=30 or protocol.get('primary_slots')!=30:raise ProtocolChanged('Confirmation slot inventory changed')
    for slot in protocol['slots']:
        if slot['attempt_path']!='attempts/'+slot['case_id']+'/'+slot['arm']+'/attempt-01':raise ProtocolChanged('Attempt path changed')
    if not 1<=protocol['workers']<=3:raise ProtocolChanged('Worker bound changed')
    return protocol


def _check_software(root,output,protocol):
    # freeze_software atomically writes a shared manifest, so serialize the
    # guard itself while allowing the independent business jobs to run freely.
    with _SOFTWARE_LOCK:
        if _private_configuration_sha(root)!=protocol.get('private_configuration_sha256'):
            raise ProtocolChanged('Private configuration changed after confirmation freeze; no new slots may start')
        _,fingerprint=freeze_software(root,Path(output)/'software-check')
    if fingerprint!=protocol['software_fingerprint']:raise ProtocolChanged('Software changed after confirmation freeze; no new slots may start')


@contextmanager
def _run_lock(output):
    with (Path(output)/'RUN.lock').open('a+') as file:
        try:fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise UncertainAttempt('This frozen confirmation already has an active runner') from None
        file.seek(0);file.truncate();file.write(json.dumps({'pid':os.getpid(),'started_at':utcnow()}));file.flush()
        try:yield
        finally:fcntl.flock(file.fileno(),fcntl.LOCK_UN)


def _primary_publication(attempt,receipt,result):
    workspace=attempt/'runtime/sessions'/receipt['session_id']/'workspace';versions=[];primary=None
    for value in result.get('published_reports',[]):
        path=PurePosixPath(value)
        if len(path.parts)!=3 or path.parts[:2]!=('/','reports') or path.suffix!='.md':
            versions.append({'publication':value,'valid':False,'reason':'Invalid publication path'});continue
        md=workspace/'reports'/path.name
        item={'publication':value,'valid':False}
        try:
            if md.is_symlink() or not md.resolve().is_relative_to(workspace.resolve()):raise ValueError('Publication escapes workspace')
            body=json.loads(md.with_suffix('.json').read_text());audit=json.loads(md.with_suffix('.audit.json').read_text())
            if body.get('review',{}).get('status')!='reviewed' or not isinstance(body.get('validation'),dict):raise ValueError('Source-reviewed publication required')
            if audit.get('verification',{}).get('status')!='passed' or audit.get('report_hash')!=_sha(md):raise ValueError('Publication audit/hash mismatch')
            item.update(valid=True,structured_sha256=_sha(md.with_suffix('.json')),report_sha256=_sha(md))
            if primary is None:primary=value
        except (OSError,ValueError,KeyError,TypeError) as exc:item['reason']=type(exc).__name__
        versions.append(item)
    return primary,versions


def _existing_result(attempt,slot,protocol):
    if not (attempt/'RESULT.json').exists():
        if attempt.exists():raise UncertainAttempt('Existing attempt has no terminal RESULT; do not restart an uncertain job')
        return None
    if not (attempt/'ATTEMPT.json').exists():raise UncertainAttempt('Terminal result lacks its preparation receipt')
    receipt=json.loads((attempt/'ATTEMPT.json').read_text());result=json.loads((attempt/'RESULT.json').read_text())
    if result.get('state') not in TERMINAL_STATES:raise UncertainAttempt('Existing result is not terminal')
    for row in (receipt,result):
        if row.get('case_id')!=slot['case_id'] or row.get('arm')!=slot['arm']:raise ProtocolChanged('Attempt identity does not match its slot')
    if receipt.get('dataset_manifest_sha256')!=protocol['dataset_manifest_sha256'] or receipt.get('software_fingerprint')!=protocol['software_fingerprint'] or receipt.get('study_policy')!=protocol['study_policy']:
        raise ProtocolChanged('Attempt dataset/software/policy differs from frozen confirmation')
    if receipt.get('source_ids')!=slot['source_ids'] or not re.fullmatch(r'[a-f0-9]{32}',str(receipt.get('session_id',''))):
        raise ProtocolChanged('Attempt source inventory/session identity differs')
    return receipt,result


def _slot_summary(attempt,slot,receipt,result,reused):
    primary,versions=_primary_publication(attempt,receipt,result)
    return {'phase':'finished','attempted':True,'reused':reused,'state':result['state'],
        'completed':result['state']=='completed' and primary is not None,'primary_publication':primary,'versions':versions,
        'result_sha256':_sha(attempt/'RESULT.json'),'attempt_sha256':_sha(attempt/'ATTEMPT.json'),
        **{key:result.get(key) for key in ('wall_seconds','preparation_seconds','end_to_end_seconds',
            'pro_physical_requests_known','jev_physical_requests_known','pro_unknown_physical_counts','jev_unknown_physical_counts',
            'pro_unknown_usage_requests','jev_unknown_usage_requests','known_estimated_pro_afp','known_estimated_jev_usd','error')}}


def _run_slot(root,output,protocol,slot,stop=None):
    _check_software(root,output,protocol)
    if _value_sha(RECOVERED_POLICY.public())!=protocol['study_policy_sha256']:raise ProtocolChanged('Live study policy differs')
    attempt=Path(output)/slot['attempt_path']
    if not attempt.resolve().is_relative_to(Path(output).resolve()):raise ProtocolChanged('Attempt escaped the isolated output')
    existing=_existing_result(attempt,slot,protocol)
    if existing:return _slot_summary(attempt,slot,*existing,True)
    if stop is not None and stop.is_set():raise ProtocolChanged('Another slot detected protocol drift; no new business request')
    invocation=Path(output)/'invocations'/(slot['case_id']+'--'+slot['arm']+'.json')
    if invocation.exists():raise UncertainAttempt('An earlier runner invocation lacks a durable result; do not restart it')
    started=time.monotonic()
    atomic(invocation,{'slot_id':slot['slot_id'],'started_at':utcnow(),'protocol_sha256':_sha(Path(output)/'PROTOCOL.json'),
        'state':'business_runner_invoked','automatic_replacement':False})
    result=run_attempt(root,protocol['dataset_dir'],attempt,slot['case_id'],slot['arm'],policy=RECOVERED_POLICY)
    existing=_existing_result(attempt,slot,protocol)
    if existing is None:raise UncertainAttempt('Runner returned without a durable terminal result')
    summary=_slot_summary(attempt,slot,*existing,False);summary['orchestrator_slot_seconds']=time.monotonic()-started
    _check_software(root,output,protocol)
    return summary


def _total(rows,key):
    values=[];unknown=0
    for row in rows:
        value=row.get(key)
        provider='jev' if key=='known_estimated_jev_usd' else 'pro' if key=='known_estimated_pro_afp' else None
        if value is None and provider and row.get('phase')=='finished' and row.get(provider+'_physical_requests_known')==0 and row.get(provider+'_unknown_physical_counts')==0:
            # An explicitly observed absence of requests is distinct from a
            # submitted request with unreported usage or account debits.
            value=0
        try:number=Decimal(str(value))
        except (InvalidOperation,ValueError):unknown+=1;continue
        if not number.is_finite() or number<0:unknown+=1;continue
        values.append(number)
    return {'known_sum':str(sum(values,Decimal(0))) if values else None,'unknown_slots':unknown}


def run_protocol(root,output):
    """Resume terminal slots without reruns; attempt all independent fixed slots."""
    root,output=Path(root).resolve(),Path(output).resolve()
    if not output.is_relative_to(root):raise ValueError('Keep the isolated study under the project root')
    with _run_lock(output):
        protocol=_load_protocol(root,output);_check_software(root,output,protocol)
        state=json.loads((output/'STATE.json').read_text())
        if state.get('protocol_sha256')!=_sha(output/'PROTOCOL.json'):raise ProtocolChanged('Progress belongs to another protocol')
        if set(state.get('slots',{}))!={s['slot_id'] for s in protocol['slots']} or not isinstance(state.get('runs'),list):
            raise ProtocolChanged('Progress slot inventory differs from the frozen protocol')
        start=time.monotonic();run_index=len(state['runs']);state['runs'].append({'started_at':utcnow(),'pid':os.getpid()})
        state.update(state='running',updated_at=utcnow());lock=RLock();stop=Event()
        def save():state['updated_at']=utcnow();atomic(output/'STATE.json',state)
        def execute(slot):
            with lock:
                prior=state['slots'][slot['slot_id']]
                if stop.is_set():return dict(prior)
                if prior.get('phase')=='attention_required':return dict(prior,reused=True)
                if prior.get('phase')=='finished':
                    try:unchanged=prior.get('result_sha256')==_sha(output/slot['attempt_path']/'RESULT.json')
                    except OSError:unchanged=False
                    if not unchanged:
                        stop.set();raise ProtocolChanged('Previously retained terminal result changed')
                state['slots'][slot['slot_id']]={'phase':'running','attempted':prior.get('attempted',False),'started_at':utcnow()};save()
            try:return _run_slot(root,output,protocol,slot,stop)
            except ProtocolChanged:stop.set();raise
            except Exception as exc:
                # Do not retry exceptions: the provider/process state may be
                # uncertain even when no final receipt has been written.
                invocation=output/'invocations'/(slot['case_id']+'--'+slot['arm']+'.json')
                return {'phase':'attention_required','attempted':(output/slot['attempt_path']).exists() or invocation.exists(),
                    'state':'runner_error','error_type':type(exc).__name__,
                    'reason':str(exc) if isinstance(exc,UncertainAttempt) else 'No automatic restart; inspect the preserved attempt and live activity.',
                    'provider_execution_unknown':True,'primary_publication':None,'versions':[]}
        fatal=None
        with ThreadPoolExecutor(max_workers=protocol['workers']) as pool:
            futures={pool.submit(execute,slot):slot for slot in protocol['slots']}
            for future in as_completed(futures):
                slot=futures[future]
                if future.cancelled():continue
                try:result=future.result()
                except ProtocolChanged as exc:
                    fatal=type(exc).__name__
                    with lock:state['slots'][slot['slot_id']]={'phase':'attention_required','state':'protocol_changed','attempted':(output/slot['attempt_path']).exists()};save()
                    for pending in futures:pending.cancel()
                    continue
                with lock:
                    state['slots'][slot['slot_id']]=result;save()
                    print(json.dumps({'slot_id':slot['slot_id'],'phase':result['phase'],'state':result.get('state'),
                        'primary_publication':result.get('primary_publication'),'reused':result.get('reused',False)},ensure_ascii=False),flush=True)
        rows=list(state['slots'].values())
        summary={'schema':SCHEMA_VERSION,'protocol_sha256':_sha(output/'PROTOCOL.json'),'planned_slots':30,
            'attempted_slots':sum(bool(r.get('attempted')) for r in rows),
            'finished_slots':sum(r.get('phase')=='finished' for r in rows),
            'completed_tasks':sum(bool(r.get('completed')) for r in rows),
            'published_tasks':sum(r.get('primary_publication') is not None for r in rows),
            'attention_required_slots':sum(r.get('phase')=='attention_required' for r in rows),
            'all_slots_attempted':all(r.get('attempted') for r in rows),
            'fatal_error':fatal,'workers':protocol['workers'],'actual_cash_debit':None,'human_labels':0,
            'resource_totals':{key:_total(rows,key) for key in ('wall_seconds','preparation_seconds','end_to_end_seconds',
                'pro_physical_requests_known','jev_physical_requests_known','pro_unknown_physical_counts','jev_unknown_physical_counts',
                'pro_unknown_usage_requests','jev_unknown_usage_requests','known_estimated_pro_afp','known_estimated_jev_usd')},
            'scope':'Thirty fixed primary slots, failures retained. Known partial resources are not cash debit or independent accuracy. Parallel durations must not be summed as overall elapsed.'}
        state['runs'][run_index].update(finished_at=utcnow(),elapsed_seconds=time.monotonic()-start)
        known_elapsed=sum(r['elapsed_seconds'] for r in state['runs'] if r.get('elapsed_seconds') is not None)
        unknown_runs=sum(r.get('elapsed_seconds') is None for r in state['runs'])
        summary['known_orchestration_elapsed_seconds']=known_elapsed
        summary['unknown_orchestration_runs']=unknown_runs
        summary['orchestration_elapsed_seconds']=None if unknown_runs else known_elapsed
        state.update(state='protocol_changed' if fatal else 'finished_with_attention' if summary['attention_required_slots'] else 'finished',summary=summary)
        save();atomic(output/'SUMMARY.json',summary)
        if fatal:raise ProtocolChanged('Software/protocol changed; pending slots were not launched')
        return summary
