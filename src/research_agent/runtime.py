"""Persistent local Deep Agents sessions with API and conversation model modes."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
import httpx
import hashlib
import re
from openai import APIError
from pathlib import Path

from deepagents import create_deep_agent, register_harness_profile, HarnessProfile, GeneralPurposeSubagentProfile
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, message_to_dict
from langgraph.checkpoint.sqlite import SqliteSaver

from research_data.http import utcnow
from research_data.normalize import timestamp, iso
from .bridge import ConversationModel
from .tools import ResearchTools
from .settings import Settings, BRIDGE_MODE
from .model import create_ark_model
from .web_search import ArkWebSearch
from .live import LiveData, Dashboard
from .terminal import ResearchShell
from .review import EvidenceReviewer
from .simulation import DecisionProfile
from .audit import BundleBuilder
from .knowledge import KnowledgeStore
from .investment.service import InvestmentService
from .bootstrap import archive_directory
from .skill_library import install_skills
from .prompts import research_system_prompt
from .sdk_bridge import ResearchSDKServer
from .planning import ResearchPlanningMiddleware, PLANNING_INSTRUCTIONS, PLAN_TOOL_DESCRIPTION

PROJECT_ROOT = Path(__file__).resolve().parents[2]
register_harness_profile("local_bridge:conversation-bridge", HarnessProfile(
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    excluded_tools=frozenset({"execute", "task", "delete"}),
))


def write_json(path, data):
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n")
    tmp.replace(path)


def retain_completed_publications(status,service):
    """Keep verified outputs visible even when a later task step fails."""
    paths=list(getattr(service,'published_reports',()) or ())
    if paths:
        status.update(published_reports=list(dict.fromkeys(paths)),publication_state='saved_before_interruption')
    return status


def freeze_software(root,state_dir):
    """Archive the code loaded at server startup, so later edits do not change a run's bundle."""
    files=[p for p in (root/'src').rglob('*') if p.is_file() and p.suffix in ('.py','.js','.css','.html','.md')]
    files += [p for p in (root/'contracts').glob('*') if p.is_file() and p.suffix in ('.sol','.json')]
    files += [root/n for n in ('requirements.lock.txt','requirements.audit.lock.txt','pyproject.toml') if (root/n).exists()]
    contents={str(p.relative_to(root)):p.read_bytes() for p in files}
    hashes={name:hashlib.sha256(body).hexdigest() for name,body in contents.items()}
    fingerprint=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    destination=state_dir/'software'/fingerprint[:24];destination.mkdir(parents=True,exist_ok=True)
    for relative,body in contents.items():
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=body:raise ValueError('Software snapshot changed; refuse reuse')
        target.write_bytes(body)
    write_json(destination/'source_manifest.json',{'fingerprint':fingerprint,'files':hashes,'scope':'Public source/environment snapshot captured when the local runtime starts; excludes credentials and runtime data'})
    return destination,fingerprint


