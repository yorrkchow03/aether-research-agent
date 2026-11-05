"""Prepare, collect, time or replay the separate MSBD5018 Jev NLP study."""
import argparse
import json
from pathlib import Path
from research_agent.jev_nlp_study import prepare, collect, calibrate, timing, analyze
from research_agent.jev_nlp_generation import generate
from research_agent.jev_nlp_revision import adopt_development


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'adopt-development', 'development', 'calibrate', 'test', 'timing', 'analyze', 'generate'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--parent-study', type=Path)
    parser.add_argument('--config-root', type=Path, default=Path(__file__).resolve().parents[2],
                        help='Directory of private .env; never copied into study outputs')
    args = parser.parse_args(); root = Path(__file__).resolve().parents[2]
    progress = lambda row: print(json.dumps(row), flush=True)
    if args.operation == 'prepare':
        if not args.dataset or not args.review:
            parser.error('prepare requires --dataset and --review')
        result = prepare(root, args.dataset, args.output, args.review)
    elif args.operation == 'adopt-development':
        if not args.parent_study:parser.error('adopt-development requires --parent-study')
        result = adopt_development(args.parent_study, args.output)
    elif args.operation in ('development', 'test'):
        result = collect(root, args.config_root, args.output, phase=args.operation, progress=progress)
    elif args.operation == 'calibrate':
        result = calibrate(root, args.output)
    elif args.operation == 'timing':
        result = timing(root, args.config_root, args.output, progress=progress)
    elif args.operation == 'generate':
        result = generate(root, args.config_root, args.output, progress=progress)
    else:
        full = analyze(root, args.output)
        result = {name: full[name] for name in ('case_count', 'unique_semantic_jobs', 'paired_input_hashes_verified', 'accounting_first_attempts')}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
