"""Replay a sealed study using its own hashed adapter code, with network blocked."""
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    output=a.output.resolve();protocol=json.loads((output/'PROTOCOL.json').read_text())
    seal=json.loads((output/'SEALED.json').read_text())
    for name,want in seal['files'].items():
        path=(output/name).resolve()
        if not path.is_relative_to(output) or hashlib.sha256(path.read_bytes()).hexdigest()!=want:raise ValueError('Sealed first result changed')
    with tempfile.TemporaryDirectory(prefix='aether-semantic-snapshot-') as directory:
        root=Path(directory).resolve();shutil.copytree(ROOT/'src',root/'src',ignore=shutil.ignore_patterns('__pycache__'))
        for name,want in protocol['implementation'].items():
            source=(output/'implementation'/name).resolve();target=(root/name).resolve()
            if not source.is_relative_to(output/'implementation') or not target.is_relative_to(root):raise ValueError('Unsafe implementation snapshot path')
            if hashlib.sha256(source.read_bytes()).hexdigest()!=want:raise ValueError('Frozen implementation hash mismatch')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
        env={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ('API_KEY','SECRET','TOKEN'))}
        env['PYTHONPATH']=str(root/'src')
        script="""import json,sys
from pathlib import Path
from unittest.mock import patch
import research_agent
from research_agent.semantic_benchmark import replay
assert Path(research_agent.__file__).resolve().is_relative_to(Path(sys.argv[1])/'src')
with patch('httpx.Client',side_effect=AssertionError('Network forbidden during cache replay')):
 result=replay(Path(sys.argv[1]),Path(sys.argv[2]))
print(json.dumps({'status':result['status'],'checks':len(result['checks']),'provider_requests':0}))
"""
        result=subprocess.run([sys.executable,'-c',script,str(root),str(output)],env=env,cwd=root,capture_output=True,text=True,timeout=120)
        if result.returncode:raise RuntimeError(result.stderr[-2000:])
        receipt=json.loads(result.stdout)
    for name,want in seal['files'].items():
        if hashlib.sha256((output/name).read_bytes()).hexdigest()!=want:raise ValueError('Replay modified sealed results')
    (output/'SNAPSHOT_REPLAY.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if __name__=='__main__':main()
