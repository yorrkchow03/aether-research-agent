"""Ark built-in search: credentials are sent only to the authorized Plan domain.

Returned citation excerpts are search-provider evidence. The secondary model's
generated search summary is never treated as the original webpage.
"""
import hashlib
import json
from urllib.parse import urlsplit
import httpx
from research_data.http import utcnow
from research_data.normalize import timestamp
from .settings import PLAN_BASE_URL


def citation_items(response):
    """Extract provider annotations, not links invented in generated prose."""
    found={}
    for item in response.get("output",[]):
        if item.get("type")!="message":continue
        for block in item.get("content",[]):
            for entry in block.get("annotations",[]):
                if entry.get("type")!="url_citation":continue
                url=entry.get("url","")
                parsed=urlsplit(url)
                if parsed.scheme not in ("https","http") or not parsed.hostname or parsed.username or parsed.password:continue
                if url not in found:found[url]=entry
    return list(found.values())


class ArkWebSearch:
    def __init__(self, settings):self.settings=settings

    def search(self, query, max_results=5, time_range="OneMonth"):
        if not isinstance(query,str) or not 1<=len(query.strip())<=500:raise ValueError("搜索词长度必须为 1～500")
        if not 1<=max_results<=10:raise ValueError("返回条数必须为 1～10")
        if time_range not in ("OneDay","OneWeek","OneMonth","OneYear","Any"):raise ValueError("Unsupported time range")
        if self.settings.base_url!=PLAN_BASE_URL:raise ValueError("Unapproved search endpoint")
        labels={"OneDay":"最近一天","OneWeek":"最近一周","OneMonth":"最近一个月","OneYear":"最近一年","Any":"不限时间"}
        prompt=(f"搜索请求，当前UTC时间 {utcnow()}。查询：{query}。时间要求：{labels[time_range]}。"
                f"请使用联网搜索返回最多{max_results}个相关来源。优先原始公告，列标题、发布时间、URL。"
                "保留搜索引用，不能自行编造日期、URL或内容。没有材料就明确说没有。")
        body={"model":self.settings.model_id,"input":prompt,"tools":[{"type":"web_search","sources":["doubao"]}],
              "max_tool_calls":1,"max_output_tokens":900,"thinking":{"type":"disabled"}}
        with httpx.Client(timeout=90,trust_env=False) as client:
            response=client.post(PLAN_BASE_URL+"/responses",headers={"Authorization":"Bearer "+self.settings.api_key},json=body)
        try:obj=response.json()
        except ValueError:raise RuntimeError(f"联网接口返回非JSON内容，HTTP {response.status_code}") from None
        if response.status_code!=200 or obj.get("error"):
            error=obj.get("error",{})
            raise RuntimeError(self.settings.redact(f"联网查询失败，HTTP {response.status_code}，{error.get('code','')}：{error.get('message','')}"))
        if not any(i.get("type")=="web_search_call" and i.get("status")=="completed" for i in obj.get("output",[])):
            raise RuntimeError("模型接口没有实际完成搜索，拒绝把普通模型回答当作联网结果")
        raw=json.dumps(obj,ensure_ascii=False,sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest();retrieved=utcnow()
        entries=citation_items(obj)
        retained=[]
        for entry in entries:
            published=entry.get("publish_time")
            if published:
                try:
                    if timestamp(published)>timestamp(retrieved):continue
                    if timestamp(published).year<=1970:published=None
                except (ValueError,TypeError):published=None
            retained.append({**entry,"publish_time":published})
        return {"response":obj,"raw":raw,"sha256":digest,"retrieved_at":retrieved,"query":query,"time_range":time_range,
                "items":retained[:max_results],"provider_status":obj.get("status"),"usage":obj.get("usage",{}),
                "note":"时间要求是发给搜索模型的检索提示，不是API硬过滤；发布时间来自搜索引擎，尚未独立核验。引用摘要不等于原站完整正文。"}
