import unittest
from datetime import datetime,timedelta,timezone
from research_agent.simulation import simulate,compare_strategies,loss_scenarios,market_statistics

def rows(prices):
 return [{'observed_at':(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=i)).isoformat(),'open_usd':str(p),'close_usd':str(p)} for i,p in enumerate(prices)]

class SimulationTests(unittest.TestCase):
 def test_constant_market_charges_both_entry_and_exit_and_caps_budget(self):
  r=simulate(rows([100]*20),{'budget_usd':1000,'fee_bps':10,'slippage_bps':10},lookback=1)
  self.assertLess(r['final_equity_usd'],1000);self.assertEqual(len(r['trades']),2)
  self.assertGreater(r['fees_usd'],0);self.assertGreater(r['slippage_usd'],0)
  self.assertGreaterEqual(min(p['cash_usd'] for p in r['curve']),-.000001)
 def test_zero_cost_buy_hold_matches_same_window_price_change(self):
  r=simulate(rows([100,100,110,121,133.1]),{'budget_usd':1000,'fee_bps':0,'slippage_bps':0},lookback=1)
  self.assertAlmostEqual(r['net_return_fraction'],.21);self.assertAlmostEqual(r['max_drawdown_fraction'],0)
 def test_future_prices_cannot_change_prior_signals(self):
  a=simulate(rows([100,100,110,120,130,140,150,160]),{},'momentum',lookback=1)
  b=simulate(rows([100,100,110,120,130,1,10000,20]),{},'momentum',lookback=1)
  self.assertEqual(a['trades'][:1],b['trades'][:1])
  for t in a['trades']:
   if t['side']!='terminal_sell':self.assertLess(t['signal_as_of'],t['time'])
 def test_buy_hold_partial_allocation_does_not_daily_rebalance(self):
  r=simulate(rows([100,100,110,121,133,146,160]),{'allocation_fraction':.5,'fee_bps':0,'slippage_bps':0},lookback=1)
  self.assertEqual(len(r['trades']),2)
 def test_dca_only_buys_at_fixed_intervals(self):
  r=simulate(rows([100+i for i in range(32)]),{},'dca',lookback=1)
  buys=[t for t in r['trades'] if t['side']=='buy'];self.assertEqual(len(buys),5)
  self.assertTrue(all(t['side'] in ('buy','terminal_sell') for t in r['trades']))
 def test_missing_daily_rows_and_nonfinite_prices_rejected(self):
  x=rows([100]*10);x.pop(4)
  with self.assertRaises(ValueError):simulate(x)
  x=rows([100,100,float('nan'),110])
  with self.assertRaises(ValueError):simulate(x)
 def test_n_periods_require_n_plus_one_prices(self):
  s=market_statistics(rows([100,110,121]),2)
  self.assertAlmostEqual(s['return_fraction'],.21);self.assertEqual(s['price_points'],3)
  with self.assertRaises(ValueError):market_statistics(rows([100,110,121]),3)
 def test_loss_limits_are_user_scenarios_not_probability_claims(self):
  r=loss_scenarios({'budget_usd':1000,'allocation_fraction':.5,'max_loss_usd':100})
  self.assertEqual(r['scenarios'][1]['loss_usd'],100);self.assertFalse(r['scenarios'][1]['exceeds_user_max_loss'])
  self.assertTrue(r['scenarios'][2]['exceeds_user_max_loss'])

if __name__=='__main__':unittest.main()
