import unittest
from research_agent.onchain import decode_weth_events,POOL,WETH,SUPPLY,WITHDRAW

def log(kind,amount):
 topic='0x'+'0'*24+WETH[2:].lower();data=(('0'*64) if kind==SUPPLY else '')+format(amount,'064x')
 return {'address':POOL,'blockNumber':'0x64','blockHash':'0xblock','transactionHash':'0xtx','logIndex':'0x0','topics':[kind,topic,'0x'+'0'*64,'0x'+'0'*64],'data':'0x'+data,'removed':False}
class OnchainTests(unittest.TestCase):
 def test_supply_and_withdraw_use_correct_abi_word_and_18_decimals(self):
  s=decode_weth_events([log(SUPPLY,2*10**18)],100,100)[0];w=decode_weth_events([log(WITHDRAW,10**18)],100,100)[0]
  self.assertEqual(s['amount_eth'],'2');self.assertEqual(w['amount_eth'],'1');self.assertEqual(s['event'],'Supply')
 def test_wrong_scope_removed_logs_and_wrong_abi_rejected(self):
  l=log(SUPPLY,10**18);l['removed']=True
  with self.assertRaises(ValueError):decode_weth_events([l],100,100)
  l=log(SUPPLY,10**18)
  with self.assertRaises(ValueError):decode_weth_events([l],101,102)
  l['data']='0x00'
  with self.assertRaises(ValueError):decode_weth_events([l],100,100)

if __name__=='__main__':unittest.main()
