# Jev validation and usage

Open **Advanced Research / Strategy Lab → Jev validation & usage**. The panel
only reads saved statistics; refresh never launches an experiment or model call.
It shows first-attempt comparisons, task-level results, development/confirmation
partitions, usable coverage, unknown usage and separate post-study diagnostics.

## Role in the product

Jev is an optional source-judgment service at three points:

1. Source intake: asset, topic and event-status classification.
2. Context: relevance, prompt-instruction detection and document relationships.
3. Final review: whether cited text supports a specific statement.

The current deployment remains **shadow**. A low-confidence or failed judgment
does not silently erase sources or replace numeric/source publication checks.
Doubao Pro still writes answers/reports inside local Deep Agents/LangGraph.
The separate static multilingual encoder retrieves source passages on local CPU.

## Price and measured result

Official prices checked2026-10-28: `jev-1.14.0` costs **USD0.042 per million input
tokens**; outputs are free. Use the actual `usage.input_tokens` from each response.
Multiple questions share the same state; do not multiply usage by question count.
See [models](https://docs.typesafe.ai/models) and
[shared questions](https://docs.typesafe.ai/cookbooks/parallel_questions).

The sealed48-case first comparison used86,781Jev input tokens, a published-price
estimate of **USD0.003644802**. An additional contract preflight brings the round's
49Jev inference requests to **USD0.003699402**. Account debits and taxes were not
independently checked. The models-list GET is recorded separately.

Doubao uses the authorized **Agent Plan**, not a cash pay-as-you-go endpoint.
The published coefficient estimate is
`AFP = (input_tokens × 2.5 + output_tokens × 2.5) / 10000`.
No independent cache discount was verified, so cached-token subsets are retained
and never deducted using an unrelated pricing table. Cash price and actual debit
remain unknown. See [AFP rules](https://docs.volcengine.com/docs/ark/agent-plan-personal-afp-credits-billing-rules?lang=zh).
The account usage API requires separate AccessKey authentication; an inference
key alone does not provide that ledger.

The original48streaming Pro responses did not report usage. A separate direct
nonstreaming probe and four adapter checks returned usage: a **partial2.10625AFP
estimate** for those five requests only. The earlier48remain unknown. Semantic
judgment calls now use nonstreaming transport; business report streaming remains.

## Fixed evaluation

The owner's fixed dataset has48cases across12event families, with16development
and32confirmation cases. It stores actual source excerpts, original text, spans,
hashes and acquisition clocks. The sources and event families do not cross the
split. There are40real-excerpt cases and8transparent controlled counterexamples,
36English questions and12Chinese questions over English evidence.

An independent Terra AI reviewer labelled all84dimensions before provider
outputs were available. Fourteen ambiguous dimensions were preexcluded from
single-label scoring. The70remaining dimensions are provisional AI references,
not human gold. Original84draft labels and68mutually agreed dimensions are kept
as sensitivity checks. Errors and abstentions remain in the scored denominator.

On the32confirmation cases, both providers matched39/44scorable dimensions.
Jev's usable coverage was36/44; Pro's was42/44. First-call median adapter latency
over48cases was1.54sJev versus3.55sPro. This small diagnostic does not establish
statistical superiority, calibrated confidence, report quality or investment value.

## Reproduce explicitly

Use separate output directories; old studies are immutable. Preparation and
replay are local. `run` explicitly issues paid/plan requests, capped by protocol.

```bash
# Prepare a new frozen dataset from an installed saved library.
.venv/bin/python scripts/evaluation/prepare_semantic_benchmark.py --help

# Obtain and freeze an independent source review before provider comparison.
.venv/bin/python scripts/evaluation/run_semantic_benchmark.py prepare \
  --dataset local/my-fixed-set --review-file local/my-blind-review.json \
  --output local/my-semantic-study
.venv/bin/python scripts/evaluation/run_semantic_benchmark.py run \
  --output local/my-semantic-study

# The owner first study predates the transport/rounding repair: replay its
# own hashed code with the HTTP client blocked, rather than rewriting its results.
.venv/bin/python scripts/evaluation/replay_semantic_benchmark_snapshot.py \
  --output local/jev-validation/v1/comparison-v1
```

The source export includes builders, scorer, replay code and synthetic unit
fixtures. Actual captured documents, private provider receipts and credentials
remain outside the upload package. The owner dataset is under
`local/jev-validation/v1/dataset/`; use its manifest rather than collecting a new
moving window and calling it the same study.

## Repairs and remaining limits

- One Jev response had two-decimal probabilities summing to0.99. The first study
  retains the error. A new Decimal feasibility check can accept bounded rounding
  while keeping raw values/choice/confidence unchanged. Raw rounded scores are
  not exact normalized probabilities and must not be used for Brier/calibration.
  Saved-response-only reparse changed one case from error to abstained because
  its topic confidence stayed below0.8; no new inference was made.
- The new12-question memory set exposed lost answer sentences despite higher
  source recall. Preserving anchored lexical slices alongside semantic hints
  changed passage retention from8/12to12/12in a post-hoc replay; baseline was
  11/12. This is a repaired development diagnostic, not an untouched confirmation.
- Broad/multilingual queries, incomplete relationship classes, selective
  abstention, counter-evidence retention and generated-report quality still need
  larger fresh assessments. No corpus-wide automatic Jev tagging/filtering or
  assist promotion is justified by this small study alone.
