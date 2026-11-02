# Conditional research and case memory

Substantive reports can connect saved observations to a qualified explanation,
its applicability and the evidence that would require another review. The local
Deep Agents harness and Doubao Pro remain responsible for the research. Source
capture, numerical checks and mandatory publication review retain their roles.

## Reading a report

The optional **Explanations and conditions** section shows:

- **Observed basis:** sourced facts, separate from the interpretation.
- **Working interpretation:** a hypothesis about what those facts mean.
- **Scope and horizon:** the timeframe, asset and event stage being discussed.
- **Applicability:** prerequisites and source qualifications.
- **Alternative explanations and counterevidence:** when supported by sources.
- **Revisit conditions and uncertainties:** how the explanation could fail and
  what remains unknown.

A weak long-term trend and a short-term deviation from an average can coexist.
The agent should explain their different scopes. Alternatives are optional;
there is no requirement to manufacture a second view or a buy/sell score.
Simple factual answers and all historical drafts remain compatible.

## Publication checks

`ReportDraft.explanations` and `AnswerDraft.explanations` accept up to three
structured `ResearchExplanation` entries. Every nested claim retains its own
source IDs and exact numeric annotations. Availability, publication, observation,
period-end and acquisition clocks are checked throughout derived-input lineage.

The mandatory reviewer assesses each nested claim using only its eligible source
IDs, and separately checks the complete chain, horizon, context labels and
uncertainties. A quote for one observation cannot approve another observation
with a different source. The existing 90-target preflight bound rejects an
oversized draft before a provider request. Review is an inspectable AI assessment,
not an independent guarantee that the publisher or interpretation is correct.

## Historical cases

Successfully reviewed explanations are retained as immutable research cases.
Each keeps its publication/review origin, asset, horizon, matching labels,
prerequisites, invalidation, limitations and original-source fingerprints.
Case IDs are navigation pointers; use the original evidence IDs for citations.

The case acquisition time is assigned by the host. Republishing identical
reasoning does not crowd recall slots: eligible duplicates are grouped while
their original records and publication origins remain readable. Private
portfolio/user-context lineage is rejected from shared research cases. Current
library quarantine applies even to evidence previously copied into memory.
Case reads also show dated status pointers for concerns, source-backed reviews
and replacement reports on the originating report. Raw personal feedback text
is not copied into the public research case, and feedback is not market evidence.

Later outcomes require separately sourced, host-recorded events. They are not
invented from a price forecast, a successful publication or a similar news item.
Outcome text and success labels never influence retrieval scores. Negative,
mixed and inconclusive observations remain available; they establish neither a
win rate nor a causal investment advantage.

Strict recall checks actual acquisition **before** ranking. Explicit retrospective
reconstruction is labeled and cannot prove what the system knew historically.
No existing historical report or missing outcome is backfilled by this feature.

## Querying from the existing terminal

```python
from research_agent.research_sdk import ResearchClient
client = ResearchClient()
packet = client.research_cases(
    "Hoodi",
    context={"asset": "ETH", "conditions": {"event_status": "delayed"}},
    budget_chars=16000,
)
for item in packet["results"]:
    print(item["case_id"], item["match"], item["case"]["evidence_ids"])
```

Use labels actually supported by the source. Retrieval reports matched,
mismatching and unknown conditions. Its transparent score combines keyword
coverage, supplied-label matches, horizon similarity and asset match; the weights
are navigation heuristics, not a trained financial model or calibrated probability.

The SDK sends one bounded representation and skips whole cases that do not fit,
keeping conditions intact. Reopen a returned case with
`client.research_cases(case_id=case_id, outcome_limit=4, outcome_offset=0)`;
`outcome_page.next_offset` supports later pages. The SDK cannot create cases,
write outcomes or alter clocks. The existing `recall_memory(kind="cases")` and
read-only `/api/memory/cases` routes expose the same underlying store.

## Jev condition diagnostics

The new experiment uses the existing `support` assessment with the distinct
`condition-preservation-v1` criterion identity. It tests event status, eligibility,
horizon, corrections, acquisition cutoff, fictional user context, certainty and
scope. It is explicit, optional evaluation; it adds no automatic second final
checker to ordinary reports. Mandatory Pro review remains required.

The public fixture has 16 synthetic cases in eight paired variations. Predeclared
labels and exact condition spans are readable; an independent AI review found
clock and wording issues in the first draft, preserved in the initial experiment.
Version 1.1 corrects these before the revised diagnostic. Old outputs are never
rescored as new successes. These are development diagnostics, not held-out real
news accuracy or human-labeled ground truth.

Run offline contracts without network:

```sh
python scripts/evaluation/evaluate_conditions.py --output local/condition-check-new
```

Explicitly run a paid, bounded matched comparison using configured credentials:

```sh
python scripts/evaluation/run_condition_comparison.py --output local/condition-comparison-new
```

Both providers receive the same eligible source bytes, question and criteria.
Each case has one first attempt. Errors, abstentions and an empty-source host
abstention stay in the planned denominator. Exact quotation transport and label
agreement are reported separately; missing quotations do not count as verified
condition preservation. Latency and actual token usage are saved. Jev USD and
Pro Plan AFP estimates use separately identified units; account debits remain
unknown. See [acceptance results](../development/CONDITIONAL_RESEARCH_ACCEPTANCE.md).
