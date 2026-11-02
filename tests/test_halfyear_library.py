import json
from pathlib import Path
import tempfile
import unittest

from research_data.library.registry import install
from research_data.library.store import ResearchLibrary, WINDOW_START
from research_data.library.repair import rate_calendar


class HalfYearLibraryTests(unittest.TestCase):
    def test_month_and_halfyear_coexist_without_global_window_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            old=ResearchLibrary(Path(folder)/'old');new=ResearchLibrary(Path(folder)/'new',window_start='2026-04-26T00:00:00Z',window_end='2026-10-26T00:00:00Z')
            for lib in (old,new):
                install(lib)
                cid=lib.capture('coinbase','https://example.com/fixture',b'[]')
                lib.put('market.coinbase.eth.hourly','2026-05-01T00:00:00Z',{'close_usd':'10'},cid,period_end='2026-05-01T01:00:00Z')
                lib.put_document('ethereum','https://example.com/a','Historical fixture','Synthetic source body',cid,published_at='2026-05-01T00:00:00Z')
            self.assertEqual(old.series('market.coinbase.eth.hourly')['total'],0)
            self.assertEqual(new.series('market.coinbase.eth.hourly')['total'],1)
            self.assertEqual(old.search_documents()['total'],0)
            self.assertEqual(new.search_documents()['total'],1)
            self.assertEqual(new.hours,4392)
            self.assertEqual(new.days,183)
            self.assertEqual(old.window_start,WINDOW_START)
            reopened=ResearchLibrary(new.root,create=False)
            self.assertEqual(reopened.window_start,new.window_start)
            coverage={r['dataset_id']:r for r in new.coverage()['datasets']}
            self.assertEqual(coverage['market.coinbase.eth.hourly']['expected'],4392)
            self.assertEqual(coverage['analysis.eth.daily']['expected'],183)
            with self.assertRaisesRegex(ValueError,'cannot be silently changed'):
                ResearchLibrary(new.root,window_start=WINDOW_START)

    def test_halfyear_calendar_excludes_fed_holidays_but_not_unknown_days(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=ResearchLibrary(folder,window_start='2026-04-26T00:00:00Z',window_end='2026-10-26T00:00:00Z')
            days=rate_calendar(lib.start,lib.end)
            for holiday in ['2026-05-25','2026-06-19','2026-07-03','2026-09-07','2026-10-12']:
                self.assertNotIn(holiday,days)
            self.assertIn('2026-10-13',days)

    def test_creation_requires_closed_day_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'UTC-day'):
                ResearchLibrary(folder,window_start='2026-04-26T01:00:00Z')

    def test_later_publication_version_cannot_remove_eligible_earlier_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=ResearchLibrary(folder,window_start='2026-04-26T00:00:00Z',window_end='2026-10-26T00:00:00Z')
            old=lib.capture('ethereum','https://example.com/post',b'old',retrieved_at='2026-05-03T00:00:00Z')
            first=lib.put_document('ethereum','https://example.com/post','Old event','Original May event.',old,published_at='2026-05-01T00:00:00Z')
            new=lib.capture('ethereum','https://example.com/post',b'new',retrieved_at='2026-07-03T00:00:00Z')
            lib.put_document('ethereum','https://example.com/post','Later event','Replacement July event.',new,published_at='2026-07-01T00:00:00Z')
            for mode,known in [('observed',None),('strict','2026-06-01T00:00:00Z')]:
                result=lib.search_documents(end='2026-06-01T00:00:00Z',mode=mode,known_by=known)
                self.assertEqual([r['record_id'] for r in result['results']],[first])


if __name__=='__main__':unittest.main()
