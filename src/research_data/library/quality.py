"""Independent publisher-response comparisons for the monthly data audit."""
import csv,io,json,math,statistics
from decimal import Decimal
from datetime import datetime,timedelta,timezone
from research_data.normalize import timestamp,etf_flows
from research_data.http import Snapshot
from .store import instant
from .time import timestamp

class SourceAudit:
 def __init__(self,library):
  from .header_quality import HeaderAudit
  self.library=library;self.cache={};self.index={};self.headers=HeaderAudit(library)
 def raw(self,cid):return self.library.raw(cid)
 def obj(self,cid):
  if cid not in self.cache:self.cache[cid]=json.loads(self.raw(cid),parse_float=Decimal)
  return self.cache[cid]
 def indexed(self,cid,kind):
  key=(cid,kind)
  if key in self.index:return self.index[key]
  obj=self.obj(cid)
  if kind=='candles':out={instant(r[0]):dict(zip(['low_usd','high_usd','open_usd','close_usd','volume_asset'],r[1:6])) for r in obj}
  elif kind=='bitstamp':out={instant(int(r['timestamp'])):{**{k+'_usd':r[k] for k in ('open','high','low','close')},'volume_asset':r['volume']} for r in obj['data']['ohlc']}
  elif kind=='cg':
   out={}
   for source,target in [('prices','price_usd'),('market_caps','market_cap_usd'),('total_volumes','rolling_24h_volume_usd')]:
    for t,v in obj[source]:out.setdefault(instant(Decimal(t)/1000),{})[target]=v
  elif kind=='cm':out={instant(r['time']):r for r in obj['data']}
  elif kind=='oi':out={instant(Decimal(r[0])/1000):dict(zip(['oi_contracts','oi_eth','oi_usd'],r[1:4])) for r in obj['data']}
  elif kind=='funding':out={instant(Decimal(r['fundingTime'])/1000):{'funding_rate_fraction':r['fundingRate'],'realized_rate_fraction':r['realizedRate']} for r in obj['data']}
  elif kind=='tvl':out={instant(r['date']):{'tvl_usd':r['tvl']} for r in obj}
  elif kind=='stable':out={instant(r['date']):{'stablecoin_circulating_value_usd':sum(Decimal(str(v)) for v in r['totalCirculatingUSD'].values())} for r in obj}
  elif kind=='yield':out={instant(r['timestamp']):dict(zip(['apy_percent','apy_base_percent','tvl_usd'],[r.get('apy'),r.get('apyBase'),r.get('tvlUsd')])) for r in obj['data']}
  elif kind=='effr':out={instant(r['effectiveDate']):{'rate_percent':r['percentRate']} for r in obj['refRates'] if r['type']=='EFFR'}
  else:raise ValueError('Unknown source comparator')
  self.index[key]=out;return out
 def compare(self,row):
  d=row['dataset_id'];cid=row['library_capture_id'];at=row['observed_at'];v=row['values'];meta=row.get('library_metadata',{});expected={}
  if d=='analysis.eth.base_fee_ratio_proxy.daily':
   from .ratio_proxy import audit_proxy
   audit_proxy(self.library,row);return True
  if d.startswith('analysis.'):return False
  if d=='network.ethereum.fee_burn.daily':
   from .fees import audit_row
   audit_row(self.headers,row);return True
  if d in ('input.ethereum.execution_headers','network.ethereum.execution.daily','network.ethereum.base_fee.daily'):
   self.headers.compare(row);return True
  if d.startswith('market.bitstamp.'):expected=self.indexed(cid,'bitstamp')[at]
  elif d.startswith('market.coinbase.'):expected=self.indexed(cid,'candles')[at]
  elif d.startswith('market.coingecko.'):expected=self.indexed(cid,'cg')[at]
  elif '.coinmetrics.' in d:
   mapping={'active_address_count':'AdrActCnt','transaction_count':'TxCnt','total_fees_eth':'FeeTotNtv','supply_eth':'SplyCur','gross_issuance_eth':'IssTotNtv','signed_burn_eth':'SplyBurntNtv','staked_eth':'SplyStkedNtv','active_validator_count':'ValidatorActOngCnt','gas_used':'GasUsed','mean_fee_eth':'FeeMeanNtv','exchange_inflow_eth':'FlowInExNtv','exchange_outflow_eth':'FlowOutExNtv'}
   for field,fcid in meta['field_captures'].items():expected[field]=self.indexed(fcid,'cm')[at][mapping[field]]
   if 'supply_change_eth' in v:
    series=self.indexed(meta['field_captures']['supply_eth'],'cm');before=instant(timestamp(at)-timedelta(days=1));expected['supply_change_eth']=Decimal(series[at]['SplyCur'])-Decimal(series[before]['SplyCur'])
  elif d.endswith('.oi.hourly'):expected=self.indexed(cid,'oi')[at]
  elif d=='derivatives.okx.eth.funding':expected=self.indexed(cid,'funding')[at]
  elif d.startswith('ecosystem.'):
   field=d.split('.')[-1]
   if field=='tvl_usd':expected=self.indexed(cid,'tvl')[at]
   elif field=='stablecoin_circulating_value_usd':expected=self.indexed(cid,'stable')[at]
   else:expected={field:dict(self.obj(cid)['totalDataChart'])[int(timestamp(at).timestamp())]}
  elif d.startswith('protocol.aave_v3.'):
   field=d.split('.')[-1];expected={field:dict(self.obj(cid)['totalDataChart'])[int(timestamp(at).timestamp())]}
  elif d.startswith('yields.'):expected=self.indexed(cid,'yield')[at]
  elif d=='macro.federal_reserve.fomc.calendar':
   import re
   key=(cid,'fomc')
   if key not in self.index:
    html=self.raw(cid).decode(errors='replace');section=re.search(r'2026 FOMC Meetings</a>(.*?)2025 FOMC Meetings</a>',html,re.S)
    if not section:raise ValueError('Original FOMC year section unavailable')
    meetings={}
    for month,dates in re.findall(r'fomc-meeting__month[^>]*><strong>(\w+)</strong></div>\s*<div[^>]*fomc-meeting__date[^>]*>([\d\-*]+)</div>',section[1]):
     days=re.findall(r'\d+',dates)
     begin=datetime.strptime(f'{month} {days[0]} 2026','%B %d %Y').replace(tzinfo=timezone.utc)
     end=datetime.strptime(f'{month} {days[-1]} 2026','%B %d %Y').replace(tzinfo=timezone.utc)+timedelta(days=1)
     meetings[instant(begin)]=instant(end)
    if len(meetings)!=8:raise ValueError('Unexpected annual FOMC listing count')
    self.index[key]=meetings
   if self.index[key].get(at)!=row['period_end']:raise ValueError('FOMC meeting range differs from original calendar')
   expected={'scheduled_meeting':1}
  elif d=='macro.fred.dff':expected=self.indexed(cid,'effr')[at]
  elif d in ('macro.fred.dgs2','macro.fred.dgs10'):
   key=(cid,'h15')
   if key not in self.index:
    data=list(csv.reader(io.StringIO(self.raw(cid).decode('utf-8-sig'))));i=next(i for i,r in enumerate(data) if r and r[0]=='Time Period');header=data[i];self.index[key]={instant(r[0]):dict(zip(header,r)) for r in data[i+1:] if r}
   code='RIFLGFCY02_N.B' if d.endswith('dgs2') else 'RIFLGFCY10_N.B';expected={'rate_percent':self.index[key][at][code]}
  elif d.startswith('macro.treasury.'):
   key=(cid,'treasury')
   if key not in self.index:
    from .extensions import treasury_rows
    self.index[key]={instant(r['NEW_DATE'][:10]):r for r in treasury_rows(self.raw(cid))}
   expected={'rate_percent':self.index[key][at][meta['original_field']]}
  elif d.startswith('network.etherscan.'):
   key=(cid,'etherscan')
   if key not in self.index:
    data=csv.DictReader(io.StringIO(self.raw(cid).decode('utf-8-sig')));out={}
    for r in data:
     t=instant(int(r['UnixTimeStamp'])) if 'UnixTimeStamp' in r else instant(datetime.strptime(r['Date(UTC)'],'%m/%d/%Y').replace(tzinfo=timezone.utc));out[t]=r
    self.index[key]=out
   raw=self.index[key][at];expected={'gas_used':raw['Value']} if 'Value' in raw else {'execution_fee_burn_eth':raw['BurntFees']}
  elif d=='flows.farside.eth_etf.daily':
   key=(cid,'etf')
   if key not in self.index:
    cap=self.library.capture_info(cid);rows=etf_flows(Snapshot({'source_id':cid,'retrieved_at':cap['retrieved_at']},self.raw(cid)));self.index[key]={instant(r['observed_at']):r['values'] for r in rows}
   original=self.index[key][at];expected={'net_flow_usd':next(x for k,x in original.items() if 'total' in k.lower())}
  elif d=='execution.coinbase.eth.snapshot':
   book=self.obj(cid);bids=[(Decimal(r[0]),Decimal(r[1])) for r in book['bids'][:25]];asks=[(Decimal(r[0]),Decimal(r[1])) for r in book['asks'][:25]];bid=bids[0][0];ask=asks[0][0];mid=(bid+ask)/2
   expected={'best_bid_usd':bid,'best_ask_usd':ask,'spread_bps':(ask-bid)/mid*10000}
   for bps in [10,50,100]:
    band=Decimal(bps)/10000
    expected[f'bid_depth_{bps}bps_usd']=sum(p*q for p,q in bids if p>=mid*(1-band));expected[f'ask_depth_{bps}bps_usd']=sum(p*q for p,q in asks if p<=mid*(1+band))
   remaining=Decimal(1000);qty=Decimal(0);cost=Decimal(0)
   for p,q in asks:
    spent=min(remaining,p*q);cost+=spent;qty+=spent/p;remaining-=spent
    if remaining<=0:break
   expected['covered_notional_1000usd']=cost
   if qty:expected.update(estimated_vwap_1000usd=cost/qty,slippage_1000usd_bps=(cost/qty/ask-1)*10000)
  else:raise ValueError('No raw-response comparator registered: '+d)
  for key,value in expected.items():
   actual=v.get(key)
   if value is None:
    if actual is not None:raise ValueError('Source null became a value: '+key)
   elif actual is None or (Decimal(str(actual))!=Decimal(str(value)) and not (d=='execution.coinbase.eth.snapshot' and math.isclose(float(actual),float(value),rel_tol=1e-14,abs_tol=1e-18))):raise ValueError('Source value mismatch: '+key)
  return True
