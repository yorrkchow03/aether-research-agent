"""Regression checks for private memory transitions and product turn scope."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_agent.user_research_memory import (
    InterestSignal, MemoryIntent, UserResearchMemory, apply_intent, parse_intent, requested_task,
)


class UserResearchMemoryTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'owner/events.sqlite'
        self.store=UserResearchMemory(self.path)
        self.first='2026-11-01T00:00:00Z'
        self.second='2026-11-02T00:00:00Z'

    def test_explicit_correction_supersedes_by_slot_and_current_skip_is_local(self):
        first='For future Ethereum Nimbus reports, always lead with the release date.'
        self.store.record('owner','s1',event_id='first',observed_at=self.first,slot='nimbus-order',
            topic='Nimbus',kind='preference',operation='remember',text=first,verbatim=first,asset='ETH',task='report')
        same='This time, do not follow the saved Nimbus preferences.'
        slots=self.store.active_slots('owner',self.second)
        overrides,written=apply_intent(self.store,'owner','s2','ask',same,self.second,MemoryIntent(),slots)
        self.assertEqual(overrides,('nimbus-order',))
        self.assertEqual(written,[])
        self.assertEqual(self.store.recall('owner',prompt='Nimbus report',as_of=self.second,task='report',
                                         overrides=overrides),[])
        revised='Replace the previous Nimbus report preference. From now on lead with release impact.'
        intent,_=parse_intent(json.dumps({'updates':[{'slot':'new-slot','topic':'Nimbus','target_slot':'nimbus-order',
            'kind':'preference','operation':'remember','asset':'ETH','task':'report',
            'quote':'From now on lead with release impact.'}]}))
        apply_intent(self.store,'owner','s2','change',revised,self.second,intent,slots)
        restarted=UserResearchMemory(self.path)
        before=restarted.recall('owner',prompt='Nimbus report',as_of=self.first,task='report')
        after=restarted.recall('owner',prompt='Nimbus report',as_of=self.second,task='report')
        self.assertEqual([r['event_id'] for r in before],['first'])
        self.assertEqual([r['event_id'] for r in after],['change:0'])
        self.assertFalse(restarted.recall('someone-else',prompt='Nimbus report',as_of=self.second,task='report'))

    def test_cross_session_repeat_preserves_scope_and_expires(self):
        first=InterestSignal(topic='Bitcoin custody withdrawals vs deposits',quote='Bitcoin custody withdrawals',
                             asset='BTC',task='answer')
        second=InterestSignal(topic='Bitcoin custody withdrawals|提现机制',quote='Bitcoin custody withdrawals',
                              asset='BTC',task='answer')
        self.assertIsNone(self.store.observe_interest('owner','a','one',self.first,first,first.quote))
        self.assertIsNone(self.store.observe_interest('owner','a','two',self.second,second,second.quote))
        self.assertEqual(self.store.observe_interest('owner','b','three',self.second,second,second.quote),
                         'three:interest')
        query={'prompt':'Bitcoin custody withdrawals','asset':'BTC','task':'answer'}
        self.assertEqual(len(self.store.recall('owner',as_of=self.second,**query)),1)
        self.assertFalse(self.store.recall('owner',as_of='2026-11-05T00:00:00Z',**query))
        self.assertFalse(self.store.recall('owner',as_of=self.second,**{**query,'task':'report'}))

    def test_ambiguous_reply_cannot_restore_a_suspended_preference(self):
        first='Always lead with Nimbus compatibility.'
        self.store.record('owner','a',event_id='first',observed_at=self.first,slot='nimbus-order',
            topic='Nimbus',kind='preference',operation='remember',text=first,verbatim=first)
        prompt='Forget my old Nimbus research preference.'
        _,saved=apply_intent(self.store,'owner','b','rev',prompt,self.second,MemoryIntent(),
                             self.store.active_slots('owner',self.second))
        self.assertEqual(saved,['rev:pending'])
        self.assertFalse(self.store.recall('owner',prompt='Nimbus',as_of='2026-11-03T00:00:00Z'))
        self.assertEqual(self.store.active_slots('owner',self.second)[0]['operation'],'suspend')

    def test_scope_uses_request_not_mention_and_parser_accepts_separate_optional_error(self):
        self.assertEqual(requested_task('Use the saved report to answer. Not a report.','answer'),'answer')
        self.assertEqual(requested_task('生成一份本周 ETH 报告','answer'),'report')
        intent,errors=parse_intent(json.dumps({'updates':[{'slot':'未规范的标签','topic':['Nimbus','客户端'],
            'kind':'focus','operation':'remember','quote':'到 11 月 4 日之前临时多看客户端',
            'expires_at':'2026-11-04T00:00:00Z'}],'interest':{'topic':'wrong object'}}))
        self.assertTrue(errors)
        prompt=intent.updates[0].quote
        _,saved=apply_intent(self.store,'owner','a','new',prompt,self.first,intent,[])
        self.assertEqual(saved,['new:0'])


if __name__=='__main__':
    unittest.main()
