# Fixed-source report confirmation and task recovery

Date: 2026-10-29. The 10 reserved questions were run once per arm, with the local
Deep Agents/LangGraph business harness and authorized Doubao Seed 2.1 Pro Plan
endpoint. Jev was pinned to 1.14.0. Coding agents did not author business reports.

## Primary results

| Flow | Completed tasks / planned | Adequate completed / planned | Needs revision | Completed-task median, including preparation |
|---|---:|---:|---:|---:|
| Original, optional semantic checks off | 10/10 | 9/10 | 1 | 168.70 s |
| Matched optional checks with Pro | 9/10 | 7/10 | 2 | 204.40 s |
| Selective Jev with conservative Pro fallback | 9/10 | 9/10 | 0 | 181.45 s |

Adequacy is an independent anonymous **AI** assessment against saved sources,
not human gold, prediction accuracy or verified external truth. The two failed
publications remain unassessable, rather than zero-error reports. Across all
30 planned slots there were 28 publications: 25 adequate and 3 needing revision.

For the nine completed matched pairs, the median Jev-minus-Pro-checks difference
was **-10.19 seconds**. Against the original flow it was **+44.51 seconds** over
nine completed pairs. These paired differences are different from subtracting
the separate medians. The experiment does not establish product-wide speedup,
accuracy equivalence or a causal advantage: there is one stochastic attempt per
heterogeneous question, remote caching is unobserved, and three concurrent jobs
share providers.

The revisions concerned treating a proposal as adopted and omitting gas/network
limits, attributing existing browser behavior to a prospective wallet feature,
and omitting a material three-month eligibility condition. Exact source links
and quote replay passed even for these reports. A valid citation is not by itself
proof that the statement is supported; the semantic judgments remain recorded.

## Resources and controls

| Flow | Pro HTTP attempts | Jev HTTP attempts | Nominal Pro AFP | Jev USD estimate |
|---|---:|---:|---:|---:|
| Original | 135 | 0 | 895.85025 | No request |
| Pro checks | 214 | 0 | 1063.85225 | No request |
| Selective Jev | 197 | 46 | 940.79975 | 0.003860724 |

The total primary experiment used **546 Pro and 46 Jev attempts**. Reported usage
was available for those attempts. AFP is a nominal Agent Plan resource estimate,
not USD or a cash debit; no unverified provider-cache discount is assumed. The
Jev estimate uses the separately saved 2026-10-28 list-price snapshot. Actual
account debits remain unknown. Coding and independent AI-review account usage
are separate from these business-provider figures.

Development/setup was not free or hidden: 9 development attempts consumed
164 Pro and 14 Jev attempts, with a known partial estimate of 871.70775 AFP and
USD 0.001161384. One development timeout had unknown usage. These development
costs are separate from the primary comparison and are not amortized away.

Inputs retain the original frozen manifest
`cf45279406ba5b78734914efcce548b4b21b65f53b0b30d8a55a9738f6f56feb`.
Confirmation protocol SHA-256:
`57e3265104a5249d81b66d7066bf8f580bbf02e1ca6b0fade19e71174270333a`.
The **actual primary software** fingerprint was
`4f5e714cdad9836115978d211a291bd5850be1617d71eb1a50132bf49d1ae32c`;
its snapshot is retained with every attempt. Subsequent production fixes do not
retroactively change this benchmark.

The protocol fixed 30 slots, rotated arm launch order, cold per-case/arm caches,
three workers, 24 author responses, four mandatory review requests, 120-second
transport timeout and one SDK retry. The host author/review/compaction client
was capped at 56 HTTP attempts; optional single-attempt services had separate
limits. "No automatic retry" in the controller refers to task-level replacement;
the limited transport retry was explicitly included in the frozen policy.

All three arms had the same source inventory, optional focused-source read
interface and mandatory source/numeric/publication review. Jev was used for
classification and relevance, with uncertain decisions routed to Pro; complex
relationships went directly to Pro. A second, duplicate final support gate was
removed from both checking arms. Mandatory publication review remained.

## Independent recovery, not replacement

The two failed primary slots were the complex quarterly-funding reports. Each
was recovered once using the same frozen sources, its prior draft, actual internal
Pro feedback and a hash-verified prior calculation. Each recovery used a fresh
runtime and directory with 12 author responses and four review requests. Neither
reference labels nor independent reviewer ratings were supplied to the author.

