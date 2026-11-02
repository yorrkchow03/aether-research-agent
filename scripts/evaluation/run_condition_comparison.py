"""Explicit bounded Jev/Pro diagnostic over frozen public synthetic sources.

Never runs on server startup or during ordinary publication. First attempts and
provider abstentions/errors are retained. The offline evaluator cannot measure
semantic truth or upgrade the production routing policy.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil

from research_agent.condition_evaluation import aggregate_attempts, condition_probe, digest, load_dataset
from research_agent.evidence_judgments import EvidenceJudgmentService, QUESTIONS
from research_data.http import utcnow


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n')


def main(argv=None):
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,default=root/'examples/evaluation/condition_preservation_v1.json')
    parser.add_argument('--timeout',type=int,default=30,choices=range(10,61))
    args=parser.parse_args(argv)
    dataset=load_dataset(args.dataset)
    # A new directory makes a repeated first-attempt run an explicit decision.
    args.output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(args.dataset,args.output/'dataset.json')
    frozen=args.output/'software';frozen.mkdir()
    for name in ('src/research_agent/evidence_judgments.py','src/research_agent/condition_evaluation.py',
                 'src/research_agent/semantic_costs.py','scripts/evaluation/run_condition_comparison.py'):
        shutil.copyfile(root/name,frozen/Path(name).name)
    write(args.output/'PROTOCOL.json',{'started_at':utcnow(),'dataset_hash':digest(dataset),
        'criterion_version':'condition-preservation-v1','criteria':QUESTIONS['support'],
        'minimum_confidence':.8,'timeout_seconds':args.timeout,'maximum_attempts_per_case':1,
        'providers':['jev','doubao'],'planned_cases_per_provider':len(dataset['cases']),
        'input_policy':'Identical eligible source bytes/question, no reference labels or obligations supplied.',
        'host_empty_context_policy':'Abstain without a provider request; retain in planned denominator.',
        'software':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen.iterdir()},
        'scope':'Synthetic development diagnostic. No production routing change, human gold labels or independent real-news accuracy claim.'})
    services={p:EvidenceJudgmentService(root,args.output/p,provider=p,
        timeout_seconds=args.timeout,max_attempts=1,minimum_confidence=.8) for p in ('jev','doubao')}
    def attempt(provider,case):
        probe=condition_probe(case)
        result=services[provider].assess('support',**probe,criterion_version='condition-preservation-v1')
        status='completed' if result['status']=='ok' else 'abstained' if result['status']=='abstained' else 'error'
        row={'case_id':case['case_id'],'arm':provider,'input_hash':digest(probe),
            'status':status,'label':result.get('label') if status=='completed' else None,
            'latency_seconds':result.get('latency_seconds'),'result':result}
        write(args.output/(provider+'--'+case['case_id']+'.json'),row)
        return row
    attempts=[]
    # Each source pair is sequential; providers run concurrently on that context.
    # This bounds request concurrency at two without giving one provider all the
    # warm inputs first. No retry, repair or response-selected rerun occurs.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i,case in enumerate(dataset['cases']):
            order=('jev','doubao') if i%2==0 else ('doubao','jev')
            for future in as_completed([pool.submit(attempt,p,case) for p in order]):
                row=future.result();attempts.append(row)
                write(args.output/'attempts.json',attempts)
                print(json.dumps({k:row[k] for k in ('case_id','arm','status','label')}) ,flush=True)
    comparison=aggregate_attempts(dataset,attempts)
    billing={}
    for provider in services:
        results=[r['result'] for r in attempts if r['arm']==provider]
        unit,key=('USD','estimated_usd') if provider=='jev' else ('AFP','estimated_afp')
        costs=[r.get('billing',{}).get(key) for r in results if r.get('billable_request_made')]
        billing[provider]={'unit':unit,'physical_http_attempts':sum(r.get('physical_request_count') or 0 for r in results),
            'submitted_results':len(costs),'known_estimates':sum(c is not None for c in costs),
            'estimated_total':str(sum((Decimal(str(c)) for c in costs),Decimal(0))) if all(c is not None for c in costs) else None,
            'actual_account_debit':None,
            'scope':'Saved coefficient estimate, not account debit. Provider units are not interchangeable.'}
    comparison.update(billing=billing,finished_at=utcnow())
    write(args.output/'comparison.json',comparison)
    write(args.output/'SEAL.json',{'files':{str(p.relative_to(args.output)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(args.output.rglob('*')) if p.is_file() and p.name!='SEAL.json'},
        'scope':'First-attempt artifacts; SQLite WAL contents are preserved as generated.'})
    print(json.dumps({'output':str(args.output.resolve()),'billing':billing}),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
