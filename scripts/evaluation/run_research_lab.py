#!/usr/bin/env python3
"""Explicit local extension job; workshop uses the existing authorized Pro Plan."""
import argparse
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from research_agent.settings import Settings
from research_agent.research_lab.service import ResearchLabService
ROOT=Path(__file__).resolve().parents[2]
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['signals','workshop']);p.add_argument('--rounds',type=int,default=2);p.add_argument('--resume');a=p.parse_args()
    runtime=SimpleNamespace(root=ROOT,state_dir=ROOT/'runtime',settings=Settings.load(ROOT),lock=threading.RLock(),active={},investment=SimpleNamespace(worker=None))
    service=ResearchLabService(runtime);row=service.start(a.kind,a.rounds,resume=a.resume)
    print(json.dumps(row),flush=True);service.worker.join()
    final=service.get(row['run_id'])['status'];print(json.dumps(final),flush=True)
    if final['state']!='completed':raise SystemExit(1)
