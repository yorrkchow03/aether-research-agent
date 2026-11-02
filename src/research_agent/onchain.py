"""Read-only Ethereum RPC and bounded Aave V3 Core WETH event analysis."""
from __future__ import annotations
import hashlib
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import httpx
from research_data.http import dumps,utcnow
from research_data.normalize import iso,timestamp
from .live import atomic
from .rpc import ReadOnlyRPC, READ_METHODS

RPC_URL='https://ethereum-rpc.publicnode.com'
POOL='0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2'
WETH='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'
ATOKEN='0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8'
SUPPLY='0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61'
WITHDRAW='0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7'


class EthereumRead:
    def __init__(self,live,rpc_client=None,**rpc_options):
        self.live=live
        self.client=rpc_client or ReadOnlyRPC(live,endpoint=RPC_URL,**rpc_options)
    def rpc(self,method,params):
        return self.client.request(method,params)

    def probe_capabilities(self,historical_block=17000000,log_blocks=32):
        """Low-load observations; deliberately not a provider saturation test."""
        if not isinstance(historical_block,int) or isinstance(historical_block,bool) or historical_block<0 or not 1<=log_blocks<=500:
            raise ValueError('Use a historical block and1..500 log blocks')
        checks={};sources=[]
        def inspect(name,method,params):
            try:
                row=self.rpc(method,params);sources.append(row['record_id'])
                checks[name]={'status':'available','record_id':row['record_id'],'retrieved_at':row['retrieved_at']}
                return row
            except (ValueError,RuntimeError,httpx.HTTPError) as exc:
                checks[name]={'status':'unavailable','kind':getattr(exc,'kind','error'),'reason':str(exc)[:450]}
                return None
        chain=inspect('chain_id','eth_chainId',[])
        head=inspect('finalized_header','eth_getBlockByNumber',['finalized',False])
        cached=inspect('same_request_cache','eth_chainId',[]) if chain else None
        if cached:checks['same_request_cache']['same_original_record']=cached['record_id']==chain['record_id']
        if head:
            end=int(head['data']['number'],16)
            if historical_block>end:raise ValueError('Historical probe block is beyond the finalized head')
            inspect('historical_header','eth_getBlockByNumber',[hex(historical_block),False])
            inspect('historical_state','eth_call',[{'to':WETH,'data':'0x70a08231'+'0'*24+ATOKEN[2:].lower()},hex(historical_block)])
            inspect('small_log_window','eth_getLogs',[{'address':POOL,'fromBlock':hex(max(0,end-log_blocks+1)),
                'toBlock':hex(end),'topics':[[SUPPLY,WITHDRAW],'0x'+'0'*24+WETH[2:].lower()]}])
        return {'checked_at':utcnow(),'endpoint':self.client.endpoint,'client_policy':asdict(self.client.policy),
            'checks':checks,'source_record_ids':list(dict.fromkeys(sources)),
            'provider_maximum_requests_per_second':None,'provider_maximum_log_range':None,
            'observed_log_span':log_blocks if checks.get('small_log_window',{}).get('status')=='available' else None,
            'scope':'Low-load capability observations and conservative client bounds; maxima/SLAs and historical completeness are not measured. Original capture times remain unchanged on cache hits.'}

    def aave_weth(self,blocks=1000,end_block=None):
        if not 1<=blocks<=15000:raise ValueError('Window supports1..15000 blocks; no unbounded log scans')
        inputs=[]
        def call(method,params):
            r=self.rpc(method,params);inputs.append(r['record_id']);return r['data']
        if int(call('eth_chainId',[]),16)!=1:raise ValueError('Expected Ethereum mainnet')
        end=call('eth_getBlockByNumber',[hex(end_block) if end_block is not None else 'finalized',False])
        last=int(end['number'],16);first=max(0,last-blocks+1);start=call('eth_getBlockByNumber',[hex(first),False])
        # Contract/reserve filtering is done by the node, then verified locally.
        events=[]
        reserve_topic='0x'+'0'*24+WETH[2:].lower()
        for record in self.client.log_batches(POOL,[[SUPPLY,WITHDRAW],reserve_topic],first,last):
            inputs.append(record['record_id']);query=record['rpc_params'][0]
            events.extend(decode_weth_events(record['data'],int(query['fromBlock'],16),int(query['toBlock'],16)))
        identities=[(e['transaction_hash'],e['log_index']) for e in events]
        if len(identities)!=len(set(identities)):raise ValueError('Duplicate logs in bounded event window')
        supply=sum(Decimal(e['amount_eth']) for e in events if e['event']=='Supply')
        withdraw=sum(Decimal(e['amount_eth']) for e in events if e['event']=='Withdraw')
        balance_call={'to':WETH,'data':'0x70a08231'+'0'*24+ATOKEN[2:].lower()}
        gaps=[]
        values={'supply_amount_eth':str(supply),'withdraw_amount_eth':str(withdraw),'net_supply_eth':str(supply-withdraw),
                'event_count':str(len(events)),'unique_transaction_count':str(len(set(e['transaction_hash'] for e in events))),
                'block_start':str(first),'block_end':str(last)}
        if end.get('baseFeePerGas') is not None:
            values['base_fee_gwei']=str(Decimal(int(end['baseFeePerGas'],16))/Decimal(10**9))
        else:gaps.append({'metric':'base_fee_gwei','status':'unavailable','reason':'Block header omits baseFeePerGas; no zero substitute'})
        for tag,key in ((hex(first),'available_liquidity_start_eth'),(hex(last),'available_liquidity_end_eth')):
            try:values[key]=str(Decimal(int(call('eth_call',[balance_call,tag]),16))/Decimal(10**18))
            except (ValueError,RuntimeError,httpx.HTTPError) as exc:gaps.append({'metric':key,'status':'unavailable','reason':str(exc)[:400]})
        units={k:'ETH' if k.endswith('_eth') else 'gwei' if k.endswith('_gwei') else 'count' for k in values}
        now=utcnow();rid='live-'+hashlib.sha256(dumps({'inputs':inputs,'values':values,'version':'aave-core-weth-v1'},sort_keys=True).encode()).hexdigest()[:24]
        row={'record_id':rid,'dataset_id':'live.aave_core.weth','observed_at':iso(timestamp(int(end['timestamp'],16))),
             'retrieved_at':now,'values':values,'units':units,'input_record_ids':inputs,'events':events,'gaps':gaps,
             'window_start':iso(timestamp(int(start['timestamp'],16))),'window_end':iso(timestamp(int(end['timestamp'],16))),
             'scope':'Ethereum Aave V3 Core Pool, WETH reserve ONLY; gross Supply minus Withdraw events, not total protocol capital inflow or ETH buying',
             'pool':POOL,'reserve':WETH,'a_token':ATOKEN,'method':'Decode verified Supply/Withdraw event ABI;18 decimal WETH; liquidity is WETH.balanceOf(aWETH) at fixed blocks',
             'configuration_sources':['https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol','https://github.com/aave/aave-v3-core/blob/master/contracts/interfaces/IPool.sol'],
             'quality_flags':['derived','finalized_block_window' if end_block is None else 'explicit_historical_block_window','limited_reserve_scope','not_net_external_inflow']}
        atomic(self.live.root/'records'/(rid+'.json'),row);return row


def decode_weth_events(logs,first,last):
    events=[]
    for log in logs:
        if log.get('removed'):raise ValueError('Removed/reorganized log is not eligible')
        if log['address'].lower()!=POOL.lower() or not first<=int(log['blockNumber'],16)<=last:raise ValueError('Log outside requested pool/block range')
        topics=log['topics'];kind=topics[0].lower()
        if len(topics)!=4 or topics[1][-40:].lower()!=WETH[2:].lower() or kind not in (SUPPLY,WITHDRAW):raise ValueError('Unexpected event signature/reserve')
        data=log['data'].removeprefix('0x')
        if len(data)!=(128 if kind==SUPPLY else 64):raise ValueError('Unexpected event ABI payload length')
        amount=int(data[64:128] if kind==SUPPLY else data,16)
        events.append({'event':'Supply' if kind==SUPPLY else 'Withdraw','amount_eth':str(Decimal(amount)/Decimal(10**18)),
                       'block_number':int(log['blockNumber'],16),'block_hash':log['blockHash'],
                       'transaction_hash':log['transactionHash'],'log_index':int(log['logIndex'],16)})
    return events
