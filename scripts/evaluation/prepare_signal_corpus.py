"""Explicit bounded real-data capture for the optional research lab; no LLM."""
import argparse
import json
from pathlib import Path

from research_agent.live import LiveData
from research_agent.research_lab.corpus import prepare_corpus

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol',type=Path,default=ROOT/'examples/evaluation/signal_protocol_v1.json')
    parser.add_argument('--output',type=Path,default=ROOT/'data/validation/signals_v1')
    args=parser.parse_args()
    result=prepare_corpus(args.output,json.loads(args.protocol.read_text()),LiveData(ROOT/'data/live'))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
