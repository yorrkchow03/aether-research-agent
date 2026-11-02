import json
import math
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone

from research_agent.metrics import calculation,buy_cost,derive_dashboard


def candles(asset='ETH',growth=1.01):
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    return {'record_id':'live-'+('a' if asset=='ETH' else 'b')*24,'asset':asset,
            'observed_at':'2026-02-10T00:00:00Z','series':[
                {'observed_at':(start+timedelta(days=i)).isoformat(),'close_usd':str(100*growth**i),'volume_asset':'10'}
                for i in range(40)]}


class MetricsTests(unittest.TestCase):
    def test_returns_and_relative_comparison_use_identical_windows(self):
        eth=candles();btc=candles('BTC',1.005)
        daily=calculation([eth],'daily',7)
        relative=calculation([eth,btc],'relative',7)
        self.assertAlmostEqual(float(daily['values']['return_fraction']),1.01**7-1)
        self.assertAlmostEqual(float(relative['values']['relative_return_fraction']),(1.01/1.005)**7-1)
        self.assertEqual(float(daily['values']['max_drawdown_fraction']),0)
        self.assertEqual(daily['meta']['year_days'],365)
        btc['series']=btc['series'][:-1]
        with self.assertRaises(ValueError):calculation([eth,btc],'relative',7)

    def test_missing_day_and_volume_do_not_become_valid_zero(self):
        row=candles();row['series'].pop(-3)
        with self.assertRaises(ValueError):calculation([row],'daily',7)
        row=candles();row['series'][-1].pop('volume_asset')
        result=calculation([row],'daily',7)
        self.assertNotIn('period_volume_asset',result['values']);self.assertIn('volume_gap',result['meta'])
        data=derive_dashboard({},Path(tempfile.gettempdir()))
        self.assertTrue(all(v['status']=='unavailable' for v in data.values()))

    def test_orderbook_partial_fill_is_not_reported_as_full_execution(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'book.json';path.write_text(json.dumps({'bids':[['99','1',1]],'asks':[['100','1',1],['110','1',1]]}))
            book={'record_id':'live-'+'c'*24,'raw_path':str(path)}
            result=buy_cost(book,300)
            self.assertFalse(result['fully_covered']);self.assertEqual(float(result['covered_notional_usd']),210)
            self.assertEqual(float(result['uncovered_notional_usd']),90)
            self.assertEqual(float(result['estimated_vwap_usd']),105)
            path.write_text(json.dumps({'auction_mode':True,'bids':[['99','1',1]],'asks':[['100','1',1]]}))
            with self.assertRaises(ValueError):buy_cost(book,50)

    def test_funding_distribution_rejects_mixed_intervals(self):
        row={'series':[{'observed_at':f'2026-01-01T{h:02}:00:00Z','funding_rate_fraction':'0.001'} for h in (0,8,16)]}
        self.assertEqual(calculation([row],'funding')['meta']['interval_hours'],8)
        row['series'][-1]['observed_at']='2026-01-01T20:00:00Z'
        with self.assertRaises(ValueError):calculation([row],'funding')

    def test_hourly_window_and_irregular_oi_are_explicit(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        series=[{'observed_at':(start+timedelta(hours=i)).isoformat(),'period_end':(start+timedelta(hours=i+1)).isoformat(),'close_usd':str(100+i)} for i in range(25)]
        self.assertAlmostEqual(float(calculation([{'series':series}],'hourly_return',24)['values']['return_fraction']),.24)
        series.pop(10)
        with self.assertRaises(ValueError):calculation([{'series':series}],'hourly_return',24)
        prior={'dataset_id':'live.open_interest.eth','instrument':'ETH-USDT-SWAP','observed_at':'2026-01-01T00:00:00Z','values':{'open_interest_usd':'100'}}
        current={**prior,'observed_at':'2026-01-01T00:17:00Z','values':{'open_interest_usd':'120'}}
        result=calculation([prior,current],'oi_change');self.assertEqual(result['meta']['elapsed_seconds'],1020)
        self.assertIn('not a 24h',result['meta']['limits']);self.assertEqual(float(result['values']['oi_change_fraction']),.2)
        current['instrument']='BTC-USDT-SWAP'
        with self.assertRaises(ValueError):calculation([prior,current],'oi_change')
    def test_daily_close_window_uses_interval_end_without_backdating_signal(self):
        from research_agent.simulation import market_statistics,simulate
        row=candles()
        for r in row['series']:
            from datetime import datetime,timedelta
            r['period_end']=(datetime.fromisoformat(r['observed_at'])+timedelta(days=1)).isoformat()
            r['open_usd']=r['close_usd']
        stats=market_statistics(row['series'],7)
        self.assertEqual(stats['end'],row['series'][-1]['period_end'])
        result=simulate(row['series'],strategy='momentum',lookback=7)
        self.assertEqual(result['trades'][0]['signal_as_of'],result['trades'][0]['time'])


if __name__=='__main__':unittest.main()
