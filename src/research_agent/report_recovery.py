"""Explicit, single-slot post-confirmation recovery. Never runs automatically.

Recovery retains the original primary outcome and files. Its exact executable
snapshot, source lineage and separate resource receipt accompany each run.
"""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime,timezone
import hashlib,json,os,re,sqlite3,time
from pathlib import Path
from urllib.parse import quote

from research_data.http import utcnow
from research_agent.live import atomic
from research_agent.report_confirmation import _check_software
from research_agent.report_flow_study import prepare_attempt,summarize_attempt
from research_agent.report_study_dataset import case_payload,canonical
from research_agent.report_study_runtime import RECOVERED_POLICY
from research_agent.reports import ReportDraft
from research_agent.review import review_targets

ROOT=Path(__file__).resolve().parents[2]
TERMINAL={'completed','failed','cancelled'}
RID=re.compile(r'(?:page|web|live|context|calc)-[a-f0-9]{12,64}')
SCOPE=('Derived prior model work/internal review only, never source evidence or a new market fact. '
       'Inspect original source IDs and exact calculation fields before using any assertion.')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def primary_hashes(primary):
    files={}
    for path in sorted(primary.rglob('*')):
        if path.is_symlink():raise ValueError('Primary artifact symlink cannot be included in recovery')
        if path.is_file() and '__pycache__' not in path.parts:files[str(path.relative_to(primary))]=sha(path)
    return files

def gate(root,protocol_dir,slot_id,output):
    root,protocol_dir,output=Path(root).resolve(),Path(protocol_dir).resolve(),Path(output).resolve()
    if not protocol_dir.is_relative_to(root):raise ValueError('Protocol must be inside the project')
    p=protocol_dir/'PROTOCOL.json'
    if sha(p)!=(protocol_dir/'PROTOCOL.sha256').read_text().strip():raise ValueError('Protocol hash changed')
    protocol=json.loads(p.read_text())
    slots=protocol.get('slots',[])
    if len(slots)!=30 or protocol.get('primary_slots')!=30:raise ValueError('Require the complete thirty-slot primary protocol')
    state=json.loads((protocol_dir/'STATE.json').read_text())
    if state.get('state')!='finished':raise ValueError('Primary controller is not finished; recovery is forbidden')
    if state.get('protocol_sha256')!=sha(p):raise ValueError('Primary state/protocol identity differs')
    selected=None
    for slot in slots:
        if not re.fullmatch(r'[a-z0-9-]+',slot['case_id']) or slot['arm'] not in ('original_off','pro_checks','jev_routed'):
            raise ValueError('Unsafe primary slot identity')
        expected='attempts/'+slot['case_id']+'/'+slot['arm']+'/attempt-01'
        if slot.get('attempt_path')!=expected:raise ValueError('Unexpected primary attempt path')
        primary=protocol_dir/expected
        result=json.loads((primary/'RESULT.json').read_text())
        receipt=json.loads((primary/'ATTEMPT.json').read_text())
        if result.get('state') not in TERMINAL:raise ValueError('All thirty primary results must be terminal')
        for row in (result,receipt):
            if row.get('case_id')!=slot['case_id'] or row.get('arm')!=slot['arm']:raise ValueError('Primary identity mismatch')
        if receipt.get('software_fingerprint')!=protocol.get('software_fingerprint') or receipt.get('dataset_manifest_sha256')!=protocol.get('dataset_manifest_sha256'):
            raise ValueError('Primary software/source receipt differs from its protocol')
        if slot['slot_id']==slot_id:selected=(slot,primary,receipt,result)
    if selected is None:raise ValueError('Unknown primary slot')
    slot,primary,receipt,result=selected
    if result['state'] not in ('failed','cancelled'):raise ValueError('Only a failed/cancelled primary may have one recovery')
    if receipt.get('source_ids')!=slot['source_ids'] or not re.fullmatch(r'[a-f0-9]{32}',str(receipt.get('session_id',''))):raise ValueError('Invalid primary source/session identity')
    expected_output=protocol_dir/'recovery'/slot['case_id']/slot['arm']/'attempt-01'
    if output!=expected_output.resolve():raise ValueError('Recovery output must be its unique independent recovery slot')
    if output.exists():raise ValueError('Recovery already exists; never repeat or overwrite it')
    _check_software(root,protocol_dir,protocol)
    return protocol,selected

