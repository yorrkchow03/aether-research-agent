# Plan-first research

Current research uses the existing Deep Agents `write_todos` tool, with a per-job
LangGraph middleware check in `research_agent/planning.py`. The system prompt
requires objective/scope, actionable steps and completion checks. The model
chooses the sources and methods; no additional planner model or business tool is
introduced.

Before a successful plan save, the agent may read skills/preferences, inspect
source metadata/user context or submit a clarification-only question. Evidence
retrieval, terminal execution, research file writes, calculations and substantive
publication are blocked. The tool error asks the model to save a plan and retry;
the blocked handler has no research side effects.

A plan and research emitted in the same model turn cannot authorize each other:
research sees the pre-turn checkpoint and must wait for the next model turn.
Only the actual successful `write_todos` state update authorizes that job. A new
question has a new job ID, so an old session plan must be refreshed. Submission
repair within the same job reuses its plan. Completed plans need an active-step
update before further research.

The model is instructed to follow the plan, keep statuses honest and revise it
when evidence or blockers change the approach. The middleware enforces ordering
and basic plan structure; it cannot prove a plan is insightful or every step was
performed correctly. Existing source/review publication checks remain separate.
Plans are checkpointed and `plan_saved`, `plan_updated`, `planning_required`
events provide a reviewable trace. This is not an operating-system sandbox.

Historical/frozen and conversation-bridge harness behavior is unchanged. No
additional skill, data adapter, dependency, model, portfolio or UI changes are
needed. Pure clarification remains possible without a plan; substantive research
uses a short plan even for a small request.

Run `python -m unittest discover -s tests -p test_planning.py -q` for offline
ordering/checkpoint tests. `python scripts/dev/verify_planning.py --start` explicitly
starts at most one real Pro task; later invocations inspect the saved same job.
The live receipt is private under `reports/planning/` and is excluded from source
delivery. It verifies integration rather than investment quality.
