"""Repair only sparse holes inside acquired OKX history; never probe old hours."""
import json
from pathlib import Path
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode
from research_data.library.store import ResearchLibrary,instant
from research_data.library.http import PublicCapture
from research_data.library.time import timestamp

PROJECT=Path(__file__).resolve().parents[2]

def main():
 lib=ResearchLibrary(PROJECT/'data/library-halfyear-20260426-20261026',create=False);http=PublicCapture(lib);dataset='derivatives.okx.eth.oi.hourly'
 rows=lib.series(dataset,limit=10000)['records'];found={r['observed_at']:r for r in rows}
 if not rows:raise ValueError('No acquired history; do not launch one-request-per-missing-hour probes')
 first=timestamp(rows[0]['observed_at']);last=timestamp(rows[-1]['observed_at'])
 internal=[first+timedelta(hours=i) for i in range(int((last-first).total_seconds()/3600)+1) if instant(first+timedelta(hours=i)) not in found]
 if len(internal)>24:raise ValueError('Internal omissions exceed sparse bounded repair policy')
 requested=[];captured={};revisions=[]
 for at in internal:
  url='https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?'+urlencode({'instId':'ETH-USDT-SWAP','period':'1H','begin':int((at-timedelta(hours=2)).timestamp()*1000),'end':int((at+timedelta(hours=2)).timestamp()*1000),'limit':10})
  result={'missing_at':instant(at),'url':url}
  try:
   cid,obj=http.json('okx',url);result.update(capture_id=cid,source_rows=len(obj.get('data',[])))
   for row in obj.get('data',[]):
    t=instant(Decimal(row[0])/1000)
    if not lib.start<=timestamp(t)<lib.end:continue
    values=dict(zip(['oi_contracts','oi_eth','oi_usd'],row[1:4]))
    if t in found and any(Decimal(str(values[k]))!=Decimal(found[t]['values'][k]) for k in values):revisions.append({'observed_at':t,'previous_record_id':found[t]['record_id'],'capture_id':cid})
    captured[t]=(values,cid)
  except Exception as exc:result['error']=type(exc).__name__+': '+str(exc)[:500]
  requested.append(result)
 # Same-response or older previously acquired inputs only. Both input source IDs
 # are retained; later provider responses never backdate a revised calculation.
 for t,(values,cid) in sorted(captured.items()):
  prev=instant(timestamp(t)-timedelta(hours=1));previous=captured.get(prev)
  if not previous and prev in found:previous=(found[prev]['values'],found[prev]['library_capture_id'])
  metadata={'instrument':'ETH-USDT-SWAP','comparison_interval_hours':1,'repair':'sparse_internal_overlap_only'}
  if previous and Decimal(previous[0]['oi_usd'])>0:
   before=Decimal(previous[0]['oi_usd']);after=Decimal(values['oi_usd']);values={**values,'oi_change_usd':after-before,'oi_change_fraction':after/before-1};metadata['change_prior_capture_id']=previous[1]
   if lib.capture_info(previous[1])['retrieved_at']>lib.capture_info(cid)['retrieved_at']:raise ValueError('Change calculation would depend on a later acquired prior value')
  lib.put(dataset,t,values,cid,metadata=metadata)
 actual=lib.series(dataset,limit=10000)['records'];times={r['observed_at'] for r in actual}
 missing=[instant(lib.start+timedelta(hours=i)) for i in range(lib.hours) if instant(lib.start+timedelta(hours=i)) not in times]
 details={'source_range_complete':not missing,'observations':len(actual),'unresolved_intervals':missing,'repair_policy':'At most24 internal omission windows; all earlier retention gaps remain unprobed','acquired_first':actual[0]['observed_at'],'acquired_last':actual[-1]['observed_at']}
 lib.job(dataset,'collected',**details)
 result={'internal_gaps_before':len(internal),'internal_gaps_remaining':sum(first<=timestamp(t)<=last for t in missing),'formal_observations':len(actual),'expected':lib.hours,'old_history_unprobed':True,'requests':requested,'provider_revisions':revisions,'details':details}
 out=PROJECT/'local/halfyear/data/OI_INTERNAL_REPAIR.json';out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ('details','requests','provider_revisions')}))

if __name__=='__main__':main()