def _safe_old_file(path,primary):
    path=Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(primary.resolve()) or not path.is_file():
        raise ValueError('Prior file is outside the primary attempt')
    if path.stat().st_size>2_000_000:raise ValueError('Prior artifact exceeds the bounded copy size')
    return path

def _referenced_ids(value):
    result=set()
    if isinstance(value,dict):
        for key,item in value.items():
            if key.endswith('evidence_ids') and isinstance(item,list):result.update(r for r in item if isinstance(r,str) and RID.fullmatch(r))
            result.update(_referenced_ids(item))
    elif isinstance(value,list):
        for item in value:result.update(_referenced_ids(item))
    return result

def _digest_value(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

def _clock(value):
    if not isinstance(value,str):return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError,OverflowError):return None

def _target_signature(target):
    if not isinstance(target,dict) or not isinstance(target.get('claim_id'),str):raise ValueError('Missing actual target identity')
    value=dict(target)
    if not isinstance(value.get('evidence_ids'),list):raise ValueError('Missing actual target source IDs')
    # Review may append its old user-context ID to summary/judgment targets.
    # It cannot turn into a new user's budget or an external-source fact.
    value['evidence_ids']=[rid for rid in value['evidence_ids'] if not str(rid).startswith('context-')]
    return _digest_value(value)

def _draft_metadata(value,file):
    raw=value.get('draft') if isinstance(value,dict) else None
    created=value.get('created_at') if isinstance(value,dict) else None
    stamp=_clock(created);mtime=file['primary_mtime_unix']
    result={'path':file['path'],'sha256':file['sha256'],'primary_file_sha256':file['primary_file_sha256'],
        'created_at':created,'primary_mtime_utc':file['primary_mtime_utc'],
        'selection_basis':'created_at' if stamp is not None else 'primary_file_mtime',
        'draft_content_sha256':_digest_value(raw) if isinstance(raw,dict) else None,
        'draft_shape':'unresolved','target_signatures':None,'normalized_draft_sha256':None,
        '_sort_time':stamp if stamp is not None else mtime}
    try:
        parsed=ReportDraft.model_validate(raw)
        targets=review_targets(parsed)
        result.update(draft_shape='typed',target_signatures={t['claim_id']:_target_signature(t) for t in targets},
            normalized_draft_sha256=_digest_value(parsed.model_dump()))
    except (ValueError,TypeError,KeyError):pass
    return result

def _public_draft(draft):
    return {key:value for key,value in draft.items() if key not in ('_sort_time','target_signatures','normalized_draft_sha256')}

def _bind_targets(targets,drafts):
    if not isinstance(targets,list) or any(not isinstance(t,dict) for t in targets):raise ValueError('No actual target array')
    ids=[t.get('claim_id') for t in targets]
    if len(set(ids))!=len(ids):raise ValueError('Ambiguous duplicate target claim IDs')
    signatures={t['claim_id']:_target_signature(t) for t in targets}
    matches=sorted({d['draft_content_sha256'] for d in drafts if d['target_signatures']==signatures})
    return matches,dict(zip(ids,targets))

