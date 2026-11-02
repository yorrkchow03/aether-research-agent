"""Exercise library API/SDK/evidence routes against the existing analyst service."""
import tempfile,unittest,json,threading
from pathlib import Path
from fixtures import project_fixture
from fastapi.testclient import TestClient
from research_data.library import ResearchLibrary
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app
from research_agent.tools import ResearchTools
from research_agent.terminal import ResearchShell
from research_agent.settings import Settings

class LibraryIntegrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=project_fixture(Path(self.tmp.name)/'project')
  self.lib=ResearchLibrary(self.root/'data/library')
  self.lib.register_dataset('market.fixture',provider='fixture',frequency='1d',scope='synthetic integration input',asset='ETH',units={'count':'count'})
  cap=self.lib.capture('fixture','https://example.com/input',b'{"count":7}',retrieved_at='2026-10-26T12:00:00Z')
  self.rid=self.lib.put('market.fixture','2026-10-01',{'count':7},cap)
  self.page=self.lib.put_document('fixture','https://example.com/news','Ethereum source','Ethereum upgrade is planned. It has not been confirmed.',cap,published_at='2026-10-01',assets=['ETH'])
  self.runtime=AgentRuntime(self.root)
  meta=self.runtime.create_session();workspace=self.runtime._session_dir(meta['session_id'])/'workspace'
  self.service=ResearchTools(Path(meta['data_run']),workspace,meta['cutoff'],allow_live=True,
       knowledge=self.runtime.knowledge,library=self.runtime.library,live_data=self.runtime.live_data,
       shell=ResearchShell(workspace,Settings(),threading.Event()))

 def test_real_api_queries_and_source_resolution(self):
  client=TestClient(create_app(self.runtime),base_url='http://127.0.0.1:8765')
  self.assertEqual(client.get('/api/library/datasets').status_code,200)
  response=client.get('/api/library/series',params={'dataset_id':'market.fixture'}).json()
  self.assertEqual(response['total'],1)
  self.assertEqual(response['records'][0]['record_id'],self.rid)
  self.assertEqual(client.get('/api/library/documents',params={'query':'Ethereum'}).json()['total'],1)
  evidence=client.get('/api/evidence/'+self.rid).json()
  self.assertTrue(evidence['raw_verified']);self.assertEqual(evidence['record']['values']['count'],'7')
  self.assertEqual(client.get('/api/library/series',params={'dataset_id':'market.fixture','mode':'strict'}).json()['total'],0)

 def test_sdk_lookup_and_reproducible_terminal_calculation(self):
  packet=self.service.sdk_call('library-series',{'dataset_id':'market.fixture'})
  self.assertEqual(packet['records'][0]['record_id'],self.rid)
  row=self.service.lookup(self.rid)
  self.assertEqual(row['values']['count'],'7')
  self.assertEqual(self.runtime.knowledge.get(self.rid)['values']['count'],'7')
  self.assertFalse(self.runtime.knowledge.contains(self.rid))  # already retained in library, no duplicate memory row
  code=self.service.workspace/'notes/double.py'
  code.write_text('import json,sys\nr=json.load(open(sys.argv[1]))\nprint(json.dumps({"values":{"double":int(r["values"]["count"])*2},"units":{"double":"count"}}))\n')
  result=self.service.register_calculation('/notes/double.py',[self.rid],'Synthetic count doubled')
  self.assertEqual(result['values']['double'],'14')
  self.assertEqual(self.service.lookup(result['record_id'])['input_record_ids'],[self.rid])

 def test_sdk_cannot_override_strict_host_cutoff(self):
  self.service.mode='strict'
  packet=self.service.sdk_call('library-series',{'dataset_id':'market.fixture','mode':'observed','known_by':'2030-01-01'})
  self.assertEqual(packet['mode'],'strict');self.assertEqual(packet['total'],0)

 def test_current_library_query_does_not_inherit_old_archive_cutoff(self):
  self.service.cutoff='2026-09-01T00:00:00Z'
  self.assertEqual(self.service.sdk_call('library-series',{'dataset_id':'market.fixture'})['total'],1)
  self.service.allow_live=False
  with self.assertRaisesRegex(ValueError,'requires current research mode'):
   self.service.sdk_call('library-series',{'dataset_id':'market.fixture'})

 def test_existing_memory_selects_library_without_future_leakage(self):
  later=self.runtime.knowledge.select_news('Ethereum',cutoff='2026-10-27',budget=5000)
  self.assertIn(self.page,[r['record_id'] for r in later['results']])
  earlier=self.runtime.knowledge.select_news('Ethereum',cutoff='2026-10-25',budget=5000)
  self.assertNotIn(self.page,[r['record_id'] for r in earlier['results']])

 def test_library_saved_research_is_recalled_and_has_separate_claim_links(self):
  body={'draft':{'facts':[{'statement':'Synthetic ETH source fact','evidence_ids':[self.rid]}]},'created_at':'2026-10-01T12:00:00Z'}
  with self.lib.connect() as c:
   c.execute('INSERT INTO research_outputs VALUES(?,?,?,?)',('answer-fixture','answers',body['created_at'],json.dumps(body)))
   c.execute('INSERT INTO output_fts VALUES(?,?)',('answer-fixture','Synthetic ETH source fact'))
   c.execute('INSERT INTO output_claims VALUES(?,?,?)',('answer-fixture','/facts/0',json.dumps(body['draft']['facts'][0])))
   c.execute('INSERT INTO claim_citations VALUES(?,?,?)',('answer-fixture','/facts/0',self.rid))
  recalled=self.runtime.knowledge.search_research('ETH')
  self.assertEqual(recalled[0]['answer_id'],'answer-fixture')
  original=self.lib.output('answer-fixture');self.assertEqual(original['claim_citations'][0]['record_id'],self.rid)
  client=TestClient(create_app(self.runtime),base_url='http://127.0.0.1:8765')
  self.assertEqual(client.get('/api/library/research',params={'query':'ETH'}).json()['total'],1)
  self.assertEqual(client.get('/api/library/research/answer-fixture').json()['claim_citations'][0]['record_id'],self.rid)

 def test_quarantined_source_stays_readable_for_audit_but_cannot_enter_new_research(self):
  with self.lib.connect() as c:c.execute('INSERT INTO relations VALUES(?,?,?,?)',(self.rid,'excluded_from_research','date_parse_error','{}'))
  self.assertTrue(self.lib.evidence(self.rid)['library_exclusion_reasons'])
  with self.assertRaisesRegex(ValueError,'quarantined'):self.service.lookup(self.rid)

 def test_relationship_api_respects_acquisition_cutoff(self):
  from research_data.library.relations import link_documents
  cap=self.lib.capture('fixture','https://example.com/news',b'New source',retrieved_at='2026-10-27T12:00:00Z')
  changed=self.lib.put_document('fixture','https://example.com/news','Ethereum source','Changed Ethereum source.',cap,published_at='2026-10-01',assets=['ETH'])
  link_documents(self.lib)
  client=TestClient(create_app(self.runtime),base_url='http://127.0.0.1:8765')
  current=client.get('/api/library/relationships',params={'record_id':changed}).json()
  self.assertEqual(current['total'],1);self.assertEqual(current['results'][0]['object'],self.page)
  early=client.get('/api/library/relationships',params={'record_id':changed,'known_by':'2026-10-27T00:00:00Z'}).json()
  self.assertEqual(early['total'],0)

 def test_strict_memory_sdk_and_tool_cannot_expand_the_host_time_boundary(self):
  self.service.mode='strict';self.service.cutoff='2026-10-26T00:00:00Z'
  packet=self.service.sdk_call('memory-news',{'query':'Ethereum','cutoff':'2030-01-01','budget':5000})
  self.assertNotIn(self.page,[r['record_id'] for r in packet['results']])
  self.assertNotIn(self.page,[r['record_id'] for r in self.service.sdk_call('memory-news',{'query':'Ethereum'})['results']])
  tool=next(t for t in self.service.as_tools() if t.name=='recall_memory')
  packet=json.loads(tool.invoke({'kind':'news','query':'Ethereum','as_of':'2030-01-01'}))
  self.assertNotIn(self.page,[r['record_id'] for r in packet['results']])

 def test_strict_report_recall_is_unavailable_without_acquisition_history(self):
  self.service.mode='strict'
  self.assertEqual(self.service.sdk_call('memory-research',{'query':'ETH'})['results'],[])
  tool=next(t for t in self.service.as_tools() if t.name=='recall_memory')
  self.assertEqual(json.loads(tool.invoke({'kind':'research','query':'ETH'}))['results'],[])

if __name__=='__main__':unittest.main()
