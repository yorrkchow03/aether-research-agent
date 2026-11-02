"""Isolated report experiments using the genuine local Deep Agents runtime.

Production defaults are unchanged. Study shell commands retain Python/terminal
access, with macOS denying IP networking and reads of project private stores.
The host SDK only serves a predeclared source set. Jev checks never replace the
mandatory source/numeric publication review.
"""
from __future__ import annotations
from dataclasses import dataclass,replace,asdict
import hashlib,json,shlex,shutil,threading,time
from pathlib import Path

from langchain_core.tools import tool
from langchain_core.messages import AIMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from research_data.http import utcnow,dumps
from .runtime import AgentRuntime
from .terminal import ResearchShell
from .tools import ResearchTools
from .model import create_ark_model
from .review import EvidenceReviewer
from .reports import ReportDraft,report_claims,validate_report
from .evidence_judgments import EvidenceJudgmentService
from .audit import BundleBuilder

ARMS=('original_off','pro_checks','jev_routed')


@dataclass(frozen=True)
class StudyPolicy:
    version:str='report-study-v1'
    author_limit:int=12
    review_limit:int=2
    wall_limit:int=900
    selective_judgments:bool=False
    duplicate_support_checks:bool=True
    finish_after_publish:bool=False
    memory_budget:int=2400
    http_timeout:int=60
    http_max_retries:int=0

    def __post_init__(self):
        if not 12<=self.author_limit<=32 or not 2<=self.review_limit<=4 or not 300<=self.wall_limit<=1800:
            raise ValueError('Use bounded study execution limits')
        if not 2400<=self.memory_budget<=12000:raise ValueError('Use a bounded exact-source memory pack')
        if not 30<=self.http_timeout<=120 or not 0<=self.http_max_retries<=1:raise ValueError('Use bounded provider transport recovery')

    def public(self):return asdict(self)


RECOVERED_POLICY=StudyPolicy(version='report-study-v2-selective-exact-views',author_limit=24,
    review_limit=4,wall_limit=1200,selective_judgments=True,duplicate_support_checks=False,
    finish_after_publish=True,memory_budget=6000,http_timeout=120,http_max_retries=1)


class CompleteAfterPublication(AgentMiddleware):
    """A host completion receipt avoids another author call after publication.

    Failed publish calls keep using the ordinary author repair loop. This only
    runs once the service has actually passed source review and saved a report.
    """
    def __init__(self,service,emit):self.service,self.emit=service,emit
    def wrap_model_call(self,request,handler):
        if self.service.published_reports and self.service.last_report:
            self.emit('host_publication_completion',{'published_reports':list(self.service.published_reports),
                'provider_requests':0,'scope':'Host status receipt after verified model-authored publication; not an additional model answer.'})
            return ModelResponse(result=[AIMessage(content='The source-checked report has been saved.',
                additional_kwargs={'aether_host_completion':True})])
        return handler(request)

class HttpMeter:
    def __init__(self,path,*,timeout=60,max_retries=0,max_requests=None):
        self.path=Path(path);self.lock=threading.Lock();self.rows=[]
        self.timeout,self.max_retries=timeout,max_retries
        self.max_requests=max_requests
    def attach(self,model,stage):
        def request(req):
            with self.lock:
                if self.max_requests is not None and len(self.rows)>=self.max_requests:
                    raise ValueError('Study host HTTP attempt budget exhausted, including source reviews and compaction')
                path='/api/plan/v3/chat/completions' if req.url.path=='/api/plan/v3/chat/completions' else 'unexpected_endpoint'
                row={'stage':stage,'request_index':len(self.rows)+1,'at':utcnow(),'method':req.method,'path':path}
                self.rows.append(row);self.save()
            req.extensions['aether_study_row']=row
        def response(res):
            row=res.request.extensions.get('aether_study_row')
            if row is not None:
                with self.lock:row['http_status']=res.status_code;self.save()
        model.http_client.event_hooks['request'].append(request);model.http_client.event_hooks['response'].append(response)
        # Every study arm uses the same nonstreaming author/reviewer transport.
        model.streaming=False
        client=model.root_client.with_options(timeout=self.timeout,max_retries=self.max_retries)
        model.root_client,model.client=client,client.chat.completions
        return model
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({'requests':self.rows},indent=2)+'\n');temp.replace(self.path)


