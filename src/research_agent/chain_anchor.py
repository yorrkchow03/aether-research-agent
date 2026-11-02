"""Sepolia-only report anchoring. Import with the isolated audit Python runtime."""
from __future__ import annotations
import hashlib,json,os
from pathlib import Path
from web3 import Web3
from eth_account import Account
from .audit import verify_bundle,sha
from research_data.http import utcnow

CHAIN_ID=11155111
RPC='https://ethereum-sepolia-rpc.publicnode.com'


def write_json(path,value,private=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    flags=os.O_WRONLY|os.O_CREAT|os.O_TRUNC
    fd=os.open(path,flags,0o600 if private else 0o644)
    with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    if private:os.chmod(path,0o600)


def assert_sepolia(chain_id):
    if chain_id!=CHAIN_ID:raise ValueError('Refusing writes outside Sepolia chain11155111')


def wallet_setup(root):
    path=Path(root)/'.audit-tools/sepolia_wallet.json'
    if not path.exists():
        account=Account.create()
        write_json(path,{'private_key':account.key.hex(),'address':account.address,'chain_id':CHAIN_ID,'purpose':'Project-only testnet hash anchoring; never fund with real assets','created_at':utcnow()},private=True)
    obj=json.loads(path.read_text());account=Account.from_key(obj['private_key'])
    if account.address!=obj['address'] or obj['chain_id']!=CHAIN_ID:raise ValueError('Test wallet metadata mismatch')
    return {'address':account.address,'chain_id':CHAIN_ID,'purpose':obj['purpose'],'key_file_created':True}


class SepoliaAnchor:
    def __init__(self,root,web3=None):
        self.root=Path(root);self.web3=web3 or Web3(Web3.HTTPProvider(RPC,request_kwargs={'timeout':45}))
        self.directory=self.root/'data/chain';self.directory.mkdir(parents=True,exist_ok=True)
        self.artifact=json.loads((self.root/'contracts/AetherResearchAnchor.artifact.json').read_text())
        if sha((self.root/'contracts/AetherResearchAnchor.sol').read_bytes())!=self.artifact['source_sha256']:raise ValueError('Compile the current contract source first')
        if 'bin-runtime' not in self.artifact:raise ValueError('Contract artifact lacks runtime bytecode')

    def connection(self):
        chain_id=self.web3.eth.chain_id;assert_sepolia(chain_id)
        obj=wallet_setup(self.root);obj.update(rpc=RPC,block_number=self.web3.eth.block_number,
            balance_wei=self.web3.eth.get_balance(obj['address']),checked_at=utcnow())
        try:obj['genesis_hash']=self.web3.eth.get_block(0)['hash'].hex()
        except Exception as exc:obj.update(genesis_hash=None,genesis_gap=str(exc)[:300])
        obj['balance_test_eth']=str(Web3.from_wei(obj['balance_wei'],'ether'));obj['status']='ready' if obj['balance_wei'] else 'needs_test_eth'
        write_json(self.directory/'status.json',obj);return obj

    def contract(self,address):
        address=Web3.to_checksum_address(address)
        code=self.web3.eth.get_code(address)
        if bytes(code).hex()!=self.artifact['bin-runtime']:raise ValueError('Deployed contract code differs from the compiled source artifact')
        return self.web3.eth.contract(address=address,abi=self.artifact['abi'])

    def send(self,function,kind):
        connection=self.connection();obj=json.loads((self.root/'.audit-tools/sepolia_wallet.json').read_text());account=Account.from_key(obj['private_key'])
        if not connection['balance_wei']:raise ValueError('Project test wallet needs free Sepolia test ETH before deployment/anchoring')
        gas=int(function.estimate_gas({'from':account.address})*1.2)
        if gas>1_500_000:raise ValueError('Unexpected gas estimate for this small audit contract')
        latest=self.web3.eth.get_block('latest');priority=1_000_000_000;base=int(latest.get('baseFeePerGas',self.web3.eth.gas_price))
        transaction=function.build_transaction({'from':account.address,'chainId':CHAIN_ID,'nonce':self.web3.eth.get_transaction_count(account.address,'pending'),
                         'gas':gas,'maxFeePerGas':2*base+priority,'maxPriorityFeePerGas':priority})
        if connection['balance_wei']<gas*transaction['maxFeePerGas']:raise ValueError('Project test wallet needs free Sepolia test ETH before deployment/anchoring')
        signed=account.sign_transaction(transaction);tx_hash=signed.hash.hex()
        pending={'kind':kind,'tx_hash':tx_hash,'chain_id':CHAIN_ID,'reporter':account.address,'nonce':transaction['nonce'],'state':'prepared','created_at':utcnow()}
        path=self.directory/('transaction-'+tx_hash+'.json');write_json(path,pending)
        submitted=self.web3.eth.send_raw_transaction(signed.raw_transaction)
        if submitted.hex()!=tx_hash:raise ValueError('RPC returned unexpected transaction hash')
        pending['state']='submitted';write_json(path,pending)
        receipt=self.web3.eth.wait_for_transaction_receipt(submitted,timeout=180,poll_latency=3)
        pending.update(state='confirmed' if receipt.status==1 else 'reverted',block_number=receipt.blockNumber,block_hash=receipt.blockHash.hex(),gas_used=receipt.gasUsed,
                       effective_gas_price=receipt.effectiveGasPrice,finished_at=utcnow());write_json(path,pending)
        if receipt.status!=1:raise ValueError('Testnet transaction reverted')
        return receipt,pending

    def deploy(self):
        self.connection()
        path=self.directory/'deployment.json'
        if path.exists():
            obj=json.loads(path.read_text());self.contract(obj['contract_address']);return obj
        for pending in self.directory.glob('transaction-*.json'):
            item=json.loads(pending.read_text())
            if item['kind']=='deploy' and item['state'] in ('prepared','submitted'):
                raise ValueError('Deployment transaction already prepared/submitted: '+item['tx_hash']+'; inspect this transaction before retrying, do not duplicate deployment')
        factory=self.web3.eth.contract(abi=self.artifact['abi'],bytecode=self.artifact['bin'])
        receipt,transaction=self.send(factory.constructor(),'deploy')
        self.contract(receipt.contractAddress)
        result={**transaction,'contract_address':receipt.contractAddress,'source_sha256':self.artifact['source_sha256'],'runtime_sha256':sha(bytes(self.web3.eth.get_code(receipt.contractAddress))),
                'compiler':self.artifact['compiler'],'scope':'Public Sepolia deployment; test tokens only'}
        write_json(path,result);return result

    def anchor(self,bundle):
        bundle=Path(bundle);reference=json.loads((bundle/'integrity_reference.json').read_text())
        integrity=verify_bundle(bundle,reference)
        if integrity['status']!='passed':raise ValueError('Bundle integrity checks failed; anchoring refused')
        connection=self.connection();deployment=json.loads((self.directory/'deployment.json').read_text());contract=self.contract(deployment['contract_address'])
        report_id=hashlib.sha256(reference['package_id'].encode()).digest();hashes=[bytes.fromhex(reference[k]) for k in ('report_hash','manifest_hash','config_hash')]
        existing=contract.functions.anchors(connection['address'],report_id).call()
        if existing[3]:
            if list(existing[:3])!=hashes:raise ValueError('The report ID already has different on-chain digests')
            return self.verify(bundle,connection['address'],contract.address)
        kind='anchor:'+reference['package_id']
        for pending in self.directory.glob('transaction-*.json'):
            item=json.loads(pending.read_text())
            if item['kind']==kind and item['state'] in ('prepared','submitted'):
                raise ValueError('Anchor transaction already prepared/submitted: '+item['tx_hash']+'; inspect this transaction before retrying')
        receipt,transaction=self.send(contract.functions.anchor(report_id,*hashes),kind)
        result={**reference,**transaction,'contract_address':contract.address,'report_id':report_id.hex(),'report_id_algorithm':'SHA-256(UTF-8 package_id)',
                'explorer_url':'https://sepolia.etherscan.io/tx/0x'+transaction['tx_hash'].removeprefix('0x')}
        write_json(bundle/'chain_receipt.json',result)
        return self.verify(bundle,connection['address'],contract.address)

    def verify(self,bundle,reporter,contract_address):
        assert_sepolia(self.web3.eth.chain_id);bundle=Path(bundle);manifest=json.loads((bundle/'manifest.json').read_text())
        report_id=hashlib.sha256(manifest['package_id'].encode()).digest();contract=self.contract(contract_address)
        anchor=contract.functions.anchors(Web3.to_checksum_address(reporter),report_id).call()
        if not anchor[3]:return {'status':'not_anchored','chain_id':CHAIN_ID,'report_id':report_id.hex()}
        expected=dict(zip(('report_hash','manifest_hash','config_hash'),[bytes(x).hex() for x in anchor[:3]]))
        integrity=verify_bundle(bundle,expected)
        result={**integrity,'chain_id':CHAIN_ID,'contract_address':contract.address,'reporter':Web3.to_checksum_address(reporter),'report_id':report_id.hex(),
            'anchored_at_unix':anchor[3],'onchain_expected_digests':expected,'verified_at':utcnow(),'scope':'Digests obtained from immutable contract storage on public Sepolia; verifies file integrity, not investment truth'}
        write_json(self.directory/('verification-'+manifest['package_id']+'.json'),result);return result
