"""Freeze once, then replay labelled sources and relevant offline contract checks."""
import argparse
import io
import json
from pathlib import Path
import sys
import unittest
import shutil

from research_agent.memory_benchmark import freeze_manifest, run_retrieval, digest
from research_data.http import utcnow

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest_path = ROOT / 'data/validation/memory_v1/manifest.json'
    if args.output.exists():
        raise ValueError('Results exist; preserve failures and use a new output directory')
    if args.freeze:
        freeze_manifest(ROOT, manifest_path)
    args.output.mkdir(parents=True)
    if args.freeze:
        for name in ('src','tests'):
            shutil.copytree(ROOT/name,args.output/'software_snapshot'/name,
                            ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
    result = run_retrieval(ROOT, manifest_path)
    (args.output / 'retrieval.json').write_text(json.dumps(result, indent=2) + '\n')
    manifest = json.loads(manifest_path.read_text())
    sys.path.insert(0, str(ROOT / 'tests'))
    checks = []
    for relative, spec in manifest['offline_suites'].items():
        path = ROOT / relative
        if digest(path) != spec['sha256']:
            raise ValueError('Frozen contract suite changed: ' + relative)
        log = io.StringIO()
        suite = unittest.TestLoader().discover(str(ROOT / 'tests'), pattern=path.name)
        outcome = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        checks.append({'suite': relative, 'family': spec['family'], 'checks': outcome.testsRun,
            'failures': len(outcome.failures), 'errors': len(outcome.errors), 'skipped': len(outcome.skipped),
            'log': log.getvalue(), 'scope': 'Offline contract/fixture tests; not independent market events'})
        (args.output / 'offline_checks.json').write_text(json.dumps(checks, indent=2) + '\n')
    summary = {'created_at': utcnow(), 'retrieval_questions': result['unique_questions'],
        'topic_groups': result['topic_groups'], 'offline_checks': sum(c['checks'] for c in checks),
        'offline_families': len(checks), 'failures': sum(c['failures'] + c['errors'] for c in checks),
        'model_quality_measured': False, 'human_labels_reviewed': False,
        'source_history_target': 'Current source snapshots captured; twelve multi-version event histories remain unavailable',
        'retrieval': result['summaries']}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'retrieval'}, indent=2))


if __name__ == '__main__':
    main()
