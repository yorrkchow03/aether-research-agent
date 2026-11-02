"""Replay a frozen benchmark in a disposable source tree, without changing the app.

Original input/code/test digests must match. Uses the installed locked runtime;
does not install packages, copy credentials or contact a model/provider.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from research_agent.memory_benchmark import digest

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/validation/memory_v1/manifest.json')
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'reports/product_evolution/memory_v1/software_snapshot')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if args.output.exists():raise ValueError('Preserve existing results; choose a new output')
    args.output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix='aether-frozen-memory-') as directory:
        frozen = Path(directory)
        for relative, expected in manifest['code'].items():
            path = (args.snapshot / relative).resolve()
            if not path.is_relative_to(args.snapshot.resolve()) or digest(path) != expected:
                raise ValueError('Frozen software mismatch: ' + relative)
        shutil.copytree(args.snapshot / 'src', frozen / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        for relative, expected in manifest['files'].items():
            path = (ROOT / relative).resolve()
            if not path.is_relative_to(ROOT) or digest(path) != expected:
                raise ValueError('Frozen input changed: ' + relative)
            target = frozen / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        test_root=args.snapshot if (args.snapshot/'tests').is_dir() else ROOT
        for relative, spec in manifest['offline_suites'].items():
            if digest(test_root / relative) != spec['sha256']:
                raise ValueError('Frozen test changed: ' + relative)
        shutil.copytree(test_root / 'tests', frozen / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
        target_manifest = frozen / 'data/validation/memory_v1/manifest.json'
        shutil.copyfile(args.manifest, target_manifest)
        script = frozen / 'scripts/evaluation/run_memory_benchmark.py'; script.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/evaluation/run_memory_benchmark.py', script)
        env = {**os.environ, 'PYTHONPATH': str(frozen / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run([sys.executable, str(script), '--output', str(args.output)],
                                cwd=frozen, env=env, check=False)
        if result.returncode:raise SystemExit(result.returncode)
        (args.output / 'replay_provenance.json').write_text(json.dumps({
            'manifest_hash': digest(args.manifest), 'isolated_source_tree': True,
            'model_calls': 0, 'source_refreshes': 0, 'credentials_copied': False,
            'scope': 'Original frozen code/input/test hashes checked; runtime dependencies use the current locked installation.'}, indent=2) + '\n')


if __name__ == '__main__':main()