Both recoveries published successfully and were independently rated adequate;
both receipts verified that the original primary files were unchanged. Thus all
30 requested reports eventually became available, while primary completion stays
**28/30**. Recovery used 36 additional Pro attempts, no Jev attempts, and a nominal
270.28300 AFP. It does not erase the three primary content revisions or become an
extra confirmation question.

Recovery also exposed a practical memory cost: copying full old diagnostic
contexts caused 4 and 5 native compaction responses. The delivery separates hot
actionable feedback from hash-linked cold diagnostics; its separate offline
memory-size checks must not be described as a newly confirmed latency saving.
The read-only comparison reduced initial eager input from 214,810 to 19,188
characters for the Jev recovery and 120,163 to 19,640 for Pro. Thirteen and six
complete feedback items respectively remain in cold storage with explicit read
pointers. This is an initial character-volume result, not measured total tokens,
cash saving or faster report completion; original cold conditions remain intact.

## Implemented operational improvements

- Framework `exit_code` artifacts now mark failed terminal commands as errors
  before working-memory recording. Error text cannot fake an exit code.
- Virtual-path failures explain `./data` and `RESEARCH_WORKSPACE`; arbitrary
  commands are never silently rewritten.
- Limited provider errors can return to the author repair loop. Saved outputs
  and checkpoints remain visible on interruption.
- Focused reads return exact original slices, offsets, hashes, omission markers
  and full-original pointers. They do not replace the original evidence ID.
- Classification reopens the actual original instead of caching a query-shaped
  snippet. Nine real classification receipts replayed across new service
  instances with zero new provider requests. This verifies reuse correctness,
  not a warm-report speed benchmark.
- The optional production selective service keeps source-bound classification
  caches, task-local dynamic checks and a shared Pro fallback/check budget.
  Default publication/source controls and terminal flexibility remain.
- A one-slot recovery API/CLI preserves failed primaries and only migrates
  calculations with checked source leaves, code and raw hashes.

A separate post-confirmation repair addresses mandatory-auditor context:
the earlier auditor saw only the first 14,000 characters and omitted title
metadata, even when the author had read a true total near the end. Claim-aware
exact excerpts and explicit omissions are tested separately. These changes are
production engineering follow-up, not a retuned score on the ten held-out cases.
A bounded real Pro audit replayed two unchanged observations previously blocked
by this omission. Both became supported when their actual source endings and
metadata were visible, using one Pro request (18,904 reported tokens). The final
deterministic selector produced exactly the same context as that replay; no
additional model call was needed for the hash-seed ordering repair.

## Evidence and reproducibility

Private receipts are under `local/report-flow-study/v2/`:

- `confirmation/PROTOCOL.json`, `STATE.json`, `SUMMARY.json`: fixed inventory and
  all primary outcomes; no silent replacements.
- `confirmation/attempts/`: exact software, original sources, calculations,
  messages, raw provider receipts, publication audits and resource summaries.
- `confirmation/reviews/`: anonymous per-question inputs and separate host maps.
- `reviewer-terra/`, `reviewer-terra-b/`: ratings frozen before mappings opened.
- `CONFIRMATION_ANALYSIS.json`, `RECOVERY_SUMMARY.json`: recomputable comparisons
  with primary failure and secondary recovery denominators kept separate.
- `trace-audit/FINAL_PRIMARY.json`: all 30 complete message histories and all
  30 case-local source/hash checks passed. No explicit reference-label or other
  store/socket access was observed. Sixteen dynamic commands remained beyond
  the static proof. The OS guard does not comprehensively prohibit framework
  source/scripts/tests access, so absence of target leakage is **not certified**.

No new automatic schedule, purchase, trading order, Git push or public deployment
was made. The private corpus, credentials, model weights and receipts remain
outside the clean source export. Existing historical evidence IDs remain valid.

## Delivery verification

The delivery passed **793 offline tests**. The local service continues using
Doubao Pro on `/api/plan/v3`; its optional policy is selective with **shadow**
selection, while mandatory audit remains enabled. Credentials and the data
library were retained. Its release software fingerprint is
`a53b937cf1d951d226e1b5e194c6f9a21911a8aad57a24ed6da7b762394a1838`.
This release includes the separately tested source-context and hot/cold-memory
fixes; it is not presented as the exact binary of the earlier primary benchmark.