class FrozenResearchShell(ResearchShell):
    def __init__(self,workspace,settings,cancel=None,*,project_root):
        super().__init__(workspace,settings,cancel)
        if not Path('/usr/bin/sandbox-exec').is_file():raise ValueError('Frozen terminal needs validated macOS sandbox-exec; no unrestricted fallback')
        self.project_root=Path(project_root).resolve();self._env['PYTHONDONTWRITEBYTECODE']='1'
        workspace=Path(workspace).resolve();workspace.mkdir(parents=True,exist_ok=True)
        quote=lambda value:json.dumps(str(value))
        rules=['(version 1)','(allow default)','(deny network*)']
        for name in ('data','runtime','local','dist','reports','docs','.git'):
            rules.append('(deny file-read* (require-all (subpath '+quote(self.project_root/name)+') (require-not (subpath '+quote(workspace)+'))))')
        rules.append('(deny file-read* (literal '+quote(self.project_root/'.env')+'))')
        rules.append('(deny file-write* (require-all (subpath '+quote(self.project_root)+') (require-not (subpath '+quote(workspace)+'))))')
        self.profile=workspace/'.study-sandbox.sb';self.profile_rules=rules
        self.profile_text='\n'.join(rules)+'\n'
        self.profile.write_text(self.profile_text)
    def attach_research_sdk(self,socket_path):
        super().attach_research_sdk(socket_path)
        rule='(allow network-outbound (remote unix-socket (path-literal '+json.dumps(str(Path(socket_path).resolve()))+')))'
        self.profile_text='\n'.join([*self.profile_rules,rule])+'\n'
        self.profile.write_text(self.profile_text)
    def execute(self,command,*,timeout=None):
        if not isinstance(command,str) or not command.strip():return super().execute(command,timeout=timeout)
        # The workspace file is inspectable only. Host-held rules are passed
        # inline, so editing that file cannot relax subsequent commands.
        wrapped=shlex.join(['/usr/bin/sandbox-exec','-p',self.profile_text,'/bin/sh','-c',command])
        return super().execute(wrapped,timeout=timeout)
    def replay_recipe(self,body,rows):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='aether-study-recipe-') as directory:
            root=Path(directory);recipe=root/'recipe.py';recipe.write_bytes(body);paths=[]
            for row in rows:
                p=root/(row['record_id']+'.json');p.write_text(json.dumps(row));paths.append(str(p))
            shell=FrozenResearchShell(root,self.settings,self.cancel,project_root=self.project_root)
            return shell.execute(shlex.join(['python','-I',str(recipe),*paths]),timeout=60)


class StudyReviewer(EvidenceReviewer):
    def __init__(self,original,meter):
        super().__init__(original.settings,original.emit,original.user_context_id,original.diagnostic_dir,
            original.cancel,original.research_cutoff,original.max_requests)
        self.meter=meter
    def build_model(self):return self.meter.attach(super().build_model(),'mandatory_source_review')


class FrozenResearchTools(ResearchTools):
    SAFE_SDK={'sources','library-evidence','memory-news',
        'memory-state','memory-note','memory-cards'}
    def __init__(self,*args,source_ids,study_policy=None,**kwargs):
        super().__init__(*args,**kwargs);self.study_source_ids=frozenset(source_ids)
        self.study_policy=study_policy or StudyPolicy()
    def catalog(self):return {'scope':'Frozen text-source report study; no market capture, external search or numeric-series access.',
        'source_ids':sorted(self.study_source_ids),'model_generated_notes':'Derived, not source evidence'}
    def lookup(self,rid):
        return self._frozen_lookup(rid,set())
    def _frozen_lookup(self,rid,parents):
        if rid in parents:raise ValueError('Frozen calculation lineage cycle')
        if len(parents)>32:raise ValueError('Frozen calculation lineage is too deep')
        derived=False
        if rid not in self.study_source_ids and not rid.startswith('context-'):
            if not self.knowledge or not self.knowledge.contains(rid):raise ValueError('Source outside the predeclared frozen study set')
            candidate=self.knowledge.get(rid)
            derived=candidate.get('dataset_id')=='live.calculation' and bool(candidate.get('recipe_sha256')) and bool(candidate.get('input_record_ids'))
            if not derived:raise ValueError('Source outside the predeclared frozen study set')
            # Validate before the base lookup recursively resolves parents.
            for parent in candidate['input_record_ids']:self._frozen_lookup(parent,parents|{rid})
        row=super().lookup(rid)
        return row
    @staticmethod
    def blocked(*args,**kwargs):raise ValueError('Frozen-source study: external capture/series/holdings operation is unavailable; use saved source IDs')
    online_search=read_page=public_api=fetch_live=chain=market=indicators=events=blocked
    def sdk_call(self,operation,arguments):
        if operation=='sources':return self.catalog()
        if operation not in self.SAFE_SDK:raise ValueError('SDK operation outside frozen source-study scope')
        if operation=='library-evidence':return self.evidence(**arguments)
        return super().sdk_call(operation,arguments)
    def news_pack(self,**arguments):
        arguments.setdefault('budget',self.study_policy.memory_budget)
        packet=super().news_pack(**arguments)
        if any(row.get('record_id') not in self.study_source_ids for row in packet.get('results',[])):
            raise ValueError('Frozen retrieval returned a source outside the declared case')
        return packet
    def as_tools(self):
        items=super().as_tools();service=self
        @tool('list_data_sources')
        def list_sources()->str:
            """List the current job's frozen source IDs and research scope."""
            return dumps(service.catalog())
        return [list_sources if item.name=='list_data_sources' else item for item in items]
    def publish(self,draft):
        if self.study_policy.finish_after_publish:
            with self.lock:
                if self.published_reports:
                    return {'status':'already_published','published_reports':list(self.published_reports),
                        'scope':'One verified publication per job; no duplicate review or export.'}
                return self._publish_study(draft)
        return self._publish_study(draft)
    def _publish_study(self,draft):
        draft=draft if isinstance(draft,ReportDraft) else ReportDraft.model_validate(draft)
        validate_report(draft,self.lookup,as_of=self.cutoff)
        if self.judgment_mode=='assist' and self.study_policy.duplicate_support_checks:
            checks=self.semantic_support(report_claims(draft));reject=[]
            for result in checks['results']:
                if result.get('status')=='ok' and result.get('label') in ('insufficient','contradicted'):
                    reject.append({'label':result['label'],'evidence_ids':result.get('evidence_ids'),
                        'input_hash':result.get('input_hash'),'scope':'Optional pre-publication advice; re-read cited evidence, not a declaration of source truth.'})
            if reject:raise ValueError('Pre-publication source check requests narrower or corrected claims: '+dumps(reject))
        return super().publish(draft)


