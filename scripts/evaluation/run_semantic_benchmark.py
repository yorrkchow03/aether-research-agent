"""Explicit freeze/run/replay of the bounded Jev versus Doubao component study."""
import argparse,json
from pathlib import Path
from research_agent.semantic_benchmark import prepare_run,run,replay

ROOT=Path(__file__).resolve().parents[2]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['prepare','run','replay'])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dataset',type=Path)
    p.add_argument('--review-file',type=Path)
    a=p.parse_args()
    if a.operation=='prepare':
        if not a.dataset or not a.review_file:p.error('prepare requires dataset and independent review file')
        result=prepare_run(ROOT,a.dataset,a.output,review_file=a.review_file)
        print(json.dumps({'status':'protocol_frozen','cases':len(result['order'])//3,'models':result['models']}))
    elif a.operation=='run':
        result=run(ROOT,a.output,progress=lambda r:print(json.dumps(r),flush=True))
        print(json.dumps({'paired_denominator':result['paired_denominator'],'providers':{k:{n:v[n] for n in ['planned','attempted','ok','abstained','errors','cost']} for k,v in result['providers'].items()}}))
    else:
        result=replay(ROOT,a.output);print(json.dumps({'status':result['status'],'replayed':len(result['checks']),'provider_requests':0}))
if __name__=='__main__':main()
