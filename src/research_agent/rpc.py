"""Bounded read-only RPC, original-time cache and adaptive log batches.

Client policies are conservative choices, not discovered provider maxima. Raw
receipts and hashes establish what was returned, not node honesty/completeness.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import random
import re
import threading
import time
from urllib.parse import urlsplit
import uuid

import httpx

from research_data.http import dumps, utcnow
from .live import atomic

DEFAULT_ENDPOINT = 'https://ethereum-rpc.publicnode.com'
READ_METHODS = frozenset({'eth_chainId', 'eth_blockNumber', 'eth_getBlockByNumber',
    'eth_getLogs', 'eth_call', 'eth_getCode', 'eth_getTransactionReceipt'})


@dataclass(frozen=True)
class RPCPolicy:
    timeout_seconds: float = 12
    max_attempts: int = 3
    min_interval_seconds: float = .8
    initial_backoff_seconds: float = .75
    max_backoff_seconds: float = 12
    request_budget_seconds: float = 45
    head_cache_seconds: float = 5
    historical_cache_seconds: float = 300
    initial_log_blocks: int = 500
    max_log_blocks: int = 5000
    max_window_blocks: int = 15000
    max_log_requests: int = 64
    scan_budget_seconds: float = 120
    max_response_bytes: int = 12_000_000

    def __post_init__(self):
        values = asdict(self)
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values.values()):
            raise ValueError('RPC policy values must be finite and nonnegative')
        for name in ('max_attempts', 'initial_log_blocks', 'max_log_blocks', 'max_window_blocks', 'max_log_requests', 'max_response_bytes'):
            if not isinstance(values[name], int) or isinstance(values[name], bool):
                raise ValueError('RPC count/byte bounds must be integers')
        if not 1 <= self.max_attempts <= 5 or self.timeout_seconds <= 0 or self.request_budget_seconds <= 0:
            raise ValueError('Invalid RPC retry/time budget')
        if not 1 <= self.initial_log_blocks <= self.max_log_blocks <= 5000:
            raise ValueError('RPC log batches must be1..5000 blocks')
        if not 1 <= self.max_window_blocks <= 15000 or not 1 <= self.max_log_requests <= 128:
            raise ValueError('Invalid RPC scan bounds')


class RPCFailure(RuntimeError):
    def __init__(self, kind, message, *, retryable=False, status=None, code=None):
        self.kind, self.retryable, self.status, self.code = kind, retryable, status, code
        super().__init__('Ethereum RPC '+kind+': '+str(message)[:350])


def classify_error(message, code=None, status=None):
    text = str(message).lower()
    if status == 429 or any(s in text for s in ('rate limit', 'too many requests', 'requests per second', 'request quota')):
        return RPCFailure('throttled', message, retryable=True, status=status, code=code)
    if status == 413 or any(s in text for s in ('block range', 'too many results', 'response size', 'query returned more', 'log limit', 'range limit')) or code == -32005:
        return RPCFailure('range_limit', message, status=status, code=code)
    if any(s in text for s in ('missing trie', 'pruned', 'historical state', 'archive node', 'archive request', 'state is not available', 'header not found')):
        return RPCFailure('history_unavailable', message, status=status, code=code)
    return RPCFailure('provider_error', message, retryable=status in (500, 502, 503, 504), status=status, code=code)


class ReadOnlyRPC:
    _guard = threading.Lock()
    _locks = {}
    _last_request = {}

    def __init__(self, live, *, endpoint=DEFAULT_ENDPOINT, policy=None, transport=None,
                 sleep=time.sleep, monotonic=time.monotonic, wall_time=time.time, jitter=None):
        u = urlsplit(endpoint)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Public RPC endpoint must be HTTPS without credentials/query parameters')
        self.live, self.endpoint = live, endpoint
        self.policy = policy or RPCPolicy()
        self.transport, self.sleep, self.monotonic, self.wall_time = transport, sleep, monotonic, wall_time
        self.jitter = jitter or (lambda delay: random.uniform(0, delay * .2))
        self.endpoint_key = hashlib.sha256(endpoint.encode()).hexdigest()[:20]
        self.root = Path(live.root) / 'rpc' / self.endpoint_key
        for d in (self.root/'cache', self.root/'diagnostics', Path(live.root)/'raw', Path(live.root)/'records'):
            d.mkdir(parents=True, exist_ok=True)
        with self._guard:
            self.lock = self._locks.setdefault(endpoint, threading.RLock())
        self.log_hint = self.policy.initial_log_blocks

    def _diagnostic(self, data):
        atomic(self.root/'diagnostics'/(uuid.uuid4().hex+'.json'),
               {'checked_at': utcnow(), 'endpoint': self.endpoint, **data})

    def _validate(self, method, params):
        if method not in READ_METHODS:
            raise ValueError('Only read-only Ethereum RPC methods are enabled')
        if not isinstance(params, list) or len(dumps(params)) > 5000:
            raise ValueError('RPC parameters must be a bounded list')
        try:
            json.dumps(params, allow_nan=False)
        except (ValueError, TypeError):
            raise ValueError('RPC parameters must be finite JSON values') from None
        if method == 'eth_getLogs':
            if len(params) != 1 or not isinstance(params[0], dict):
                raise ValueError('Logs require one filter object')
            f = params[0]
            if not f.get('address'):
                raise ValueError('Logs require an explicit contract address')
            addresses = f['address'] if isinstance(f['address'], list) else [f['address']]
            if len(addresses) > 8 or any(not isinstance(a, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', a) for a in addresses):
                raise ValueError('Logs require valid bounded contract addresses')
            if f.get('blockHash'):
                if 'fromBlock' in f or 'toBlock' in f or not re.fullmatch(r'0x[0-9a-fA-F]{64}', f['blockHash']):
                    raise ValueError('blockHash is exclusive with a numeric range')
            else:
                if not all(isinstance(f.get(k), str) and re.fullmatch(r'0x[0-9a-fA-F]+', f[k]) for k in ('fromBlock', 'toBlock')):
                    raise ValueError('Logs require an explicit numeric block range')
                if not 0 <= int(f['toBlock'], 16)-int(f['fromBlock'], 16) < self.policy.max_log_blocks:
                    raise ValueError('Each log request is limited to'+str(self.policy.max_log_blocks)+' blocks; use bounded batches')

    def _ttl(self, method, params):
        if method == 'eth_chainId':
            return 3600
        # A number alone is not a finality proof. Even old-number caches expire.
        if any(tag in dumps(params) for tag in ('"latest"', '"pending"', '"safe"', '"finalized"')) or method == 'eth_blockNumber':
            return self.policy.head_cache_seconds
        return self.policy.historical_cache_seconds

    def _cached(self, key, payload):
        path = self.root/'cache'/(key+'.json')
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text())
            if self.wall_time() >= float(entry['expires_at']):
                return None
            rid = entry['record_id']
            if not re.fullmatch(r'live-[a-f0-9]{24}', rid):
                raise ValueError('Invalid cached record ID')
            row = json.loads((Path(self.live.root)/'records'/(rid+'.json')).read_text())
            raw = Path(row['raw_path']).resolve()
            if not raw.is_relative_to((Path(self.live.root)/'raw').resolve()):
                raise ValueError('Raw cache path escaped its source directory')
            body = raw.read_bytes()
            if hashlib.sha256(body).hexdigest() != row['source']['sha256'] or row['source']['url'] != self.endpoint:
                raise ValueError('Raw receipt or endpoint changed')
            obj = json.loads(body)
            if dumps(row['source']['request_body'], sort_keys=True) != dumps(payload, sort_keys=True) or obj['result'] != row['data']:
                raise ValueError('Cached request/result changed')
            if row['rpc_method'] != payload['method'] or row['rpc_params'] != payload['params']:
                raise ValueError('Normalized cached request changed')
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise RPCFailure('cache_integrity', str(exc)) from None
        self._diagnostic({'method': payload['method'], 'cache_hit': True, 'record_id': rid,
                          'original_retrieved_at': row['retrieved_at'], 'upstream_attempts': 0})
        return row

    def _archive(self, payload, body, data, attempts):
        now = utcnow(); digest = hashlib.sha256(body).hexdigest()
        raw = Path(self.live.root)/'raw'/(digest+'.bin')
        if raw.exists() and raw.read_bytes() != body:
            raise RPCFailure('receipt_integrity', 'Existing content-addressed raw file changed')
        raw.write_bytes(body)
        rid = 'live-'+hashlib.sha256((self.endpoint+dumps(payload, sort_keys=True)+digest+now).encode()).hexdigest()[:24]
        row = {'record_id': rid, 'dataset_id': 'live.ethereum_rpc', 'observed_at': now,
            'retrieved_at': now, 'available_at': now, 'values': {}, 'units': {}, 'data': data,
            'rpc_method': payload['method'], 'rpc_params': payload['params'], 'raw_path': str(raw),
            'source': {'source_id': rid, 'name': 'Ethereum '+payload['method'], 'url': self.endpoint,
                       'retrieved_at': now, 'sha256': digest, 'request_body': payload, 'http_status': 200},
            'rpc_policy': asdict(self.policy), 'upstream_attempts': attempts,
            'quality_flags': ['live_rpc', 'block_context_must_be_checked', 'ttl_cache_is_not_finality_proof']}
        atomic(Path(self.live.root)/'records'/(rid+'.json'), row)
        return row

    def _delay(self, attempt, retry_after):
        if retry_after:
            try:
                seconds = max(0, float(retry_after))
            except ValueError:
                try:
                    seconds = max(0, parsedate_to_datetime(retry_after).timestamp()-self.wall_time())
                except (ValueError, TypeError, OverflowError):
                    seconds = 0
            if seconds > self.policy.max_backoff_seconds:
                return None  # Stop instead of retrying earlier than Retry-After.
        else:
            seconds = 0
        backoff = min(self.policy.max_backoff_seconds, self.policy.initial_backoff_seconds*2**attempt)
        return max(seconds, min(self.policy.max_backoff_seconds, backoff+self.jitter(backoff)))

    def request(self, method, params, *, force=False):
        self._validate(method, params)
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
        key = hashlib.sha256(dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if not force:
                cached = self._cached(key, payload)
                if cached is not None:
                    return cached
            started = self.monotonic(); attempts = []; failure = None
            with httpx.Client(transport=self.transport, trust_env=self.transport is None) as client:
                for attempt in range(self.policy.max_attempts):
                    wait = max(0, self.policy.min_interval_seconds-(self.monotonic()-self._last_request.get(self.endpoint, -1e20)))
                    if self.monotonic()-started+wait >= self.policy.request_budget_seconds:
                        failure = RPCFailure('budget', 'Request time budget exhausted'); break
                    if wait:
                        self.sleep(wait)
                    self._last_request[self.endpoint] = self.monotonic()
                    retry_after = None; status = None
                    try:
                        timeout = min(self.policy.timeout_seconds, self.policy.request_budget_seconds-(self.monotonic()-started))
                        with client.stream('POST', self.endpoint, json=payload, timeout=timeout) as response:
                            status = response.status_code; retry_after = response.headers.get('Retry-After')
                            chunks = []; size = 0
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > self.policy.max_response_bytes:
                                    raise RPCFailure('range_limit', 'Response exceeds local byte bound')
                                chunks.append(chunk)
                            body = b''.join(chunks)
                        if status != 200:
                            try:
                                error = json.loads(body).get('error', {})
                                if not isinstance(error, dict):error = {}
                            except (ValueError, AttributeError):
                                error = {}
                            raise classify_error(error.get('message', 'HTTP '+str(status)), error.get('code'), status)
                        try:
                            obj = json.loads(body)
                        except (ValueError, UnicodeError):
                            raise RPCFailure('protocol', 'Invalid JSON response') from None
                        if not isinstance(obj, dict) or obj.get('jsonrpc') != '2.0' or obj.get('id') != 1:
                            raise RPCFailure('protocol', 'Response envelope/ID mismatch')
                        if obj.get('error'):
                            error = obj['error']
                            if not isinstance(error, dict):
                                raise RPCFailure('protocol', 'Malformed RPC error object')
                            raise classify_error(error.get('message', 'Provider RPC error'), error.get('code'))
                        if 'result' not in obj or obj['result'] is None:
                            raise RPCFailure('not_found', 'Result unavailable; not a zero observation')
                        if method == 'eth_getLogs' and (not isinstance(obj['result'], list) or any(not isinstance(x, dict) for x in obj['result'])):
                            raise RPCFailure('protocol', 'Logs result must be an array of objects')
                        row = self._archive(payload, body, obj['result'], attempt+1)
                        atomic(self.root/'cache'/(key+'.json'), {'record_id': row['record_id'],
                            'expires_at': self.wall_time()+self._ttl(method, params)})
                        self._diagnostic({'method': method, 'cache_hit': False, 'record_id': row['record_id'],
                                          'attempts': attempts+[{'status': status, 'result': 'success'}]})
                        return row
                    except httpx.HTTPError as exc:
                        failure = RPCFailure('transport', type(exc).__name__, retryable=True)
                    except RPCFailure as exc:
                        failure = exc
                    attempts.append({'status': status, 'kind': failure.kind, 'message': str(failure)})
                    if not failure.retryable or attempt+1 >= self.policy.max_attempts:
                        break
                    delay = self._delay(attempt, retry_after)
                    if delay is None or self.monotonic()-started+delay >= self.policy.request_budget_seconds:
                        break
                    attempts[-1]['backoff_seconds'] = delay
                    self.sleep(delay)
            self._diagnostic({'method': method, 'cache_hit': False, 'attempts': attempts,
                              'failure': str(failure), 'upstream_attempts': len(attempts)})
            raise failure or RPCFailure('budget', 'Request did not run')

    def log_batches(self, address, topics, first, last):
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (first, last)) or not 0 <= last-first < self.policy.max_window_blocks:
            raise ValueError('Invalid bounded log window')
        if first < 0:
            raise ValueError('Negative block number')
        pending = deque((lo, min(lo+self.log_hint-1, last)) for lo in range(first, last+1, self.log_hint))
        requests = 0; started = self.monotonic()
        while pending:
            lo, hi = pending.popleft()
            if hi-lo+1 > self.log_hint:
                mid = min(hi, lo+self.log_hint-1); pending.appendleft((mid+1, hi)); pending.appendleft((lo, mid)); continue
            if requests >= self.policy.max_log_requests or self.monotonic()-started >= self.policy.scan_budget_seconds:
                raise RPCFailure('budget', 'Log scan incomplete; no complete-window metric may be published')
            requests += 1
            try:
                yield self.request('eth_getLogs', [{'address': address, 'topics': topics,
                    'fromBlock': hex(lo), 'toBlock': hex(hi)}])
            except RPCFailure as exc:
                if exc.kind != 'range_limit' or lo == hi:
                    raise
                self.log_hint = max(1, (hi-lo+1)//2)
                mid = lo+self.log_hint-1
                pending.appendleft((mid+1, hi)); pending.appendleft((lo, mid))
