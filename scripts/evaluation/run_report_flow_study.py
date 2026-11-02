"""Explicitly run a bounded frozen-source local-agent report attempt."""
import argparse,json
from pathlib import Path
from research_agent.report_flow_study import run_attempt,summarize_attempt

ROOT=Path(__file__).resolve().parents[2]
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('run','summary'))
    parser.add_argument('--dataset',type=Path,default=ROOT/'local/report-flow-study/v1/dataset')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case')
    parser.add_argument('--arm',choices=('original_off','pro_checks','jev_routed'))
    args=parser.parse_args()
    if args.action=='run':
        if not args.case or not args.arm:parser.error('run requires --case and --arm')
        result=run_attempt(ROOT,args.dataset,args.output,args.case,args.arm)
    else:result=summarize_attempt(args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('pro_usage_rows','judgment_billing','routing_metrics')},indent=2))
