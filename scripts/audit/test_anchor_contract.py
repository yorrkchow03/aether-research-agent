"""Compile and test real EVM anchoring; explicitly a local test chain, not Sepolia."""
import hashlib,json,os
from pathlib import Path
import solcx
from web3 import Web3,EthereumTesterProvider
root=Path(__file__).resolve().parents[2];compiler=root/'.audit-tools/solc';os.environ['SOLCX_BINARY_PATH']=str(compiler)
source=(root/'contracts/AetherResearchAnchor.sol').read_text()
compiled=solcx.compile_source(source,output_values=['abi','bin','bin-runtime'],solc_binary=compiler/'solc-v0.8.28')
artifact=next(iter(compiled.values()));(root/'contracts/AetherResearchAnchor.artifact.json').write_text(json.dumps({'compiler':'0.8.28','source_sha256':hashlib.sha256(source.encode()).hexdigest(),**artifact},indent=2))
w=Web3(EthereumTesterProvider());owner,other=w.eth.accounts[:2]
factory=w.eth.contract(abi=artifact['abi'],bytecode=artifact['bin']);receipt=w.eth.wait_for_transaction_receipt(factory.constructor().transact({'from':owner}))
c=w.eth.contract(address=receipt.contractAddress,abi=artifact['abi']);rid=hashlib.sha256(b'report-id').digest();hashes=[hashlib.sha256(x).digest() for x in (b'report',b'manifest',b'config')]
r=w.eth.wait_for_transaction_receipt(c.functions.anchor(rid,*hashes).transact({'from':owner}));stored=c.functions.anchors(owner,rid).call()
checks={'deployed':bool(w.eth.get_code(c.address)),'digests_match':list(stored[:3])==hashes,'timestamp_recorded':stored[3]>0,'anchored_event':len(c.events.Anchored().process_receipt(r))==1}
try:c.functions.anchor(rid,*hashes).transact({'from':owner});checks['duplicate_id_rejected']=False
except Exception:checks['duplicate_id_rejected']=True
try:c.functions.anchor(bytes(32),*hashes).transact({'from':owner});checks['empty_id_rejected']=False
except Exception:checks['empty_id_rejected']=True
c.functions.anchor(rid,*[hashlib.sha256(x).digest() for x in (b'other report',b'other manifest',b'other config')]).transact({'from':other})
checks['reporter_namespace_preserved']=list(c.functions.anchors(owner,rid).call()[:3])==hashes
result={'scope':'Actual local EthereumTester/PyEVM chain; not public Sepolia deployment','chain_id':w.eth.chain_id,'contract':c.address,'deploy_tx':receipt.transactionHash.hex(),'anchor_tx':r.transactionHash.hex(),'compiler':'0.8.28','checks':checks}
(root/'reports/anchor_contract_tests.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
if not all(checks.values()):raise SystemExit(1)
