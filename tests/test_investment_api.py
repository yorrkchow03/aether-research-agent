import json,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app
from research_agent.tools import ResearchTools
from unittest.mock import patch


class InvestmentAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runtime=AgentRuntime(root=self.root,model_mode='current_conversation_no_api')
        self.app=create_app(self.runtime);self.client=TestClient(self.app,base_url='http://127.0.0.1:8765');self.addCleanup(self.client.close)
        self.headers={'x-local-token':self.app.state.csrf_token}
    def test_clean_install_starts_empty_and_serves_new_assets(self):
        self.assertEqual(self.client.get('/').status_code,200)
        for name in ('dashboard.js','investment.js','investment.css'):self.assertEqual(self.client.get('/assets/'+name).status_code,200)
        self.assertTrue(self.client.get('/api/portfolio').json()['empty'])
        self.assertEqual(self.client.get('/api/investment/config').json()['jev']['configured'],False)
        self.assertFalse(self.client.get('/api/config').json()['configured'])
        rid='live-'+'c'*24;self.runtime.knowledge.register({'record_id':rid,'values':{'test':'1'},'units':{'test':'count'},'observed_at':'2025-01-01T00:00:00Z','retrieved_at':'2025-01-01T00:00:00Z'})
        self.assertEqual(self.client.get('/api/evidence/'+rid).json()['record']['values']['test'],'1')
        self.assertEqual(self.runtime.sessions(),[])
        session=self.runtime.create_session();tools=ResearchTools(Path(session['data_run']),self.runtime._session_dir(session['session_id'])/'workspace',session['cutoff'])
        self.assertEqual(tools.market()['status'],'archive_not_prepared')
    def test_atomic_import_csrf_void_restore_and_export_roundtrip(self):
        body={'text':'external_id,time,kind,asset,quantity\nf1,2025-01-01T00:00:00Z,deposit,USD,1000\n'}
        self.assertEqual(self.client.post('/api/portfolio/import',json=body).status_code,403)
        result=self.client.post('/api/portfolio/import',json=body,headers=self.headers)
        self.assertEqual(result.status_code,200);rid=result.json()['ids'][0]
        self.assertEqual(self.client.get('/api/portfolio').json()['cash_usd'],'1000')
        self.client.post('/api/portfolio/transactions/'+rid+'/void',json={'void':True},headers=self.headers).raise_for_status()
        self.assertTrue(self.client.get('/api/portfolio').json()['empty'])
        self.client.post('/api/portfolio/transactions/'+rid+'/void',json={'void':False},headers=self.headers).raise_for_status()
        csv=self.client.get('/api/portfolio/export').text
        self.assertEqual(self.client.post('/api/portfolio/import',json={'text':csv},headers=self.headers).json()['duplicates'],1)
        bad=self.client.post('/api/portfolio/transactions',json={'external_id':'b1','time':'2025-01-02T00:00:00Z','kind':'buy','asset':'ETH','quantity':'1','price_usd':'2000'},headers=self.headers)
        self.assertEqual(bad.status_code,400);self.assertEqual(len(self.client.get('/api/portfolio/transactions').json()['items']),1)
    def test_optional_tools_available_only_for_current_research(self):
        session=self.runtime.create_session();w=self.runtime._session_dir(session['session_id'])/'workspace'
        common={'run_dir':Path(session['data_run']),'workspace':w,'cutoff':session['cutoff'],'investment':self.runtime.investment}
        current=ResearchTools(**common,allow_live=True,live_data=self.runtime.live_data,knowledge=self.runtime.knowledge)
        tools={t.name:t for t in current.as_tools()};self.assertIn('get_private_portfolio',tools);self.assertNotIn('start_strategy_experiment',tools)
        self.assertEqual(current.sdk_call('strategy-status',{}),[])
        packet=json.loads(tools['get_private_portfolio'].invoke({}));self.assertEqual(packet['data_category'],'private_portfolio');self.assertEqual(packet['portfolio']['transaction_count'],0)
        historic={t.name for t in ResearchTools(**common).as_tools()};self.assertNotIn('get_private_portfolio',historic)
        bad=self.client.post('/api/strategies',json={'asset':'DOGE'},headers=self.headers);self.assertEqual(bad.status_code,422)
    def test_partial_book_keeps_full_intended_fee_and_caps_sizing(self):
        book_path=self.root/'book.json';book_path.write_text(json.dumps({'asks':[['100','2']]}))
        book={'record_id':'live-'+'b'*24,'raw_path':str(book_path),'values':{'top25_ask_depth_usd':'200'}}
        self.runtime.investment.ledger.import_rows([{'external_id':'fund','time':'2025-01-01T00:00:00Z','kind':'deposit','asset':'USD','quantity':'1000'}])
        with patch.object(self.runtime.live_data,'get',return_value={'record':book}):
            r=self.client.post('/api/portfolio/preview',json={'notional':500,'fee_bps':100,'max_asset_fraction':.25,'max_loss_usd':60},headers=self.headers)
        self.assertEqual(r.status_code,200);data=r.json();self.assertEqual(float(data['estimated_entry_fee_usd']),5)
        self.assertEqual(float(data['total_recorded_cash_needed_usd']),505);self.assertFalse(data['estimate']['fully_covered'])
        self.assertAlmostEqual(float(data['maximum_notional_under_entered_constraints_usd']),60/.31)
