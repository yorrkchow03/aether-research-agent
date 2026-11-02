"""Offline tutorial using the real Deep Agents loop and Aether's planning gate.

Run from the repository after installing it: python examples/framework/minimal_agent.py
The model and article are synthetic. No provider key, network, shell or publication.
Production wiring remains in research_agent.runtime; this is a teaching example.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from deepagents import (
    GeneralPurposeSubagentProfile, HarnessProfile, create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from research_agent.planning import (
    PLAN_TOOL_DESCRIPTION, PLANNING_INSTRUCTIONS, ResearchPlanningMiddleware,
)
from research_agent.skill_library import install_skills


class DemoModel(BaseChatModel):
    """Scripted responses test framework wiring; they are not model judgments."""

    model_name: str = 'offline'
    responses: list[AIMessage]
    cursor: int = 0

    @property
    def _llm_type(self):
        return 'aether_demo'

    def _get_ls_params(self, **kwargs):
        return {'ls_provider': 'aether_demo', 'ls_model_name': self.model_name,
                'ls_model_type': 'chat'}

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.cursor >= len(self.responses):
            raise RuntimeError('Offline demonstration exhausted its scripted messages')
        message = self.responses[self.cursor]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_call(name, arguments, identifier):
    return AIMessage(content='', tool_calls=[
        {'name': name, 'args': arguments, 'id': identifier},
    ])


def run_demo():
    register_harness_profile('aether_demo:offline', HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({'task', 'execute', 'delete'})))
    events, reads = [], []
    plan = [
        {'content': 'Scope: inspect the synthetic announcement and its event status',
         'status': 'in_progress'},
        {'content': 'Check that planned does not mean completed; finish the demo',
         'status': 'pending'},
    ]
    with TemporaryDirectory(prefix='aether-framework-') as directory:
        workspace = Path(directory)
        install_skills(workspace)
        (workspace / 'memory').mkdir()
        (workspace / 'memory/preferences.md').write_text(
            'Synthetic demonstration only. No real holdings or preferences.\n')

        @tool
        def read_demo_record() -> str:
            """Read a synthetic teaching record, never actual market evidence."""
            reads.append('read')
            return json.dumps({'record_id': 'synthetic-demo', 'synthetic': True,
                               'text': 'The demo upgrade is planned, not confirmed.'})

        model = DemoModel(responses=[
            tool_call('read_demo_record', {}, 'before-plan'),
            tool_call('read_file', {'file_path': '/skills/research/SKILL.md'}, 'skill'),
            tool_call('write_todos', {'todos': plan}, 'plan'),
            tool_call('read_demo_record', {}, 'after-plan'),
            tool_call('write_todos', {'todos': [dict(p, status='completed') for p in plan]}, 'done'),
            AIMessage(content='Offline demonstration complete; no real research was performed.'),
        ])
        saver = InMemorySaver()
        graph = create_deep_agent(
            model=model, tools=[read_demo_record],
            system_prompt='Aether framework tutorial. All inputs are synthetic. ' + PLANNING_INSTRUCTIONS,
            backend=FilesystemBackend(root_dir=workspace, virtual_mode=True),
            memory=['/memory/preferences.md'], skills=['/skills/'],
            middleware=[
                TodoListMiddleware(system_prompt=PLANNING_INSTRUCTIONS,
                                   tool_description=PLAN_TOOL_DESCRIPTION),
                ResearchPlanningMiddleware('offline-demo-job',
                    lambda kind, data: events.append((kind, data))),
            ], checkpointer=saver,
        )
        config = {'configurable': {'thread_id': 'offline-demo-session'}, 'recursion_limit': 30}
        state = graph.invoke({'messages': [HumanMessage(content='Inspect the synthetic example')]}, config)
        results = {m.tool_call_id: m for m in state['messages'] if isinstance(m, ToolMessage)}
        assert results['before-plan'].status == 'error'
        assert results['after-plan'].status == 'success' and reads == ['read']
        assert results['skill'].status == 'success'
        saved = graph.get_state(config).values
        assert saved['research_plan_job_id'] == 'offline-demo-job'
        assert all(p['status'] == 'completed' for p in saved['todos'])
        return {
            'status': 'passed', 'synthetic': True,
            'framework': 'Deep Agents / LangGraph',
            'model': 'Deterministic offline fixture, not Doubao',
            'blocked_before_plan': True, 'successful_demo_reads': len(reads),
            'skill_read': True, 'checkpoint_saved': True,
            'skills': sorted(p.parent.name for p in (workspace / 'skills').glob('*/SKILL.md')),
            'events': [kind for kind, _ in events],
            'external_model_calls': 0, 'published_reports': 0,
        }


if __name__ == '__main__':
    print(json.dumps(run_demo(), indent=2))
