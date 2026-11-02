# Jev in complete research reports: development pilot

Date: 2026-10-29. This is a small development experiment, not a confirmation
benchmark, an investment-return study, or human certification.

## What was implemented

The experiment runs the genuine local Deep Agents/LangGraph business agent with
Doubao Seed 2.1 Pro on the authorized Agent Plan endpoint. Terminal/Python,
plan-first execution, original evidence reads, typed publication and mandatory
separate-context source review remain available. Codex does not write the
business reports. Production remains on its existing configuration.

Three arms separate the cost of adding checks from the choice of checking model:

1. `original_off`: original report flow, optional semantic checks off.
2. `pro_checks`: the same flow with Pro classification, retrieval/relationship
   checks and a pre-publication claim check.
3. `jev_routed`: the same optional checks, pinned Jev 1.14 first, with bounded Pro
   fallback for low-confidence/uncertain results. All required dimensions matter;
   an apparently certain topic does not certify the asset or event status.

The routed component retains both responses, caution flags, abstentions, model
identity and actual physical request counts. Conflicting/correcting sources are
preserved. A missing judgment is unknown, not negative evidence. Optional checks
never replace the mandatory source/numeric/quote publication audit.

These optional pre-publication checks are experimental behavior isolated in
`ReportStudyRuntime`; they are not silently enabled in the product.

## Fixed evidence and controls

The dataset has **12 questions and 31 saved originals**, with 2 development
questions and 10 confirmation questions separated by event family and source.
Sources used by earlier Jev/memory diagnostic cases were excluded. Complete
originals, exact common source slices, URLs, original IDs, publication/acquisition
clocks and hashes are preserved. The frozen manifest is
`cf45279406ba5b78734914efcce548b4b21b65f53b0b30d8a55a9738f6f56feb`.

Only the two development questions were run: EF organizational/board changes and
an Aave bug-bounty restructuring proposal with reviewer comments. Confirmation
reports have **not** been generated. The author receives no reference labels.

Every attempt has fresh task memory, provider caches and a case-only source
store. The host SDK rejects external capture and sources outside that case.
macOS child-command guards deny IP networking, other SDK sockets and private
project stores; only the task's exact SDK socket is allowed. The host supplies
policy inline, so modifying an inspectable workspace policy file cannot relax
later commands. This is a scoped study guard, not a general proof against
arbitrary hostile code. Production's authorized terminal behavior is unchanged.

The pilot allows 12 author responses, 2 mandatory review requests, and a
900-second task bound. Routed semantic calls allow at most 12 primary Jev and
8 fallback Pro requests. No unlimited retry or manual report rewriting was used.

## Observed results

| Development question | Arm | Task outcome | Wall seconds | Pro HTTP requests | Jev HTTP requests | Nominal Pro AFP | Estimated Jev USD |
|---|---|---|---:|---:|---:|---:|---:|
| EF structure/board | Original | Completed | 112.87 | 11 | 0 | 59.63775 | — |
| EF structure/board | Pro checks | Completed | 232.47 | 26 | 0 | 80.66025 | — |
| EF structure/board | Jev routed | Completed | 185.44 | 16 | 12 | 61.86000 | 0.000709926 |
| Aave bounty proposal | Original | Failed before publication | 53.52 | 12 | 0 | 69.38000 | — |
| Aave bounty proposal | Pro checks | Completed | 253.78 | 24 | 0 | 115.19200 | — |
| Aave bounty proposal | Jev routed | Completed | 194.10 | 21 | 10 | 91.90000 | 0.000941598 |

There are **5 completed tasks out of 6 planned pilot slots**. The Pro-check arm
published two versions for the second question; both are retained, so six saved
reports do not mean six independent successful tasks. The original second task
spent its author budget troubleshooting virtual `/data` versus terminal `./data`
paths and a long offloaded source, before attempting publication. Its short failed
latency is not a speed advantage. No rescue run has been substituted for it.

