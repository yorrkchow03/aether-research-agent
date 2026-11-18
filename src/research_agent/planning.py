"""Per-job plan-first ordering for the current research harness.

This is an execution-order contract, not a strategy or a second planner model.
The existing write_todos tool and LangGraph checkpoint store the model's plan.
"""
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Annotated

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import OmitFromInput
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from typing_extensions import NotRequired


PLANNING_INSTRUCTIONS = """Plan-first research contract:
Before collecting new evidence, computing, writing research files, or
publishing substantive findings, call write_todos to save a plan for THIS request.
For detailed questions, the plan must state the objective, asset/time scope and
relevant unknowns, then actionable research steps and completion criteria
(source checks, calculations if needed, and the requested answer/report).
Choose the steps to fit the question; no fixed list of indicators or data sources
is required. A small research request may use a short two-step plan.
You may first read saved evidence, task notes, applicable skills, source metadata or user preferences,
or ask a clarification-only question. Pure clarification needs no research plan.
Use one write_todos call in its own tool turn and wait for success before research
tools. Old session plans do not authorize a new request: refresh the plan, retaining
honestly completed reusable work rather than repeating it.
Execute according to the plan. Keep the active step in_progress; update the list
when steps finish, evidence changes the approach, or a blocker needs another path.
Only mark work completed after it succeeds. Record unavailable data and unresolved
questions honestly; never label a blocked investigation completed. After a successful
submission, the host records report completion without an extra plan-only model turn.
"""

PLAN_TOOL_DESCRIPTION = """Save or update this request's research plan. Required before
substantive research, even when an earlier session plan exists. Include the objective
and scope, actionable steps, and completion checks. Initially provide 2–12 distinct
items. Status is pending, in_progress or completed; the host activates the first
pending step when no step is active, avoiding an extra planning round.
Call once in a separate tool turn before research; update honestly as work progresses.
Clarification-only replies do not require a plan."""

CONVERSATION_PLANNING_INSTRUCTIONS = """For this chat turn, answer directly from the
conversation and the supplied saved context when they suffice. You may read saved
reports, recall local material and inspect existing evidence without a plan.
Use write_todos when the request requires a new investigation, calculation or fresh
collection. Keep that plan proportional to the actual missing work and reuse
completed work. Do not create a research plan merely to explain an existing result.
"""


class ResearchPlanningState(AgentState):
    research_plan_job_id: Annotated[NotRequired[str], OmitFromInput]


class ResearchPlanningMiddleware(AgentMiddleware):
    state_schema = ResearchPlanningState

    def __init__(self, job_id, emit=lambda *_: None, *, allow_saved_context=False,continued_from=None):
        self.job_id = job_id
        self.emit = emit
        self.allow_saved_context = allow_saved_context
        self.continued_from=continued_from

    @staticmethod
    def _saved_context_read(call):
        if call['name'] in {'recall_memory','read_evidence','get_private_portfolio',
                            'get_research_context','list_data_sources','ls','glob','grep'}:
            return True
        if call['name']=='read_file':
            path=call.get('args',{}).get('file_path')
            return isinstance(path,str) and '..' not in PurePosixPath(path).parts
        return False

    def _accepted(self, state):
        saved=state.get('research_plan_job_id')
        return saved==self.job_id or bool(self.continued_from and saved==self.continued_from)

    @staticmethod
    def _preparation(call):
        if call["name"] in {"get_research_context", "list_data_sources"}:
            return True
        if call["name"] == "read_file":
            file_path = call.get("args", {}).get("file_path", "")
            if not isinstance(file_path, str):
                return False
            path = PurePosixPath(file_path)
            # Do not let path traversal turn skill preparation into data access.
            return ".." not in path.parts and (
                path.parts[:2] == ("/", "skills") or str(path) == "/memory/preferences.md")
        if call["name"] == "submit_research_answer":
            draft = call.get("args", {}).get("draft", {})
            return isinstance(draft, dict) and bool(draft.get("next_steps")) and not any(
                draft.get(key) for key in ("facts", "interpretations", "advice"))
        return False

    def _error(self, call, reason):
        self.emit("planning_required", {"call_id": call["id"], "name": call["name"], "reason": reason})
        return ToolMessage(name=call["name"], tool_call_id=call["id"], status="error",
                           content=reason + " Call write_todos in a separate turn and wait for success; then continue using the saved plan.")

    def _check(self, request):
        call, state = request.tool_call, request.state
        if self.allow_saved_context and self._saved_context_read(call):
            return None
        if call["name"] == "write_todos":
            todos = call.get("args", {}).get("todos")
            minimum = 1 if self._accepted(state) else 2
            if not isinstance(todos, list) or not minimum <= len(todos) <= 12:
                return self._error(call, f"Provide {minimum}–12 meaningful plan items.")
            seen = set()
            for item in todos:
                if not isinstance(item, dict) or not isinstance(item.get("content"), str) or not item["content"].strip() or not isinstance(item.get("status"), str) or item["status"] not in {"pending", "in_progress", "completed"}:
                    return self._error(call, "Each plan item needs nonblank content and a valid status.")
                content = " ".join(item["content"].split()).casefold()
                if content in seen:
                    return self._error(call, "Plan items must describe distinct work.")
                seen.add(content)
            if not self._accepted(state) and all(item['status']=='completed' for item in todos):
                return self._error(call, 'A new request cannot start with all work completed.')
        elif not self._accepted(state) and not self._preparation(call):
            return self._error(call, "No plan has been saved for this research request; the requested tool has not executed.")
        elif self._accepted(state) and not any(item.get("status") == "in_progress" for item in state.get("todos", [])) and call["name"] not in {"submit_research_answer", "publish_report","publish_saved_report","save_report_draft"} and not self._preparation(call):
            return self._error(call, "The saved plan has no active step; update it before starting further research.")
        return None

    def _saved(self, request, result):
        if request.tool_call["name"] != "write_todos":
            return result
        update = result.update if isinstance(result, Command) and isinstance(result.update, dict) else {}
        messages = update.get("messages", [])
        if update.get("todos") != request.tool_call["args"]["todos"] or not any(
            isinstance(message, ToolMessage) and message.tool_call_id == request.tool_call["id"] and message.status != "error" for message in messages
        ) or any(isinstance(message, ToolMessage) and message.status == "error" for message in messages):
            return result  # A failed/non-saving tool call cannot authorize research.
        if not any(item['status']=='in_progress' for item in update['todos']):
            todos=[dict(item) for item in update['todos']]
            for item in todos:
                if item['status']=='pending':
                    item['status']='in_progress';break
            update={**update,'todos':todos}
        self.emit("plan_updated" if self._accepted(request.state) else "plan_saved", {
            "job_id": self.job_id, "call_id": request.tool_call["id"], "todos": update["todos"]})
        return replace(result, update={**update, "research_plan_job_id": self.job_id})

    def wrap_tool_call(self, request, handler):
        blocked = self._check(request)
        return blocked if blocked is not None else self._saved(request, handler(request))

    async def awrap_tool_call(self, request, handler):
        blocked = self._check(request)
        return blocked if blocked is not None else self._saved(request, await handler(request))
