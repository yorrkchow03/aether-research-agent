# Evidence memory: methods and evaluation

Memory has three distinct layers: LangGraph conversation checkpoints; immutable
source receipts and document/event versions; and earlier research reasoning.
Reviewed answer/report summaries help find earlier work but are not new factual
sources. User parameters remain separate. Follow original evidence IDs and dates.

## Selection

SQLite FTS first retrieves up to60 eligible news candidates. Local character
TF-IDF provides lexical relevance/similarity; no embedding API or language-model
training is required. Relevance, MMR and coverage selectors share candidate
inputs, estimated context budget and cutoff checks. Coverage uses a weighted
facility-location objective with greedy gain/cost packing. Same explicit URL or
exact repeated text reduces duplicate demand. Explicit saved event revisions
create atomic update bundles; missing updates exclude incomplete bundles.

This is a bounded heuristic, with no optimality claim for dependency constraints.
Similar wording does not prove independence, correction, contradiction or truth.
The UTF-8 cost estimate is not the provider's actual token usage. Inspect pool
limits/gaps and read original source text. Specific queries default to relevance;
coverage remains available for broad scans and research comparisons.

## Frozen source pilot v1

Private captured inputs live in `data/validation/memory_v1`. Short public labels
are in `examples/evaluation/memory_annotations_v1.json`. There are13 official
Foundation articles, twelve topic groups (companion posts grouped together),
four development groups and eight held-out groups. The39 probes comprise thirteen
fact questions, thirteen unsupported-premise/scope questions and thirteen
just-before-capture cutoffs. Source text/raw bytes, labels, code and relevant
test suites have immutable digests. All labels were authored by Codex and await
independent human adjudication.

These are current source snapshots, not twelve independently observed revision
histories or historical public-availability proofs. Topic grouping does not make
one publisher's sources statistically independent market events.

The first saved pilot measured factual required-passage recall:

| Selector | Development (5 questions) | Held-out (8 questions) |
|---|---:|---:|
| Recent receipt order |0/5|1/8|
| Lexical relevance |5/5|7/8|
| MMR |5/5|7/8|
| Coverage |3/5|5/8|
| Coverage without duplicate demand weighting |4/5|5/8|

Every policy blocked the target in all13 before-capture probes. Dependency
ablation is unidentifiable in this corpus because it lacks explicit real
correction links; synthetic tests check those separately. These results do not
show coverage outperforming relevance/MMR, and are not answer correctness or
investment quality. The original weaker results are retained. Because these
held-out results have now been inspected, future tuning needs a new untouched
confirmation set.

The matching offline manifest records77 contract checks in eight families, zero
failures. Replaying the frozen source tree reproduced the same selected evidence
and checks after the application had changed. The full application suite is a
separate167-test regression, not167 independent news/market events.

## Bounded paired Pro answers

`reports/product_evolution/paired_memory_v1` holds a preregistered four-case ×
three-selector experiment. All twelve outputs use local Deep Agents0.7.23 and
the same configured Doubao Pro plan, frozen candidates and context limit. The
author has no network, terminal, current shared memory or future outcomes. Each
case permits at most three model calls; the completed pilot used one per output.
Failures would remain in the common denominator rather than being retried until
success. Actual outputs, software/input snapshots, latency and provider usage
are saved. No LLM truth judge is used.

| Policy | Completed | Mode agrees with AI-authored rubric | Eligible/verbatim claim quotations | Author tokens | Mean latency |
|---|---:|---:|---:|---:|---:|
| Relevance |4/4|3/4|9/9|21,450|7.77s|
| MMR |4/4|3/4|8/8|21,876|9.69s|
| Coverage |4/4|3/4|6/6|21,260|7.67s|

Mode disagreement is a useful failure example: every Hegota answer said that
evaluating proposals did not establish universal adoption, but selected the
`answer` enum instead of the predeclared `reject_premise`. The original3/4 score
is preserved. This illustrates label/schema sensitivity; it is not a75% semantic
accuracy measurement. Eligible IDs and exact quotations also cannot establish
that all prose is supported. Human semantic correctness, appropriate abstention,
unsupported-advice rate, generalization and investment performance remain
unmeasured. Twelve outputs from four cases do not establish statistical gains.

## Reproduction

With an existing private frozen v1 capture and original software snapshot:

```bash
.venv/bin/python scripts/evaluation/replay_memory_benchmark.py \
  --output reports/product_evolution/memory_v1_replay_new
```

This copies verified frozen inputs/source into a disposable tree and never
modifies the running app. No keys, model calls or source refreshes are used. The
installed dependency runtime is still relevant; retain the environment lock.
Do not overwrite results or silently regenerate source dates.

A clean source checkout contains code/short labels, not private captured pages:

```bash
.venv/bin/python scripts/evaluation/prepare_memory_benchmark.py
.venv/bin/python scripts/evaluation/run_memory_benchmark.py --freeze \
  --output reports/product_evolution/memory_v1
```

Preparation fetches the thirteen official URLs once; preserve failures and check
the labelled passages. New captures form a new experiment, not a bit-identical
reproduction of these results. The freeze command preserves source and tests in
the result's software_snapshot directory before implementation changes.
For an explicitly requested paid paired run:

```bash
.venv/bin/python scripts/evaluation/evaluate_memory_answers.py \
  --output reports/product_evolution/paired_memory_new
```

Use a new output path and the existing private Pro plan settings. Do not rerun
automatically or select only successful attempts. Independently review blinded
answers against the prewritten passages before claiming semantic improvements.
