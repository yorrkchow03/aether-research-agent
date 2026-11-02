# Monthly report baseline

This adds report diagnostics to the existing monthly-library v4. It does not
resolve the retained data rights gaps or change the business harness/model/tool
count. The actual product and the fixed-context study are different experiments.

## What is measured

- The real product pilot uses its planning, terminal, ResearchClient and existing
  report publication gate. All attempts and reviewer calls remain in its trace.
  Its archive-only request is prompt guidance, not a network isolation guarantee;
  the final source-lineage audit checks actual archive membership.
- The eight fixed-context cases use the same local Deep Agents and configured
  Doubao Pro Plan, with selected real captured records and a typed report tool.
  They have no data network, terminal, shared memory, Jev or outcome capability.
  They measure synthesis under supplied context, not retrieval quality.
- Mechanical checks cover eligible IDs, exact structured values/units, requested
  rubric fields and strict system-acquisition cutoffs. Missing denominators and
  failed/abstaining cases are retained. A correct ID is not semantic support.
- ISO dates absent from the supplied context are review candidates. Future
  scenario dates may be legitimate; these flags are not automatic truth grades.
- Semantic accuracy, causal rationale and the usefulness of advice need review
  against originals. Product AI review is not human ground truth. Fill the
  generated human_review.jsonl rather than reporting an invented accuracy.

The predeclared questions live in
`examples/evaluation/monthly_report_cases_v1.json`. This is a small diagnostic
pilot, with provisional AI-authored expected modes, not a statistically powered
held-out benchmark. Original rubric defects and failed schema submissions stay
visible. Schema-only follow-up is recorded separately from first-pass performance.

## Inspect the saved experiment without API calls

From the project virtual environment:

```sh
.venv/bin/python scripts/evaluation/run_monthly_report_baseline.py summary
.venv/bin/python scripts/evaluation/replay_monthly_report_baseline.py
.venv/bin/python scripts/evaluation/review_monthly_report_baseline.py
```

The private output folder is `local/report-baseline/monthly-v1/`. It contains
the original frozen inputs, labels, source hashes, model messages, first reports,
scores and failures; separate schema-only repairs; human review and machine
flags; actual product receipts and final source audit. The review copy places
Markdown in `product-report/reports/` and citations in `product-report/records/`
so numbered links resolve. These files are excluded from the public source ZIP.
Review summaries refresh automatic flags while preserving human annotations.

## Start a new bounded study

These commands intentionally use a NEW folder and incur authorized model calls
only at the run step. A populated monthly library, private `.env` with the
configured Pro Plan key, and pinned dependencies are required. Source-only
exports contain neither credentials, the dataset nor prior run outputs.

```sh
.venv/bin/python scripts/evaluation/run_monthly_report_baseline.py prepare --output local/report-baseline/monthly-next/frozen
.venv/bin/python scripts/evaluation/run_monthly_report_baseline.py run --output local/report-baseline/monthly-next/frozen
.venv/bin/python scripts/evaluation/review_monthly_report_baseline.py --output local/report-baseline/monthly-next
```

Prepare refuses to overwrite a study and pins the code and selected source
bytes. Run refuses changed code and never silently reruns existing case folders.
Use `--cases market derivatives` for a deliberately smaller subset. It retains
first submissions without correctness feedback. The adapter may retry a
transport failure once; failed-request billing can be unreported.

The actual product pilot is started once using
`scripts/dev/verify_monthly_report.py --start`; later invocations inspect state.
It refuses duplicate research while active. Its default receipt already exists
on the developer machine; choose an explicit unused `--output` for a new run.
Never rerun data collection or restart a healthy service just to inspect results.

## Costs, scope and next comparisons

Record per-case wall time, started requests, returned responses and provider
input/output usage, including failures and separate repair phases. Subscription
dollar cost is unknown; token counts are not a dollar invoice. Product traces
with autonomous research are not directly comparable to short supplied-context
cases. They cannot demonstrate a Jev speedup by themselves.

For future Jev classification, context-conflict selection and final checking,
keep question/source bytes, time constraints, context conditions and model
settings identical within each paired comparison. Predeclare semantic labels
and the failure/abstention rules before judging test outputs. No Jev experiment
or model training was run in this baseline task.

## Product fixes motivated by the pilot

Recipe registration accepts virtual `/notes/name.py`, shell `./notes/name.py`
and actual absolute paths inside the current session notes. External paths and
symlink escapes remain rejected. Shell and file-tool path conventions are
documented without imposing a fixed research recipe.

Full reports have an 8,192-token response cap; ordinary answers and the frozen
pilot retain 4,096. A response marked `finish_reason=length` is saved and blocked
before any of its partially parsed tools execute. This is an output limit,
not an increase or bypass of the account's Plan quota. Source publication checks
remain mandatory.
