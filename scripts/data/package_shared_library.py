"""Explicit local snapshot packaging; no data acquisition or remote upload."""
import argparse
import json
from pathlib import Path
from dotenv import dotenv_values
from research_data.library.sharing import build_shared_library,archive_shared_library


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--archive',type=Path)
    p.add_argument('--credential-file',type=Path,help='Local configuration to scan against; values are never logged')
    args=p.parse_args();forbidden=[]
    if args.credential_file:
        forbidden=[v.encode() for k,v in dotenv_values(args.credential_file).items()
                   if v and len(v)>=12 and any(t in k.upper() for t in ('KEY','TOKEN','SECRET','PASSWORD'))]
    forbidden.extend(str(parent).encode()+b'/' for parent in [Path.home()] if len(parent.parts)>2)
    m=build_shared_library(args.source,args.destination,forbidden=forbidden)
    result={'directory':str(args.destination),'files':len(m['files']),'core_complete':m['core_complete']}
    if args.archive:result['archive']=archive_shared_library(args.destination,args.archive)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
