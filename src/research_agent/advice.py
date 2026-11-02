"""Optional evidence-linked advice; historical analogy is not a forecast proof."""
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field, model_validator
from research_data.http import dumps, utcnow
from research_data.normalize import timestamp

from .claims import Claim, validate_claims


class HistoricalCase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    case_end: str = Field(description="End of the historical event/outcome window, with timezone; cannot exceed the research as-of time.")
    evidence_ids: list[str] = Field(min_length=1, max_length=10)
    similarities: list[str] = Field(min_length=1, max_length=5)
    differences: list[str] = Field(min_length=1, max_length=5)
    limitations: list[str] = Field(min_length=1, max_length=5)
    availability: Literal['observed_history', 'point_in_time'] = Field(
        default='observed_history',
        description="observed_history is retrospective. point_in_time additionally requires saved availability receipts no later than case_end.")

    @model_validator(mode='after')
    def valid_time(self):
        timestamp(self.case_end)
        if datetime.fromisoformat(self.case_end.replace('Z', '+00:00')).utcoffset() is None:
            raise ValueError('Historical case_end requires an explicit timezone')
        return self


class ResearchAdvice(BaseModel):
    summary: str = Field(min_length=1, max_length=1600, description="Qualified suggestion grounded in the supplied evidence, not a promise of performance.")
    horizon_days: int | None = Field(default=None, ge=1, le=365)
    scope: Literal['general', 'user_context'] = 'general'
    context_evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    rationale: list[Claim] = Field(min_length=1, max_length=5)
    conditions: list[Claim] = Field(min_length=1, max_length=5)
    counterpoints: list[Claim] = Field(default_factory=list, max_length=5)
    invalidation: list[Claim] = Field(min_length=1, max_length=5)
    historical_cases: list[HistoricalCase] = Field(default_factory=list, max_length=3)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    review_triggers: list[str] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def context_scope(self):
        if self.scope == 'user_context' and not self.context_evidence_ids:
            raise ValueError('Context-specific advice requires actual current context evidence')
        return self


def advice_claims(advice):
    if advice is None:
        return []
    return [*advice.rationale, *advice.conditions, *advice.counterpoints, *advice.invalidation]


def advice_evidence_ids(advice):
    if advice is None:
        return []
    if isinstance(advice, dict):
        advice = ResearchAdvice.model_validate(advice)
    ids = [*advice.context_evidence_ids]
    ids.extend(rid for claim in advice_claims(advice) for rid in claim.evidence_ids)
    ids.extend(rid for case in advice.historical_cases for rid in case.evidence_ids)
    return list(dict.fromkeys(ids))


def validate_advice(advice, lookup, as_of=None):
    if advice is None:
        return {'evidence_ids': [], 'numeric_claims_checked': 0, 'historical_cases_checked': 0}
    cutoff = timestamp(as_of or utcnow())
    ids, numeric = validate_claims(advice_claims(advice), lookup)
    for rid in advice.context_evidence_ids:
        record = lookup(rid)
        if record.get('dataset_id') != 'local.user_research_parameters':
            raise ValueError('Use saved current research parameters for advice context')
        profile = record.get('profile', {})
        supplied = any(profile.get(key) is not None for key in (
            'budget_usd', 'existing_crypto_usd', 'portfolio_total_usd', 'max_loss_usd'))
        supplied = supplied or bool(str(profile.get('preference') or '').strip())
        if advice.scope == 'user_context' and not supplied:
            raise ValueError('Unknown personal amounts and default assumptions cannot establish personalized context')
        ids.add(rid)

    def check_available(rid, seen, before, require_receipt):
        if rid in seen:
            return
        seen.add(rid)
        if len(seen) > 200:
            raise ValueError('Historical evidence lineage exceeds the bounded check')
        record = lookup(rid)
        dates = [record.get('available_at'), record.get('retrieved_at'),
                 record.get('source', {}).get('retrieved_at')]
        known = [timestamp(value) for value in dates if value]
        if any(value > before for value in known):
            raise ValueError('Historical analogy uses evidence unavailable at its declared cutoff')
        if not known and require_receipt:
            raise ValueError('Point-in-time historical evidence requires an availability receipt')
        for child in record.get('input_record_ids', []):
            check_available(child, seen, before, require_receipt)

    seen = set()
    for rid in ids - set(advice.context_evidence_ids):
        check_available(rid, seen, cutoff, False)
    for case in advice.historical_cases:
        end = timestamp(case.case_end)
        if end > cutoff:
            raise ValueError('Historical analogy cannot use a future outcome window')
        bound = end if case.availability == 'point_in_time' else cutoff
        seen = set()
        for rid in case.evidence_ids:
            check_available(rid, seen, bound, case.availability == 'point_in_time')
            ids.add(rid)
    return {'evidence_ids': sorted(ids), 'numeric_claims_checked': numeric,
            'historical_cases_checked': len(advice.historical_cases)}


