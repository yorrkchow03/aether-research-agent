"""Separate execution + blob fee burn from complete original header receipts.

Fixed-window BPO2 mainnet support only. Normative EIP-4844 integer pricing and
the actual pinned mainnet chain spec determine the calculation; illustrative
EIP schedules, transaction tips and consensus penalties are not substituted.
"""
import hashlib,json,re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from .store import WINDOW_START,WINDOW_END,instant
from .time import timestamp
from .http import PublicCapture
from .headers import INPUT,EXECUTION,JOB,ENDPOINT
from .header_quality import HeaderAudit
from research_data.http import dumps

DATASET='network.ethereum.fee_burn.daily'
VERSION='ethereum-fee-burn-v1'
GAS_PER_BLOB=2**17

def fake_exponential(factor,numerator,denominator):
 if min(factor,numerator)<0 or denominator<=0:raise ValueError('Invalid integer blob-price parameters')
 term=factor*denominator;total=0
 for i in range(1,1025):
  if not term:return total//denominator
  if term.bit_length()>512:raise ValueError('Blob-price integer exceeds client bound')
  total+=term;term=term*numerator//(denominator*i)
 raise ValueError('Blob-price series exceeds client iteration bound')

def parameters(library,captures):
 """Validate the fixed-window schedule against an archived actual mainnet spec."""
 config=library.capture_info(captures['chain_spec']);url=config['url']
 if config['status']!=200 or config['error'] or not re.fullmatch(r'https://raw.githubusercontent.com/ethereum/go-ethereum/[a-f0-9]{40}/params/chainspecs/mainnet.json',url):raise ValueError('Use a successful pinned official mainnet configuration')
 chain=json.loads(library.raw(captures['chain_spec']))
 if chain.get('chainId')!=1:raise ValueError('Blob schedule must describe Ethereum mainnet')
 start=int(library.start.timestamp());end=int(library.end.timestamp())
 known={'shanghaiTime','cancunTime','pragueTime','osakaTime','bpo1Time','bpo2Time'}
 if any(k.endswith('Time') and v is not None and v<end and k not in known for k,v in chain.items()):raise ValueError('An unsupported mainnet fork affects the fixed window')
 if not chain.get('bpo2Time') or chain['bpo2Time']>start:raise ValueError('This calculation requires BPO2 throughout the fixed window')
 if any(start<v<end for k,v in chain.items() if k.endswith('Time') and isinstance(v,int)):raise ValueError('A fork inside the fixed window requires explicit split support')
 schedule=chain['blobSchedule']['bpo2']
 if any(not isinstance(schedule.get(k),int) or schedule[k]<=0 for k in ('target','max','baseFeeUpdateFraction')) or schedule['target']>=schedule['max']:raise ValueError('Invalid archived blob schedule')
 formula=library.capture_info(captures['blob_formula'])
 expected=url.rsplit('/params/',1)[0]+'/consensus/misc/eip4844/eip4844.go'
 if formula['status']!=200 or formula['error'] or formula['url']!=expected:raise ValueError('Blob pricing reference must use the same pinned official commit')
 library.raw(captures['blob_formula'])
 return schedule

def blob_burn_wei(block,schedule):
 values={}
 for key in ('blobGasUsed','excessBlobGas'):
  raw=block.get(key)
  if not isinstance(raw,str) or not re.fullmatch(r'0x[0-9a-fA-F]+',raw):raise ValueError('Missing/invalid original blob header field: '+key)
  values[key]=int(raw,16)
  if values[key].bit_length()>64:raise ValueError('Blob header exceeds uint64')
 gas=values['blobGasUsed']
 if gas%GAS_PER_BLOB or gas>schedule['max']*GAS_PER_BLOB:raise ValueError('Blob gas violates the actual mainnet schedule')
 price=fake_exponential(1,values['excessBlobGas'],schedule['baseFeeUpdateFraction'])
 return gas,gas*price

def protocol_references(library,http):
 library.register_source('ethereum_protocol',base_url='https://github.com/ethereum',material_scope='Pinned Ethereum mainnet configuration and normative public protocol references; not market observations')
 _,body=http.fetch('ethereum_protocol','https://api.github.com/repos/ethereum/go-ethereum/commits/master',accept_json=True)
 commit=json.loads(body)['sha']
 if not re.fullmatch('[a-f0-9]{40}',commit):raise ValueError('Invalid official source commit')
 root='https://raw.githubusercontent.com/ethereum/go-ethereum/'+commit
 return {'chain_spec':http.fetch('ethereum_protocol',root+'/params/chainspecs/mainnet.json')[0],
         'blob_formula':http.fetch('ethereum_protocol',root+'/consensus/misc/eip4844/eip4844.go')[0]}

