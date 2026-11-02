#!/usr/bin/env python3
"""Freeze and replay synthetic condition-change contracts without network/API use."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

from research_agent.condition_evaluation import aggregate_attempts, digest, load_dataset, offline_checks
from research_data.http import utcnow


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=root/'examples/evaluation/condition_preservation_v1.json')
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; existing output is never overwritten')
    parser.add_argument('--attempts', type=Path, help='Optional saved first-attempt records; no requests are made')
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)
    attempts = json.loads(args.attempts.read_text()) if args.attempts else None
    summary = offline_checks(dataset)
    comparison = aggregate_attempts(dataset, attempts) if attempts is not None else None
    args.output.mkdir(parents=True, exist_ok=False)
    copied = args.output/'dataset.json'
    shutil.copyfile(args.dataset, copied)
    (args.output/'protocol.json').write_text(json.dumps(dataset['protocol'], ensure_ascii=False, indent=2)+'\n')
    (args.output/'offline-checks.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    if attempts is not None:
        shutil.copyfile(args.attempts, args.output/'attempts.json')
        (args.output/'comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2)+'\n')
    import research_agent.condition_evaluation as implementation
    source = Path(implementation.__file__)
    shutil.copyfile(source, args.output/'condition_evaluation.py')
    shutil.copyfile(__file__, args.output/'evaluate_conditions.py')
    files = {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir()) if p.is_file()}
    manifest = {'schema':'aether-condition-preservation-freeze-v1', 'frozen_at':utcnow(),
                'dataset_hash':digest(dataset), 'protocol_hash':digest(dataset['protocol']), 'files':files,
                'provider_calls':0, 'new_source_acquisitions':0,
                'scope':'Immutable diagnostic inputs/code and offline replay, not independent provider validation.'}
    (args.output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'output':str(args.output.resolve()), 'fixture_cases':summary['fixture_cases'],
                      'pairs':summary['pairs'], 'fixture_contracts_passed':summary['fixture_contracts_passed'],
                      'provider_calls':0, 'semantic_correctness':None}, ensure_ascii=False))
    return 0 if summary['fixture_contracts_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
