"""Offline verification: python scripts/audit/replay_bundle.py <bundle> --execute-recipes."""
import argparse,json
from pathlib import Path
from research_agent.replay import replay_bundle
from research_agent.settings import Settings

parser=argparse.ArgumentParser()
parser.add_argument('bundle',type=Path)
parser.add_argument('--expected',type=Path,help='Authenticated expected digests, e.g. verified chain receipt')
parser.add_argument('--execute-recipes',action='store_true',help='Run the saved model-written Python locally')
args=parser.parse_args()
expected=json.loads(args.expected.read_text()) if args.expected else None
settings=Settings()  # Offline verifier needs no API credential.
result=replay_bundle(args.bundle,settings,expected,args.execute_recipes)
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if result['status']=='passed' else 1)
