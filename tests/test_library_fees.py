"""Protocol vectors and explicit fork/unknown-data boundaries for fee burn."""
import json,tempfile,unittest
from decimal import Decimal
from pathlib import Path
from research_data.library.store import ResearchLibrary
from research_data.library.fees import fake_exponential,parameters,blob_burn_wei,collect,GAS_PER_BLOB

class FeeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.l=ResearchLibrary(Path(self.tmp.name)/'library')
  self.root='https://raw.githubusercontent.com/ethereum/go-ethereum/'+'1'*40
  self.config={'chainId':1,'bpo2Time':1767747671,'blobSchedule':{'bpo2':{'target':14,'max':21,'baseFeeUpdateFraction':11684671}}}
 def refs(self,config):
  return {'chain_spec':self.l.capture('ethereum_protocol',self.root+'/params/chainspecs/mainnet.json',json.dumps(config).encode()),'blob_formula':self.l.capture('ethereum_protocol',self.root+'/consensus/misc/eip4844/eip4844.go',b'synthetic protocol reference')}
 def test_official_integer_vectors_include_rounding_that_float_exp_would_miss(self):
  # Published geth TestFakeExponential inputs/results, not a generated oracle.
  vectors=[(1,0,1,1),(38493,0,1000,38493),(0,1234,2345,0),(1,2,1,6),(1,4,2,6),(1,3,1,16),(1,6,2,18),(1,4,1,49),(1,8,2,50),(10,8,2,542),(11,8,2,596),(1,5,1,136),(1,5,2,11),(2,5,2,23),(1,50000000,2225652,5709098764)]
  for factor,numerator,denominator,result in vectors:self.assertEqual(fake_exponential(factor,numerator,denominator),result)
  for bad in [(-1,0,1),(1,-1,1),(1,1,0)]:
   with self.assertRaises(ValueError):fake_exponential(*bad)
  with self.assertRaisesRegex(ValueError,'bound'):fake_exponential(1,2**64-1,1)
 def test_actual_mainnet_schedule_is_required_and_new_forks_are_not_assumed_compatible(self):
  self.assertEqual(parameters(self.l,self.refs(self.config))['max'],21)
  for change in ({'chainId':5},{'bpo2Time':1800000000},{'bpo3Time':1790812800},{'newForkTime':1780000000}):
   with self.assertRaises(ValueError):parameters(self.l,self.refs({**self.config,**change}))
  # A later fork outside the window need not invalidate old observations.
  self.assertEqual(parameters(self.l,self.refs({**self.config,'futureTime':1900000000}))['target'],14)
 def test_blob_zero_is_explicit_and_missing_or_impossible_fields_are_not_zero_filled(self):
  p=parameters(self.l,self.refs(self.config))
  self.assertEqual(blob_burn_wei({'blobGasUsed':hex(2*GAS_PER_BLOB),'excessBlobGas':'0x0'},p),(2*GAS_PER_BLOB,2*GAS_PER_BLOB))
  self.assertEqual(blob_burn_wei({'blobGasUsed':'0x0','excessBlobGas':'0x0'},p),(0,0))
  for fields in ({'blobGasUsed':'0x0'},{'blobGasUsed':'0x1','excessBlobGas':'0x0'},{'blobGasUsed':hex(22*GAS_PER_BLOB),'excessBlobGas':'0x0'},{'blobGasUsed':'0x0','excessBlobGas':hex(2**64)}):
   with self.assertRaises(ValueError):blob_burn_wei(fields,p)
 def test_partial_header_scan_cannot_publish_or_request_protocol_data(self):
  class NoNetwork:
   def fetch(self,*args,**kwargs):raise AssertionError('Unexpected HTTP request')
  self.l.job('ethereum-header-backfill','running',block_headers=64,source_range_complete=False)
  with self.assertRaisesRegex(ValueError,'Complete the original'):collect(self.l,http=NoNetwork())
  self.assertEqual(self.l.datasets(),[])
 def test_separate_total_recomputed_from_raw_headers_and_not_confused_with_execution_only(self):
  from research_data.library.headers import HeaderBackfill,INPUT,EXECUTION,ENDPOINT,VERSION as HEADER_VERSION
  from research_data.library.fees import DATASET,VERSION
  from research_data.library.quality import SourceAudit
  from research_data.library.time import timestamp
  from research_data.library.store import WINDOW_END
  HeaderBackfill(self.l);start=int(timestamp('2026-09-26').timestamp())
  def block(n):return {'number':hex(n),'timestamp':hex(start+12*(n-100)),'hash':'0x'+format(n,'064x'),'parentHash':'0x'+format(n-1,'064x'),'baseFeePerGas':'0x64','gasUsed':'0x2','gasLimit':'0x100','blobGasUsed':hex(GAS_PER_BLOB),'excessBlobGas':'0x0'}
  def capture(ns):
   req=[{'id':n,'method':'eth_getBlockByNumber','params':[hex(n),False]} for n in ns];res=[{'id':n,'result':block(n)} for n in ns]
   if ns==[102]:res[0]['result']['timestamp']=hex(int(timestamp(WINDOW_END).timestamp()))
   return self.l.capture('ethereum_rpc',ENDPOINT,json.dumps(res if len(ns)>1 else res[0]).encode(),headers={'request_body':req if len(ns)>1 else req[0]})
  cap=capture([100,101]);rid=self.l.put(INPUT,start,{'block_count':2},cap,period_end=start+12,metadata={'first_block':100,'end_block_exclusive':102,'first_hash':block(100)['hash'],'last_hash':block(101)['hash']})
  chain=self.l.capture('ethereum_rpc',ENDPOINT,b'{"id":"chain","result":"0x1"}',headers={'request_body':{'id':'chain','method':'eth_chainId','params':[]}})
  native={'algorithm':HEADER_VERSION,'utc_day':'2026-09-26','input_record_ids':[rid],'first_block':100,'end_block_exclusive':102,'boundary_capture_ids':[capture([99]),capture([102])],'chain_id_capture':chain,'implementation_sha256':self.l.object(b'synthetic native calculation')}
  nc=self.l.capture('calculation:'+HEADER_VERSION,'urn:test',json.dumps(native).encode());parent=self.l.put(EXECUTION,'2026-09-26',{'gas_used':4,'execution_base_fee_burn_eth':Decimal('4e-16'),'block_count':2},nc,period_end='2026-09-27',input_ids=[rid])
  inputs=[parent,rid];manifest={'algorithm':VERSION,'utc_day':'2026-09-26','input_record_ids':inputs,'execution_record_id':parent,'protocol_captures':self.refs(self.config),'implementation_sha256':self.l.object(b'synthetic fee calculation')}
  fc=self.l.capture('calculation:'+VERSION,'urn:test-fee',json.dumps(manifest).encode())
  vals={'execution_base_fee_burn_eth':Decimal('4e-16'),'blob_fee_burn_eth':Decimal('2.62144e-13'),'total_fee_burn_eth':Decimal('2.62544e-13'),'blob_gas_used':262144,'block_count':2}
  self.l.register_dataset(DATASET,provider='fixture',frequency='1d',asset='ETH',scope='Synthetic source-lineage regression',units={k:'fixture' for k in vals})
  out=self.l.put(DATASET,'2026-09-26',vals,fc,period_end='2026-09-27',input_ids=inputs);row=self.l.evidence(out)
  self.assertTrue(SourceAudit(self.l).compare(row));row['values']['total_fee_burn_eth']='0.0000000000000004'
  with self.assertRaisesRegex(ValueError,'differ from original'):SourceAudit(self.l).compare(row)

if __name__=='__main__':unittest.main()