def _feedback(artifacts,drafts,errors):
    """Extract only exact negative feedback, never promote AI approval to facts."""
    latest=max(drafts,key=lambda d:(d['_sort_time'],d['primary_file_sha256'])) if drafts else None
    latest_hash=latest.get('draft_content_sha256') if latest else None
    issues={};unresolved=[];positive=0;negative=0
    def add(issue,occurrence):
        key=_digest_value(issue)
        if key not in issues:issues[key]={**issue,'issue_id':'feedback-'+key[:24],'occurrences':[]}
        issues[key]['occurrences'].append(occurrence)
    for artifact in artifacts:
        if artifact['kind']!='internal_pro_review':continue
        value=artifact.get('_value');pointer={key:artifact[key] for key in ('path','sha256','primary_file_sha256')}
        try:
            if not isinstance(value,dict):raise ValueError('Diagnostic is not JSON')
            candidates,targets=_bind_targets(value.get('targets'),drafts)
            response=value.get('response_content')
            response=json.loads(response) if isinstance(response,str) else response
            if not isinstance(response,dict) or not isinstance(response.get('verdicts'),list):raise ValueError('Provider response has no verdict array')
            verdicts=response['verdicts'];verdict_ids=[v.get('claim_id') for v in verdicts if isinstance(v,dict)]
            if len(verdict_ids)!=len(verdicts) or len(set(verdict_ids))!=len(verdict_ids):raise ValueError('Ambiguous provider claim IDs')
            if set(verdict_ids)!=set(targets):raise ValueError('Provider verdict/actual target inventory differs')
            binding=candidates[0] if len(candidates)==1 else None
            if binding is None:unresolved.append({**pointer,'reason':'draft_binding_unresolved','candidate_draft_content_sha256':candidates})
            for verdict in verdicts:
                status=verdict.get('status')
                if status in ('supported','reasonable_inference','conditional'):
                    positive+=1;continue
                if status not in ('insufficient','contradicted'):
                    unresolved.append({**pointer,'reason':'unknown_verdict_status','claim_id':verdict.get('claim_id')});continue
                target=targets[verdict['claim_id']]
                if not isinstance(verdict.get('reason'),str) or not isinstance(verdict.get('correction',''),str):
                    unresolved.append({**pointer,'reason':'unparsed_negative_verdict','claim_id':verdict['claim_id']});continue
                negative+=1
                source_ids=[rid for rid in target['evidence_ids'] if isinstance(rid,str) and RID.fullmatch(rid) and not rid.startswith('context-')]
                quote_refs=verdict.get('quotes',[])
                aliases=value.get('source_aliases',{})
                used_aliases={q.get('source_id') for q in quote_refs if isinstance(q,dict)} if isinstance(quote_refs,list) else set()
                issue={'kind':'internal_pro_negative_verdict','status':status,'claim_id':verdict['claim_id'],
                    'actual_target':target,'target_signature_sha256':_target_signature(target),
                    'statement_sha256':_digest_value(target.get('statement')),
                    'draft_content_sha256':binding,'candidate_draft_content_sha256':candidates,
                    'applies_to_latest_draft':binding==latest_hash if binding is not None and latest_hash is not None else None,
                    'source_ids':source_ids,'reason':verdict['reason'],'correction':verdict.get('correction',''),
                    'quotes_as_returned':quote_refs,'quote_source_aliases':{key:aliases[key] for key in used_aliases if key in aliases},
                    'provider_response_validated':value.get('status') in ('reviewed','needs_revision'),
                    'scope':SCOPE}
                add(issue,{**pointer,'review_id':value.get('review_id'),'attempt':value.get('attempt'),
                    'reviewed_at':value.get('reviewed_at')})
        except (ValueError,TypeError,KeyError) as exc:
            unresolved.append({**pointer,'reason':'unparsed_or_unbound_diagnostic','error_type':type(exc).__name__})
    for event in errors:
        content={key:event.get(key) for key in ('kind','name','error','exit_code','output_preview')}
        sources=sorted({rid for rid in RID.findall(canonical(content)) if not rid.startswith('context-')})
        add({'kind':'tool_error','feedback':content,'draft_content_sha256':None,'applies_to_latest_draft':None,
             'source_ids':sources,'scope':SCOPE,
             'binding_notice':'No draft is guessed from chronology. Compare the actual error to the latest draft/source before applying.',
             'saved_preview_may_be_incomplete':True},event['_pointer'])
    ordered=sorted(issues.values(),key=lambda issue:(0 if issue.get('applies_to_latest_draft') else 1 if issue['kind']=='tool_error' else 2,issue['issue_id']))
    return {'scope':SCOPE,'latest_draft':_public_draft(latest) if latest else None,
        'issues':ordered,'unresolved_diagnostics':unresolved,'positive_verdicts_not_carried_forward':positive,
        'negative_verdicts_seen':negative,'distinct_issues':len(ordered),
        'deduplicated_occurrences':sum(len(issue['occurrences'])-1 for issue in ordered)}

def _write_redacted(path,value,redact):
    text=redact(json.dumps(value,ensure_ascii=False,indent=2))+'\n';path.write_text(text)
    return json.loads(text)

