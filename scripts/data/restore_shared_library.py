"""Restore a shared data Release without overwriting local research state."""
import argparse
import json
from pathlib import Path
from research_data.library.sharing import restore_shared_library


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path)
    p.add_argument('--destination',type=Path,default=Path('data/library-halfyear-20260426-20261026'))
    p.add_argument('--checksums',type=Path,help='Release SHA256SUMS file')
    args=p.parse_args();expected=None
    if args.checksums:
        values={line.split(maxsplit=1)[1].strip().lstrip('*'):line.split()[0]
                for line in args.checksums.read_text().splitlines() if line.strip()}
        expected=values[args.archive.name]
    print(json.dumps(restore_shared_library(args.archive,args.destination,expected_sha256=expected),indent=2))


if __name__=='__main__':main()
