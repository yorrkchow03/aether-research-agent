# Architecture

The loopback FastAPI application serves the dashboard and local APIs. Deep Agents and LangGraph run the research harness with a configured Doubao model, tools, a checkpointer and conditional evidence-reading subagents.

Public source capture preserves original bytes, IDs, clocks and hashes. Time and asset eligibility run before passage retrieval. Keyword retrieval and optional local embeddings select snippets; optional Jev assistance can judge public-source relevance. Task state, corrections and source memory support later work without treating old model answers as new facts.

For report requests the author stores a typed draft. Program checks cover source bindings, numbers, units and calculation replay. A separate review context checks source support using the generation model or optional Jev. Normal revisions are bounded; the final attempt reduces scope to supported facts. Unsupported results stay explicit.

Portfolio records and strategy research are local services. Market refresh, new training and model workshops are explicit actions. There is no broker connection or trade execution. The local shell runs with the operator's account permissions; see [Security](../../SECURITY.md).
