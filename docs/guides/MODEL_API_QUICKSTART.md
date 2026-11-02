# 豆包与 Jev 接入速查

项目主 agent 使用豆包；Jev 是独立的可选语义判断服务。真实密钥通过团队私下分享，在各自本机填写，不放入共享源码。

## 在项目内运行

将根目录 `.env.example` 复制为 `.env`，填写两个独立的 key：

```dotenv
AGENT_MODEL_MODE=ark_plan
ARK_API_KEY=填豆包的key
ARK_MODEL_ID=doubao-seed-2.1-pro
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3
ARK_THINKING=disabled
WEB_SEARCH_ENABLED=true
TYPESAFE_API_KEY=填TypeSafe的key
JEV_MODEL=jev-latest
JEV_MIN_CONFIDENCE=0.8
```

依赖安装见 [团队交接](../development/TEAM_HANDOFF.md)。配置后执行 `python scripts/start_agent.py --port 8765`，打开 `http://127.0.0.1:8765/`。已启动的服务修改配置后需要重启。

## 单独调用豆包

- OpenAI 兼容协议；SDK 的 `base_url` 为上面的 `ARK_BASE_URL`。
- 完整请求地址：`POST https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions`。
- 请求头：`Authorization: Bearer <豆包key>`。
- 主要参数：`model`、`messages`；当前项目关闭 thinking。
- 保留 `/api/plan/v3`，普通 `/api/v3` 不属于本项目使用的套餐端点。

在仓库根目录执行以下 Python 代码（会产生真实调用）：

```python
from dotenv import dotenv_values
from openai import OpenAI

cfg = dotenv_values('.env')
client = OpenAI(api_key=cfg['ARK_API_KEY'], base_url=cfg['ARK_BASE_URL'])
response = client.chat.completions.create(
    model=cfg['ARK_MODEL_ID'],
    messages=[{'role': 'user', 'content': 'Say hello in one sentence.'}],
    extra_body={'thinking': {'type': 'disabled'}},
    max_tokens=128,
)
print(response.choices[0].message.content)
```

豆包负责研究、问答与报告。新闻搜索仍是 harness 的工具调用，并非仅凭一次普通聊天请求自动联网。

## 单独调用 Jev

- 请求地址：`POST https://api.typesafe.ai/v1/systemone`。
- 请求头：`Authorization: Bearer <TypeSafe key>`、`Content-Type: application/json`。
- 主要参数：`model`、`state`（材料）、`questions`（判断题与标准）。
- 默认 `jev-latest`；2026-10-23 的真实适配器测试实际返回 `jev-1.14.0`。

```python
from dotenv import dotenv_values
import httpx

cfg = dotenv_values('.env')
response = httpx.post(
    'https://api.typesafe.ai/v1/systemone',
    headers={'Authorization': 'Bearer ' + cfg['TYPESAFE_API_KEY']},
    json={
        'model': cfg.get('JEV_MODEL') or 'jev-latest',
        'state': 'A maintenance update is planned. Its completion is not confirmed.',
        'questions': {
            'completed': {
                'type': 'noul',
                'instructions': 'Does the supplied text explicitly confirm completion?',
            }
        },
    },
    timeout=40,
)
response.raise_for_status()
result = response.json()
print(result['model'], result['answers'], result['usage'])
```

Jev 返回类型化判断和概率，不生成研究报告。本项目已有 `investment/jev.py` 适配器；分类、上下文筛选和结论核验三处自动闭环仍需后续实验开发。真实接通测试仅证明鉴权、调用和一个合成事件判断成功，不证明各类任务的准确率。比较实验需固定实际模型版本，并记录返回模型、耗时和 token 用量。

官方接口：[TypeSafe System One](https://docs.typesafe.ai/api/system-one)。
