"""Agent Plan chat adapter. Deep Agents owns the planning/tool loop."""
import httpx
from langchain_openai import ChatOpenAI


class ArkPlanModel(ChatOpenAI):
    def _get_ls_params(self, **kwargs):
        return {"ls_provider":"ark_plan","ls_model_name":self.model_name,"ls_model_type":"chat"}


def create_ark_model(settings, *, max_output_tokens=4096):
    if not settings.api_key:raise ValueError("本机尚未配置 Agent Plan API Key")
    if max_output_tokens not in (4096,8192):raise ValueError('Unsupported bounded output limit')
    return ArkPlanModel(model=settings.model_id,api_key=settings.api_key,base_url=settings.base_url,
        extra_body={"thinking":{"type":settings.thinking}},max_tokens=max_output_tokens,max_retries=1,streaming=True,
        timeout=120,http_client=httpx.Client(trust_env=False))