def advice_targets(advice, prefix='V'):
    if advice is None:
        return []
    ids = advice_evidence_ids(advice)
    targets = [{'claim_id': prefix + '0', 'kind': 'judgment',
                'statement': dumps({'summary': advice.summary, 'scope': advice.scope,
                                    'horizon_days': advice.horizon_days,
                                    'assumptions': advice.assumptions,
                                    'missing_information': advice.missing_information}),
                'evidence_ids': ids}]
    for code, claims in [('R', advice.rationale), ('C', advice.conditions),
                         ('B', advice.counterpoints), ('I', advice.invalidation)]:
        targets.extend({'claim_id': prefix + code + str(i + 1), 'kind': 'interpretation',
                        'advice_section': {'R':'rationale','C':'conditions','B':'counterpoints','I':'invalidation'}[code],
                        'temporal_role': 'prospective_criterion' if code in ('C','I') else 'evidence_based_reasoning',
                        **claim.model_dump()} for i, claim in enumerate(claims))
    for i, case in enumerate(advice.historical_cases):
        targets.append({'claim_id': prefix + 'H' + str(i + 1), 'kind': 'interpretation',
                        'statement': dumps(case.model_dump()), 'evidence_ids': case.evidence_ids})
    targets.append({'claim_id': prefix + 'T', 'kind': 'research_action',
                    'statement': dumps(advice.review_triggers), 'evidence_ids': ids})
    return targets


def render_advice(advice, cite):
    if advice is None:
        return ''
    lines = ['## Conditional research suggestion', '', advice.summary + ' ' + cite(advice_evidence_ids(advice)),
             '', 'Scope: ' + ('Supplied personal context' if advice.scope == 'user_context' else 'General market research')]
    if advice.horizon_days:
        lines.append(f'Research horizon: {advice.horizon_days} days')
    for heading, claims in [('Evidence and reasoning', advice.rationale),
                            ('Conditions to investigate', advice.conditions),
                            ('Counterevidence', advice.counterpoints),
                            ('Revisit or invalidate when', advice.invalidation)]:
        if claims:
            lines.extend(['', heading + ':', *[f'- {claim.statement} {cite(claim.evidence_ids)}' for claim in claims]])
    for case in advice.historical_cases:
        lines.extend(['', 'Historical comparison: ' + case.name + ' ' + cite(case.evidence_ids),
                      'Case window ends: ' + case.case_end,
                      'Availability: ' + ('Retrospective observed history; not proof of historical public availability.'
                                         if case.availability == 'observed_history' else 'Saved availability receipts checked against case end.'),
                      'Similarities: ' + '; '.join(case.similarities),
                      'Differences: ' + '; '.join(case.differences),
                      'Limits of transfer: ' + '; '.join(case.limitations)])
    for heading, items in [('Scenario assumptions', advice.assumptions),
                           ('Missing information', advice.missing_information),
                           ('What would prompt another review', advice.review_triggers)]:
        if items:
            lines.extend(['', heading + ':', *['- ' + item for item in items]])
    return '\n'.join(lines)
