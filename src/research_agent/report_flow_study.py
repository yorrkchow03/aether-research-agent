"""Bounded, resumable whole-harness study. No provider calls during preparation.

Only the explicit run command invokes authorized business providers. Attempt
directories are immutable after completion; failures remain in the denominator.
"""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal
import hashlib,json,time
from pathlib import Path

from research_data.http import utcnow
from .report_study_dataset import load_dataset,case_payload,canonical,text_sha
from .report_study_runtime import ReportStudyRuntime,ARMS,StudyPolicy
from .library_router import open_library
from .settings import Settings,PLAN_BASE_URL
from .live import atomic
from .semantic_costs import estimate_cost,token_count


def task_prompt(case,policy=None):
    payload=case_payload(case)
    prompt=('Write and publish a concise English research report answering this frozen-source question. '
        'Use the genuine plan-first local harness. Start source discovery with recall_memory '
        '(kind=news, asset=ALL, query from research_context.query_terms); inspect relevant saved originals '
        'using read_evidence. The source index is /data/study/source_pack.json. '
        'External networking, portfolio records, numeric series and other sources are unavailable. '
        'All arms have exactly the same saved evidence set. Do not fill gaps from model recollection. '
        'Use 2-3 precise observations and, where useful, 1-2 qualified interpretations; state '
        'counterpoints, proposal-versus-execution status, date scope and legitimate unknowns. '
        'No investment order, invented financial benefit or unsupported forecast. '
        'Cite actual record IDs. Qualitative facts, dates and quoted budget amounts can be stated '
        'with metric/value/unit omitted; numeric metrics require exact matching source fields or '
        'a reproducible registered calculation. Keep the report compact enough for source review. '
        'Save through publish_report, fix reported issues, and finish the plan honestly. '
        'This is retrospective observed-source research, not proof the system knew it at an earlier date.\n'
        +json.dumps(payload,ensure_ascii=False))
    if policy is not None and policy.version!='report-study-v1':
        prompt+='\nExecution guidance: File tools use virtual /data paths, but terminal/Python must use ./data or os.environ["RESEARCH_WORKSPACE"]. Do not retry a failed absolute virtual path. For long text prefer read_evidence(record_id, query="the specific factual point") to get exact original slices and omission pointers; expand missing conditions or counterevidence with another focus or the full original. Do not reload whole files already read. After one successful publish_report, the host ends the task without an extra model closing paragraph; do not publish a second version merely to finish the plan. Complete actual research steps before the publication call. All mandatory source checks still apply.'
    return prompt


def prepare_attempt(root,dataset_dir,output,case_id,arm,*,policy=None):
    started=time.monotonic();policy=policy or StudyPolicy()
    root,output=Path(root).resolve(),Path(output).resolve()
    if arm not in ARMS:raise ValueError('Unknown study arm')
    data=load_dataset(dataset_dir)
    case=next((c for c in data['cases'] if c['case_id']==case_id),None)
    if not case:raise ValueError('Unknown case')
    if output.exists():raise ValueError('Attempt path already exists; inspect or create an explicit new attempt')
    settings=Settings.load(root)
    if settings.model_id!='doubao-seed-2.1-pro' or settings.base_url!=PLAN_BASE_URL or not settings.api_key:
        raise ValueError('The study requires configured Pro on the authorized Plan endpoint')
    library=open_library(root,settings.library_path)
    if not library:raise ValueError('Existing source library required')
    rows=[]
    for rid in case['source_ids']:
        row=library.evidence(rid);saved=data['sources'][rid]
        cleaned={k:v for k,v in row.items() if k not in ('raw_path','recipe_path')}
        if text_sha(canonical(cleaned))!=saved['original_record_sha256']:
            raise ValueError('Selected original differs from frozen source: '+rid)
        rows.append(row)
    # Construction freezes actual software and makes fresh task-local memory.
    runtime=ReportStudyRuntime(root,output/'runtime',arm=arm,source_ids=case['source_ids'],policy=policy)
    for row in rows:runtime.knowledge.register(row)
    run=output/'data_run';run.mkdir(parents=True)
    atomic(run/'manifest.json',{'cutoff':case['cutoff'],'datasets':[],'sources':[],
        'scope':'Frozen saved text only; no numeric series. Retrospective observed availability.'})
    session=runtime.create_session(title='Frozen report '+case_id,cutoff=case['cutoff'],mode='observed',
        run_dir=run,model_mode='ark_plan',research_mode='current',
        preferences='Use only the declared source set. Holdings, risk tolerance and investment horizon are unknown.')
    workspace=runtime.sessions_dir/session['session_id']/'workspace'
    source_pack={'case':case_payload(case),'sources':[
        {'record_id':r['record_id'],'title':r.get('title'),'url':r.get('source',{}).get('url'),
         'published_at':r.get('published_at'),'available_at':r.get('available_at')} for r in rows],
        'scope':'Index only. Retrieve exact saved originals; no reference labels are provided.'}
    atomic(workspace/'data/study/source_pack.json',source_pack)
    receipt={'case_id':case_id,'arm':arm,'source_ids':case['source_ids'],'prepared_at':utcnow(),
        'dataset_manifest_sha256':hashlib.sha256((Path(dataset_dir)/'MANIFEST.json').read_bytes()).hexdigest(),
        'prompt_sha256':text_sha(task_prompt(case,policy)),'software_fingerprint':runtime.software_fingerprint,
        'session_id':session['session_id'],'model':'doubao-seed-2.1-pro',
        'max_author_responses':policy.author_limit,'max_source_review_requests':policy.review_limit,'wall_limit_seconds':policy.wall_limit,
        'study_policy':policy.public(),'preparation_seconds':time.monotonic()-started,
        'runtime_configuration':{**runtime.settings.public(),'thinking':runtime.settings.thinking},
        'source_pack_sha256':text_sha(canonical(source_pack)),
        'cache_policy':'Fresh per-case/arm local stores; provider remote caching unobservable; no prewarming.',
        'status':'prepared','provider_calls':0}
    atomic(output/'ATTEMPT.json',receipt)
    return runtime,case,receipt


