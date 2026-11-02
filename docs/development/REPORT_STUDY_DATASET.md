# Frozen complete-report study inputs

The study moves from isolated Jev checks to complete, short English research
reports. It uses the existing local Deep Agents/Doubao author and compares three
configurations: `original_off`, `pro_checks`, and `jev_routed`. The dataset module
does not call any model or change the live product.

## Frozen package

`local/report-flow-study/v1/dataset/` contains 12 questions, 31 saved real source
records, complete normalized originals, exact initial research snippets, and
predeclared reference observations. Two cases are development pilots; ten are
confirmation cases. All source packs contain 2–4 distinct records. This is a
small convenience sample, not a general accuracy or investment-return claim.

The manifest SHA-256 is
`cf45279406ba5b78734914efcce548b4b21b65f53b0b30d8a55a9738f6f56feb`.
The package was frozen at `2026-10-29T04:43:03.538952Z` before provider runs.
The observed retrospective cutoff is `2026-10-28T23:59:59Z`; the material was
actually acquired on October 26–27. Earlier publication dates do not establish
that the system knew the material earlier.

| Case | Split | Research question family |
| --- | --- | --- |
| report-dev-01 | Development | EF reorganization and board roles |
| report-dev-02 | Development | Aave bug-bounty restructuring, reviewer concerns and governance conditions |
| report-confirm-01 | Confirmation | Native transaction assertions: mechanism, trust and proposed design |
| report-confirm-02 | Confirmation | WEBCAT, Clear Signing and mutable-state/adoption boundaries |
| report-confirm-03 | Confirmation | Platåberget testing and gas-repricing impact, without assuming mainnet readiness |
| report-confirm-04 | Confirmation | EF Q1/Q2 award totals and USD/quarter/publication scope |
| report-confirm-05 | Confirmation | SEC tokenized-stock exemption chronology and missing primary body |
| report-confirm-06 | Confirmation | Lido post-quantum pre-proposal versus exploratory contributor work |
| report-confirm-07 | Confirmation | Aave V3/V4 rate/cap units, entity scope and execution status |
| report-confirm-08 | Confirmation | MetaMask Staking precaution and staged governance execution |
| report-confirm-09 | Confirmation | Lido Earn Q2 coverage, ETH/share/USD units, control exceptions and accountability questions |
| report-confirm-10 | Confirmation | Simple DVT regular-cluster wind-down, completion and grant scope |

The old Jev48 and memory pilot case files were checked for exact source-ID
overlap. None of these 31 selected records occurs in the prior Jev dataset or
`local/halfyear/{jev,memory}/pilot-*/cases.json`. Previously retrieved V3.8,
CSM-bot, SSV-treasury and Q3 Earn candidates were rejected during preparation.
Event-family separation is also required between the two new splits. Common
publishers, Ethereum terminology and related protocol background still occur
across studies; this is not a claim of zero prior domain exposure. The split is
by event/source, not a chronological train/test split.

## Files and trust boundaries

- `sources.json`: keys are existing evidence IDs. Each entry contains the full
  `original_record`, a minimal `research_record`, Unicode span offsets, original
  and snippet text hashes, and the full original-record hash. Saved URLs,
  publication/acquisition clocks and the raw capture hash are retained. Private
  absolute capture/recipe paths are removed.
- `cases.json`: questions, source IDs, cutoff, shared research context, and the
  AI-authored reference rubric. The rubric includes 2–3 reference observations,
  exact supporting quotes, counterevidence, unsupported inferences and legitimate
  gaps. It is not independent human truth and is never author-model input.
- `model_inputs.json`: only task fields eligible for report generation. Both the
  source index and full-original reading capability must be identical across all
  three configurations.
- `blind_reviewer_input.json`: questions, actual initial research records and
  pointers to complete originals; no draft reference rubric, provider results or
  explicit split field. IDs retain their development/confirmation prefixes, so
  “blind” here means blind to draft answers and provider results, not blind to
  every property of dataset membership. Report-arm identities need separate
  anonymization in the later report-review packet.
