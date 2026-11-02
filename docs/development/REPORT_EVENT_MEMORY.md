# Report event and source context

`research_agent.report_event_memory` is a read-only, optional context builder for
research reports. It helps the report author find saved event revisions, source
versions, explicit disagreements, and potentially newer entity-related material.
It does not change source records, introduce a business tool, call a model, or
perform internet requests.

## Integration

```python
from research_agent.report_event_memory import build_report_event_context

context = build_report_event_context(
    knowledge,
    selected_news,                     # Existing evidence rows or IDs
    query="Ethereum upgrade schedule",
    as_of=research_cutoff,
    availability_mode="observed",      # Use "strict" for acquired-by-cutoff replay
    max_events=6,
    max_sources=12,
    previous_report=actual_report_id,   # Optional; resolved from saved research
    related_queries=[{"query": "Hoodi", "source": "ethereum"}],
)
```

The public signature is:

```text
build_report_event_context(knowledge, sources, *, query='', as_of=None,
    availability_mode='observed', max_events=6, max_sources=12,
    previous_report=None, related_queries=None, max_related_queries=2)
```

Attach the result as supplementary metadata to the existing news/recall pack.
`prompt_context` is a short navigation aid, at most 2,800 characters; it is not a
replacement for the source excerpts or original evidence. Read a candidate's
original by `evidence_id` before using it in a report. The caller can choose
explicit public-entity queries and source filters; there is no fixed sequence
of research tools. If `related_queries` is omitted, a nonempty `query` is used
once. Supply `[]` or `max_related_queries=0` to disable supplementary search.

The builder opens at most `max_sources` unique originals, including failed
reads and supplementary candidates. It asks for at most 120 saved event
versions, six initial-source relationship lists of twelve links each, and two
local document searches with six returned results each. Queries use the existing
library search implementation and remain within its configured window. These
are application-level result bounds, not a promise that the underlying search
engine does not examine its eligible corpus. There is no new provider or index.

## Output and meaning

- `source_cards`: immutable evidence/version identity, URL, publisher, source
  hash, claimed publication time and local acquisition time. If the store has no
  separate version ID, the immutable evidence ID is the version identity. Full
  text remains in the evidence store.
- `event_groups`: saved reviewed-event history, explicitly declared event IDs,
  saved source relationships, same-source version groups, or independent
  unlinked sources. A saved analyst review is recorded as a classification; it
  is not independent authentication of the event. Every member is retained.
- `newer_related_candidates`: local search results with their explicit query,
  source filter and publication-time differences relative to selected evidence.
  Each is `needs_original_review=True` and
  `event_equivalence_confirmed=False`. Keywords never establish event identity,
  cancellation or contradiction. Search candidates are not silently merged with
  the selected sources.
- `comparison`: evidence additions and reused IDs relative to an actual saved
  earlier report. A supplied body alone is not trusted as earlier research.
  Textual differences never become confirmed changes of fact or opinion.
- `gaps`, `limits`, `deferred_source_ids`: unavailable originals, unknown relation
  acquisition time, incomplete event evidence and exhausted budgets. A group
  budget can defer a group while its source card remains available. Missing
  counterevidence is explicitly reported rather than treated as absent.

`source_updates` means versions of the same URL/document. A cross-source saved
relationship is labeled `explicit_relation_group`. Neither label asserts a
semantic publisher correction. Independent sources with similar titles remain
independent. Forum URL fragments are preserved because they may identify
different posts. No Jev shadow result is read or promoted into fact.

## Time and failure handling

`observed` permits later-acquired versions for retrospective reconstruction,
while flagging `version_acquired_after_as_of`. It still excludes a claimed
publication after the cutoff. `strict` additionally requires that the source
version was acquired by the cutoff. Publication dates remain publisher claims,
not independently authenticated timestamps.

Saved event classifications must themselves have been recorded by the cutoff in
both modes. An explicit source relationship with a later `known_at` is omitted.
In strict mode, a relationship without its own `known_at` or
`effective_known_at` is not used, even when both endpoints are old: old documents
do not prove that their relationship was already known. Same-source identity can
still be derived from the eligible originals. Raw declared-event metadata also
needs `event_known_at` in strict mode. Excluded future source IDs and later event
statuses are not returned as context.

Reading a broken source, relation list, or search records a local gap and leaves
other groups usable. No supplied excerpt can replace a missing or corrupt
original. The builder never writes a relation, invents a date, removes an
opposing source, or resolves a conflict solely because it is inconvenient.

## Validation and current limits

Run the isolated regression set with:

```sh
.venv/bin/python -m unittest discover -s tests -p test_report_event_memory.py -v
```

Thirteen explicitly synthetic tests cover event delays, contradictory sources,
late-acquired originals/events/relations, undated relations, URL versions,
different forum posts, declared-event metadata, real-report identity resolution,
incomplete counterevidence, local entity searches, unavailable originals,
private context and bounded output. These fixtures are not investment evidence.

The bounded real acceptance uses the existing half-year library plus saved
product evidence through SQLite read-only connections. Three initial sources
and one source-filtered local `Hoodi` search need nine original reads in total,
with no model or network request. It surfaces official EF evidence
`page-546a7ebd8e4cad2fd9923418`, published October 22, ahead of older schedule
material. The output deliberately does not infer that its postponement of an
October 26 plan proves cancellation of a separate October 27 roadmap milestone.
A strict October 26 replay excludes the selected sources acquired afterward.

Acceptance inputs, structured contexts and hashes are saved under
`local/report-quality/v1/event-memory/`. This demonstrates source navigation and
time safeguards, not complete semantic event clustering, factual truth,
exhaustive news coverage or improved report quality across all topics. Broader
report evaluation and runtime integration are separate checks. Source and group
budgets can miss relevant updates; the report should state that uncertainty and
inspect newer official originals when a schedule or disputed event matters.
