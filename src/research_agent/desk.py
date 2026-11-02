"""Saved-data desk views. No model calls, quote refresh or investment actions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from research_data.http import utcnow, dumps
from research_data.normalize import timestamp
from .audit import report_evidence_ids
from .knowledge import canonical_url


def report_changes(older, newer):
    """Literal version comparison; changed wording is not a new market fact."""
    before, after = older['report'], newer['report']
    def claims(draft):
        return {dumps(row, sort_keys=True): row for row in
                [*draft.get('observations', []), *draft.get('key_changes', []), *draft.get('interpretations', [])]}
    a, b = claims(before), claims(after)
    old_ids, new_ids = report_evidence_ids(before), report_evidence_ids(after)
    return {'older_title': before['title'], 'newer_title': after['title'],
            'older_created_at': older.get('created_at'), 'newer_created_at': newer.get('created_at'),
            'judgment': {'before': before['judgment'], 'after': after['judgment'],
                         'changed': before['judgment'] != after['judgment']},
            'summary_changed': before['summary'] != after['summary'],
            'advice_changed': before.get('advice') != after.get('advice'),
            'added_claims': [row for key, row in b.items() if key not in a],
            'removed_claims': [row for key, row in a.items() if key not in b],
            'added_evidence_ids': sorted(new_ids - old_ids),
            'removed_evidence_ids': sorted(old_ids - new_ids),
            'scope': 'Literal saved-report comparison. Wording or source changes do not establish materiality, causation or improved advice.'}


def market_baseline(dashboard):
    items = {}
    for key in ('eth_price', 'btc_price'):
        packet = dashboard.get('items', {}).get(key, {})
        row = packet.get('record')
        if row and packet.get('status') == 'ok':
            items[key] = {name: row.get(name) for name in
                          ('record_id', 'dataset_id', 'instrument', 'provider', 'quote_currency', 'observed_at', 'values')}
    return {'saved_at': utcnow(), 'items': items}


def market_changes(baseline, dashboard):
    changes = []
    for key, prior in (baseline or {}).get('items', {}).items():
        packet = dashboard.get('items', {}).get(key, {})
        current = packet.get('record') or {}
        if packet.get('status') != 'ok':continue
        if any(current.get(field) != prior.get(field) for field in ('dataset_id', 'instrument', 'provider', 'quote_currency')):continue
        try:
            earlier, later = timestamp(prior['observed_at']), timestamp(current['observed_at'])
            a, b = float(prior['values']['price_usd']), float(current['values']['price_usd'])
            from math import isfinite
            if later <= earlier or not isfinite(a) or not isfinite(b) or a <= 0 or b <= 0:continue
        except (ValueError, TypeError, KeyError):continue
        changes.append({'asset': 'ETH' if key == 'eth_price' else 'BTC', 'change_fraction': b / a - 1,
                        'from_time': prior['observed_at'], 'to_time': current['observed_at'],
                        'previous_record_id': prior['record_id'], 'record_id': current['record_id'],
                        'scope': 'Change between two saved snapshots; irregular interval, not a 24-hour return or investment PnL.'})
    return changes


class ResearchDesk:
    def __init__(self, state_dir, knowledge, scheduler, dashboard):
        self.path = Path(state_dir) / 'research_desk.sqlite'
        self.knowledge, self.scheduler, self.dashboard = knowledge, scheduler, dashboard
        with self.db() as c:
            c.executescript('CREATE TABLE IF NOT EXISTS seen(item_id TEXT PRIMARY KEY,seen_at TEXT NOT NULL);'
                            'CREATE TABLE IF NOT EXISTS context(key TEXT PRIMARY KEY,body TEXT NOT NULL);')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=15)
        try:
            with c:yield c
        finally:c.close()

    def packet(self):
        with self.db() as c:
            seen = {row[0] for row in c.execute('SELECT item_id FROM seen')}
            saved = c.execute("SELECT body FROM context WHERE key='baseline'").fetchone()
        baseline = json.loads(saved[0]) if saved else None
        reports = self.knowledge.search_reports('', 30)
        events = self.knowledge.events('ETH')
        runs = self.scheduler.runs()
        subscriptions = self.scheduler.list()
        inbox = []
        for e in events:
            inbox.append({'id': 'event:' + e['event_id'] + ':' + str(e['version']), 'kind': 'event',
                'title': e['title'], 'detail': e['summary'], 'time': e['available_at'],
                'event_id': e['event_id'], 'evidence_ids': e['evidence_ids'], 'status': e['status'],
                'why': 'A source-reviewed event classification is available; inspect its status and version.'})
        for r in reports:
            inbox.append({'id': 'report:' + r['report_id'], 'kind': 'report',
                'title': r['title'], 'detail': r['summary'], 'time': r['created_at'],
                'report_id': r['report_id'], 'session_id': r['session_id'], 'status': r['review_status'],
                'why': 'A saved research judgment is available to compare with its sources and prior reports.'})
        for r in runs:
            if r['state'] not in ('completed', 'failed', 'interrupted', 'cancelled'):continue
            config = next((s for s in subscriptions if s['id'] == r['subscription_id']), {})
            inbox.append({'id': 'run:' + r['id'] + ':' + r['state'], 'kind': 'scheduled_run',
                'title': config.get('name', 'Archived schedule'),
                'detail': 'Scheduled research ' + r['state'] + '. Saved work and diagnostics are retained.',
                'diagnostic': r.get('error'),
                'time': r.get('finished_at') or r['created_at'], 'session_id': r.get('session_id'),
                'status': r['state'], 'why': 'Check the result or saved failure before relying on an automatic brief.'})
        # A repeated feed/page capture at one URL is not an independent catalyst.
        latest = {}
        for row in self.knowledge.search_news('', limit=30, material_only=True):
            url = canonical_url(row['url'])
            if url not in latest or (row['source_kind'] == 'page' and latest[url]['source_kind'] != 'page'):
                latest[url] = row
        for row in latest.values():
            inbox.append({'id': 'source:' + row['version_id'], 'kind': 'source',
                'title': row['title'], 'detail': row['excerpt'][:240], 'time': row['first_seen_at'],
                'evidence_ids': [row['record_id']], 'status': 'captured',
                'why': 'Newly saved publisher material; capture time does not establish a new event or confirm the publisher.'})
        for row in inbox:row['seen'] = row['id'] in seen
        inbox.sort(key=lambda row: timestamp(row['time']), reverse=True)
        board = self.dashboard.snapshot()
        latest_report = None
        if reports:
            latest_report = {**reports[0], 'report': self.knowledge.report(reports[0]['report_id'])['report']}
        return {'generated_at': utcnow(), 'baseline': baseline,
                'market_changes': market_changes(baseline, board), 'inbox': inbox[:100],
                'unseen_count': sum(not row['seen'] for row in inbox[:100]),
                'latest_report': latest_report, 'events': events[:5],
                'scheduler': {'enabled': sum(s['enabled'] for s in subscriptions),
                              'schedules': len(subscriptions), 'items': subscriptions,
                              'local_service_required': True},
                'scope': 'Saved workspace activity. This view performs no live requests or model calls.'}

    def acknowledge(self, ids, capture_baseline=False):
        packet = self.packet()
        allowed = {row['id'] for row in packet['inbox']}
        if any(item not in allowed for item in ids):
            raise ValueError('Only visible saved activity can be acknowledged')
        with self.db() as c:
            c.executemany('INSERT OR REPLACE INTO seen VALUES(?,?)', [(item, utcnow()) for item in ids])
            if capture_baseline:
                c.execute("INSERT OR REPLACE INTO context VALUES('baseline',?)", (dumps(market_baseline(self.dashboard.snapshot())),))
        return {'status': 'saved', 'acknowledged': len(ids), 'baseline_saved': capture_baseline}
