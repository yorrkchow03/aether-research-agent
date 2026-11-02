"""Optional, source-grounded explanations with explicit applicability and limits.

These are hypotheses for a reader to examine, not price predictions or learned
market rules. Each nested claim gets its own mandatory review target and source
binding; a separate compound target checks coherence, horizon and context.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from research_data.http import dumps, utcnow

from .claims import Claim, validate_claims


BoundedText = Annotated[str, Field(min_length=1, max_length=800)]
ContextKey = Annotated[str, Field(min_length=1, max_length=64)]
ContextValue = Annotated[str, Field(min_length=1, max_length=200)]


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Explanation clocks require an ISO timestamp with explicit timezone')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Explanation clocks require an ISO timestamp with explicit timezone') from None
    if result.utcoffset() is None:
        raise ValueError('Explanation clocks require an explicit timezone')
    return result.astimezone(timezone.utc)


class ResearchExplanation(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=160)
    observed_basis: list[Claim] = Field(min_length=1, max_length=4,
        description='Actual saved observations supporting the interpretation. Keep exact numeric fields and original source IDs.')
    hypothesis: Claim = Field(description='Qualified interpretation of the observations; do not assert causality or predictive skill without evidence.')
    horizon: str = Field(min_length=1, max_length=240,
        description='Timeframe or event stage to which this explanation applies. Distinguish analysis choice from source facts.')
    applicability: list[Claim] = Field(min_length=1, max_length=3,
        description='Source-grounded prerequisites or scope. State future prerequisites as conditions, not completed events.')
    alternative_explanations: list[Claim] = Field(default_factory=list, max_length=2,
        description='Other qualified interpretations, only when relevant and grounded; never force an opposing view.')
    counterevidence: list[Claim] = Field(default_factory=list, max_length=3,
        description='Contrary observations or limits in the supplied sources; an empty list does not establish absence of contrary evidence.')
    invalidation: list[Claim] = Field(min_length=1, max_length=3,
        description='Evidence-grounded conditions that would require revisiting the interpretation, not arbitrary trading thresholds.')
    uncertainties: list[BoundedText] = Field(min_length=1, max_length=4,
        description='Missing information or limits of this explanation; do not insert unsupported facts or invented probabilities.')
    asset: Literal['ETH', 'BTC'] | None = None
    horizon_days: int | None = Field(default=None, ge=1, le=365,
        description='Optional analysis horizon for case retrieval, not a validated prediction horizon.')
    context: dict[ContextKey, ContextValue | None] = Field(default_factory=dict, max_length=8,
        description='Optional contemporaneous conditions for historical-case matching. Every non-null label must be supported by observed_basis/applicability; unknown values stay null. No inferred regime presented as measured fact.')

    @field_validator('name', 'horizon')
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError('Explanation names and horizons must contain meaningful text')
        return value

    @field_validator('uncertainties')
    @classmethod
    def meaningful_uncertainties(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError('Explanation uncertainties must contain meaningful text')
        return values

    @field_validator('context')
    @classmethod
    def meaningful_context(cls, values):
        if any(not key.strip() or (value is not None and not value.strip()) for key, value in values.items()):
            raise ValueError('Explanation context labels must be nonblank or explicitly null')
        return values


def _models(explanations):
    return [ResearchExplanation.model_validate(value) if isinstance(value, dict) else value
            for value in explanations or []]


def explanation_claims(explanations):
    return [claim for item in _models(explanations)
            for claim in [*item.observed_basis, item.hypothesis, *item.applicability,
                          *item.alternative_explanations, *item.counterevidence, *item.invalidation]]


def explanation_evidence_ids(explanations):
    return list(dict.fromkeys(rid for claim in explanation_claims(explanations) for rid in claim.evidence_ids))


def validate_explanations(explanations, lookup, as_of=None):
    models = _models(explanations)
    ids, numeric = validate_claims(explanation_claims(models), lookup)
    before = _time(as_of or utcnow()) if models else None
    seen = set()

    def available(rid):
        if rid in seen:
            return
        seen.add(rid)
        if len(seen) > 200:
            raise ValueError('Explanation evidence lineage exceeds the bounded check')
        row = lookup(rid)
        source = row.get('source') if isinstance(row.get('source'), dict) else {}
        clock_keys = ('available_at', 'retrieved_at', 'calculated_at', 'published_at',
                      'observed_at', 'period_end', 'known_at')
        dates = [container[key] for container in (row, source) for key in clock_keys
                 if key in container and container[key] is not None]
        if any(_time(value) > before for value in dates):
            raise ValueError('Explanation uses evidence unavailable at the research cutoff')
        for child in row.get('input_record_ids', []):
            available(child)

    for rid in ids:
        available(rid)
    return {'evidence_ids': sorted(ids), 'numeric_claims_checked': numeric,
            'explanations_checked': len(models),
            'scope': 'Saved IDs, exact numeric fields and explicit acquisition/publication/observation clocks through the evidence lineage; semantic applicability remains a separate source review.'}


def explanation_targets(explanations, prefix='E'):
    """Bind every nested claim to its own sources, then review chain coherence.

    The compound target cannot substitute for per-claim source support. Existing
    review-budget limits reject oversized outputs before an HTTP request.
    """
    targets = []
    for i, item in enumerate(_models(explanations)):
        identity = prefix + str(i + 1)
        targets.append({'claim_id': identity, 'kind': 'interpretation',
            'temporal_role': 'conditional_explanation',
            'statement': dumps(item.model_dump()),
            'evidence_ids': explanation_evidence_ids([item]),
            'review_scope': 'Check chain coherence and every context label, horizon, name and uncertainty. Retain source status and prerequisites. This compound assessment does not replace the separately reviewed nested claims.'})
        for code, section, claims, kind, role in (
            ('O', 'observed_basis', item.observed_basis, 'observation', 'observed_fact'),
            ('H', 'hypothesis', [item.hypothesis], 'interpretation', 'evidence_based_reasoning'),
            ('C', 'applicability', item.applicability, 'interpretation', 'prospective_criterion'),
            ('A', 'alternative_explanations', item.alternative_explanations, 'interpretation', 'evidence_based_reasoning'),
            ('B', 'counterevidence', item.counterevidence, 'interpretation', 'evidence_based_reasoning'),
            ('X', 'invalidation', item.invalidation, 'interpretation', 'prospective_criterion'),
        ):
            targets.extend({'claim_id': identity + code + str(n + 1), 'kind': kind,
                'explanation_section': section, 'temporal_role': role, **claim.model_dump()}
                for n, claim in enumerate(claims))
    return targets


def render_explanations(explanations, cite):
    models = _models(explanations)
    if not models:
        return ''
    lines = ['## Explanations and conditions', '',
             'These are qualified interpretations of saved observations. Different horizons or prerequisites can support different explanations.']
    for item in models:
        lines += ['', '### ' + item.name, '',
                  'Working interpretation: ' + item.hypothesis.statement + ' ' + cite(item.hypothesis.evidence_ids),
                  '', 'Scope and horizon: ' + item.horizon]
        if item.asset:
            lines.append('Asset: ' + item.asset)
        if item.horizon_days is not None:
            lines.append('Analysis horizon: ' + str(item.horizon_days) + ' days')
        for heading, claims in [('Observed basis', item.observed_basis),
                                ('Applies under these conditions', item.applicability),
                                ('Alternative explanations', item.alternative_explanations),
                                ('Counterevidence and limitations', item.counterevidence),
                                ('Revisit this explanation when', item.invalidation)]:
            if claims:
                lines += ['', heading + ':',
                          *['- ' + claim.statement + ' ' + cite(claim.evidence_ids) for claim in claims]]
        if item.context:
            lines += ['', 'Conditions recorded for comparison: ' + '; '.join(
                key + '=' + (value if value is not None else 'Unknown') for key, value in item.context.items()) + '.']
        lines += ['', 'Uncertainties:', *['- ' + text for text in item.uncertainties]]
    return '\n'.join(lines)
