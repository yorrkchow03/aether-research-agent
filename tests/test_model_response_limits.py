from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage

from research_agent.model import create_ark_model
from research_agent.runtime import TraceMiddleware
from research_agent.settings import Settings


class ResponseLimitTests(unittest.TestCase):
    def test_truncated_well_formed_tool_call_is_retained_but_not_returned_for_execution(self):
        events = []
        middleware = TraceMiddleware(lambda kind, value: events.append((kind, value)))
        reply = AIMessage(content='', tool_calls=[{'id': 'fixture', 'name': 'publish_report', 'args': {'draft': {'summary': 'partial'}}}],
            response_metadata={'finish_reason': 'length', 'model_name': 'fixture'})
        with self.assertRaisesRegex(RuntimeError, 'tools were not executed'):
            middleware.wrap_model_call(None, lambda _: SimpleNamespace(result=[reply]))
        self.assertEqual(events[1][1]['finish_reason'], 'length')
        self.assertEqual(events[-1][0], 'model_output_truncated')
        self.assertEqual(events[-1][1]['message']['data']['tool_calls'][0]['name'], 'publish_report')

    def test_complete_tool_response_remains_available(self):
        middleware = TraceMiddleware(lambda *_: None)
        result = SimpleNamespace(result=[AIMessage(content='done', response_metadata={'finish_reason': 'stop'})])
        self.assertIs(middleware.wrap_model_call(None, lambda _: result), result)

    def test_report_output_limit_is_explicit_and_default_remains_bounded(self):
        settings = Settings(api_key='fixture-key')
        with patch('research_agent.model.ArkPlanModel') as model:
            create_ark_model(settings)
            self.assertEqual(model.call_args.kwargs['max_tokens'], 4096)
            model.call_args.kwargs['http_client'].close()
            create_ark_model(settings, max_output_tokens=8192)
            self.assertEqual(model.call_args.kwargs['max_tokens'], 8192)
            model.call_args.kwargs['http_client'].close()
            with self.assertRaises(ValueError):
                create_ark_model(settings, max_output_tokens=999999)


if __name__ == '__main__':
    unittest.main()
