"""Exact host process statements proved from the delivered draft and source clocks.

This whitelist does not approve publisher facts, arbitrary process claims, or
text containing a familiar disclaimer. Callers still bind every target to the
actual draft and validate all other claims with the selected reviewer.
"""
from __future__ import annotations

from datetime import datetime
import json

from .minimal_delivery import MINIMAL_LIMIT, MINIMAL_SUMMARY


ONLINE_PREFIXES = ('live-', 'page-', 'web-')
AVAILABILITY_PREFIX = 'Current source snapshots were retrieved through '
AVAILABILITY_SUFFIX = '. Observation, publication and retrieval times differ; current retrievals cannot establish past availability.'
BOUND_RECORDS_PREFIX = 'Report-bound evidence records (including derived records) were retrieved or created through '
BOUND_RECORDS_SUFFIX = '. Publication, original capture and derivation clocks differ; these records do not establish past availability.'


def _no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate evidence metadata key')
        value[key] = item
    return value


def _clock(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('Missing source acquisition timestamp')
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.utcoffset() is None:
        raise ValueError('Source acquisition timestamp lacks timezone')
    return at


def _source_clock(serialized, record_id):
    if not isinstance(serialized, str):
        raise ValueError('A serialized evidence record is required')
    metadata = json.loads(serialized, object_pairs_hook=_no_duplicates)
    if not isinstance(metadata, dict) or metadata.get('record_id') != record_id:
        raise ValueError('Source metadata identity mismatch')
    source = metadata.get('source')
    if source is not None and not isinstance(source, dict):
        raise ValueError('Invalid source metadata')
    clocks = []
    for item in (metadata, source or {}):
        if 'retrieved_at' in item:
            clocks.append((_clock(item['retrieved_at']), item['retrieved_at']))
    if not clocks or any(at != clocks[0][0] for at, _ in clocks):
        raise ValueError('Missing or inconsistent source acquisition clocks')
    # The nested source clock is what the publication harness uses when both
    # exist. Publication/observed/available clocks and article text are ignored.
    return clocks[-1]


def automatic_disclosure(statement):
    """Recognize only a complete, valid host template, never a loose prefix."""
    if not isinstance(statement, str):
        return None
    for kind, prefix, suffix in (
        ('legacy', AVAILABILITY_PREFIX, AVAILABILITY_SUFFIX),
        ('bound_records', BOUND_RECORDS_PREFIX, BOUND_RECORDS_SUFFIX),
    ):
        if statement.startswith(prefix) and statement.endswith(suffix):
            value = statement[len(prefix):-len(suffix)]
            try:
                _clock(value)
            except (ValueError, TypeError, OverflowError):
                return None
            return kind, value
    return None


def _bound_record_clock(serialized, record_id):
    if isinstance(serialized, str):
        metadata = json.loads(serialized, object_pairs_hook=_no_duplicates)
    elif isinstance(serialized, dict):
        metadata = serialized
    else:
        raise ValueError('An evidence metadata object is required')
    if not isinstance(metadata, dict) or metadata.get('record_id') != record_id:
        raise ValueError('Bound evidence metadata identity mismatch')
    source = metadata.get('source')
    if source is not None and not isinstance(source, dict):
        raise ValueError('Invalid bound source metadata')
    acquired = []
    for item in (metadata, source or {}):
        if 'retrieved_at' in item:
            acquired.append((_clock(item['retrieved_at']), item['retrieved_at']))
    if acquired and any(at != acquired[0][0] for at, _ in acquired):
        # A later derivation belongs in calculated_at/created_at; it must not
        # silently rebrand an inconsistent original-source acquisition clock.
        raise ValueError('Conflicting top-level and nested retrieval clocks')
    clocks = list(acquired)
    for key in ('calculated_at', 'created_at'):
        if key in metadata:
            clocks.append((_clock(metadata[key]), metadata[key]))
    if not clocks:
        raise ValueError('Missing bound evidence retrieval/creation clock')
    return max(clocks, key=lambda item: (item[0], item[1]))


def bound_record_timestamp(records):
    """Maximum of every explicitly bound online record, not its input closure.

    Retrieval and derivation/record creation clocks are all checked; publication,
    observation and article dates cannot fill a missing record clock.
    """
    if not isinstance(records, dict) or not records:
        return None
    clocks = [_bound_record_clock(value, rid) for rid, value in records.items()]
    return max(clocks, key=lambda item: (item[0], item[1]))[1]


def _minimal_shape(body):
    if (body.get('delivery_kind') != 'minimal' or body.get('summary') != MINIMAL_SUMMARY or
            body.get('judgment', 'informational') != 'informational' or
            body.get('judgment_reason', '') != ''):
        return False
    if not isinstance(body.get('observations'), list) or not body['observations']:
        return False
    for field in ('key_changes', 'interpretations', 'forecasts', 'questions_to_watch',
                  'explanations', 'charts', 'documents', 'narrative_evidence_ids'):
        value = body.get(field, [])
        if not isinstance(value, list) or value:
            return False
    return body.get('advice') is None and body.get('purchase_decision') is None


def is_verified_host_target(target, body, texts, process_context=None):
    """Return True only for an exact fixed process statement with checked proof.

    ``process_context`` is accepted for the reviewer interface, but cannot turn
    an unproved statement into approval. Archived-availability prose is left to
    ordinary review until a separate deterministic proof contract is provided.
    """
    if hasattr(body, 'model_dump'):
        body = body.model_dump()
    if not isinstance(target, dict) or not isinstance(body, dict) or not isinstance(texts, dict):
        return False
    kind, statement = target.get('kind'), target.get('statement')
    if not isinstance(statement, str):
        return False
    if _minimal_shape(body):
        if kind == 'summary' and body.get('summary') == MINIMAL_SUMMARY:
            span = target.get('summary_span')
            if span is None:
                return statement == MINIMAL_SUMMARY
            if not isinstance(span, dict):
                return False
            start, end = span.get('start'), span.get('end')
            return (type(start) is int and type(end) is int and
                    0 <= start < end <= len(MINIMAL_SUMMARY) and
                    MINIMAL_SUMMARY[start:end] == statement)
        if kind == 'limitation' and statement == MINIMAL_LIMIT:
            return MINIMAL_LIMIT in body.get('limitations', [])
    if kind != 'limitation' or statement not in body.get('limitations', []):
        return False
    recognized = automatic_disclosure(statement)
    if recognized is None:
        return False
    try:
        from .audit import report_evidence_ids
        all_ids = report_evidence_ids(body)
        online = {rid for rid in all_ids if isinstance(rid, str) and rid.startswith(ONLINE_PREFIXES)}
        target_ids = target.get('evidence_ids', [])
        if not isinstance(target_ids, list) or not all(isinstance(rid, str) for rid in target_ids):
            return False
        # A global availability statement cannot omit a later bound source.
        if not online or not online.issubset(target_ids):
            return False
        # Extra online IDs supplied to this target must also have clocks; no
        # subset of conveniently timed sources may establish the global max.
        online.update(rid for rid in target_ids if rid.startswith(ONLINE_PREFIXES))
        if recognized[0] == 'legacy':
            clocks = [_source_clock(texts[rid], rid) for rid in sorted(online)]
            latest = max(clocks, key=lambda item: (item[0], item[1]))[1]
            return statement == AVAILABILITY_PREFIX + latest + AVAILABILITY_SUFFIX
        latest = bound_record_timestamp({rid: texts[rid] for rid in sorted(online)})
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return statement == BOUND_RECORDS_PREFIX + latest + BOUND_RECORDS_SUFFIX
