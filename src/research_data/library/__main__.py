"""Explicit local library commands. No background schedules or trading operations."""
import argparse,json
from pathlib import Path
from research_data.http import dumps
from .store import ResearchLibrary
from .registry import install

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('command',choices=['init','collect','texts','import-legacy','coverage','series','search','verify','freeze','backup'])
 p.add_argument('--root',type=Path,default=Path('data/library'))
 p.add_argument('--project',type=Path,default=Path.cwd())
 p.add_argument('--groups',default='')
 p.add_argument('--dataset',default='')
 p.add_argument('--query',default='')
 p.add_argument('--output',type=Path)
 p.add_argument('--refresh',action='store_true')
 p.add_argument('--start');p.add_argument('--end')
 p.add_argument('--window-start');p.add_argument('--window-end')
 p.add_argument('--mode',choices=['observed','strict'],default='observed');p.add_argument('--known-by')
 p.add_argument('--asset');p.add_argument('--source');p.add_argument('--kind');p.add_argument('--limit',type=int);p.add_argument('--offset',type=int,default=0)
 args=p.parse_args();writing=args.command in ('init','collect','texts','import-legacy')
 lib=ResearchLibrary(args.root,create=writing,window_start=args.window_start,window_end=args.window_end)
 if writing:install(lib)
 if args.command=='init':result={'initialized':str(lib.path),'datasets':len(lib.datasets())}
 elif args.command=='collect':
  from .numeric import NumericCollector
  NumericCollector(lib,refresh=args.refresh).run(args.groups.split(',') if args.groups else None);result=lib.coverage()
 elif args.command=='texts':
  from .text import TextCollector
  TextCollector(lib,refresh=args.refresh).run(args.groups.split(',') if args.groups else None);result=lib.coverage()
 elif args.command=='import-legacy':
  from .legacy import import_legacy
  result=import_legacy(lib,args.project)
 elif args.command=='coverage':result=lib.coverage()
 elif args.command=='series':result=lib.series(args.dataset,start=args.start,end=args.end,mode=args.mode,known_by=args.known_by,limit=args.limit or 2000,offset=args.offset)
 elif args.command=='search':result=lib.search_documents(args.query,start=args.start,end=args.end,mode=args.mode,known_by=args.known_by,asset=args.asset,source=args.source,kind=args.kind,limit=args.limit or 30,offset=args.offset)
 elif args.command=='backup':
  if not args.output:p.error('backup requires --output')
  result=lib.backup(args.output)
 elif args.command in ('verify','freeze'):
  from .verify import verify_library,freeze_library
  result=verify_library(lib) if args.command=='verify' else freeze_library(lib,args.output)
 if args.output and args.command not in ('backup','freeze'):
  args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(dumps(result,indent=2)+'\n')
 print(dumps(result,indent=2))

if __name__=='__main__':main()