def summarize_attempt(output,runtime=None):
    output=Path(output);receipt=json.loads((output/'ATTEMPT.json').read_text())
    session=output/'runtime/sessions'/receipt['session_id']
    statuses=list((session/'jobs').glob('*/status.json'))
    status=json.loads(statuses[0].read_text()) if statuses else {'state':'prepared'}
    events=[]
    if statuses:
        ep=statuses[0].parent/'events.jsonl'
        if ep.exists():events=[json.loads(line) for line in ep.read_text().splitlines() if line.strip()]
    http_path=output/'runtime/http-requests.json'
    http=json.loads(http_path.read_text())['requests'] if http_path.exists() else []
    author=[e for e in events if e['kind']=='model_finished']
    pro_rows=[]
    for event in author:
        result={'provider':'doubao','usage':event.get('usage'),'physical_request_count':1,'billable_request_made':True}
        pro_rows.append({'stage':'author','usage':event.get('usage'),'billing':estimate_cost(result)})
    for event in events:
        if event['kind']=='context_summary_finished':
            usage=event.get('usage');result={'provider':'doubao','usage':usage,'physical_request_count':1,'billable_request_made':True}
            pro_rows.append({'stage':'context_summary','usage':usage,'billing':estimate_cost(result)})
    reviews=[]
    for path in (session/'workspace/reviews/attempts').glob('*.json'):
        row=json.loads(path.read_text());reviews.append(row)
    # Review diagnostics preserve raw usage; do not invent usage for missing attempts.
    for row in reviews:
        usage=row.get('usage')
        if usage:pro_rows.append({'stage':'mandatory_source_review','usage':usage,
            'billing':estimate_cost({'provider':'doubao','usage':usage,'physical_request_count':1,'billable_request_made':True})})
    judgments=[e for e in events if e['kind']=='evidence_judgment']
    judgment_billing=[];judgment_results=[]
    for event in judgments:
        if event.get('cache_hit'):continue
        routing=event.get('routing')
        if routing:
            for entry in routing.get('trace',[]):
                result=entry['result']
                if not result.get('cache_hit'):
                    judgment_results.append(result);judgment_billing.append(result.get('billing') or estimate_cost(result))
        else:
            judgment_results.append(event)
            bill=event.get('billing')
            if not bill:
                if event.get('billable_request_made') is None:
                    bill={'provider':event.get('provider'),'estimated_usd':None,'estimated_afp':None,
                        'complete_usage':False,'scope':'Submission status/usage unknown; never assume no bill.'}
                else:bill=estimate_cost(event)
            judgment_billing.append(bill)
    # Cached repeated semantic results have zero new requests, never charged twice.
    projudges=[b for b in judgment_billing if b.get('provider')=='doubao']
    jevjudges=[b for b in judgment_billing if b.get('provider')=='jev']
    pro_judge_requests=sum(r.get('physical_request_count') or 0 for r in judgment_results if r.get('provider')=='doubao')
    jev_judge_requests=sum(r.get('physical_request_count') or 0 for r in judgment_results if r.get('provider')=='jev')
    pro_usage=[r['usage'] for r in pro_rows]+[r.get('usage') for r in judgment_results if r.get('provider')=='doubao']
    jev_results=[r for r in judgment_results if r.get('provider')=='jev']
    pro_results=[r for r in judgment_results if r.get('provider')=='doubao']
    def missing_outcomes(rows,fields):
        return sum(r.get('billable_request_made') is not False and any(token_count(r.get('usage'),f) is None for f in fields) for r in rows)
    def known_tokens(usages,field):
        values=[token_count(u,field) for u in usages if token_count(u,field) is not None]
        return sum(values) if values else None
    physical_judgments=sum((r.get('physical_request_count') or 0) for r in judgment_results)
    def add_known(rows,key):
        known=[Decimal(r[key]) for r in rows if r.get(key) is not None]
        return str(sum(known,Decimal(0))) if known else None
    result={'case_id':receipt['case_id'],'arm':receipt['arm'],'state':status['state'],
        'error':status.get('error'),'published_reports':status.get('published_reports',[]),
        'http_author_requests':sum(r['stage']=='author' for r in http),
        'http_author_or_summary_requests':sum(r['stage']=='author' for r in http),
        'context_summary_responses':sum(e['kind']=='context_summary_finished' for e in events),
        'http_mandatory_review_requests':sum(r['stage']=='mandatory_source_review' for r in http),
        'judgment_physical_requests_known':physical_judgments,
        'pro_physical_requests_known':len(http)+pro_judge_requests,
        'jev_physical_requests_known':jev_judge_requests,
        'jev_unknown_physical_counts':sum(r.get('physical_request_count') is None for r in judgment_results if r.get('provider')=='jev'),
        'pro_unknown_physical_counts':sum(r.get('physical_request_count') is None for r in judgment_results if r.get('provider')=='doubao'),
        'pro_unknown_usage_requests':max(missing_outcomes(pro_results,('input_tokens','output_tokens')),len(http)+pro_judge_requests-sum(all(token_count(u,f) is not None for f in ('input_tokens','output_tokens')) for u in pro_usage),0),
        'known_pro_input_tokens':known_tokens(pro_usage,'input_tokens'),
        'known_pro_output_tokens':known_tokens(pro_usage,'output_tokens'),
        'known_jev_input_tokens':known_tokens([r.get('usage') for r in judgment_results if r.get('provider')=='jev'],'input_tokens'),
        'jev_unknown_usage_requests':max(missing_outcomes(jev_results,('input_tokens',)),jev_judge_requests-sum(token_count(r.get('usage'),'input_tokens') is not None for r in jev_results),0),
        'judgment_unknown_physical_counts':sum(r.get('physical_request_count') is None for r in judgment_results),
        'author_responses':len(author),'tool_calls':sum(e['kind']=='tool_started' for e in events),
        'tool_errors':sum(e['kind']=='tool_failed' for e in events),
        'terminal_command_failures':sum(e['kind']=='tool_finished' and e.get('name')=='execute' and e.get('exit_code') not in (None,0) for e in events),
        'plan_saved':any(e['kind']=='plan_saved' for e in events),
        'author_usage_responses':sum(e.get('usage') is not None for e in author),
        'known_estimated_pro_afp':add_known([r['billing'] for r in pro_rows]+projudges,'estimated_afp'),
        'known_estimated_jev_usd':add_known(jevjudges,'estimated_usd'),
        'pro_usage_rows':pro_rows,'judgment_billing':judgment_billing,
        'started_at':status.get('created_at'),'finished_at':status.get('finished_at'),
        'actual_cash_debit':None,'human_labels':0,
        'scope':'Whole-task diagnostic. Known partial usage is not a complete debit. AI review is separate from human truth.'}
    if status.get('created_at') and status.get('finished_at'):
        from research_data.normalize import timestamp
        result['wall_seconds']=(timestamp(status['finished_at'])-timestamp(status['created_at'])).total_seconds()
        result['preparation_seconds']=receipt.get('preparation_seconds')
        result['end_to_end_seconds']=result['wall_seconds']+receipt.get('preparation_seconds',0)
    if runtime is not None and runtime.judge is not None and hasattr(runtime.judge,'metrics'):
        result['routing_metrics']=runtime.judge.metrics()
    elif (output/'RESULT.json').exists():
        prior=json.loads((output/'RESULT.json').read_text())
        if prior.get('routing_metrics'):result['routing_metrics']=prior['routing_metrics']
    result['http_stage_scope']='Author client is shared by continuation compaction; author responses and summary responses are separate above. HTTP total includes both.'
    atomic(output/'RESULT.json',result)
    return result


