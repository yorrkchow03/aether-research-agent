"""Budgeted selection of exact passages for the subquestions of a report."""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlsplit

from .knowledge import canonical_url

UPDATE_RELATIONS = {'corrects', 'supersedes', 'contradicts', 'unresolved_conflict'}
LINEAGE_RELATIONS = {'duplicate_of', 'syndicated_from', 'translation_of'}


def normalize_facets(queries):
    if queries is None:
        return ()
    if not isinstance(queries, (list, tuple)) or len(queries) > 6:
        raise ValueError('Provide at most six report subquestions')
    facets = []
    for query in queries:
        if not isinstance(query, str) or not query.strip() or len(query) > 400:
            raise ValueError('Each report subquestion must have 1..400 characters')
        query = ' '.join(query.split())
        if query.casefold() not in {facet.casefold() for facet in facets}:
            facets.append(query)
    return tuple(facets)


def source_origins(rows, relations):
    """Deduplicate known lineage; an unmatched source is not proven independent."""
    parents = {row['record_id']: row['record_id'] for row in rows}

    def root(rid):
        while parents[rid] != rid:
            rid = parents[rid]
        return rid

    def join(left, right):
        if left in parents and right in parents:
            a, b = sorted((root(left), root(right)))
            parents[b] = a

    seen = {}
    for row in rows:
        rid = row['record_id']
        url = row.get('url') or (row.get('source') or {}).get('url')
        try:
            normalized = canonical_url(url) if url else ''
            fragment = urlsplit(url).fragment if url else ''
        except (ValueError, TypeError):
            normalized = fragment = ''
        for identity in (
            ('document', row.get('document_id')),
            ('url', (normalized, fragment) if normalized else None),
            ('body', row.get('source_body_sha256')),
        ):
            if identity[1]:
                join(rid, seen.setdefault(identity, rid))
    for link in relations:
        if link.get('predicate') in LINEAGE_RELATIONS:
            join(link.get('subject'), link.get('object'))
    return {rid: root(rid) for rid in parents}


def choose_facets(ordered, by_id, facet_orders, origins, build_group, *,
                  limit, budget_chars, relation_pairs=(), prioritize_updates=True):
    """Cover each facet first, then fill with distinct origins where possible.

    build_group applies the same atomic relationship constraints as flat rank.
    Facet matches are ranking signals, not judgments of factual support.
    """
    selected = []
    selected_set = set()
    covered_origins = set()
    selected_groups = []
    used = 0
    missed_groups = []
    priority_pairs = set(relation_pairs) if prioritize_updates else set()
    linked_sources = {rid for pair in priority_pairs for rid in pair}

    def origin(pid):
        rid = by_id[pid]['record_id']
        return origins.get(rid, rid)

    def add(pid, allowed_origin):
        nonlocal used
        if pid in selected_set or (not allowed_origin and origin(pid) in covered_origins):
            return False
        related, members = build_group(pid)
        if not members:
            if related:
                missed_groups.append((related, 'related_evidence_endpoint_unavailable'))
            return False
        additions = [candidate for candidate in members if candidate not in selected_set]
        cost = sum(len(by_id[candidate]['excerpt']) for candidate in additions)
        if len(selected) + len(additions) > limit or used + cost > budget_chars:
            if len(related) > 1:
                missed_groups.append((related, 'related_evidence_group_exceeds_context'))
            return False
        used += cost
        selected.extend(additions)
        selected_set.update(additions)
        covered_origins.update(origin(candidate) for candidate in additions)
        if len(related) > 1 and related not in selected_groups:
            selected_groups.append(related)
        return True

    # A known update with an already competitive facet match gets priority.
    # Raw keywords or text similarity never create an update relationship.
    queues = []
    for ranking in facet_orders:
        competitive = ranking[:6]
        prioritized = [pid for pid in competitive if by_id[pid]['record_id'] in linked_sources]
        queue = list(dict.fromkeys([*prioritized, *ranking]))
        queues.append(queue)

    for index in sorted(range(len(queues)), key=lambda i: (len(queues[i]), i)):
        ranking = queues[index]
        if any(pid in selected_set for pid in ranking[:3]):
            continue
        # Permit a second distinct passage from an already used origin for a
        # different facet when no new origin can satisfy the budget.
        for allow_reuse in (False, True):
            if any(add(pid, allow_reuse) for pid in ranking):
                break

    for allow_reuse in (False, True):
        for pid in ordered:
            if len(selected) >= limit:
                break
            add(pid, allow_reuse)

    memberships = defaultdict(list)
    for index, ranking in enumerate(facet_orders):
        for pid in ranking:
            if pid in selected_set:
                memberships[index].append(pid)
    unique_misses = list(dict.fromkeys((tuple(group), reason) for group, reason in missed_groups))
    return selected, used, selected_groups, memberships, unique_misses
