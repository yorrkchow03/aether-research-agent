"""Investment application services; computation and private state stay on this host."""
from __future__ import annotations
import hashlib,json,threading,uuid
from pathlib import Path
from decimal import Decimal
from research_data.http import utcnow,dumps
from research_agent.live import atomic
from research_agent.metrics import buy_cost
from .ledger import Ledger,dec
from .jev import JevProvider,JevSettings
from .history import fetch_history
from .strategies import StrategyConfig,run_experiment


def strategy_values(result):
    values={'latest_up_probability':str(result['latest']['probability_up']),'holdout_brier_score':str(result['holdout_scores']['brier_score']),'holdout_directional_accuracy':str(result['holdout_scores']['directional_accuracy']),'holdout_observations':str(result['holdout_scores']['samples'])}
    units={k:'count' if k=='holdout_observations' else 'fraction' if k!='holdout_brier_score' else 'score' for k in values}
    for row in result['comparison']['results']:
        for key in ('net_return_fraction','max_drawdown_fraction','fees_usd','slippage_usd','final_equity_usd'):
            name=row['strategy']+'_'+key;values[name]=str(row[key]);units[name]='USD' if key.endswith('usd') else 'fraction'
    return {'values':values,'units':units}


def brief(result):
    return {k:v for k,v in result.items() if k not in ('folds','decisions','latest','comparison')}|{'latest':{k:v for k,v in result['latest'].items() if k!='model'},'comparison':{'results':[{k:v for k,v in row.items() if k not in ('curve','trades')} for row in result['comparison']['results']]},'decision_count':len(result['decisions'])}


