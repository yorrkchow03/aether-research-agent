"""Exact source binding and small revisions of immutable saved report drafts."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from .report_presentation import ReportScope, resolve_report_media
from .reports import ReportDraft
from .claims import validate_claims
from .etf_flow_validation import check_etf_flow_statement
from .host_review_facts import automatic_disclosure


LEGACY_ARCHIVE_LIMITATION = "Archived datasets are reconstructed by observation time; unknown publication times prevent strict point-in-time claims."
ARCHIVE_LIMITATION = ("Archived observations are reconstructed by their recorded observation times. "
                      "This reconstruction does not establish which records were available at those times.")
OMITTED_DOCUMENT_LIMITATION = "No original document cards are included in this report."

_DISPLAY_NUMBER = re.compile(
    r'(?<![\w.,])(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
    r'\s*(?P<scale>thousand\b|million\b|billion\b|trillion\b|[kmbt]\b|%)(?!\w)',
    re.I,
)
_DISPLAY_SCALES = {'k':'thousand','m':'million','b':'billion','t':'trillion'}


def _numeric_display(text):
    matches = _DISPLAY_NUMBER.finditer(text)
    return ((Decimal(match['number'].replace(',','')),
             _DISPLAY_SCALES.get(match['scale'].lower(), match['scale'].lower())) for match in matches)


def _can_keep_number_in_prose(claim):
    value = claim.get('value')
    statement = claim.get('statement')
    if not isinstance(value,str) or not isinstance(statement,str) or not _DISPLAY_NUMBER.fullmatch(value.strip()):
        return False
    try:
        number = next(_numeric_display(value.strip()))
        return number in _numeric_display(statement)
    except (InvalidOperation, StopIteration):
        return False


class LivePublicationDraft(ReportDraft):
    """Adapt a scoped full draft at the live submission boundary only."""
    _submitted: dict | None = PrivateAttr(default=None)
    _adjustments: list[dict] = PrivateAttr(default_factory=list)

    @model_validator(mode='wrap')
    @classmethod
    def recover_optional_input(cls, payload, handler):
        try:
            return handler(payload)
        except ValidationError as original_error:
            if not isinstance(payload, dict) or payload.get('delivery_kind', 'full') != 'full':
                raise
            scope = payload.get('scope')
            if not isinstance(scope, dict):
                raise
            corrected = dict(payload)
            changes = []
            if 'title' not in payload and isinstance(scope.get('title'), str) and scope['title'].strip():
                corrected['title'] = scope['title']
                corrected['scope'] = {key: value for key, value in scope.items() if key != 'title'}
                changes.append({'action': 'move', 'from': '/draft/scope/title', 'to': '/draft/title'})
            try:
                ReportScope.model_validate(corrected['scope'])
            except ValidationError:
                raise original_error from None
            for section in ('observations', 'key_changes', 'interpretations'):
                items = payload.get(section)
                if not isinstance(items, list):
                    continue
                retained = list(items)
                for error in original_error.errors(include_input=False, include_url=False):
                    loc = error['loc']
                    if len(loc) != 3 or loc[0] != section or not isinstance(loc[1], int) or loc[2] != 'value':
                        continue
                    index = loc[1]
                    claim = items[index]
                    if not isinstance(claim, dict) or not _can_keep_number_in_prose(claim):
                        continue
                    fields = ('metric','value','unit')
                    retained[index] = {key: value for key,value in claim.items() if key not in fields}
                    changes.append({'action': 'retain_as_prose', 'path': f'/draft/{section}/{index}',
                                    'removed_annotations': {key: claim[key] for key in fields if key in claim},
                                    'reason': 'The same amount is already explicit in the unchanged claim text. Source review still applies.'})
                corrected[section] = retained
            docs = payload.get('documents')
            if isinstance(docs, list):
                errors_by_index = {}
                for error in original_error.errors(include_input=False, include_url=False):
                    loc = error['loc']
                    if len(loc) >= 2 and loc[0] == 'documents' and isinstance(loc[1], int):
                        errors_by_index.setdefault(loc[1], []).append({
                            'path': '/draft/'+'/'.join(map(str, loc)), 'reason': error['msg']})
                # A length error applies to the collection, not an individual card.
                if errors_by_index and not any(e['loc'] == ('documents',) for e in original_error.errors(include_input=False)):
                    corrected['documents'] = [doc for index, doc in enumerate(docs) if index not in errors_by_index]
                    for index, errors in errors_by_index.items():
                        doc = docs[index]
                        changes.append({'action': 'omit', 'path': f'/draft/documents/{index}',
                                        'evidence_id': doc.get('evidence_id') if isinstance(doc, dict) else None,
                                        'errors': errors})
            if not changes:
                raise
        accepted = handler(corrected)
        accepted._submitted = deepcopy(payload)
        accepted._adjustments = changes
        return accepted


class LivePublishArguments(BaseModel):
    draft: LivePublicationDraft
    user_request: str = ''


class ReportEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    path:str=Field(min_length=2,max_length=180,description='Existing report JSON pointer. For questions_to_watch/limitations replace the whole string OR remove the optional list item, e.g. op=remove,path=/questions_to_watch/1. Keep at least one limitation; remove several indices in descending order. Strings have no evidence_ids child. To bind narrative sources replace /narrative_evidence_ids with saved IDs. Claim sources use /observations/1/evidence_ids.')
    op:Literal['replace','remove']='replace'
    value:Any=None


def evidence_reference_paths(body, record_id):
    """Locate the exact editable fields that point to a saved record."""
    single_fields = {'evidence_id'}
    list_fields = {'evidence_ids','narrative_evidence_ids','supporting_evidence_ids',
                   'counter_evidence_ids','context_evidence_ids','simulation_evidence_ids'}
    paths = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                child = path+'/'+key.replace('~','~0').replace('/','~1')
                if key in single_fields and value == record_id:
                    paths.append(child)
                elif key in list_fields and isinstance(value,list):
                    paths.extend(f'{child}/{index}' for index, item in enumerate(value)
                                 if item == record_id)
                else:
                    walk(value,child)
        elif isinstance(node,list):
            for index,item in enumerate(node):
                walk(item,f'{path}/{index}')

    walk(body,'')
    return paths


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
        binding = _bind_document_quote(document, lookup(document.evidence_id), index)
        if binding:
            bindings.append(binding)
    return draft,bindings


def _bind_document_quote(document, row, index):
    try:
        bound = bind_source_quote(row.get('text'), document.quote)
    except ValueError as exc:
        raise ValueError(f'Document {document.evidence_id}: {exc}') from exc
    if len(bound['quote']) > 600:
        raise ValueError('Canonical quote exceeds the excerpt limit; select a shorter exact source span')
    if bound['method'] == 'exact':
        return None
    binding = {'evidence_id': document.evidence_id, 'path': f'/documents/{index}/quote',
               'submitted_quote': document.quote, **bound}
    document.quote = bound['quote']
    return binding


def prepare_report_documents(draft, lookup, *, evidence_as_of):
    """Keep eligible optional cards for live publication, before full report validation."""
    from .host_review_facts import bound_record_timestamp

    if draft.scope is None or not draft.documents:
        draft, bindings = bind_report_quotes(draft, lookup)
        return draft, bindings, []
    if len({item.evidence_id for item in draft.documents}) != len(draft.documents):
        raise ValueError('Duplicate featured document')

    candidate = draft.model_copy(update={'charts': [], 'documents': []})
    resolve_report_media(candidate, lookup, evidence_as_of=evidence_as_of)

    kept, bindings, omitted = [], [], []
    for index, document in enumerate(draft.documents):
        receipt = {'evidence_id': document.evidence_id, 'path': f'/documents/{index}'}
        try:
            row = lookup(document.evidence_id)
        except (ValueError, KeyError, FileNotFoundError):
            omitted.append({**receipt, 'reason': 'Saved original is unavailable or ineligible.'})
            continue

        document = document.model_copy(deep=True)
        try:
            binding = _bind_document_quote(document, row, len(kept))
            candidate.documents = [document]
            resolve_report_media(candidate, lambda _: row, evidence_as_of=evidence_as_of)
            bound_record_timestamp({document.evidence_id: row})
            path = f'/documents/{index}/relevance'
            validate_claims([document.relevance], lambda _: row, claim_paths=[path])
            check_etf_flow_statement(document.relevance.statement, document.relevance.evidence_ids,
                                     lambda _: row, path=path)
        except ValueError as exc:
            # The media validator wraps read failures as ValueErrors.
            if isinstance(exc.__cause__, OSError) and not isinstance(exc.__cause__, FileNotFoundError):
                raise
            omitted.append({**receipt, 'reason': str(exc)})
            continue
        kept.append(document)
        if binding:
            bindings.append(binding)

    return draft.model_copy(update={'documents': kept}), bindings, omitted


_PLAIN_NUMBER = re.compile(r'(?<![\w.,])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w|[.,]\d)')


def bind_text_event_numbers(draft, lookup):
    """Keep exactly quoted numeric tokens in event prose for semantic review."""
    changes = []
    for index, event in enumerate(draft.key_changes):
        if event.metric is None or not all(rid.startswith('page-') for rid in event.evidence_ids):
            continue
        # A genuine structured number still needs exact field/unit validation.
        sources = [lookup(rid) for rid in event.evidence_ids]
        if any(source.get('dataset_id') != 'live.webpage' or source.get('values') for source in sources):
            continue
        value = Decimal(event.value)

        def mentions_value(text):
            return isinstance(text, str) and any(
                Decimal(match.group().replace(',', '')) == value for match in _PLAIN_NUMBER.finditer(text))

        witnesses = [source['record_id'] for source in sources if mentions_value(source.get('text'))]
        if not witnesses or not mentions_value(event.statement):
            continue
        changes.append({'path': f'/key_changes/{index}', 'witness_ids': witnesses,
            'removed_annotations': {'metric': event.metric, 'value': event.value, 'unit': event.unit},
            'reason': 'Value appears in the unchanged event and original page text. The event still requires semantic source review.'})
        event.metric = event.value = event.unit = None
    return changes


def omit_rejected_optional_items(draft, review):
    """Remove only optional items identified in a complete, draft-bound semantic review."""
    if draft.delivery_kind != 'full' or review.get('status') != 'needs_revision':
        return None
    if review.get('reviewer_provider') == 'jev':
        if review.get('unassessed_targets') or any(batch['status'] != 'validated' for batch in review['batches']):
            return None
    elif review.get('review_stage') != 'full_review':
        return None

    issues = review.get('revision_required', [])
    if not issues:
        return None
    targets = {target['claim_id']: target for target in review['targets']}
    if {verdict['claim_id'] for verdict in review.get('verdicts', [])} != set(targets):
        return None
    omissions = {}
    for issue in issues:
        target = targets.get(issue['claim_id'], {})
        path = target.get('claim_path', '')
        match = re.fullmatch(r'/(questions_to_watch|limitations)/(\d+)|/documents/(\d+)/relevance', path)
        if not match:
            return None
        section = match[1] or 'documents'
        index = int(match[2] or match[3])
        items = getattr(draft, section)
        if index >= len(items):
            return None
        item = items[index]
        statement = item.relevance.statement if section == 'documents' else item
        if target.get('statement') != statement:
            return None
        if section == 'limitations' and (automatic_disclosure(item) or item in (
            ARCHIVE_LIMITATION, LEGACY_ARCHIVE_LIMITATION, OMITTED_DOCUMENT_LIMITATION,
        )):
            return None
        key = (section, index)
        receipt = omissions.setdefault(key, {'claim_id': issue['claim_id'], 'path': f'/{section}/{index}', 'reasons': []})
        if issue['reason'] not in receipt['reasons']:
            receipt['reasons'].append(issue['reason'])

    sections = {section for section, _ in omissions}
    remaining = {section: [item for index, item in enumerate(getattr(draft, section)) if (section, index) not in omissions]
                 for section in sections}
    # One rejected clause can share a limitation with a material qualification.
    # Auto-omit only a duplicate whose exact caution survives in this section.
    for section, index in omissions:
        if section == 'limitations':
            caution = draft.limitations[index]
            if not caution.strip() or caution not in remaining['limitations']:
                return None
    return draft.model_copy(update=remaining), list(omissions.values())


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


def bind_calculation_chart_inputs(draft, lookup):
    """Cite displayed direct inputs of the registered calculation supporting a numeric claim."""
    from functools import cache
    from .claims import validate_claims

    sources = cache(lookup)
    chart_ids = {chart.evidence_id for chart in draft.charts}
    bindings = []
    for section in ('observations', 'key_changes'):
        for index, claim in enumerate(getattr(draft, section)):
            if claim.metric is None or claim.value is None or claim.unit is None:
                continue
            for rid in list(claim.evidence_ids):
                if not rid.startswith('live-') or rid in chart_ids:
                    continue
                row = sources(rid)
                if (row.get('record_id') != rid or row.get('dataset_id') != 'live.calculation'
                        or not {'recipe_reexecuted', 'declared_inputs_replayed'}.issubset(row.get('quality_flags') or [])
                        or not re.fullmatch(r'[0-9a-f]{64}', row.get('recipe_sha256') or '')):
                    continue
                candidates = [source for source in dict.fromkeys(row.get('input_record_ids', []))
                              if source in chart_ids and source not in claim.evidence_ids]
                if not candidates:
                    continue
                # The registered result must support the value and unit before adding any chart.
                try:
                    validate_claims([claim.model_copy(update={'evidence_ids': [rid]})], sources)
                except ValueError:
                    continue
                if len(claim.evidence_ids) + len(candidates) > 20:
                    continue
                claim.evidence_ids = [*claim.evidence_ids, *candidates]
                bindings.append({'claim_path': f'/{section}/{index}', 'calculation_id': rid,
                                 'recipe_sha256': row['recipe_sha256'], 'added_input_ids': candidates})
    return bindings
