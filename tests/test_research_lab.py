import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from research_data.normalize import timestamp, iso
from research_agent.research_lab.corpus import prepare_corpus, load_corpus, validate_protocol
from research_agent.research_lab.signals import signal_catalog
from research_agent.research_lab.evaluation import variants, window_rows, fit_case, paired_interval, probability_weights
from research_agent.rpc import classify_error
from test_strategy_models import history

ROOT=Path(__file__).resolve().parents[1]

def protocol():
    p=json.loads((ROOT/'examples/evaluation/signal_protocol_v1.json').read_text())
    p.update(assets=['ETH'],start='2024-01-01T00:00:00Z',end='2025-01-01T00:00:00Z')
    p['windows']=[{'id':s,'split':s,'start':f'2024-{m:02d}-01T00:00:00Z','end':f'2024-{m+1:02d}-01T00:00:00Z'} for s,m in [('development',7),('validation',8),('confirmation',9)]]
    p['evaluation']['bootstrap_draws']=20
    return p

class FakeLive:
    def __init__(self,root):self.root=Path(root);self.calls=0
    def request(self,provider,path,params,ttl):
        import hashlib
        self.calls+=1;start=timestamp(params['start']);end=timestamp(params['end'])
        bars=[]
        for i in range((end-start).days+1):
            t=start+timedelta(days=i);price=100+((t-timestamp('2024-01-01')).days%15)
            bars.append([int(t.timestamp()),price-1,price+2,price,price+1,100])
        body=json.dumps(bars).encode();name=self.root/f'{self.calls}.json';name.write_bytes(body)
        return {'record':{'record_id':'live-'+str(self.calls).zfill(24),'raw_path':str(name),'retrieved_at':'2026-10-16T12:00:00Z','source':{'sha256':hashlib.sha256(body).hexdigest(),'url':'https://api.exchange.coinbase.com'+path}}}

class CorpusTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.live=FakeLive(self.root)
    def capture(self):return prepare_corpus(self.root/'corpus',protocol(),self.live,sleep=lambda _:None)
    def test_capture_pages_preserves_raw_and_replays_without_network(self):
        r=self.capture();self.assertEqual(r['datasets'][0]['candles'],366)
        with patch.object(self.live,'request',side_effect=AssertionError('Network forbidden')):
            c=load_corpus(self.root/'corpus',expected_manifest_sha256=r['manifest_sha256'])
        self.assertEqual(len(c['histories']['ETH']['series']),366)
        self.assertEqual(self.live.calls,2)
    def test_frozen_capture_never_overwrites_or_refreshes(self):
        self.capture()
        with self.assertRaisesRegex(ValueError,'Frozen corpus'):self.capture()
        self.assertEqual(self.live.calls,2)
    def test_changed_original_and_external_identity_are_rejected(self):
        self.capture()
        with self.assertRaisesRegex(ValueError,'identity'):load_corpus(self.root/'corpus',expected_manifest_sha256='0'*64)
        next((self.root/'corpus/raw').glob('*')).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'bytes changed'):load_corpus(self.root/'corpus')
    def test_resume_uses_only_verified_completed_pages(self):
        real=self.live.request
        def fail(*a,**k):
            if self.live.calls>=1:raise ValueError('Synthetic unavailable page')
            return real(*a,**k)
        with patch.object(self.live,'request',side_effect=fail):
            with self.assertRaises(ValueError):self.capture()
        self.capture();self.assertEqual(self.live.calls,2)
        self.assertTrue((self.root/'corpus/capture_failures.jsonl').exists())
    def test_protocol_rejects_overlap_split_reorder_and_path_escape(self):
        for mutate in [lambda p:p['windows'][1].update(start=p['windows'][0]['start']),lambda p:p['windows'][0].update(split='confirmation'),lambda p:p['windows'][0].update(id='../outside'),lambda p:p['evaluation'].update(logistic_C=float('nan'))]:
            p=protocol();mutate(p)
            with self.assertRaises(ValueError):validate_protocol(p)
    def test_manifests_cannot_reference_unfrozen_or_external_source(self):
        from research_agent.research_lab.corpus import digest
        self.capture();root=self.root/'corpus';m=json.loads((root/'manifest.json').read_text());m['files']['../outside']='x'
        (root/'manifest.json').write_text(json.dumps(m));f=json.loads((root/'freeze.json').read_text());f['manifest_sha256']=digest(root/'manifest.json');(root/'freeze.json').write_text(json.dumps(f))
        with self.assertRaisesRegex(ValueError,'escaped'):load_corpus(root)

