from fixtures import project_fixture,fixture_run
import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

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
