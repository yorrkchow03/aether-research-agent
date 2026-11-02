"""Fixed-window execution-header backfill over an admitted public read method.

Uses eth_getBlockByNumber only; never requests restricted feeHistory, sends a
transaction or adds credentials. The measured64-header batch is a client choice,
not a claim about provider maxima. Partial scans never publish daily aggregates.
"""
from __future__ import annotations
import hashlib,json,re,shutil,time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import httpx
from research_data.http import dumps
from .store import WINDOW_START,WINDOW_END,instant
from .time import timestamp

ENDPOINT='https://ethereum-rpc.publicnode.com'
INPUT='input.ethereum.execution_headers'
EXECUTION='network.ethereum.execution.daily'
BASE='network.ethereum.base_fee.daily'
JOB='ethereum-header-backfill'
VERSION='ethereum-headers-v1'


def validate_headers(request,response):
 requests=request if isinstance(request,list) else [request]
 responses=response if isinstance(response,list) else [response]
 expected={r['id']:r['params'][0] for r in requests};found={}
 for packet in responses:
  if packet.get('error'):raise ValueError('Read-only header RPC error: '+str(packet['error'])[:500])
  identity=packet.get('id')
  if identity not in expected or identity in found:raise ValueError('Unexpected or duplicate RPC response ID')
  block=packet.get('result')
  if not isinstance(block,dict):raise ValueError('Missing execution block')
  for key in ('number','timestamp','baseFeePerGas','gasUsed','gasLimit'):
   if not isinstance(block.get(key),str) or not re.fullmatch(r'0x[0-9a-fA-F]+',block[key]):raise ValueError('Missing/invalid header field: '+key)
  if int(block['gasUsed'],16)>int(block['gasLimit'],16):raise ValueError('Gas used exceeds header gas limit')
  for key in ('hash','parentHash'):
   if not re.fullmatch(r'0x[0-9a-fA-F]{64}',block.get(key,'')):raise ValueError('Invalid block hash field')
  if expected[identity]!='finalized' and int(expected[identity],16)!=int(block['number'],16):raise ValueError('Returned block is not the requested height')
  found[identity]=block
 if set(found)!=set(expected):raise ValueError('RPC batch omitted requested headers')
 return [found[r['id']] for r in requests]

def add_headers(state,blocks,*,window_start=WINDOW_START,window_end=WINDOW_END):
 working=json.loads(dumps(state));batch_days=set()
 for i,block in enumerate(blocks):
  n=int(block['number'],16);t=int(block['timestamp'],16)
  if n!=state['cursor']+i or block['parentHash']!=working['previous_hash'] or t<=working['previous_timestamp']:raise ValueError('Header number/parent/timestamp continuity failure')
  at=timestamp(t)
  if not timestamp(window_start)<=at<timestamp(window_end):raise ValueError('Returned header lies outside fixed formal window')
  day=at.date().isoformat();batch_days.add(day);row=working['days'].setdefault(day,{'blocks':0,'base_fee_sum_wei':'0','gas_used':'0','execution_burn_wei':'0','input_ids':[]})
  base=int(block['baseFeePerGas'],16);gas=int(block['gasUsed'],16)
  row.update(blocks=row['blocks']+1,base_fee_sum_wei=str(int(row['base_fee_sum_wei'])+base),gas_used=str(int(row['gas_used'])+gas),execution_burn_wei=str(int(row['execution_burn_wei'])+base*gas))
  working.update(previous_hash=block['hash'],previous_timestamp=t)
 return working,batch_days

def day_values(row):
 if row['blocks']<=0:raise ValueError('Cannot publish an unknown base-fee mean as zero')
 return {'mean_base_fee_wei_per_gas':Decimal(row['base_fee_sum_wei'])/row['blocks']},{'gas_used':row['gas_used'],'execution_base_fee_burn_eth':Decimal(row['execution_burn_wei'])/Decimal(10**18),'block_count':row['blocks']}


