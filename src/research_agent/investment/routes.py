"""Local investment API. All writes inherit the server's origin/CSRF gate."""
from typing import Literal
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from .ledger import Transaction
from .strategies import StrategyConfig


class ImportCSV(BaseModel):
    text:str=Field(max_length=2_000_000)

class VoidInput(BaseModel):
    void:bool=True

class PreviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    asset:Literal['ETH','BTC']='ETH'
    notional:float=Field(gt=0,le=1e9)
    fee_bps:float=Field(default=10,ge=0,le=500)
    max_asset_fraction:float=Field(default=.5,gt=0,le=1)
    stress_decline_fraction:float=Field(default=.3,gt=0,le=1)
    max_loss_usd:float|None=Field(default=None,gt=0,le=1e9)

class AssessmentInput(BaseModel):
    task:Literal['source_support','relevance','event_status']='source_support'
    claim:str=Field(min_length=1,max_length=2500)
    record_id:str=Field(pattern=r'^(page|web)-[a-f0-9]{24}$')
    asset:Literal['ETH','BTC']='ETH'

def register_routes(app,runtime):
    service=runtime.investment
    @app.get('/api/investment/config')
    def config():return {'jev':service.jev.settings.public(),'assets':['ETH','BTC'],'ledger':'Private local FIFO ledger, USD fees; no exchange orders'}
    @app.get('/api/portfolio')
    def portfolio():return service.portfolio()
    @app.get('/api/portfolio/transactions')
    def transactions():return {'items':service.ledger.list()}
    @app.post('/api/portfolio/transactions')
    def add_transaction(body:Transaction):return service.ledger.import_rows([body.model_dump()])
    @app.post('/api/portfolio/import')
    def import_csv(body:ImportCSV):return service.ledger.import_csv(body.text)
    @app.get('/api/portfolio/export')
    def export_csv():return Response(service.ledger.csv(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="aether-private-ledger.csv"'})
    @app.post('/api/portfolio/transactions/{entry_id}/void')
    def void(entry_id:str,body:VoidInput):return service.ledger.void(entry_id,restore=not body.void)
    @app.post('/api/portfolio/preview')
    def preview(body:PreviewInput):return service.preview_buy(**body.model_dump())
    @app.get('/api/strategies')
    def strategies():return {'items':service.strategies()}
    @app.post('/api/strategies')
    def start_strategy(body:StrategyConfig):return service.start_strategy(body.model_dump())
    @app.get('/api/strategies/{run_id}')
    def strategy(run_id:str):return service.strategy(run_id)
    @app.get('/api/strategies/{run_id}/export')
    def export_strategy(run_id:str):return service.strategy(run_id,True)
    @app.get('/api/strategies/{run_id}/report')
    def strategy_report(run_id:str):return Response(service.strategy_report(run_id),media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="aether-strategy-report.md"'})
    @app.post('/api/jev/assess')
    def assess(body:AssessmentInput):return service.assess_source(body.task,body.claim,body.record_id,body.asset)
    @app.get('/api/jev/assessments')
    def assessments():return {'items':service.assessments()}
