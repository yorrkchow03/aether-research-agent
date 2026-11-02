import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from research_data.verify import verify_bundle

parser=argparse.ArgumentParser(description="Verify downloaded research data, no network calls")
parser.add_argument("--run-dir")
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
run_dir=args.run_dir or json.loads((root/"data/latest.json").read_text())["run_dir"]
result=verify_bundle(run_dir)
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if result["status"]=="passed" else 1)
