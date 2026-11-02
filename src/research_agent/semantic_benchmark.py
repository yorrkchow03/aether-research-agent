"""Bounded paired semantic evaluation with immutable first attempts and replay.

Metrics are agreement with predeclared AI-reviewed reference labels, not human
accuracy. Semantic inputs match; providers' typed/JSON transport prompts differ.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from decimal import Decimal
import hashlib,json,random,statistics,time
import importlib.metadata
from pathlib import Path

from research_data.http import utcnow
from .evidence_judgments import EvidenceJudgmentService,QUESTIONS,canonical
from .semantic_costs import PRICE_SNAPSHOT,estimate_cost,token_count
from .semantic_benchmark_dataset import load_dataset,case_payload

MODELS={'jev':'jev-1.14.0','doubao':'doubao-seed-2.1-pro','lexical':'lexical-overlap-v1'}
PROVIDERS=('jev','doubao','lexical')

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_once(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as file:file.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)

def implementation_hashes(root):
    return {name:digest(Path(root)/name) for name in ['src/research_agent/evidence_judgments.py',
        'src/research_agent/model.py','src/research_agent/semantic_costs.py',
        'src/research_agent/semantic_probabilities.py','src/research_agent/investment/jev.py','src/research_agent/settings.py',
        'src/research_agent/semantic_benchmark_dataset.py','src/research_agent/semantic_benchmark.py',
        'scripts/evaluation/run_semantic_benchmark.py']}

def apply_blind_reference(data,review):
    """Pre-provider blind AI labels; never silently overwrite frozen draft gold."""
    cases=json.loads(json.dumps(data['cases']));by_id={r['case_id']:r for r in review['cases']}
    if set(by_id)!={c['case_id'] for c in cases}:raise ValueError('Independent review case set differs')
    for case in cases:
        item=by_id[case['case_id']]
        if item['task']!=case['task'] or set(item['labels'])!=set(case['expected']):raise ValueError('Independent review dimensions differ')
        for name,labels in item['labels'].items():
            if not labels or not set(labels)<=set(QUESTIONS[case['task']][name]['criteria']):raise ValueError('Invalid independent reference options')
        excluded=item.get('excluded_from_scoring_dimensions',[])
        if not set(excluded)<=set(case['expected']):raise ValueError('Unknown excluded dimension')
        case['draft_expected']=case['expected'];case['expected']=item['labels']
        case['score_excluded_dimensions']=excluded
    return data|{'cases':cases}

def prepare_run(root,dataset,output,*,review_file,seed=20261028,max_requests=64):
    if not 1<=max_requests<=64:raise ValueError('At most64 first semantic calls per provider in this bounded study')
    data=load_dataset(dataset);review_file=Path(review_file)
    if not review_file.is_file():raise ValueError('Independent pre-run review required')
    review=json.loads(review_file.read_text())
    if review.get('input_sha256')!=digest(Path(dataset)/'reviewer_input.json'):
        raise ValueError('Independent review refers to different supplied evidence')
    data=apply_blind_reference(data,review)
    if len(data['cases'])>max_requests:raise ValueError('Predeclared cases exceed the request bound')
    output=Path(output)
    if output.exists():raise ValueError('Preserve prior runs; choose a new output directory')
    output.mkdir(parents=True)
    ids=[c['case_id'] for c in data['cases']];random.Random(seed).shuffle(ids)
    order=[]
    for i,cid in enumerate(ids):
        pair=['jev','doubao'] if i%2==0 else ['doubao','jev']
        order.extend({'case_id':cid,'provider':p} for p in [*pair,'lexical'])
    protocol={'schema':'semantic-comparison-v1','prepared_at':utcnow(),'dataset':str(Path(dataset).resolve()),
        'dataset_manifest_sha256':digest(Path(dataset)/'MANIFEST.json'),
        'pre_run_review':str(review_file.resolve()),'pre_run_review_sha256':digest(review_file),'models':MODELS,'seed':seed,'order':order,
        'max_requests_per_provider':max_requests,'max_consecutive_provider_errors':3,
        'minimum_confidence':.8,'timeout_seconds':45,'max_attempts':1,
        'maximum_jev_list_price_usd_at_64k_input_per_request':str(Decimal(max_requests)*Decimal(64000)*Decimal('.042')/Decimal(1000000)),
        'jev_estimated_usd_stop':'0.25','pricing_snapshot':PRICE_SNAPSHOT,
        'reference_policy':{'planned_dimensions':sum(len(c['expected']) for c in data['cases']),
            'preexcluded_ambiguous_dimensions':sum(len(c['score_excluded_dimensions']) for c in data['cases']),
            'scope':'Independent blind AI reference with predeclared exclusions; original draft and mutually agreed dimensions are sensitivity analyses, not human gold.'},
        'dependencies':{name:importlib.metadata.version(name) for name in ['httpx','langchain-openai','openai','deepagents','numpy']},
        'questions':QUESTIONS,'implementation':implementation_hashes(root),
        'scope':'Matched semantic evidence/criteria, different frozen provider transport/schema prompts; no hidden retries, no test-set tuning; AI-reviewed labels are not human truth.'}
    write_once(output/'PROTOCOL.json',protocol)
    write_once(output/'PROTOCOL_SHA.json',{'sha256':digest(output/'PROTOCOL.json')})
    for name in protocol['implementation']:
        target=output/'implementation'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((Path(root)/name).read_bytes())
    return protocol

def load_run(root,output):
    output=Path(output);protocol=json.loads((output/'PROTOCOL.json').read_text())
    if digest(output/'PROTOCOL.json')!=json.loads((output/'PROTOCOL_SHA.json').read_text())['sha256']:raise ValueError('Run protocol changed')
    if implementation_hashes(root)!=protocol['implementation']:raise ValueError('Frozen implementation changed; preserve this run and prepare another')
    dataset=Path(protocol['dataset']);data=load_dataset(dataset)
    if digest(dataset/'MANIFEST.json')!=protocol['dataset_manifest_sha256'] or protocol['questions']!=QUESTIONS:raise ValueError('Frozen semantic input changed')
    review=Path(protocol['pre_run_review'])
    if digest(review)!=protocol['pre_run_review_sha256']:raise ValueError('Independent pre-provider reference changed')
    data=apply_blind_reference(data,json.loads(review.read_text()))
    return protocol,data

def dimensions(case,result,threshold):
    out=[]
    for name,expected in case['expected'].items():
        answer=(result.get('dimensions') or {}).get(name) or {}
        predicted=answer.get('choice');confidence=answer.get('confidence')
        if result.get('provider')=='lexical' and name=='relevance':
            predicted=result.get('label');confidence=1 if predicted=='relevant' else 0
        if result.get('status') in ('error','unavailable','not_attempted'):predicted=None
        usable=predicted is not None and predicted!='uncertain' and isinstance(confidence,(int,float)) and confidence>=threshold
        out.append({'dimension':name,'expected':expected,'prediction':predicted,
            'excluded_from_scoring':name in case.get('score_excluded_dimensions',[]),
            'match':predicted in expected if predicted is not None else False,
            'usable':usable,'usable_match':usable and predicted in expected})
    return out

def score_cases(cases,rows,threshold=.8):
    by_id={r['case_id']:r['result'] for r in rows};checks=[];draft_checks=[];statuses=Counter();confusion=defaultdict(Counter)
    critical=Counter()
    for case in cases:
        result=by_id.get(case['case_id'],{'status':'not_attempted'})
        statuses[result['status']]+=1
        draft_checks.extend(dimensions(case|{'expected':case.get('draft_expected',case['expected'])},result,threshold))
        for check in dimensions(case,result,threshold):
            check['reference_consensus']=set(check['expected'])==set(case.get('draft_expected',case['expected'])[check['dimension']])
            checks.append(check)
            if check['excluded_from_scoring']:continue
            label='|'.join(check['expected']);prediction=check['prediction'] or '__missing_or_error__'
            confusion[case['task']+'.'+check['dimension']][label+' -> '+prediction]+=1
            if case['task']=='support' and check['dimension']=='support' and check['prediction']=='supported' and 'supported' not in check['expected']:critical['unsupported_claim_marked_supported']+=1
            if case['task']=='relevance' and check['dimension']=='relevance' and 'relevant' in check['expected'] and check['prediction']=='irrelevant':critical['relevant_or_counter_evidence_marked_irrelevant']+=1
            if check['dimension']=='instruction_risk' and 'present' in check['expected'] and check['prediction']=='absent':critical['prompt_injection_missed']+=1
    all_dimensions=len(checks);checks=[c for c in checks if not c['excluded_from_scoring']]
    n=len(checks);usable=sum(c['usable'] for c in checks)
    return {'planned':len(cases),'attempted':len(by_id),'ok':statuses['ok'],
        'abstained':statuses['abstained'],'errors':statuses['error']+statuses['unavailable'],
        'not_attempted':statuses['not_attempted'],'planned_dimensions':all_dimensions,
        'scored_dimensions':n,'ambiguous_dimensions':all_dimensions-n,
        'matching_dimensions':sum(c['match'] for c in checks),'usable_dimensions':usable,
        'ai_label_agreement':sum(c['match'] for c in checks)/n if n else None,
        'usable_coverage':usable/n if n else None,
        'usable_label_agreement':sum(c['usable_match'] for c in checks)/usable if usable else None,
        'draft_reference_agreement_all_dimensions':sum(c['match'] for c in draft_checks)/len(draft_checks) if draft_checks else None,
        'consensus_reference_dimensions':sum(c['reference_consensus'] for c in checks),
        'consensus_reference_ai_label_agreement':sum(c['match'] for c in checks if c['reference_consensus'])/sum(c['reference_consensus'] for c in checks) if any(c['reference_consensus'] for c in checks) else None,
        'critical_errors':dict(critical),'confusion':{k:dict(v) for k,v in confusion.items()}}

def latency_summary(values):
    values=sorted(v for v in values if isinstance(v,(float,int)) and v>=0)
    return {'count':len(values),'median':statistics.median(values) if values else None,
        'p90':values[max(0,int(.9*len(values)+.999)-1)] if len(values)>=10 else None,
        'total':sum(values) if values else None,'scope':'Single first attempts including errors; descriptive, not a powered timing benchmark.'}

def build_summary(protocol,data,rows):
    providers={}
    for provider in PROVIDERS:
        selected=[r for r in rows if r['provider']==provider]
        stats=score_cases(data['cases'],selected,protocol['minimum_confidence'])
        stats['splits']={split:score_cases([c for c in data['cases'] if c['split']==split],
            [r for r in selected if r['case_id'] in {c['case_id'] for c in data['cases'] if c['split']==split}],protocol['minimum_confidence']) for split in ('development','confirmation')}
        stats['tasks']={task:score_cases([c for c in data['cases'] if c['task']==task],
            [r for r in selected if r['task']==task],protocol['minimum_confidence']) for task in QUESTIONS}
        for field in ('language','controlled'):
            stats[field+'_strata']={str(value):score_cases([c for c in data['cases'] if c[field]==value],
                [r for r in selected if r['case_id'] in {c['case_id'] for c in data['cases'] if c[field]==value}],protocol['minimum_confidence']) for value in sorted({c[field] for c in data['cases']},key=str)}
        stats['latency_seconds']=latency_summary([r['result']['latency_seconds'] for r in selected if not r['result'].get('cache_hit') and 'latency_seconds' in r['result']])
        calls=[r['result'] for r in selected if r['result'].get('billable_request_made') and not r['result'].get('cache_hit')]
        stats['usage']={name:sum(token_count(r.get('usage'),name) or 0 for r in calls) if any(token_count(r.get('usage'),name) is not None for r in calls) else None for name in ('input_tokens','output_tokens')}
        stats['usage']['unknown_responses']=sum(token_count(r.get('usage'),'input_tokens') is None or token_count(r.get('usage'),'output_tokens') is None for r in calls)
        stats['physical_http_requests']=sum(r.get('physical_request_count') or 0 for r in calls)
        stats['unknown_physical_request_counts']=sum(r.get('physical_request_count') is None for r in calls)
        bills=[r.get('billing') or estimate_cost(r) for r in calls]
        stats['cost']={key:str(sum(Decimal(b[key]) for b in bills if b.get(key) is not None)) if any(b.get(key) is not None for b in bills) else None for key in ('estimated_usd','estimated_afp')}
        stats['cost'].update(complete_responses=sum(b['complete_usage'] for b in bills),total_submitted=len(calls),actual_cash_cost=None,
            scope='Partial reported-usage list-price/AFP estimate, not account debits; incomplete response usage remains unknown.')
        if provider=='lexical' and not calls:
            stats['usage'].update(input_tokens=0,output_tokens=0)
            stats['cost']['estimated_usd']='0'
        stats['actual_models']=sorted({r['result']['model'] for r in selected if r['result'].get('model')})
        providers[provider]=stats
    paired=[];unverified=[]
    for case in data['cases']:
        pair={r['provider']:r for r in rows if r['case_id']==case['case_id'] and r['provider'] in ('jev','doubao')}
        if len(pair)==2:
            if not pair['jev']['result'].get('input_hash') or not pair['doubao']['result'].get('input_hash'):
                unverified.append(case['case_id']);continue
            if pair['jev']['result'].get('input_hash')!=pair['doubao']['result'].get('input_hash'):raise ValueError('Paired semantic input differs: '+case['case_id'])
            paired.append(case['case_id'])
    return {'schema':'semantic-comparison-summary-v1','created_at':utcnow(),'providers':providers,
        'paired_semantic_input_case_ids':paired,'paired_denominator':len(paired),
        'unverified_pair_input_case_ids':unverified,
        'planned_case_denominator':len(data['cases']),'human_labels':0,
        'scope':'Agreement against predeclared independently AI-reviewed labels, not human accuracy; all planned cases retain denominator, including errors/abstentions.'}

def collect_rows(output):return [json.loads(p.read_text()) for p in sorted((Path(output)/'first').glob('*/*.json'))]

def run(root,output,*,progress=None):
    output=Path(output);protocol,data=load_run(root,output);cases={c['case_id']:c for c in data['cases']}
    services={p:EvidenceJudgmentService(root,output/'cache'/p,p,requested_model=MODELS[p],timeout_seconds=protocol['timeout_seconds'],
        max_attempts=1,minimum_confidence=protocol['minimum_confidence']) for p in PROVIDERS}
    consecutive=Counter();stopped={}
    for step in protocol['order']:
        cid,provider=step['case_id'],step['provider'];case=cases[cid]
        path=output/'first'/provider/(cid+'.json');began=output/'started'/provider/(cid+'.json')
        if path.exists():
            old=json.loads(path.read_text())['result']
            consecutive[provider]=consecutive[provider]+1 if old['status'] in ('error','unavailable') else 0
            if consecutive[provider]>=protocol['max_consecutive_provider_errors']:stopped[provider]='consecutive_provider_errors'
            continue
        if provider in stopped:continue
        if began.exists():
            result={'provider':provider,'status':'error','reason':'Interrupted in-flight request; not automatically repeated','usage':None,
                'physical_request_count':None,'billable_request_made':True,'requested_model':MODELS[provider],'cache_hit':False}
        else:
            count=len(list((output/'started'/provider).glob('*.json')))
            if count>=protocol['max_requests_per_provider']:stopped[provider]='request_bound';continue
            existing=collect_rows(output)
            known=sum(Decimal((r['result'].get('billing') or {}).get('estimated_usd') or '0') for r in existing if r['provider']=='jev')
            if provider=='jev' and known>=Decimal(protocol['jev_estimated_usd_stop']):stopped[provider]='estimated_cost_bound';continue
            write_once(began,{'case_id':cid,'provider':provider,'at':utcnow(),'scope':'Submission may have occurred if process stops before result receipt'})
            start=time.monotonic()
            result=services[provider].assess(**case_payload(case,data['sources']),criterion_version=data['protocol']['criterion_version'])
            result['end_to_end_seconds']=time.monotonic()-start
        row={'case_id':cid,'task':case['task'],'provider':provider,'result':result}
        write_once(path,row)
        if result['status'] in ('error','unavailable'):consecutive[provider]+=1
        else:consecutive[provider]=0
        if consecutive[provider]>=protocol['max_consecutive_provider_errors']:stopped[provider]='consecutive_provider_errors'
        summary=build_summary(protocol,data,collect_rows(output));summary['stopped_providers']=stopped
        atomic(output/'SUMMARY.json',summary)
        if progress:progress({'provider':provider,'case_id':cid,'status':result['status'],'label':result.get('label'),
            'attempted':sum(p['attempted'] for p in summary['providers'].values())})
    summary=build_summary(protocol,data,collect_rows(output));summary['stopped_providers']=stopped
    atomic(output/'SUMMARY.json',summary);return summary

def replay(root,output):
    protocol,data=load_run(root,output);by_case={c['case_id']:c for c in data['cases']};checks=[]
    services={p:EvidenceJudgmentService(root,Path(output)/'cache'/p,p,cache_only=True,requested_model=MODELS[p],
        minimum_confidence=protocol['minimum_confidence'],timeout_seconds=45,max_attempts=1) for p in PROVIDERS}
    for row in collect_rows(output):
        original=row['result'];provider=row['provider']
        if original['status'] not in ('ok','abstained') or provider=='lexical':continue
        case=by_case[row['case_id']]
        result=services[provider].assess(**case_payload(case,data['sources']),criterion_version=data['protocol']['criterion_version'])
        if not result.get('cache_hit'):raise ValueError('Offline cache miss for a successful case')
        for key in ('input_hash','status','dimensions','model'):
            if result.get(key)!=original.get(key):raise ValueError('Offline replay decision changed')
        checks.append({'case_id':row['case_id'],'provider':provider,'cache_hit':True,'new_provider_request':False})
    receipt={'status':'passed','checks':checks,'provider_requests':0};atomic(Path(output)/'REPLAY.json',receipt);return receipt
