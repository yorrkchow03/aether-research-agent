"""Offline, exact-budget evidence packing from cached semantic judgments.

This research component has no provider/client, credentials, store, or reference
labels. All variants receive the same candidates and render the same exact
source text. Character budgets count the returned JSONL context, including
metadata and separators. A token budget requires a caller-supplied tokenizer;
character/byte heuristics are never presented as exact provider tokens.

Relation edges are ordered A/B: ``source`` is A, ``target`` is B. Thus
``supersedes``/``corrects`` means B explicitly updates A. An accepted update or
contradiction protects both endpoints, rather than declaring either true.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
import json
import math

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


VARIANTS = ('lexical', 'semantic_no_graph', 'semantic_graph', 'graph_no_protection')
POLICY_VERSION = 'cached-relation-exact-context-v1'
PROTECTED_RELATIONS = frozenset({'contradicts', 'unresolved_conflict', 'supersedes', 'corrects', 'update'})
RELATIONS = PROTECTED_RELATIONS | {'duplicate', 'different_scope', 'unrelated', 'insufficient'}
SOURCE_FIELDS = ('title', 'url', 'source', 'source_kind', 'dataset_id', 'asset', 'assets',
                 'scope', 'published_at', 'event_at', 'retrieved_at', 'available_at',
                 'first_seen_at', 'origin_key', 'provenance')


def _probability(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 <= value <= 1)


def _threshold(value):
    if not _probability(value):
        raise ValueError('minimum_confidence must be finite and in [0,1]')


def gate_dimensions(judgment, *, minimum_confidence=.8):
    """Keep dimension acceptance distinct from whole-case abstention.

    A low-confidence topic can abstain the classification case while its asset
    dimension is usable. Errors/unavailable responses never yield an accepted
    dimension. This helper does not repair a malformed provider response.
    """
    _threshold(minimum_confidence)
    if not isinstance(judgment, Mapping):
        raise ValueError('Supply a cached judgment object')
    dimensions = judgment.get('dimensions') or {}
    if not isinstance(dimensions, Mapping):
        raise ValueError('dimensions must be a mapping')
    accepted, unresolved = {}, {}
    usable_status = judgment.get('status') in ('ok', 'abstained')
    for name in sorted(dimensions):
        answer = dimensions[name]
        if not usable_status:
            unresolved[name] = 'case_error_or_unavailable'
        elif not isinstance(answer, Mapping) or not isinstance(answer.get('choice'), str) or not answer['choice']:
            unresolved[name] = 'missing_or_invalid_choice'
        elif answer['choice'] == 'uncertain':
            unresolved[name] = 'uncertain_choice'
        elif not _probability(answer.get('confidence')) or answer['confidence'] < minimum_confidence:
            unresolved[name] = 'low_or_invalid_confidence'
        else:
            accepted[name] = deepcopy(dict(answer))
    return {'case_status': judgment.get('status'),
            'whole_case_accepted': judgment.get('status') == 'ok' and bool(dimensions) and not unresolved,
            'accepted_dimensions': accepted, 'unresolved_dimensions': unresolved}


def _lexical_scores(rows, query):
    # Match the existing selector's training-free, multilingual character
    # TF-IDF representation. Only this eligible pool supplies corpus statistics.
    if not query.strip():
        return {rid: 1.0 for rid in rows}
    texts = [' '.join((str(row.get('title') or ''), row['text'])) for row in rows.values()]
    try:
        matrix = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5),
                                 max_features=12000).fit_transform([*texts, query])
        values = np.clip((matrix[:-1] @ matrix[-1].T).toarray().ravel(), 0, 1)
    except ValueError:
        values = np.zeros(len(rows))
    return {rid: float(value) for rid, value in zip(rows, values)}


def _relevance(row, minimum_confidence):
    value = row.get('relevance')
    if isinstance(value, Mapping):
        status = value.get('status', 'ok')
        confidence = value.get('confidence')
        distribution = value.get('scores')
        score = value.get('score', distribution.get('relevant') if isinstance(distribution, Mapping) else None)
    else:
        status = row.get('score_status', 'ok')
        confidence = row.get('score_confidence', 1.0)
        score = row.get('score', value)
    if status != 'ok' or not _probability(confidence) or confidence < minimum_confidence or not _probability(score):
        return None
    return float(score)


def _render(rows, selected):
    # Source metadata is separate from cached model judgments. Preserve every
    # supplied metadata field in selected_records; expose source fields only in
    # the source context. Extra publisher provenance belongs in ``provenance``.
    records = []
    for rid in selected:
        row = rows[rid]
        records.append({'id': rid, 'text': row['text'],
                        **{name: row[name] for name in SOURCE_FIELDS if name in row}})
    return '\n'.join(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':'), allow_nan=False) for row in records)


def pack_evidence(candidates: Sequence[Mapping], relations: Sequence[Mapping] = (), *,
                  query='', budget_chars=2400, variant='semantic_graph',
                  minimum_confidence=.8, limit=8, token_budget=None,
                  token_counter: Callable[[str], int] | None = None):
    """Pack source records using optional cached relevance and relation edges.

    Candidates require ``id`` (or ``record_id``) and exact ``text`` (or
    ``excerpt``). Optional ``score`` is a relevance score in [0,1]. A nested
    ``relevance={score, confidence, status}`` carries explicit uncertainty.
    An edge is ``{source, target, label, confidence, status='ok'}``.

    Unknown/error/low-confidence judgments never remove an evidence record.
    Accepted duplicate edges group demand, retaining one representative unless
    several members must remain to preserve a contradiction/version chain.
    Graph protection is all-or-neither: an impossible protected bundle returns
    an explicit gap and no partial context. The ablation disables this rule.
    No input is mutated, and no text is shortened or paraphrased.
    """
    _threshold(minimum_confidence)
    if variant not in VARIANTS:
        raise ValueError('Unknown policy variant')
    if not isinstance(query, str):
        raise ValueError('query must be a string')
    if isinstance(budget_chars, bool) or not isinstance(budget_chars, int) or budget_chars < 0:
        raise ValueError('budget_chars must be a nonnegative integer')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 150:
        raise ValueError('limit must be in 1..150')
    if token_counter is not None and not callable(token_counter):
        raise ValueError('token_counter must be callable')
    if token_budget is not None:
        if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 0:
            raise ValueError('token_budget must be a nonnegative integer')
        if token_counter is None:
            raise ValueError('An exact token budget requires token_counter')
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or len(candidates) > 150:
        raise ValueError('Supply up to150 frozen candidates')
    if not isinstance(relations, Sequence) or isinstance(relations, (str, bytes)) or len(relations) > 11175:
        raise ValueError('Supply a bounded frozen relation list')
    rows, originals = {}, {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError('Candidates must be mappings')
        rid = candidate.get('id', candidate.get('record_id'))
        text = candidate.get('text', candidate.get('excerpt'))
        if not isinstance(rid, str) or not rid or not isinstance(text, str) or not text.strip():
            raise ValueError('Each source needs a nonempty ID and exact text')
        original = deepcopy(dict(candidate))
        if rid in originals and originals[rid] != original:
            raise ValueError('An evidence ID cannot be rebound to different content')
        originals[rid] = original
        rows[rid] = {**original, 'text': text}
    rows = dict(sorted(rows.items()))
    lexical = _lexical_scores(rows, query)
    scores, gaps = {}, []
    for rid, row in rows.items():
        semantic = _relevance(row, minimum_confidence)
        scores[rid] = lexical[rid] if variant == 'lexical' or semantic is None else .5 * lexical[rid] + .5 * semantic
        if variant != 'lexical' and semantic is None:
            gaps.append({'reason': 'relevance_unknown_lexical_fallback', 'evidence_ids': [rid]})

    graph = variant in ('semantic_graph', 'graph_no_protection')
    protection = variant == 'semantic_graph'
    accepted_edges, protected = [], set()
    for edge in relations:
        if not isinstance(edge, Mapping):
            raise ValueError('Relations must be mappings')
        source, target = edge.get('source'), edge.get('target')
        label, confidence = edge.get('label'), edge.get('confidence')
        if not isinstance(source, str) or not isinstance(target, str) or source not in rows or target not in rows or source == target:
            if graph:
                gaps.append({'reason': 'relation_endpoint_unavailable',
                             'evidence_ids': [source, target]})
            continue
        if edge.get('status', 'ok') != 'ok' or not isinstance(label, str) or label not in RELATIONS or not _probability(confidence) or confidence < minimum_confidence:
            if graph:
                gaps.append({'reason': 'untrusted_relation_ignored',
                             'evidence_ids': [source, target], 'label': label})
            continue
        if graph:
            accepted_edges.append({'source': source, 'target': target, 'label': label,
                                   'confidence': float(confidence)})
            if protection and label in PROTECTED_RELATIONS:
                protected.update((source, target))
    accepted_edges.sort(key=lambda edge: (edge['source'], edge['target'], edge['label'], edge['confidence']))

    parent = {rid: rid for rid in rows}
    def find(rid):
        while parent[rid] != rid:
            parent[rid] = parent[parent[rid]]
            rid = parent[rid]
        return rid
    for edge in accepted_edges:
        if edge['label'] == 'duplicate':
            left, right = sorted((find(edge['source']), find(edge['target'])))
            parent[right] = left
    components = {}
    for rid in rows:
        components.setdefault(find(rid), []).append(rid)
    retained, dropped, duplicate_groups = set(), {}, []
    for members in components.values():
        kept = sorted(protected.intersection(members))
        if not kept:
            kept = [min(members, key=lambda rid: (-scores[rid], len(_render(rows, [rid])), rid))]
        retained.update(kept)
        representative = min(kept, key=lambda rid: (-scores[rid], len(_render(rows, [rid])), rid))
        for rid in members:
            if rid not in kept:
                dropped[rid] = representative
        if len(members) > 1:
            duplicate_groups.append({'members': members, 'retained': kept})
        if len(kept) > 1:
            gaps.append({'reason': 'duplicate_group_contains_protected_versions', 'evidence_ids': kept})

    def measure(selected):
        context = _render(rows, selected)
        tokens = token_counter(context) if token_counter is not None else None
        if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0):
            raise ValueError('token_counter must return a nonnegative integer')
        return context, tokens
    def fits(selected):
        context, tokens = measure(selected)
        return (len(selected) <= limit and len(context) <= budget_chars
                and (token_budget is None or tokens <= token_budget))

    selected = sorted(protected)
    trace = []
    impossible = bool(selected) and not fits(selected)
    if impossible:
        required_context, required_tokens = measure(selected)
        gaps.append({'reason': 'protected_bundle_does_not_fit', 'evidence_ids': selected,
                     'required_chars': len(required_context), 'required_tokens': required_tokens,
                     'required_count': len(selected)})
        selected = []
    else:
        if selected:
            trace.append({'reason': 'protected_bundle', 'added': selected[:]})
        # All variants share this relevance/cost rule; semantic and graph
        # information are the only experimental differences.
        while len(selected) < limit:
            current_context, _ = measure(selected)
            possible = []
            for rid in sorted(retained.difference(selected)):
                trial = [*selected, rid]
                if fits(trial):
                    new_context, _ = measure(trial)
                    cost = max(1, len(new_context) - len(current_context))
                    possible.append((scores[rid] / cost, scores[rid], rid))
            if not possible:
                break
            _, _, chosen = min(possible, key=lambda item: (-item[0], -item[1], item[2]))
            selected.append(chosen)
            trace.append({'reason': 'relevance_per_exact_character', 'added': [chosen]})
    context, tokens = measure(selected)
    if not fits(selected):
        # A tokenizer may count an empty string as a special token. Its budget
        # must still be checked even when no evidence fits.
        raise ValueError('The empty context exceeds the supplied token budget')
    selected_set = set(selected)
    relation_coverage = []
    for edge in accepted_edges:
        if edge['label'] not in PROTECTED_RELATIONS:
            continue
        both = {edge['source'], edge['target']}.issubset(selected_set)
        relation_coverage.append({**edge, 'all_endpoints_selected': both})
        if not both:
            gaps.append({'reason': 'protected_relation_not_in_context',
                         'evidence_ids': [edge['source'], edge['target']], 'label': edge['label']})
    excluded = sorted(retained.difference(selected_set))
    if excluded:
        gaps.append({'reason': 'evidence_not_in_context', 'evidence_ids': excluded})
    gaps.sort(key=lambda gap: json.dumps(gap, ensure_ascii=False, sort_keys=True))
    return {'policy_version': POLICY_VERSION, 'variant': variant,
            'selected_ids': selected, 'selected_records': [deepcopy(originals[rid]) for rid in selected],
            'context': context, 'context_chars': len(context), 'budget_chars': budget_chars,
            'context_tokens': tokens, 'token_budget': token_budget,
            'token_count_basis': 'caller_supplied_exact_tokenizer' if token_counter is not None else 'not_measured',
            'candidate_count': len(rows), 'dropped_duplicates': dict(sorted(dropped.items())),
            'duplicate_groups': duplicate_groups, 'protected_ids': sorted(protected),
            'accepted_relations': accepted_edges, 'relation_coverage': relation_coverage,
            'gaps': gaps, 'trace': trace,
            'status': 'insufficient_budget' if impossible else 'packed' if selected else 'empty',
            'scope': 'Offline cached-judgment policy; exact supplied source text, no truth adjudication or source deletion.'}
