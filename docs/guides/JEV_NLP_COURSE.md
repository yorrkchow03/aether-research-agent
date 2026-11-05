# Jev NLP evidence processing study

This separate experiment compares document classification, context relevance and
relationships, and claim support with Jev 1.14, Doubao Pro and fixed lexical rules.
It does not replace the product's Deep Agents harness or mandatory publication audit.

## What is included

- `examples/evaluation/jev_nlp_course_v2.json`: 48 frozen cases, 80 dimensions,
  ten fresh event families, exact public-source excerpts and controlled fixtures.
- Four June/July development families (20 cases) and six October confirmation
  families (28 cases), excluding previously used study sources and event families.
- Independent pre-provider AI references, strict whole-case gating, development-only
  routing, cached graph packing and exact context budgets.
- Bounded request ledgers, immutable first responses, interrupted-submission guards,
  client-cache timing, stage/packing ablations and 24 fixed-Pro RAG answers.

Labels and anonymous answer ratings are AI judgments; **human gold labels: 0**.
This is a small controlled diagnostic, with deliberately artificial stress cases.

## Inspect and replay without model keys

After installing the repository dependencies:

```sh
python scripts/evaluation/prepare_jev_nlp_course.py \
  --verify --output examples/evaluation/jev_nlp_course_v2.json
python -m unittest discover -s tests -p 'test_jev_nlp*.py' -q
```

The report's companion bundle contains `experiments/study-v3/` with frozen inputs,
first responses, source snapshots, original reference reviews, timing trials and
generated answers. From this source checkout, point to that directory:

```sh
python scripts/evaluation/run_jev_nlp_course.py analyze \
  --output /path/to/bundle/experiments/study-v3
python scripts/evaluation/summarize_jev_nlp_course.py \
  --output /path/to/bundle/experiments/study-v3
```

Both commands are offline. Frozen software hashes must match; use the source
version supplied with the bundle when later code changes. Analysis rewrites its
derived `ANALYSIS.json` timestamp, so preserve original deliverables and use a
copy when replaying. Fixed generation pins its analysis inputs and will reject
resume after that file changes.

## New provider runs

Only `development`, `test`, `timing` and `generate` submit provider requests.
They require separately authorized TypeSafe and Doubao credentials in your local
`.env`. A new run must first freeze its dataset and independent review with
`prepare`; do not reuse a delivered run directory. `calibrate` seals development
gates before `test`. `adopt-development` supports an explicitly versioned,
payload-identical development revision, preserving paid usage and raw receipts.

The request plan uses `jev-1.14.0` and `doubao-seed-2.1-pro` on the authorized
`/api/plan/v3` endpoint. One transport attempt, a 45-second operation timeout,
three-consecutive-error stop and 160-request-per-provider bound prevent hidden
retries. Warm timing is cache-only. Jev's estimated USD stop is separate from
Doubao's nominal AFP quota accounting; actual cash debits remain unknown.

## Main results and limits

On 28 confirmation cases, raw reference agreement was 45/47 dimensions for Jev
and 41/47 for Pro, with whole-case acceptance 21/28 and 26/28. Pro's two rejected
classification responses contained correct labels but invalid probability sums;
their primary failures were preserved. Jev had a connection failure and a
low-confidence relevance disagreement. The six-family macro-F1 difference
interval includes zero.

Jev graph packing retained 24/24 required source-bound quotes, compared with
19/24 for lexical packing and 17/24 for Pro graph packing. Disabling protection
also retained 24/24: this dataset does not establish an added protection benefit.
In the fixed-Pro writing probe, Jev contexts produced six complete answers but
only four supported answers; Pro contexts produced five supported and five
complete answers. Source retention is not a guarantee of grounded writing.

Client-observed median request time was 1.900 seconds for Jev and 2.940 for Pro.
Network paths differ, and replayed routing is not live-agent throughput. The full
collection observed 134 Jev attempts and 158 Pro attempts, including generation.
Known list-price usage was USD 0.008627934 for Jev, with one unknown-usage attempt,
and 67.12625 nominal AFP for Pro. These are different units, not a cash-cost ratio.