class InvestmentService:
    def __init__(self,root,state_dir,live,knowledge):
        self.root=Path(root);self.state=Path(state_dir)/'investment';self.state.mkdir(parents=True,exist_ok=True)
        self.live=live;self.knowledge=knowledge;self.ledger=Ledger(self.state/'portfolio');self.jev=JevProvider(JevSettings.load(root),self.state/'jev');self.lock=threading.RLock();self.worker=None
        for p in (self.state/'strategies').glob('*/status.json'):
            status=json.loads(p.read_text())
            if status['state'] in ('running','queued'):
                status.update(state='interrupted',error='Local process stopped; saved inputs and intermediate state retained',finished_at=utcnow());atomic(p,status)
    def portfolio(self,as_of=None):
        initial=self.ledger.snapshot(as_of=as_of);marks={};gaps=[]
        for p in initial['positions']:
            if dec(p['quantity'])==0:continue
            try:
                row=self.live.get('price',p['asset'])['record'];marks[p['asset']]={'price_usd':row['values']['price_usd'],'observed_at':row['observed_at'],'retrieved_at':row['retrieved_at'],'record_id':row['record_id'],'scope':row['scope']}
            except Exception as exc:gaps.append({'asset':p['asset'],'reason':str(exc)[:200]})
        result=self.ledger.snapshot(marks,as_of);result['mark_gaps']=gaps
        result['warnings']=['Manual records may omit external holdings/cash. Missing values are not zero.','Ledger dates are economic dates under the current corrected ledger, not a historical-knowledge reconstruction.']
        return result
    def portfolio_evidence(self):
        snapshot=self.portfolio();now=utcnow();body=dumps(snapshot,sort_keys=True).encode();digest=hashlib.sha256(body).hexdigest();rid='live-'+hashlib.sha256((digest+now).encode()).hexdigest()[:24]
        raw=self.live.root/'raw'/(digest+'.bin');raw.write_bytes(body)
        values={k:v for k,v in snapshot.items() if k in ('cash_usd','net_cash_contributions_usd','fees_usd','equity_usd','realized_pnl_usd','unrealized_pnl_usd') and v is not None}
        for position in snapshot['positions']:
            for key in ('quantity','cost_basis_usd','market_value_usd','realized_pnl_usd','unrealized_pnl_usd'):
                if position[key] is not None:values[position['asset'].lower()+'_'+key]=position[key]
        record={'record_id':rid,'dataset_id':'local.portfolio.snapshot','asset':'PORTFOLIO','data_category':'private_portfolio','source_kind':'private_ledger_snapshot','observed_at':now,'retrieved_at':now,'available_at':now,'values':values,'units':{k:k.split('_')[0].upper() if k.endswith('_quantity') else 'USD' for k in values},'raw_path':str(raw),'source':{'source_id':rid,'url':'local://private-portfolio','name':'Private manually recorded portfolio','sha256':digest,'retrieved_at':now},'portfolio':snapshot,'input_record_ids':[p['mark_source']['record_id'] for p in snapshot['positions'] if p.get('mark_source')],'quality_flags':['private_user_data','manual_records_not_exchange_verified','missing_basis_is_unknown'],'scope':'User-entered private ledger; not a verified exchange account or tax basis. Exporting a report citing this record includes this private summary.'}
        self.knowledge.register(record);return self.knowledge.get(rid)
    def start_strategy(self,config):
        cfg=StrategyConfig.model_validate(config)
        with self.lock:
            if self.worker and self.worker.is_alive():raise ValueError('A strategy experiment is already running; wait for its saved result')
            rid=uuid.uuid4().hex;directory=self.state/'strategies'/rid;directory.mkdir(parents=True)
            status={'run_id':rid,'state':'queued','config':cfg.model_dump(),'started_at':utcnow()};atomic(directory/'status.json',status)
            self.worker=threading.Thread(target=self._strategy,args=(directory,cfg,status),daemon=True);self.worker.start()
        return status
    def _strategy(self,directory,cfg,status):
        try:
            status.update(state='running',stage='Capturing completed OHLCV');atomic(directory/'status.json',status)
            history=fetch_history(self.live,self.knowledge,cfg.asset,cfg.history_days);atomic(directory/'input.json',history)
            status.update(stage='Training and chronological validation',source_record_id=history['record_id']);atomic(directory/'status.json',status)
            result=run_experiment(history,cfg);result.update(run_id=status['run_id'],created_at=utcnow())
            numeric=strategy_values(result);now=utcnow();rid='live-'+hashlib.sha256(dumps({'run':status['run_id'],'values':numeric},sort_keys=True).encode()).hexdigest()[:24]
            recipe=self.live.root/'recipes'/(rid+'.py');recipe.parent.mkdir(parents=True,exist_ok=True)
            recipe.write_text('import json,sys\nfrom research_agent.investment.strategies import run_experiment\nfrom research_agent.investment.service import strategy_values\n'+f'r=run_experiment(json.load(open(sys.argv[1])),{cfg.model_dump()!r})\nprint(json.dumps(strategy_values(r)))\n')
            record={'record_id':rid,'dataset_id':'live.strategy.experiment','asset':cfg.asset,'observed_at':history['observed_at'],'retrieved_at':now,'available_at':now,**numeric,'input_record_ids':[history['record_id']],'recipe_path':str(recipe),'recipe_sha256':hashlib.sha256(recipe.read_bytes()).hexdigest(),'calculation_version':result['version'],'calculation_meta':{'config':cfg.model_dump(),'model_selected':result['model_selected'],'split':result['split'],'latest_decision_at':result['latest']['decision_at'],'execute_not_before':result['latest']['execute_not_before'],'environment':result['environment'],'limitations':result['limitations']},'method':'Fit predeclared logistic/tree candidates on earlier data; select by validation Brier; expanding holdout refits with strictly earlier available labels, delayed next-open execution, explicit costs and common-window baselines','quality_flags':['model_forecast_not_future_fact','retrospective_research','not_independent_test_after_repeated_experiments'],'scope':'Trained local research model and saved retrospective performance. Probabilities/thresholds are not verified price forecasts or instructions to trade.'}
            self.knowledge.register(record);result['evidence_record_id']=rid;atomic(directory/'result.json',result);atomic(directory/'model.json',result['latest']['model'])
            atomic(directory/'artifact_manifest.json',{'files':{name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ('input.json','result.json','model.json')},'environment':result['environment'],'config':cfg.model_dump(),'source_record_id':history['record_id'],'scope':'Local experiment artifacts; no pickle or executable uploaded model is loaded'})
            status.update(state='completed',stage='Completed',finished_at=utcnow(),evidence_record_id=rid);atomic(directory/'status.json',status)
        except Exception as exc:
            status.update(state='failed',error=str(exc)[:800],finished_at=utcnow());atomic(directory/'status.json',status)
    def strategy(self,rid,details=False):
        if len(rid)!=32 or any(c not in '0123456789abcdef' for c in rid):raise ValueError('Invalid strategy run ID')
        directory=self.state/'strategies'/rid;status=json.loads((directory/'status.json').read_text());path=directory/'result.json'
        if path.exists():
            result=json.loads(path.read_text());status['result']=result if details else brief(result)
        return status
    def strategies(self):
        paths=sorted((self.state/'strategies').glob('*/status.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        return [json.loads(p.read_text()) for p in paths[:30]]
    def strategy_report(self,rid):
        from .report import render_experiment
        packet=self.strategy(rid,True)
        if packet['state']!='completed':raise ValueError('A completed experiment is required')
        result=packet['result'];history=self.knowledge.get(result['source_record_id'])
        return render_experiment(result,[self.knowledge.get(i) for i in history['input_record_ids']])
    def assess_source(self,task,claim,record_id,asset='ETH'):
        if not record_id.startswith(('page-','web-')):raise ValueError('Select saved public publisher material, not portfolio/context data')
        source=self.knowledge.get(record_id)
        return self.jev.evaluate(task,claim,source,asset)
    def assessments(self):
        paths=sorted((self.state/'jev').glob('jev-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        return [{k:v for k,v in json.loads(p.read_text()).items() if k not in ('request','response')} for p in paths[:30]]
    def preview_buy(self,asset,notional,fee_bps=10,max_asset_fraction=.5,stress_decline_fraction=.3,max_loss_usd=None):
        if asset not in ('ETH','BTC'):raise ValueError('Supported assets: ETH/BTC')
        amount=dec(notional);fee=dec(fee_bps)
        if amount<=0 or not 0<=fee<=500 or not 0<max_asset_fraction<=1 or not 0<stress_decline_fraction<=1:raise ValueError('Invalid preview assumptions')
        if max_loss_usd is not None and dec(max_loss_usd)<=0:raise ValueError('Loss budget must be positive when supplied')
        book=self.live.get('orderbook',asset)['record'];estimate=buy_cost(book,amount);portfolio=self.portfolio();charge=amount*fee/10000
        existing=next((p for p in portfolio['positions'] if p['asset']==asset),None);current_value=dec(existing['market_value_usd']) if existing and existing['market_value_usd'] is not None else Decimal(0) if not existing else None
        equity=dec(portfolio['equity_usd']) if portfolio['equity_usd'] is not None else None
        after_fraction=(current_value+amount)/(equity-charge) if equity is not None and equity>charge and current_value is not None else None
        stress=amount*dec(stress_decline_fraction)+charge;violations=[]
        if dec(portfolio['cash_usd'])<amount+charge:violations.append('Insufficient recorded ledger cash; external balances are unknown')
        if not estimate['fully_covered']:violations.append('Saved book does not cover the full amount')
        if after_fraction is None:violations.append('Post-purchase concentration cannot be determined from recorded marks/equity')
        elif after_fraction>dec(max_asset_fraction):violations.append('Above the supplied maximum single-asset allocation assumption')
        if max_loss_usd is not None and stress>dec(max_loss_usd):violations.append('Stress loss exceeds the supplied loss budget')
        maximum=None
        if equity is not None and current_value is not None and not portfolio['empty']:
            cap=dec(max_asset_fraction);fee_fraction=fee/10000
            caps=[dec(portfolio['cash_usd'])/(1+fee_fraction),(cap*equity-current_value)/(1+cap*fee_fraction),dec(book['values']['top25_ask_depth_usd'])]
            if max_loss_usd is not None:caps.append(dec(max_loss_usd)/(dec(stress_decline_fraction)+fee_fraction))
            maximum=str(max(Decimal(0),min(caps)))
        return {'estimate':estimate,'estimated_entry_fee_usd':str(charge),'total_recorded_cash_needed_usd':str(amount+charge),'post_purchase_asset_fraction':str(after_fraction) if after_fraction is not None else None,'hypothetical_stress_loss_usd':str(stress),'maximum_notional_under_entered_constraints_usd':maximum,'assumptions':{'max_asset_fraction':max_asset_fraction,'stress_decline_fraction':stress_decline_fraction,'fee_bps':fee_bps,'max_loss_usd':max_loss_usd},'constraints':violations,'ledger_is_empty':portfolio['empty'],'scope':'Research preview only, no order sent; concentration/stress describe the intended full purchase. Thresholds/stress are user scenario assumptions, book fills are estimates, and external holdings/cash are unknown'}
