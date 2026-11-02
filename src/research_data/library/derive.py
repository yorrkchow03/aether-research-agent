"""Reproducible prefix-only descriptive calculations over captured observations."""
from __future__ import annotations
import hashlib,json,math,statistics
from datetime import timedelta
from decimal import Decimal
from .store import WINDOW_START,WINDOW_END
from research_data.http import dumps
from research_data.normalize import timestamp

VERSION='library-descriptive-v1'

def contiguous_lookback(rows,index,hourly):
 needed=720 if hourly else 30
 if index<needed:return False
 step=timedelta(hours=1) if hourly else timedelta(days=1)
 sample=rows[index-needed:index+1]
 return all(timestamp(b['observed_at'])-timestamp(a['observed_at'])==step for a,b in zip(sample,sample[1:]))

def feature_contracts(library):
 output=[(f'analysis.{a}.{k}',f'market.coinbase.{a}.{k}',h) for a in ('eth','btc') for k,h in [('hourly',True),('daily',False)]]
 for contract in library.datasets():
  if contract.get('feature_input_dataset'):
   output.append((contract['dataset_id'],contract['feature_input_dataset'],contract['frequency']=='1h'))
 return output

def feature_values(rows,index,hourly):
 factor=24 if hourly else 1
 closes=[float(row['values']['close_usd']) for row in rows[:index+1]]
 volumes=[float(row['values']['volume_asset']) for row in rows[:index+1]]
 needed=30*factor
 if len(closes)<=needed:raise ValueError('Missing feature warmup')
 def ret(n):return closes[-1]/closes[-1-n]-1
 def vol(n):
  sample=closes[-n-1:];returns=[math.log(b/a) for a,b in zip(sample,sample[1:])]
  return statistics.stdev(returns)*math.sqrt(365*factor)
 def dd(n):
  peak=0.;draw=0.
  for value in closes[-n-1:]:
   peak=max(peak,value);draw=min(draw,value/peak-1)
  return draw
 last=rows[index]['values'];output={'max_drawdown_30d':dd(30*factor),'range_fraction':float(Decimal(last['high_usd'])/Decimal(last['low_usd'])-1)}
 if hourly:
  output.update({f'return_{name}':ret(n) for name,n in [('1h',1),('24h',24),('7d',168),('30d',720)]})
  output.update(realized_vol_7d=vol(168),distance_sma24=closes[-1]/statistics.mean(closes[-24:])-1,distance_sma168=closes[-1]/statistics.mean(closes[-168:])-1,
                volume_vs_prior24=volumes[-1]/statistics.mean(volumes[-25:-1])-1 if statistics.mean(volumes[-25:-1]) else None)
 else:
  output.update({f'return_{n}d':ret(n) for n in [1,3,7,21,30]})
  output.update(realized_vol_7d=vol(7),realized_vol_21d=vol(21),distance_sma21=closes[-1]/statistics.mean(closes[-21:])-1,
                volume_vs_prior20=volumes[-1]/statistics.mean(volumes[-21:-1])-1 if statistics.mean(volumes[-21:-1]) else None)
 return output

def recipe_capture(library,name,inputs):
 source=Path(__file__).read_bytes()
 manifest={'version':VERSION,'implementation_sha256':hashlib.sha256(source).hexdigest(),
           'input_ids':inputs,'method':'Saved captured prefixes only. Source implementation is separately content-addressed.'}
 code_sha=library.object(source);manifest['implementation_object']=code_sha
 return library.capture('calculation:'+VERSION,'urn:aether:calculation:'+name,dumps(manifest,sort_keys=True).encode()),manifest

from pathlib import Path
from .time import timestamp

