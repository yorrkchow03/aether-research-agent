"""Independent audit rejects altered values or broken original-header lineage."""
import copy,json,tempfile,unittest
from pathlib import Path
from decimal import Decimal
from research_data.library.store import ResearchLibrary,WINDOW_END,instant
from research_data.library.time import timestamp
from research_data.library.headers import HeaderBackfill,INPUT,BASE,EXECUTION,ENDPOINT,VERSION
from research_data.library.header_quality import HeaderAudit

START=int(timestamp('2026-09-26').timestamp())
def block(n,base=100,gas=2):
 return {'number':hex(n),'timestamp':hex(START+12*(n-100)),'hash':'0x'+format(n,'064x'),'parentHash':'0x'+format(n-1,'064x'),'baseFeePerGas':hex(base),'gasUsed':hex(gas),'gasLimit':'0x100'}

class HeaderAuditTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.l=ResearchLibrary(Path(self.tmp.name)/'library');HeaderBackfill(self.l)
  self.l.register_dataset(BASE,provider='ethereum_rpc',frequency='1d',asset='ETH',scope='fixture',units={'mean_base_fee_wei_per_gas':'wei/gas'})
  self.blocks=[block(100,100,2),block(101,200,3)]
  request=[{'jsonrpc':'2.0','id':n,'method':'eth_getBlockByNumber','params':[hex(n),False]} for n in (100,101)]
  self.cap=self.capture(request,[{'id':n,'result':b} for n,b in zip((100,101),self.blocks)])
  self.rid=self.l.put(INPUT,START,{'block_count':2},self.cap,period_end=START+12,metadata={'first_block':100,'end_block_exclusive':102,'first_hash':self.blocks[0]['hash'],'last_hash':self.blocks[-1]['hash']})
  before=block(99);after=block(102);after['timestamp']=hex(int(timestamp(WINDOW_END).timestamp()))
  boundary=[self.capture({'id':n,'method':'eth_getBlockByNumber','params':[hex(n),False]},{'id':n,'result':b}) for n,b in ((99,before),(102,after))]
  chain=self.capture({'id':'chain','method':'eth_chainId','params':[]},{'id':'chain','result':'0x1'})
  self.manifest={'utc_day':'2026-09-26','input_record_ids':[self.rid],'first_block':100,'end_block_exclusive':102,'boundary_capture_ids':boundary,'chain_id_capture':chain,'algorithm':VERSION,'implementation_sha256':self.l.object(b'audit fixture implementation')}
  self.calc=self.l.capture('calculation:'+VERSION,'urn:aether:execution-headers:2026-09-26',json.dumps(self.manifest).encode())
  self.mean=self.l.put(BASE,'2026-09-26',{'mean_base_fee_wei_per_gas':150},self.calc,period_end='2026-09-27',input_ids=[self.rid])
  self.execution=self.l.put(EXECUTION,'2026-09-26',{'gas_used':5,'execution_base_fee_burn_eth':Decimal('0.0000000000000008'),'block_count':2},self.calc,period_end='2026-09-27',input_ids=[self.rid])
 def capture(self,request,response):return self.l.capture('ethereum_rpc',ENDPOINT,json.dumps(response).encode(),headers={'request_body':request})
 def test_values_recomputed_from_original_headers_without_collector_accumulator(self):
  audit=HeaderAudit(self.l)
  for rid in (self.rid,self.mean,self.execution):audit.compare(self.l.evidence(rid))
  result=audit.complete_range([self.l.evidence(self.execution)])
  self.assertEqual(result,{'days':1,'headers':2,'full_month':False})
  for rid,key in ((self.mean,'mean_base_fee_wei_per_gas'),(self.execution,'gas_used'),(self.execution,'execution_base_fee_burn_eth')):
   altered=self.l.evidence(rid);altered['values'][key]='42'
   with self.assertRaisesRegex(ValueError,'mismatch'):audit.compare(altered)
 def test_missing_or_duplicate_input_and_changed_boundary_are_rejected(self):
  for change in ({'input_record_ids':[self.rid,self.rid]},{'input_record_ids':['missing']},{'first_block':99},{'implementation_sha256':'f'*64}):
   manifest={**self.manifest,**change};cid=self.l.capture('calculation:'+VERSION,'urn:test',json.dumps(manifest).encode())
   with self.assertRaises((ValueError,KeyError)):HeaderAudit(self.l).day(cid)
 def test_input_times_counts_and_source_method_are_checked(self):
  for change in ({'observed_at':instant(START+1)},{'values':{'block_count':'3'}},{'library_metadata':{'first_block':99,'end_block_exclusive':102,'first_hash':self.blocks[0]['hash'],'last_hash':self.blocks[-1]['hash']}}):
   row={**self.l.evidence(self.rid),**change}
   with self.assertRaises(ValueError):HeaderAudit(self.l).compare(row)
  cid=self.capture({'id':100,'method':'eth_feeHistory','params':['0x64',False]},{'id':100,'result':self.blocks[0]})
  with self.assertRaisesRegex(ValueError,'read-only header'):HeaderAudit(self.l).packet(cid)
 def test_forged_input_list_cannot_match_a_real_manifest(self):
  row=self.l.evidence(self.execution);row['input_record_ids']=[]
  with self.assertRaisesRegex(ValueError,'input IDs'):HeaderAudit(self.l).compare(row)
 def test_completed_job_label_cannot_replace_missing_monthly_observations(self):
  from research_data.library.registry import install
  from research_data.library.verify import verify_library
  library=ResearchLibrary(Path(self.tmp.name)/'empty-month');install(library);HeaderBackfill(library)
  library.job('ethereum-header-backfill','collected',source_range_complete=True)
  report=verify_library(library)
  self.assertFalse(report['integrity_passed'])
  self.assertIn('Header job claims complete without a verified full monthly aggregate range',report['errors'])
 def test_provider_precision_never_hides_gas_count_or_unit_errors(self):
  from research_data.library.header_quality import provider_difference
  self.assertEqual(provider_difference('17.352585795429747362','17.3525857954297',allow_display_rounding=True)['comparison'],'compatible_with_displayed_precision')
  self.assertEqual(provider_difference('217230267606','217230267606')['comparison'],'exact')
  self.assertEqual(provider_difference('217230267607','217230267606')['comparison'],'unexplained_difference')
  self.assertEqual(provider_difference('17.352585795429747362','0.0173525857954297',allow_display_rounding=True)['comparison'],'unexplained_difference')

if __name__=='__main__':unittest.main()
