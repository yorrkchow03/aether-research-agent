"""Typed research reports with mandatory citation and numeric-field checks."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from .claims import Claim, validate_claims
from .advice import ResearchAdvice, advice_evidence_ids, validate_advice, render_advice
from .research_explanations import (ResearchExplanation, explanation_evidence_ids,
                                    validate_explanations, render_explanations)


def distinct_sources(sources):
    """One publisher/capture receipt per source list; row lineage stays intact."""
    import json
    unique={json.dumps(item,sort_keys=True,ensure_ascii=False):item for item in sources}
    return sorted(unique.values(),key=lambda item:(item.get('name',''),item.get('retrieved_at',''),item.get('url','')))


class Forecast(BaseModel):
    statement: str = Field(min_length=1,max_length=2000)
    horizon_days: int = Field(ge=1,le=365)
    assumptions: list[str] = Field(min_length=1,max_length=10)
    supporting_evidence_ids: list[str] = Field(min_length=1,max_length=20)
    counter_evidence_ids: list[str] = Field(default_factory=list,max_length=20)
    uncertainty: str = Field(min_length=1,max_length=1000)
    direction:Literal['up','down','flat','abstain']|None=Field(default=None,description='Optional explicit directional hypothesis for historical evaluation. Abstain when evidence is insufficient; never force a prediction.')


class DecisionCondition(BaseModel):
    condition:str=Field(min_length=1,max_length=1200)
    evidence_ids:list[str]=Field(min_length=1,max_length=10)


class PurchaseDecision(BaseModel):
    horizon_days:int=Field(ge=1,le=365)
    conditions_to_consider:list[DecisionCondition]=Field(default_factory=list,max_length=5,description="Conditional buying research prerequisites, not an instruction to place an order. Ground them in actual evidence.")
    reasons_to_wait:list[DecisionCondition]=Field(min_length=1,max_length=5)
    invalidation_conditions:list[str]=Field(min_length=1,max_length=5)
    missing_information:list[str]=Field(min_length=1,max_length=10)
    monitor:list[str]=Field(min_length=1,max_length=8)
    cost_and_execution_notes:list[str]=Field(min_length=1,max_length=8,description="Discuss spread/fees/slippage and staged vs single entry as conditional options. Clearly label simulated assumptions; never invent user assets/risk tolerance.")
    simulation_evidence_ids:list[str]=Field(default_factory=list,max_length=5)


class ReportDraft(BaseModel):
    title: str = Field(min_length=1,max_length=200)
    summary: str = Field(min_length=1,max_length=3000)
    observations: list[Claim] = Field(min_length=1,max_length=30)
    key_changes: list[Claim] = Field(default_factory=list,max_length=6,
        description="Optional recent changes supported by original evidence. Compare actual dates/sources; do not invent a previous report or confirmed event. For dates/status/reasons use statement and evidence_ids only; omit metric/value/unit.")
    interpretations: list[Claim] = Field(default_factory=list,max_length=20)
    explanations: list[ResearchExplanation] = Field(default_factory=list, max_length=3,
        description='Optional compact explanations linking observations to a hypothesis, horizon, applicability, alternatives and invalidation. Use for substantive research when helpful; do not force competing views or forecasts.')
    questions_to_watch: list[str] = Field(default_factory=list,max_length=6,
        description="Optional unanswered questions or proposed follow-up checks. These are not occurred facts, directional forecasts or unsupported numeric trading thresholds.")
    forecasts: list[Forecast] = Field(default_factory=list,max_length=10)
    judgment: Literal["informational","consider","wait","avoid","insufficient_evidence"] = "informational"
    judgment_reason: str = Field(default="",max_length=2000,
        description="Optional for an informational brief. Required for any explicit investment/research judgment; explain evidence and limits.")
    limitations: list[str] = Field(min_length=1,max_length=20)
    purchase_decision:PurchaseDecision|None=Field(default=None,description="Include for reports intended to help consider purchasing ETH: conditions, contrary evidence, invalidation, missing user/data inputs, costs and monitoring. Ordinary summaries may omit.")
    advice:ResearchAdvice|None=Field(default=None,description="Use when suggesting what to investigate or monitor: source-linked rationale, conditions, counterpoints, invalidation and optional qualified historical cases. Fact summaries may omit.")

    @field_validator('questions_to_watch')
    @classmethod
    def meaningful_questions(cls, questions):
        if any(not question.strip() for question in questions):
            raise ValueError('Follow-up questions must contain meaningful text')
        return questions

    @model_validator(mode='after')
    def judgment_has_reason(self):
        if self.judgment!='informational' and not self.judgment_reason.strip():
            raise ValueError('An explicit research judgment requires a nonblank reason')
        return self


def report_claims(draft: ReportDraft) -> list[Claim]:
    """All factual/interpretive report claims, including optional key changes."""
    return [*draft.observations,*draft.key_changes,*draft.interpretations]


def validate_report(draft: ReportDraft, lookup, as_of=None):
    if draft.judgment!='informational' and not draft.judgment_reason.strip():
        raise ValueError('An explicit research judgment requires a nonblank reason')
    checked,numeric=validate_claims(report_claims(draft),lookup)
    explanation_check=validate_explanations(draft.explanations,lookup,as_of)
    checked.update(explanation_check['evidence_ids']);numeric+=explanation_check['numeric_claims_checked']
    advice_check=validate_advice(draft.advice,lookup,as_of)
    checked.update(advice_check['evidence_ids']);numeric+=advice_check['numeric_claims_checked']
    for forecast in draft.forecasts:
        for rid in [*forecast.supporting_evidence_ids,*forecast.counter_evidence_ids]:lookup(rid);checked.add(rid)
    if draft.purchase_decision:
        for condition in [*draft.purchase_decision.conditions_to_consider,*draft.purchase_decision.reasons_to_wait]:
            for rid in condition.evidence_ids:lookup(rid);checked.add(rid)
        for rid in draft.purchase_decision.simulation_evidence_ids:lookup(rid);checked.add(rid)
    if draft.judgment not in ("informational","insufficient_evidence") and not (draft.interpretations or draft.explanations):
        raise ValueError("An investment judgment requires at least one cited interpretation.")
    return {"status":"passed","unique_evidence_records":len(checked),"numeric_claims_checked":numeric,
            "explanations_checked":explanation_check['explanations_checked'],
            "scope":"Citation eligibility and structured numeric fields; does not prove the prose, causation or forecasts."}


def _numeric_note(claim):
    """Display convenience only; exact source key/value/unit are never mutated."""
    if not claim.metric:return []
    lines=[]
    if claim.unit=='fraction':
        try:
            value=Decimal(claim.value)
            if value.is_finite():
                lines += ['',f"  Approximate percentage: {(value*100).normalize():.6g}%."]
        except (InvalidOperation,TypeError,ValueError):pass
    lines += ['','  <details>','  <summary>Exact saved numeric field</summary>','',
        '  <code>'+escape(claim.metric)+'</code> = <code>'+escape(claim.value or '')+'</code> '+escape(claim.unit or ''),
        '', '  </details>', '']
    return lines


def render_report(draft: ReportDraft, cutoff, validation, sources, online_through=None, review=None):
    labels={"informational":"Research context","consider":"Conditions worth investigating","wait":"Wait and monitor","avoid":"Caution under current conditions","insufficient_evidence":"Insufficient evidence"}
    ids=[]
    def include(references):
        for rid in references:
            if rid not in ids:ids.append(rid)
    for claim in [*draft.key_changes,*draft.observations,*draft.interpretations]:include(claim.evidence_ids)
    include(explanation_evidence_ids(draft.explanations))
    for forecast in draft.forecasts:include([*forecast.supporting_evidence_ids,*forecast.counter_evidence_ids])
    if draft.purchase_decision:
        for condition in [*draft.purchase_decision.conditions_to_consider,*draft.purchase_decision.reasons_to_wait]:include(condition.evidence_ids)
        include(draft.purchase_decision.simulation_evidence_ids)
    include(advice_evidence_ids(draft.advice))
    numbers={rid:n+1 for n,rid in enumerate(ids)}
    def cite(references):return " ".join(f"[{numbers[rid]}](../records/{rid}.json)" for rid in references)
    lines=["# "+draft.title,"",draft.summary]
    if online_through:lines += ['', 'Source captures through: '+online_through+'. Publication and event dates remain separate.']
    def section(title,claims):
        if not claims:return
        lines.extend(['','## '+title,''])
        for claim in claims:
            lines.append('- '+claim.statement+' '+cite(claim.evidence_ids))
            lines.extend(_numeric_note(claim))
    section('Recent key changes',draft.key_changes)
    section('Main facts',draft.observations)
    section('Interpretation',draft.interpretations)
    if draft.explanations:lines += ['',render_explanations(draft.explanations,cite)]
    if draft.questions_to_watch:
        lines += ['','## Questions to follow up','',
            'These are proposed checks and open questions, not confirmed developments.']
        lines.extend('- '+question for question in draft.questions_to_watch)
    if draft.forecasts:
        lines += ['','## Conditional scenarios','']
        for f in draft.forecasts:
            lines += [f"- {f.horizon_days}-day horizon: {f.statement}","  Assumptions: "+"; ".join(f.assumptions),
                      "  Uncertainty: "+f.uncertainty,"  Supporting sources: "+cite(f.supporting_evidence_ids),
                      "  Contrary sources: "+(cite(f.counter_evidence_ids) or "No independent contrary source supplied")]
    if draft.judgment!='informational' or draft.judgment_reason.strip():
        lines += ["","## Research judgment","",labels[draft.judgment]+". "+draft.judgment_reason]
    if draft.purchase_decision:
        d=draft.purchase_decision;lines += ["","## Decision context","",f"Research horizon: {d.horizon_days} days","","Conditions to investigate:"]
        lines.extend("- "+c.condition+" "+cite(c.evidence_ids) for c in d.conditions_to_consider)
        lines.append("Reasons to wait / contrary evidence:")
        lines.extend("- "+c.condition+" "+cite(c.evidence_ids) for c in d.reasons_to_wait)
        for label,items in (("Invalidation conditions",d.invalidation_conditions),("Missing information",d.missing_information),("What to monitor",d.monitor),("Costs and execution assumptions",d.cost_and_execution_notes)):
            lines += ["",label+":",*["- "+item for item in items]]
        if d.simulation_evidence_ids:lines += ['','Supporting scenario evidence: '+cite(d.simulation_evidence_ids)]
    if draft.advice:lines += ['',render_advice(draft.advice,cite)]
    lines += ["","## Limits and unknowns",""]
    lines.extend("- "+item for item in draft.limitations)
    lines += ["","## Sources",""]
    for src in distinct_sources(sources):lines.append("- ["+src['name']+"]("+src['url']+") — retrieved "+src['retrieved_at'])
    lines += ['','Numbered citations open saved evidence in the downloadable bundle. Publisher/API links may show later content.',
        '', '<details>', '<summary>Source checks and technical provenance</summary>', '', '### Evidence review', '']
    if review and review.get('status')=='reviewed':
        lines.append(f"{review['claims_checked']} statements reviewed in a separate context by {review['reviewer_model']}.")
        for v in review.get('verdicts',[]):lines.append(f"- {v['claim_id']} · {v['status']}: {v['reason']}")
    else:lines.append("No separate-context source review is attached to this rendering.")
    lines += ["","Source support is an inspectable AI assessment, not proof of source truth, causation, or future investment performance.",
              "", "### Citation and numeric checks", "",
              "Status: "+str(validation.get('status','unknown'))+". Saved evidence records: "+str(validation.get('unique_evidence_records','not recorded'))+"; structured numeric fields checked: "+str(validation.get('numeric_claims_checked','not recorded'))+".",
              validation.get('scope','Checks cover citation eligibility and structured numeric fields, not prose truth or prediction quality.'),
              "","### Archive context","", "Reference historical bundle cutoff: "+cutoff+" (applies only to archived datasets)."]
    if online_through:lines.append("Current evidence retrieved through: "+online_through+"; individual observation times remain attached to their sources.")
    lines += ['', '</details>']
    return "\n".join(lines)+"\n"
