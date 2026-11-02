import math,unittest
from datetime import datetime,timedelta,timezone
from research_agent.investment.strategies import samples,features,training_before,run_experiment,StrategyConfig

def history(n=330):
    start=datetime(2024,1,1,tzinfo=timezone.utc);rows=[];price=100
    for i in range(n):
        op=price*(1+.003*math.sin(i*.83));price=op*(1+.015*math.sin(i*.43)+.005*math.cos(i*.17))
        rows.append({'observed_at':(start+timedelta(days=i)).isoformat(),'period_end':(start+timedelta(days=i+1)).isoformat(),'open_usd':str(op),'close_usd':str(price),'high_usd':str(max(op,price)*1.01),'low_usd':str(min(op,price)*.99),'volume_asset':str(100+i%23)})
    return {'record_id':'live-'+'a'*24,'series':rows,'scope':'Synthetic unit-test OHLCV only'}

class StrategyTests(unittest.TestCase):
    def test_feature_prefix_is_unchanged_by_future_prices(self):
        original=history()['series'];before=features(original,60)
        changed=[dict(r) for r in original]
        for row in changed[61:]:row['close_usd']='999999';row['volume_asset']='999999'
        self.assertEqual(before,features(changed,60))
    def test_labels_are_purged_until_available_and_execution_is_delayed(self):
        data=history()['series'];rows=samples(data,1);decision=rows[100]
        self.assertGreaterEqual(datetime.fromisoformat(decision['execute_at'])-datetime.fromisoformat(decision['decision_at']),timedelta(days=1))
        train=training_before(rows,decision['decision_at'])
        self.assertTrue(all(r['label_available_at']<decision['decision_at'] for r in train))
        self.assertNotIn(rows[99],train)
    def test_real_training_holdout_and_baselines_use_same_calendar_and_costs(self):
        run=run_experiment(history(),{'history_days':330,'refit_every':30})
        self.assertGreater(run['holdout_scores']['samples'],40)
        for fold in run['folds']:self.assertLess(fold['last_training_label_available_at'],fold['first_decision_at'])
        self.assertLess(run['split']['validation_end'],run['split']['holdout_start'])
        results=run['comparison']['results'];self.assertEqual(len({(r['start'],r['end']) for r in results}),1)
        self.assertTrue(all(r['profile']['fee_bps']==10 and r['terminal_liquidation'] for r in results))
        for r in results:
            self.assertTrue(all(t['signal_as_of']<=t['time'] for t in r['trades'] if 'signal_as_of' in t))
        self.assertIn('model',run['latest']);self.assertNotIn('pickle',run['latest']['model'])
    def test_holdout_changes_do_not_change_candidate_validation_or_early_predictions(self):
        a=history();first=run_experiment(a,{'history_days':330,'refit_every':30})
        modified=history();start=next(i for i,r in enumerate(modified['series']) if r['period_end']==first['decisions'][25]['decision_at'])+1
        for i,row in enumerate(modified['series'][start:]):
            factor=1.1+.01*i
            for key in ('open_usd','high_usd','low_usd','close_usd'):row[key]=str(float(row[key])*factor)
        second=run_experiment(modified,{'history_days':330,'refit_every':30})
        self.assertEqual(first['candidate_validation'],second['candidate_validation'])
        self.assertEqual(first['model_selected'],second['model_selected'])
        self.assertEqual(first['decisions'][0]['probability_up'],second['decisions'][0]['probability_up'])
    def test_short_or_gapped_history_does_not_fabricate_a_model(self):
        with self.assertRaises(ValueError):run_experiment(history(90),{})
        data=history();data['series'].pop(40)
        with self.assertRaises(ValueError):run_experiment(data,{})
        with self.assertRaises(ValueError):StrategyConfig(long_probability=float('nan'))
