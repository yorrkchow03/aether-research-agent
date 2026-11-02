# Verification

For teammate onboarding run `python examples/framework/minimal_agent.py` and
`python scripts/dev/check_framework.py`. The latter checks a real offline graph,
plan ordering, packaged skills, checkpoints, the current tool surface and clean
ASGI application state. HTTP transport is blocked during the check; no provider
credential or personal state is copied. The receipt is saved in ignored
`logs/framework-check.json`. See [team handoff](TEAM_HANDOFF.md).

`python -m unittest discover -s tests -q` requires no key or paid API. Temporary synthetic fixtures exercise the historical SDK, source eligibility, reports and actual Deep Agents bridge loop. Mock transports verify wire contracts, not live-provider operation.

Investment tests cover FIFO cash/fees, unknown basis, atomic duplicate/conflict imports, reversible voiding, fractional times and unavailable marks. Changing future observations cannot change earlier features, selection or predictions. Tests check training-label availability, execution delay, common windows/costs and invalid histories. API tests cover clean empty startup, CSRF, accounting rollback, CSV and partial-book sizing.

For live acceptance: refresh data, run Strategy Lab, inspect source lineage, download JSON/summary, and ask local Pro to inspect that saved run. Harness success does not prove investment skill. Jev needs its separate key; absent credentials remain unverified and must not be replaced by an Ark judgment labelled Jev.

Inspect desktop/mobile layouts, validation and source modals in the real browser. Use a disposable installation for transaction UI acceptance; never insert fake holdings into the user's actual ledger.

The source export checks an allowlist, configured-key literals and common credential patterns. Its relative-path checksum manifest helps review but is not an exhaustive secret detector. Fresh install/start/tests must work without copied `.env`, data or runtime. Audit-chain dependencies are optional and locked separately.

Harness SDK tests exercise an actual Unix socket and terminal subprocess, source
capture, a custom calculation and numeric publication checks, cancellation,
credential redaction, socket cleanup, unified recall and the exact reduced tool
set. macOS/Linux must permit local socket creation for these offline tests; a
restricted executor may need permission to create local connections.

The earlier product-evolution milestone contained 167 offline tests. Those cases cover
conditional advice, numeric units, actual vs unknown personal context, historical
availability and input lineage, correction bundles, duplicate demand, memory
identity, persistent inbox/CSRF, report diffs and frozen benchmark integrity.
These are software contracts, not 167 independent investment events.

See [memory evaluation](../guides/MEMORY_EVALUATION.md) for frozen-source replay
and paired Pro answers. Real paid integration is explicit via
`scripts/dev/verify_product_evolution.py --start` and
`scripts/dev/verify_experience.py --start`; each pins one job and never silently
restarts a failed/completed run. Inspect later without --start. Keep failed
drafts, provider usage and independent-source limitations in the record.