def run_attempt(root,dataset_dir,output,case_id,arm,*,policy=None):
    policy=policy or StudyPolicy()
    runtime,case,receipt=prepare_attempt(root,dataset_dir,output,case_id,arm,policy=policy)
    output=Path(output);started=time.monotonic()
    status=runtime.start(receipt['session_id'],task_prompt(case,policy),deliverable='report')
    receipt.update(status='running',job_id=status['job_id']);atomic(output/'ATTEMPT.json',receipt)
    while True:
        state=runtime.snapshot(receipt['session_id'])
        if not state['active_job_id']:break
        if time.monotonic()-started>receipt['wall_limit_seconds']:
            runtime.stop(receipt['session_id'])
            # Finite HTTP timeout + cancellation; preserve any unfinished state.
            deadline=time.monotonic()+policy.http_timeout*(policy.http_max_retries+1)+15
            while runtime.snapshot(receipt['session_id'])['active_job_id'] and time.monotonic()<deadline:time.sleep(.5)
            if runtime.snapshot(receipt['session_id'])['active_job_id']:
                receipt.update(status='cancelling',error='Provider call still active after bounded cancellation grace; do not restart this attempt')
                atomic(output/'ATTEMPT.json',receipt)
                raise RuntimeError('Study cancellation is still active; results remain unsealed and this attempt cannot be restarted automatically')
            break
        time.sleep(.5)
    result=summarize_attempt(output,runtime)
    receipt.update(status=result['state'],finished_at=utcnow());atomic(output/'ATTEMPT.json',receipt)
    return result
