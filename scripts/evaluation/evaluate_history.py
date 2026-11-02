"""Run a predeclared historical Pro pilot. Raw drafts are not product recommendations."""
import argparse,json
from pathlib import Path
from research_data.http import utcnow
from research_agent.evaluation import EvaluationRunner
from research_agent.settings import Settings

root=Path(__file__).resolve().parents[2];p=argparse.ArgumentParser()
p.add_argument('--cases',type=int,default=12);p.add_argument('--workers',type=int,default=2)
p.add_argument('--output',type=Path)
args=p.parse_args()
if not 1<=args.workers<=2:p.error('workers must be1or2')
settings=Settings.load(root)
if settings.model_id!='doubao-seed-2.1-pro':p.error('This evaluation requires the configured Pro model; no Lite fallback')
output=args.output or root/'data/evaluations'/utcnow().replace(':','').replace('-','').replace('.','')
runner=EvaluationRunner(root,settings,output,args.cases,args.workers)
print(json.dumps({'output':str(output),'planned_cases':args.cases,'config_hash':runner.config_hash}),flush=True)
result=runner.run()
print(json.dumps({'state':'completed','splits':result['splits'],'output':str(output)},ensure_ascii=False),flush=True)