def _hot_feedback(full,cold_pointer,*,budget=12000):
    """Keep whole issues or explicit cold pointers, never trim a qualifier."""
    hot={'scope':SCOPE,'memory_layout':'recovery-hot-cold-v1','latest_draft':full['latest_draft'],
        'issues':[],'cold_issue_pointers':[],'unresolved_diagnostics_count':len(full['unresolved_diagnostics']),
        'positive_verdicts_not_carried_forward':full['positive_verdicts_not_carried_forward'],
        'cold_feedback':cold_pointer,'soft_character_budget':budget,
        'omission_notice':'Only complete issues appear inline. Omitted/unresolved feedback must be read from the cold pointer before applying it; absence is not approval.'}
    omitted=[]
    for i,issue in enumerate(full['issues']):
        candidate={**hot,'issues':[*hot['issues'],issue]}
        if len(json.dumps(candidate,ensure_ascii=False,indent=2))<=budget:hot['issues'].append(issue)
        else:omitted.append({'issue_id':issue['issue_id'],'kind':issue['kind'],'status':issue.get('status'),
            'source_ids':issue['source_ids'],'applies_to_latest_draft':issue.get('applies_to_latest_draft'),
            'cold_json_key':'issues['+str(i)+']','reason':'complete_issue_exceeds_hot_budget'})
    # A large catalog cannot itself become the new overfull context. The cold
    # registry always retains every omitted issue/source pointer and its text.
    for pointer in omitted:
        candidate={**hot,'cold_issue_pointers':[*hot['cold_issue_pointers'],pointer]}
        if len(json.dumps(candidate,ensure_ascii=False,indent=2))<=budget:hot['cold_issue_pointers'].append(pointer)
    hot.update(inline_issue_count=len(hot['issues']),omitted_issue_count=len(omitted),
        omitted_pointer_count=len(omitted)-len(hot['cold_issue_pointers']),
        complete_feedback_inline=not omitted and not full['unresolved_diagnostics'])
    return hot