class ReportStudyRuntime(AgentRuntime):
    def __init__(self,root,state_dir,*,arm,source_ids,policy=None):
        if arm not in ARMS:raise ValueError('Unknown frozen report-study arm')
        self.study_arm=arm;self.study_source_ids=tuple(source_ids)
        self.study_policy=policy or StudyPolicy()
        self.http_meter=HttpMeter(Path(state_dir)/'http-requests.json',timeout=self.study_policy.http_timeout,max_retries=self.study_policy.http_max_retries,
            max_requests=(self.study_policy.author_limit+self.study_policy.review_limit)*(1+self.study_policy.http_max_retries))
        super().__init__(root,state_dir=state_dir,review_request_limit=self.study_policy.review_limit)
        self.settings=replace(self.settings,max_model_calls=self.study_policy.author_limit,search_enabled=False,thinking='disabled',
            judgment_mode='off' if arm=='original_off' else 'assist',judgment_provider='doubao' if arm=='pro_checks' else 'jev')
        self.library=None;self.knowledge.library=None;self.experience=None;self.investment=None
        self.audit_builder=BundleBuilder(self.root,self.state_dir/'audit',
            (self.settings.api_key,*self.settings.private_secrets),self.software_dir,self.state_dir/'knowledge')
        self.judge=None
    def build_finalization_middleware(self,service,emit):
        return CompleteAfterPublication(service,emit) if self.study_policy.finish_after_publish else None
    def build_author_model(self,settings,deliverable):return self.http_meter.attach(super().build_author_model(settings,deliverable),'author')
    def build_shell(self,workspace,cancel):return FrozenResearchShell(workspace,self.settings,cancel,project_root=self.root)
    def build_judgment_service(self):
        if self.judge is None:
            def provider(name,classification=False):return EvidenceJudgmentService(self.root,self.state_dir/(('classification-' if classification else 'judgments-')+name),provider=name,
                requested_model='jev-1.14.0' if name=='jev' else 'doubao-seed-2.1-pro',timeout_seconds=30,max_attempts=1,minimum_confidence=.8)
            if self.study_arm=='pro_checks':self.judge=provider('doubao')
            elif self.study_policy.selective_judgments:
                from .selective_judgments import SelectiveJudgmentService
                self.judge=SelectiveJudgmentService(provider('jev'),provider('doubao'),
                    classification_primary=provider('jev',True),classification_pro=provider('doubao',True),
                    primary_limit=12,pro_limit=16,minimum_confidence=.8)
            else:
                from .semantic_routing import RoutedJudgmentService
                self.judge=RoutedJudgmentService(provider('jev'),provider('doubao'),primary_limit=12,fallback_limit=8,minimum_confidence=.8)
        return self.judge
    def build_research_tools(self,*args,**kwargs):
        if kwargs.get('reviewer'):kwargs['reviewer']=StudyReviewer(kwargs['reviewer'],self.http_meter)
        kwargs.update(library=None,investment=None)
        if not self.study_policy.duplicate_support_checks:kwargs['report_judgments']=None
        return FrozenResearchTools(*args,source_ids=self.study_source_ids,study_policy=self.study_policy,**kwargs)