def derive_all(library,job):
 for dataset,source_dataset,hourly in feature_contracts(library):
   def collect(h=hourly,d=dataset,source=source_dataset):
    start=library.start-timedelta(days=30 if h else 91)
    rows=library.series(source,start=start,limit=10000)['records']
    cid,manifest=recipe_capture(library,d,[row['record_id'] for row in rows]);count=0;skipped=[]
    for i,row in enumerate(rows):
     if timestamp(row['observed_at'])<library.start:continue
     if not contiguous_lookback(rows,i,h):
      skipped.append(row['observed_at']);continue
     values=feature_values(rows,i,h)
     library.put(d,row['observed_at'],values,cid,period_end=row['period_end'],input_ids=[row['record_id']],metadata={'calculation_version':VERSION,'source_dataset':source,'input_manifest_sha256':hashlib.sha256(library.raw(cid)).hexdigest(),'input_prefix_end':row['record_id'],'lookback_intervals':720 if h else 30,'no_future_inputs':True});count+=1
    return {'source_range_complete':count==(library.hours if h else library.days),'observations':count,'calculation_version':VERSION,'source_dataset':source,'skipped_incomplete_lookback':skipped,'policy':'Only consecutive native-frequency lookbacks; no bar-count shifting or interpolated prices'}
   job(dataset,collect)
 def relative():
  start=library.start-timedelta(days=91)
  eth=library.series('market.coinbase.eth.daily',start=start,limit=1000)['records'];btc=library.series('market.coinbase.btc.daily',start=start,limit=1000)['records']
  index={r['observed_at']:r for r in btc}
  if any(r['observed_at'] not in index for r in eth):raise ValueError('Relative inputs must use identical dates')
  ratios=[Decimal(r['values']['close_usd'])/Decimal(index[r['observed_at']]['values']['close_usd']) for r in eth]
  cid,_=recipe_capture(library,'analysis.eth_btc.daily',[r['record_id'] for r in eth+btc]);count=0
  for i,row in enumerate(eth):
   if timestamp(row['observed_at'])<library.start:continue
   if i<90:raise ValueError('Missing 90-return correlation warmup')
   x=[float(Decimal(eth[j]['values']['close_usd'])/Decimal(eth[j-1]['values']['close_usd'])-1) for j in range(i-89,i+1)]
   y=[float(Decimal(index[eth[j]['observed_at']]['values']['close_usd'])/Decimal(index[eth[j-1]['observed_at']]['values']['close_usd'])-1) for j in range(i-89,i+1)]
   values={'eth_btc_ratio':ratios[i],'relative_return_7d':ratios[i]/ratios[i-7]-1,'relative_return_30d':ratios[i]/ratios[i-30]-1,'return_correlation_90d':statistics.correlation(x,y)}
   library.put('analysis.eth_btc.daily',row['observed_at'],values,cid,period_end=row['period_end'],input_ids=[row['record_id'],index[row['observed_at']]['record_id']],metadata={'calculation_version':VERSION,'lookback_returns':90});count+=1
  return {'source_range_complete':count==library.days}
 job('analysis.eth_btc.daily',relative)
 def funding():
  rows=library.series('derivatives.okx.eth.funding',start=library.start-timedelta(days=30),limit=1000)['records']
  cid,_=recipe_capture(library,'analysis.eth.funding',[r['record_id'] for r in rows]);history={};count=0
  for i,row in enumerate(rows):
   if i==0:continue
   interval=(timestamp(row['observed_at'])-timestamp(rows[i-1]['observed_at'])).total_seconds()
   if interval<=0:raise ValueError('Invalid funding intervals')
   rate=float(row['values']['realized_rate_fraction']);group=history.setdefault(interval,[]);group.append(rate)
   if timestamp(row['observed_at'])<library.start:continue
   values={'latest_settled_rate_fraction':rate,'median_settled_rate_fraction':statistics.median(group),'empirical_percentile_fraction':sum(v<=rate for v in group)/len(group)}
   library.put('analysis.eth.funding',row['observed_at'],values,cid,input_ids=[row['record_id']],metadata={'calculation_version':VERSION,'settlement_interval_hours':interval/3600,'prefix_observations':len(group)});count+=1
  return {'source_range_complete':count==len([r for r in rows if timestamp(r['observed_at'])>=library.start]),'observations':count}
 job('analysis.eth.funding',funding)
