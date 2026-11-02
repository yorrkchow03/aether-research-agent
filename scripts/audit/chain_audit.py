"""Use .audit-venv/bin/python scripts/audit/chain_audit.py; no Ark key is needed."""
import argparse,json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
from research_agent.chain_anchor import wallet_setup,SepoliaAnchor
p=argparse.ArgumentParser();p.add_argument('action',choices=['setup','status','deploy','anchor','verify']);p.add_argument('--bundle',type=Path)
p.add_argument('--reporter');p.add_argument('--contract');args=p.parse_args()
try:
 if args.action=='setup':result=wallet_setup(root)
 else:
  client=SepoliaAnchor(root)
  if args.action=='status':result=client.connection()
  elif args.action=='deploy':result=client.deploy()
  elif args.action=='anchor':
   if not args.bundle:p.error('--bundle required')
   result=client.anchor(args.bundle)
  else:
   if not all((args.bundle,args.reporter,args.contract)):p.error('--bundle, --reporter and --contract required')
   result=client.verify(args.bundle,args.reporter,args.contract)
 print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception as exc:
 print(json.dumps({'status':'incomplete','error':str(exc)[:600]},ensure_ascii=False));raise SystemExit(1)
