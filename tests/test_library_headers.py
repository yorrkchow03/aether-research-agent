"""Header-source identity, complete batches, UTC boundaries and safe resume math."""
import copy,json,tempfile,unittest
from pathlib import Path
from decimal import Decimal
import httpx
from research_data.library.store import ResearchLibrary
from research_data.library.time import timestamp
from research_data.library.headers import HeaderBackfill,validate_headers,add_headers,day_values

START=int(timestamp('2026-09-26').timestamp())
def block(n,base=100,gas=2):
 return {'number':hex(n),'timestamp':hex(START+12*(n-100)),'hash':'0x'+format(n,'064x'),'parentHash':'0x'+format(n-1,'064x'),'baseFeePerGas':hex(base),'gasUsed':hex(gas),'gasLimit':'0x100'}

class HeaderTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.lib=ResearchLibrary(Path(self.tmp.name)/'library')
 def test_unordered_responses_are_matched_and_missing_or_wrong_blocks_rejected(self):
  request=[{'id':n,'params':[hex(n),False]} for n in (100,101)]
  response=[{'id':101,'result':block(101)},{'id':100,'result':block(100)}]
  self.assertEqual([int(b['number'],16) for b in validate_headers(request,response)],[100,101])
  for bad in [response[:1],response+[response[0]],[{'id':100,'result':block(101)},response[0]]]:
   with self.assertRaises(ValueError):validate_headers(request,bad)
 def test_failed_batch_does_not_change_a_committed_checkpoint(self):
  state={'cursor':100,'previous_hash':block(99)['hash'],'previous_timestamp':START-12,'days':{}}
  original=copy.deepcopy(state);bad=block(101);bad['parentHash']='0x'+'f'*64
  with self.assertRaises(ValueError):add_headers(state,[block(100),bad])
  self.assertEqual(state,original)
  result,_=add_headers(state,[block(100,100,2),block(101,200,3)])
  mean,execution=day_values(result['days']['2026-09-26'])
  self.assertEqual(mean['mean_base_fee_wei_per_gas'],Decimal(150));self.assertEqual(execution['gas_used'],'5')
  self.assertEqual(execution['execution_base_fee_burn_eth'],Decimal('0.0000000000000008'))
  self.assertEqual(state,original)
 def test_source_cache_reuses_original_capture_and_rejects_access_denial(self):
  count=[]
  def handler(request):
   count.append(1);obj=json.loads(request.content);return httpx.Response(200,json={'id':obj['id'],'result':block(100)})
  worker=HeaderBackfill(self.lib,sleep=lambda _:None,transport=httpx.MockTransport(handler))
  with httpx.Client(transport=worker.transport) as worker.client:
   a=worker.request([100]);b=worker.request([100])
  self.assertEqual(a,b);self.assertEqual(len(count),1)
  failed=HeaderBackfill(self.lib,sleep=lambda _:None,transport=httpx.MockTransport(lambda _:httpx.Response(403,json={'error':{'message':'personal token required'}})))
  with httpx.Client(transport=failed.transport) as failed.client:
   with self.assertRaisesRegex(ValueError,'403'):failed.request([101])
 def test_boundary_uses_actual_timestamps_not_a_fixed_blocks_per_day(self):
  worker=HeaderBackfill(self.lib,sleep=lambda _:None)
  def header(n):
   row=block(n);row['timestamp']=hex(START+12*(n-100)+(12 if n>=150 else 0));return 'fixture',row
  worker.header=header
  self.assertEqual(worker.first_at(timestamp(START+1200),header(1000)[1]),199)
 def test_wrong_chain_and_zero_observation_mean_are_not_accepted(self):
  worker=HeaderBackfill(self.lib,sleep=lambda _:None,transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'id':'chain','result':'0x5'})))
  with httpx.Client(transport=worker.transport) as worker.client:
   with self.assertRaisesRegex(ValueError,'mainnet'):worker.mainnet()
  with self.assertRaises(ValueError):day_values({'blocks':0})

if __name__=='__main__':unittest.main()
