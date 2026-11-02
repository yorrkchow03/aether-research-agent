"""Refresh public ETH data; does not invoke an LLM or trading API."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from research_data.pipeline import Preparation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat(), help="UTC cutoff date, YYYY-MM-DD; complete days only")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    result = Preparation(args.as_of, args.days).run()
    print(json.dumps({"status": result["status"], "run_dir": result["run_dir"], "datasets": len(result["datasets"]),
                      "records": sum(d["record_count"] for d in result["datasets"]), "unavailable": [j["name"] for j in result["jobs"] if j["status"] != "ok"]}, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
