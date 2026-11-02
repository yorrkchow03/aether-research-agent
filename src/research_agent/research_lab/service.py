"""Lazy opt-in research extension. No work, model calls or data fetch on import/GET."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from dotenv import dotenv_values
from research_agent.live import atomic
from research_data.http import utcnow
from .corpus import load_corpus, digest
from .signals import signal_catalog

class LabRunInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['signals','workshop']='signals'
    rounds:int=Field(default=2,ge=1,le=3)


class ResearchLabService:
    def __init__(self,runtime):
        self.runtime=runtime;self.root=runtime.root;self.state=runtime.state_dir/'research_lab'
        self.corpus=self.root/'data/validation/signals_v1';self.lock=threading.RLock();self.worker=None;self.cancel=None
        self.enabled=str(os.environ.get('AETHER_RESEARCH_LAB_ENABLED',dotenv_values(self.root/'.env').get('AETHER_RESEARCH_LAB_ENABLED','true'))).lower() not in ('0','false','no')
        # Preserve interrupted work; explicit resume, never silently issue new paid calls.
        for path in (self.state/'runs').glob('*/status.json'):
            row=json.loads(path.read_text())
            if row['state'] in ('running','queued'):
                row.update(state='interrupted',stage='Process stopped; artifacts retained',finished_at=utcnow());atomic(path,row)

    def config(self):
        frozen=(self.corpus/'freeze.json').exists();manifest=json.loads((self.corpus/'manifest.json').read_text()) if frozen else None
        return {'enabled':self.enabled,'corpus_ready':frozen,'corpus':None if not manifest else {'manifest_sha256':digest(self.corpus/'manifest.json'),'datasets':manifest['datasets'],'start':manifest['start'],'end':manifest['end']},
            'catalog':signal_catalog(),'model_ready':self.runtime.settings.model_mode=='ark_plan' and self.runtime.settings.model_id=='doubao-seed-2.1-pro' and bool(self.runtime.settings.api_key),
            'engine':{'active':'python-pure-prefix-v1','argo':'not_integrated','go_available':bool(shutil.which('go')),'reason':'Argo needs a separate Go/WASM strategy adapter and execution-convention parity tests; it is not simulated by the Python engine.'},
            'scope':'Optional advanced research. No orders, ledger writes or default analyst changes. Funding arbitrage is deferred.'}

    def _directory(self,rid):
        if not re.fullmatch(r'[a-f0-9]{32}',rid):raise ValueError('Invalid lab run ID')
        return self.state/'runs'/rid

    def list(self):
        return sorted([json.loads(p.read_text()) for p in (self.state/'runs').glob('*/status.json')],key=lambda r:r['started_at'],reverse=True)

    def get(self,rid):
        directory=self._directory(rid);row=json.loads((directory/'status.json').read_text());path=directory/'artifacts/result.json'
        review=directory/'developer_review.json'
        return {'status':row,'result':json.loads(path.read_text()) if path.exists() else None,
                'developer_review':json.loads(review.read_text()) if review.exists() else None,
                'promotion':'Research artifacts only; independent code/formula review required. No strategy is promoted to the main analyst or orders.',
                'cases':[p.stem for p in sorted((directory/'artifacts/cases').glob('*.json'))]}

    def case(self,rid,name):
        if not re.fullmatch(r'(ETH|BTC)-[a-zA-Z0-9_-]{1,80}',name):raise ValueError('Invalid case ID')
        c=json.loads((self._directory(rid)/'artifacts/cases'/(name+'.json')).read_text())
        return {k:v for k,v in c.items() if k not in ('probabilities','targets','variants')}|{
            'variants':{name:{k:v for k,v in value.items() if k!='model'} for name,value in c['variants'].items()}}

    def start(self,kind='signals',rounds=2,resume=None):
        if not self.enabled:raise ValueError('Research extension disabled in local configuration')
        if kind not in ('signals','workshop') or rounds not in (1,2,3):raise ValueError('Unknown bounded research request')
        if not (self.corpus/'freeze.json').exists():raise ValueError('Capture and freeze the validation corpus with scripts/evaluation/prepare_signal_corpus.py first')
        if kind=='workshop' and not self.config()['model_ready']:raise ValueError('Configure the authorized Doubao Pro Plan first')
        with self.lock,self.runtime.lock:
            if self.worker and self.worker.is_alive():raise ValueError('One extension job is already running')
            if self.runtime.active or getattr(self.runtime.investment,'worker',None) and self.runtime.investment.worker.is_alive():raise ValueError('Main research is active; start the extension when idle')
            if resume:
                directory=self._directory(resume);row=json.loads((directory/'status.json').read_text())
                if row['kind']!='workshop' or row['state'] not in ('interrupted','stopped'):raise ValueError('Only interrupted/stopped workshops resume saved roles')
                kind=row['kind'];rounds=row['rounds'];rid=resume
            else:
                rid=uuid.uuid4().hex;directory=self._directory(rid);directory.mkdir(parents=True)
                row={'run_id':rid,'kind':kind,'rounds':rounds,'started_at':utcnow()}
            row.update(state='queued',stage='Waiting for local worker',error=None);atomic(directory/'status.json',row)
            self.cancel=threading.Event();self.worker=threading.Thread(target=self._execute,args=(directory,row,self.cancel),daemon=True);self.worker.start()
            return row

    def stop(self,rid):
        with self.lock:
            row=self.get(rid)['status']
            if row['state'] not in ('running','queued'):return row
            if row['kind']!='workshop':raise ValueError('Offline signal evaluation is bounded and completes its current run')
            if not self.cancel:raise ValueError('No matching local worker; resume interrupted work explicitly')
            self.cancel.set();return {'state':'stop_requested','scope':'An in-flight model request may finish before stopping; saved roles are retained'}

    def _execute(self,directory,row,cancel):
        def emit(stage,data):
            row.update(stage=stage,updated_at=utcnow(),progress=data);atomic(directory/'status.json',row)
        row.update(state='running');emit('Starting',{})
        try:
            if row['kind']=='signals':
                from .evaluation import run_evaluation
                result=run_evaluation(self.corpus,directory/'artifacts')
                if result['failures']:raise ValueError('Signal evaluation has failed cases; all results retained for inspection')
            else:
                from .workshop import run_workshop
                run_workshop(self.corpus,directory/'artifacts',self.runtime.settings,rounds=row['rounds'],cancel=cancel,emit=emit)
            row.update(state='completed',stage='Completed',finished_at=utcnow());atomic(directory/'status.json',row)
        except Exception as exc:
            row.update(state='stopped' if cancel.is_set() else 'failed',stage='Saved for inspection',finished_at=utcnow(),error=self.runtime.settings.redact(str(exc))[:900]);atomic(directory/'status.json',row)


def register_routes(app,runtime):
    from fastapi.responses import Response
    service=ResearchLabService(runtime);app.state.research_lab=service
    @app.get('/api/research-lab')
    def config():return {'config':service.config(),'runs':service.list()}
    @app.post('/api/research-lab/runs')
    def start(body:LabRunInput):return service.start(**body.model_dump())
    @app.get('/api/research-lab/runs/{rid}')
    def get(rid:str):return service.get(rid)
    @app.get('/api/research-lab/runs/{rid}/cases/{name}')
    def case(rid:str,name:str):return service.case(rid,name)
    @app.post('/api/research-lab/runs/{rid}/resume')
    def resume(rid:str):return service.start(resume=rid)
    @app.post('/api/research-lab/runs/{rid}/stop')
    def stop(rid:str):return service.stop(rid)
    @app.get('/api/research-lab/runs/{rid}/strategy.py')
    def source(rid:str):
        row=service.get(rid)
        if row['status']['kind']!='workshop' or not row['result']:raise ValueError('No selected strategy yet')
        return Response(row['result']['source'],media_type='text/plain',headers={'Content-Disposition':'attachment; filename="research-strategy.py"'})
