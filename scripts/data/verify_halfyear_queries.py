"""Actual source-linked half-year price/text/evidence/calculation integration."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from decimal import Decimal
from research_data.library.store import ResearchLibrary

PROJECT=Path(__file__).resolve().parents[2]

def verify(root):
 lib=ResearchLibrary(root,create=False)
 if (lib.window_start,lib.window_end)!=('2026-04-26T00:00:00Z','2026-10-26T00:00:00Z'):raise ValueError('Not the fixed half-year research window')
 details=[]
 for asset in ('eth','btc'):
  price=lib.series(f'market.bitstamp.{asset}.hourly',limit=10000)
  assert price['total']==4392
  inputs=lib.series(f'market.bitstamp.{asset}.hourly',start='2026-03-27T00:00:00Z',limit=10000)['records']
  assert len(inputs)==5112
  ids={row['observed_at']:i for i,row in enumerate(inputs)}
  features=lib.series(f'analysis.bitstamp.{asset}.hourly',limit=10000)
  assert features['total']==4392
  samples=[]
  for row in (features['records'][0],features['records'][len(features['records'])//2],features['records'][-1]):
   i=ids[row['observed_at']];now=inputs[i];before=inputs[i-24]
   computed=Decimal(now['values']['close_usd'])/Decimal(before['values']['close_usd'])-1
   assert math.isclose(float(computed),float(row['values']['return_24h']),rel_tol=1e-12,abs_tol=1e-12)
   for parent in (now,before):assert hashlib.sha256(lib.raw(parent['library_capture_id'])).hexdigest()==parent['source']['sha256']
   samples.append({'at':row['observed_at'],'feature_id':row['record_id'],'close_evidence_ids':[now['record_id'],before['record_id']],'return_24h_recomputed_decimal':str(computed),'venue':'Bitstamp','quoted_currency':'USD'})
  details.append({'dataset':price['dataset']['dataset_id'],'formal':price['total'],'warmup':len(inputs)-price['total'],'samples':samples})
 docs=lib.search_documents('Ethereum',source='ethereum',limit=10)
 assert docs['total']>0
 evidence=lib.evidence(docs['results'][0]['record_id']);raw=lib.raw(evidence['library_capture_id'])
 assert len(evidence['text'])>=200 and hashlib.sha256(raw).hexdigest()==evidence['source']['sha256']
 strict=lib.search_documents('Ethereum',source='ethereum',mode='strict',known_by=lib.window_end,limit=100)
 for row in strict['results']:assert row['available_at']<=lib.window_end
 with lib.connect(readonly=True) as c:
  references=[r[0] for r in c.execute('SELECT DISTINCT record_id FROM output_citations')]
  outputs=c.execute('SELECT count(*) FROM research_outputs').fetchone()[0]
 assert all(lib.contains(rid) for rid in references)
 return {'passed':True,'window':[lib.window_start,lib.window_end],'hourly_reference_checks':details,
   'news_search':{'source':'ethereum','matches':docs['total'],'selected_evidence_id':evidence['record_id'],'url':evidence['url'],'source_sha256':evidence['source']['sha256'],'original_text_characters':len(evidence['text']),'published_at':evidence['published_at'],'acquired_at':evidence['available_at'],'strict_cutoff':lib.window_end,'strict_matches':strict['total']},
   'actual_saved_outputs':outputs,'resolved_unique_output_citations':len(references),'scope':'Real historical source observations and original texts captured retrospectively; strict local-acquisition cutoff kept separate. No model or forecast-quality claim.'}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=PROJECT/'data/library-halfyear-20260426-20261026');p.add_argument('--output',type=Path,default=PROJECT/'local/halfyear/data/QUERY_INTEGRATION.json');a=p.parse_args();result=verify(a.root);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'passed':result['passed'],'datasets':len(result['hourly_reference_checks']),'source_evidence_id':result['news_search']['selected_evidence_id'],'outputs':result['actual_saved_outputs'],'resolved_citations':result['resolved_unique_output_citations']}))
