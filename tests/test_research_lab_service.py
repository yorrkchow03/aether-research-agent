import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from research_agent.research_lab.service import ResearchLabService
from research_agent.settings import Settings
from research_agent.live import atomic

class LabServiceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);root=Path(t.name)
        self.runtime=SimpleNamespace(root=root,state_dir=root/'runtime',settings=Settings(),lock=threading.RLock(),active={},investment=SimpleNamespace(worker=None))
        self.service=ResearchLabService(self.runtime)
    def frozen_marker(self):
        self.service.corpus.mkdir(parents=True);atomic(self.service.corpus/'freeze.json',{})
        atomic(self.service.corpus/'manifest.json',{'datasets':[],'start':'2020-01-01','end':'2026-10-16'})
    def test_import_and_status_do_not_trigger_data_or_models(self):
        c=self.service.config();self.assertFalse(c['corpus_ready']);self.assertFalse(c['model_ready']);self.assertEqual(c['engine']['argo'],'not_integrated')
        self.assertEqual(self.service.list(),[]);self.assertFalse(self.service.state.exists())
    def test_disabled_extension_and_missing_corpus_fail_without_job(self):
        with self.assertRaisesRegex(ValueError,'freeze'):self.service.start()
        with patch.dict(os.environ,{'AETHER_RESEARCH_LAB_ENABLED':'false'}):s=ResearchLabService(self.runtime)
        with self.assertRaisesRegex(ValueError,'disabled'):s.start()
        self.assertEqual(self.service.list(),[])
    def test_main_active_job_is_not_restarted_or_competed_with(self):
        self.frozen_marker();self.runtime.active['existing']=('job',None)
        with self.assertRaisesRegex(ValueError,'Main research'):self.service.start()
        self.assertEqual(self.runtime.active,{'existing':('job',None)});self.assertEqual(self.service.list(),[])
    def test_restart_marks_interrupted_without_auto_paid_resume(self):
        d=self.service.state/'runs'/('a'*32);atomic(d/'status.json',{'run_id':'a'*32,'kind':'workshop','state':'running','started_at':'2026-01-01'})
        s=ResearchLabService(self.runtime);self.assertEqual(s.get('a'*32)['status']['state'],'interrupted');self.assertIsNone(s.worker)
    def test_resume_refuses_failed_and_completed_jobs(self):
        self.frozen_marker()
        for status in ('failed','completed'):
            atomic(self.service.state/'runs'/('a'*32)/'status.json',{'kind':'workshop','state':status,'started_at':'2026-01-01'})
            with self.assertRaisesRegex(ValueError,'interrupted'):self.service.start(resume='a'*32)
    def test_path_escapes_and_nonfinite_parameters_are_not_routes(self):
        from research_agent.research_lab.service import LabRunInput
        for rid in ('../x','a'*31,'f'*32+'x'):
            with self.assertRaises(ValueError):self.service.get(rid)
        with self.assertRaises(ValueError):LabRunInput(rounds=4)
        with self.assertRaises(ValueError):LabRunInput(kind='orders')

class LabAPITests(unittest.TestCase):
    def test_optional_routes_inherit_local_token_and_leave_default_state_empty(self):
        from fastapi.testclient import TestClient
        from research_agent.runtime import AgentRuntime
        from research_agent.server import create_app
        with tempfile.TemporaryDirectory() as tmp:
            runtime=AgentRuntime(root=Path(tmp));app=create_app(runtime)
            with TestClient(app,base_url='http://127.0.0.1:8765') as c:
                status=c.get('/api/research-lab');self.assertEqual(status.status_code,200)
                self.assertEqual(status.json()['runs'],[])
                self.assertEqual(c.get('/assets/research-lab.js').status_code,200)
                self.assertEqual(c.post('/api/research-lab/runs',json={'kind':'signals'}).status_code,403)
                headers={'x-local-token':app.state.csrf_token}
                self.assertEqual(c.post('/api/research-lab/runs',json={'kind':'signals'},headers=headers).status_code,400)
                self.assertEqual(c.post('/api/research-lab/runs',json={'kind':'orders'},headers=headers).status_code,422)
                self.assertEqual(c.get('/api/portfolio/transactions').json()['items'],[])
                self.assertEqual(c.get('/api/subscriptions').json()['items'],[])
