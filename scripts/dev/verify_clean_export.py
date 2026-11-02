"""Verify exported source, or an optional wheel, with fresh state and installed dependencies.

No credential/data/runtime copies or network/model calls. This is not a fresh
dependency download test; application imports must come from the selected artifact.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, help='Optional: check wheel imports instead of exported src/')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    manifest = json.loads((source / 'SOURCE_MANIFEST.json').read_text())
    if (source / '.env').exists():raise ValueError('Clean verification refuses a source tree with .env')
    for name, expected in manifest['files'].items():
        file = source / name
        if file.is_symlink() or not file.resolve().is_relative_to(source):raise ValueError('Invalid source manifest path')
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:raise ValueError('Source manifest mismatch: '+name)
    with tempfile.TemporaryDirectory(prefix='aether-clean-install-') as directory:
        root = Path(directory).resolve()
        site = root / 'site' if args.wheel else source / 'src'
        if args.wheel:
            site.mkdir()
            with zipfile.ZipFile(args.wheel) as archive:
                for name in archive.namelist():
                    if not (site / name).resolve().is_relative_to(site):raise ValueError('Wheel traversal path')
                archive.extractall(site)
        env = {k: v for k, v in os.environ.items() if not any(t in k.upper() for t in ('API_KEY', 'SECRET', 'TOKEN'))}
        env['PYTHONPATH'] = str(site)
        probe = '''import json,sys
from pathlib import Path
import research_agent,research_data
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app
from fastapi.testclient import TestClient
site=Path(sys.argv[1]);root=Path(sys.argv[2])
assert Path(research_agent.__file__).is_relative_to(site)
assert Path(research_data.__file__).is_relative_to(site)
skills=list((site/'research_agent/skills').glob('*/SKILL.md'));assert len(skills)==4
runtime=AgentRuntime(root=root)
app=create_app(runtime)
with TestClient(app,base_url='http://127.0.0.1:8765') as client:
 assert client.get('/').status_code==200
 assert client.get('/api/config').json()['configured'] is False
 assert client.get('/api/portfolio').json()['empty'] is True
 assert client.get('/api/desk').json()['inbox']==[]
 for name in ('dashboard.js','desk.js','desk.css','investment.js'):
  assert client.get('/assets/'+name).status_code==200
 assert runtime.sessions()==[]
print(json.dumps({'artifact_imports':True,'packaged_skills':len(skills),'empty_state':True,'local_api':True,'credentials_copied':False,'model_calls':0}))
'''
        result = subprocess.run([sys.executable, '-c', probe, str(site), str(root / 'fresh')],
                                cwd=root, env=env, text=True, capture_output=True)
        if result.returncode:raise RuntimeError(result.stderr)
        probe_result = json.loads(result.stdout)
        tests = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
            cwd=args.source.resolve(), env=env, text=True, capture_output=True)
        receipt = {'probe': probe_result, 'artifact':'wheel' if args.wheel else 'exported_source',
            'source_manifest_checked':True,'exported_suite_passed': tests.returncode == 0,
            'test_output': tests.stdout + tests.stderr,
            'scope': 'Application imported from the selected artifact with fresh private state; installed dependencies reused; no new dependency install or live-provider test.'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt, indent=2))
        if tests.returncode:raise SystemExit(tests.returncode)


if __name__ == '__main__':main()
