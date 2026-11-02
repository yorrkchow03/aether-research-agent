"""Concise source-gated research replies; facts render from reviewed claims only."""
from pydantic import BaseModel,Field,model_validator
from .claims import Claim
from .claims import validate_claims
from .advice import ResearchAdvice, advice_evidence_ids, advice_targets, validate_advice, render_advice
from .research_explanations import (ResearchExplanation, explanation_evidence_ids, explanation_targets,
                                    validate_explanations, render_explanations)


class AnswerDraft(BaseModel):
    facts:list[Claim]=Field(default_factory=list,max_length=12)
    interpretations:list[Claim]=Field(default_factory=list,max_length=6)
    explanations:list[ResearchExplanation]=Field(default_factory=list,max_length=3,
        description='Optional source-grounded explanations with horizon, conditions and limits. Brief fact answers may omit; do not force alternative hypotheses.')
    next_steps:list[str]=Field(default_factory=list,max_length=5,description='Questions or proposed research actions only. No ungrounded factual assertions or numeric decision thresholds.')
    advice:ResearchAdvice|None=Field(default=None,description='Optional conditional suggestion with evidence and qualified historical comparisons; ordinary fact answers may omit.')

    @model_validator(mode='after')
    def nonempty(self):
        if not (self.facts or self.interpretations or self.explanations or self.next_steps or self.advice):raise ValueError('Provide a sourced answer or a proposed clarification/research action')
        return self


def answer_targets(draft):
    targets=[{'claim_id':f'A{n+1}','kind':'observation',**c.model_dump()} for n,c in enumerate(draft.facts)]
    targets += [{'claim_id':f'I{n+1}','kind':'interpretation',**c.model_dump()} for n,c in enumerate(draft.interpretations)]
    ids=answer_evidence_ids(draft)
    targets += [{'claim_id':f'N{n+1}','kind':'research_action','statement':text,'evidence_ids':ids} for n,text in enumerate(draft.next_steps)]
    return targets+explanation_targets(draft.explanations)+advice_targets(draft.advice)


def validate_answer(draft,lookup,as_of=None):
    ids,numeric=validate_claims([*draft.facts,*draft.interpretations],lookup)
    explanation_check=validate_explanations(draft.explanations,lookup,as_of)
    ids.update(explanation_check['evidence_ids']);numeric+=explanation_check['numeric_claims_checked']
    check=validate_advice(draft.advice,lookup,as_of)
    ids.update(check['evidence_ids']);numeric+=check['numeric_claims_checked']
    return {'status':'passed','numeric_claims_checked':numeric,'unique_evidence_records':len(ids),
            'explanations_checked':explanation_check['explanations_checked'],
            'historical_cases_checked':check['historical_cases_checked']}


def answer_evidence_ids(draft):
    return list(dict.fromkeys([rid for claim in [*draft.facts,*draft.interpretations]
                              for rid in claim.evidence_ids]+explanation_evidence_ids(draft.explanations)+advice_evidence_ids(draft.advice)))


def render_answer(draft,source_links):
    paragraphs=[];indices={rid:i+1 for i,rid in enumerate(source_links)}
    for c in [*draft.facts,*draft.interpretations]:
        refs=' '.join(f'[{indices[rid]}]({source_links[rid]})' for rid in c.evidence_ids)
        paragraphs.append(c.statement+' '+refs)
    if draft.explanations:
        def cite(ids):return ' '.join(f'[{indices[rid]}]({source_links[rid]})' for rid in ids)
        paragraphs.append(render_explanations(draft.explanations,cite))
    if draft.next_steps:paragraphs.extend(draft.next_steps)
    if draft.advice:
        def cite(ids):return ' '.join(f'[{indices[rid]}]({source_links[rid]})' for rid in ids)
        paragraphs.append(render_advice(draft.advice,cite))
    return '\n\n'.join(paragraphs)