- `protocol.json`: input/report contracts, split/selection disclosure, three
  configurations, pilot bounds and review/resource dimensions.
- `MANIFEST.json` and `MANIFEST.sha256`: hash-bind all five payload files.

Hashes prove that the saved evidence is unchanged. They do not establish that
every publisher assertion is true. In particular, the official SEC capture has
a title but a navigation-only body; it cannot independently establish every
legal condition reported in secondary coverage. Forum support is not approval,
a scheduled action is not execution, and a participant's question is not a
settled allegation. These are intentional real-source limitations, not missing
facts to fill with generated text.

The complete originals remain available, including caveats that a brief initial
snippet might omit. Reference observations are not an exhaustive list of every
valid report claim: additional observations must be checked against the actual
read source. Unread, unsupported or irrelevant details should not earn credit.

## Query integration

```python
from research_agent.report_study_dataset import FrozenReportCorpus, load_dataset

path = "local/report-flow-study/v1/dataset"
dataset = load_dataset(path)  # verifies manifest, spans, rubric quotes and clocks
corpus = FrozenReportCorpus(dataset)
task = corpus.task("report-dev-01")         # no rubric or split
index = corpus.source_pack("report-dev-01") # exact initial records
hits = corpus.search("board", case_id="report-dev-01")
record = corpus.read(hits[0]["record_id"], original=True, case_id="report-dev-01")
```

`load_dataset` returns `manifest`, `protocol`, `sources`, and `cases`.
The unified read-only query class exposes task, source-pack, lexical search and
evidence-ID reads, with optional per-case scope. The production harness can map
these records into an isolated knowledge store and let the LLM plan and read
through its existing SDK/terminal. The source raw hash can be checked again
against the original library when importing. No new per-indicator business tool
or mutable product-memory write is needed.

This experiment is bounded source-pack research, not unrestricted live web
discovery. The parent runner must isolate runtime state, prevent access to
rubrics/other cases, and save the actual prompts, context, source reads, terminal
output and tool results for every configuration. Equal starting snippets alone
are insufficient if one configuration has broader source or memory access.

## Running and interpreting the study

Start with one triad for `report-dev-01`; then finish the two development cases
before opening confirmation outcomes. Pilot reports have at most 12 author calls
and two mandatory review passes. Freeze the runner/model/threshold settings and
record changes before confirmation. The dataset does not itself spend credits.

Reports should contain 2–3 attributed source observations and one qualified
inference or useful question to watch, with source-ID citations. A trading
recommendation and a price prediction are not required. Judge source support,
material caveats, scope/units, temporal status, unsupported claims and useful
uncertainty separately from schema/quote/citation checks.

Record complete wall time, all author/checker/Jev calls, provider-reported token
and credit usage, source reads, retries, failures and publication outcomes. Do
not compare Jev-call latency with total baseline-report latency. Account plan
quota and estimated list-price equivalents must be distinguished from observed
charges. Keep every planned case in the denominator, including failed or empty
reports. One triad cannot establish a latency distribution or broad superiority.

Independent AI reviewers should first see anonymized reports and actual evidence
without the draft rubric. Reconcile their observations with the predeclared
rubric in a separate record. Do not describe that process as human certification
or use the report-writer model's self-score as independent validation.

## Preparation and verification

The package is already frozen. Re-running preparation at the same destination
raises an error. A genuinely new dataset version requires a new output path:

```sh
.venv/bin/python scripts/evaluation/prepare_report_study.py --output local/report-flow-study/v2/dataset
.venv/bin/python -m unittest discover -s tests -p test_report_study_dataset.py -v
```

Preparation reads only saved library records and performs no network/model
calls. Tests cover hash/slice identity, source metadata, quote visibility,
observed cutoff, source/family split isolation, read scope, blind task fields,
tamper rejection and the installed 12-case package. No frozen payload may be
edited to fit model results. Any later source/rubric correction must be saved
outside `v1/dataset`, identified by revision and affected cases, and included in
the final interpretation.
