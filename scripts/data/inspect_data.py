"""Show a compact inventory and recent ETH metrics without network/model calls."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from research_data.sdk import ResearchData

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cutoff",help="ISO-8601 UTC cutoff; defaults to bundle cutoff")
parser.add_argument("--mode",choices=["observed","strict"],default="observed")
parser.add_argument("--dataset",default="analysis.eth_btc.daily")
args=parser.parse_args()
sdk=ResearchData(cutoff=args.cutoff,mode=args.mode)
try:
    inventory=sdk.list_datasets()
    data=sdk.load_dataset(args.dataset)
    print(json.dumps({"cutoff":inventory["cutoff"],"mode":inventory["mode"],"datasets":len(inventory["datasets"]),
                      "eligible_records":sum(d["eligible_records"] for d in inventory["datasets"]),"selected_dataset":args.dataset,
                      "latest_observation":data["records"][-1] if data["records"] else None},ensure_ascii=False,indent=2))
finally:sdk.close()
