# Offline report-quality regression ledger

This is a small, reviewable development ledger for the report-quality upgrade.
It combines existing reports, selected archived evidence, two attributed Terra
reviews, and synthetic contract counterexamples. It is not a held-out quality
benchmark or a measurement of investment performance.

## Reproduce

From the project directory:

```sh
.venv/bin/python -m unittest discover -s tests -p test_report_quality_evaluation.py -v
.venv/bin/python scripts/evaluation/evaluate_report_quality.py
```

The script reads three selected existing report files and their archived
manifests, five selected archived evidence records, the two saved Terra review
artifacts, and the existing bounded event-context acceptance. It performs no
network or model request, does not execute saved calculation recipes, does not
scan the database, and does not rewrite the originals. Existing source and
context hashes are checked. The previous bounded context experiment is reused,
not rerun as a new independent experiment.

Outputs are under `local/report-quality/v1/evaluation/`:

- `case_manifest.json`: original report identities/hashes, attributed model
  labels, exact scope of each programmatic check, source-navigation comparison,
  synthetic cases and known limits.
- `receipt.json`: offline outcome, current software hashes, source-read counts
  and a copy of the separately reported external acceptance state.
- `acceptance_status.json`: fields for the new real business-harness Pro report
  and its later independent review. Both start at `pending`. Existing contents
  are preserved on rerun so a parent execution can update the artifact paths.

## What is verified

The real saved cases verify selected report/source hashes and source identities,
whether the old report's citation set includes the official Hoodi update,
stored publication/acquisition/creation-time ordering, exact structured numeric
values and units, and arithmetic over saved price endpoints. They do not verify
all hourly input rows, upstream price quality, ETF totals, or every report
sentence. Publisher publication times remain claims recorded by the source.

The saved newer EF document is demonstrably available before the old report was
created but absent from that report's citation set. Terra high and the targeted
xhigh rereview assess the summary omission and the treatment of the roadmap as
important. Those severity and semantic findings stay
`label_type=independent_model`, `human_ground_truth=false`. A model's original
`confirmed_issue` wording is retained as that model's status; it does not become
a programmatic fact label. The beta-language and ETF monitoring-window concerns
are recorded the same way, without automatic semantic repair claims.

The enhanced context's existing acceptance surfaces the official update as a
candidate needing original review. Its title, keyword match, later publication,
or model judgment does not establish that a distinct October 27 roadmap date
was cancelled. Candidate discovery is not recorded as successful semantic report
repair. The saved strict replay excludes the selected later-acquired versions.

Synthetic contract cases verify:

- `key_changes` enters factual review targets, source selection for audit, and
  exact numeric/unit checks. The audit check tests selected evidence IDs; it is
  not a complete new archive-publication experiment.
- An ordinary brief can remain informational without forced investment advice
  or a directional conclusion.
- Open monitoring questions use `research_action`. A quote-free conditional
  future check can pass that type while a factual observation cannot.
- Strict retrieval rejects a source version acquired after the cutoff; observed
  reconstruction flags that later acquisition.

Old stored review responses are parsed and their reports rendered with retained
review versions. They are not re-reviewed by a model or silently relabeled as
new reviews. Ten unit tests additionally cover hash tampering, false candidate
promotion, duplicate report/replay counting, pending external acceptance and the
separation of independent-model labels from human truth.

## Comparison and remaining acceptance

The recorded comparison is deliberately limited:

| Stage | Recorded result |
|---|---|
| Old saved example | Two independent-model reviews flag a newer official update omitted from the old report; citation membership and stored date ordering are programmatically inspectable. |
| Enhanced context | Existing bounded acceptance surfaces the official candidate, preserving uncertainty; this is source navigation. |
| New real Pro research report | Initially pending; provide report and execution receipt paths after a real business-harness run. |
| Independent review of new report | Initially pending; provide the actual later review path and reviewer identity. |

The parent execution can update `new_real_pro` and
`post_upgrade_independent_model` in `acceptance_status.json`. The script preserves
and copies those reported fields; it does not convert them into its own semantic
quality certification. Human labels remain a separate field and are zero unless
genuine human annotation has occurred.

There are three groups of existing report originals and zero new independent
report samples in this offline run. Multiple checks, repeat renderings,
offline replays, and a second review of preselected findings do not add samples.
Synthetic counterexamples are labeled separately and never inflate the real
report denominator. The cases were selected after inspection, so there is no
accuracy rate, statistical improvement, or causal performance claim. A later
fixed, event-separated evaluation with independent annotations remains future
work.
