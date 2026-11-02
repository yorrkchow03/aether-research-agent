"""Actual local EVM test of deployment, anchoring and tamper detection. No public claim."""
import json,sys,tempfile,shutil,hashlib
from pathlib import Path
from unittest.mock import patch,PropertyMock
from web3 import Web3,EthereumTesterProvider
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
from research_agent.chain_anchor import SepoliaAnchor,write_json,assert_sepolia,CHAIN_ID
from research_agent.audit import BundleBuilder

checks={}
try:assert_sepolia(1);checks['mainnet_refused']=False
except ValueError:checks['mainnet_refused']=True
with tempfile.TemporaryDirectory() as tmp:
 project=Path(tmp);(project/'contracts').mkdir()
 for name in ('AetherResearchAnchor.sol','AetherResearchAnchor.artifact.json'):shutil.copyfile(root/'contracts'/name,project/'contracts'/name)
 provider=EthereumTesterProvider();w=Web3(provider);owner=w.eth.accounts[0];key=provider.ethereum_tester.backend.account_keys[0].to_bytes().hex()
 write_json(project/'.audit-tools/sepolia_wallet.json',{'private_key':key,'address':owner,'chain_id':CHAIN_ID,'purpose':'local fixture','created_at':'fixture'},private=True)
 checks['private_wallet_mode']=((project/'.audit-tools/sepolia_wallet.json').stat().st_mode&0o777)==0o600
 run=project/'data/run';run.mkdir(parents=True);(run/'manifest.json').write_text(json.dumps({'sources':[]}));workspace=project/'workspace';(workspace/'reports').mkdir(parents=True)
 report={'report':{'observations':[{'evidence_ids':['r1']}],'interpretations':[],'forecasts':[]},'created_at':'2026-10-14T00:00:00Z','cutoff':'2026-10-13T00:00:00Z','mode':'observed','research_mode':'historical'}
 (workspace/'reports/research-0123456789ab.json').write_text(json.dumps(report));(workspace/'reports/research-0123456789ab.md').write_text('fixture42')
 package=BundleBuilder(project,project/'data/audit').build(workspace,run,'/reports/research-0123456789ab.md',lambda rid:{'record_id':rid,'values':{'value':'42'},'units':{'value':'count'}},{})
 # Only the test fixture advertises the Sepolia ID. Production has no override flag.
 with patch.object(type(w.eth),'chain_id',new_callable=PropertyMock,return_value=CHAIN_ID):
  client=SepoliaAnchor(project,w);deployment=client.deploy();checks['compiled_runtime_verified']=bool(client.contract(deployment['contract_address']))
  anchored=client.anchor(package['path']);checks['client_anchor_and_verify']=anchored['status']=='passed'
  again=client.deploy();checks['deployment_is_reused']=again['tx_hash']==deployment['tx_hash']
  before=len(list((project/'data/chain').glob('transaction-*.json')));client.anchor(package['path']);checks['existing_anchor_is_reused']=len(list((project/'data/chain').glob('transaction-*.json')))==before
  bundle=Path(package['path']);(bundle/'report/report.md').write_text('changed');verification=client.verify(bundle,owner,deployment['contract_address'])
  checks['tampered_report_rejected']=verification['status']=='failed' and any(i.get('hash')=='report_hash' for i in verification['issues'])
  (bundle/'config.json').unlink();verification=client.verify(bundle,owner,deployment['contract_address']);checks['missing_config_rejected']=verification['status']=='failed'
  artifact={'scope':'Local EthereumTester EVM only; production Sepolia identity gate tested independently. No public deployment claimed.',
            'checks':checks,'local_deploy_tx':deployment['tx_hash'],'local_contract':deployment['contract_address']}
  (root/'reports/chain_client_tests.json').write_text(json.dumps(artifact,indent=2));print(json.dumps(artifact,indent=2))
if not all(checks.values()):raise SystemExit(1)
