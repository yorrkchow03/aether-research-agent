"""RPC failure/caching contracts, using mocked transport rather than a provider."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

import httpx

from research_agent.rpc import ReadOnlyRPC, RPCFailure, RPCPolicy
from research_agent.onchain import EthereumRead, POOL


class Clock:
    def __init__(self):self.value=1000.;self.waits=[]
    def now(self):return self.value
    def sleep(self,seconds):self.waits.append(seconds);self.value+=seconds


def result(value, *, identity=1):
    return httpx.Response(200,json={'jsonrpc':'2.0','id':identity,'result':value})


class RPCTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.live=SimpleNamespace(root=Path(tmp.name));self.clock=Clock();self.calls=[]
        self.endpoint='https://rpc.example.test'
        ReadOnlyRPC._last_request.pop(self.endpoint,None)

    def client(self,handler=None,**policy):
        def send(request):
            self.calls.append(request)
            self.assertNotIn('authorization',request.headers)
            return (handler or (lambda r:result('0x1')))(request)
        return ReadOnlyRPC(self.live,endpoint=self.endpoint,
            policy=RPCPolicy(min_interval_seconds=0,**policy),transport=httpx.MockTransport(send),
            sleep=self.clock.sleep,monotonic=self.clock.now,wall_time=self.clock.now,jitter=lambda x:0)

    def test_cache_hit_preserves_original_record_capture_and_raw_identity(self):
        rpc=self.client();first=rpc.request('eth_chainId',[]);self.clock.value+=20
        second=rpc.request('eth_chainId',[])
        self.assertEqual(len(self.calls),1);self.assertEqual(first,second)
        self.assertEqual(first['retrieved_at'],second['retrieved_at'])
        self.assertNotIn('cache_hit',second)  # Serving metadata cannot mutate citable evidence.

    def test_expired_head_is_refetched_instead_of_relabeled_fresh(self):
        rpc=self.client(head_cache_seconds=5)
        first=rpc.request('eth_getBlockByNumber',['finalized',False]);self.clock.value+=6
        second=rpc.request('eth_getBlockByNumber',['finalized',False])
        self.assertEqual(len(self.calls),2);self.assertNotEqual(first['record_id'],second['record_id'])

    def test_number_block_cache_has_explicit_finite_ttl(self):
        rpc=self.client(historical_cache_seconds=10)
        rpc.request('eth_call',[{'to':POOL,'data':'0x'},'0x100']);self.clock.value+=9
        rpc.request('eth_call',[{'to':POOL,'data':'0x'},'0x100']);self.assertEqual(len(self.calls),1)
        self.clock.value+=2;rpc.request('eth_call',[{'to':POOL,'data':'0x'},'0x100'])
        self.assertEqual(len(self.calls),2)

    def test_raw_cache_tampering_is_an_error_not_an_unnoticed_refresh(self):
        rpc=self.client();row=rpc.request('eth_chainId',[])
        Path(row['raw_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(RPCFailure,'cache_integrity'):rpc.request('eth_chainId',[])
        self.assertEqual(len(self.calls),1)

    def test_normalized_cache_tampering_is_detected(self):
        rpc=self.client();row=rpc.request('eth_chainId',[])
        path=self.live.root/'records'/(row['record_id']+'.json');row['data']='0x99';path.write_text(json.dumps(row))
        with self.assertRaisesRegex(RPCFailure,'cache_integrity'):rpc.request('eth_chainId',[])

    def test_throttle_respects_retry_after_and_saves_attempts(self):
        def send(r):return httpx.Response(429,headers={'Retry-After':'2'}) if len(self.calls)==1 else result('0x1')
        rpc=self.client(send);row=rpc.request('eth_chainId',[])
        self.assertEqual(len(self.calls),2);self.assertEqual(row['upstream_attempts'],2)
        self.assertEqual(self.clock.waits,[2.])
        receipt=json.loads(next((rpc.root/'diagnostics').glob('*.json')).read_text())
        self.assertEqual(receipt['attempts'][0]['kind'],'throttled')

    def test_retry_after_beyond_budget_stops_without_retrying_early(self):
        rpc=self.client(lambda r:httpx.Response(429,headers={'Retry-After':'600'}))
        with self.assertRaisesRegex(RPCFailure,'throttled'):rpc.request('eth_chainId',[])
        self.assertEqual(len(self.calls),1);self.assertEqual(self.clock.waits,[])

    def test_transient_http_retries_have_bounded_exponential_backoff(self):
        rpc=self.client(lambda r:httpx.Response(503))
        with self.assertRaises(RPCFailure):rpc.request('eth_chainId',[])
        self.assertEqual(len(self.calls),3);self.assertEqual(self.clock.waits,[.75,1.5])

    def test_request_deadline_prevents_an_extra_throttled_attempt(self):
        rpc=self.client(lambda r:httpx.Response(429),request_budget_seconds=.5)
        with self.assertRaisesRegex(RPCFailure,'throttled'):rpc.request('eth_chainId',[])
        self.assertEqual(len(self.calls),1)

    def test_missing_historical_state_is_not_cached_or_zero_filled(self):
        rpc=self.client(lambda r:httpx.Response(200,json={'jsonrpc':'2.0','id':1,'error':{'code':-32000,'message':'missing trie node; historical state pruned'}}))
        with self.assertRaisesRegex(RPCFailure,'history_unavailable'):rpc.request('eth_call',[{},'0x100'])
        self.assertEqual(len(self.calls),1);self.assertEqual(list((rpc.root/'cache').glob('*.json')),[])

    def test_invalid_params_is_permanent(self):
        rpc=self.client(lambda r:httpx.Response(200,json={'jsonrpc':'2.0','id':1,'error':{'code':-32602,'message':'bad params'}}))
        with self.assertRaises(RPCFailure):rpc.request('eth_call',[])
        self.assertEqual(len(self.calls),1)

    def test_http_range_error_is_split_with_exact_contiguous_coverage(self):
        def send(request):
            query=json.loads(request.content)['params'][0]
            lo,hi=int(query['fromBlock'],16),int(query['toBlock'],16)
            if hi-lo+1>3:return httpx.Response(400,json={'error':{'code':-32602,'message':'block range limit is3'}})
            return result([])
        rpc=self.client(send,initial_log_blocks=8)
        batches=list(rpc.log_batches(POOL,[],100,112))
        coverage=[n for row in batches for n in range(int(row['rpc_params'][0]['fromBlock'],16),int(row['rpc_params'][0]['toBlock'],16)+1)]
        self.assertEqual(coverage,list(range(100,113)))
        self.assertTrue(all(int(row['rpc_params'][0]['toBlock'],16)-int(row['rpc_params'][0]['fromBlock'],16)<3 for row in batches))
        self.assertLess(len(self.calls),15)

    def test_range_failure_at_single_block_terminates(self):
        rpc=self.client(lambda r:httpx.Response(200,json={'jsonrpc':'2.0','id':1,'error':{'code':-32005,'message':'too many results'}}),initial_log_blocks=8)
        with self.assertRaisesRegex(RPCFailure,'range_limit'):list(rpc.log_batches(POOL,[],100,107))
        self.assertEqual(len(self.calls),4)

    def test_log_request_budget_never_returns_an_incomplete_window_as_complete(self):
        rpc=self.client(lambda r:result([]),initial_log_blocks=1,max_log_requests=2)
        with self.assertRaisesRegex(RPCFailure,'incomplete'):list(rpc.log_batches(POOL,[],100,104))
        self.assertEqual(len(self.calls),2)

    def test_read_only_and_bounded_queries_fail_before_network(self):
        rpc=self.client()
        for method,params in [('eth_sendRawTransaction',['0xabc']),('eth_getLogs',[{'fromBlock':'latest','toBlock':'latest'}]),('eth_getLogs',[{'address':POOL,'fromBlock':'0x0','toBlock':'0x2000'}]),('eth_call',[float('nan')])]:
            with self.assertRaises(ValueError):rpc.request(method,params)
        self.assertEqual(self.calls,[])

    def test_block_hash_filter_is_exclusive_with_number_range(self):
        rpc=self.client(lambda r:result([]));h='0x'+'a'*64
        rpc.request('eth_getLogs',[{'address':POOL,'blockHash':h}])
        with self.assertRaises(ValueError):rpc.request('eth_getLogs',[{'address':POOL,'blockHash':h,'fromBlock':'0x1'}])
        self.assertEqual(len(self.calls),1)

    def test_wrong_response_identity_and_malformed_logs_are_rejected(self):
        for response in [result([],identity=2),result({'not':'logs'}),httpx.Response(200,text='not json')]:
            rpc=self.client(lambda r:response)
            with self.assertRaisesRegex(RPCFailure,'protocol'):rpc.request('eth_getLogs',[{'address':POOL,'fromBlock':'0x1','toBlock':'0x2'}])

    def test_null_receipt_is_unknown_and_an_empty_log_array_is_valid(self):
        rpc=self.client(lambda r:result(None))
        with self.assertRaisesRegex(RPCFailure,'not_found'):rpc.request('eth_getTransactionReceipt',['0x'+'a'*64])
        rpc=self.client(lambda r:result([]));row=rpc.request('eth_getLogs',[{'address':POOL,'fromBlock':'0x1','toBlock':'0x2'}])
        self.assertEqual(row['data'],[])

    def test_concurrent_identical_requests_share_one_original_capture(self):
        rpc=self.client();rows=[];errors=[]
        def run():
            try:rows.append(rpc.request('eth_chainId',[]))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=run) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[]);self.assertEqual(len(self.calls),1)
        self.assertEqual(len({r['record_id'] for r in rows}),1)

    def test_transport_timeout_is_bounded_and_saved(self):
        def send(request):raise httpx.ReadTimeout('synthetic timeout')
        rpc=self.client(send)
        with self.assertRaisesRegex(RPCFailure,'transport'):rpc.request('eth_chainId',[])
        self.assertEqual(len(self.calls),3)
        self.assertEqual(len(list((rpc.root/'diagnostics').glob('*.json'))),1)

    def test_endpoint_credentials_and_invalid_policy_are_refused(self):
        for endpoint in ['http://rpc.example.test','https://secret@rpc.example.test','https://rpc.example.test?key=secret']:
            with self.assertRaises(ValueError):ReadOnlyRPC(self.live,endpoint=endpoint)
        for kwargs in [{'max_attempts':2.5},{'min_interval_seconds':float('nan')},{'initial_log_blocks':6000},{'max_attempts':True}]:
            with self.assertRaises(ValueError):RPCPolicy(**kwargs)

    def test_response_byte_bound_does_not_create_partial_evidence(self):
        rpc=self.client(lambda r:result('a'*1000),max_response_bytes=80)
        with self.assertRaisesRegex(RPCFailure,'range_limit'):rpc.request('eth_chainId',[])
        self.assertEqual(list((self.live.root/'records').glob('*.json')),[])

    def test_missing_base_fee_and_archived_balance_stay_unknown(self):
        (self.live.root/'records').mkdir()
        class Fake:
            def request(self,method,params):
                if method=='eth_chainId':data='0x1'
                elif method=='eth_getBlockByNumber':data={'number':params[0] if params[0]!='finalized' else '0x100','timestamp':'0x1000'}
                else:raise RPCFailure('history_unavailable','synthetic archived state gap')
                return {'record_id':'live-'+'a'*24,'data':data}
            def log_batches(self,*args):return iter([])
        row=EthereumRead(self.live,rpc_client=Fake()).aave_weth(10)
        self.assertNotIn('base_fee_gwei',row['values'])
        self.assertNotIn('available_liquidity_start_eth',row['values'])
        self.assertEqual({x['metric'] for x in row['gaps']},{'base_fee_gwei','available_liquidity_start_eth','available_liquidity_end_eth'})


if __name__=='__main__':unittest.main()
