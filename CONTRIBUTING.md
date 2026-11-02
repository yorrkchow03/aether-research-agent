# Contributing

New teammates: read [TEAM_HANDOFF.md](docs/development/TEAM_HANDOFF.md), run
`python examples/framework/minimal_agent.py` and
`python scripts/dev/check_framework.py`, then make changes at the documented
extension points. These entry points do not require a key or real model call.

Use Python 3.12–3.14 and an editable installation from the repository root. The exact lock was captured on macOS / Python 3.14; CI is configured to check the declared Python versions on Linux; local verification used Python 3.14 / macOS. Run `python -m unittest discover -s tests -q`. Tests use synthetic temporary fixtures and mock network transports; they do not call paid APIs or import personal data.

Keep research methods optional. Preserve public API exploration and local Python tools. Every published factual or numeric claim needs eligible saved evidence, units, timestamps and lineage. Missing values remain unknown. Future observations must not change earlier predictions or enter earlier training labels. Add counterexample tests for changes to accounting, time eligibility or evaluation.

Frontend code lives in `src/research_agent/web/`; investment modules in `src/research_agent/investment/`. Check changed JavaScript with `node --check`, then inspect the local dashboard at desktop and mobile widths. Document external integrations that have not been verified live.

Never commit `.env`, runtime/checkpoint/ledger databases, downloaded data, model artifacts or exported private reports. `python scripts/dev/export_source.py` creates a reviewed allowlist export. A source export is not a backup of personal data. Do not enable paid-service fallbacks or public hosting implicitly.
