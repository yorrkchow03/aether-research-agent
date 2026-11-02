"""Private server-side settings; never expose credentials in session metadata."""
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values

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
    max_model_calls:int=40
    library_path:str='data/library'
    memory_enabled:bool=True
    judgment_provider:str='jev'
    judgment_mode:str='shadow'
    judgment_policy:str='legacy'
    context_trigger_tokens:int=48000
    memory_encoder_path:str='local/models/potion-multilingual-128M'
    memory_hybrid_enabled:bool=True

    @classmethod
    def load(cls, root:Path):
        cfg=dotenv_values(root/".env")
        key=cfg.get("ARK_API_KEY") or ""
        mode=cfg.get("AGENT_MODEL_MODE") or ("ark_plan" if key else BRIDGE_MODE)
        base=(cfg.get("ARK_BASE_URL") or PLAN_BASE_URL).rstrip("/")
        if mode not in ("ark_plan",BRIDGE_MODE):raise ValueError("Unknown model mode")
        if base!=PLAN_BASE_URL:raise ValueError("当前只使用已授权的 Agent Plan 地址 /api/plan/v3")
        thinking=cfg.get("ARK_THINKING") or "disabled"
        if thinking not in ("disabled","enabled"):raise ValueError("Invalid thinking configuration")
        judgment_provider=cfg.get('AETHER_JUDGMENT_PROVIDER') or 'jev'
        judgment_mode=cfg.get('AETHER_JUDGMENT_MODE') or 'shadow'
        if judgment_provider not in ('jev','doubao','lexical','disabled'):raise ValueError('Invalid evidence judgment provider')
        if judgment_mode not in ('off','shadow','assist'):raise ValueError('Invalid evidence judgment mode')
        judgment_policy=cfg.get('AETHER_JUDGMENT_POLICY') or 'legacy'
        if judgment_policy not in ('legacy','selective'):raise ValueError('Invalid semantic service policy')
        context_trigger=int(cfg.get('AETHER_CONTEXT_TRIGGER_TOKENS') or '48000')
        if not 12000<=context_trigger<=120000:raise ValueError('Context trigger must be 12000..120000 tokens')
        return cls(model_mode=mode,model_id=cfg.get("ARK_MODEL_ID") or "doubao-seed-2.1-pro",base_url=base,api_key=key,
                   thinking=thinking,search_enabled=str(cfg.get("WEB_SEARCH_ENABLED","false")).lower()=="true",private_secrets=tuple(x for x in (cfg.get('TYPESAFE_API_KEY'),) if x),
                   library_path=cfg.get('AETHER_LIBRARY_PATH') or 'data/library',
                   memory_enabled=str(cfg.get('AETHER_MEMORY_ENABLED','true')).lower()=='true',
                   memory_encoder_path=cfg.get('AETHER_MEMORY_ENCODER_PATH') or 'local/models/potion-multilingual-128M',
                   memory_hybrid_enabled=str(cfg.get('AETHER_MEMORY_HYBRID_ENABLED','true')).lower()=='true',
                   judgment_provider=judgment_provider,judgment_mode=judgment_mode,judgment_policy=judgment_policy,context_trigger_tokens=context_trigger)

    def public(self):
        return {"model_mode":self.model_mode,"model_id":self.model_id if self.model_mode=="ark_plan" else "conversation-bridge",
                "configured":self.model_mode=="ark_plan" and bool(self.api_key),
                "search_enabled":self.search_enabled and bool(self.api_key),"base_url":self.base_url,
                'memory_enabled':self.memory_enabled,'judgment_provider':self.judgment_provider,
                'judgment_mode':self.judgment_mode,'judgment_policy':self.judgment_policy,'context_trigger_tokens':self.context_trigger_tokens}

    def redact(self, value):
        text=str(value)
        for key in (self.api_key,*self.private_secrets):
            if key:text=text.replace(key,"[REDACTED]")
        return text
