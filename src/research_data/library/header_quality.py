"""Offline header/UTC/range/value checks over original admitted RPC receipts."""
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
import json
from .headers import INPUT,BASE,EXECUTION,ENDPOINT,VERSION,validate_headers
from .store import WINDOW_START,WINDOW_END,instant
from .time import timestamp

def provider_difference(native,reference,*,allow_display_rounding=False):
 native=Decimal(native);reference=Decimal(reference);delta=native-reference
 compatible=bool(delta) and allow_display_rounding and abs(delta)<=Decimal(1).scaleb(reference.as_tuple().exponent)/2
 return {'native_minus_reference':str(delta),'comparison':'exact' if not delta else 'compatible_with_displayed_precision' if compatible else 'unexplained_difference'}

class HeaderAudit:
 def __init__(self,library):self.library=library;self.packets=OrderedDict();self.days={}
 def packet(self,cid):
  if cid in self.packets:
   self.packets.move_to_end(cid);return self.packets[cid]
  cap=self.library.capture_info(cid)
  if cap['status']!=200 or cap['error'] or cap['url']!=ENDPOINT:raise ValueError('Invalid header provider/capture status')
  request=json.loads(cap['headers']).get('request_body');requests=request if isinstance(request,list) else [request]
  if any(not isinstance(r,dict) or r.get('method')!='eth_getBlockByNumber' or len(r.get('params',[]))!=2 or r['params'][1] is not False for r in requests):raise ValueError('Expected an original read-only header request')
  rows=validate_headers(request,json.loads(self.library.raw(cid)))
  keys=('number','timestamp','baseFeePerGas','gasUsed','gasLimit','hash','parentHash');result=[]
  for block in rows:
   for key in keys[:5]:
    if int(block[key],16).bit_length()>(256 if key=='baseFeePerGas' else 64):raise ValueError('Header integer exceeds protocol field size')
   result.append({k:block[k] for k in (*keys,'blobGasUsed','excessBlobGas') if k in block})
  self.packets[cid]=result
  while len(self.packets)>8:self.packets.popitem(last=False)
  return result
 def check_input(self,row):
  blocks=self.packet(row['library_capture_id']);meta=row['library_metadata']
  first=int(blocks[0]['number'],16);stop=int(blocks[-1]['number'],16)+1
  if [int(b['number'],16) for b in blocks]!=list(range(first,stop)):raise ValueError('Input batch has missing/overlapping heights')
  if meta['first_block']!=first or meta['end_block_exclusive']!=stop or meta['first_hash']!=blocks[0]['hash'] or meta['last_hash']!=blocks[-1]['hash']:raise ValueError('Input batch metadata differs from original headers')
  if Decimal(row['values']['block_count'])!=len(blocks):raise ValueError('Input count differs from original batch')
  if row['observed_at']!=instant(int(blocks[0]['timestamp'],16)) or row['period_end']!=instant(int(blocks[-1]['timestamp'],16)):raise ValueError('Input batch time differs from original headers')
  for before,after in zip(blocks,blocks[1:]):
   if after['parentHash']!=before['hash'] or int(after['timestamp'],16)<=int(before['timestamp'],16):raise ValueError('Input batch parent/time discontinuity')
  return blocks
 def day(self,cid):
  if cid in self.days:return self.days[cid]
  manifest=json.loads(self.library.raw(cid));date=manifest['utc_day'];start=timestamp(date);end=start+timedelta(days=1)
  if manifest['algorithm']!=VERSION or date!=start.date().isoformat():raise ValueError('Unknown header calculation version or non-date UTC day')
  with self.library.connect(readonly=True) as c:code=c.execute('SELECT path FROM objects WHERE sha256=?',(manifest['implementation_sha256'],)).fetchone()
  if code is None:raise ValueError('Missing saved calculation implementation')
  import hashlib
  if hashlib.sha256((self.library.root/code[0]).read_bytes()).hexdigest()!=manifest['implementation_sha256']:raise ValueError('Saved calculation implementation hash mismatch')
  if not self.library.start<=start<self.library.end:raise ValueError('Derived day outside formal window')
  ids=manifest['input_record_ids']
  if len(set(ids))!=len(ids) or not ids:raise ValueError('Duplicate/missing daily input references')
  blocks=[]
  for rid in ids:
   parent=self.library.evidence(rid)
   if parent['dataset_id']!=INPUT:raise ValueError('Daily aggregate references a non-header dataset')
   included=[b for b in self.check_input(parent) if start<=timestamp(int(b['timestamp'],16))<end]
   if not included:raise ValueError('Day references an input page with no observations that day')
   blocks.extend(included)
  blocks.sort(key=lambda b:int(b['number'],16));heights=[int(b['number'],16) for b in blocks]
  if not blocks or heights!=list(range(heights[0],heights[-1]+1)):raise ValueError('Daily headers are duplicated or incomplete')
  for before,after in zip(blocks,blocks[1:]):
   if after['parentHash']!=before['hash'] or int(after['timestamp'],16)<=int(before['timestamp'],16):raise ValueError('Daily header chain is discontinuous')
  if heights[0]<manifest['first_block'] or heights[-1]>=manifest['end_block_exclusive']:raise ValueError('Derived day exceeds declared complete range')
  boundaries=[self.packet(c)[0] for c in manifest['boundary_capture_ids']]
  before,after=boundaries
  if int(before['number'],16)!=manifest['first_block']-1 or int(after['number'],16)!=manifest['end_block_exclusive']:raise ValueError('Boundary heights disagree with declared range')
  if not int(before['timestamp'],16)<self.library.start.timestamp()<=int(after['timestamp'],16) or int(after['timestamp'],16)<self.library.end.timestamp():raise ValueError('Boundary timestamps do not bracket the fixed window')
  cap=self.library.capture_info(manifest['chain_id_capture']);chain=json.loads(self.library.raw(manifest['chain_id_capture']));request=json.loads(cap['headers']).get('request_body',{})
  if cap['status']!=200 or cap['error'] or cap['url']!=ENDPOINT or request.get('method')!='eth_chainId' or chain.get('result')!='0x1' or chain.get('id')!=request.get('id'):raise ValueError('Missing Ethereum mainnet source proof')
  if start==self.library.start and (heights[0]!=manifest['first_block'] or blocks[0]['parentHash']!=before['hash']):raise ValueError('First day is not linked to start boundary')
  if end==self.library.end and (heights[-1]!=manifest['end_block_exclusive']-1 or after['parentHash']!=blocks[-1]['hash']):raise ValueError('Last day is not linked to end boundary')
  count=len(blocks);fee=sum(int(b['baseFeePerGas'],16) for b in blocks);gas=sum(int(b['gasUsed'],16) for b in blocks);burn=sum(int(b['baseFeePerGas'],16)*int(b['gasUsed'],16) for b in blocks)
  values={'mean_base_fee_wei_per_gas':Decimal(fee)/count,'gas_used':Decimal(gas),'execution_base_fee_burn_eth':Decimal(burn)/10**18,'block_count':Decimal(count)}
  result={'date':date,'values':values,'first_height':heights[0],'last_height':heights[-1],'first_parent_hash':blocks[0]['parentHash'],'last_hash':blocks[-1]['hash'],'global_first':manifest['first_block'],'global_end':manifest['end_block_exclusive']}
  self.days[cid]=result;return result
 def compare(self,row):
  if row['dataset_id']==INPUT:self.check_input(row);return
  day=self.day(row['library_capture_id'])
  if row['observed_at']!=instant(day['date']) or row['period_end']!=instant(timestamp(day['date'])+timedelta(days=1)):raise ValueError('Daily aggregate time mismatch')
  manifest=json.loads(self.library.raw(row['library_capture_id']))
  if row['input_record_ids']!=manifest['input_record_ids']:raise ValueError('Aggregate input IDs differ from saved calculation manifest')
  for k,value in row['values'].items():
   if Decimal(value)!=day['values'][k]:raise ValueError('Header-derived value mismatch: '+k)
 def complete_range(self,rows):
  if not rows:return {'days':0,'headers':0,'full_month':False}
  summaries=[self.day(r['library_capture_id']) for r in rows];summaries.sort(key=lambda r:r['date'])
  for a,b in zip(summaries,summaries[1:]):
   if b['first_height']!=a['last_height']+1 or b['first_parent_hash']!=a['last_hash']:raise ValueError('Execution aggregate days have a block gap or broken parent link')
  count=sum(int(r['values']['block_count']) for r in summaries);full=len(summaries)==self.library.days
  if any((r['global_first'],r['global_end'])!=(summaries[0]['global_first'],summaries[0]['global_end']) for r in summaries):raise ValueError('Daily manifests disagree on the complete monthly range')
  if full and (summaries[0]['first_height']!=summaries[0]['global_first'] or summaries[-1]['last_height']!=summaries[-1]['global_end']-1 or count!=summaries[0]['global_end']-summaries[0]['global_first']):raise ValueError('Claimed monthly execution range is incomplete')
  return {'days':len(rows),'headers':count,'full_month':full}
