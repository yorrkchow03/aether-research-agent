"""Manually and incrementally index captured originals; never fetch or summarize."""
import argparse,json
from pathlib import Path
from research_agent.runtime import AgentRuntime
from research_agent.memory_service import index_source
from research_data.http import utcnow

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',action='append',help='Existing source ID, repeat for multiple independent source batches')
    p.add_argument('--limit',type=int,default=30,help='Maximum original versions per source in this manual batch')
    p.add_argument('--offset',type=int,default=0)
    p.add_argument('--state-dir',type=Path)
    p.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args()
    if not 1<=args.limit<=100 or args.offset<0:raise ValueError('Use bounded batches and an explicit continuation offset')
    if args.receipt.exists():raise ValueError('Preserve prior receipts')
    runtime=AgentRuntime(ROOT,state_dir=args.state_dir)
    if not runtime.library or not runtime.memory_index:raise ValueError('Selected library and memory index are required')
    receipt={'started_at':utcnow(),'library_window':[runtime.library.window_start,runtime.library.window_end],
        'offset':args.offset,'limit_per_source':args.limit,'sources':args.source or [None],'items':[],
        'provider_calls':0,'training':False,'scope':'Partial derived index over already captured originals. This is not full corpus coverage or semantic fact extraction.'}
    for source in args.source or [None]:
        page=runtime.library.search_documents('',source=source,limit=args.limit,offset=args.offset)
        for item in page['results']:
            rid=item['record_id']
            try:result=index_source(runtime.memory_index,runtime.knowledge.get(rid))
            except (ValueError,KeyError,OSError) as exc:result={'record_id':rid,'indexed':False,'error_type':type(exc).__name__}
            receipt['items'].append(result)
    receipt['ended_at']=utcnow();receipt['indexed']=sum(r.get('indexed',False) for r in receipt['items'])
    args.receipt.parent.mkdir(parents=True,exist_ok=True);args.receipt.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'indexed':receipt['indexed'],'items':len(receipt['items']),'receipt':str(args.receipt)}))

if __name__=='__main__':main()
