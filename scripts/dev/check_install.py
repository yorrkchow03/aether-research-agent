"""Offline Deep Agents import, graph and tool-call check; no API keys needed."""
import json
import platform
import subprocess
from importlib.metadata import version
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

ROOT = Path(__file__).resolve().parents[2]


class OfflineProbeModel(BaseChatModel):
    """Deterministic local stub for verifying harness wiring, not model quality."""

    @property
    def _llm_type(self) -> str:
        return "offline-installation-probe"

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if any(isinstance(m, ToolMessage) for m in messages):
            message = AIMessage(content="offline-ok")
        else:
            message = AIMessage(content="", tool_calls=[{
                "name": "installation_probe", "args": {}, "id": "probe-call"
            }])
        return ChatResult(generations=[ChatGeneration(message=message)])


def installation_probe() -> str:
    """Return a fixed marker to verify local tool execution."""
    return "tool-ok"


def main():
    agent = create_deep_agent(
        model=OfflineProbeModel(),
        tools=[installation_probe],
        checkpointer=InMemorySaver(),
        system_prompt="Run the installation probe and return its result.",
    )
    config = {"configurable": {"thread_id": "installation-check"}, "recursion_limit": 10}
    result = agent.invoke({"messages": [{"role": "user", "content": "Check installation"}]}, config=config)
    assert result["messages"][-1].content == "offline-ok", "Agent loop failed"
    assert any(isinstance(m, ToolMessage) and m.content == "tool-ok" for m in result["messages"]), "Tool execution failed"
    assert agent.get_state(config).values["messages"], "Checkpoint check failed"
    receipt = {
        "project_root": str(ROOT),
        "python": platform.python_version(),
        "packages": {p: version(p) for p in ("deepagents", "langgraph", "langchain", "langchain-openai", "python-dotenv")},
        "source_repository": "https://github.com/langchain-ai/deepagents",
        "source_tag": "deepagents==0.7.23",
        "installation_source": "Pinned PyPI distribution; optional upstream checkout is not required",
        "offline_import_graph_tool_checkpoint_check": "passed",
        "live_model_check": "not_run",
        "business_quality_check": "not_part_of_this_offline_wiring_probe",
    }
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs/installation-check.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
