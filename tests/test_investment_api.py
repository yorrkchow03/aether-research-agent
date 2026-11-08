import hashlib,json,tempfile,unittest
from pathlib import Path
from decimal import Decimal
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
    def save_capture(self,metric,identity,observed='2025-01-02T00:00:00Z',retrieved=None,source_retrieved=None,price='100',depth='200',raw_data=None,record_asset='ETH'):
        retrieved=retrieved or observed;rid='live-'+(identity if len(identity)==24 else identity*24)
        data=raw_data if raw_data is not None else {'asks':[['100','2']]} if metric=='orderbook' else {'price':price}
        raw=json.dumps(data).encode();path=self.root/(identity+'-raw.json');path.write_bytes(raw)
        row={'record_id':rid,'dataset_id':'live.'+metric+'.eth','asset':'ETH','observed_at':observed,
             'retrieved_at':retrieved,'available_at':retrieved,'raw_path':str(path),
             'scope':'Saved single-exchange test snapshot',
             'values':{'top25_ask_depth_usd':depth} if metric=='orderbook' else {'price_usd':price},
             'source':{'sha256':hashlib.sha256(raw).hexdigest(),'retrieved_at':source_retrieved or retrieved}}
        if record_asset is None:row.pop('asset')
        else:row['asset']=record_asset
        self.runtime.knowledge.register(row)
        return self.runtime.knowledge.get(rid)
    def fund_and_hold(self,basis='100'):
        self.runtime.investment.ledger.import_rows([
            {'external_id':'fund','time':'2025-01-01T00:00:00Z','kind':'deposit','asset':'USD','quantity':'1000'},
            {'external_id':'held','time':'2025-01-02T00:00:00Z','kind':'transfer_in','asset':'ETH','quantity':'2','cost_basis_usd':basis}])
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
        self.save_capture('orderbook','b')
        self.runtime.investment.ledger.import_rows([{'external_id':'fund','time':'2025-01-01T00:00:00Z','kind':'deposit','asset':'USD','quantity':'1000'}])
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            r=self.client.post('/api/portfolio/preview',json={'notional':500,'fee_bps':100,'max_asset_fraction':.25,'max_loss_usd':60},headers=self.headers)
        fetch.assert_not_called()
        self.assertEqual(r.status_code,200);data=r.json();self.assertEqual(float(data['estimated_entry_fee_usd']),5)
        self.assertEqual(float(data['total_recorded_cash_needed_usd']),505);self.assertFalse(data['estimate']['fully_covered'])
        self.assertAlmostEqual(float(data['maximum_notional_under_entered_constraints_usd']),60/.31)
    def test_saved_ledger_read_uses_stale_mark_and_keeps_unknown_basis(self):
        self.fund_and_hold(basis=None);row=self.save_capture('price','a')
        before=self.runtime.investment.ledger.csv()
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            result=self.client.get('/api/portfolio?refresh=false')
        fetch.assert_not_called();self.assertEqual(result.status_code,200);data=result.json()
        self.assertEqual(data['equity_usd'],'1200')
        position=data['positions'][0]
        self.assertEqual(position['market_value_usd'],'200')
        self.assertIsNone(position['cost_basis_usd']);self.assertIsNone(position['unrealized_pnl_usd'])
        self.assertEqual(position['mark_source']['record_id'],row['record_id'])
        self.assertEqual(position['mark_source']['freshness'],'stale')
        self.assertGreater(position['mark_source']['age_seconds'],60)
        self.assertEqual(data['market_data']['mode'],'saved_only')
        self.assertEqual(before,self.runtime.investment.ledger.csv())
    def test_no_saved_mark_does_not_turn_position_or_equity_into_zero(self):
        self.fund_and_hold()
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            result=self.client.get('/api/portfolio')
        fetch.assert_not_called();self.assertEqual(result.status_code,200);data=result.json()
        self.assertEqual(data['cash_usd'],'1000');self.assertIsNone(data['equity_usd'])
        self.assertIsNone(data['positions'][0]['market_value_usd'])
        self.assertIsNone(data['positions'][0]['mark_source'])
        self.assertEqual(data['mark_sources']['ETH']['status'],'unavailable')
        self.assertEqual(data['mark_sources']['ETH']['freshness'],'unknown')
        self.assertTrue(data['mark_gaps'])
    def test_historical_read_excludes_future_observation_and_all_late_capture_clocks(self):
        self.fund_and_hold();early=self.save_capture('price','a')
        self.save_capture('price','b',observed='2025-01-02T12:00:00Z',retrieved='2025-01-04T00:00:00Z',price='200')
        self.save_capture('price','c',observed='2025-01-02T18:00:00Z',source_retrieved='2025-01-04T00:00:00Z',price='300')
        self.save_capture('price','d',observed='2025-01-05T00:00:00Z',retrieved='2025-01-02T19:00:00Z',price='400')
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            response=self.client.get('/api/portfolio',params={'as_of':'2025-01-03T00:00:00Z'})
        fetch.assert_not_called();self.assertEqual(response.status_code,200)
        data=response.json();self.assertEqual(data['positions'][0]['mark_source']['record_id'],early['record_id'])
        self.assertEqual(data['equity_usd'],'1200');self.assertEqual(data['as_of'],'2025-01-03T00:00:00Z')
        before_entries=self.client.get('/api/portfolio',params={'as_of':'2024-12-31T00:00:00Z'}).json()
        self.assertTrue(before_entries['empty']);self.assertEqual(before_entries['transaction_count'],0)
        self.assertEqual(before_entries['recorded_transaction_count'],2)
        self.assertEqual(before_entries['mark_sources'],{})
        funding_only=self.client.get('/api/portfolio',params={'as_of':'2025-01-01T12:00:00Z'}).json()
        self.assertEqual(funding_only['transaction_count'],1);self.assertEqual(funding_only['positions'],[])
        self.assertEqual(funding_only['equity_usd'],'1000')
    def test_corrupt_latest_capture_falls_back_to_verified_older_mark_without_fetch(self):
        self.fund_and_hold();early=self.save_capture('price','a')
        newest=self.save_capture('price','b',observed='2025-01-02T01:00:00Z',price='200')
        Path(newest['raw_path']).write_text('corrupt raw response')
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            response=self.client.get('/api/portfolio')
        fetch.assert_not_called();self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['positions'][0]['mark_source']['record_id'],early['record_id'])
    def test_unverified_raw_fields_cannot_be_used_as_saved_marks(self):
        self.fund_and_hold();early=self.save_capture('price','a')
        for i,missing in enumerate(('both','raw_path','sha256')):
            rid='live-'+f'{100+i:024x}';capture='2025-01-03T00:00:00Z'
            row={'record_id':rid,'dataset_id':'live.price.eth','asset':'ETH',
                 'observed_at':capture,'retrieved_at':capture,'available_at':capture,
                 'values':{'price_usd':'999'}}
            if missing=='raw_path':row['source']={'sha256':'f'*64}
            if missing=='sha256':
                path=self.root/'unverified-price.json';path.write_text('{"price":"999"}')
                row['raw_path']=str(path);row['source']={}
            self.runtime.knowledge.register(row)
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            response=self.client.get('/api/portfolio')
        fetch.assert_not_called();self.assertEqual(response.status_code,200);data=response.json()
        self.assertEqual(data['positions'][0]['mark_source']['record_id'],early['record_id'])
        self.assertEqual(data['positions'][0]['market_value_usd'],'200')
        self.assertEqual(data['mark_sources']['ETH']['capture_search']['skipped_captures'],3)
    def test_mark_requires_exact_asset_and_raw_normalized_price_agreement(self):
        self.fund_and_hold();early=self.save_capture('price','a')
        self.save_capture('price','b',observed='2025-01-03T00:00:00Z',price='999',record_asset=None)
        self.save_capture('price','c',observed='2025-01-04T00:00:00Z',price='999',record_asset='BTC')
        self.save_capture('price','d',observed='2025-01-05T00:00:00Z',price='999',raw_data={'price':'100'})
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            response=self.client.get('/api/portfolio')
        fetch.assert_not_called();self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['positions'][0]['mark_source']['record_id'],early['record_id'])
    def test_unusable_latest_saved_book_falls_back_to_older_usable_snapshot(self):
        self.runtime.investment.ledger.import_rows([{'external_id':'fund','time':'2025-01-01T00:00:00Z','kind':'deposit','asset':'USD','quantity':'1000'}])
        early=self.save_capture('orderbook','b')
        bad_books=[{'asks':[]},{'asks':[['0','2']]},{'asks':[['100','0']]},
                   {'asks':[['100','-1']]},{'asks':[['100','2']],'auction_mode':True},
                   {'asks':[['100','2'],['99','2']]}]
        for i,data in enumerate(bad_books):
            self.save_capture('orderbook',f'{200+i:024x}',observed='2025-01-03T00:00:00Z',raw_data=data)
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            response=self.client.post('/api/portfolio/preview',json={'notional':100},headers=self.headers)
        fetch.assert_not_called();self.assertEqual(response.status_code,200);data=response.json()
        self.assertEqual(data['estimate']['status'],'saved')
        self.assertEqual(data['estimate']['source_record_id'],early['record_id'])
        self.assertEqual(Decimal(data['estimate']['asset_quantity']),Decimal('1'))
        self.assertEqual(data['orderbook_source']['capture_search']['skipped_captures'],len(bad_books))
    def test_paged_book_search_finds_usable_capture_beyond_32_invalid_newer_captures(self):
        self.runtime.investment.ledger.import_rows([{'external_id':'fund','time':'2025-01-01T00:00:00Z','kind':'deposit','asset':'USD','quantity':'1000'}])
        early=self.save_capture('orderbook','b')
        for i in range(40):
            self.save_capture('orderbook',f'{300+i:024x}',observed='2025-01-03T00:00:00Z',raw_data={'asks':[]})
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            response=self.client.post('/api/portfolio/preview',json={'notional':100},headers=self.headers)
        fetch.assert_not_called();self.assertEqual(response.status_code,200);data=response.json()
        self.assertEqual(data['estimate']['source_record_id'],early['record_id'])
        self.assertEqual(data['orderbook_source']['capture_search']['candidates_examined'],41)
        self.assertEqual(data['orderbook_source']['capture_search']['skipped_captures'],40)
        self.assertEqual(data['orderbook_source']['capture_search']['page_size'],32)
    def test_missing_book_preserves_unknown_fill_but_computes_scenario_arithmetic(self):
        self.runtime.investment.ledger.import_rows([{'external_id':'fund','time':'2025-01-01T00:00:00Z','kind':'deposit','asset':'USD','quantity':'1000'}])
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            response=self.client.post('/api/portfolio/preview',json={'notional':100,'fee_bps':100},headers=self.headers)
        fetch.assert_not_called();self.assertEqual(response.status_code,200);data=response.json()
        self.assertEqual(Decimal(data['estimated_entry_fee_usd']),Decimal('1'))
        self.assertEqual(Decimal(data['total_recorded_cash_needed_usd']),Decimal('101'))
        self.assertEqual(Decimal(data['hypothetical_stress_loss_usd']),Decimal('31'))
        self.assertIsNone(data['estimate']['fully_covered']);self.assertIsNone(data['estimate']['asset_quantity'])
        self.assertIsNone(data['maximum_notional_under_entered_constraints_usd'])
        self.assertEqual(data['orderbook_source']['status'],'unavailable')
        self.assertTrue(any('unknown' in text for text in data['constraints']))
    def test_refresh_requires_local_token_and_historical_reads_cannot_refresh(self):
        self.fund_and_hold()
        with patch.object(self.runtime.live_data,'get') as fetch:
            self.assertEqual(self.client.get('/api/portfolio?refresh=true').status_code,403)
            response=self.client.get('/api/portfolio',params={'refresh':'true','as_of':'2025-01-03T00:00:00Z'},headers=self.headers)
            self.assertEqual(response.status_code,400)
            for cutoff in ('','2025-01-03T00:00:00','2999-01-01T00:00:00Z'):
                self.assertEqual(self.client.get('/api/portfolio',params={'as_of':cutoff}).status_code,400)
        fetch.assert_not_called()
    def test_explicit_refresh_can_use_new_capture_acquired_after_request_start(self):
        self.fund_and_hold()
        def capture(metric,asset):
            self.assertEqual((metric,asset),('price','ETH'))
            return {'record':self.save_capture('price','a',observed='2026-01-01T00:00:01Z')}
        with patch.object(self.runtime.live_data,'get',side_effect=capture) as fetch, patch('research_agent.investment.service.utcnow',side_effect=['2026-01-01T00:00:00Z','2026-01-01T00:00:02Z','2026-01-01T00:00:03Z']):
            response=self.client.get('/api/portfolio?refresh=true',headers=self.headers)
        self.assertEqual(response.status_code,200);fetch.assert_called_once_with('price','ETH')
        self.assertEqual(response.json()['equity_usd'],'1200')
        self.assertEqual(response.json()['positions'][0]['mark_source']['freshness'],'fresh')
        self.assertEqual(response.json()['market_data']['mode'],'refresh_requested')
    def test_failed_requested_refresh_retains_saved_value_with_disclosed_failure(self):
        self.fund_and_hold();self.save_capture('price','a')
        with patch.object(self.runtime.live_data,'get',side_effect=RuntimeError('provider unavailable')):
            response=self.client.get('/api/portfolio?refresh=true',headers=self.headers)
        self.assertEqual(response.status_code,200);data=response.json()
        self.assertEqual(data['equity_usd'],'1200')
        self.assertEqual(data['mark_sources']['ETH']['refresh_error'],'provider unavailable')
        self.assertTrue(any('refresh failed' in text for text in data['warnings']))
    def test_preview_saved_stale_marks_are_disclosed_and_explicit_book_refresh_is_opt_in(self):
        self.fund_and_hold();self.save_capture('price','a');self.save_capture('orderbook','b')
        with patch.object(self.runtime.live_data,'get',side_effect=AssertionError('Unexpected external collection')) as fetch:
            saved=self.client.post('/api/portfolio/preview',json={'notional':100},headers=self.headers)
        fetch.assert_not_called();self.assertEqual(saved.status_code,200)
        self.assertTrue(any('Concentration uses stale' in text for text in saved.json()['constraints']))
        self.assertEqual(saved.json()['estimate']['status'],'saved')
        with patch.object(self.runtime.live_data,'get',side_effect=RuntimeError('offline provider')) as fetch:
            refreshed=self.client.post('/api/portfolio/preview',json={'notional':100,'refresh':True},headers=self.headers)
        self.assertEqual(refreshed.status_code,200);self.assertEqual(fetch.call_count,2)
        self.assertEqual(refreshed.json()['market_data']['mode'],'refresh_requested')
        self.assertEqual(refreshed.json()['orderbook_source']['refresh_error'],'offline provider')
        self.assertTrue(any('mark refresh failed' in text for text in refreshed.json()['constraints']))
