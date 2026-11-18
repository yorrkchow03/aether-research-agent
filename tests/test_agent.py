from fixtures import project_fixture,fixture_run
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.tools import tool

from research_agent.bridge import write_reply
from research_agent.reports import ReportDraft
from research_agent.runtime import AgentRuntime, PROJECT_ROOT
from research_agent.server import create_app
from research_agent.tools import ResearchTools


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.runtime=AgentRuntime(root=project_fixture(Path(self.temp.name)/"project"),state_dir=Path(self.temp.name)/"runtime",model_mode="current_conversation_no_api")
        self.session=self.runtime.create_session()
        self.sid=self.session["session_id"]
        self.workspace=self.runtime._session_dir(self.sid)/"workspace"
        self.tools=ResearchTools(Path(self.session["data_run"]),self.workspace,self.session["cutoff"])

    def wait_state(self, state):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            jobs=self.runtime.snapshot(self.sid)["jobs"]
            if jobs and jobs[-1]["state"]==state:return jobs[-1]
            if jobs and jobs[-1]["state"]=="failed":self.fail(jobs[-1]["error"])
            time.sleep(.02)
        self.fail("Expected state "+state)

    def reply(self, content="", calls=None):
        job=self.wait_state("waiting_for_conversation")
        packet=Path(job["pending_request"])
        write_reply(packet,{"content":content,"tool_calls":calls or []})
        # Wait until this reply was consumed or a new request appears.
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            current=self.runtime.snapshot(self.sid)["jobs"][-1]
            if current.get("pending_request")!=str(packet):return
            time.sleep(.02)
        self.fail("Reply was not consumed")

    def test_deep_agents_executes_tools_and_persists_conversation(self):
        self.runtime.start(self.sid,"fixture: calculate ETH return")
        first=self.wait_state("waiting_for_conversation")
        packet=json.loads(Path(first["pending_request"]).read_text())
        names={x["function"]["name"] for x in packet["tools"]}
        self.assertIn("write_todos",names);self.assertIn("read_file",names)
        self.assertNotIn("execute",names);self.assertNotIn("task",names)
        context=json.dumps(packet['messages'])
        for skill in ('research','quantitative-analysis','strategy-research','portfolio-analysis'):
            self.assertIn(skill,context)
        self.assertTrue((self.workspace/'skills/quantitative-analysis/scripts/market_statistics.py').exists())
        self.reply(calls=[{"name":"analyze_market","args":{"asset":"ETH","days":30}},
                         {"name":"write_file","args":{"file_path":"/data/forged.json","content":"fake"}}])
        second=self.wait_state("waiting_for_conversation")
        messages=json.loads(Path(second["pending_request"]).read_text())["messages"]
        results=[m["data"]["content"] for m in messages if m["type"]=="tool"]
        self.assertTrue(any('"return_fraction"' in result for result in results))
        self.assertFalse((self.workspace/"data/forged.json").exists())
        self.assertTrue(any("denied" in result.lower() or "not allowed" in result.lower() for result in results))
        self.reply("fixture calculation completed")
        self.wait_state("completed")
        extra=self.workspace/'skills/fixture-extra/SKILL.md'
        extra.parent.mkdir()
        extra.write_text('---\nname: fixture-extra\ndescription: Synthetic rediscovery test\n---\nFixture instructions.\n')
        restored=AgentRuntime(root=self.runtime.root,state_dir=self.runtime.state_dir,model_mode="current_conversation_no_api")
        restored.start(self.sid,"fixture: remember the previous question")
        self.runtime=restored
        third=self.wait_state("waiting_for_conversation")
        messages=json.loads(Path(third["pending_request"]).read_text())["messages"]
        self.assertTrue(any(m["data"]["content"]=="fixture calculation completed" for m in messages))
        self.assertIn('fixture-extra',json.dumps(messages))
        self.reply("fixture followup completed");self.wait_state("completed")

    def test_unregistered_model_tool_rejected_and_cancel(self):
        self.runtime.start(self.sid,"fixture")
        job=self.wait_state("waiting_for_conversation")
        with self.assertRaises(ValueError):write_reply(Path(job["pending_request"]),{"tool_calls":[{"name":"execute","args":{}}]})
        self.runtime.stop(self.sid);self.wait_state("cancelled")

    def test_browser_publication_uses_the_current_request_intent(self):
        app=create_app(self.runtime)
        client=TestClient(app,base_url='http://127.0.0.1:8765')
        headers={'X-Local-Token':app.state.csrf_token}
        route=f'/api/sessions/{self.sid}/messages'
        response=client.post(route,headers=headers,json={'prompt':'Refresh ETH','purpose':'market_refresh','deliverable':'report'})
        self.assertEqual(response.status_code,400)
        response=client.post(route,headers=headers,json={'prompt':'Refresh ETH','purpose':'market_refresh'})
        self.assertEqual(response.status_code,200)
        job=self.wait_state('waiting_for_conversation')
        tools=json.loads(Path(job['pending_request']).read_text())['tools']
        self.assertNotIn('publish_report',{tool['function']['name'] for tool in tools})
        self.runtime.stop(self.sid)
        self.wait_state('cancelled')

        calc=self.tools.analyze("ETH",30)
        draft={"title":"ETH weekly report","summary":"Fixture report","observations":[
            {"statement":"Saved return","evidence_ids":[calc["record_id"]],"metric":"return_fraction",
             "value":calc["values"]["return_fraction"],"unit":"fraction"}],
            "judgment":"insufficient_evidence","judgment_reason":"Fixture","limitations":["Fixture"]}
        for prompt in ('解释 ETH 质押是什么。', '不要生成报告，告诉我 ETH 质押是什么。'):
            with self.subTest(prompt=prompt):
                response=client.post(route,headers=headers,json={'prompt':prompt}).raise_for_status().json()
                self.assertEqual(response['deliverable'],'answer')
                self.reply(calls=[{'name':'publish_report','args':{'draft':draft,'user_request':prompt}}])
                pending=self.wait_state('waiting_for_conversation')
                messages=json.loads(Path(pending['pending_request']).read_text())['messages']
                rejection=next(message['data'] for message in reversed(messages) if message['type']=='tool')
                self.assertIn('explicit publication request',rejection['content'])
                self.assertFalse(self.runtime.snapshot(self.sid)['reports'])
                self.reply('Fixture answer.')
                self.wait_state('completed')

        request='生成这一周的报告'
        response=client.post(route,headers=headers,json={'prompt':request})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['deliverable'],'report')
        self.assertTrue(response.json()['report_from_chat'])
        self.reply(calls=[{'name':'publish_report','args':{'draft':draft}}])
        # Successful publication completes in the host without another author turn.
        result = self.wait_state('completed')
        self.assertEqual(len(result['published_reports']),1)
        self.assertTrue(result['answer'].startswith('Report published: [ETH weekly report]'))

    def test_continue_preserves_the_original_report_request(self):
        app=create_app(self.runtime)
        client=TestClient(app,base_url='http://127.0.0.1:8765')
        self.addCleanup(client.close)
        headers={'X-Local-Token':app.state.csrf_token}
        route=f'/api/sessions/{self.sid}'
        request='Give me report about ETH in a recent week.'
        with patch('research_agent.runtime.threading.Thread'):
            first=client.post(route+'/messages',headers=headers,json={'prompt':request}).raise_for_status().json()
            self.assertEqual(first['deliverable'],'report')
            path=self.runtime._session_dir(self.sid)/'jobs'/first['job_id']/'status.json'
            first.update(state='failed',error='Synthetic interruption')
            path.write_text(json.dumps(first))
            self.runtime.active.pop(self.sid)
            resumed=client.post(route+'/jobs/'+first['job_id']+'/continue',headers=headers).raise_for_status().json()
            self.assertEqual(resumed['prompt'],request)
            self.assertEqual(resumed['deliverable'],'report')
            self.assertEqual(resumed['continued_from_job_id'],first['job_id'])
            self.assertEqual(client.post(route+'/jobs/'+first['job_id']+'/continue',headers=headers).status_code,400)

    def test_recent_month_routes_and_restores_legacy_completed_answer(self):
        app = create_app(self.runtime)
        client = TestClient(app,base_url='http://127.0.0.1:8765')
        self.addCleanup(client.close)
        headers = {'X-Local-Token':app.state.csrf_token}
        sid = self.runtime.create_session(research_mode='current')['session_id']
        route = f'/api/sessions/{sid}'
        prompt = '给我近一个月的报告'
        clock = '2026-11-18T13:25:37Z'
        period = ['2026-10-18T00:00:00Z','2026-11-18T00:00:00Z']
        with (patch('research_agent.runtime.threading.Thread'),
              patch('research_agent.runtime.utcnow',return_value=clock)):
            first = client.post(route+'/messages',headers=headers,json={'prompt':prompt}).raise_for_status().json()
            self.assertEqual(first['deliverable'],'report')
            self.assertTrue(first['report_from_chat'])
            self.assertEqual(first['required_report_window'],period)
            path = self.runtime._session_dir(sid)/'jobs'/first['job_id']/'status.json'
            first.update(state='completed',deliverable='answer',published_reports=[])
            first.pop('report_from_chat')
            first.pop('report_window_reference_at')
            first.pop('required_report_window')
            path.write_text(json.dumps(first))
            self.runtime.active.pop(sid)
            old = client.get(route).raise_for_status().json()['jobs'][0]
            self.assertTrue(old['report_retry_available'])
            resumed = client.post(route+'/jobs/'+first['job_id']+'/continue',headers=headers).raise_for_status().json()
            self.assertEqual(resumed['deliverable'],'report')
            self.assertEqual(resumed['required_report_window'],period)
            self.assertEqual(resumed['continued_from_job_id'],first['job_id'])
            newer = path.parent.parent/resumed['job_id']/'status.json'
            resumed.update(state='completed',published_reports=['/reports/research-ae391ead7b91.md'],
                           report_delivery_kind='minimal')
            newer.write_text(json.dumps(resumed))
            self.runtime.active.pop(sid)
            self.assertEqual(client.post(route+'/jobs/'+first['job_id']+'/continue',headers=headers).status_code,400)

    def test_evidence_gap_retry_keeps_original_report_window_and_notices(self):
        app = create_app(self.runtime)
        client = TestClient(app,base_url='http://127.0.0.1:8765')
        self.addCleanup(client.close)
        headers = {'X-Local-Token':app.state.csrf_token}
        sid = self.runtime.create_session(research_mode='current')['session_id']
        route = f'/api/sessions/{sid}'
        prompt = '给我近一个月的报告'
        notice = '/reports/research-000000000001.md'
        with (patch('research_agent.runtime.threading.Thread'),
              patch('research_agent.runtime.utcnow',return_value='2026-11-18T13:25:37Z')):
            first = client.post(route+'/messages',headers=headers,json={'prompt':prompt}).raise_for_status().json()
        jobs = self.runtime._session_dir(sid)/'jobs'
        original = jobs/first['job_id']/'status.json'
        first.update(state='completed', published_reports=[notice], report_delivery_kind='evidence_gap')
        original.write_text(json.dumps(first))
        self.runtime.active.pop(sid)
        self.assertTrue(client.get(route).json()['jobs'][0]['report_retry_available'])
        with (patch('research_agent.runtime.threading.Thread'),
              patch('research_agent.runtime.utcnow',return_value='2026-11-22T13:25:37Z')):
            resumed = client.post(route+'/jobs/'+first['job_id']+'/continue',headers=headers).raise_for_status().json()
        self.assertEqual(resumed['required_report_window'],first['required_report_window'])
        self.assertEqual(resumed['prompt'],prompt)
        self.assertEqual(json.loads(original.read_text())['published_reports'],[notice])
        resumed.update(state='completed', published_reports=['/reports/research-000000000002.md'],
                       report_delivery_kind='minimal')
        (jobs/resumed['job_id']/'status.json').write_text(json.dumps(resumed))
        self.runtime.active.pop(sid)
        self.assertFalse(client.get(route).json()['jobs'][0]['report_retry_available'])
        self.assertEqual(client.post(route+'/jobs/'+first['job_id']+'/continue',headers=headers).status_code,400)

    def test_report_task_excludes_the_chat_answer_submission_tool(self):
        @tool
        def submit_research_answer():
            """Synthetic alternative output for the tool-selection regression."""
            return 'Fixture answer'
        build=self.runtime.build_research_tools
        def with_answer_tool(*args,**kwargs):
            service=build(*args,**kwargs)
            original=service.as_tools
            service.as_tools=lambda:[*original(),submit_research_answer]
            return service
        with patch.object(self.runtime,'build_research_tools',side_effect=with_answer_tool):
            self.runtime.start(self.sid,'Generate an ETH report',request_purpose='chat')
            job=self.wait_state('waiting_for_conversation')
            packet=json.loads(Path(job['pending_request']).read_text())
            names={t['function']['name'] for t in packet['tools']}
            self.assertIn('publish_report',names)
            self.assertNotIn('submit_research_answer',names)
            self.runtime.stop(self.sid)
            self.wait_state('cancelled')

    def test_report_numeric_and_unit_checks(self):
        calc=self.tools.analyze("ETH",30)
        draft={"title":"fixture","summary":"fixture","observations":[{"statement":"return","evidence_ids":[calc["record_id"]],"metric":"return_fraction","value":calc["values"]["return_fraction"],"unit":"fraction"}],"judgment":"insufficient_evidence","judgment_reason":"fixture","limitations":["fixture"]}
        self.assertEqual(self.tools.publish(draft)["validation"]["status"],"passed")
        draft["observations"][0]["value"]="99999"
        with self.assertRaises(ValueError):self.tools.publish(draft)
        draft["observations"][0].update(value=calc["values"]["return_fraction"],unit="USD")
        with self.assertRaises(ValueError):self.tools.publish(draft)

    def test_cutoff_and_strict_mode(self):
        recent=self.tools.market()["latest"]["record_id"]
        past=ResearchTools(Path(self.session["data_run"]),self.workspace,"2026-09-01T00:00:00Z")
        with self.assertRaises(KeyError):past.lookup(recent)
        strict=ResearchTools(Path(self.session["data_run"]),self.workspace,self.session["cutoff"],"strict")
        self.assertEqual(strict.market()["status"],"unavailable_at_cutoff")
        with self.assertRaises(ValueError):self.runtime.create_session(cutoff="2030-01-01T00:00:00Z")

    def test_wildcard_units_for_etf(self):
        item=self.tools.chain("etf_flows")["datasets"][0]["latest"]
        draft={"title":"fixture","summary":"fixture","observations":[{"statement":"ETF","evidence_ids":[item["record_id"]],"metric":"total_net_flow_usd","value":item["values"]["total_net_flow_usd"],"unit":"USD/trading day"}],"judgment":"insufficient_evidence","judgment_reason":"fixture","limitations":["fixture"]}
        self.assertEqual(self.tools.publish(draft)["validation"]["numeric_claims_checked"],1)

    def test_console_origin_token_and_traversal(self):
        app=create_app(self.runtime)
        with TestClient(app,base_url="http://127.0.0.1:8765") as client:
            self.assertEqual(client.get("/api/sessions").status_code,200)
            self.assertEqual(client.post("/api/sessions",json={}).status_code,403)
            token={"X-Local-Token":app.state.csrf_token}
            self.assertEqual(client.post("/api/sessions",json={},headers=token).status_code,200)
            self.assertEqual(client.get("/api/sessions",headers={"Origin":"https://other.example"}).status_code,403)
            self.assertEqual(client.get("/api/sessions",headers={"Host":"other.example"}).status_code,403)
            r=client.get(f"/api/sessions/{self.sid}/artifact",params={"path":"/../../session.json"})
            self.assertEqual(r.status_code,400)


if __name__=="__main__":unittest.main()
