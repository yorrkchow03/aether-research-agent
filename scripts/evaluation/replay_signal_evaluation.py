#!/usr/bin/env python3
"""Verify artifacts and replay with the saved source snapshot, with sockets blocked."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
SCRIPT='''import json,sys,socket
request=json.loads(sys.stdin.read());sys.path.insert(0,request['software'])
def forbidden(*args,**kwargs):raise AssertionError('Network forbidden in frozen replay')
socket.socket.connect=forbidden;socket.create_connection=forbidden
from research_agent.research_lab.evaluation import run_evaluation
run_evaluation(request['corpus'],request['output'],expected_manifest_sha256=request['manifest'])
'''
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--corpus',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=a.run.resolve();manifest=json.loads((root/'artifacts.json').read_text())
    for name,sha in manifest.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('Artifact identity changed: '+name)
    lock=json.loads((root/'input_lock.json').read_text())
    with tempfile.TemporaryDirectory(prefix='aether-signal-replay-') as tmp:
        payload={'software':str(root/'software'),'corpus':str(a.corpus.resolve()),'output':tmp+'/result','manifest':lock['corpus_manifest_sha256']}
        subprocess.run([sys.executable,'-I','-c',SCRIPT],input=json.dumps(payload),text=True,check=True,timeout=180)
        cases=sorted((root/'cases').glob('*.json'));changed=[p.name for p in cases if json.loads(p.read_text())!=json.loads((Path(tmp)/'result/cases'/p.name).read_text())]
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({'passed':not changed,'case_count':len(cases),'changed_cases':changed,'network_blocked':True,'replayed_saved_source':True,'manifest_sha256':lock['corpus_manifest_sha256']},indent=2)+'\n')
        if changed:raise SystemExit(1)