def collect(library,*,http=None):
 with library.connect(readonly=True) as c:job=c.execute('SELECT status,body FROM jobs WHERE job_id=?',(JOB,)).fetchone()
 if not job or job['status']!='collected' or not json.loads(job['body']).get('source_range_complete'):raise ValueError('Complete the original header scan before deriving fee burn')
 existing=library.coverage()['jobs'].get(DATASET,{})
 if existing.get('status')=='collected' and existing.get('source_range_complete'):return existing
 refs=protocol_references(library,http or PublicCapture(library));schedule=parameters(library,refs)
 library.register_dataset(DATASET,provider='ethereum_rpc',frequency='1d',asset='ETH',required=False,
  scope='All fixed-window mainnet execution blocks: EIP-1559 execution burn + EIP-4844 blob burn. Excludes consensus penalties, lost coins, issuance and transaction tips; this is fee burn, not net supply change.',
  units={'execution_base_fee_burn_eth':'ETH/day','blob_fee_burn_eth':'ETH/day','total_fee_burn_eth':'ETH/day','blob_gas_used':'blob gas/day','block_count':'count'})
 library.job(DATASET,'running',source_range_complete=False,algorithm=VERSION)
 try:
  audit=HeaderAudit(library);native=library.series(EXECUTION,limit=1000)['records']
  if not audit.complete_range(native)['full_month']:raise ValueError('Original execution daily range is incomplete')
  code=library.object(Path(__file__).read_bytes());count=0
  for parent in native:
   audit.compare(parent);date=parent['observed_at'][:10];start=timestamp(date);end=start+timedelta(days=1);blob=0;gas=0;blocks=0
   ids=parent['input_record_ids']
   for rid in ids:
    for block in audit.check_input(library.evidence(rid)):
     if start<=timestamp(int(block['timestamp'],16))<end:
      used,burn=blob_burn_wei(block,schedule);gas+=used;blob+=burn;blocks+=1
   if blocks!=int(parent['values']['block_count']):raise ValueError('Blob aggregation omitted original execution blocks')
   execution=Decimal(parent['values']['execution_base_fee_burn_eth']);blob_eth=Decimal(blob)/Decimal(10**18)
   values={'execution_base_fee_burn_eth':execution,'blob_fee_burn_eth':blob_eth,'total_fee_burn_eth':execution+blob_eth,'blob_gas_used':gas,'block_count':blocks}
   manifest={'algorithm':VERSION,'implementation_sha256':code,'protocol_captures':refs,'utc_day':date,'execution_record_id':parent['record_id'],'input_record_ids':[parent['record_id'],*ids]}
   cap=library.capture('calculation:'+VERSION,'urn:aether:fee-burn:'+date,dumps(manifest,sort_keys=True).encode())
   library.put(DATASET,date,values,cap,period_end=end,input_ids=manifest['input_record_ids'],metadata={'algorithm':VERSION,'scope':'Execution + blob fee burn; no staking or consensus-penalty inference'})
   count+=1
  library.job(DATASET,'collected',source_range_complete=True,algorithm=VERSION,observations=count,protocol_captures=refs)
  return {'observations':count,'source_range_complete':True}
 except Exception as exc:
  library.job(DATASET,'interrupted',source_range_complete=False,algorithm=VERSION,error=type(exc).__name__+': '+str(exc)[:500]);raise

def audit_row(audit,row):
 library=audit.library;manifest=json.loads(library.raw(row['library_capture_id']))
 if manifest['algorithm']!=VERSION or row['input_record_ids']!=manifest['input_record_ids']:raise ValueError('Fee-burn calculation/input version mismatch')
 with library.connect(readonly=True) as c:code=c.execute('SELECT path FROM objects WHERE sha256=?',(manifest['implementation_sha256'],)).fetchone()
 if not code or hashlib.sha256((library.root/code[0]).read_bytes()).hexdigest()!=manifest['implementation_sha256']:raise ValueError('Fee-burn implementation is missing or changed')
 schedule=parameters(library,manifest['protocol_captures']);parent=library.evidence(manifest['execution_record_id'])
 if parent['dataset_id']!=EXECUTION or manifest['input_record_ids']!=[parent['record_id'],*parent['input_record_ids']]:raise ValueError('Fee-burn references changed original execution lineage')
 audit.compare(parent);day=manifest['utc_day'];start=timestamp(day);end=start+timedelta(days=1)
 if parent['observed_at']!=instant(day) or row['observed_at']!=instant(day) or row['period_end']!=instant(end):raise ValueError('Fee-burn UTC day mismatch')
 blob_wei=0;blob_gas=0;count=0
 for rid in parent['input_record_ids']:
  for block in audit.check_input(library.evidence(rid)):
   if start<=timestamp(int(block['timestamp'],16))<end:
    gas,wei=blob_burn_wei(block,schedule);blob_wei+=wei;blob_gas+=gas;count+=1
 execution=Decimal(parent['values']['execution_base_fee_burn_eth']);blob=Decimal(blob_wei)/10**18
 expected={'execution_base_fee_burn_eth':execution,'blob_fee_burn_eth':blob,'total_fee_burn_eth':execution+blob,'blob_gas_used':blob_gas,'block_count':count}
 if any(Decimal(row['values'][k])!=v for k,v in expected.items()):raise ValueError('Fee-burn values differ from original execution/blob headers')
