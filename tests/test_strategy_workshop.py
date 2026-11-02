import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from research_agent.research_lab.strategy_code import validate_source, execute_source
from research_agent.research_lab.workshop import author_context, evaluate_code, run_workshop
from test_research_lab import protocol
from test_strategy_models import history

CODE='''def strategy(bars, config):
    if len(bars) < 22:
        return 0.0
    previous = float(bars[-8]["close_usd"])
    latest = float(bars[-1]["close_usd"])
    if latest > previous:
        return config["allocation_cap"]
    return 0.0
'''

def corpus():
    h=history(366);h['asset']='ETH'
    return {'protocol':protocol(),'histories':{'ETH':h},'manifest_sha256':'a'*64}

class CodeTests(unittest.TestCase):
    def test_actual_code_gets_only_prefix_and_handles_short_history(self):
        c=corpus();bars=c['histories']['ETH']['series'];config=c['protocol']['evaluation']
        first=execute_source(CODE,[{'series':bars,'indices':[10,50,80]}],config)
        changed=copy.deepcopy(bars)
        for b in changed[81:]:b['close_usd']='999999'
        second=execute_source(CODE,[{'series':changed,'indices':[10,50,80]}],config)
        self.assertEqual(first['weights'],second['weights']);self.assertEqual(first['weights'][0][0],0.)
    def test_import_network_io_introspection_recursion_mutation_and_unbounded_syntax_fail(self):
        bodies=['import os\n    return 0','return open("x")','return bars.__class__','return strategy(bars, config)',
                'bars[0] = 2\n    return 0','while True:\n        pass','return [b for b in bars]',
                'return 2 ** len(bars)','def other():\n        return 0\n    return other()','sum = 1\n    return 0']
        for body in bodies:
            with self.subTest(body=body),self.assertRaises((ValueError,SyntaxError)):validate_source('def strategy(bars, config):\n    '+body+'\n')
    def test_nonfinite_and_out_of_cap_outputs_are_not_clamped_or_zero_filled(self):
        for expr in ['float("nan")','float("inf")','-0.1','0.6','True','"0.2"','bars[99]["close_usd"]']:
            with self.subTest(expr=expr),self.assertRaises(ValueError):execute_source('def strategy(bars, config):\n    return '+expr,[{'series':[{}],'indices':[0]}],{'allocation_cap':.5})
    def test_numeric_range_and_wall_clock_bounds(self):
        with self.assertRaisesRegex(ValueError,'504'):execute_source('def strategy(bars, config):\n    for i in range(1000):\n        pass\n    return 0',[{'series':[{}],'indices':[0]}],{'allocation_cap':.5})
        from subprocess import TimeoutExpired
        with patch('subprocess.run',side_effect=TimeoutExpired('test',.01)),self.assertRaisesRegex(ValueError,'wall-clock'):execute_source(CODE,[{'series':[{}],'indices':[0]}],{'allocation_cap':.5})
    def test_declared_mean_diagnostic_detects_semantic_error_despite_valid_syntax(self):
        import importlib.util
        path=Path(__file__).resolve().parents[1]/'scripts/evaluation/review_generated_strategy.py'
        spec=importlib.util.spec_from_file_location('strategy_review',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        bad='def strategy(bars, config):\n    total = 0.0\n    for i in range(42):\n        total = total + float(bars[i]["close_usd"])\n    slow_average = total / 63.0\n    return 0.0\n'
        self.assertFalse(module.constant_mean_check(bad,'slow_average',63)['passed'])
        self.assertTrue(module.constant_mean_check(bad.replace('range(42)','range(63)'),'slow_average',63)['passed'])
    def test_costs_and_execution_cutoffs_match_baselines(self):
        r=evaluate_code(corpus(),CODE,{'validation'})
        c=r['cases'][0];dates={(p['start'],p['end']) for p in c['paper']}
        dates.update((p['start'],p['end']) for values in c['baselines'].values() for p in values)
        self.assertEqual(len(dates),1);self.assertGreater(r['common_rows'],20)
        self.assertTrue(all(d['decision_at']<d['execute_at'] for d in c['decisions']))

class WorkshopTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'run'
        self.settings=SimpleNamespace(model_id='doubao-seed-2.1-pro',base_url='authorized-plan',redact=str)
        self.calls=[]
    def role(self,settings,role,context,directory,cancel):
        self.calls.append((role,context))
        encoded=json.dumps(context);self.assertNotIn('target_return_fraction',encoded);self.assertNotIn('confirmation-2025',encoded)
        if role=='reviewer':return {'approved':True,'notes':['Research-only eligible; test costs and regime dependence.']}
        return {'code':CODE,'rationale':'Unverified momentum hypothesis','signals':['7d return'],'failure_conditions':['Reversal'],'assumptions':['One-bar lag and fixed costs']}
    def test_real_code_loop_locks_before_final_and_final_never_reaches_roles(self):
        with patch('research_agent.research_lab.workshop.load_corpus',return_value=corpus()):
            r=run_workshop('unused',self.path,self.settings,rounds=2,call_role=self.role)
            self.assertEqual(len(self.calls),4);self.assertEqual(r['selection']['round'],1) # Equal scores retain earlier attempt.
            self.assertTrue((self.path/'selection_lock.json').exists());self.assertEqual(r['orders_placed'],0)
            for _,context in self.calls:self.assertNotIn('confirmation',json.dumps(context.get('earlier_attempts',[])))
            before=(self.path/'confirmation.json').read_bytes()
            self.assertEqual(run_workshop('unused',self.path,self.settings,rounds=2,call_role=self.role),r)
            self.assertEqual(len(self.calls),4);self.assertEqual(before,(self.path/'confirmation.json').read_bytes())
    def test_saved_role_outputs_survive_stop_and_explicit_resume(self):
        cancel=threading.Event()
        def role(*args):
            result=self.role(*args)
            if args[1]=='reviewer':cancel.set()
            return result
        with patch('research_agent.research_lab.workshop.load_corpus',return_value=corpus()):
            with self.assertRaisesRegex(RuntimeError,'Stopped|stopped'):run_workshop('unused',self.path,self.settings,rounds=2,call_role=role,cancel=cancel)
            calls=len(self.calls);cancel.clear()
            r=run_workshop('unused',self.path,self.settings,rounds=2,call_role=self.role,cancel=cancel)
            self.assertEqual(len(self.calls),calls+2);self.assertEqual(r['selection']['round'],1)
    def test_rejected_attempts_are_retained_and_no_confirmation_is_fabricated(self):
        def reject(settings,role,*args):return {'approved':False,'notes':['Missing rationale']} if role=='reviewer' else self.role(settings,role,*args)
        with patch('research_agent.research_lab.workshop.load_corpus',return_value=corpus()),self.assertRaisesRegex(ValueError,'No eligible'):
            run_workshop('unused',self.path,self.settings,rounds=1,call_role=reject)
        self.assertTrue((self.path/'round-1/summary.json').exists());self.assertFalse((self.path/'confirmation.json').exists())
    def test_changed_config_or_selected_code_refuses_resume(self):
        with patch('research_agent.research_lab.workshop.load_corpus',return_value=corpus()):
            run_workshop('unused',self.path,self.settings,rounds=1,call_role=self.role)
            with self.assertRaisesRegex(ValueError,'changed'):run_workshop('unused',self.path,self.settings,rounds=2,call_role=self.role)
