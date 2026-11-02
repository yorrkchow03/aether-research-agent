# Jev and source-preserving memory

Aether still uses local Deep Agents/LangGraph and Doubao Pro to plan research,
read sources and write the report. Optional semantic services do not replace the
report author or the mandatory publication audit.

## Provider policy

`AETHER_JUDGMENT_POLICY=legacy` preserves the earlier construction.
`AETHER_JUDGMENT_POLICY=selective` enables the source-bound service factory when
the configured judgment provider is Jev:

- Classification and relevance use Jev; uncertain required dimensions can fall
  back to Pro under a shared, finite physical-request budget.
- Source relationships use Pro directly. A second duplicate final support
  checker is omitted; the mandatory Pro source/numeric/quote audit remains.
- Classification reopens the saved original. Its exact semantic input, raw
  source hash, model, criterion version and confidence policy bind the cache.
  A changed source/version is a different input. The provider text limit and
  truncation flag remain explicit; "original input" does not mean every
  character of an arbitrarily long source was classified.
- Classification stores persist across tasks. Dynamic relevance/relationship
  stores are fresh per job, so an old query's decision is not a new query's fact.

`AETHER_JUDGMENT_MODE=shadow` retains the ordinary evidence selection and records
the optional checks as diagnostics. `assist` may apply optional relevance and
relationship hints; it is a separate behavior change. An abstention is unknown,
not a negative label. Original and correcting/conflicting sources are retained.
The small confirmation experiment does not justify a general accuracy claim.

Changing a private setting requires restarting the local service. Do not change
configuration during a frozen experiment: its hash guard deliberately rejects
drift. Do not rerun the delivered primary protocol after updating source code;
use retained receipts for audit or freeze a separately named regression protocol.

## Reading long evidence

The same `read_evidence` capability accepts an optional query:

```python
read_evidence(record_id="page-<saved-id>", query="the exact funding condition")
```

It returns exact source slices, Unicode offsets, hashes, source clocks and
explicit omissions. An empty query reads the ordinary full saved record. A
focused view does not rebind the source ID or become a new publisher statement.
If the needed condition or counterevidence is omitted, expand the original.
Search-provider snippets remain snippets; focus does not turn one into a fetched
article. File-tool `/data` paths are terminal `./data` paths.

The mandatory auditor separately selects bounded claim-aware source context,
including the article's ending and relevant numeric/keyword spans. It preserves
title/URL metadata, exact segment boundaries and omitted ranges. A missing
passage in a bounded context cannot establish absence from the original.

## Task failure and recovery

Failed terminal exit codes are recorded as errors before task-memory updates.
Limited provider failures return to the repair loop, and genuinely saved reports
remain visible if later work is interrupted. The author gets a finite-budget
reminder; no program fabricates missing facts or silently rewrites its commands.

For the frozen report-study workflow, explicit recovery creates a new slot with
the same sources. Only source/record/code-hash-verified calculations can transfer.
Prior drafts and auditor feedback are derived guidance, not original evidence.
Hot actionable feedback points to hash-linked cold diagnostics and the latest
draft instead of repeatedly loading full old source contexts. Parsing failures,
omissions and unresolved issues remain explicit. Raw diagnostics remain readable.

The recovery CLI refuses active or uncertain primary runs, previously consumed
recovery slots, changed software/configuration, and output paths that would
overwrite a primary. It does not automatically retry or replace a failed score.
Existing evidence IDs and historical report links remain compatible.

See [confirmation and recovery results](../development/REPORT_CONFIRMATION.md)
for real task outcomes, resource estimates and independent AI-review limits.
