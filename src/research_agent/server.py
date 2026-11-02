"""Loopback-only chat console, explicit manual model replies, and local artifacts."""
import json
import secrets
import re
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field

from .bridge import ModelReply, write_reply
from .runtime import AgentRuntime
from .public_web import PageText
from .simulation import DecisionProfile,compare_strategies,market_statistics,loss_scenarios
from .audit import verify_bundle
from .replay import replay_bundle
from .scheduler import Scheduler,Subscription


class SessionInput(BaseModel):
    title:str=Field(default="ETH research",max_length=200)
    cutoff:str|None=None
    mode:str="observed"
    preferences:str=Field(default="",max_length=4000)
    model_mode:str|None=None
    research_mode:str|None=None


class Question(BaseModel):
    prompt:str=Field(min_length=1,max_length=20000)
    deliverable:Literal['answer','report']='answer'


class SimulationRequest(BaseModel):
    profile:DecisionProfile=Field(default_factory=DecisionProfile)
    lookback_days:int=Field(default=7,ge=1,le=60)


class ReportConcern(BaseModel):
    note:str=Field(min_length=1,max_length=4000)
    claim_id:str=Field(default='S1',max_length=80)
    category:Literal['outdated','source','calculation','unclear','other']='other'


class ConcernReview(BaseModel):
    reviewer_kind:Literal['independent_model','programmatic','user']
    receipt_path:str=Field(min_length=1,max_length=1000)
    evidence_ids:list[str]=Field(min_length=1,max_length=40)
    assessment:Literal['needs_correction','needs_more_evidence','not_reproduced']
    correction:str=Field(default='',max_length=4000)


class ConcernResolution(BaseModel):
    replacement_report_id:str=Field(min_length=1,max_length=160)