class HeaderBackfill:
 def __init__(self,library,*,transport=None,interval=1.1,batch_size=64,sleep=time.sleep):
  if not 1<=batch_size<=64:raise ValueError('Use the preflighted1..64-header client batch bound')
  self.library=library;self.interval=interval;self.batch_size=batch_size;self.sleep=sleep;self.last=0.;self.calls=0;self.transport=transport;self.client=None;self.boundary_cache={};self.source_bytes=Path(__file__).read_bytes()
  with library.connect() as c:c.execute("CREATE INDEX IF NOT EXISTS capture_request_hash ON captures(source_id,json_extract(headers,'$.request_sha256'),retrieved_at)")
  library.register_dataset(INPUT,provider='ethereum_rpc',frequency='batch',asset='ETH',scope='Complete, explicitly numbered Ethereum mainnet execution-header input batches. Raw responses include transaction hashes; this is not an hourly observation.',units={'block_count':'count'},required=False)
  library.register_dataset(EXECUTION,provider='ethereum_rpc',frequency='1d',asset='ETH',scope='Ethereum mainnet mined execution blocks in each UTC day; gas and execution base-fee burn from all headers. Blob burn, slashing and consensus issuance are not included.',units={'gas_used':'gas/day','execution_base_fee_burn_eth':'ETH/day','block_count':'count'},required=False)
  library.register_source('ethereum_rpc',base_url=ENDPOINT,material_scope='Public read-only execution headers; client uses measured64-header batches, minimum1.1 seconds per HTTP request and bounded retries. Provider quotas/SLAs are not established.',request_method='POST',minimum_request_interval_seconds=interval)

 def request(self,heights):
  request=[{'jsonrpc':'2.0','id':h,'method':'eth_getBlockByNumber','params':[hex(h) if isinstance(h,int) else h,False]} for h in heights]
  if len(request)==1:request=request[0]
  return self.fetch(request,lambda obj:validate_headers(request,obj))

 def fetch(self,request,validator):
  request_hash=hashlib.sha256(dumps(request,sort_keys=True).encode()).hexdigest()
  with self.library.connect(readonly=True) as c:cached=c.execute("SELECT capture_id FROM captures WHERE source_id='ethereum_rpc' AND json_extract(headers,'$.request_sha256')=? AND status=200 AND error IS NULL ORDER BY retrieved_at DESC LIMIT 1",(request_hash,)).fetchone()
  if cached:
   cid=cached[0];return cid,validator(json.loads(self.library.raw(cid)))
  if self.calls>=5000:raise ValueError('Client request budget reached; resume from the saved checkpoint')
  for attempt in range(3):
   self.sleep(max(0,self.interval-(time.monotonic()-self.last)));self.last=time.monotonic();self.calls+=1
   try:
    with self.client.stream('POST',ENDPOINT,json=request) as response:
     raw=bytearray()
     for part in response.iter_bytes():
      raw.extend(part)
      if len(raw)>12_000_000:raise ValueError('RPC body exceeds client12MB bound')
    status=response.status_code;headers={'request_body':request,'request_sha256':request_hash,'content-type':response.headers.get('content-type'),'retry-after':response.headers.get('retry-after')}
    error=None if status==200 else 'HTTP '+str(status)
    try:blocks=validator(json.loads(raw)) if status==200 else None
    except ValueError as exc:error=str(exc);blocks=None
    cid=self.library.capture('ethereum_rpc',ENDPOINT,bytes(raw),status=status,headers=headers,error=error)
    if blocks is not None:return cid,blocks
    if status not in (429,500,502,503,504):raise ValueError(error or 'Header request failed')
    if attempt==2:raise ValueError(error+' after bounded retries')
    retry=headers['retry-after'] or '';self.sleep(min(20,float(retry) if retry.isdigit() else 2**(attempt+1)))
   except httpx.HTTPError as exc:
    self.library.capture('ethereum_rpc',ENDPOINT,error=type(exc).__name__+': '+str(exc),headers={'request_body':request,'request_sha256':request_hash})
    if attempt==2:raise ValueError('Header transport failed after bounded retries: '+type(exc).__name__) from None
    self.sleep(2**(attempt+1))
  raise ValueError('No valid header response')

 def mainnet(self):
  request={'jsonrpc':'2.0','id':'chain','method':'eth_chainId','params':[]}
  def validate(obj):
   if obj.get('id')!='chain' or obj.get('result')!='0x1':raise ValueError('Expected an Ethereum mainnet chain ID response')
   return obj['result']
  return self.fetch(request,validate)[0]

 def header(self,height):
  if height not in self.boundary_cache:
   cid,rows=self.request([height]);self.boundary_cache[height]=(cid,rows[0])
  return self.boundary_cache[height]

 def first_at(self,at,head):
  target=int(at.timestamp());height=int(head['number'],16);latest=int(head['timestamp'],16)
  if target>latest:raise ValueError('Fixed window extends beyond captured finalized head')
  # Slot length supplies a bracket estimate only. Header timestamps determine
  # the exact boundary; missed slots never become invented blocks.
  estimate=max(1,height-(latest-target)//12);span=2048
  for _ in range(9):
   lo=max(0,estimate-span);hi=min(height,estimate+span)
   a=self.header(lo)[1];b=self.header(hi)[1]
   if int(a['timestamp'],16)<target<=int(b['timestamp'],16):break
   span*=2
  else:raise ValueError('Could not bracket fixed UTC boundary from actual timestamps')
  while hi-lo>1:
   mid=(lo+hi)//2
   if int(self.header(mid)[1]['timestamp'],16)<target:lo=mid
   else:hi=mid
  return hi

 def run(self):
  start=self.library.start;end=self.library.end
  with self.library.connect(readonly=True) as c:old=c.execute('SELECT status,body FROM jobs WHERE job_id=?',(JOB,)).fetchone()
  state=json.loads(old['body']) if old else {}
  if old and old['status']=='collected' and state.get('source_range_complete'):return state
  try:
   if state.get('first_block') and (state.get('version')!=VERSION or not 5000*self.library.days<state['end_block_exclusive']-state['first_block']<14000*self.library.days or not state['first_block']<=state['cursor']<=state['end_block_exclusive']):raise ValueError('Invalid/version-mismatched saved header checkpoint')
   for name in (INPUT,BASE,EXECUTION):self.library.job(name,'running',source_range_complete=False,collector_job=JOB)
   with httpx.Client(timeout=35,follow_redirects=False,transport=self.transport,headers={'User-Agent':'AetherAcademicResearch/0.3','Content-Type':'application/json'}) as self.client:
    if not state.get('first_block'):
     chain_id=self.mainnet()
     cid,head=self.header('finalized');first=self.first_at(start,head);stop=self.first_at(end,head)
     if not 5000*self.library.days<stop-first<14000*self.library.days:raise ValueError('Unexpected fixed-month block count; review source/time boundaries')
     before_id,before=self.header(first-1);after_id,after=self.header(stop)
     state={'first_block':first,'end_block_exclusive':stop,'cursor':first,'previous_hash':before['hash'],'previous_timestamp':int(before['timestamp'],16),'boundary_capture_ids':[before_id,after_id], 'finalized_head_capture':cid,'chain_id_capture':chain_id,'days':{},'input_batches':0,'block_headers':0,'version':VERSION,'source_range_complete':False}
     self.library.job(JOB,'running',**state)
    cursor=state['cursor'];stop=state['end_block_exclusive']
    # A full month is several GB of original header/transaction-hash receipts.
    if shutil.disk_usage(self.library.root).free<16_000_000_000:raise ValueError('Need16GB free for source receipts and independent freeze/restore copies')
    while cursor<stop:
     limit=min(stop,cursor+self.batch_size);cid,blocks=self.request(list(range(cursor,limit)))
     working,batch_days=add_headers(state,blocks,window_start=self.library.window_start,window_end=self.library.window_end)
     first=blocks[0];last=blocks[-1]
     rid=self.library.put(INPUT,int(first['timestamp'],16),{'block_count':len(blocks)},cid,period_end=int(last['timestamp'],16),metadata={'first_block':cursor,'end_block_exclusive':limit,'first_hash':first['hash'],'last_hash':last['hash'],'rpc_method':'eth_getBlockByNumber','request_batch_size':len(blocks)})
     for day in batch_days:working['days'][day]['input_ids'].append(rid)
     working.update(cursor=limit,input_batches=working['input_batches']+1,block_headers=working['block_headers']+len(blocks));state=working;self.library.job(JOB,'running',**state)
     if state['input_batches']%10==0:print('HEADERS '+str(state['block_headers'])+'/'+str(stop-state['first_block'])+' last UTC '+instant(int(last['timestamp'],16)),flush=True)
     cursor=limit
    _,end_header=self.header(stop)
    if end_header['parentHash']!=state['previous_hash']:raise ValueError('End boundary is not linked to the last included block')
    expected={(start+timedelta(days=i)).date().isoformat() for i in range(self.library.days)}
    if set(state['days'])!=expected:raise ValueError('Unexplained missing UTC days in complete block range')
    code=self.library.object(self.source_bytes)
    for day,row in sorted(state['days'].items()):
     mean,execution=day_values(row)
     manifest={'algorithm':VERSION,'implementation_sha256':code,'input_record_ids':row['input_ids'],'utc_day':day,'boundary_capture_ids':state['boundary_capture_ids'],'chain_id_capture':state['chain_id_capture'],'first_block':state['first_block'],'end_block_exclusive':stop,'scope':'Arithmetic block mean; execution base-fee burn only. All included header timestamps belong to the UTC day. Boundary headers establish coverage and are excluded from values.'}
     calc=self.library.capture('calculation:'+VERSION,'urn:aether:execution-headers:'+day,dumps(manifest,sort_keys=True).encode())
     meta={'algorithm':VERSION,'weighting':'arithmetic across all mined execution blocks','input_manifest_sha256':hashlib.sha256(self.library.raw(calc)).hexdigest(),'native_frequency':'UTC day','block_count':row['blocks']}
     self.library.put(BASE,day,mean,calc,period_end=timestamp(day)+timedelta(days=1),input_ids=row['input_ids'],metadata=meta)
     self.library.put(EXECUTION,day,execution,calc,period_end=timestamp(day)+timedelta(days=1),input_ids=row['input_ids'],metadata=meta)
    state['source_range_complete']=True;self.library.job(JOB,'collected',**state)
    for name in (INPUT,BASE,EXECUTION):self.library.job(name,'collected',source_range_complete=True,observations=state['input_batches'] if name==INPUT else self.library.days,algorithm=VERSION)
    return state
  except Exception as exc:
   self.library.job(JOB,'interrupted',**{**state,'source_range_complete':False,'error':type(exc).__name__+': '+str(exc)[:1000]})
   for name in (INPUT,BASE,EXECUTION):self.library.job(name,'interrupted',source_range_complete=False,collector_job=JOB,error=type(exc).__name__+': '+str(exc)[:1000])
   raise
  finally:self.client=None
