"""Plan ordering, failed writes and checkpoint isolation; no model/network calls."""
import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from research_agent.planning import (
    ResearchPlanningMiddleware, PLANNING_INSTRUCTIONS, PLAN_TOOL_DESCRIPTION,
)
from research_agent.prompts import research_system_prompt


PLAN = [
    {"content": "Scope: ETH, last seven days; capture dated close prices", "status": "in_progress"},
    {"content": "Calculate the return, verify sources and submit a concise answer", "status": "pending"},
]


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args=None, identifier="test"):
    return {"name": name, "args": args or {}, "id": identifier}


def request(name, args=None, state=None):
    return SimpleNamespace(tool_call=call(name, args), state=state or {})


def save_plan(req):
    return Command(update={"todos": req.tool_call["args"]["todos"], "messages": [
        ToolMessage(content="saved", tool_call_id=req.tool_call["id"])]})


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.gate = ResearchPlanningMiddleware("new-job", lambda kind, data: self.events.append((kind, data)))

    def test_no_research_side_effect_before_plan(self):
        for name in ("request_public_api", "search_web", "fetch_webpage", "recall_memory",
                     "read_evidence", "get_private_portfolio", "execute", "write_file",
                     "register_calculation", "record_news_event", "publish_report"):
            with self.subTest(name=name):
                result = self.gate.wrap_tool_call(request(name), lambda _: self.fail("Must not execute"))
                self.assertEqual(result.status, "error")
        self.assertEqual(len(self.events), 11)

    def test_preparation_and_clarification_remain_available(self):
        for name, args in [("read_file", {"file_path": "/skills/research/SKILL.md"}),
                           ("read_file", {"file_path": "/memory/preferences.md"}),
                           ("get_research_context", {}), ("list_data_sources", {}),
                           ("submit_research_answer", {"draft": {"next_steps": ["Which time horizon?"]}})]:
            marker = object()
            self.assertIs(self.gate.wrap_tool_call(request(name, args), lambda _: marker), marker)
        for name, args in [("read_file", {"file_path": "/skills/../data/prices.json"}),
                           ("read_file", {"file_path": None}),
                           ("read_file", {"file_path": "/data/prices.json"}),
                           ("grep", {"path": "/data", "pattern": ".*"}),
                           ("submit_research_answer", {"draft": {"next_steps": ["Review"], "facts": [{}]}})]:
            self.assertEqual(self.gate.wrap_tool_call(request(name, args), lambda _: self.fail("Must not execute")).status, "error")

    def test_invalid_initial_plans_do_not_authorize_work(self):
        invalid = [[], PLAN[:1], [{"content": "", "status": "in_progress"}, PLAN[1]],
                   [PLAN[0], PLAN[0]], [{**p, "status": "completed"} for p in PLAN],
                   [{**p, "status": "pending"} for p in PLAN],
                   [{**PLAN[0], "status": []}, PLAN[1]],
                   [{**PLAN[0], "status": "unknown"}, PLAN[1]]]
        for todos in invalid:
            with self.subTest(todos=todos):
                result = self.gate.wrap_tool_call(request("write_todos", {"todos": todos}), lambda _: self.fail("Invalid plan must not save"))
                self.assertEqual(result.status, "error")
        self.assertFalse(any(kind == "plan_saved" for kind, _ in self.events))

    def test_only_successful_plan_command_authorizes_current_job(self):
        req = request("write_todos", {"todos": deepcopy(PLAN)})
        failed = ToolMessage(content="write failed", tool_call_id="test", status="error")
        self.assertIs(self.gate.wrap_tool_call(req, lambda _: failed), failed)
        wrong = Command(update={"todos": [], "messages": [ToolMessage(content="not saved", tool_call_id="test")]})
        self.assertIs(self.gate.wrap_tool_call(req, lambda _: wrong), wrong)
        result = self.gate.wrap_tool_call(req, save_plan)
        self.assertEqual(result.update["research_plan_job_id"], "new-job")
        marker = object()
        self.assertIs(self.gate.wrap_tool_call(request("execute", state=result.update), lambda _: marker), marker)

    def test_completed_plan_needs_update_for_new_research(self):
        state = {"research_plan_job_id": "new-job", "todos": [{**p, "status": "completed"} for p in PLAN]}
        self.assertEqual(self.gate.wrap_tool_call(request("execute", state=state), lambda _: self.fail("No active step")).status, "error")
        # Finishing the publication and honest completion updates remain possible.
        marker = object()
        self.assertIs(self.gate.wrap_tool_call(request("publish_report", state=state), lambda _: marker), marker)
        result = self.gate.wrap_tool_call(request("write_todos", {"todos": state["todos"]}, state), save_plan)
        self.assertEqual(result.update["research_plan_job_id"], "new-job")

    def test_async_wrapper_has_the_same_ordering(self):
        async def scenario():
            async def handler(req):
                return save_plan(req)
            result = await self.gate.awrap_tool_call(request("write_todos", {"todos": PLAN}), handler)
            self.assertEqual(result.update["research_plan_job_id"], "new-job")
            blocked = await self.gate.awrap_tool_call(request("execute"), handler)
            self.assertEqual(blocked.status, "error")
        asyncio.run(scenario())

    def test_current_instructions_only(self):
        meta = {"cutoff": "2026-01-01T00:00:00Z", "mode": "observed"}
        self.assertIn(PLANNING_INSTRUCTIONS, research_system_prompt(meta, True, True))
        self.assertNotIn(PLANNING_INSTRUCTIONS, research_system_prompt(meta, False, False))


class PlanningGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.effects = []
        self.events = []
        self.saver = InMemorySaver()
        self.config = {"configurable": {"thread_id": "same-session"}}

    def graph(self, job, responses):
        effects = self.effects

        @tool
        def capture_prices() -> str:
            """Capture synthetic prices for offline plan-order testing only."""
            effects.append("capture")
            return "synthetic test evidence"

        return create_deep_agent(ScriptedModel(responses=responses), tools=[capture_prices],
            backend=FilesystemBackend(root_dir=Path(self.temp.name), virtual_mode=True),
            middleware=[TodoListMiddleware(system_prompt=PLANNING_INSTRUCTIONS, tool_description=PLAN_TOOL_DESCRIPTION),
                        ResearchPlanningMiddleware(job, lambda k, d: self.events.append((k, d)))],
            checkpointer=self.saver)

    def test_parallel_plan_and_research_cannot_bypass_order(self):
        graph = self.graph("job-1", [
            AIMessage(content="", tool_calls=[call("write_todos", {"todos": PLAN}, "plan"), call("capture_prices", identifier="too-early")]),
            AIMessage(content="", tool_calls=[call("capture_prices", identifier="after-plan")]),
            AIMessage(content="done"),
        ])
        state = graph.invoke({"messages": [HumanMessage(content="Detailed research")]}, self.config)
        results = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}
        self.assertEqual(results["too-early"].status, "error")
        self.assertEqual(results["after-plan"].status, "success")
        self.assertEqual(self.effects, ["capture"])
        self.assertEqual(state["research_plan_job_id"], "job-1")
        self.assertEqual(state["todos"], PLAN)

    def test_checkpoint_plan_is_reused_only_within_same_job(self):
        graph = self.graph("job-1", [AIMessage(content="", tool_calls=[call("write_todos", {"todos": PLAN})]), AIMessage(content="planned")])
        graph.invoke({"messages": [HumanMessage(content="Research")]}, self.config)
        same_job = self.graph("job-1", [AIMessage(content="", tool_calls=[call("capture_prices", identifier="continuation")]), AIMessage(content="done")])
        same_job.invoke({"messages": [HumanMessage(content="Submission repair within same job")]}, self.config)
        new_job = self.graph("job-2", [
            AIMessage(content="", tool_calls=[call("capture_prices", identifier="stale")]),
            AIMessage(content="", tool_calls=[call("write_todos", {"todos": PLAN}, "fresh")]),
            AIMessage(content="", tool_calls=[call("capture_prices", identifier="new-work")]), AIMessage(content="done")])
        state = new_job.invoke({"messages": [HumanMessage(content="New question")]}, self.config)
        results = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}
        self.assertEqual(results["stale"].status, "error")
        self.assertEqual(self.effects, ["capture", "capture"])
        self.assertEqual(state["research_plan_job_id"], "job-2")

    def test_duplicate_plan_calls_do_not_save_or_authorize(self):
        graph = self.graph("job-1", [
            AIMessage(content="", tool_calls=[call("write_todos", {"todos": PLAN}, "p1"), call("write_todos", {"todos": PLAN}, "p2")]),
            AIMessage(content="", tool_calls=[call("capture_prices", identifier="blocked")]), AIMessage(content="stopped")])
        state = graph.invoke({"messages": [HumanMessage(content="Research")]}, self.config)
        self.assertFalse(self.effects)
        self.assertNotIn("research_plan_job_id", state)
        self.assertFalse(any(k == "plan_saved" for k, _ in self.events))


if __name__ == "__main__":
    unittest.main()