def create_app(runtime=None, port=8765):
    runtime=runtime or AgentRuntime();runtime.recover_interrupted_jobs()
    token=secrets.token_urlsafe(32)
    scheduler=Scheduler(runtime,runtime.state_dir)
    from .desk import ResearchDesk, report_changes
    desk=ResearchDesk(runtime.state_dir,runtime.knowledge,scheduler,runtime.dashboard)
    from .report_feedback import ReportFeedbackStore
    feedback=getattr(runtime,'report_feedback',None) or ReportFeedbackStore(runtime.state_dir/'report-feedback',report_lookup=runtime.knowledge.report,
        evidence_lookup=runtime.knowledge.get,redact=runtime.settings.redact,experience=runtime.experience)
    runtime.knowledge.report_feedback=feedback
    @asynccontextmanager
    async def lifespan(app):
        scheduler.start()
        try:yield
        finally:scheduler.stop()
    app=FastAPI(title="Aether Research",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.runtime=runtime;app.state.csrf_token=token
    app.state.scheduler=scheduler
    app.state.report_feedback=feedback
    from .investment.routes import register_routes
    register_routes(app,runtime)
    from .research_lab.service import register_routes as register_research_lab
    register_research_lab(app,runtime)

    @app.middleware("http")
    async def local_only(request:Request, call_next):
        allowed={f"127.0.0.1:{port}",f"localhost:{port}"}
        host=request.headers.get("host","")
        origin=request.headers.get("origin")
        if host not in allowed or (origin and origin not in {"http://"+h for h in allowed}):
            return JSONResponse({"error":"Local origin required"},status_code=403)
        if request.method not in ("GET","HEAD") and not secrets.compare_digest(request.headers.get("x-local-token",""),token):
            return JSONResponse({"error":"Local session token required"},status_code=403)
        response=await call_next(request)
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):return JSONResponse({"error":str(exc)},status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):return JSONResponse({"error":"Local file or research session unavailable"},status_code=404)

    @app.get("/")
    def index():
        html=(Path(__file__).parent/"web/dashboard.html").read_text()
        return HTMLResponse(html.replace("__LOCAL_TOKEN__",json.dumps(token)))

    @app.get("/assets/{name}")
    def assets(name:str):
        if name not in ("dashboard.js","dashboard.css","investment.js","investment.css","desk.js","desk.css","research-lab.js"):raise FileNotFoundError()
        return FileResponse(Path(__file__).parent/"web"/name,media_type="text/javascript" if name.endswith("js") else "text/css")

    @app.get("/api/sessions")
    def sessions():return runtime.sessions()

    @app.get("/api/config")
    def configuration():return {**runtime.settings.public(),"terminal_mode":"authorized_local_host","harness":"Deep Agents 0.7.23","software_fingerprint":runtime.software_fingerprint,"server_pid":os.getpid()}

    @app.get('/api/health')
    def health():
        with runtime.lock:active=[{'session_id':sid,'job_id':row[0]} for sid,row in runtime.active.items()]
        return {'status':'ok','active_jobs':active,'software_fingerprint':runtime.software_fingerprint}

    @app.get('/api/research-capabilities')
    def research_capabilities():
        library=getattr(runtime,'library',None)
        coverage=library.coverage() if library else None
        rows=coverage.get('datasets',[]) if coverage else []
        configured=(bool(runtime.settings.private_secrets) if runtime.settings.judgment_provider=='jev'
                    else bool(runtime.settings.api_key) if runtime.settings.judgment_provider=='doubao'
                    else runtime.settings.judgment_provider=='lexical')
        return {'library':{'available':library is not None,'window_start':coverage.get('window_start') if coverage else None,
            'window_end':coverage.get('window_end') if coverage else None,'datasets':len(rows),
            'complete_datasets':sum(row.get('status')=='complete' for row in rows),
            'core_complete':coverage.get('core_complete') if coverage else None},
            'memory':{'enabled':runtime.settings.memory_enabled,'task_state':True,'source_evidence_packs':True,
                      'correction_aware_recall':True,'direct_process_context':True,'structured_research_state':True,
                      'conditional_cases':getattr(runtime,'case_memory',None) is not None,
                      'local_retrieval':getattr(runtime,'memory_index_status',{}),
                      'context_trigger_tokens':runtime.settings.context_trigger_tokens},
            'semantic_checks':{'provider':runtime.settings.judgment_provider,'mode':runtime.settings.judgment_mode,
                'configured':configured,'tasks':['classification','relevance','relation','support'],
                'scope':'Derived public-source judgments; shadow keeps ordinary evidence selection and publication checks.'},
            'scope':'Local stored capabilities; this read does not call a model or capture data.'}

    @app.get('/api/memory/status')
    def memory_status():
        index=getattr(runtime,'memory_index',None);counts={};vectors={}
        if index:
            with index.connect() as c:
                for table in ('sources','cards','links'):
                    counts[table]=c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                retriever=getattr(runtime,'memory_retriever',None)
                if retriever:
                    row=c.execute("SELECT COUNT(*),COUNT(DISTINCT substr(item_id,1,instr(item_id,':')-1)) FROM embeddings WHERE namespace=?",(retriever.namespace,)).fetchone()
                    vectors={'cached_passages':row[0],'cached_source_versions':row[1],
                        'max_characters_per_document':retriever.max_document_chars,
                        'scope':'Current local representation cache, not source completeness or semantic accuracy.'}
        return {'enabled':runtime.settings.memory_enabled,'index':counts,
            'semantic_index':vectors,
            'encoder':getattr(runtime,'memory_index_status',{}),'startup_issues':getattr(runtime,'memory_startup_issues',[]),
            'scope':'Partial derived source index, not full corpus coverage. Model availability is not semantic accuracy.'}

    @app.get('/api/semantic-validation')
    def semantic_validation():
        from .semantic_validation_view import load_semantic_validation
        return load_semantic_validation(runtime.state_dir, mode=runtime.settings.judgment_mode,
                                        provider=runtime.settings.judgment_provider)

    @app.get('/api/memory/cards')
    def memory_cards(query:str='',cutoff:str|None=None,mode:str='observed',limit:int=8):
        index=getattr(runtime,'memory_index',None)
        if not index:return {'results':[],'available':False}
        return index.query(query,cutoff=cutoff,mode=mode,limit=limit)

    @app.get('/api/memory/cases')
    def research_cases(query:str='',asset:str|None=None,horizon_days:int|None=None,
                       cutoff:str|None=None,limit:int=4,budget_chars:int=16000):
        memory=getattr(runtime,'case_memory',None)
        if not memory:return {'available':False,'results':[]}
        context={key:value for key,value in {'asset':asset,'horizon_days':horizon_days}.items() if value is not None}
        return memory.recall(query,context=context,cutoff=cutoff,limit=limit,budget_chars=budget_chars)

    @app.get('/api/memory/cases/{case_id}')
    def research_case(case_id:str,cutoff:str|None=None):
        memory=getattr(runtime,'case_memory',None)
        if not memory:raise ValueError('Conditional case memory unavailable')
        return memory.get(case_id,cutoff=cutoff)

    @app.get('/api/sessions/{sid}/working-memory')
    def task_memory(sid:str,job_id:str|None=None):
        directory=runtime._session_dir(sid)
        if job_id is None:
            jobs=runtime.snapshot(sid)['jobs']
            if not jobs:return {'available':False,'reason':'No research task yet'}
            job_id=jobs[-1]['job_id']
        if not re.fullmatch(r'[a-f0-9]{32}',job_id) or not (directory/'jobs'/job_id/'status.json').is_file():raise FileNotFoundError()
        path=directory/'workspace/memory/task-state'
        if not (path/'working.sqlite').is_file():return {'available':False,'reason':'This task predates working-memory capture'}
        from .working_memory import WorkingMemory
        memory=WorkingMemory(path,redact=runtime.settings.redact)
        try:return {'available':True,'state':memory.state(job_id),'context':memory.context(job_id,budget_chars=3000)}
        except KeyError:return {'available':False,'reason':'This task has no captured working state'}

    @app.get('/api/news')
    def news(query:str='',asset:Literal['ETH','BTC','ALL']='ETH',original_only:bool=False):
        return {'items':runtime.knowledge.search_news(query,asset,limit=30,original_only=original_only,material_only=True),'scope':'Publisher material; search result snippets remain available through research recall, not featured as news'}

    @app.get('/api/news/events')
    def news_events(asset:Literal['ETH','BTC']='ETH'):return {'items':runtime.knowledge.events(asset)}

    @app.get('/api/news/events/history')
    def news_history(asset:Literal['ETH','BTC']='ETH'):
        rows=runtime.knowledge.event_history(asset,limit=150)
        return {'items':rows,'limit':150,'possibly_truncated':len(rows)==150,
                'scope':'Saved source-reviewed classifications; versions preserve earlier judgments, not independently verified outcomes.'}

    @app.get('/api/desk')
    def research_desk():return desk.packet()

    @app.get('/api/evaluations/memory/latest')
    def memory_evaluation():
        paths=sorted((runtime.root/'reports/product_evolution').glob('memory_v*/summary.json'),
                     key=lambda path:path.stat().st_mtime,reverse=True)
        if not paths:return {'state':'not_run','summary':None}
        return {'state':'completed','version':paths[0].parent.name,'summary':json.loads(paths[0].read_text())}

    class SeenInput(BaseModel):
        ids:list[str]=Field(default_factory=list,max_length=100)
        capture_baseline:bool=False

    @app.post('/api/desk/seen')
    def seen_activity(body:SeenInput):return desk.acknowledge(body.ids,body.capture_baseline)

    @app.get('/api/reports')
    def all_reports(query:str=''):return {'items':runtime.knowledge.search_reports(query,30)}

    @app.get('/api/reports/compare')
    def compare_saved_reports(older:str,newer:str):
        before,after=runtime.knowledge.report(older),runtime.knowledge.report(newer)
        from research_data.normalize import timestamp
        if timestamp(before['created_at'])>timestamp(after['created_at']):raise ValueError('Select an earlier report as the comparison')
        return report_changes(before,after)

    @app.get('/api/reports/{report_id}/feedback')
    def report_feedback(report_id:str,cutoff:str|None=None):
        runtime.knowledge.report(report_id)
        return {'items':feedback.list_for_report(report_id,cutoff=cutoff),
            'scope':'Saved concerns and attributed reviews; user flags and AI findings are not verified facts.'}

    @app.post('/api/reports/{report_id}/feedback')
    def flag_report(report_id:str,body:ReportConcern):
        return feedback.submit(report_id,**body.model_dump(),actor='user')

    @app.post('/api/feedback/{feedback_id}/review')
    def review_concern(feedback_id:str,body:ConcernReview):
        return feedback.review(feedback_id,**body.model_dump())

    @app.post('/api/feedback/{feedback_id}/resolve')
    def resolve_concern(feedback_id:str,body:ConcernResolution):
        return feedback.resolve(feedback_id,**body.model_dump())

    @app.get('/api/subscriptions')
    def subscriptions():return {'items':scheduler.list(),'runs':scheduler.runs(),'local_service_required':True}

    @app.post('/api/subscriptions')
    def save_subscription(body:Subscription):
        if runtime.model_mode!='ark_plan':raise ValueError('Configure an API-backed research model before automatic briefs')
        return scheduler.save(body.model_dump())

    @app.delete('/api/subscriptions/{subscription_id}')
    def remove_subscription(subscription_id:str):return scheduler.delete(subscription_id)

    @app.post('/api/subscriptions/{subscription_id}/run')
    def run_subscription(subscription_id:str):return scheduler.run_now(subscription_id)

    @app.get('/api/sessions/{sid}/evidence/{rid}')
    def read_source(sid:str,rid:str):
        directory=runtime._session_dir(sid)
        legacy=False
        if runtime.knowledge.contains(rid):row=runtime.knowledge.get(rid)
        elif rid.startswith('live-'):
            runtime.knowledge.register(runtime.live_data.lookup(rid));row=runtime.knowledge.get(rid)
        elif re.fullmatch(r'(calc|series|context)-[a-f0-9]{24}',rid):
            folder='calculations' if rid.startswith('calc-') else 'collections' if rid.startswith('series-') else 'context'
            row=json.loads((directory/'workspace/data'/folder/(rid+'.json')).read_text());legacy=True
        else:
            from research_data.sdk import ResearchData
            meta=json.loads((directory/'session.json').read_text());sdk=ResearchData(Path(meta['data_run']),cutoff=meta['cutoff'],mode=meta['mode'])
            try:
                packet=sdk.read_evidence(rid);row=packet['record'];legacy=True
                dataset=next(d for d in sdk.manifest['datasets'] if d['dataset_id']==row['dataset_id'])
                row['units']=dataset['units'];row['scope']=dataset['scope']
                if packet.get('evidence'):
                    source_id=packet['evidence'][0]['source_id'];row['source']=next(s for s in sdk.manifest['sources'] if s['source_id']==source_id)
            except (KeyError,StopIteration):raise ValueError('Source is unavailable at this archived session cutoff') from None
            finally:sdk.close()
        return {'record':{k:v for k,v in row.items() if k not in ('raw_path','recipe_path','data','series')},
                'series_preview':row.get('series',[])[-10:],'raw_verified':bool(row.get('raw_path')),
                'scope':'Legacy archive, not current market evidence' if legacy else 'Saved original material or derived calculation; attribution and integrity do not prove truth'}

    @app.get('/api/evidence/{rid}')
    def read_shared_source(rid:str):
        if not re.fullmatch(r'(live|page|web)-[a-f0-9]{24}',rid):raise ValueError('Select a current saved source ID')
        library=getattr(runtime,'library',None)
        row=runtime.knowledge.get(rid) if runtime.knowledge.contains(rid) else library.evidence(rid) if library else runtime.knowledge.get(rid)
        return {'record':{k:v for k,v in row.items() if k not in ('raw_path','recipe_path','data','series')},'series_preview':row.get('series',[])[-10:],'raw_verified':bool(row.get('raw_path')),'scope':'Current saved evidence, without creating an LLM session; not a historical-cutoff lookup'}

    def library():
        value=getattr(runtime,'library',None)
        if value is None:raise ValueError('Research library has not been built')
        return value

    @app.get('/api/library/datasets')
    def library_datasets():return {'datasets':library().datasets()}

    @app.get('/api/library/coverage')
    def library_coverage():return library().coverage()

    @app.get('/api/library/series')
    def library_series(dataset_id:str,start:str|None=None,end:str|None=None,mode:Literal['observed','strict']='observed',known_by:str|None=None,limit:int=2000,offset:int=0):
        return library().series(dataset_id,start=start,end=end,mode=mode,known_by=known_by,limit=limit,offset=offset)

    @app.get('/api/library/documents')
    def library_documents(query:str='',asset:str|None=None,source:str|None=None,kind:str|None=None,start:str|None=None,end:str|None=None,mode:Literal['observed','strict']='observed',known_by:str|None=None,limit:int=30,offset:int=0):
        return library().search_documents(query,asset=asset,source=source,kind=kind,start=start,end=end,mode=mode,known_by=known_by,limit=limit,offset=offset)

    @app.get('/api/library/research')
    def library_research(query:str='',limit:int=8,offset:int=0):
        return library().search_outputs(query,limit=limit,offset=offset)

    @app.get('/api/library/research/{output_id}')
    def library_output(output_id:str):return library().output(output_id)

    @app.get('/api/library/relationships')
    def library_links(record_id:str,known_by:str|None=None,limit:int=30,offset:int=0):
        return library().record_links(record_id,known_by=known_by,limit=limit,offset=offset)

    @app.get("/api/dashboard")
    def dashboard():return runtime.dashboard.snapshot()

    @app.post("/api/dashboard/refresh")
    def refresh_dashboard():return runtime.dashboard.start_refresh()

    @app.get("/api/decision-profile")
    def profile():return runtime.decision_profile()

    @app.post("/api/decision-profile")
    def update_profile(body:DecisionProfile):return runtime.save_decision_profile(body.model_dump())

    @app.get("/api/decision-context")
    def decision_context():
        p=runtime.decision_profile();data=runtime.dashboard.snapshot();item=data.get("items",{}).get("eth_candles",{})
        row=item.get("record");stats=None;error=None
        if row:
            try:stats=market_statistics(row["series"],min(p["horizon_days"],len(row["series"])-1))
            except ValueError as exc:error=str(exc)
        from .metrics import derive_records
        amount=p['budget_usd'] or 1000
        purchase=derive_records(data.get('items',{}),runtime.live_data.root,runtime.knowledge,[('purchase','purchase',['orderbook'],amount)])['purchase']
        return {"profile":p,"loss_scenarios":loss_scenarios(p),"market_statistics":stats,"statistics_error":error,
                "purchase_estimate":purchase,"purchase_amount_is_default":p['budget_usd'] is None,
                "price_record_id":row["record_id"] if row else None,"price_source_status":item.get("status","unavailable"),
                "data_scope":"Daily closed ETH prices; historical descriptions and hypothetical losses, not probability or a buying recommendation"}

    @app.post("/api/simulations")
    def simulation(body:SimulationRequest):
        data=runtime.dashboard.snapshot();item=data.get("items",{}).get("eth_candles",{});row=item.get("record")
        if not row:raise ValueError("Completed daily data is unavailable; refresh sources first")
        series=row["series"][-min(len(row["series"]),body.profile.horizon_days+body.lookback_days+1):]
        result=compare_strategies(series,body.profile.model_dump(),body.lookback_days)
        return {**result,"price_record_id":row["record_id"],"source":row["source"],"source_status":item.get("status"),
                "scope":"Actual saved API prices; retrospective paper comparison with assumed costs, not a live strategy"}

    @app.get("/api/sessions/{sid}/sources")
    def sources(sid:str):
        workspace=runtime._session_dir(sid)/"workspace"
        rows=[]
        for pattern in ("data/web/web-*.json","data/live/*.json"):
            for path in workspace.glob(pattern):
                row=json.loads(path.read_text());source=row.get("source",{})
                title=row.get("title") or source.get("name") or row["dataset_id"]
                excerpt=(row.get("text") or row.get("method") or row.get("scope") or "")[:1000]
                if row["record_id"].startswith("page-") and row.get("raw_path") and not row.get('text') and row.get('content_type')!='application/pdf':
                    parser=PageText();parser.feed(Path(row["raw_path"]).read_text(errors="replace"))
                    title=parser.title() or title;excerpt=parser.text()[:1000]
                rows.append({"record_id":row["record_id"],"title":title,
                             "url":row.get("url") or source.get("url"),"published_at":row.get("published_at"),
                             "retrieved_at":row.get("retrieved_at"),"excerpt":excerpt,
                             "quality_flags":row.get("quality_flags",[])})
        return sorted(rows,key=lambda x:x.get("retrieved_at") or "",reverse=True)

    @app.get("/api/sessions/{sid}/packages")
    def packages(sid:str):
        workspace=runtime._session_dir(sid)/'workspace'
        return [json.loads(p.read_text()) for p in workspace.glob('reports/*.audit.json')]

    def audit_path(package_id):
        if not re.fullmatch(r'research-[a-f0-9]{12}-[a-f0-9]{10}',package_id):raise ValueError('Invalid audit package ID')
        path=runtime.root/'data/audit'/package_id
        if not path.is_dir():raise FileNotFoundError()
        return path

    @app.get('/api/audits/{package_id}/download')
    def download_package(package_id:str):
        directory=audit_path(package_id);path=directory.with_suffix('.zip')
        if not path.is_file():raise FileNotFoundError()
        return FileResponse(path,media_type='application/zip',filename=path.name)

    @app.post('/api/audits/{package_id}/verify')
    def verify_package(package_id:str):
        directory=audit_path(package_id)
        reference=json.loads((directory/'integrity_reference.json').read_text())
        return verify_bundle(directory,reference)

    @app.post('/api/audits/{package_id}/replay')
    def replay_package(package_id:str):
        directory=audit_path(package_id)
        reference=json.loads((directory/'integrity_reference.json').read_text())
        result=replay_bundle(directory,runtime.settings,reference,execute_recipes=True)
        from research_data.http import utcnow
        destination=runtime.root/'reports/replays';destination.mkdir(parents=True,exist_ok=True)
        (destination/(package_id+'.json')).write_text(json.dumps({**result,'verified_at':utcnow()},ensure_ascii=False,indent=2))
        return result

    @app.get('/api/evaluations/latest')
    def latest_evaluation():
        runs=sorted((runtime.root/'data/evaluations').glob('*/config.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        if not runs:return {'state':'not_run','scope':'Historical evaluation has not run'}
        directory=runs[0].parent;status=json.loads((directory/'status.json').read_text())
        summary=directory/'summary.json'
        return {'run_id':directory.name,'status':{k:v for k,v in status.items() if k!='results'},
                'summary':json.loads(summary.read_text()) if summary.exists() else None}

    @app.post("/api/sessions")
    def create_session(body:SessionInput):return runtime.create_session(**body.model_dump())

    @app.get("/api/sessions/{sid}")
    def snapshot(sid:str):return runtime.snapshot(sid)

    @app.post("/api/sessions/{sid}/messages")
    def ask(sid:str, body:Question):return runtime.start(sid,body.prompt,body.deliverable)

    @app.post("/api/sessions/{sid}/stop")
    def stop(sid:str):return runtime.stop(sid)

    def pending(sid):
        snap=runtime.snapshot(sid)
        if not snap["jobs"] or snap["jobs"][-1]["state"]!="waiting_for_conversation":raise ValueError("会话当前没有等待中的模型请求")
        return Path(snap["jobs"][-1]["pending_request"])

    @app.get("/api/sessions/{sid}/pending")
    def read_pending(sid:str):return json.loads(pending(sid).read_text())

    @app.post("/api/sessions/{sid}/reply")
    def reply(sid:str, body:ModelReply):return {"response_path":write_reply(pending(sid),body.model_dump())}

    @app.get("/api/sessions/{sid}/events")
    def events(sid:str):
        snap=runtime.snapshot(sid)
        if not snap["jobs"]:return []
        path=runtime._session_dir(sid)/"jobs"/snap["jobs"][-1]["job_id"]/"events.jsonl"
        if not path.exists():return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    @app.get("/api/sessions/{sid}/artifact")
    def artifact(sid:str,path:str):
        workspace=runtime._session_dir(sid)/"workspace"
        file=(workspace/path.lstrip("/")).resolve()
        if not file.is_relative_to(workspace.resolve()) or file.suffix not in (".md",".json",".py") or file.parts[len(workspace.parts)] not in ("reports","data","notes","reviews","drafts","answers"):
            raise ValueError("只能读取本会话的报告和数据材料")
        if not file.is_file():raise FileNotFoundError()
        return FileResponse(file,media_type="text/plain; charset=utf-8",filename=file.name,content_disposition_type="inline")

    return app