Across all six attempts: **110 Pro requests and 22 Jev requests**. The nominal
Pro usage estimate is **478.63000 AFP**, and the saved-list-price Jev estimate is
**USD 0.001651524**. Usage was returned for these requests. AFP and USD are
different units, not a combined cash bill. Actual account debits are unobserved.
Estimates use the separately documented 2026-10-28 pricing snapshot; no unverified
cache discount or subscription amortization is assumed.

All six published versions passed saved source-ID/structured numeric contracts
and exact mandatory-review quote replay. Structured numeric validation does not
by itself verify numbers written only in prose. A separate Terra reviewer first
inspected the sources without provider reports, then reviewed anonymous reports
without arm mappings. All six published versions were judged adequate within
the saved-source scope, with no material errors/omissions identified. The empty
failed task remains unassessable, not a zero-error report. Role attribution and
turning future evaluation into a binding condition were noted as minor cautions.
This is independent **AI review**, not human gold or publisher-truth validation.

## Interpretation and next gate

In these two pilot questions Jev routing took less time and nominal Pro usage
than adding the same checks entirely with Pro. However, the original completed
EF report was faster than both checking arms. Conservative fallback adds calls;
these results do not establish that Jev makes the whole product faster or more
accurate. They also do not establish general accuracy parity.

The original EF run preceded the two concurrent checking arms; all three Aave
arms ran concurrently. Remote provider caching and contention are unmeasured.
Development code additions changed whole-tree software fingerprints, with each
actual snapshot retained. These are feasibility observations, not a clean
immutable-software confirmation run or a statistically reliable latency sample.

The confirmation gate is not passed. Before opening the 10 confirmation cases:

1. Calibrate a common author/recovery budget on development cases. Preserve the
   first-attempt failure and any future continuation separately.
2. Evaluate selective, reusable Jev annotations for classification/event state
   and cached memory, rather than assuming every real-time check should route
   through Jev. Keep uncertain sources, important updates and Pro final review.
3. Freeze the shared harness, routing thresholds, cache policy, run order and
   cost accounting; then execute confirmation without tuning on its outcomes.

## Reproduction and receipts

See [dataset design](REPORT_STUDY_DATASET.md) for input preparation. Local study
files live under `local/report-flow-study/v1/` and are intentionally excluded from
the public source export:

- `dataset/`: immutable inputs and manifest.
- `attempts/`: exact software snapshots, checkpoints, events, provider receipts,
  drafts, published versions and per-attempt resource summaries.
- `reviewer-terra/`: independent source/anonymous report review.
- `review-dev01/` and `review-dev02/`: anonymous inputs; arm mapping and mechanical
  audits are stored separately and withheld from the reviewer.
- `PILOT_SUMMARY.json`: all planned pilot outcomes, including failure and versions.

```bash
# No provider calls: local guard probe, requires macOS sandbox-exec.
.venv/bin/python scripts/evaluation/check_report_study_isolation.py

# Real authorized provider calls: choose a NEW attempt directory explicitly.
.venv/bin/python scripts/evaluation/run_report_flow_study.py run \
  --case report-dev-01 --arm jev_routed \
  --output local/report-flow-study/v1/attempts/report-dev-01/jev_routed/attempt-02

# No provider calls: recompute a completed attempt's accounting.
.venv/bin/python scripts/evaluation/run_report_flow_study.py summary \
  --output local/report-flow-study/v1/attempts/report-dev-01/jev_routed/attempt-01
```

The example real-run command is opt-in, not a request to rerun already completed
attempts. Existing frozen inputs and completed attempts are never overwritten.
No new schedule, provider purchase, order, Git push or public deployment was made.

Verification: **681 offline tests passed**; the real macOS socket/network/file
guard probe passed, including policy-file tampering. The shared source factories
preserve production defaults; routing and study publication behavior are opt-in.