class TraceMiddleware(AgentMiddleware):
    def __init__(self, emit, cancel=None, settings=None, working_memory=None, job_id=None, experience_context='',context_accounting=None):
        self.emit=emit;self.cancel=cancel;self.settings=settings;self.model_calls=0
        self.working_memory=working_memory;self.job_id=job_id
        self.experience_context=experience_context;self.context_accounting=context_accounting

    def wrap_model_call(self, request, handler):
        if self.cancel and self.cancel.is_set():raise RuntimeError("用户已停止任务")
        self.model_calls+=1
        if self.settings and self.model_calls>self.settings.max_model_calls:raise RuntimeError("达到单个任务模型调用上限，请缩小问题或继续会话")
        self.emit("model_started",{"round":self.model_calls})
        if request is not None and self.settings and self.settings.max_model_calls-self.model_calls<4:
            remaining=self.settings.max_model_calls-self.model_calls+1
            original=request.system_message or SystemMessage(content='')
            content=list(original.content) if isinstance(original.content,list) else [{'type':'text','text':original.content}]
            content.append({'type':'text','text':f'Execution budget: {remaining} author responses remain including this one. Reuse acquired sources and focused excerpts. Prioritize the requested source-checked submission and any necessary corrections; avoid redundant full-file reads or republishing a successfully saved report. Missing information must remain a disclosed gap. Never skip required evidence review or claim unfinished work succeeded.'})
            request=request.override(system_message=original.model_copy(update={'content':content}))
            self.emit('model_budget_notice',{'remaining_author_responses':remaining,'limit':self.settings.max_model_calls})
        if request is not None and (self.working_memory and self.job_id or self.experience_context):
            try:
                note=self.working_memory.context(self.job_id,budget_chars=4000)['text'] if self.working_memory and self.job_id else ''
                original=request.system_message or SystemMessage(content='')
                content=list(original.content) if isinstance(original.content,list) else [{'type':'text','text':original.content}]
                content.append({'type':'text','text':'Task continuation notes (derived, not source evidence; verify cited originals):\n'+note+'\n'+self.experience_context})
                request=request.override(system_message=original.model_copy(update={'content':content}))
            except Exception as exc:self.emit('working_memory_error',{'stage':'context','error_type':type(exc).__name__})
        for repair in range(3):
            measured=None
            if self.context_accounting and request is not None:
                measured=self.context_accounting.estimate([request.system_message,*request.messages],request.tools)
                self.emit('context_measured',measured)
            result=handler(request)
            messages=getattr(result,"result",[])
            usage=getattr(messages[-1],"usage_metadata",None) if messages else None
            actual=getattr(messages[-1],"response_metadata",{}).get("model_name") if messages else None
            finish=getattr(messages[-1],"response_metadata",{}).get('finish_reason') if messages else None
            if measured:self.context_accounting.observe(measured,usage)
            self.emit("model_finished",{"round":self.model_calls,"usage":usage,"actual_model":actual,"finish_reason":finish})
            if self.cancel and self.cancel.is_set():raise RuntimeError("用户已停止任务")
            if finish=='length':
                # Partial JSON can look like a valid tool call after streaming
                # parsing. Never execute such a truncated publication or shell call.
                self.emit('model_output_truncated',{'round':self.model_calls,'message':message_to_dict(messages[-1])})
                raise RuntimeError('Model output reached its response limit; the truncated response was saved and its tools were not executed. Continue with a shorter response or an appropriate report output limit.')
            invalid=getattr(messages[-1],"invalid_tool_calls",[]) if messages else []
            if not invalid:return result
            # Some OpenAI-compatible providers return malformed JSON tool arguments.
            # Ask the model to re-emit them; never repair or execute guessed arguments.
            diagnostic=[{k:c.get(k) for k in ('id','name','args','error')} for c in invalid]
            self.emit('model_format_error',{'invalid_tool_calls':diagnostic,'repair_attempt':repair+1})
            if repair==2:raise RuntimeError('模型连续返回无效工具参数；原始结果已保留，可继续会话修正')
            self.model_calls+=1
            if self.settings and self.model_calls>self.settings.max_model_calls:raise RuntimeError('达到单个任务模型调用上限，请继续会话')
            request=request.override(messages=[*request.messages,HumanMessage(content='上次工具调用的参数不是合法 JSON，工具没有执行。请重新发出合法的工具调用，减少冗余条目，检查数组和引号，不要声称已经保存。错误详情：'+json.dumps(diagnostic,ensure_ascii=False)[:14000])])
            self.emit('model_started',{'round':self.model_calls,'format_repair':True})

    def wrap_tool_call(self, request, handler):
        call=request.tool_call
        if self.cancel and self.cancel.is_set():raise RuntimeError("用户已停止任务")
        self.emit("tool_started", {"name":call["name"],"call_id":call["id"],"arguments_preview":json.dumps(call.get("args",{}),ensure_ascii=False)[:2500]})
        try:
            result=handler(request)
            exit_code=None
            if call['name']=='execute' and isinstance(getattr(result,'artifact',None),dict):
                value=result.artifact.get('exit_code')
                if isinstance(value,int) and not isinstance(value,bool):exit_code=value
                if exit_code not in (None,0) and isinstance(result,ToolMessage):
                    result=result.model_copy(update={'status':'error'})
            if self.working_memory and self.job_id:
                try:
                    payload=getattr(result,'content',None) or getattr(result,'update',None) or str(result)
                    ids=list(dict.fromkeys(re.findall(r'(?:live|page|web|series|calc)-[a-f0-9]{12,64}',str(payload))))[:80]
                    if hasattr(self.working_memory,'observe_tool'):
                        self.working_memory.observe_tool(self.job_id,call['name'],payload,status=getattr(result,'status','success'),tool_call_id=call['id'])
                        if isinstance(result,ToolMessage):
                            view=self.working_memory.progressive_tool_view(self.job_id,call['name'],payload,status=getattr(result,'status','success'))
                            if view!=payload:result=result.model_copy(update={'content':view})
                    else:self.working_memory.record_tool(self.job_id,call['name'],payload,evidence_ids=ids)
                except Exception as exc:self.emit('working_memory_error',{'stage':'tool_artifact','error_type':type(exc).__name__})
            if self.cancel and self.cancel.is_set():raise RuntimeError("用户已停止任务")
            status=getattr(result,"status","success")
            self.emit("tool_finished", {"name":call["name"],"call_id":call["id"],"status":status,
                      "exit_code":exit_code,
                      "output_preview":str(getattr(result,"content",""))[:1800] if call["name"] in ("execute","register_calculation") else None})
            return result
        except Exception as exc:
            error=self.settings.redact(exc) if self.settings else str(exc)
            if self.working_memory and self.job_id and hasattr(self.working_memory,'observe_tool'):
                try:self.working_memory.observe_tool(self.job_id,call['name'],{'status':'error','error':error},status='error',tool_call_id=call['id'])
                except Exception:pass
            self.emit("tool_failed", {"name":call["name"],"call_id":call["id"],"error":error[:1500]})
            if isinstance(exc,(ValueError,KeyError,FileNotFoundError,RuntimeError,OSError,httpx.HTTPError,APIError)) and not (self.cancel and self.cancel.is_set()):
                content={"status":"error","error":error[:1500],"repair":"Correct arguments and retry, or choose another accessible source/method. Unavailable sources are gaps; continue useful research. For reports use actual evidence IDs and exact metric keys, numeric values and units."}
                return ToolMessage(content=json.dumps(content,ensure_ascii=False),name=call["name"],tool_call_id=call["id"],status="error")
            raise


