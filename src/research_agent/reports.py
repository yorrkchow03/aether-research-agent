"""Typed research reports with mandatory citation and numeric-field checks."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import cache
from html import escape
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from .claims import Claim, validate_claims
from .advice import ResearchAdvice, advice_evidence_ids, validate_advice, render_advice
from .research_explanations import (ResearchExplanation, explanation_evidence_ids,
                                    validate_explanations, render_explanations)
from .report_presentation import (FeaturedDocument, ReportChart, ReportScope,
                                  presentation_evidence_ids, resolve_report_media,
                                  report_display_limits, report_display_summary, report_display_title,
                                  report_timeline, report_watch_items)
from .etf_flow_validation import check_etf_flow_statement


GREGORIAN_WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
_LEADING_DATE_WEEKDAY = re.compile(
    r'^\s*(\d{4}-\d{2}-\d{2})\s*\((' + '|'.join(GREGORIAN_WEEKDAYS) + r')\)',
    re.IGNORECASE,
)


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
    scope: ReportScope | None = Field(default=None, description="Research assets and time window. Required for new current research reports.")
    charts: list[ReportChart] = Field(default_factory=list, max_length=3,
        description="Select only central time-series metrics. Give a saved series or candle ID and exact metric. The host supplies points, dates and units.")
    documents: list[FeaturedDocument] = Field(default_factory=list, max_length=5,
        description="Select only important originals. Cite each saved page ID with an exact excerpt and explain its relevance. Label background, corrections and updates outside the window.")
    narrative_evidence_ids: list[str] = Field(default_factory=list,max_length=10,
        exclude_if=lambda value:not value,
        description="Optional saved source IDs for summary, questions, limitations and judgment only. Explicitly bind narrative-only facts here; IDs written inside plain strings do not bind. Other claims retain their own evidence_ids.")
    delivery_kind: Literal["full","minimal","evidence_gap"] = Field(default="full", exclude_if=lambda value:value=="full")
    observations: list[Claim] = Field(min_length=0,max_length=30,
        description="Key indicators and other supporting facts. For a period report, describe meaningful changes with the comparison window, venue and units.")
    key_changes: list[Claim] = Field(default_factory=list,max_length=12,
        description="Key event summaries for the requested assets and window, separate from chart metrics and featured document cards. Give one concise, dated, source-bound entry per material event; merge duplicate reporting and keep important corrections or disputed status. Use statement and evidence_ids for qualitative events.")
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
        if self.delivery_kind=='full' and not self.observations:
            raise ValueError('A research report requires at least one observation')
        if self.delivery_kind=='minimal' and not (self.observations or self.key_changes):
            raise ValueError('A limited report requires at least one observation or event')
        if self.delivery_kind=='evidence_gap':
            from .minimal_delivery import NOTICE_TITLE,NOTICE_SUMMARY,NOTICE_REASON,NOTICE_LIMIT
            if (self.title!=NOTICE_TITLE or self.summary!=NOTICE_SUMMARY or self.judgment_reason!=NOTICE_REASON
                or self.limitations!=[NOTICE_LIMIT] or self.judgment!='insufficient_evidence'
                or any((self.observations,self.key_changes,self.interpretations,self.forecasts,self.questions_to_watch,
                    self.charts,self.documents,self.narrative_evidence_ids,self.explanations,self.advice,self.purchase_decision))):
                raise ValueError('Evidence-gap delivery accepts only literal host notice text and no external facts')
        return self


def report_claims(draft: ReportDraft) -> list[Claim]:
    """All factual/interpretive report claims, including optional key changes."""
    return [*draft.observations,*draft.key_changes,*draft.interpretations]


def validate_report(draft: ReportDraft, lookup, as_of=None, *, raw_reader=None, check_etf_flows=True):
    if draft.judgment!='informational' and not draft.judgment_reason.strip():
        raise ValueError('An explicit research judgment requires a nonblank reason')
    lookup=cache(lookup)
    claim_paths=[f'/{field}/{index}' for field in ('observations','key_changes','interpretations')
                 for index, _ in enumerate(getattr(draft,field))]
    claims=report_claims(draft)
    calendar_labels_checked=0
    for claim,path in zip(claims,claim_paths):
        label=_LEADING_DATE_WEEKDAY.match(claim.statement)
        if label:
            day,weekday=label.groups()
            try:
                expected=GREGORIAN_WEEKDAYS[date.fromisoformat(day).weekday()]
            except ValueError:
                raise ValueError(f'Invalid calendar date {day} at {path}/statement') from None
            if weekday.casefold()!=expected.casefold():
                raise ValueError(f'Calendar label at {path}/statement says {weekday}, but {day} falls on '
                                 f'{expected}. Check the event date with the original before editing.')
            calendar_labels_checked+=1
    checked,numeric=validate_claims(claims,lookup,claim_paths=claim_paths)
    for rid in draft.narrative_evidence_ids:lookup(rid);checked.add(rid)
    media=resolve_report_media(draft,lookup,evidence_as_of=as_of,raw_reader=raw_reader)
    document_claims=[item.relevance for item in draft.documents]
    document_paths=[f'/documents/{index}/relevance' for index, _ in enumerate(draft.documents)]
    doc_checked,doc_numeric=validate_claims(document_claims,lookup,claim_paths=document_paths)
    checked.update(doc_checked);numeric+=doc_numeric
    checked.update(presentation_evidence_ids(draft))
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
    result={"status":"passed","unique_evidence_records":len(checked),"numeric_claims_checked":numeric,
            "explanations_checked":explanation_check['explanations_checked'],
            "scope":"Citation eligibility and structured numeric fields; does not prove the prose, causation or forecasts."}
    if calendar_labels_checked:
        result['calendar_labels_checked']=calendar_labels_checked
    if check_etf_flows:
        dated_totals = sum(
            check_etf_flow_statement(claim.statement, claim.evidence_ids, lookup, path=path, raw_reader=raw_reader)
            for claim, path in zip(claims + document_claims, claim_paths + document_paths)
        )
        narrative = [('/title', draft.title), ('/summary', draft.summary), ('/judgment_reason', draft.judgment_reason)]
        narrative.extend((f'/limitations/{index}', text) for index, text in enumerate(draft.limitations))
        narrative_ids = sorted(checked)
        dated_totals += sum(
            check_etf_flow_statement(text, narrative_ids, lookup, path=path, raw_reader=raw_reader)
            for path, text in narrative
        )
        if dated_totals:
            result['farside_dated_totals_checked'] = dated_totals
    if draft.scope is not None:
        result.update(charts_checked=len(media["charts"]),
                      chart_points_checked=sum(len(chart["points"]) for chart in media["charts"]),
                      featured_documents_checked=len(media["documents"]))
    return result


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


def render_report(draft: ReportDraft, cutoff, validation, sources, online_through=None, review=None,
                  presentation=None, *, lookup=None):
    labels={"informational":"Research context","consider":"Conditions worth investigating","wait":"Wait and monitor","avoid":"Caution under current conditions","insufficient_evidence":"Insufficient evidence"}
    ids=[]
    def include(references):
        for rid in references:
            if rid not in ids:ids.append(rid)
    for claim in [*draft.observations,*draft.key_changes,*draft.interpretations]:include(claim.evidence_ids)
    include(explanation_evidence_ids(draft.explanations))
    for forecast in draft.forecasts:include([*forecast.supporting_evidence_ids,*forecast.counter_evidence_ids])
    if draft.purchase_decision:
        for condition in [*draft.purchase_decision.conditions_to_consider,*draft.purchase_decision.reasons_to_wait]:include(condition.evidence_ids)
        include(draft.purchase_decision.simulation_evidence_ids)
    include(advice_evidence_ids(draft.advice))
    include(presentation_evidence_ids(draft))
    numbers={rid:n+1 for n,rid in enumerate(ids)}
    def cite(references):return " ".join(f"[{numbers[rid]}](../records/{rid}.json)" for rid in references)
    factual = draft.delivery_kind != "evidence_gap"
    lines=["# "+report_display_title(draft),"","## Summary","",report_display_summary(draft)]
    def section(title,claims,level=2):
        if not claims:return
        lines.extend(['','#'*level+' '+title,''])
        for claim in claims:
            lines.append('- '+claim.statement+' '+cite(claim.evidence_ids))
            lines.extend(_numeric_note(claim))

    if presentation:
        overview=presentation["overview"]
        lines += ["",
                  "- Generated at " + overview["created_at"],
                  "- Assets " + ", ".join(overview["assets"]),
                  "- Research window " + overview["start"] + " to " + overview["end"] + " (exclusive end)",
                  "- Evidence cutoff " + overview["evidence_as_of"]]
    charts = (presentation or {}).get("charts", [])
    if factual:
        if charts:
            lines += ["", "## Key data charts", ""]
        for chart in charts:
            first,last=chart["points"][0],chart["points"][-1]
            lines += ["", "### " + escape(chart["asset"] + " " + chart["title"]), "",
                      f'{chart["provider"]} · {chart["frequency"]} · {chart["unit"]} · '
                      f'{len(chart["points"])} saved observations. ' + cite([chart["evidence_id"]]),
                      f'First point {first["value"]} at {first["at"]}. Last point {last["value"]} at {last["at"]}.']
            coverage=chart["coverage"]
            if coverage.get("starts_after_window") or coverage.get("ends_before_window") or coverage["internal_gap_count"]:
                lines.append("Saved observations do not span the full window continuously.")
        metrics = [claim for claim in draft.observations if claim.metric]
        if metrics:
            section('Key metric changes',metrics)
        else:
            lines += ["", "## Key metric changes", "", "No structured metrics were available for this report."]
        cards = report_timeline(draft,presentation,lookup)
        lines += ["", "## Key events", ""]
        if not cards:
            lines.append("The saved sources did not establish any key events for this report.")
        for card in cards:
            label = card["date_label"] or "Date not recorded"
            if card["date_kind"] == "publication":
                label = "Published " + label
            lines += ["", "### " + escape(label), ""]
            if card["display_statement"]:
                lines.append(card["display_statement"] + " " + cite(card["evidence_ids"]))
            if card["measurement"]:
                lines.extend(_numeric_note(Claim.model_validate(card["measurement"])))
            for fact in card["facts"]:
                lines.append("- " + fact["statement"] + " " + cite(fact["evidence_ids"]))
            for item in card["documents"]:
                role = {"primary":"main in-window evidence"}.get(item["role"],item["role"].replace("_"," "))
                lines += ["", "**" + escape(item["title"]) + "**",
                          f'{escape(item["publisher"])} · Published {item["published_at"]} · {role}',
                          f'[Captured publication](<{item["url"]}>) ' + cite([item["evidence_id"]]),
                          "", "<blockquote>" + escape(item["quote"]) + "</blockquote>", "",
                          escape(item["relevance"])]
    if factual:
        lines += ["", "## Research judgment", ""]
        if draft.judgment_reason.strip():
            lines.append(labels[draft.judgment]+". "+draft.judgment_reason)
        elif not any((draft.interpretations,draft.explanations,draft.forecasts,draft.purchase_decision,draft.advice)):
            lines.append("The saved sources did not support a separate research judgment.")
    elif draft.judgment_reason.strip():
        lines += ["", "## Research judgment", "", labels[draft.judgment]+". "+draft.judgment_reason]
    section('Interpretation',draft.interpretations,level=3)
    if draft.explanations:
        lines += ['',re.sub(r"^(#{2,5}) ",r"#\1 ",render_explanations(draft.explanations,cite),flags=re.M)]
    if draft.forecasts:
        lines += ['','### Conditional scenarios','']
        for f in draft.forecasts:
            lines += [f"- {f.horizon_days}-day horizon: {f.statement}","  Assumptions: "+"; ".join(f.assumptions),
                      "  Uncertainty: "+f.uncertainty,"  Supporting sources: "+cite(f.supporting_evidence_ids),
                      "  Contrary sources: "+(cite(f.counter_evidence_ids) or "No independent contrary source supplied")]
    if draft.purchase_decision:
        d=draft.purchase_decision;lines += ["","### Decision context","",f"Research horizon: {d.horizon_days} days","","Conditions to investigate:"]
        lines.extend("- "+c.condition+" "+cite(c.evidence_ids) for c in d.conditions_to_consider)
        lines.append("Reasons to wait / contrary evidence:")
        lines.extend("- "+c.condition+" "+cite(c.evidence_ids) for c in d.reasons_to_wait)
        for label,items in (("Invalidation conditions",d.invalidation_conditions),("Missing information",d.missing_information),("What to monitor",d.monitor),("Costs and execution assumptions",d.cost_and_execution_notes)):
            lines += ["",label+":",*["- "+item for item in items]]
        if d.simulation_evidence_ids:lines += ['','Supporting scenario evidence: '+cite(d.simulation_evidence_ids)]
    if draft.advice:lines += ['',re.sub(r"^(#{2,5}) ",r"#\1 ",render_advice(draft.advice,cite),flags=re.M)]
    if factual:
        lines += ['','## What to watch next','']
        follow_ups = report_watch_items(draft.questions_to_watch)
        lines.extend(['- '+question for question in follow_ups] or ["No specific follow-up was identified in the saved sources."])
    lines += ["","## Limits and unknowns",""]
    lines.extend("- "+item for item in report_display_limits(draft))
    lines += ["","## Sources, review and downloads",""]
    for src in distinct_sources(sources):lines.append("- ["+src['name']+"]("+src['url']+") — retrieved "+src['retrieved_at'])
    lines += ['','Numbered citations open saved evidence in the downloadable bundle. Publisher/API links may show later content.',
        '', '<details>', '<summary>Source checks and technical provenance</summary>']
    if presentation:
        lines += ['', '### Original task question', '', '<pre>'+escape(presentation['overview']['user_prompt'])+'</pre>']
    lines += ['', '### Evidence review', '']
    if review and review.get('status')=='reviewed':
        lines.append(f"{review['claims_checked']} statements reviewed in a separate context by {review['reviewer_model']}.")
        for v in review.get('verdicts',[]):lines.append(f"- {v['claim_id']} · {v['status']}: {v['reason']}")
    else:lines.append("No separate-context source review is attached to this rendering.")
    lines += ["","Source support is an inspectable AI assessment, not proof of source truth, causation, or future investment performance.",
              "A captured media publication remains that publisher's account; main in-window evidence does not imply an issuer's first-hand statement.",
              "", "### Citation and numeric checks", "",
              "Status: "+str(validation.get('status','unknown'))+". Saved evidence records: "+str(validation.get('unique_evidence_records','not recorded'))+"; structured numeric fields checked: "+str(validation.get('numeric_claims_checked','not recorded'))+".",
              validation.get('scope','Checks cover citation eligibility and structured numeric fields, not prose truth or prediction quality.'),
              "","### Archive context","", "Reference historical bundle cutoff: "+cutoff+" (applies only to archived datasets)."]
    if online_through:lines.append("Current evidence retrieved through: "+online_through+"; individual observation times remain attached to their sources.")
    lines += ['', '</details>']
    return "\n".join(lines)+"\n"