def copy_derived(primary,receipt,workspace,redact,*,hot_budget=12000):
    old=primary/'runtime/sessions'/receipt['session_id']/'workspace'
    destination=workspace/'memory/recovery';destination.mkdir(parents=True,exist_ok=True)
    cold=destination/'cold';cold.mkdir(exist_ok=True)
    copied=[];gaps=[];ids=set();total=0;drafts=[];by_content={}
    files=[*sorted((old/'drafts').glob('*.json')),*sorted((old/'reviews/attempts').glob('*.json'))]
    for number,path in enumerate(files):
        try:
            path=_safe_old_file(path,primary);body=path.read_bytes()
            kind='draft' if path.parent.name=='drafts' else 'internal_pro_review'
            digest=hashlib.sha256(body).hexdigest();key=(kind,digest)
            occurrence={'primary_file':str(path.relative_to(primary)),'primary_file_sha256':digest,
                'primary_mtime_unix':path.stat().st_mtime,
                'primary_mtime_utc':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()}
            if key in by_content:
                existing=by_content[key];existing['primary_occurrences'].append(occurrence)
                if occurrence['primary_mtime_unix']>existing['primary_mtime_unix']:
                    existing.update(primary_mtime_unix=occurrence['primary_mtime_unix'],primary_mtime_utc=occurrence['primary_mtime_utc'])
                continue
            total+=len(body)
            if total>16_000_000:raise ValueError('Recovery memory copy budget reached')
            try:value=json.loads(body);parse_error=None
            except (ValueError,UnicodeError) as exc:value=None;parse_error=type(exc).__name__
            ids.update(_referenced_ids(value))
            name='prior-'+str(number).zfill(3)+'-'+digest[:16]+'.json'
            # Preserve exact JSON spelling/precision in cold storage, except
            # explicit credential redaction. Provenance/scope live in MANIFEST.
            (cold/name).write_text(redact(body.decode(errors='replace')))
            item={'path':'/memory/recovery/cold/'+name,'kind':kind,'sha256':sha(cold/name),
                'primary_file_sha256':digest,'primary_mtime_unix':occurrence['primary_mtime_unix'],
                'primary_mtime_utc':occurrence['primary_mtime_utc'],'primary_occurrences':[occurrence],
                'parse_error':parse_error,'_value':value}
            copied.append(item);by_content[key]=item
        except (OSError,ValueError,TypeError) as exc:gaps.append({'file':path.name,'reason':type(exc).__name__})
    errors=[]
    for path in sorted((old.parent/'jobs').glob('*/events.jsonl')):
        try:
            path=_safe_old_file(path,primary)
            for line_number,line in enumerate(path.read_text().splitlines(),1):
                event=json.loads(line)
                if event.get('kind') in ('tool_failed','review_invalid') or event.get('kind')=='tool_finished' and event.get('exit_code') not in (None,0):
                    errors.append({**{key:event.get(key) for key in ('time','kind','name','error','exit_code','output_preview')},
                        '_pointer':{'primary_file':str(path.relative_to(primary)),'primary_file_sha256':sha(path),'line':line_number,'time':event.get('time')}})
        except (OSError,ValueError,TypeError) as exc:gaps.append({'file':path.name,'reason':type(exc).__name__})
    _write_redacted(cold/'TOOL_FEEDBACK.json',{'scope':SCOPE,'events':errors},redact)
    drafts=[_draft_metadata(item.get('_value'),item) for item in copied if item['kind']=='draft']
    full=_feedback(copied,drafts,errors)
    full=_write_redacted(cold/'ALL_FEEDBACK.json',full,redact)
    pointer={'path':'/memory/recovery/cold/ALL_FEEDBACK.json','sha256':sha(cold/'ALL_FEEDBACK.json')}
    hot=_hot_feedback(full,pointer,budget=hot_budget)
    hot['cold_copy_gap_count']=len(gaps)
    if gaps:hot['complete_feedback_inline']=False
    _write_redacted(destination/'ACTIONABLE_FEEDBACK.json',hot,redact)
    manifest={'scope':SCOPE,'files':[{key:value for key,value in item.items() if key!='_value'} for item in copied],
        'drafts':[_public_draft(d) for d in sorted(drafts,key=lambda d:d['_sort_time'],reverse=True)],
        'tool_feedback':{'path':'/memory/recovery/cold/TOOL_FEEDBACK.json','sha256':sha(cold/'TOOL_FEEDBACK.json')},
        'all_feedback':pointer,'copy_gaps':gaps}
    _write_redacted(cold/'MANIFEST.json',manifest,redact)
    index={'scope':SCOPE,'memory_layout':'recovery-hot-cold-v1','latest_draft':full['latest_draft'],
        'actionable_feedback':{'path':'/memory/recovery/ACTIONABLE_FEEDBACK.json','sha256':sha(destination/'ACTIONABLE_FEEDBACK.json')},
        'cold_manifest':{'path':'/memory/recovery/cold/MANIFEST.json','sha256':sha(cold/'MANIFEST.json')},
        'cold_file_count':len(copied),'copy_gap_count':len(gaps),
        'retrieval_instructions':'Read ACTIONABLE_FEEDBACK then the latest draft and current original evidence. Open exact cold feedback only when an unresolved/omitted issue is material; cold AI text is never market evidence.',
        'complete_feedback_inline':hot['complete_feedback_inline']}
    index=json.loads(redact(json.dumps(index,ensure_ascii=False)))
    return index,ids

