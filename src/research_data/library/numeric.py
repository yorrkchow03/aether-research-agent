"""Explicit fixed-window public collectors. Every value retains its actual input."""
from __future__ import annotations
import csv,io,json,math,statistics
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode
from pathlib import Path

from research_data.http import Snapshot,utcnow,dumps
from research_data.normalize import timestamp,iso,etf_flows
from .store import WINDOW_START,WINDOW_END,instant
from .http import PublicCapture
from .registry import CHAINS,install
from .time import timestamp


class NumericCollector:
 def __init__(self,library,*,refresh=False):
  self.library=library;self.http=PublicCapture(library,refresh=refresh);install(library)
  self.start=library.start;self.end=library.end

 def run(self,groups=None):
  groups=groups or ['candles','references','network','derivatives','defi','etf','macro','snapshot','derive']
  for group in groups:
   print('START '+group,flush=True)
   try:getattr(self,group)()
   except Exception as exc:
    self.library.job('group:'+group,'failed',error=type(exc).__name__+': '+str(exc)[:1000])
    print('FAILED '+group+' '+str(exc)[:180],flush=True)
   print('END '+group,flush=True)

 def job(self,name,fn):
  self.library.job(name,'running')
  try:
   details=fn() or {};self.library.job(name,'collected',**details)
   print('COLLECTED '+name,flush=True)
  except Exception as exc:
   self.library.job(name,'failed',error=type(exc).__name__+': '+str(exc)[:1200])
   print('GAP '+name+' '+str(exc)[:180],flush=True)

 def candles(self):
  for asset in ['ETH','BTC']:
   for hourly in [True,False]:
    dataset=f'market.coinbase.{asset.lower()}.'+('hourly' if hourly else 'daily')
    self.job(dataset,lambda a=asset,h=hourly,d=dataset:self._candles(a,h,d))

 def _candles(self,asset,hourly,dataset):
  seconds=3600 if hourly else 86400
  begin=self.start-timedelta(days=30 if hourly else 91)
  cursor=begin;seen={}
  while cursor<self.end:
   stop=min(self.end,cursor+timedelta(seconds=seconds*250))
   url='https://api.exchange.coinbase.com/products/'+asset+'-USD/candles?'+urlencode({'granularity':seconds,'start':iso(cursor),'end':iso(stop)})
   cid,rows=self.http.json('coinbase',url)
   if not isinstance(rows,list):raise ValueError('Expected candles array')
   for row in rows:
    t=timestamp(row[0]);period_end=t+timedelta(seconds=seconds)
    if t<begin or period_end>self.end:continue
    low,high,opening,close,volume=[Decimal(str(v)) for v in row[1:6]]
    if not all(v.is_finite() for v in [low,high,opening,close,volume]) or low<=0 or volume<0 or not low<=opening<=high or not low<=close<=high:raise ValueError('Invalid candle range')
    values=dict(zip(['low_usd','high_usd','open_usd','close_usd','volume_asset'],[low,high,opening,close,volume]))
    if t in seen and seen[t]!=values:raise ValueError('Conflicting overlapping candle')
    seen[t]=values
    self.library.put(dataset,t,values,cid,period_end=period_end,metadata={'warmup':t<self.start,'source_array_time':row[0]})
   cursor=stop
   self.library.job(dataset,'running',cursor=iso(cursor),warmup_start=iso(begin),unique_intervals=len(seen))
  expected=int((self.end-begin).total_seconds()/seconds)
  missing=[iso(begin+timedelta(seconds=i*seconds)) for i in range(expected) if begin+timedelta(seconds=i*seconds) not in seen]
  if missing:raise ValueError('Missing '+str(len(missing))+' requested candles; first '+str(missing[:5]))
  return {'source_range_complete':True,'warmup_intervals':expected-(self.library.hours if hourly else self.library.days),'cursor':iso(self.end)}

 def bitstamp(self):
  from .extensions import collect_bitstamp
  collect_bitstamp(self.library,self.http,self.job)

 def treasury(self):
  from .extensions import collect_treasury
  collect_treasury(self.library,self.http,self.job)

 def references(self):
  for asset,slug in [('ETH','ethereum'),('BTC','bitcoin')]:
   dataset='market.coingecko.'+asset.lower()+'.daily'
   def collect(a=asset,s=slug,d=dataset):
    url=f'https://api.coingecko.com/api/v3/coins/{s}/market_chart/range?'+urlencode({'vs_currency':'usd','from':int(self.start.timestamp()),'to':int(self.end.timestamp())})
    cid,obj=self.http.json('coingecko',url);points={}
    for source,target in [('prices','price_usd'),('market_caps','market_cap_usd'),('total_volumes','rolling_24h_volume_usd')]:
     for t,v in obj[source]:
      at=timestamp(Decimal(t)/1000)
      if at.hour==at.minute==at.second==0 and self.start<=at<self.end:points.setdefault(at,{})[target]=v
    for at,values in points.items():self.library.put(d,at,values,cid)
    return {'source_range_complete':len(points)==self.library.days,'timestamps':len(points)}
   self.job(dataset,collect)

 def cm(self,metric,asset='eth',warmup=0):
  url='https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?'+urlencode({'assets':asset,'metrics':metric,'frequency':'1d','start_time':(self.start-timedelta(days=warmup)).date().isoformat(),'end_time':(self.end-timedelta(seconds=1)).date().isoformat(),'page_size':1000})
  result={};seen=set()
  while url:
   if url in seen:raise ValueError('Coin Metrics pagination loop')
   seen.add(url);cid,obj=self.http.json('coinmetrics',url)
   for row in obj.get('data',[]):
    if row.get(metric) is not None:result[instant(row['time'])]=(row[metric],cid,{k:v for k,v in row.items() if k.endswith('-status') or k.endswith('-status-time')})
   url=obj.get('next_page_url')
  return result

 def network(self):
  groups={
   'network.coinmetrics.eth.daily':{'AdrActCnt':'active_address_count','TxCnt':'transaction_count','FeeTotNtv':'total_fees_eth','GasUsed':'gas_used','FeeMeanNtv':'mean_fee_eth'},
   'network.coinmetrics.eth.supply':{'SplyCur':'supply_eth','IssTotNtv':'gross_issuance_eth','SplyBurntNtv':'signed_burn_eth','SplyStkedNtv':'staked_eth','ValidatorActOngCnt':'active_validator_count'},
   'flows.coinmetrics.eth.daily':{'FlowInExNtv':'exchange_inflow_eth','FlowOutExNtv':'exchange_outflow_eth'},
  }
  for dataset,mapping in groups.items():
   def collect(d=dataset,m=mapping):
    values={};errors={};supply={}
    for metric,key in m.items():
     network='eth_cl' if metric in ('SplyStkedNtv','ValidatorActOngCnt') else 'eth'
     try:
      series=self.cm(metric,asset=network,warmup=1 if metric=='SplyCur' else 0)
      if metric=='SplyCur':supply=series
      for at,(value,cid,meta) in series.items():
       if timestamp(at)<self.start:continue
       item=values.setdefault(at,{'values':{},'captures':{},'provider_status':{}})
       item['values'][key]=value;item['captures'][key]=cid;item['provider_status'][key]=meta
     except Exception as exc:errors[metric]=network+': '+str(exc)
    if d.endswith('.supply'):
     for at,item in values.items():
      prev=instant(timestamp(at)-timedelta(days=1))
      if at in supply and prev in supply:item['values']['supply_change_eth']=str(Decimal(supply[at][0])-Decimal(supply[prev][0]))
    for at,item in values.items():
     cid=next(iter(item['captures'].values()))
     self.library.put(d,at,item['values'],cid,period_end=timestamp(at)+timedelta(days=1),metadata={'field_captures':item['captures'],'provider_status':item['provider_status'],'missing_metrics':errors})
    return {'source_range_complete':not errors,'missing_metrics':errors,'observations':len(values),'permission_required':any('403' in v for v in errors.values())}
   self.job(dataset,collect)
  self.library.job('network.ethereum.base_fee.daily','unresolved',reason='Daily execution base fee requires a verified historical aggregate source; total transaction fees or one block are not replacements',source_range_complete=False)

 def derivatives(self):
  self.job('derivatives.okx.eth.funding',self._funding)
  self.job('derivatives.okx.eth.oi.hourly',self._oi)

 def _funding(self):
  after=None;seen=set();rows={};oldest=None
  while True:
   params={'instId':'ETH-USDT-SWAP','limit':400}
   if after:params['after']=after
   url='https://www.okx.com/api/v5/public/funding-rate-history?'+urlencode(params)
   cid,obj=self.http.json('okx',url);items=obj['data']
   if not items:break
   new=min(int(r['fundingTime']) for r in items)
   if new in seen:raise ValueError('Funding pagination did not advance')
   seen.add(new)
   for item in items:
    at=timestamp(Decimal(item['fundingTime'])/1000)
    if self.start-timedelta(days=30)<=at<self.end:
     rows[at]=item
     self.library.put('derivatives.okx.eth.funding',at,{'funding_rate_fraction':item.get('fundingRate'),'realized_rate_fraction':item.get('realizedRate')},cid,metadata={'instrument':'ETH-USDT-SWAP','method':item.get('method'),'formula_type':item.get('formulaType'),'warmup':at<self.start})
   oldest=timestamp(Decimal(new)/1000)
   if oldest<self.start:break
   after=str(new)
  return {'source_range_complete':oldest is not None and oldest<self.start,'observations':len(rows),'actual_intervals_hours':sorted({(b-a).total_seconds()/3600 for a,b in zip(sorted(rows),sorted(rows)[1:])})}

 def _oi(self):
  begin=self.start-timedelta(hours=1);cursor=begin;found={}
  while cursor<self.end:
   stop=min(self.end,cursor+timedelta(hours=90))
   url='https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?'+urlencode({'instId':'ETH-USDT-SWAP','period':'1H','begin':int(cursor.timestamp()*1000),'end':int(stop.timestamp()*1000),'limit':100})
   cid,obj=self.http.json('okx',url)
   for row in obj.get('data',[]):
    at=timestamp(Decimal(row[0])/1000)
    if begin<=at<self.end:
     values={'oi_contracts':row[1],'oi_eth':row[2],'oi_usd':row[3]}
     if at in found and found[at][0]!=values:raise ValueError('OI overlapping values disagree')
     found[at]=(values,cid)
   cursor=stop
  # The endpoint can omit a boundary bucket. Repair only missing timestamps
  # with overlapping small windows; never interpolate open interest.
  missing=[begin+timedelta(hours=i) for i in range(self.library.hours+1) if begin+timedelta(hours=i) not in found]
  # Sparse omitted boundaries can be repaired. A large historical permission/
  # retention gap must not produce thousands of nearly identical retry calls.
  internal=[at for at in missing if found and min(found)<=at<=max(found)]
  candidates=missing if len(missing)<=24 else internal
  repair_missing=candidates if len(candidates)<=24 else []
  for at in repair_missing:
   url='https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?'+urlencode({'instId':'ETH-USDT-SWAP','period':'1H','begin':int((at-timedelta(hours=2)).timestamp()*1000),'end':int((at+timedelta(hours=2)).timestamp()*1000),'limit':10})
   cid,obj=self.http.json('okx',url)
   for row in obj.get('data',[]):
    t=timestamp(Decimal(row[0])/1000)
    if begin<=t<self.end:found[t]=({'oi_contracts':row[1],'oi_eth':row[2],'oi_usd':row[3]},cid)
  for at,(values,cid) in sorted(found.items()):
   if at<self.start:continue
   previous=found.get(at-timedelta(hours=1))
   if previous:
    before=Decimal(previous[0]['oi_usd']);after=Decimal(values['oi_usd'])
    if before>0:values={**values,'oi_change_usd':str(after-before),'oi_change_fraction':str(after/before-1)}
   self.library.put('derivatives.okx.eth.oi.hourly',at,values,cid,metadata={'instrument':'ETH-USDT-SWAP','comparison_interval_hours':1})
  return {'source_range_complete':len([t for t in found if t>=self.start])==self.library.hours,'observations':len(found),'unresolved_intervals':[iso(t) for t in missing if t>=self.start and t not in found],'repair_policy':'Only at most24 sparse omissions (or internal acquired-range omissions) get overlap retries; larger source-retention gaps remain explicit'}

 def defi(self):
  for chain in CHAINS:
   for field in ['tvl_usd','stablecoin_circulating_value_usd','dex_volume_usd']:
    dataset=f'ecosystem.{chain.lower()}.{field}'
    def collect(ch=chain,key=field,d=dataset):
     if key=='tvl_usd':
      cid,obj=self.http.json('defillama',f'https://api.llama.fi/v2/historicalChainTvl/{ch}');series=[(r['date'],r['tvl']) for r in obj]
     elif key=='stablecoin_circulating_value_usd':
      cid,obj=self.http.json('stablecoins',f'https://stablecoins.llama.fi/stablecoincharts/{ch}')
      series=[(r['date'],sum(Decimal(str(v)) for v in r['totalCirculatingUSD'].values())) for r in obj]
     else:
      cid,obj=self.http.json('defillama',f'https://api.llama.fi/overview/dexs/{ch}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true');series=obj['totalDataChart']
     count=0
     for t,value in series:
      at=timestamp(int(t))
      if self.start<=at<self.end:self.library.put(d,at,{key:value},cid,period_end=at+timedelta(days=1));count+=1
     return {'source_range_complete':count==self.library.days,'observations':count}
    self.job(dataset,collect)
  for dtype,key in [('dailyFees','fees_usd'),('dailyRevenue','revenue_usd')]:
   dataset='protocol.aave_v3.'+key
   def fees(dt=dtype,k=key,d=dataset):
    cid,obj=self.http.json('defillama','https://api.llama.fi/summary/fees/aave-v3?dataType='+dt);count=0
    for t,value in obj['totalDataChart']:
     at=timestamp(int(t))
     if self.start<=at<self.end:self.library.put(d,at,{k:value},cid,period_end=at+timedelta(days=1));count+=1
    return {'source_range_complete':count==self.library.days,'observations':count}
   self.job(dataset,fees)
  pools={'lido.steth':'747c1d2a-c668-4682-b9f9-296708a3dd90','aave_v3.weth':'e880e828-ca59-4ec6-8d4f-27182a4dc23d'}
  for name,pool in pools.items():
   def yields(n=name,p=pool):
    cid,obj=self.http.json('defillama','https://yields.llama.fi/chart/'+p);count=0
    for row in obj['data']:
     at=timestamp(row['timestamp'])
     if self.start<=at<self.end:
      self.library.put('yields.'+n,at,{'apy_percent':row.get('apy'),'apy_base_percent':row.get('apyBase'),'tvl_usd':row.get('tvlUsd')},cid,metadata={'pool_id':p});count+=1
    return {'source_range_complete':count>=self.library.days,'observations':count,'pool_id':p}
   self.job('yields.'+name,yields)

 def etf(self):
  def collect():
   url='https://farside.co.uk/ethereum-etf-flow-all-data/';cid,raw=self.http.fetch('farside',url)
   cap=self.library.capture_info(cid);snap=Snapshot({'source_id':cid,'retrieved_at':cap['retrieved_at']},raw)
   rows=etf_flows(snap);count=0
   for row in rows:
    at=timestamp(row['observed_at'])
    if self.start<=at<self.end:
     values=row['values'];totals=[v for k,v in values.items() if 'total' in k.lower()]
     if len(totals)!=1:raise ValueError('ETF total column ambiguous')
     self.library.put('flows.farside.eth_etf.daily',at,{'net_flow_usd':totals[0]},cid,period_end=at+timedelta(days=1),metadata={'fund_columns_usd':values});count+=1
   holidays={'2026-05-25','2026-06-19','2026-07-03','2026-09-07'}
   expected=[(self.start+timedelta(days=i)).date().isoformat() for i in range(self.library.days) if (self.start+timedelta(days=i)).weekday()<5 and (self.start+timedelta(days=i)).date().isoformat() not in holidays]
   return {'source_range_complete':count==len(expected),'observations':count,'expected_source_dates':expected,'calendar':'US ETF trading dates; Columbus Day remains an exchange trading day; no weekend zero-fill',
     'nonobservation_dates':[{'date':(self.start+timedelta(days=i)).date().isoformat(),'state':'legitimate_no_observation','reason':'weekend' if (self.start+timedelta(days=i)).weekday()>=5 else 'US equity exchange holiday'} for i in range(self.library.days) if (self.start+timedelta(days=i)).weekday()>=5 or (self.start+timedelta(days=i)).date().isoformat() in holidays]}
  self.job('flows.farside.eth_etf.daily',collect)

 def macro(self):
  from .repair import repair_public_sources,collect_calendar
  repair_public_sources(self.library,refresh=self.http.refresh)
  self.job('macro.federal_reserve.fomc.calendar',lambda:collect_calendar(self.library))

 def snapshot(self):
  def collect():
   cid,obj=self.http.json('coinbase','https://api.exchange.coinbase.com/products/ETH-USD/book?level=2',force=True)
   bids=obj['bids'][:25];asks=obj['asks'][:25];bid=Decimal(bids[0][0]);ask=Decimal(asks[0][0]);mid=(bid+ask)/2
   if bid<=0 or ask<bid or obj.get('auction_mode'):raise ValueError('Invalid/auction book')
   values={'best_bid_usd':bid,'best_ask_usd':ask,'spread_bps':(ask-bid)/mid*10000}
   for side,levels in [('bid',bids),('ask',asks)]:
    for bps in [10,50,100]:values[f'{side}_depth_{bps}bps_usd']=sum(Decimal(p)*Decimal(q) for p,q,*_ in levels if abs(Decimal(p)/mid-1)*10000<=bps)
   remaining=Decimal(1000);quantity=Decimal(0)
   for p,q,*_ in asks:
    p=Decimal(p);take=min(Decimal(q),remaining/p);remaining-=take*p;quantity+=take
   vwap=(Decimal(1000)-remaining)/quantity if quantity else None
   values.update(estimated_vwap_1000usd=vwap,slippage_1000usd_bps=(vwap/ask-1)*10000 if vwap else None,covered_notional_1000usd=Decimal(1000)-remaining)
   bands={str(bps):{'bid_band_fully_spanned':Decimal(bids[-1][0])<=mid*(1-Decimal(bps)/10000),'ask_band_fully_spanned':Decimal(asks[-1][0])>=mid*(1+Decimal(bps)/10000)} for bps in (10,50,100)}
   self.library.put('execution.coinbase.eth.snapshot',self.library.capture_info(cid)['retrieved_at'],values,cid,metadata={'levels_per_side':25,'scenario_notional_usd':'1000','uncovered_notional_usd':str(remaining),'not_a_historical_snapshot':True,'depth_band_coverage':bands,'depth_scope':'Reported depth is a top-25 lower bound when a band is not fully spanned','scenario_fully_covered':remaining<=0})
   return {'source_range_complete':True,'snapshot_only':True}
  self.job('execution.coinbase.eth.snapshot',collect)

 def derive(self):
  from .derive import derive_all
  derive_all(self.library,self.job)
  from .repair import derive_mean_fee
  derive_mean_fee(self.library)
  available={r['dataset_id'] for r in self.library.datasets()}
  if {'network.etherscan.gas.daily','network.etherscan.burn.daily'}<=available:
   from .ratio_proxy import derive_proxy
   derive_proxy(self.library)

 def execution_headers(self):
  from .headers import HeaderBackfill
  return HeaderBackfill(self.library).run()

 def fee_burn(self):
  from .fees import collect
  return collect(self.library,http=self.http)
