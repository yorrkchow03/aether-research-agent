import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from research_agent.job_progress import _TAIL_BYTES, research_progress
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app


class JobProgressTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.job = {'job_id': 'b' * 32}

    def test_session_projects_stages_without_tool_trace(self):
        runtime = AgentRuntime(root=self.root, model_mode='current_conversation_no_api')
        app = create_app(runtime)
        sid = 'a' * 32
        session = runtime.sessions_dir / sid
        job_dir = session / 'jobs' / self.job['job_id']
        job_dir.mkdir(parents=True)
        (session / 'session.json').write_text(json.dumps({'session_id': sid}))
        (job_dir / 'status.json').write_text(json.dumps({**self.job, 'state': 'running', 'created_at': '2026-11-07T02:05:53Z'}))
        log = job_dir / 'events.jsonl'
        events = [
            {'kind': 'plan_saved', 'todos': ['private fixture']},
            {'kind': 'tool_started', 'name': 'sdk:local-market'},
            {'kind': 'tool_started', 'name': 'search_web', 'arguments_preview': 'private fixture'},
            {'kind': 'tool_started', 'name': 'publish_report'},
            {'kind': 'tool_finished', 'name': 'publish_report', 'status': 'error'},
            {'kind': 'model_started'},
            {'kind': 'plan_updated'},
            {'kind': 'tool_finished', 'name': 'search_web', 'status': 'success'},
        ]
        log.write_text(''.join(json.dumps(event) + '\n' for event in events))
        with TestClient(app, base_url='http://127.0.0.1:8765') as client:
            response = client.get('/api/sessions/' + sid).raise_for_status()
            self.assertNotIn('private fixture', response.text)
            self.assertEqual(response.json()['research_progress'], {
                **self.job, 'stage': 'revision', 'previous': ['web', 'verification'],
            })
            with log.open('a') as output:
                output.write(json.dumps({'kind': 'tool_finished', 'name': 'publish_report', 'status': 'success'}) + '\n')
            self.assertEqual(client.get('/api/sessions/' + sid).json()['research_progress']['stage'], 'finalizing')

    def test_missing_and_bounded_partial_log(self):
        log = self.root / 'events.jsonl'
        self.assertEqual(research_progress(self.job, log)['stage'], 'starting')
        with log.open('w') as output:
            output.write(json.dumps({'kind': 'plan_saved', 'data': 'x' * _TAIL_BYTES}) + '\n')
            output.write('{"kind":"tool_started","name":"read_evidence"}\n{"kind":')
        self.assertEqual(research_progress(self.job, log), {**self.job, 'stage': 'evidence', 'previous': []})

    def test_chat_progress_only_shows_planning_when_a_plan_is_actually_saved(self):
        log=self.root/'events.jsonl'
        job={**self.job,'response_mode':'conversation'}
        self.assertEqual(research_progress(job,log)['stage'],'responding')
        log.write_text('{"kind":"harness_started"}\n{"kind":"model_started"}\n')
        self.assertEqual(research_progress(job,log)['stage'],'responding')
        with log.open('a') as out:out.write('{"kind":"plan_saved"}\n')
        self.assertEqual(research_progress(job,log)['stage'],'planning')

    def test_chat_status_distinguishes_answer_from_report_without_running_research(self):
        runtime = AgentRuntime(root=self.root, model_mode='current_conversation_no_api')
        app = create_app(runtime)
        sid = 'a' * 32
        session = runtime.sessions_dir / sid
        job_dir = session / 'jobs' / self.job['job_id']
        job_dir.mkdir(parents=True)
        (session / 'session.json').write_text(json.dumps({'session_id': sid}))
        record = {**self.job, 'state': 'completed', 'deliverable': 'answer',
                  'prompt': 'What changed this week?', 'created_at': '2026-01-01T00:00:00Z',
                  'answer': 'Saved research answer.', 'published_reports': []}
        (job_dir / 'status.json').write_text(json.dumps(record))
        (job_dir / 'events.jsonl').write_text('{"kind":"plan_saved"}\n')
        path = '/api/sessions/' + sid
        headers = {'X-Local-Token': app.state.csrf_token}
        with TestClient(app, base_url='http://127.0.0.1:8765') as client, patch.object(runtime, '_run') as run:
            status = client.post(path+'/messages', json={'prompt':'所以你是生成完毕了？'}, headers=headers).raise_for_status().json()
            self.assertEqual(status['chat_reply'], 'status')
            self.assertEqual(status['state'], 'completed')
            self.assertIn('没有发布报告', status['answer'])
            self.assertNotIn('verified_answer', status)
            self.assertEqual(client.get(path).json()['research_progress']['job_id'], self.job['job_id'])
            self.assertEqual(client.get(path+'/events').json(), [{'kind':'plan_saved'}])

            record.update(state='running', deliverable='answer', prompt='Give me report about ETH in a recent week.')
            (job_dir / 'status.json').write_text(json.dumps(record))
            with runtime.lock:
                runtime.active[sid] = (self.job['job_id'], threading.Event())
            status = client.post(path+'/messages', json={'prompt':'所以报告是生成完毕了？'}, headers=headers).raise_for_status().json()
            self.assertIn('仍在运行', status['answer'])
            self.assertEqual(client.get(path).json()['active_job_id'], self.job['job_id'])
            market = client.post(path+'/messages', json={'prompt':'你好，请分析 ETH'}, headers=headers)
            self.assertEqual(market.status_code, 400)
            run.assert_not_called()

            stale_stop = client.post(path+'/stop', params={'job_id':'c'*32}, headers=headers).raise_for_status().json()
            self.assertEqual(stale_stop['status'], 'idle')
            self.assertFalse(client.get(path).json()['stopping'])
            stop = client.post(path+'/stop', params={'job_id':self.job['job_id']}, headers=headers).raise_for_status().json()
            self.assertEqual(stop['status'], 'stopping')
            self.assertTrue(client.get(path).json()['stopping'])

            record.update(state='completed', published_reports=['/reports/research-0123456789ab.md'])
            (job_dir / 'status.json').write_text(json.dumps(record))
            with runtime.lock:
                runtime.active.pop(sid)
            status = client.post(path+'/messages', json={'prompt':'报告在哪看？'}, headers=headers).raise_for_status().json()
            self.assertIn('Analyst → Reports', status['answer'])

            record.update(report_delivery_kind='evidence_gap')
            (job_dir / 'status.json').write_text(json.dumps(record))
            for prompt in ('这个报告是啥意思', '报告发布了吗？'):
                status = client.post(path+'/messages', json={'prompt':prompt}, headers=headers).raise_for_status().json()
                self.assertIn('证据缺口说明', status['answer'])
                self.assertNotIn('报告已发布', status['answer'])
                self.assertEqual(status['published_reports'], [])
            run.assert_not_called()
