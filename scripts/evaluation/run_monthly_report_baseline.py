"""Freeze, run or inspect a monthly-library fixed-context report diagnostic."""
import argparse
import json
from pathlib import Path

from research_agent.monthly_report_benchmark import prepare, run, summarize
from research_agent.settings import Settings

ROOT = Path(__file__).resolve().parents[2]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'summary'])
    parser.add_argument('--output', type=Path, default=ROOT / 'local/report-baseline/monthly-v1/frozen')
    parser.add_argument('--cases', nargs='+')
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare(ROOT, args.output)
        print(json.dumps({k: result[k] for k in ('schema', 'cases', 'dataset_manifest_sha256')}, indent=2))
    elif args.action == 'run':
        result = run(ROOT, Settings.load(ROOT), args.output, args.cases)
        print(json.dumps({k: v for k, v in result.items() if k not in ('results', 'limitations')}, indent=2))
    else:
        result = summarize(args.output)
        print(json.dumps({k: v for k, v in result.items() if k not in ('results', 'limitations')}, indent=2))
