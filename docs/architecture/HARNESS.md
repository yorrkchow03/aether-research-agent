# Research harness

Current API-backed research exposes **12 business tools** and **8 framework tools**.
Historical/frozen studies keep their old restricted interface for reproducibility.
The model remains Doubao Pro through the authorized Ark Plan endpoint; Deep Agents
owns the planning and tool loop.

## Model-visible business tools

| Tool | Responsibility |
|---|---|
| list_data_sources | Discover source providers and normalized adapters |
| request_public_api | Flexible public GET with archived response/source ID |
| search_web | Fresh publisher/announcement search excerpts |
| fetch_webpage | Fetch and archive original public page text |
| read_evidence | Inspect saved record, source, times and integrity |
| register_calculation | Rerun a model-written recipe on saved inputs and record lineage |
| get_research_context | Read supplied preferences and labelled scenario defaults |
| get_private_portfolio | Read actual manual ledger/marks as private source evidence |
| recall_memory | Unified recall of news, prior reports and event revisions |
| record_news_event | Source-reviewed event creation/correction |
| submit_research_answer | Numeric/source-checked concise answer |
| publish_report | Numeric/source-checked report and evidence export |

Availability depends on current/live/search/reviewer configuration. Jev is not a
default model-visible tool. Its optional adapter remains available in the product
and requires its independent credential.

Framework tools: execute, ls, read_file, write_file, edit_file, glob, grep,
write_todos. Synchronous subagent delegation and the delete tool remain disabled.

## Skills and code

Four packaged skill directories are copied into each session workspace:

| Skill | Use |
|---|---|
| research | Market/news evidence, recall, interpretation and delivery |
| quantitative-analysis | Model-selected indicators and reproducible Python recipes |
| strategy-research | Baselines, experiments, temporal evaluation and costs |
| portfolio-analysis | Manual holdings, P&L, sizing and hypothetical stress |

Deep Agents adds skill names/descriptions to system context. The model reads full
instructions, references and scripts as needed. Continuing sessions rediscover
the skill list without discarding conversation history. Full skill files are
included in the software fingerprint and exported audit evidence.

Analysis libraries remain ordinary Python: simulation, metrics and
investment.strategies. Agents can inspect/import tested code and choose or
modify methods. The main prompt contains identity and source/publication rules;
methods, caveats and SDK examples are in skills.

## Terminal data access

ResearchClient and python -m research_agent.research_sdk access the active job's
application services. A short-lived local Unix socket connects the terminal to
the same ResearchTools instance used by the harness. The shell receives only
the socket path, never Ark/TypeSafe credentials. SDK data captures retain canonical
source receipts and input files; SDK activity is visible in the normal task trace.
The socket closes when the job ends or fails. Like the local shell, it assumes a
trusted personal OS user; it is not a multi-tenant security boundary.

The SDK offers data adapters, archive access, read-only Ethereum queries and
persistent experiment/preview helpers. Public provider paths and parameters remain
model-selected. Indicator logic and custom analysis execute in terminal Python.
Existing frontend APIs, saved experiments, simulations and accounting remain intact.

Publication and ledger writes are not SDK operations. Free code output becomes
citable only after registering its inputs, recipe and values. The independent
source reviewer still runs inside answer/report publication; it needs no separate
author-facing review_report tool.
