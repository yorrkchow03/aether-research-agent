# Fixed semantic benchmark dataset

This dataset is a predeclared diagnostic for the optional Jev/Pro judgment
services. Classification addresses document organization; relevance and relation
address intermediate context management; support addresses final claim checking.
The four task contracts are the existing `QUESTIONS` in `evidence_judgments.py`.
They are not additional agent business tools.

## Construction and provenance

The initial private freeze contains48cases across12event families, with12cases
per task. Four May2026families form development; eight August–October families
form confirmation. Event families and original source IDs cannot cross those
splits, including controlled copies and excerpts. All family source dates are
before the fixed archive cutoff. Actual acquisitions occurred later and remain
visible: this is not a simulation of what the agent historically knew.

Source material comes from the owner's already captured six-month public
archive. Sixteen original records support26distinct model inputs. Originals are
copied separately with their complete normalized text; actual model excerpts
retain exact character offsets and SHA256bindings. Raw publisher-object hashes
are checked by the library before preparation. Input excerpts stay below the
adapter's12,000character limit; no hidden truncation can change the task.

Some ordered comparison statements, an incomplete reference, duplicate copies
and a prompt-injection suffix are controlled text. They have `fixture-` IDs,
their own text hashes, no invented publication dates and explicit non-publisher
provenance. They test defined semantic contracts and must be scored separately
from real excerpt cases. They do not establish that a publisher contradicted,
withdrew or corrected an actual announcement. The benchmark never imports those
fixtures as product evidence or writes them into the research library.

## Labels, language and review

Every case freezes `expected` choices for every requested dimension: three for
classification, two for relevance, one each for relation and support. Options
and rationale are authored before reading any provider result. Current labels
are AI-authored expectations, not independent human ground truth. The independent
reviewer receives `reviewer_input.json`, containing only task, question, actual
model excerpts and question criteria. It contains no draft gold, split,
construction rationale or provider result. Disagreements require source-based
adjudication, and any post-freeze correction must have a separate version/receipt.

Some questions are Chinese while the actual evidence remains English. Case
`language` describes the question; `evidence_language` describes the supplied
material. Report these conditions separately, not as proof of multilingual
superiority. Relation labels include duplicate, supersedes, update, contradicts,
different_scope, unrelated and insufficient, but rare labels have very small
denominators. Classification does not cover every possible topic/event-status
label equally. Nothing here proves open-domain accuracy or investment value.

Older Hoodi-upgrade/zkAPI pilot examples and provider responses were not used
to select confirmation cases. The PERCH family happens to test on the Hoodi
network, but it concerns an independent mump2p evaluation, not the upgrade event.
The confirmation set is frozen before the new provider comparison; its small,
AI-selected nature prevents calling it an independent human-labelled benchmark.

## Files and use

- `protocol.json`: task criteria, freeze time, counts and limitations.
- `originals.json`: actual saved original normalized records.
- `sources.json`: exact excerpt/fixture construction and model-visible records.
- `cases.json`: questions, split/family/language and predeclared expectations.
- `memory_queries.json`: twelve source-navigation queries with exact targets.
- `reviewer_input.json`: blind label-review input, without draft gold/results.
- `MANIFEST.json` / `MANIFEST.sha256`: binding hashes for every frozen payload.

```bash
.venv/bin/python scripts/evaluation/prepare_semantic_benchmark.py
.venv/bin/python scripts/evaluation/prepare_semantic_benchmark.py --verify
```

Preparation refuses an existing output directory. `load_dataset(path)` verifies
all file hashes, current question criteria, exact original/excerpt/fixture
bindings and split isolation. `case_payload(case,sources)` returns only
`task`, `question`, `evidence` for either provider, excluding labels and split.
The fixed corpus/threshold/actual normalized request hashes must be identical
across provider conditions. Each transport's request schema still differs and
must be recorded as a comparison limitation.

Memory queries are a separate offline retrieval target set, not classifier or
generated-report accuracy. Their current narrow corpus and source-linked wording
may create easy matches. Measure source/span recall, context bytes and latency
with the same source pool and budget; do not merge these scores into Jev labels.
No provider API, credential file, live search or new schedule is used by this
preparation module. Frozen originals and benchmark artifacts stay private and
are excluded from the public source export.