class SignalTests(unittest.TestCase):
    def test_every_feature_has_rationale_and_falsification(self):
        catalog=signal_catalog()['signals'];self.assertEqual(len(catalog),10)
        for s in catalog:self.assertTrue(all(s[k] for k in ('formula','rationale','failure_conditions','available_at','evaluation')))
        catalog[0]['name']='changed';self.assertNotEqual(signal_catalog()['signals'][0]['name'],'changed')
    def test_all_variants_share_strict_purging_and_target_dates(self):
        h=history(366);h['asset']='ETH';p=protocol();r=fit_case(h,p,p['windows'][0])
        self.assertEqual(len(r['variants']),23);self.assertEqual(len(variants()),23)
        self.assertLess(timestamp(r['last_training_label_available_at']),timestamp(r['first_decision_at']))
        self.assertEqual({v['scores']['samples'] for v in r['variants'].values()},{r['common_target_rows']})
        dates={(b['start'],b['end']) for v in r['variants'].values() for b in v['paper']}
        dates.update((b['start'],b['end']) for values in r['baselines'].values() for b in values);self.assertEqual(len(dates),1)
        self.assertTrue(all(x['net_return_fraction']==0 for x in r['baselines']['cash']))
    def test_future_changes_do_not_affect_earlier_case_predictions(self):
        h=history(366);h['asset']='ETH';p=protocol();before=fit_case(h,p,p['windows'][0]);modified=copy.deepcopy(h)
        for row in modified['series'][250:]:
            for k in ('close_usd','open_usd','high_usd','low_usd'):row[k]=str(float(row[k])*2)
        after=fit_case(modified,p,p['windows'][0]);self.assertEqual(before['probabilities'],after['probabilities'])
    def test_short_and_single_class_training_are_explicit_failures(self):
        p=protocol();h=history(366);h['asset']='ETH'
        p['windows'][0]['start']='2024-02-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'training'):window_rows(h,p,p['windows'][0])
    def test_delayed_execution_and_abstention_do_not_read_future_features(self):
        h=history(366);p=protocol();_,test=window_rows(h,p,p['windows'][0])
        weights,audit=probability_weights(test,[.8,.5,.2]+[.5]*(len(test)-3),h['series'],p['evaluation'])
        self.assertEqual([a['weight'] for a in audit[:3]],[.5,.5,0.])
        self.assertTrue(all(timestamp(a['execute_at'])-timestamp(a['decision_at'])==timedelta(days=1) for a in audit))
        self.assertEqual(len(weights),len(test))
    def test_time_block_uncertainty_is_paired_repeatable_and_refuses_nonfinite(self):
        c=protocol()['evaluation'];a=paired_interval(np.arange(50)/100,c)
        self.assertEqual(a,paired_interval(np.arange(50)/100,c));self.assertEqual(paired_interval([0]*50,c)['ci95'],[0.,0.])
        self.assertIsNone(paired_interval([1,2],c)['ci95'])
        with self.assertRaises(ValueError):paired_interval([float('nan')],c)
    def test_actual_provider_archive_authorization_message_is_a_history_gap(self):
        self.assertEqual(classify_error('Archive requests require a personal token.').kind,'history_unavailable')
