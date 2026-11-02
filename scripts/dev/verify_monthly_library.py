"""Real captured-library API/ResearchClient/terminal/citation smoke; no LLM calls."""
import argparse,json,threading,hashlib
from pathlib import Path
from fastapi.testclient import TestClient
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app
from research_agent.tools import ResearchTools
from research_agent.terminal import ResearchShell
from research_agent.sdk_bridge import ResearchSDKServer
from research_agent.research_sdk import ResearchClient

ROOT=Path(__file__).resolve().parents[2]

def verify(destination):
 destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
 runtime=AgentRuntime(ROOT,state_dir=destination/'isolated-runtime')
 if runtime.library is None:raise ValueError('Build the real library first')
 session=runtime.create_session('Real monthly library integration',research_mode='current')
 workspace=runtime._session_dir(session['session_id'])/'workspace';cancel=threading.Event()
 shell=ResearchShell(workspace,runtime.settings,cancel)
 service=ResearchTools(Path(session['data_run']),workspace,session['cutoff'],allow_live=True,library=runtime.library,knowledge=runtime.knowledge,live_data=runtime.live_data,shell=shell)
 sdk=ResearchSDKServer(service,runtime.settings,cancel)
 try:
  path=sdk.start();shell.attach_research_sdk(path);client=ResearchClient(path)
  bars=client.library_series('market.coinbase.eth.hourly',start='2026-10-25T00:00:00Z',end='2026-10-25T02:00:00Z',limit=2)
  assert bars['total']==2
  news=client.library_search('',source='ethereum',limit=1);assert news['total']>0
  page=runtime.library.evidence(news['results'][0]['record_id']);assert page['text'] and page['raw_path']
  memory=client.call('memory-news',query=page['title'],asset='ETH',limit=30,budget=20000,original_only=True)
  assert page['record_id'] in [r['record_id'] for r in memory['results']]
  old_mode,old_cutoff=service.mode,service.cutoff
  try:
   service.mode='strict';service.cutoff='2026-10-26T00:00:00Z'
   past=client.call('memory-news',query=page['title'],cutoff='2030-01-01',budget=20000)
   assert page['record_id'] not in [r['record_id'] for r in past['results']]
  finally:service.mode,service.cutoff=old_mode,old_cutoff
  first,last=bars['records'];recipe=workspace/'notes/monthly_return.py'
  recipe.write_text('import json,sys\nfrom decimal import Decimal\na,b=[json.load(open(p))["values"]["close_usd"] for p in sys.argv[1:]]\nprint(json.dumps({"values":{"return_fraction":str(Decimal(b)/Decimal(a)-1)},"units":{"return_fraction":"fraction"}}))\n')
  # Preserve the redacted subprocess response if an intermittent host failure occurs.
  execute=shell.execute
  def captured_execute(command,**kwargs):
   result=execute(command,**kwargs)
   (destination/'TERMINAL_RESULT.json').write_text(json.dumps({'exit_code':result.exit_code,'truncated':result.truncated,'output':result.output},indent=2)+'\n')
   return result
  shell.execute=captured_execute
  calculation=service.register_calculation('/notes/monthly_return.py',[first['record_id'],last['record_id']],'Saved Coinbase close-to-close one-hour return; descriptive, not a strategy forecast')
  citation=service.evidence(calculation['record_id']);assert citation['record']['input_record_ids']==[first['record_id'],last['record_id']]
  client_api=TestClient(create_app(runtime),base_url='http://127.0.0.1:8765')
  response=client_api.get('/api/library/series',params={'dataset_id':'market.coinbase.eth.hourly'});assert response.status_code==200 and response.json()['total']==720
  source=client_api.get('/api/evidence/'+page['record_id']).json();assert source['raw_verified']
  strict=client_api.get('/api/library/series',params={'dataset_id':'market.coinbase.eth.hourly','mode':'strict'}).json();assert strict['total']==0
  receipt={'passed':True,'scope':'Real captured data, existing HTTP API and job-scoped ResearchClient Unix socket, actual terminal computation and evidence/input/raw-source resolution. No model generation or report-publication quality is claimed.',
           'hourly_count':response.json()['total'],'input_ids':[first['record_id'],last['record_id']],'calculation_id':calculation['record_id'],'values':calculation['values'],'news_id':page['record_id'],'news_url':page['url'],'news_raw_sha256':page['source']['sha256'],'strict_window_count':strict['total'],'current_memory_contains_library_source':True,'strict_memory_excludes_later_library_source':True,'session_id':session['session_id'],'llm_calls':0}
  from research_data.library.fees import DATASET
  if any(r['dataset_id']==DATASET for r in runtime.library.datasets()):
   rows=client.library_series(DATASET,limit=1);assert rows['total']==30
   fee=rows['records'][0];record=client.call('library-evidence',record_id=fee['record_id'])
   assert record['record']['input_record_ids'] and record['record']['values']==fee['values']
   from decimal import Decimal
   v=fee['values'];assert Decimal(v['total_fee_burn_eth'])==Decimal(v['execution_base_fee_burn_eth'])+Decimal(v['blob_fee_burn_eth'])
   receipt['native_fee_burn']={'daily_records':rows['total'],'record_id':fee['record_id'],'input_count':len(record['record']['input_record_ids']),'values':v}
  (destination/'RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt
 finally:sdk.close()

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'local/data-library/month-20260926-20261026/integration');args=p.parse_args();print(json.dumps(verify(args.output),indent=2))