def migrate_calculations(primary,source_ids,record_ids,new_knowledge,workspace,redact):
    """Read prior SQLite immutably; only transfer source-bound live.calculation."""
    path=primary/'runtime/knowledge/knowledge.sqlite';imported=[];gaps=[];checked={}
    wanted=sorted(r for r in record_ids if r not in source_ids)
    if not wanted:return {'imported':[],'gaps':[],'scope':'No prior calculation citation requires transfer'}
    if not path.is_file() or Path(str(path)+'-wal').exists() and Path(str(path)+'-wal').stat().st_size:
        return {'imported':[],'gaps':[{'record_id':r,'reason':'Prior database unavailable or not checkpointed; recompute from frozen originals'} for r in wanted]}
    connection=sqlite3.connect('file:'+quote(str(path.resolve()))+'?mode=ro&immutable=1',uri=True)
    try:
        def read(rid,stack=()):
            if rid in stack or len(stack)>32:raise ValueError('Calculation lineage cycle/depth')
            if rid in checked:return checked[rid]
            saved=connection.execute('SELECT body,digest FROM evidence WHERE record_id=?',(rid,)).fetchone()
            if not saved or hashlib.sha256(saved[0].encode()).hexdigest()!=saved[1]:raise ValueError('Missing or invalid prior evidence digest')
            row=json.loads(saved[0])
            if row.get('record_id')!=rid:raise ValueError('Prior evidence identity mismatch')
            if rid in source_ids:
                current=new_knowledge.get(rid)
                clean=lambda value:{k:v for k,v in value.items() if k not in ('raw_path','recipe_path')}
                if canonical(clean(row))!=canonical(clean(current)):raise ValueError('Leaf differs from frozen original')
            else:
                if row.get('dataset_id')!='live.calculation' or not rid.startswith('live-') or not row.get('input_record_ids'):
                    raise ValueError('Only source-bound live.calculation can transfer; recompute other records')
                for parent in row['input_record_ids']:
                    if not isinstance(parent,str) or not RID.fullmatch(parent):raise ValueError('Invalid calculation parent')
                    read(parent,(*stack,rid))
                recipe=_safe_old_file(row.get('recipe_path',''),primary);body=recipe.read_bytes()
                if hashlib.sha256(body).hexdigest()!=row.get('recipe_sha256'):raise ValueError('Prior recipe hash mismatch')
                if redact(body.decode())!=body.decode():raise ValueError('Credential-bearing recipe cannot be copied')
            if row.get('raw_path'):
                raw=_safe_old_file(row['raw_path'],primary)
                if sha(raw)!=row.get('source',{}).get('sha256'):raise ValueError('Prior raw hash mismatch')
            if rid not in source_ids:
                new_knowledge.register(row)
                copied=new_knowledge.get(rid)
                code=Path(copied['recipe_path']).read_bytes();target=workspace/'notes/recovery'/f'{rid}.py'
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(code)
                imported.append({'record_id':rid,'recipe':'/notes/recovery/'+target.name,'recipe_sha256':row['recipe_sha256'],
                    'primary_record_sha256':saved[1],'input_record_ids':row['input_record_ids'],
                    'scope':'Prior derived calculation with checked lineage/code hashes; arithmetic/method still need review.'})
            checked[rid]=row;return row
        for rid in wanted:
            try:read(rid)
            except (OSError,ValueError,KeyError,sqlite3.Error,UnicodeError) as exc:gaps.append({'record_id':rid,'reason':type(exc).__name__,'action':'Recompute using current saved originals; do not trust the prior value'})
    finally:connection.close()
    return {'imported':imported,'gaps':gaps,'scope':'No old checkpoints/data/live/query caches copied; only verified code/record lineage transfers.'}

