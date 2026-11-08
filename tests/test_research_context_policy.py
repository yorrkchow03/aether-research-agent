"""Targeted local contracts for research capacity and chat/delegation routing."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from langchain_core.messages import HumanMessage

from research_agent.conversation_context import ConversationWindowMiddleware
from research_agent.model_context import context_policy
from research_agent.research_policy import requests_research
from research_agent.settings import Settings


class Request(SimpleNamespace):
    def override(self,**values):return Request(**{**self.__dict__,**values})


class ResearchContextPolicyTests(unittest.TestCase):
    def test_verified_million_context_uses_auto_headroom_and_all_old_summary_input(self):
        policy=context_policy(model_id='doubao-seed-2.1-pro')
        self.assertEqual(policy['capacity_tokens'],1024000)
        self.assertEqual(policy['trigger_tokens'],512000)
        self.assertEqual(policy['keep_tokens'],64000)
        self.assertIsNone(policy['summary_input_trim_tokens'])
        self.assertEqual(policy['trigger_mode'],'auto_50_percent')

    def test_explicit_context_override_is_not_capped_at_120k_and_cannot_exceed_model(self):
        policy=context_policy(model_id='doubao-seed-2.1-pro',trigger_tokens=700000)
        self.assertEqual(policy['trigger_tokens'],700000)
        self.assertEqual(policy['trigger_mode'],'explicit')
        with self.assertRaises(ValueError):context_policy(model_id='doubao-seed-2.1-pro',trigger_tokens=1024000)
        model=SimpleNamespace(profile={'max_input_tokens':256000},max_tokens=8192)
        with self.assertRaises(ValueError):context_policy(model,trigger_tokens=250000)

    def test_settings_auto_and_numeric_override_preserve_capacity_and_explicit_task_caps(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.env').write_text('AETHER_CONTEXT_TRIGGER_TOKENS=auto\nAETHER_MAX_MODEL_CALLS=72\n')
            settings=Settings.load(root)
            self.assertIsNone(settings.context_trigger_tokens)
            self.assertEqual(settings.max_model_calls,72)
            self.assertEqual(settings.public()['context_trigger_tokens'],512000)
            (root/'.env').write_text('AETHER_CONTEXT_TRIGGER_TOKENS=300000\n')
            self.assertEqual(Settings.load(root).context_trigger_tokens,300000)

    def test_initial_depth_hint_recognizes_explicit_research_without_requiring_a_report(self):
        for prompt in ('请深入研究ETH本周的网络升级和市场情况','详细分析ETH近一个月的变化，不用生成报告','帮我研究一下ETH近期的升级信息',
                       'Please investigate the conflicting ETH upgrade announcements.',
                       'Compare ETH and staking across primary sources'):
            self.assertTrue(requests_research(prompt),prompt)
        for prompt in ('最近ETH的行情怎么样','刚才报告里的波动率是什么意思？','What is research?',
                       'Explain the report you already generated.', '不要深入研究，只解释报告',
                       'Explain this phrase: "investigate ETH deeply"'):
            self.assertFalse(requests_research(prompt),prompt)

    def test_initial_chat_hint_does_not_mask_research_tools(self):
        for started in (False,True):
            middleware=ConversationWindowMiddleware(research_started=lambda:started)
            tools=[{'name':name} for name in ('write_todos','task','publish_report','publish_saved_report',
                'submit_research_answer','read_evidence','recall_memory','search_web','request_public_api','execute')]
            request=Request(tools=tools,messages=[HumanMessage(content='A short question')])
            actual=middleware.wrap_model_call(request,lambda value:value)
            self.assertEqual(actual.tools,tools)
            self.assertEqual(actual.messages,request.messages)


if __name__=='__main__':unittest.main()
