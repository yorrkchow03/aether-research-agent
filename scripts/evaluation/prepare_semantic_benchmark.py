"""Freeze a new saved-source semantic benchmark; never call model providers."""
import argparse,json
from pathlib import Path
from research_data.library import ResearchLibrary
from research_agent.semantic_benchmark_dataset import construct_real_dataset,freeze_dataset,load_dataset

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,default=ROOT/'data/library-halfyear-20260426-20261026')
    parser.add_argument('--output',type=Path,default=ROOT/'local/jev-validation/v1/dataset')
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if args.verify:
        result=load_dataset(args.output)
        print(json.dumps({'status':'verified','manifest':result['manifest']}));return
    library=ResearchLibrary(args.library,create=False)
    data=construct_real_dataset(library)
    result=freeze_dataset(args.output,**data,library_window=[library.window_start,library.window_end])
    print(json.dumps(result))

if __name__=='__main__':main()
