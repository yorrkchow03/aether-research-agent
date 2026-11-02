import json,tempfile,unittest
from pathlib import Path
from research_agent.investment.history import normalize_history

class StrategyHistoryTests(unittest.TestCase):
    def test_original_paged_overlap_gap_and_future_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);start=1735689600
            rows=[[start+i*86400,1,3,2,2,10] for i in range(4)]
            a=root/'a';b=root/'b';a.write_text(json.dumps(rows[:3]));b.write_text(json.dumps(rows[2:]))
            records=[{'raw_path':str(p)} for p in (a,b)]
            good=normalize_history(records,'ETH','2025-01-01T00:00:00Z','2025-01-04T00:00:00Z')
            self.assertEqual(len(good['series']),3)
            bad=rows[2:];bad[0][4]=2.5;b.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError,'disagree'):normalize_history(records,'ETH','2025-01-01T00:00:00Z','2025-01-05T00:00:00Z')
            a.write_text(json.dumps([rows[0],rows[2],rows[3]]))
            with self.assertRaisesRegex(ValueError,'missing'):normalize_history(records[:1],'ETH','2025-01-01T00:00:00Z','2025-01-05T00:00:00Z')
    def test_incomplete_boundaries_and_impossible_prices_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'raw';start=1735689600;rows=[[start+i*86400,1,3,2,2,10] for i in range(4)]
            p.write_text(json.dumps(rows[1:]))
            with self.assertRaisesRegex(ValueError,'boundary'):normalize_history([{'raw_path':str(p)}],'ETH','2025-01-01T00:00:00Z','2025-01-05T00:00:00Z')
            rows[0][4]=4;p.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError,'range'):normalize_history([{'raw_path':str(p)}],'ETH','2025-01-01T00:00:00Z','2025-01-05T00:00:00Z')
