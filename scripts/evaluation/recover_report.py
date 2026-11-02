"""Explicit one-slot post-confirmation recovery; no automatic rerun."""
import argparse,json
from pathlib import Path
from research_agent.report_recovery import recover

ROOT=Path(__file__).resolve().parents[2]
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--slot',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=recover(ROOT,args.protocol,args.slot,args.output)
    print(json.dumps({k:result.get(k) for k in ('state','published_reports',
        'pro_physical_requests_known','jev_physical_requests_known','known_estimated_pro_afp',
        'known_estimated_jev_usd')},indent=2))
