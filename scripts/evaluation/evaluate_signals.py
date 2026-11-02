#!/usr/bin/env python3
"""Evaluate the frozen real signal corpus, offline; output directories never overwrite."""
import argparse
from pathlib import Path
from research_agent.research_lab.evaluation import run_evaluation
ROOT=Path(__file__).resolve().parents[2]
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,default=ROOT/'data/validation/signals_v1')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--manifest-sha256')
    a=p.parse_args();r=run_evaluation(a.corpus,a.output,expected_manifest_sha256=a.manifest_sha256)
    print({k:r[k] for k in ('completed_cases','planned_cases','elapsed_seconds','failures','network_calls','model_api_calls')})
