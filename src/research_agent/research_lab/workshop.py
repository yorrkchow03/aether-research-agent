"""Optional strategy-code author/reviewer loop; sealed final evaluation follows selection.

The roles use Deep Agents, independently of the main analyst's tools/profile/memory.
Only development/validation summaries are supplied to the roles. Deterministic
execution, source/code receipts and costs are the evaluation, not model self-grades.
"""
from __future__ import annotations
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import time
from research_agent.live import atomic
from research_data.http import utcnow
from .corpus import load_corpus, digest, identity
from .evaluation import window_rows, paper_case
from .signals import signal_catalog
from .strategy_code import validate_source, execute_source

AUTHOR_SYSTEM='''You are a quantitative research strategy author. Produce actual Python source for ONE pure function strategy(bars, config) returning a finite spot long/cash target weight in [0, config["allocation_cap"]].
Only completed prefix bars (at most252) are passed: observed_at, period_end, open_usd, high_usd, low_usd, close_usd, volume_asset. Numeric values can be strings; use float. These are retrospective OHLCV, not news or point-in-time facts. Execution waits one full daily bar. No orders.
Allowed Python: local assignments, if/for, indexing/slicing, arithmetic/comparison and return; helpers len,sum,min,max,abs,float,int,mean,std,sqrt,log,range (max504). Only dict.get attributes. No imports, comprehensions, while, global/local introspection, recursion, I/O, classes, annotations, decorators, mutations or nested functions. Function signature exactly strategy(bars, config). Handle short history; maxlookback252. Use straightforward interpretable signals and avoid overfitting. Do not shadow helpers/bars/config. You cannot access final confirmation prices/outcomes. Do not request them or invent win rates or thresholds as verified facts.
Keep the first candidate simple: at most three interpretable signal types. Do not build lists or use list.append; use direct bars[i] indexing and local numeric accumulators. An eligible pattern is: if len(bars)<22 return0; latest=float(bars[-1]["close_usd"]); earlier=float(bars[-8]["close_usd"]); return config["allocation_cap"] if latest>earlier else0. Implement actual valid Python syntax and choose your own justified hypothesis rather than treating this example as evidence.
Use submit_candidate exactly once with code, rationale, declared signals, failure conditions and evaluation assumptions. Rationale must distinguish hypotheses from established facts. Review/backtest feedback is from development/validation only. Fix failures or simplify. End after successful submission.'''
REVIEW_SYSTEM='''You are an independent strategy-code reviewer, using only supplied source, rationale and development/validation scope. Inspect timing, units, unhandled inputs, long/cash cap, rationale, competing explanations and overfitting. Treat financial statements as hypotheses. Do not request final confirmation data. Do not execute trades. Call submit_review once with approved and concrete notes. Approval only means suitable for deterministic research testing, not profitable or production-safe. Never claim code was backtested by you. End after successful submission.'''


