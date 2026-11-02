# Historical evidence, task memory and Jev

Aether can select a fixed historical library without changing its local Deep
Agents/LangGraph harness, Doubao Pro/Agent Plan model, four skills or general
terminal workflow. No additional database service is needed.

## Select the library

The half-year interval is **[2026-04-26, 2026-10-26) UTC**, covering 183 days and
4,392 closed hourly observations per asset. Set the following privately in
`.env`, after building or restoring that library:

```dotenv
AETHER_LIBRARY_PATH=data/library-halfyear-20260426-20261026
AETHER_MEMORY_ENABLED=true
AETHER_JUDGMENT_PROVIDER=jev
AETHER_JUDGMENT_MODE=shadow
AETHER_CONTEXT_TRIGGER_TOKENS=48000
```

Restart an idle service and reload its browser page. Settings shows the actual
selected interval, coverage and semantic mode. A missing explicit library is a
configuration error; the application does not silently use a different period.
The retained monthly library still resolves old citation IDs.

Complete Bitstamp USD series and Coinbase USD series are independent datasets.
Coinbase has five missing hours on May 8; no interpolation or venue substitution
is used. OKX retains a shorter available history. Permissions, calendar dates,
provider retention and the cost of a native full-header scan leave explicit
source-specific gaps. A usable available-data snapshot is not complete required
core coverage. Consult the library coverage matrix before choosing metrics.

```bash
python -m research_data.library coverage --root data/library-halfyear-20260426-20261026
python -m research_data.library search --root data/library-halfyear-20260426-20261026 --query 'Ethereum upgrade' --limit 5
```

These are read commands. Acquisition and incremental updates remain explicit
manual commands; the upgrade installs no new schedule.

## Use long series from the terminal

```python
from research_agent.research_sdk import ResearchClient
client = ResearchClient()  # inside an active business-agent task
page = client.library_series('market.bitstamp.eth.hourly', limit=10000)
print(page['total'], page['collection_record_id'], page['data_file'])
```

`data_file` is a saved JSON page containing exact `records`. In the terminal,
resolve its virtual `/data/...` path relative to the workspace. Write a Python
recipe under `./notes/` and give the returned collection ID to the existing
`register_calculation` tool. The host re-executes the recipe, verifies each input
against its original library row and retains full citation lineage. A page is
not automatically the full query: inspect `next_offset` and
`query_result_fully_materialized`. Even a fully returned query can have missing
market observations; check coverage and continuity separately.

Registration replays the self-contained recipe in a fresh working directory
using declared JSON input arguments, so an accidental read of an exploratory
notes file fails immediately. This is a dependency/reproducibility check, not an
OS sandbox or proof of an appropriate financial method.

## Three distinct kinds of memory

| Layer | Stored content | Boundary |
|---|---|---|
| Long-term evidence | Original responses, document versions, publication/acquisition clocks, events, saved reports and citations | Summaries and prior answers are context; facts still require originals. |
| Task working memory | Goal, saved plan/todos, unknowns and pointers to full redacted tool-output artifacts | Tool output is not automatically promoted to a verified fact. |
| Correction experience | Process corrections backed by existing local evidence/review receipts | No invented trading pattern, winning rate or personal preference. |

Nonempty news queries use lexical relevance by default. Packing reads bounded
exact passages, retains update/dependency constraints and reports budget gaps.
Alternative MMR/coverage selection is available for experiments; it is not
assumed better. Results expose source IDs and excerpt offsets/hashes so the
agent can reopen original text before publication.

Native Deep Agents context compaction summarizes older model context at the
configured trigger. Summary calls use the same authorized Pro model, with their
own usage/latency events. Original checkpoint messages and offloaded history
remain available. Derived summaries are continuation notes, never new sources.
Task state can be inspected through the read-only
`/api/sessions/{session_id}/working-memory` route. Missing optional memory or
semantic services are reported without stopping independent source research.

## Jev is an optional public-evidence sidecar

`EvidenceJudgmentService` supports document classification, retrieval relevance,
document relationships and claim support. Relationships distinguish updates,
supersession, contradiction and different scope. Source text/hash, judged
excerpt/hash, criterion version, actual model, confidence, status, cache and
reported usage remain separate. Unknown/abstained/error never means a negative
label. Provider confidence is not calibrated accuracy.

The default `shadow` mode saves judgments while retaining deterministic evidence
selection and existing publication review. `assist` is an opt-in experiment;
`off` disables semantic calls. The source/number gate remains mandatory in every
mode. Jev is not a price predictor, an author of reports, or an authority on
publisher truth. It requires its own `TYPESAFE_API_KEY`, separate from Ark.

Calls use bounded snippets, request/time budgets, cache and failure breakers.
Retrieval judgments receive a fixed public topic descriptor rather than the
user's personal question. Personal/context evidence is excluded; ambiguous
personal statements skip the optional semantic step. Do not place private data
in a public evidence record.

Bulk classification is a separate read-only, resumable command:

```bash
python scripts/evaluation/classify_jev_documents.py --help
```

Inventory is the default. Explicit `--run` permits a bounded paid-provider batch.
Labels are separate annotations; originals are never overwritten. The delivered
24-document integration sample is a small sample, not whole-library completion
or a representative accuracy estimate. Missing key/permission/credit is a
recorded gap, not a reason to alter ordinary research.

## Time and evaluation

`observed` queries reconstruct historical observations using versions captured
later. `strict` queries additionally require acquisition by the cutoff. Capturing
an old article today cannot establish that this system possessed it earlier.
Raw provider files can contain dates outside the requested interval; only the
filtered evidence pack belongs in a frozen evaluation context. A general host
terminal is not an isolated benchmark sandbox.

The Jev/Pro pilot pins the same inputs and preserves format failures, abstention,
latency and unknown usage. The memory replay compares policies without new
model calls. Neither the small diagnostic samples nor software test counts
establish semantic accuracy, causality or an investment edge. Human labels and
a larger untouched evaluation remain necessary.

The [delivery acceptance record](../development/HALF_YEAR_ACCEPTANCE.md)
separates actual acquisition, live product attempts, offline replay and remaining
source/annotation limits.