def recover(root,protocol_dir,slot_id,output):
    root,protocol_dir,output=Path(root).resolve(),Path(protocol_dir).resolve(),Path(output).resolve()
    protocol,(slot,primary,primary_receipt,primary_result)=gate(root,protocol_dir,slot_id,output)
    marker=protocol_dir/'recovery/reservations'/(slot['case_id']+'--'+slot['arm']+'.json');marker.parent.mkdir(parents=True,exist_ok=True)
    with marker.open('x') as file:json.dump({'slot_id':slot_id,'reserved_at':utcnow(),'automatic_replacement':False},file)
    before=primary_hashes(primary);policy=replace(RECOVERED_POLICY,version='report-recovery-v2',author_limit=12)
    started=time.monotonic();runtime=None
    receipt={'kind':'single_bounded_post_primary_recovery','slot_id':slot_id,'created_at':utcnow(),
        'primary_state':primary_result['state'],'primary_attempt_sha256':sha(primary/'ATTEMPT.json'),
        'primary_result_sha256':sha(primary/'RESULT.json'),'protocol_sha256':sha(protocol_dir/'PROTOCOL.json'),
        'dataset_manifest_sha256':protocol['dataset_manifest_sha256'],'source_ids':slot['source_ids'],
        'policy':policy.public(),'host_http_cap':(policy.author_limit+policy.review_limit)*(1+policy.http_max_retries),
        'primary_replaced':False,'read_reference_labels':False,'classification_cache_seeded':False}
    try:
        runtime,case,attempt=prepare_attempt(root,protocol['dataset_dir'],output,slot['case_id'],slot['arm'],policy=policy)
        if runtime.software_fingerprint!=protocol['software_fingerprint']:raise ValueError('Recovery software differs from primary freeze')
        workspace=runtime.sessions_dir/attempt['session_id']/'workspace'
        index,ids=copy_derived(primary,primary_receipt,workspace,runtime.settings.redact)
        migration=migrate_calculations(primary,set(slot['source_ids']),ids,runtime.knowledge,workspace,runtime.settings.redact)
        index['calculation_transfer']=migration;atomic(workspace/'memory/recovery/INDEX.json',index)
        source=Path(__file__).read_bytes();snapshot=output/'recovery-tool/recover_report.py';snapshot.parent.mkdir(parents=True);snapshot.write_bytes(source)
        receipt.update(tool_sha256=hashlib.sha256(source).hexdigest(),tool_snapshot='recovery-tool/recover_report.py',
            derived_memory=index,source_hashes={rid:runtime.knowledge.get(rid).get('source',{}).get('sha256') for rid in slot['source_ids']},
            preparation_seconds=time.monotonic()-started,status='prepared')
        atomic(output/'RECOVERY.json',receipt)
        prompt=('Recover this failed frozen-source research report using the same local harness. First save a fresh plan, then read '
            '/memory/recovery/INDEX.json and /memory/recovery/ACTIONABLE_FEEDBACK.json. Read the latest draft identified there '
            'and current original evidence. Open cold diagnostics only when a particular omitted/unresolved issue needs it; do not '
            'load every historical evidence_text or every earlier draft. These files are derived prior model '
            'work, not source evidence or verified market facts. Repair the specific review/validation failures; do not repeat a broad '
            'investigation or use prior prose as an original source. Use read_evidence to inspect the frozen source IDs and any transferred '
            'calculation. Numeric metric/value/unit must match actual calculation fields exactly; if unsupported, recompute/register '
            'with the current originals or use attributed source prose with metric/value/unit omitted. Keep scope, dates, uncertainty '
            'and contrary information. Untransferred records are gaps, not zero. No external capture or trade execution. Publish one '
            'concise corrected report via publish_report; mandatory source audit remains. Terminal paths are ./data or '
            'RESEARCH_WORKSPACE, while file tools use /data. The host completes after verified publication.\n'+json.dumps(case_payload(case),ensure_ascii=False))
        actual_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest()
        attempt.update(prompt_sha256=actual_prompt_sha256,recovery_of_primary_slot=slot_id)
        receipt['actual_prompt_sha256']=actual_prompt_sha256
        atomic(output/'ATTEMPT.json',attempt);atomic(output/'RECOVERY.json',receipt)
        status=runtime.start(attempt['session_id'],prompt,deliverable='report')
        attempt.update(status='running',job_id=status['job_id']);atomic(output/'ATTEMPT.json',attempt)
        request_start=time.monotonic()
        while runtime.snapshot(attempt['session_id'])['active_job_id']:
            if time.monotonic()-request_start>policy.wall_limit:
                runtime.stop(attempt['session_id']);deadline=time.monotonic()+policy.http_timeout*(policy.http_max_retries+1)+15
                while runtime.snapshot(attempt['session_id'])['active_job_id'] and time.monotonic()<deadline:time.sleep(.5)
                if runtime.snapshot(attempt['session_id'])['active_job_id']:raise RuntimeError('Recovery cancellation remains active; do not restart')
                break
            time.sleep(.5)
        result=summarize_attempt(output,runtime);attempt.update(status=result['state'],finished_at=utcnow());atomic(output/'ATTEMPT.json',attempt)
        receipt.update(status=result['state'],result_sha256=sha(output/'RESULT.json'),elapsed_seconds=time.monotonic()-started,
            result_summary={key:result.get(key) for key in ('state','error','published_reports','pro_physical_requests_known','jev_physical_requests_known','known_estimated_pro_afp','known_estimated_jev_usd')})
        return result
    except Exception as exc:
        receipt.update(status='failed_or_uncertain',error_type=type(exc).__name__,elapsed_seconds=time.monotonic()-started,
            automatic_retry_forbidden=True)
        raise
    finally:
        receipt['primary_unchanged']=before==primary_hashes(primary)
        if output.exists():atomic(output/'RECOVERY.json',receipt)
        atomic(marker,{'slot_id':slot_id,'status':receipt['status'],'recovery_receipt':str(output/'RECOVERY.json'),
            'primary_unchanged':receipt['primary_unchanged'],'automatic_replacement':False})
        if not receipt['primary_unchanged']:raise RuntimeError('Primary artifacts changed during recovery; investigate without replacing original outcome')
