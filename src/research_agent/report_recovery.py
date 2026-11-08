"""Exact source binding and small revisions of immutable saved report drafts."""
from copy import deepcopy
import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    path:str=Field(min_length=2,max_length=180,description='Existing report JSON pointer. For questions_to_watch/limitations replace the whole string OR remove the optional list item, e.g. op=remove,path=/questions_to_watch/1. Keep at least one limitation; remove several indices in descending order. Strings have no evidence_ids child. To bind narrative sources replace /narrative_evidence_ids with saved IDs. Claim sources use /observations/1/evidence_ids.')
    op:Literal['replace','remove']='replace'
    value:Any=None


def bind_source_quote(text,quote):
    """Return actual source bytes-as-text, offsets and hash; never a paraphrase.

    A display quote differing only in whitespace may locate ONE canonical span.
    Punctuation, word and number differences or ambiguous alignment are rejected.
    Publication still validates the resulting exact quote and original response.
    """
    if not isinstance(text,str) or not isinstance(quote,str) or not quote.strip():
        raise ValueError('Quote binding needs saved source text and a nonempty quote')
    start=text.find(quote)
    method='exact'
    if start<0:
        chunks=[];offsets=[]
        for match in re.finditer(r'\s+|\S',text):
            chunks.append(' ' if match.group().isspace() else match.group())
            offsets.append((match.start(),match.end()))
        folded=''.join(chunks);needle=' '.join(quote.split())
        position=folded.find(needle)
        if position<0:raise ValueError('Quote does not match saved source words and punctuation')
        if folded.find(needle,position+1)>=0:raise ValueError('Whitespace-aligned quote is ambiguous; use an exact source substring')
        start=offsets[position][0];end=offsets[position+len(needle)-1][1]
        exact=text[start:end];method='unique_whitespace_alignment'
    else:end=start+len(quote);exact=quote
    return {'quote':exact,'start':start,'end':end,
        'sha256':hashlib.sha256(exact.encode()).hexdigest(),
        'source_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'method':method}


def bind_report_quotes(draft,lookup):
    """Bind display excerpts before the ordinary exact-source and Pro gates."""
    draft=draft.model_copy(deep=True);bindings=[]
    for index,document in enumerate(draft.documents):
        row=lookup(document.evidence_id)
        try:bound=bind_source_quote(row.get('text'),document.quote)
        except ValueError as exc:raise ValueError(f'Document {document.evidence_id}: {exc}') from exc
        if len(bound['quote'])>600:raise ValueError('Canonical quote exceeds the excerpt limit; select a shorter exact source span')
        if bound['method']!='exact':
            bindings.append({'evidence_id':document.evidence_id,'path':f'/documents/{index}/quote',
                'submitted_quote':document.quote,**bound})
            document.quote=bound['quote']
    return draft,bindings


def revise_report(payload,edits):
    """Apply explicit field edits to a copy; the old draft is never mutated."""
    from .reports import ReportDraft
    body=deepcopy(payload)
    body.setdefault('narrative_evidence_ids',[])  # Empty legacy drafts retain their original serialized hash.
    if len(edits)>20:raise ValueError('At most 20 focused draft edits per submission')
    for raw in edits:
        edit=ReportEdit.model_validate(raw) if isinstance(raw,dict) else raw
        parts=[part.replace('~1','/').replace('~0','~') for part in edit.path.split('/')[1:]]
        if not edit.path.startswith('/') or parts[0] not in ReportDraft.model_fields:
            raise ValueError('Draft edits must target report fields, not metadata or files')
        parent=body
        try:
            for part in parts[:-1]:parent=parent[int(part)] if isinstance(parent,list) and part.isdecimal() else parent[part]
            key=parts[-1]
            if isinstance(parent,list):
                if not key.isdecimal() or int(key)>=len(parent):raise KeyError(key)
                key=int(key)
            elif not isinstance(parent,dict) or key not in parent:raise KeyError(key)
            if edit.op=='remove':
                if isinstance(parent,list):parent.pop(key)
                else:del parent[key]
            else:parent[key]=edit.value
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            if isinstance(parent,str):
                owner='/'.join(edit.path.split('/')[:-1])
                raise ValueError('Draft edit must target an existing field: '+edit.path+'. '+owner+' is a string; replace the whole string. For narrative sources replace /narrative_evidence_ids, not a string/evidence_ids child.') from exc
            raise ValueError('Draft edit must target an existing field: '+edit.path) from exc
    return ReportDraft.model_validate(body)
