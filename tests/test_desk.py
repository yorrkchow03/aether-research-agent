"""Saved-data product contracts in an isolated workspace, without provider calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app
from research_agent.desk import market_baseline, market_changes, report_changes


def draft(text='Saved observation', judgment='insufficient_evidence', rid='page-'+'a'*24):
    return {'title': 'Synthetic report', 'summary': text, 'judgment': judgment,
            'judgment_reason': 'Synthetic fixture', 'observations': [{'statement': text, 'evidence_ids': [rid]}],
            'interpretations': [], 'limitations': ['Synthetic isolated data.']}


class DeskTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = AgentRuntime(root=self.root, model_mode='current_conversation_no_api')
        self.app = create_app(self.runtime)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8765'); self.addCleanup(self.client.close)
        self.headers = {'x-local-token': self.app.state.csrf_token}

    def test_empty_desk_does_not_invent_positions_research_or_monitoring(self):
        with patch.object(self.runtime.live_data, 'get', side_effect=AssertionError('No refresh allowed')):
            data = self.client.get('/api/desk').raise_for_status().json()
        self.assertEqual(data['inbox'], [])
        self.assertIsNone(data['latest_report'])
        self.assertEqual(data['scheduler']['enabled'], 0)
        self.assertEqual(self.runtime.sessions(), [])
        self.assertIsNone(self.client.get('/api/evaluations/memory/latest').json()['summary'])
        for asset in ('desk.js', 'desk.css'):
            self.assertEqual(self.client.get('/assets/' + asset).status_code, 200)

    def test_acknowledgement_is_protected_persistent_and_only_for_visible_items(self):
        self.runtime.knowledge.retain_report('synthetic-1', 'session', {'created_at': '2026-01-01T00:00:00Z', 'report': draft()})
        self.assertEqual(self.client.get('/api/desk').json()['unseen_count'], 1)
        self.assertEqual(self.client.post('/api/desk/seen', json={'ids': ['report:synthetic-1']}).status_code, 403)
        self.client.post('/api/desk/seen', json={'ids': ['report:synthetic-1'], 'capture_baseline': True}, headers=self.headers).raise_for_status()
        packet = self.client.get('/api/desk').json()
        self.assertEqual(packet['unseen_count'], 0)
        self.assertIsNotNone(packet['baseline'])
        another = create_app(self.runtime)
        with TestClient(another, base_url='http://127.0.0.1:8765') as client:
            self.assertEqual(client.get('/api/desk').json()['unseen_count'], 0)
        self.assertEqual(self.client.post('/api/desk/seen', json={'ids': ['invented']}, headers=self.headers).status_code, 400)

    def test_report_comparison_preserves_old_version_and_evidence_changes(self):
        before = {'created_at': '2026-01-01T00:00:00Z', 'report': draft('Earlier')}
        after = {'created_at': '2026-01-02T00:00:00Z', 'report': draft('Revised', rid='page-'+'b'*24)}
        self.runtime.knowledge.retain_report('old', 'a', before)
        self.runtime.knowledge.retain_report('new', 'b', after)
        result = self.client.get('/api/reports/compare?older=old&newer=new').raise_for_status().json()
        self.assertEqual(result['added_claims'][0]['statement'], 'Revised')
        self.assertEqual(result['removed_evidence_ids'], ['page-'+'a'*24])
        self.assertFalse(result['judgment']['changed'])
        self.assertEqual(self.runtime.knowledge.report('old'), before)
        self.assertEqual(self.client.get('/api/reports/compare?older=new&newer=old').status_code, 400)

    def test_original_and_feed_at_same_url_are_one_capture_inbox_item(self):
        for prefix, char, kind in [('web-', 'a', 'feed_item'), ('page-', 'b', None)]:
            self.runtime.knowledge.register({'record_id': prefix+char*24, 'url': 'https://example.org/ethereum',
                'title': 'Ethereum synthetic announcement', 'text': 'Ethereum synthetic source.',
                'source_kind': kind, 'retrieved_at': '2026-01-01T00:00:00Z', 'source': {}})
        sources = [r for r in self.client.get('/api/desk').json()['inbox'] if r['kind'] == 'source']
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['evidence_ids'], ['page-'+'b'*24])

    def test_timeline_keeps_prior_versions_and_cutoff(self):
        store = self.runtime.knowledge
        rid = 'page-'+'c'*24
        store.register({'record_id': rid, 'url': 'https://example.org/event', 'title': 'Ethereum event',
                        'text': 'Ethereum synthetic plan.', 'retrieved_at': '2026-01-01T00:00:00Z'})
        event = {'asset': 'ETH', 'title': 'Synthetic event', 'summary': 'A plan.', 'status': 'planned',
                 'review_id': 'mocked-review', 'evidence_ids': [rid]}
        with patch('research_agent.knowledge.utcnow', return_value='2026-01-02T00:00:00Z'):
            first = store.record_event(event, store.get)
        with patch('research_agent.knowledge.utcnow', return_value='2026-01-03T00:00:00Z'):
            store.record_event({**event, 'event_id': first['event_id'], 'status': 'delayed'}, store.get)
        packet = self.client.get('/api/news/events/history').raise_for_status().json()
        self.assertEqual([r['version'] for r in packet['items']], [2, 1])
        self.assertEqual(len(store.event_history(cutoff='2026-01-02T12:00:00Z')), 1)

    def test_comparison_is_irregular_snapshot_change_not_pnl_and_rejects_stale_or_changed_series(self):
        old = {'status': 'ok', 'record': {'record_id': 'old', 'dataset_id': 'ETH',
               'observed_at': '2026-01-01T00:00:00Z', 'values': {'price_usd': '100'}}}
        new = {'status': 'ok', 'record': {**old['record'], 'record_id': 'new',
               'observed_at': '2026-01-01T00:10:00Z', 'values': {'price_usd': '110'}}}
        baseline = market_baseline({'items': {'eth_price': old}})
        result = market_changes(baseline, {'items': {'eth_price': new}})
        self.assertAlmostEqual(result[0]['change_fraction'], .1)
        self.assertIn('not a 24-hour', result[0]['scope'])
        self.assertEqual(market_changes(baseline, {'items': {'eth_price': {**new, 'status': 'unavailable'}}}), [])
        self.assertEqual(market_changes(baseline, {'items': {'eth_price': old}}), [])
        changed = {**new, 'record': {**new['record'], 'dataset_id': 'BTC'}}
        self.assertEqual(market_changes(baseline, {'items': {'eth_price': changed}}), [])


if __name__ == '__main__':unittest.main()
