"""Small Python/CLI client for the active research session, with no provider keys.

Analysis code can import ordinary Python modules freely. This client is for data
capture and persistent application services which need the harness's source store.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path


# Names here are Python/CLI operations, not schemas registered with the LLM.
SDK_OPERATIONS = {
    'library-datasets':'library_datasets',
    'library-coverage':'library_coverage',
    'library-series':'library_series',
    'library-search':'library_search',
    'library-evidence':'library_evidence',
    'sources': 'list_data_sources',
    'fetch': 'get_live_data',
    'local-market': 'local_market',
    'collection-status': 'collection_status',
    'public-api': 'request_public_api',
    'archive-market': 'get_historical_market_data',
    'archive-chain': 'get_chain_data',
    'archive-events': 'search_events',
    'indicators': 'get_market_indicators',
    'eth-rpc': 'read_eth_rpc',
    'protocol-activity': 'get_protocol_activity',
    'simulate': 'run_paper_simulation',
    'strategy-start': 'start_strategy_experiment',
    'strategy-status': 'get_strategy_experiment',
    'position-preview': 'preview_position',
    'context': 'get_purchase_context',
    'memory-news': 'recall_memory',
    'memory-research': 'recall_memory',
    'memory-state': 'recall_memory',
    'memory-note': 'recall_memory',
    'memory-cards': 'recall_memory',
    'memory-cases': 'recall_memory',
}


class ResearchClient:
    """Access the current job's source-preserving services from its terminal."""

    def __init__(self, socket_path: str | None = None, timeout: float = 110):
        self.socket_path = socket_path or os.environ.get('AETHER_RESEARCH_SOCKET', '')
        self.timeout = timeout
        if not self.socket_path:
            raise ValueError('Run inside an active Aether research terminal; no session SDK is attached')

    def call(self, operation: str, **arguments):
        if operation not in SDK_OPERATIONS:
            raise ValueError('Unknown operation; use python -m research_agent.research_sdk --help')
        body = json.dumps({'operation': operation, 'arguments': arguments}).encode() + b'\n'
        if len(body) > 65536:
            raise ValueError('Research SDK arguments exceed 64 KiB')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            connection.connect(self.socket_path)
            connection.sendall(body)
            with connection.makefile('rb') as response:
                packet = json.loads(response.readline(32 * 1024 * 1024))
        if not packet.get('ok'):
            raise ValueError(packet.get('error', 'Research SDK request failed'))
        return packet['result']

    def fetch(self, metric: str, asset: str = 'ETH'):
        return self.call('fetch', metric=metric, asset=asset)

    def read_json(self, artifact, *, max_bytes=64*1024*1024):
        """Read a returned task artifact locally; no API request or value coercion.

        Accept the SDK packet or a virtual/relative/workspace-absolute filename.
        Financial decimal strings remain strings: parse chosen values with
        Decimal before arithmetic. This helper never reads outside the job root.
        """
        root=os.environ.get('RESEARCH_WORKSPACE')
        if not root:raise ValueError('RESEARCH_WORKSPACE is required for local artifacts')
        root=Path(root).resolve()
        if isinstance(artifact,dict):
            artifact=(artifact.get('terminal_data_file') or artifact.get('data_file') or artifact.get('path')
                or (artifact.get('series_file') or {}).get('path'))
        if not isinstance(artifact,(str,Path)):raise ValueError('Supply an artifact packet or file path')
        value=str(artifact);path=Path(value)
        if path.is_absolute() and path.parts[1:2] in [('data',),('notes',),('reports',),('memory',)]:
            path=root/value.lstrip('/')
        elif not path.is_absolute():path=root/path
        path=path.resolve()
        if not path.is_relative_to(root):raise ValueError('Artifact path escapes the current task workspace')
        if not isinstance(max_bytes,int) or isinstance(max_bytes,bool) or not 1024<=max_bytes<=512*1024*1024:
            raise ValueError('Use an explicit local JSON size bound between 1 KiB and 512 MiB')
        if path.stat().st_size>max_bytes:raise ValueError('Artifact exceeds local JSON size bound; select a smaller query/page')
        return json.loads(path.read_text())

    def local_market(self, metric: str, asset: str = 'ETH', **filters):
        return self.call('local-market', metric=metric, asset=asset, **filters)

    def archive_market(self, asset: str = 'ETH', days: int = 30):
        return self.call('archive-market', asset=asset, days=days)

    def library_series(self,dataset_id: str,**filters):
        return self.call('library-series',dataset_id=dataset_id,**filters)

    def library_search(self,query: str='',**filters):
        return self.call('library-search',query=query,**filters)

    def research_cases(self, query: str = '', **filters):
        """Recall source-linked hypotheses with explicit matching conditions."""
        if 'case_id' in filters:
            if query:raise ValueError('A case ID read cannot also search by query')
            return self.call('memory-cases', **filters)
        return self.call('memory-cases', query=query, **filters)

    def public_api(self, provider: str, path: str, params: dict | None = None):
        return self.call('public-api', provider=provider, path=path, params=params)

    def simulate(self, price_record_id: str, profile: dict | None = None, lookback_days: int = 7):
        return self.call('simulate', price_record_id=price_record_id, profile=profile,
                         lookback_days=lookback_days)

    def start_strategy(self, config: dict):
        return self.call('strategy-start', config=config)

    def strategy(self, run_id: str = ''):
        return self.call('strategy-status', run_id=run_id)

    def preview_position(self, asset: str, notional: float, **assumptions):
        return self.call('position-preview', asset=asset, notional=notional, **assumptions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=SDK_OPERATIONS)
    parser.add_argument('--args', default='{}', help='JSON object of named arguments')
    args = parser.parse_args()
    try:
        arguments = json.loads(args.args)
        if not isinstance(arguments, dict):
            raise ValueError('--args must be a JSON object')
        result = ResearchClient().call(args.operation, **arguments)
    except (ValueError, OSError) as exc:
        parser.exit(1, json.dumps({'status': 'error', 'error': str(exc)}) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
