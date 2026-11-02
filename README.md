# Aether

A local ETH/BTC research assistant for our project team. It combines market data
and original news sources into cited research reports that help readers make
their own decisions.

## What is implemented

- **Research:** local Deep Agents / LangGraph with Doubao Pro, web search,
  public data APIs and terminal/Python analysis.
- **Evidence and reports:** saved originals, source links, numeric/unit checks,
  separate source review, explicit explanations and their applicability limits.
- **Memory:** versioned news, task state, corrections and conditional research
  cases recalled across sessions with acquisition-time filters.
- **Product:** English dashboard, research chat, reports, local brief schedules,
  a manual portfolio and an optional Strategy Lab.
- **Experiments:** optional Jev classification/checks and reproducible comparisons
  of evidence selection, report quality, latency and usage.

Jev is auxiliary; mandatory publication review remains in place. Historical
coverage has documented gaps, and simulations do not establish an investment
advantage. The product does not place trades.

## Run locally

Use Python 3.12–3.14 on macOS/Linux; local verification uses Python 3.14.
Windows users should use WSL.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
cp .env.example .env
python scripts/start_agent.py --port 8765
```

Open <http://127.0.0.1:8765/>. Configure your own local `.env` before using model
research; see [model setup](docs/guides/MODEL_API_QUICKSTART.md).
The checkout opens with empty local data and portfolio state. Credentials,
downloaded datasets, model weights and personal research history are not shared.

The optional [team dataset Release](docs/guides/DATASET_SHARING.md) provides a
separately checked half-year research snapshot and a local restore command.

## Repository layout

```text
src/research_agent/   Harness, memory, publication, API and web dashboard
src/research_data/    Data collectors, normalization and historical library
scripts/             Start service; data, audit, evaluation and development tools
tests/               Offline tests and synthetic fixtures
examples/            Minimal framework demo and reproducible evaluation inputs
docs/                Architecture, usage, team guide and dated research records
contracts/           Optional audit-anchor prototype
```

## Develop and verify

```sh
python examples/framework/minimal_agent.py
python scripts/dev/check_framework.py
python -m unittest discover -s tests -q
python scripts/dev/check_repository.py
```

These checks do not call paid model APIs. Add analysis methods as Python code or
skill guidance; preserve source IDs, timestamps and publication checks.

Start with the [team handoff](docs/development/TEAM_HANDOFF.md),
[documentation index](docs/README.md) and [contribution guide](CONTRIBUTING.md).
Project code is shared for team collaboration; no open-source license is assigned.
