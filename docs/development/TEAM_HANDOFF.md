# Aether 团队开发交接

更新：2026-11-02。仓库提供可运行的完整源码，用于小组查看和协作。
真实凭证、历史数据库、持仓、模型权重和个人运行记录保留在各自本机。

## 开始运行

按根目录 [README](../../README.md) 安装依赖、复制配置模板，再运行：

```sh
python examples/framework/minimal_agent.py
python scripts/dev/check_framework.py
python -m unittest discover -s tests -q
python scripts/dev/check_repository.py
python scripts/start_agent.py --port 8765
```

最小示例、框架自检和测试使用合成资料或 mock transport，不调用付费模型。
配置文件如何填写见 [模型接入](../guides/MODEL_API_QUICKSTART.md)。
网页在无凭证、无历史资料时仍可打开；模型研究需要本机配置。
终端/SDK 面向 macOS/Linux；Windows 使用 WSL。

## 先理解主流程

**网页/FastAPI → AgentRuntime → 本地 Deep Agents/LangGraph → 豆包与工具循环
→ 数值及来源审核 → 回答/报告。**

每个实质任务先通过计划门禁。模型根据问题选择资料、调用工具或写 Python；
skill 提供方法指导，程序负责保存来源和执行发布核验。
发布审核是独立上下文的模型判断，仍可能出错。主流程的通用子 agent 委派关闭。

## 从哪里改

下表中简写的 Python 路径位于 `src/research_agent/`。

| 模块 | 入口 | 关注点 |
|---|---|---|
| 服务和会话 | `server.py`、`runtime.py` | API、任务、模型、middleware、checkpoint |
| 模型和总指令 | `settings.py`、`model.py`、`prompts.py` | 本机配置、协议、角色和发布边界 |
| 计划和 skills | `planning.py`、`skills/` | 先计划、按需读方法、按问题调整研究 |
| 工具和终端 | `tools.py`、`terminal.py`、`research_sdk.py` | 通用分析、来源留存、任务范围 SDK |
| 原文和事件记忆 | `knowledge.py`、`event_memory_index.py`、`hybrid_memory.py` | 原文版本、时间可得性、来源段落与检索 |
| 过程和任务记忆 | `experience_memory.py`、`working_memory.py` | 更正、任务恢复、热/冷上下文 |
| 条件研究和案例 | `research_explanations.py`、`case_memory.py` | 事实、假设、适用条件、反证、案例取得时间 |
| Jev 和路由 | `evidence_judgments.py`、`selective_judgments.py` | 公共来源分类/判断、缓存、弃权和 Pro fallback |
| 回答、报告和审核 | `answers.py`、`reports.py`、`review.py`、`audit.py` | 逐条引用、单位、原文审核与证据包 |
| 数据层 | `src/research_data/` | API 采集、归一化、历史库与覆盖缺口 |
| 产品扩展 | `web/`、`investment/`、`research_lab/` | 看板、手工账本与策略实验 |

## 当前能力与实验边界

- 业务研究继续使用本地 Deep Agents 和豆包 Pro。当前模型接口表保留 12 个
  业务工具；数值分析主要由普通 Python、现有 SDK 和四个 skill 承担。
- Jev 已有真实适配器、来源绑定缓存、分类/相关性及有限路由实验。
  条件保留检测有固定对照资料与实际比较，但仍是辅助实验，不替代最终来源审核。
- 新审核报告中的解释可保存为跨会话案例。检索展示匹配、不匹配和未知条件；
  案例不是新证据。后续结果需要独立来源和取得时间，不自动生成胜率或历史经验。
- 原始数据和新闻有不同频率、来源及覆盖缺口。仓库不包含作者的历史库，
  不应把干净副本的空数据状态当作采集失败。

## 开发约定

1. 先从 `AgentRuntime._run` 看组装方式，再进入负责模块。
2. 新指标和分析方法优先使用 Python 库或 skill；不要把每个步骤注册成工具。
3. 保留来源 ID、原始值/单位、版本、更正链及取得时间。未来资料不能进入历史查询。
4. 调整 Jev/memory 时先冻结同输入对照，分别报告效果、弃权、耗时和消耗。
5. 先做离线回归；真实模型和数据调用必须显式、有界。CI 不配置任何真实凭证。
6. 每个功能分支通过 PR 合并；不要把数据库或 `.env` 放入提交。

## 常见问题

| 现象 | 处理 |
|---|---|
| `ModuleNotFoundError` | 激活同一个虚拟环境并完成 editable 安装 |
| 没配置时能打开网页但不能研究 | 填写本机 `.env`，重启自己的服务 |
| `Local session token required` | 服务重启后刷新网页，使用新的本地会话 token |
| SDK 未连接 | `ResearchClient` 在活跃 agent 任务的终端内使用，不是独立模型 API |
| 提交被审核拒绝 | 按错误补证据、纠正单位或限制结论；不得改写原始来源迎合审核 |
| Unix socket 权限错误 | 测试执行器需允许本机 socket；不代表公网 API 故障 |

详细资料见 [文档索引](../README.md)。验收记录是带日期、输入及方法限制的记录，
不能用单次通过或多个模型一致代替人工真值与投资优势证明。
