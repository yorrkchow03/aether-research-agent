import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_agent.knowledge import KnowledgeStore
from research_agent.research_leads import build_research_leads, research_leads_tool
from research_agent.user_research_memory import UserResearchMemory


def day(month, number=1):
    return f'2026-{month:02}-{number:02}T00:00:00Z'


class ResearchLeadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.knowledge = KnowledgeStore(root / 'knowledge')
        self.memory = UserResearchMemory(root / 'memory.sqlite')

    def save(self, rid, *, session='other', series=None, start=None, end=None, asset='ETH',
             created=None, acquired=None, review='reviewed', kind='full', watches=None):
        created = created or day(10, 2)
        draft = {'delivery_kind': kind, 'scope': {'assets': [asset], 'start': start or day(9),
                 'end': end or day(10)}, 'summary': 'Private test payload excluded from leads'}
        if watches is not None:
            draft['questions_to_watch'] = watches
        body = {'report': draft, 'review': {'status': review}, 'run_metadata': {'series_id': series},
                'created_at': created}
        with patch('research_agent.knowledge.utcnow', return_value=acquired or created):
            self.knowledge.retain_report(rid, session, body)

    def note(self, uid, eid, *, slot=None, asset='ETH', task='report', when=None, op='remember',
             expires=None, kind='focus'):
        self.memory.record(uid, 'prior', event_id=eid, observed_at=when or day(10),
                           slot=slot or eid.split(':')[0], topic='Pectra', kind=kind, operation=op,
                           text='Check the Pectra follow-up.', verbatim='Private conversation detail',
                           asset=asset, task=task, expires_at=expires)

    def snapshot(self, **overrides):
        args = dict(knowledge=self.knowledge, user_memory=self.memory, user_id='owner', session_id='here',
                    prompt='Prepare the ETH monthly report.', as_of=day(11, 4),
                    window_start=day(10), window_end=day(11))
        return build_research_leads(**{**args, **overrides})

    def test_related_series_then_session_then_workspace_with_assets_and_earlier_period(self):
        self.save('workspace', watches=['Workspace?'])
        self.save('session', session='here', start=day(8), end=day(9), watches=['Session?'])
        self.save('series', series='monthly', start=day(7), end=day(8), watches=['Series?'])
        self.save('same-window', session='here', start=day(10), end=day(11))
        self.save('different-asset', session='here', asset='BTC')
        self.save('unreviewed', session='here', review='rejected')
        self.save('notice', session='here', kind='evidence_gap')
        self.save('future', session='here', created=day(11, 6))
        self.save('same-end', session='here', start=day(9), end=day(11))
        for kwargs, expected in [
            ({'series_id': 'monthly'}, ('same_series', 'series')),
            ({'series_report_ids': ['notice', 'series']}, ('same_series', 'series')),
            ({}, ('same_session', 'session')),
            ({'session_id': 'new'}, ('workspace_asset', 'workspace')),
        ]:
            with self.subTest(kwargs=kwargs):
                actual = self.snapshot(**kwargs)
                self.assertEqual((actual['selection_basis'], actual['previous_report']['report_id']), expected)

    def test_most_recent_eligible_period_retains_only_its_own_authored_watch_ids(self):
        self.save('old', start=day(7), end=day(8), watches=['Old question?'])
        self.save('current', kind='minimal', watches=['Alpha?', 'alpha', None, 'Beta?', 'Gamma?', 'Fourth?'])
        actual = self.snapshot()
        self.assertEqual(actual['previous_report']['period']['end'], day(10))
        self.assertEqual(actual['previous_report']['delivery_kind'], 'minimal')
        self.assertEqual([(x['lead_id'], x['text']) for x in actual['questions_to_watch']],
                         [('report:current:watch:1', 'Alpha?'), ('report:current:watch:4', 'Beta?'),
                          ('report:current:watch:5', 'Gamma?')])
        self.assertNotIn('Private test payload', json.dumps(actual))

    def test_missing_and_empty_in_previous_report_never_borrow_from_older_period(self):
        self.save('older', start=day(7), end=day(8), watches=['Older text?'])
        self.save('missing', start=day(8), end=day(9))
        self.assertEqual(self.snapshot()['previous_report']['watch_status'], 'field_missing')
        self.save('empty', watches=[])
        actual = self.snapshot()
        self.assertEqual(actual['previous_report']['watch_status'], 'empty')
        self.assertEqual(actual['questions_to_watch'], [])

    def test_overlapping_earlier_windows_and_acquisition_cutoff(self):
        self.save('rolling', start=day(9, 29), end=day(10, 29), created=day(10, 30), acquired=day(11, 5))
        self.assertEqual(self.snapshot()['previous_report']['report_id'], 'rolling')
        self.assertIsNone(self.snapshot(mode='strict')['previous_report'])
        self.assertEqual(self.snapshot(mode='strict', as_of=day(11, 6))['previous_report']['report_id'], 'rolling')

    def test_legacy_reports_require_known_asset_and_period_but_delivery_kind_defaults_to_full(self):
        body = {'created_at': day(10, 3), 'review': {'status': 'reviewed'}, 'report': {
            'title': 'ETH monthly', 'questions_to_watch': ['Legacy question?']}}
        self.knowledge.retain_report('unknown-scope', 'here', body)
        self.assertIsNone(self.snapshot()['previous_report'])
        body['report']['scope'] = {'assets': ['ETH'], 'start': day(9), 'end': day(10)}
        self.knowledge.retain_report('known-scope', 'other', body)
        actual = self.snapshot()
        self.assertEqual(actual['previous_report']['delivery_kind'], 'full')
        self.assertEqual(actual['questions_to_watch'][0]['lead_id'], 'report:known-scope:watch:1')

    def test_broad_explicit_focus_obeys_owner_scope_revision_expiry_override_and_projection(self):
        self.note('elsewhere', 'other-user')
        self.note('owner', 'unrelated-asset', asset='BTC')
        self.note('owner', 'answer-only', task='answer')
        self.note('owner', 'expired', expires=day(10, 10))
        self.note('owner', 'past', slot='changed')
        self.note('owner', 'revoke', slot='changed', op='forget', when=day(10, 10))
        self.note('owner', 'overridden')
        self.note('owner', 'inferred:interest')
        self.note('owner', 'future', when=day(11, 5))
        self.note('owner', 'explicit', kind='follow_up', asset='ALL')
        actual = self.snapshot(overrides=['overridden'], broad_report=True)
        self.assertEqual([row['lead_id'] for row in actual['user_leads']], ['user:explicit'])
        self.assertNotIn('Private conversation detail', json.dumps(actual))
        self.assertEqual(self.snapshot(overrides=['overridden'])['user_leads'], [])
        topical = self.snapshot(prompt='Research Pectra.', overrides=['overridden'])
        self.assertEqual({row['lead_id'] for row in topical['user_leads']}, {'user:explicit', 'user:inferred:interest'})
        self.assertEqual(self.snapshot(broad_report=True, user_id=None)['user_leads'], [])

    def test_zero_argument_tool_keeps_original_snapshot_across_memory_changes(self):
        self.note('owner', 'explicit')
        snapshot = self.snapshot(broad_report=True)
        reader = research_leads_tool(snapshot)
        snapshot['user_leads'].clear()
        self.note('owner', 'remove', slot='explicit', when=day(10, 10), op='forget')
        self.assertEqual(reader.name, 'get_research_leads')
        self.assertEqual([row['lead_id'] for row in json.loads(reader.invoke({}))['user_leads']], ['user:explicit'])
        self.assertEqual(self.snapshot(broad_report=True)['user_leads'], [])


if __name__ == '__main__':
    unittest.main()