class AgentRuntime:
    def build_finalization_middleware(self,service,emit):
        return None

    def build_author_model(self,settings,deliverable):
        return create_ark_model(settings,max_output_tokens=8192 if deliverable=='report' else 4096)

    def build_shell(self,workspace,cancel):
        return ResearchShell(workspace,self.settings,cancel)

    def build_judgment_service(self):
        if self.settings.judgment_policy=='selective' and self.settings.judgment_provider=='jev':
            from .semantic_services import build_judgment_service
            return build_judgment_service(self.root,self.state_dir,provider='jev',policy='selective')
        from .evidence_judgments import EvidenceJudgmentService
        return EvidenceJudgmentService(self.root,self.state_dir/'evidence-judgments',provider=self.settings.judgment_provider,timeout_seconds=12,max_attempts=1)

    def build_research_tools(self,*arguments,**options):
        return ResearchTools(*arguments,**options)

    def case_evidence(self, record_id):
        """Keep shared case recall behind the same active-source eligibility gate."""
        library=getattr(self,'library',None)
        if library and library.exclusion_reasons(record_id):
            raise ValueError('Research-case source is quarantined')
        return self.knowledge.get(record_id)

    def case_origin_feedback(self, origin, cutoff):
        """Return dated status pointers, without copying personal feedback text."""
        feedback=getattr(self,'report_feedback',None)
        if not feedback or not isinstance(origin,dict) or origin.get('kind')!='reviewed_report':return []
        rows=feedback.list_for_report(origin['id'],cutoff=cutoff)
        return [{'feedback_id':row['feedback_id'],'claim_id':row['claim_id'],
            'status':row['status'],'known_at':row['last_known_at'],
            'assessment':(row.get('latest_review') or {}).get('assessment'),
            'replacement_report_id':(row.get('resolution') or {}).get('replacement_report_id'),
            'scope':'Attributed origin-report feedback; reopen sources and any reviewed update before reusing the case.'}
            for row in rows[-12:]]

    def __init__(self, root=PROJECT_ROOT, state_dir=None, model_mode=None, review_request_limit=4):
        self.root=Path(root).resolve()
        self.settings=Settings.load(self.root)
        if not 1<=review_request_limit<=4:raise ValueError('Source review request budget must be 1..4')
        self.review_request_limit=review_request_limit
        self.model_mode=model_mode or self.settings.model_mode
        self.state_dir=Path(state_dir) if state_dir else self.root/"runtime"
        self.sessions_dir=self.state_dir/"sessions"
        self.sessions_dir.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.active={}
        self.live_data=LiveData(self.root/"data/live")
        self.dashboard=Dashboard(self.live_data)
        self.profile_path=self.state_dir/"decision_profile.json"
        self.software_dir,self.software_fingerprint=freeze_software(self.root,self.state_dir)
        self.audit_builder=BundleBuilder(self.root,self.root/'data/audit',(self.settings.api_key,*self.settings.private_secrets),self.software_dir,self.state_dir/'knowledge')
        self.knowledge=KnowledgeStore(self.state_dir/'knowledge')
        from .library_router import open_library
        self.library=open_library(self.root,self.settings.library_path)
        self.knowledge.library=self.library
        self.experience=None;self.case_memory=None;self.memory_startup_issues=[]
        if self.settings.memory_enabled:
            try:
                from .case_memory import CaseMemory
                self.case_memory=CaseMemory(self.state_dir/'research-cases',
                    redact=self.settings.redact,evidence_lookup=self.case_evidence,
                    origin_feedback=self.case_origin_feedback)
            except Exception as exc:self.memory_startup_issues.append({'component':'research_cases','error_type':type(exc).__name__})
            try:
                from .experience_memory import ExperienceMemory,seed_project_corrections
                self.experience=ExperienceMemory(self.state_dir/'experience',redact=self.settings.redact,evidence_lookup=self.knowledge.get)
                seeded=seed_project_corrections(self.root,self.experience)
                write_json(self.state_dir/'experience/bootstrap.json',seeded)
            except Exception as exc:self.memory_startup_issues.append({'component':'experience','error_type':type(exc).__name__})
        self.live_data.knowledge=self.knowledge
        self.investment=InvestmentService(self.root,self.state_dir,self.live_data,self.knowledge)
        self.bootstrap_knowledge()
        from .report_feedback import ReportFeedbackStore
        self.report_feedback=ReportFeedbackStore(self.state_dir/'report-feedback',report_lookup=self.knowledge.report,
            evidence_lookup=self.knowledge.get,redact=self.settings.redact,experience=self.experience)
        self.knowledge.report_feedback=self.report_feedback
        self.memory_index=None;self.memory_retriever=None;self.memory_index_status={'encoder':'disabled'}
        if self.settings.memory_enabled and self.settings.memory_hybrid_enabled:
            try:
                from .memory_service import open_memory_service,index_source
                self.memory_index,self.memory_retriever,self.memory_index_status=open_memory_service(self.root,self.state_dir,self.knowledge,self.settings.memory_encoder_path)
                self.knowledge.source_observer=lambda row:index_source(self.memory_index,row)
            except Exception as exc:self.memory_startup_issues.append({'component':'event_index','error_type':type(exc).__name__})

    def bootstrap_knowledge(self):
        """Import saved source snapshots, not model conversation summaries as facts."""
        issues=[]
        for workspace in self.sessions_dir.glob('*/workspace'):
            for pattern in ('data/live/*.json','data/web/web-*.json'):
                for path in workspace.glob(pattern):
                    try:
                        row=json.loads(path.read_text());raw=None
                        if row['record_id'].startswith('web-') and not row.get('raw_path'):
                            raw=workspace/'data/web/raw'/(row['source']['sha256']+'.json')
                        if self.knowledge.contains(row['record_id']):continue
                        self.knowledge.register(row,raw)
                        atomic_row=self.knowledge.get(row['record_id']);write_json(path,atomic_row)
                    except (ValueError,KeyError,OSError) as exc:issues.append({'record':path.name,'error':str(exc)[:200]})
            for path in workspace.glob('reports/research-*.json'):
                if path.name.endswith('.audit.json'):continue
                if self.settings.api_key and not path.with_suffix('.audit.json').exists():
                    # A failed export can leave a pre-release file; do not present it as published.
                    published=[]
                    for status in (workspace.parent/'jobs').glob('*/status.json'):
                        published.extend(json.loads(status.read_text()).get('published_reports',[]))
                    if '/reports/'+path.stem+'.md' not in published:continue
                try:self.knowledge.retain_report(path.stem,workspace.parent.name,json.loads(path.read_text()))
                except (ValueError,KeyError,OSError) as exc:issues.append({'report':path.name,'error':str(exc)[:200]})
            for path in workspace.glob('answers/answer-*.json'):
                try:
                    body=json.loads(path.read_text())
                    if body.get('review',{}).get('status')=='reviewed':
                        self.knowledge.retain_answer(path.stem,workspace.parent.name,body)
                except (ValueError,KeyError,OSError) as exc:issues.append({'answer':path.name,'error':str(exc)[:200]})
        write_json(self.state_dir/'knowledge/bootstrap.json',{'checked_at':utcnow(),'issues':issues,'scope':'Receipts begin at import; legacy snapshots are not proof of historical availability'})

    def decision_profile(self):
        return DecisionProfile.model_validate(json.loads(self.profile_path.read_text()) if self.profile_path.exists() else {}).model_dump()

    def save_decision_profile(self,profile):
        obj=DecisionProfile.model_validate(profile).model_dump()
        with self.lock:write_json(self.profile_path,obj)
        return obj

    def _session_dir(self, session_id):
        if len(session_id)!=32 or any(c not in "0123456789abcdef" for c in session_id):
            raise ValueError("Invalid session ID")
        path=self.sessions_dir/session_id
        if not (path/"session.json").exists():raise FileNotFoundError("会话不存在")
        return path

    def create_session(self, title="ETH research", cutoff=None, mode="observed", preferences="", run_dir=None, model_mode=None, research_mode=None,external_run_id=None):
        if mode not in ("observed","strict"):raise ValueError("Invalid availability mode")
        chosen_model=model_mode or self.model_mode
        if chosen_model not in (BRIDGE_MODE,"ark_plan"):raise ValueError("Invalid model mode")
        if chosen_model=="ark_plan" and not self.settings.api_key:raise ValueError("尚未配置 Agent Plan API Key")
        research_mode=research_mode or ("current" if chosen_model=="ark_plan" else "historical")
        if research_mode not in ("current","historical"):raise ValueError("Invalid research mode")
        if research_mode=="current" and mode=="strict":raise ValueError("严格历史模式不能同时查询实时互联网资料")
        run=Path(run_dir) if run_dir else archive_directory(self.root,self.state_dir)
        manifest=json.loads((run/"manifest.json").read_text())
        # Never silently expand beyond the data bundle's configured historical cutoff.
        upper=manifest["cutoff"]
        selected=iso(timestamp(cutoff or upper))
        if timestamp(selected)>timestamp(upper):raise ValueError("截止时间不能晚于该数据包的截止时间")
        sid=uuid.uuid4().hex;directory=self.sessions_dir/sid;workspace=directory/"workspace"
        for part in ("memory","skills/research","notes","reports","data"):(workspace/part).mkdir(parents=True,exist_ok=True)
        preferences=preferences or self.decision_profile().get('preference','')
        (workspace/"memory/preferences.md").write_text("# User preferences (user-supplied data)\n"+preferences[:4000]+"\n")
        install_skills(workspace)
        meta={"session_id":sid,"title":title[:200],"cutoff":selected,"mode":mode,"data_run":str(run.resolve()),
              "model_mode":chosen_model,"model_id":self.settings.model_id if chosen_model=="ark_plan" else "conversation-bridge",
              "research_mode":research_mode,"created_at":utcnow(),"preferences":preferences[:4000]}
        meta["decision_profile"]=self.decision_profile()
        if external_run_id:meta['external_run_id']=external_run_id
        write_json(directory/"session.json",meta)
        return meta

    def sessions(self):
        return sorted([json.loads(p.read_text()) for p in self.sessions_dir.glob("*/session.json")],key=lambda x:x["created_at"],reverse=True)

    def snapshot(self, sid):
        directory=self._session_dir(sid)
        meta=json.loads((directory/"session.json").read_text())
        jobs=[json.loads(p.read_text()) for p in (directory/"jobs").glob("*/status.json")]
        jobs.sort(key=lambda j:j["created_at"])
        reports=["/reports/"+p.name for p in (directory/"workspace/reports").glob("*.md")]
        with self.lock:live=self.active.get(sid)
        return {"session":meta,"jobs":jobs,"reports":reports,"active_job_id":live[0] if live else None}

    def start(self, sid, prompt, deliverable='answer', schedule_run_id=None):
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>20000:raise ValueError("问题长度必须在 1～20000 之间")
        if deliverable not in ('answer','report'):raise ValueError('Unknown deliverable')
        directory=self._session_dir(sid)
        meta=json.loads((directory/"session.json").read_text())
        with self.lock:
            if sid in self.active:raise ValueError("这个会话已有任务正在运行")
            if meta.get("research_mode")=="current":meta["decision_profile"]=self.decision_profile()
            if meta["model_mode"]=="ark_plan":meta["model_id"]=self.settings.model_id
            write_json(directory/"session.json",meta)
            job_id=uuid.uuid4().hex;job_dir=directory/"jobs"/job_id;job_dir.mkdir(parents=True)
            status={"job_id":job_id,"session_id":sid,"state":"running","prompt":prompt,"deliverable":deliverable,"created_at":utcnow(),"model_mode":meta["model_mode"],"model_id":meta["model_id"],"decision_profile":meta.get("decision_profile",{})}
            if schedule_run_id or meta.get('external_run_id'):status['schedule_run_id']=schedule_run_id or meta['external_run_id']
            write_json(job_dir/"status.json",status)
            cancel=threading.Event();self.active[sid]=(job_id,cancel)
            thread=threading.Thread(target=self._run,args=(directory,job_dir,prompt,cancel),daemon=True)
            thread.start()
        return status

    def stop(self, sid):
        self._session_dir(sid)
        with self.lock:
            active=self.active.get(sid)
            if not active:return {"status":"idle"}
            active[1].set()
        return {"status":"stopping"}

    def _run(self, directory, job_dir, prompt, cancel):
        meta=json.loads((directory/"session.json").read_text());status=json.loads((job_dir/"status.json").read_text())
        event_lock=threading.RLock();working=None
        def emit(kind, data):
            with event_lock:
                data=json.loads(self.settings.redact(json.dumps(data,ensure_ascii=False,default=str)))
                event={"time":utcnow(),"kind":kind,**data}
                status.update(updated_at=event['time'],last_event=kind)
                with (job_dir/"events.jsonl").open("a") as f:f.write(json.dumps(event,ensure_ascii=False,default=str)+"\n")
                if kind=="model_waiting":status.update(state="waiting_for_conversation",pending_request=data["request_path"])
                elif kind=="model_replied":status.update(state="running",pending_request=None)
                write_json(job_dir/"status.json",status)
                if working and kind in ('plan_saved','plan_updated'):
                    try:working.update(status['job_id'],plan=data.get('todos',[]),todos=data.get('todos',[]))
                    except Exception:pass
        conn=None;model=None;sdk_server=None;service=None;graph=None;config=None
        try:
            workspace=directory/"workspace"
            if meta["model_mode"]=="ark_plan":
                has_shell=meta.get("research_mode")=="current"
                register_harness_profile("ark_plan:"+meta["model_id"],HarnessProfile(
                    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),excluded_tools=frozenset({"task","delete"} if has_shell else {"execute","task","delete"})))
                from dataclasses import replace
                active_settings=replace(self.settings,model_id=meta["model_id"])
                model=self.build_author_model(active_settings,status.get('deliverable'))
            else:
                model=ConversationModel(bridge_directory=job_dir/"bridge",job_id=status["job_id"],cancel_event=cancel,event_sink=emit)
            current=meta.get("research_mode")=="current" and meta["model_mode"]=="ark_plan"
            live=current and self.settings.search_enabled
            shell=self.build_shell(workspace,cancel) if current else None
            memory=None;retrieval_judgments=None;report_judgments=None;experience_context=''
            if current and self.settings.memory_enabled:
                from .working_memory import WorkingMemory
                from .evidence_memory import EvidenceMemory
                try:
                    working=WorkingMemory(workspace/'memory/task-state',redact=self.settings.redact,evidence_lookup=self.knowledge.get)
                    working.begin(status['job_id'],prompt,meta['cutoff'],mode=meta['mode'])
                except Exception as exc:
                    working=None
                    emit('working_memory_unavailable',{'error_type':type(exc).__name__,
                        'scope':'Optional derived task notes unavailable; original checkpoints and source research continue.'})
                try:
                    if self.experience:
                        experience=self.experience.recall(prompt,cutoff=meta['cutoff'] if meta['mode']=='strict' else None,budget_chars=2200)
                        experience_context=experience['prompt_context']
                        (workspace/'memory/research-experience.md').write_text(experience_context)
                        emit('experience_recalled',{'count':len(experience['results']),'characters':len(experience_context),'scope':'Directly provided process guidance; not market evidence.'})
                except Exception as exc:emit('experience_memory_unavailable',{'error_type':type(exc).__name__})
                if self.settings.judgment_mode!='off' and self.settings.judgment_provider!='disabled':
                    from .evidence_judgments import EvidenceJudgmentService
                    from .context_runtime import BoundedJudgmentSidecar
                    try:
                        judge=self.build_judgment_service()
                        retrieval_judgments=BoundedJudgmentSidecar(judge,emit,limit=12,mode=self.settings.judgment_mode)
                        report_judgments=BoundedJudgmentSidecar(judge,emit,limit=8,mode=self.settings.judgment_mode)
                        if self.settings.judgment_policy=='selective':report_judgments=None
                    except Exception as exc:emit('evidence_sidecar_unavailable',{'error_type':type(exc).__name__,'scope':'Ordinary source research remains available.'})
                memory=EvidenceMemory(self.knowledge,judge=retrieval_judgments)
            # Install current resources for continuing sessions too.
            install_skills(workspace)
            service=self.build_research_tools(Path(meta["data_run"]),workspace,meta["cutoff"],meta["mode"],emit,
                                  web_search=ArkWebSearch(active_settings) if live else None,allow_live=current,
                                  live_data=self.live_data if current else None,shell=shell,
                                  reviewer=EvidenceReviewer(active_settings,emit,diagnostic_dir=workspace/'reviews/attempts',cancel=cancel,research_cutoff=None if current else meta['cutoff'],max_requests=getattr(self,'review_request_limit',4)) if meta["model_mode"]=="ark_plan" else None,
                                  decision_profile=meta.get("decision_profile",{}),audit_builder=self.audit_builder,
                                  run_metadata={"session_id":meta['session_id'],"job_id":status['job_id'],"requested_model":meta['model_id'],"harness":"deepagents==0.7.23","software_fingerprint":self.software_fingerprint},
                                  knowledge=self.knowledge if current else None,investment=self.investment if current else None,
                                  library=self.library if current else None,evidence_memory=memory,
                                  retrieval_judgments=retrieval_judgments,report_judgments=report_judgments,
                                  judgment_mode=self.settings.judgment_mode)
            service.working_memory=working;service.job_id=status['job_id']
            service.case_memory=getattr(self,'case_memory',None)
            if working:working.evidence_lookup=service.lookup
            service.memory_index=getattr(self,'memory_index',None);service.memory_retriever=getattr(self,'memory_retriever',None)
            if current:
                sdk_server=ResearchSDKServer(service,self.settings,cancel)
                shell.attach_research_sdk(sdk_server.start())
            conn=sqlite3.connect(directory/"checkpoints.sqlite",check_same_thread=False)
            saver=SqliteSaver(conn)
            system=research_system_prompt(meta,current,live)
            if working:
                system+='\nTask working notes and source-backed process corrections are available under /memory/task-state/ and /memory/research-experience.md. These are derived guidance, not market evidence. Read originals before asserting facts.\n'
            from .context_accounting import ContextAccounting
            context_accounting=ContextAccounting(emit)
            middleware=[TodoListMiddleware(system_prompt=PLANNING_INSTRUCTIONS,tool_description=PLAN_TOOL_DESCRIPTION) if current else TodoListMiddleware(),
                        TraceMiddleware(emit,cancel,self.settings,working,status['job_id'],experience_context=experience_context,context_accounting=context_accounting)]
            completion=self.build_finalization_middleware(service,emit)
            if completion is not None:middleware.insert(0,completion)
            if current:
                middleware.append(ResearchPlanningMiddleware(status['job_id'],emit))
                if working:
                    from .context_runtime import make_context_middleware
                    middleware.append(make_context_middleware(model,shell,emit,trigger_tokens=self.settings.context_trigger_tokens,token_counter=context_accounting.counter))
            graph=create_deep_agent(model,tools=service.as_tools(),system_prompt=system,
                backend=shell or FilesystemBackend(root_dir=workspace,virtual_mode=True),
                permissions=None if shell else [FilesystemPermission(operations=["write"],paths=["/data/**","/memory/**","/skills/**","/reports/**"],mode="deny")],
                memory=["/memory/preferences.md"],skills=["/skills/"],
                middleware=middleware,checkpointer=saver,name="eth-research-local")
            config={"configurable":{"thread_id":meta["session_id"]},"recursion_limit":200,"max_concurrency":4}
            if saver.get_tuple(config):
                # Older checkpoints cache the former one-skill directory. Rediscover
                # metadata without dropping the conversation or changing preferences.
                graph.update_state(config,{'skills_metadata':None})
            emit("harness_started",{"framework":"deepagents==0.7.23","cutoff":meta["cutoff"],"mode":meta["mode"]})
            result=graph.invoke({"messages":[HumanMessage(content=prompt)]},config=config)
            if status.get('deliverable')=='report' and not service.published_reports:
                emit('report_pending',{'reason':'报告尚未经过发布工具保存'})
                result=graph.invoke({'messages':[HumanMessage(content='本次明确要求保存研究报告，当前没有成功发布。请复用已经获得的证据和计算，调用 publish_report 完成简明报告；修正审核或参数错误后发布，不要重新展开大量检索，也不能仅输出聊天文本冒充完成。')]},config=config)
                if not service.published_reports:raise RuntimeError('报告尚未成功保存；数据和草稿已保留，请继续研究修正')
            if current and status.get('deliverable')=='answer' and not service.submitted_answers:
                emit('answer_pending',{'reason':'No source-checked answer has been submitted'})
                result=graph.invoke({'messages':[HumanMessage(content='Submit the concise response through submit_research_answer now. Reuse existing sources, correct review issues, and put clarification questions or proposed actions in next_steps. Do not present unverified financial claims only as a closing chat message.')]},config=config)
                if not service.submitted_answers:raise RuntimeError('No verified answer saved; sources and draft are retained for continuation')
            final=result['messages'][-1]
            if not getattr(final,'content',None) and not service.published_reports:raise RuntimeError('模型未返回有效回答或已保存报告；可继续会话')
            serialized=json.dumps([message_to_dict(m) for m in result["messages"]],ensure_ascii=False,default=str)
            write_json(job_dir/"messages.json",json.loads(self.settings.redact(serialized)))
            answer=self.settings.redact(final.content) if isinstance(final.content,str) else final.content
            if current and service.submitted_answers:
                answer=service.submitted_answers[-1]['rendered'];status['verified_answer']=service.submitted_answers[-1]
            elif current and service.last_report:
                report=service.last_report;href=f"/api/sessions/{meta['session_id']}/artifact?path="+report['path']
                answer=f"Report published: [{report['draft']['title']}]({href})\n\n"+report['draft']['summary']
            status.update(state="completed",pending_request=None,answer=answer,published_reports=service.published_reports,finished_at=utcnow())
        except Exception as exc:
            status.update(state="cancelled" if cancel.is_set() else "failed",pending_request=None,error=self.settings.redact(exc)[:2500],finished_at=utcnow())
            retain_completed_publications(status,service)
            if graph is not None and config is not None:
                try:
                    messages=graph.get_state(config).values.get('messages',[])
                    write_json(job_dir/'messages.json',json.loads(self.settings.redact(json.dumps([message_to_dict(m) for m in messages],ensure_ascii=False,default=str))))
                except Exception:pass
        finally:
            if sdk_server:sdk_server.close()
            if conn:conn.close()
            if meta["model_mode"]=="ark_plan" and model and model.http_client:model.http_client.close()
            if working:
                try:working.update(status['job_id'],status=status['state'])
                except Exception:pass
            write_json(job_dir/"status.json",status)
            with self.lock:self.active.pop(meta["session_id"],None)

    def recover_interrupted_jobs(self):
        """A fresh process cannot resume a waiting Python call; preserve checkpoints and flag it."""
        for path in self.sessions_dir.glob("*/jobs/*/status.json"):
            item=json.loads(path.read_text())
            if item["state"] in ("running","waiting_for_conversation"):
                item.update(state="interrupted",pending_request=None,error="本地进程已退出；可在同一会话发送新问题继续。")
                write_json(path,item)
