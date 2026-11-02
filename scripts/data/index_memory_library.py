"""Explicit full saved-library source index and bounded local vector warming."""
import argparse,json
from pathlib import Path
from research_data.library import ResearchLibrary
from research_agent.knowledge import KnowledgeStore
from research_agent.memory_service import open_memory_service
from research_agent.memory_library_index import library_inventory,index_inventory,import_version_links

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,default=ROOT/'runtime')
    parser.add_argument('--encoder-path',type=Path,default=Path('local/models/potion-multilingual-128M'))
    parser.add_argument('--max-items',type=int,default=1000)
    parser.add_argument('--retry-errors',action='store_true')
    parser.add_argument('--warm',action='store_true',help='Prepare local vectors in100-source batches after import')
    args=parser.parse_args()
    library=ResearchLibrary(args.library,create=False)
    knowledge=KnowledgeStore(args.state_dir/'knowledge');knowledge.library=library
    index,retriever,status=open_memory_service(ROOT,args.state_dir,knowledge,args.encoder_path)
    inventory=library_inventory(library)
    result=index_inventory(index,library,inventory,args.batch,max_items=args.max_items,retry_errors=args.retry_errors,
        progress=lambda row:print(json.dumps(row),flush=True))
    print(json.dumps({k:v for k,v in result.items() if k!='errors'}),flush=True)
    if result['status']=='complete_selected_inventory':
        links=import_version_links(index,library,inventory)
        (args.batch/'VERSION_LINKS.json').write_text(json.dumps(links,indent=2)+'\n')
    if args.warm:
        if retriever.encoder is None:raise ValueError('Local encoder unavailable; source import retained')
        # Reopen originals and enforce integrity before each bounded batch.
        rows=index.eligible_sources(source_limit=10000)['sources'];warm=[]
        for offset in range(0,len(rows),100):
            _,_,metadata=retriever._document_vectors(rows[offset:offset+100])
            warm.append(metadata)
            (args.batch/'WARM.json').write_text(json.dumps({'prepared_sources':min(offset+100,len(rows)),
                'total_sources':len(rows),'batches':warm,'provider_calls':0,'network_requests':0},indent=2)+'\n')
            print(json.dumps({'vector_prepared_sources':min(offset+100,len(rows)),'total':len(rows)}),flush=True)

if __name__=='__main__':main()
