"""Agent Plan chat adapter. Deep Agents owns the planning/tool loop."""
import httpx
from langchain_openai import ChatOpenAI
from .model_context import DEFAULT_OUTPUT_TOKENS, documented_context_capacity


class ArkPlanModel(ChatOpenAI):
    def _get_ls_params(self, **kwargs):
        return {"ls_provider":"ark_plan","ls_model_name":self.model_name,"ls_model_type":"chat"}


def create_ark_model(settings, *, max_output_tokens=DEFAULT_OUTPUT_TOKENS):
    if not settings.api_key:raise ValueError("本机尚未配置 Agent Plan API Key")
    if isinstance(max_output_tokens,bool) or not isinstance(max_output_tokens,int) or not 1024<=max_output_tokens<=65536:
        raise ValueError('Output limit must be 1024..65536 tokens')
    capacity=documented_context_capacity(settings.model_id)
    return ArkPlanModel(model=settings.model_id,api_key=settings.api_key,base_url=settings.base_url,
        profile={'max_input_tokens':capacity} if capacity else None,
        extra_body={"thinking":{"type":settings.thinking}},max_tokens=max_output_tokens,max_retries=1,streaming=True,
        timeout=120,http_client=httpx.Client(trust_env=False))
