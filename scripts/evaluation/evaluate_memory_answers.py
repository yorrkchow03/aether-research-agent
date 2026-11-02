"""Run twelve bounded frozen Deep Agents + Pro answers; no live research tools."""
import argparse
from pathlib import Path
from research_agent.memory_answer_evaluation import PairedMemoryAnswers
from research_agent.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    PairedMemoryAnswers(ROOT, Settings.load(ROOT), args.output).run()
