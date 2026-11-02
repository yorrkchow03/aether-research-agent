from fixtures import project_fixture,fixture_run
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_agent.runtime import AgentRuntime, PROJECT_ROOT
from research_agent.settings import Settings, PLAN_BASE_URL
from research_agent.tools import ResearchTools
from research_agent.web_search import citation_items
from research_agent.bridge import write_reply


class FakeSearch:
    settings=SimpleNamespace(model_id="fixture")
    def search(self,query,max_results,time_range):
        raw=b'{"fixture":true}'
        return {"raw":raw,"sha256":hashlib.sha256(raw).hexdigest(),"retrieved_at":"2026-10-13T14:00:00Z",
                "items":[{"type":"url_citation","title":"Fixture official post","url":"https://example.org/news","publish_time":"2026-10-13T09:00:00Z","summary":"Fixture source excerpt"}],
                "provider_status":"completed","usage":{},"note":"Fixture, not live network"}


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=Path(self.temp.name)/"workspace"
        run=fixture_run(Path(self.temp.name)/'project')
        self.tools=ResearchTools(run,self.workspace,"2026-10-13T00:00:00Z",web_search=FakeSearch(),allow_live=True)

    def test_public_settings_never_contain_secret_and_require_plan_endpoint(self):
        secret="fixture-private-secret"
        cfg=Settings(model_mode="ark_plan",api_key=secret,search_enabled=True)
        self.assertNotIn(secret,json.dumps(cfg.public()));self.assertNotIn(secret,repr(cfg))
        self.assertEqual(cfg.redact("failed "+secret),"failed [REDACTED]")
        root=Path(self.temp.name)
        (root/".env").write_text("AGENT_MODEL_MODE=ark_plan\nARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3\n")
        with self.assertRaises(ValueError):Settings.load(root)

    def test_only_provider_annotations_are_citable(self):
        response={"output":[{"type":"message","content":[{"type":"output_text","text":"invented https://example.net", "annotations":[
            {"type":"url_citation","url":"https://example.org","summary":"source"},
            {"type":"url_citation","url":"https://example.org","summary":"duplicate"},
            {"type":"url_citation","url":"javascript:alert(1)"},
            {"type":"url_citation","url":"https://name:password@example.org"}]}]}]}
        found=citation_items(response)
        self.assertEqual(len(found),1);self.assertEqual(found[0]["url"],"https://example.org")

    def test_online_evidence_separate_from_historical_cutoff_and_hash_verified(self):
        result=self.tools.online_search("fixture");rid=result["results"][0]["record_id"]
        self.assertEqual(self.tools.evidence(rid)["record"]["text"],"Fixture source excerpt")
        historical=ResearchTools(self.tools.run_dir,self.workspace,self.tools.cutoff)
        with self.assertRaises(ValueError):historical.lookup(rid)
        draft={"title":"fixture","summary":"fixture","observations":[{"statement":"source excerpt","evidence_ids":[rid]}],"judgment":"insufficient_evidence","judgment_reason":"fixture","limitations":["fixture"]}
        report=self.tools.publish(draft)
        obj=json.loads((self.workspace/report["structured_path"].lstrip("/")).read_text())
        self.assertEqual(obj["cutoff"],"2026-10-13T00:00:00Z")
        self.assertEqual(obj["online_retrieved_through"],"2026-10-13T14:00:00Z")
        source=self.tools.lookup(rid)["source"]
        (self.workspace/"data/web/raw"/(source["sha256"]+".json")).write_text("changed")
        with self.assertRaises(ValueError):self.tools.evidence(rid)

    def test_live_search_budget(self):
        for _ in range(3):self.tools.online_search("fixture")
        with self.assertRaises(ValueError):self.tools.online_search("fixture")

    def test_tool_errors_return_to_model_instead_of_killing_run(self):
        runtime=AgentRuntime(root=project_fixture(Path(self.temp.name)/"project"),state_dir=Path(self.temp.name)/"runtime",model_mode="current_conversation_no_api")
        session=runtime.create_session();sid=session["session_id"]
        runtime.start(sid,"fixture bad evidence")
        def pending(previous=None):
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                status=runtime.snapshot(sid)["jobs"][-1]
                if status.get("pending_request") and status["pending_request"]!=previous:return Path(status["pending_request"])
                if status["state"]=="failed":self.fail(status["error"])
                time.sleep(.02)
            self.fail("No model request")
        first=pending();write_reply(first,{"tool_calls":[{"name":"read_evidence","args":{"record_id":"absent"}}]})
        second=pending(str(first));packet=json.loads(second.read_text())
        result=packet["messages"][-1]
        self.assertEqual(result["type"],"tool");self.assertEqual(result["data"]["status"],"error")
        write_reply(second,{"content":"fixture acknowledged error"})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if runtime.snapshot(sid)["jobs"][-1]["state"]=="completed":break
            time.sleep(.02)
        self.assertEqual(runtime.snapshot(sid)["jobs"][-1]["state"],"completed")


if __name__=="__main__":unittest.main()
