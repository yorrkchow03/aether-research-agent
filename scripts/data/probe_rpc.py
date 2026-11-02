"""Explicit bounded public RPC probe. No model configuration or credentials."""
import argparse
import json
from pathlib import Path

from research_agent.live import LiveData, atomic
from research_agent.onchain import EthereumRead

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--historical-block',type=int,default=17000000)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/research_extensions/rpc_probe.json')
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Use a new output path; preserve earlier probe results')
    result=EthereumRead(LiveData(ROOT/'data/live')).probe_capabilities(args.historical_block)
    atomic(args.output,result)
    print(json.dumps({'checks':{k:v['status'] for k,v in result['checks'].items()},
        'same_original_cache_record':result['checks'].get('same_request_cache',{}).get('same_original_record'),
        'provider_maxima_measured':False,'model_calls':0},indent=2))

if __name__=='__main__':main()