def role_call(settings, role, context, directory, cancel=None):
    """Distinct profile provider identity prevents changing the main analyst profile."""
    from deepagents import create_deep_agent, register_harness_profile, HarnessProfile, GeneralPurposeSubagentProfile
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemPermission
    from langchain_core.tools import tool
    from langchain_core.messages import HumanMessage, message_to_dict
    from research_agent.model import ArkPlanModel
    from research_agent.runtime import TraceMiddleware
    import httpx
    if settings.model_mode!='ark_plan' or settings.model_id!='doubao-seed-2.1-pro':raise ValueError('Workshop requires the configured Doubao Pro Agent Plan')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);submitted=[]
    class WorkshopModel(ArkPlanModel):
        def _get_ls_params(self,**kwargs):return {'ls_provider':'aether_workshop','ls_model_name':self.model_name,'ls_model_type':'chat'}
    register_harness_profile('aether_workshop:'+settings.model_id,HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),excluded_tools=frozenset({'task','execute','delete'})))
    @tool
    def submit_candidate(code:str,rationale:str,signals:list[str],failure_conditions:list[str],assumptions:list[str])->dict:
        """Submit one complete pure Python strategy and its explicit research contract."""
        if submitted:raise ValueError('Only one immutable submission per author turn')
        if not all(isinstance(v,str) and 0<len(v)<=4000 for v in (rationale,*signals,*failure_conditions,*assumptions)) or not 1<=len(signals)<=10 or not 1<=len(failure_conditions)<=12 or not 1<=len(assumptions)<=12:raise ValueError('Bounded explicit rationale/signals/failures/assumptions required')
        value={'code':code,'rationale':rationale,'signals':signals,'failure_conditions':failure_conditions,'assumptions':assumptions}
        attempts=directory/'submissions';attempts.mkdir(exist_ok=True)
        receipt=attempts/f'{len(list(attempts.glob("*.json")))+1:03d}.json'
        atomic(receipt,{'candidate':value,'state':'received'})
        try:check=validate_source(code)
        except (ValueError,SyntaxError) as exc:
            atomic(receipt,{'candidate':value,'state':'rejected','error':str(exc)});raise
        atomic(receipt,{'candidate':value,'state':'accepted','source_sha256':check['sha256']})
        submitted.append(value);return {'submitted':True,'source_sha256':check['sha256'],'next':'Submission retained; deterministic evaluation follows independent review'}
    @tool
    def submit_review(approved:bool,notes:list[str])->dict:
        """Submit research-code eligibility and concrete review notes, not a performance grade."""
        if submitted:raise ValueError('Only one immutable review per reviewer turn')
        if not 1<=len(notes)<=12 or any(not isinstance(n,str) or not 0<len(n)<=2000 for n in notes):raise ValueError('Concrete bounded review notes required')
        submitted.append({'approved':approved,'notes':notes});return {'submitted':True}
    events=[]
    def emit(kind,data):
        event=json.loads(settings.redact(json.dumps({'time':utcnow(),'kind':kind,**data},default=str)))
        events.append(event)
        with (directory/'events.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
    model=WorkshopModel(model=settings.model_id,api_key=settings.api_key,base_url=settings.base_url,extra_body={'thinking':{'type':settings.thinking}},
        max_tokens=4096,max_retries=1,streaming=True,timeout=120,http_client=httpx.Client(trust_env=False))
    atomic(directory/'context.json',context)
    try:
        graph=create_deep_agent(model,tools=[submit_candidate if role=='author' else submit_review],
            system_prompt=AUTHOR_SYSTEM if role=='author' else REVIEW_SYSTEM,backend=FilesystemBackend(root_dir=directory,virtual_mode=True),
            permissions=[FilesystemPermission(operations=['write'],paths=['/**'],mode='deny')],
            middleware=[TraceMiddleware(emit,cancel,replace(settings,max_model_calls=5))],name='strategy-'+role)
        state=graph.invoke({'messages':[HumanMessage(content=json.dumps(context,ensure_ascii=False))]},config={'recursion_limit':16,'max_concurrency':1})
        atomic(directory/'messages.json',json.loads(settings.redact(json.dumps([message_to_dict(m) for m in state['messages']],default=str))))
        if not submitted:raise ValueError('Role ended without a valid submission')
        return submitted[0]
    finally:
        model.http_client.close()


def evaluate_code(corpus,source,splits):
    config=corpus['protocol']['evaluation'];selected=[];payload=[]
    for asset,history in corpus['histories'].items():
        for window in corpus['protocol']['windows']:
            if window['split'] not in splits:continue
            _,rows=window_rows(history,corpus['protocol'],window)
            # Even the child is not given subsequent partition bars.
            selected.append((asset,history,window,rows));payload.append({'series':history['series'][:rows[-1]['feature_index']+1],'indices':[r['feature_index'] for r in rows]})
    execution=execute_source(source,payload,config);cases=[]
    for (asset,history,window,rows),values in zip(selected,execution['weights']):
        weights={history['series'][r['execute_index']-1]['period_end']:v for r,v in zip(rows,values)}
        paper=[paper_case(history,rows,weights,config,c,keep_curve=False) for c in config['cost_scenarios']]
        baselines={name:[paper_case(history,rows,{},config,c,strategy=name) for c in config['cost_scenarios']] for name in ('buy_hold','dca')}
        cases.append({'asset':asset,'window':window,'source_record_id':history['record_id'],'rows':len(rows),
            'decisions':[{'decision_at':r['decision_at'],'execute_at':r['execute_at'],'weight':v} for r,v in zip(rows,values)],'paper':paper,'baselines':baselines})
    validation=[c for c in cases if c['window']['split']=='validation']
    score=sum(math.log1p(next(p['net_return_fraction'] for p in c['paper'] if p['cost_id']=='base')) for c in validation) if validation else None
    return {'source_sha256':execution['identity']['sha256'],'cases':cases,'validation_objective':score,
        'common_rows':sum(c['rows'] for c in cases),'network_calls':0,'execution':execution['execution'],
        'scope':'Same windows/capital/costs across candidates; sum log growth is a selection statistic, not portfolio return. Fees/slippage and terminal exit included.'}


def author_context(corpus):
    summaries=[]
    for asset,h in corpus['histories'].items():
        for w in corpus['protocol']['windows']:
            if w['split']=='confirmation':continue
            _,rows=window_rows(h,corpus['protocol'],w)
            summaries.append({'asset':asset,'window':w,'target_rows':len(rows),'price_scope':'Completed single-venue daily OHLCV, captured retrospectively'})
    return {'catalog':signal_catalog(),'available_windows':summaries,'allocation_cap':corpus['protocol']['evaluation']['allocation_cap'],
        'cost_scenarios':corpus['protocol']['evaluation']['cost_scenarios'],'execution_delay_bars':corpus['protocol']['evaluation']['execution_delay_bars'],
        'selection':corpus['protocol']['workshop']['selection'],'confirmation':'Sealed until source selection is locked; unavailable to author and reviewer'}


def feedback(result):
    return {'validation_objective':result['validation_objective'],'cases':[{'asset':c['asset'],'window':c['window'],
        'rows':c['rows'],'paper':c['paper'],'baselines':c['baselines']} for c in result['cases']]}


def run_workshop(corpus_root,output,settings,*,rounds=2,cancel=None,call_role=role_call,emit=lambda *_:None):
    if rounds not in (1,2,3):raise ValueError('Workshop is bounded to1..3 rounds')
    corpus=load_corpus(corpus_root);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    lock={'corpus_manifest_sha256':corpus['manifest_sha256'],'rounds':rounds,'model':settings.model_id,'base_url':settings.base_url,
        'role_prompts_sha256':identity([AUTHOR_SYSTEM,REVIEW_SYSTEM]),'software':{p.name:digest(p) for p in (Path(__file__),Path(__file__).with_name('strategy_code.py'),Path(__file__).with_name('evaluation.py'))}}
    path=output/'input_lock.json'
    if path.exists() and json.loads(path.read_text())!=lock:raise ValueError('Workshop input/software changed; refuse resume')
    if not path.exists():atomic(path,lock)
    if (output/'result.json').exists():
        manifest=json.loads((output/'artifacts.json').read_text())
        if any(digest(output/name)!=sha for name,sha in manifest.items()):raise ValueError('Completed workshop artifact identity changed')
        return json.loads((output/'result.json').read_text())
    base=author_context(corpus);attempts=[];selection_path=output/'selection_lock.json'
    if not selection_path.exists():
        for i in range(rounds):
            if cancel and cancel.is_set():raise RuntimeError('Workshop stopped; completed role outputs retained for explicit resume')
            directory=output/f'round-{i+1}';directory.mkdir(exist_ok=True);summary_path=directory/'summary.json'
            if summary_path.exists():attempts.append(json.loads(summary_path.read_text()));continue
            attempt={'round':i+1,'eligible':False};emit('author',{'round':i+1})
            try:
                author_path=directory/'author.json'
                if author_path.exists():author=json.loads(author_path.read_text())
                else:
                    context={**base,'round':i+1,'earlier_attempts':attempts}
                    author=call_role(settings,'author',context,directory/'author',cancel);atomic(author_path,author)
                check=validate_source(author['code']);code=directory/'strategy.py'
                if code.exists() and digest(code)!=check['sha256']:raise ValueError('Saved strategy code identity changed')
                if not code.exists():code.write_text(author['code'])
                emit('reviewer',{'round':i+1});review_path=directory/'review.json'
                if review_path.exists():review=json.loads(review_path.read_text())
                else:
                    review=call_role(settings,'reviewer',{'candidate':author,'contract':base},directory/'reviewer',cancel);atomic(review_path,review)
                attempt.update(source_sha256=check['sha256'],review=review,rationale=author['rationale'])
                if not review['approved']:attempt['error']='Independent reviewer rejected research eligibility'
                else:
                    emit('backtest',{'round':i+1});result=evaluate_code(corpus,author['code'],{'development','validation'})
                    atomic(directory/'evaluation.json',result);attempt.update(eligible=True,evaluation=feedback(result))
            except Exception as exc:
                attempt['error']=settings.redact(str(exc))[:900]
                if cancel and cancel.is_set():raise RuntimeError('Workshop stopped; role artifacts retained') from exc
            atomic(summary_path,attempt);attempts.append(attempt);emit('round_completed',{'round':i+1,'eligible':attempt['eligible'],'error':attempt.get('error')})
        eligible=[a for a in attempts if a['eligible']]
        if not eligible:raise ValueError('No eligible strategy; all attempts and failures retained')
        chosen=max(eligible,key=lambda a:a['evaluation']['validation_objective'])
        selection={'round':chosen['round'],'source_sha256':chosen['source_sha256'],'locked_at':utcnow(),
            'corpus_manifest_sha256':corpus['manifest_sha256'],'selection':corpus['protocol']['workshop']['selection'],
            'validation_objective':chosen['evaluation']['validation_objective'],'input_lock_sha256':digest(path)}
        atomic(selection_path,selection)
    selection=json.loads(selection_path.read_text());source=(output/f'round-{selection["round"]}'/'strategy.py').read_text()
    if hashlib.sha256(source.encode()).hexdigest()!=selection['source_sha256'] or selection['corpus_manifest_sha256']!=corpus['manifest_sha256']:raise ValueError('Selected code/corpus identity changed')
    if cancel and cancel.is_set():raise RuntimeError('Stopped before sealed confirmation; selection retained')
    confirmation_path=output/'confirmation.json'
    if confirmation_path.exists():confirmation=json.loads(confirmation_path.read_text())
    else:
        emit('confirmation',{'selected_round':selection['round']});atomic(output/'confirmation_started.json',{'selection_lock_sha256':digest(selection_path),'started_at':utcnow(),'scope':'Resume may finish this same fixed evaluation; never start a new author after selection'})
        confirmation=evaluate_code(corpus,source,{'confirmation'});atomic(confirmation_path,confirmation)
    attempts=[json.loads(p.read_text()) for p in sorted(output.glob('round-*/summary.json'))]
    result={'version':'strategy-workshop-v1','selection':selection,'attempts':attempts,'confirmation':confirmation,
        'source':source,'corpus_manifest_sha256':corpus['manifest_sha256'],'orders_placed':0,
        'scope':'Bounded multi-role research code author/review/refinement. Confirmation is final, not fed back to models. Review approval is not evidence of investment performance.',
        'limitations':['One retrospective venue and idealized daily fills','Adaptive validation reuse can overfit','No independently authenticated point-in-time news or executable investment advice','Pure-code restrictions are not a hostile-code sandbox','Tokens recorded per role; monetary price unavailable and not inferred']}
    atomic(output/'result.json',result)
    atomic(output/'artifacts.json',{str(p.relative_to(output)):digest(p) for p in sorted(output.rglob('*')) if p.is_file() and p.name!='artifacts.json'})
    return result
