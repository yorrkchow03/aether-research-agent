# Architecture

The loopback FastAPI application serves the dashboard and local APIs. Deep Agents and LangGraph run the research harness with a configured Doubao model, tools, a checkpointer and conditional evidence-reading subagents.

Public source capture preserves original bytes, IDs, clocks and hashes. Time and asset eligibility run before passage retrieval. Keyword retrieval and optional local embeddings select snippets; optional Jev assistance can judge public-source relevance. Task state, corrections and source memory support later work without treating old model answers as new facts.

For a new periodic report with a resolved time window, the agent records four stages: discovery, verification, drafting and review. It reads eligible preferences and follow-ups from prior reports, then delegates a source survey for the current window. The main agent chooses topics from that survey and records specific questions, evidence, unresolved gaps and failed attempts in a separate ledger. Reader findings and tool results update that ledger so later turns can reuse evidence or change strategy. A continued report carries forward its original scope, progress and historical leads.

The optional Jev reranker receives a public research question, the candidate passage and the host-resolved asset and date bounds. It uses the returned classification and confidence to adjust passage order within the eligible set. The report still goes through an independent review context.

For report requests the author stores a typed draft. Program checks cover source bindings, numbers, units and calculation replay. A separate review context checks source support using the generation model or optional Jev. Normal revisions are bounded; the final attempt reduces scope to supported facts. Unsupported results stay explicit.

The local model bridge can supply author, reader, reviewer and context-compaction replies through separate files for an opted-in report session. The harness still executes the tools and validates publication. See the [paired-run evaluation](RESEARCH_MEMORY_EVALUATION.md) for the measured context and quality results.

Portfolio records and strategy research are local services. Market refresh, new training and model workshops are explicit actions. There is no broker connection or trade execution. The local shell runs with the operator's account permissions; see [Security](../../SECURITY.md).
