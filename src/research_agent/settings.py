"""Private server-side settings; never expose credentials in session metadata."""
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values
from .model_context import context_policy

PLAN_BASE_URL="https://ark.cn-beijing.volces.com/api/plan/v3"
BRIDGE_MODE="current_conversation_no_api"


@dataclass(frozen=True)
class Settings:
    model_mode:str=BRIDGE_MODE
    model_id:str="doubao-seed-2.1-pro"
    base_url:str=PLAN_BASE_URL
    api_key:str=field(default="",repr=False)
    private_secrets:tuple[str,...]=field(default=(),repr=False)
    thinking:str="disabled"
    search_enabled:bool=False
    max_model_calls:int=60
    library_path:str='data/library'
    memory_enabled:bool=True
    judgment_provider:str='lexical'
    judgment_mode:str='shadow'
    judgment_policy:str='legacy'
    context_trigger_tokens:int|None=None
    memory_encoder_path:str='local/models/potion-multilingual-128M'
    memory_hybrid_enabled:bool=True
    news_reranking:str='off'
    evidence_reader_enabled:bool=True
    restricted_execution_root:str|None=field(default=None,repr=False)

    @classmethod
    def load(cls, root:Path):
        source=Path(root).resolve()
        cfg=dotenv_values(source/".env")
        key=cfg.get("ARK_API_KEY") or ""
        mode=cfg.get("AGENT_MODEL_MODE") or ("ark_plan" if key else BRIDGE_MODE)
        base=(cfg.get("ARK_BASE_URL") or PLAN_BASE_URL).rstrip("/")
        if mode not in ("ark_plan",BRIDGE_MODE):raise ValueError("Unknown model mode")
        if base!=PLAN_BASE_URL:raise ValueError("当前只使用已授权的 Agent Plan 地址 /api/plan/v3")
        thinking=cfg.get("ARK_THINKING") or "disabled"
        if thinking not in ("disabled","enabled"):raise ValueError("Invalid thinking configuration")
        judgment_provider=cfg.get('AETHER_JUDGMENT_PROVIDER') or 'lexical'
        judgment_mode=cfg.get('AETHER_JUDGMENT_MODE') or 'shadow'
        if judgment_provider not in ('jev','doubao','lexical','disabled'):raise ValueError('Invalid evidence judgment provider')
        if judgment_mode not in ('off','shadow','assist'):raise ValueError('Invalid evidence judgment mode')
        judgment_policy=cfg.get('AETHER_JUDGMENT_POLICY') or 'legacy'
        if judgment_policy not in ('legacy','selective'):raise ValueError('Invalid semantic service policy')
        news_reranking=cfg.get('AETHER_NEWS_RERANKING') or 'off'
        if news_reranking not in ('off','jev'):raise ValueError('Invalid news reranking policy')
        model_id=cfg.get('ARK_MODEL_ID') or 'doubao-seed-2.1-pro'
        context_setting=(cfg.get('AETHER_CONTEXT_TRIGGER_TOKENS') or 'auto').strip().lower()
        context_trigger=None if context_setting=='auto' else int(context_setting)
        context_policy(model_id=model_id,trigger_tokens=context_trigger)
        max_calls=int(cfg.get('AETHER_MAX_MODEL_CALLS') or '60')
        if not 1<=max_calls<=200:raise ValueError('Task model-call limit must be 1..200')
        return cls(model_mode=mode,model_id=model_id,base_url=base,api_key=key,max_model_calls=max_calls,
                   thinking=thinking,search_enabled=str(cfg.get("WEB_SEARCH_ENABLED","false")).lower()=="true",private_secrets=tuple(x for x in (cfg.get('TYPESAFE_API_KEY'),) if x),
                   library_path=cfg.get('AETHER_LIBRARY_PATH') or 'data/library',
                   memory_enabled=str(cfg.get('AETHER_MEMORY_ENABLED','true')).lower()=='true',
                   memory_encoder_path=cfg.get('AETHER_MEMORY_ENCODER_PATH') or 'local/models/potion-multilingual-128M',
                   memory_hybrid_enabled=str(cfg.get('AETHER_MEMORY_HYBRID_ENABLED','true')).lower()=='true',
                   judgment_provider=judgment_provider,judgment_mode=judgment_mode,judgment_policy=judgment_policy,context_trigger_tokens=context_trigger,
                   news_reranking=news_reranking,evidence_reader_enabled=str(cfg.get('AETHER_EVIDENCE_READER_ENABLED','true')).lower()=='true')

    def public(self):
        return {"model_mode":self.model_mode,"model_id":self.model_id if self.model_mode=="ark_plan" else "conversation-bridge",
                "configured":self.model_mode=="ark_plan" and bool(self.api_key),
                "search_enabled":self.search_enabled and bool(self.api_key),"base_url":self.base_url,
                'memory_enabled':self.memory_enabled,'judgment_provider':self.judgment_provider,
                'judgment_mode':self.judgment_mode,'judgment_policy':self.judgment_policy,
                'context_trigger_tokens':context_policy(model_id=self.model_id,trigger_tokens=self.context_trigger_tokens)['trigger_tokens'],
                'context_policy':context_policy(model_id=self.model_id,trigger_tokens=self.context_trigger_tokens),
                'max_model_calls':self.max_model_calls,
                'news_reranking':self.news_reranking,'evidence_reader_enabled':self.evidence_reader_enabled}

    def redact(self, value):
        text=str(value)
        for key in (self.api_key,*self.private_secrets):
            if key:text=text.replace(key,"[REDACTED]")
        return text
