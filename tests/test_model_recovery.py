import unittest
from dataclasses import dataclass,replace
from types import SimpleNamespace
from langchain_core.messages import AIMessage,HumanMessage
from research_agent.runtime import TraceMiddleware
from research_agent.settings import Settings

@dataclass
class Request:
 messages:list
 def override(self,**kwargs):return replace(self,**kwargs)

class ModelRecoveryTests(unittest.TestCase):
 def bad(self):return SimpleNamespace(result=[AIMessage(content='',invalid_tool_calls=[{'name':'publish_report','args':'{bad','id':'call-test','error':'Invalid JSON'}])])
 def test_invalid_provider_json_is_reemitted_by_model_never_executed(self):
  events=[];seen=[];trace=TraceMiddleware(lambda k,d:events.append((k,d)),settings=Settings())
  good=SimpleNamespace(result=[AIMessage(content='fixed')])
  def handler(request):
   seen.append(request)
   return self.bad() if len(seen)==1 else good
  self.assertIs(trace.wrap_model_call(Request([HumanMessage(content='research')]),handler),good)
  self.assertEqual(trace.model_calls,2);self.assertIn('没有执行',seen[1].messages[-1].content)
  self.assertTrue(any(k=='model_format_error' for k,d in events))
 def test_repeated_invalid_calls_fail_without_false_completion(self):
  trace=TraceMiddleware(lambda *args:None,settings=Settings())
  with self.assertRaisesRegex(RuntimeError,'连续返回无效工具参数'):
   trace.wrap_model_call(Request([]),lambda r:self.bad())
  self.assertEqual(trace.model_calls,3)

if __name__=='__main__':unittest.main()
