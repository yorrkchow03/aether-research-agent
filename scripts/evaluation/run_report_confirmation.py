"""Explicit one-time confirmation protocol freeze/run; no scheduling."""
import argparse,json
from pathlib import Path
from research_agent.report_confirmation import freeze_protocol,run_protocol

ROOT=Path(__file__).resolve().parents[2]
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','run'))
    parser.add_argument('--dataset',type=Path,default=ROOT/'local/report-flow-study/v1/dataset')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args()
    result=freeze_protocol(ROOT,args.dataset,args.output,args.workers) if args.action=='freeze' else run_protocol(ROOT,args.output)
    if args.action=='freeze':result={k:result[k] for k in ('schema','primary_slots','question_count','workers','software_fingerprint','study_policy_sha256')}
    print(json.dumps(result,indent=2))
