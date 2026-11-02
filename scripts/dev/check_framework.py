"""Offline teammate onboarding check; fresh temporary state, no API or user .env.

Run after installing the project: python scripts/dev/check_framework.py
This checks wiring and local API contracts, not investment or live-provider quality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import runpy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from research_agent.research_sdk import SDK_OPERATIONS
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app
from research_agent.tools import ResearchTools

ROOT = Path(__file__).resolve().parents[2]


def check_framework():
    # Real HTTP is an error; TestClient's in-process ASGI transport is permitted.
    with patch.object(httpx.HTTPTransport, 'handle_request',
                      side_effect=AssertionError('External HTTP is forbidden in this offline probe')):
        demo = runpy.run_path(str(ROOT / 'examples/framework/minimal_agent.py'))['run_demo']()
        with TemporaryDirectory(prefix='aether-onboarding-') as directory:
            runtime = AgentRuntime(root=Path(directory))
            app = create_app(runtime)
            with TestClient(app, base_url='http://127.0.0.1:8765') as client:
                assert client.get('/').status_code == 200
                assert client.get('/api/health').json()['active_jobs'] == []
                assert client.get('/api/config').json()['configured'] is False
                assert client.get('/api/portfolio').json()['empty'] is True
                assert client.get('/api/desk').json()['inbox'] == []
                assert client.post('/api/sessions', json={}).status_code == 403
                for asset in ('dashboard.js', 'dashboard.css', 'desk.js', 'desk.css',
                              'investment.js', 'investment.css', 'research-lab.js'):
                    assert client.get('/assets/' + asset).status_code == 200
            meta = runtime.create_session()
            workspace = runtime._session_dir(meta['session_id']) / 'workspace'
            service = ResearchTools(Path(meta['data_run']), workspace, meta['cutoff'],
                allow_live=True, live_data=runtime.live_data, knowledge=runtime.knowledge,
                investment=runtime.investment, web_search=SimpleNamespace(),
                reviewer=SimpleNamespace())
            names = sorted(t.name for t in service.as_tools())
            expected = {'list_data_sources', 'request_public_api', 'search_web', 'fetch_webpage',
                'read_evidence', 'register_calculation', 'get_private_portfolio',
                'record_news_event', 'submit_research_answer', 'publish_report',
                'get_research_context', 'recall_memory'}
            assert set(names) == expected
    return {'status': 'passed', 'python': platform.python_version(), 'demo': demo,
            'current_business_tools': names, 'sdk_operations': sorted(SDK_OPERATIONS),
            'fresh_empty_state': True, 'local_web_assets': True, 'local_token_guard': True,
            'user_credentials_loaded': False, 'external_model_calls': 0,
            'scope': 'Offline framework and clean-state checks; live APIs, model quality and OS portability are not established.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/framework-check.json')
    args = parser.parse_args()
    receipt = check_framework()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
