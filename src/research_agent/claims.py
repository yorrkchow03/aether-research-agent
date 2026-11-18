"""Shared source and numeric contract for observations and advice evidence."""
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase
import json

from pydantic import BaseModel, Field, field_validator, model_validator


class Claim(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    metric: str | None = Field(default=None, min_length=1, description="Exact English values key from the cited record. Omit for qualitative claims.")
    value: str | None = Field(default=None, min_length=1, description="Exact raw numeric string. Do not round, scale or turn fractions into percentages.")
    unit: str | None = Field(default=None, min_length=1, description="Exact cited units value. Omit with metric/value for qualitative claims.")

    @field_validator('value')
    @classmethod
    def finite_numeric_annotation(cls, value):
        if value is None:return value
        try:valid=Decimal(value).is_finite()
        except (InvalidOperation,ValueError):valid=False
        if not valid:
            raise ValueError('value is only for a finite raw number from evidence.values. For dates, status, reasons or other qualitative claims, omit metric, value and unit; keep statement and evidence_ids.')
        return value

    @model_validator(mode='after')
    def complete_numeric_annotation(self):
        present=[getattr(self,key) is not None for key in ('metric','value','unit')]
        if any(present) and not all(present):
            raise ValueError('Numeric annotations require metric, numeric value and unit together. Omit all three for qualitative claims.')
        return self


def _diagnostic_scalar(value, limit=100):
    """Expose only a bounded requested field, never an enclosing source body."""
    if not isinstance(value, (str, int, float, bool, type(None))):
        return {'type': type(value).__name__, 'scope': 'Read the cited source field.'}
    if isinstance(value, str) and len(value) > limit:
        return {'preview': value[:limit], 'truncated': True}
    return value


def _numeric_error(reason, claim, records, path, index):
    label = path or 'claim[' + str(index) + ']'
    submitted = {key: _diagnostic_scalar(getattr(claim, key)) for key in ('metric', 'value', 'unit')}
    sources = []
    for rid, record in zip(claim.evidence_ids, records):
        values = record.get('values', {})
        matching_units = {pattern: _diagnostic_scalar(unit) for pattern, unit in record.get('units', {}).items()
                          if fnmatchcase(claim.metric, pattern)}
        sources.append({'record_id': rid, 'values_key_present': claim.metric in values,
                        'value': _diagnostic_scalar(values.get(claim.metric)), 'units': matching_units})
    repair = 'Use the exact saved values key/value/unit; never invent a field. '
    repair += ('If the figure exists only in page text, keep it in statement + evidence_ids for source review '
               'and omit all three numeric annotations. ')
    if path:
        edits = [{'path': path + '/' + key, 'op': 'remove'} for key in ('metric', 'value', 'unit')]
        repair += 'For that text-only case, publish_saved_report edits=' + json.dumps(edits, separators=(',', ':')) + '. '
    else:
        repair += 'Remove metric, value and unit together from this claim; source review still applies. '
    return ValueError(reason + ' at ' + label + ' (' + claim.statement[:100] + '). submitted='
        + json.dumps(submitted, ensure_ascii=False, separators=(',', ':')) + '. ' + repair
        + 'Cited field checks=' + json.dumps(sources, ensure_ascii=False, separators=(',', ':')))


def _numeric_issue(claim, records):
    if (claim.metric is None) != (claim.value is None):
        return 'Numeric claims require both metric and value'
    if claim.unit is not None and claim.metric is None:
        return 'A unit requires a metric and value'
    if claim.metric is None:
        return None
    candidates = []
    for record in records:
        raw = record.get('values', {}).get(claim.metric)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        units = {unit for pattern, unit in record.get('units', {}).items() if fnmatchcase(claim.metric, pattern)}
        if value.is_finite() and claim.unit in units:
            candidates.append(value)
    if claim.unit is None or not candidates:
        return 'Numeric unit or cited field mismatch'
    try:
        actual = Decimal(claim.value)
    except (InvalidOperation, ValueError):
        return 'Numeric claim is not a decimal value'
    if not actual.is_finite() or not any(
        abs(actual - value) <= max(abs(value) * Decimal('1e-10'), Decimal('1e-12')) for value in candidates
    ):
        return 'Numeric value differs from saved evidence'
    return None


def _numeric_issues_error(errors):
    if len(errors) == 1:
        reason, claim, records, path, index = errors[0]
        if claim.metric is None or claim.value is None:
            return ValueError(reason)
        return _numeric_error(reason, claim, records, path, index)

    rows = []
    text_only = []
    for reason, claim, records, path, index in errors:
        label = path or f'claim[{index}]'
        rows.append(f'{label}: {reason}; metric={_diagnostic_scalar(claim.metric)}')
        if (path and claim.metric is not None and claim.value is not None and claim.unit is not None
                and all(rid.startswith('page-') and claim.metric not in record.get('values', {})
                        for rid, record in zip(claim.evidence_ids, records))):
            text_only.append(path)
    message = f'{len(rows)} numeric annotations failed. '+' | '.join(rows)+'. '
    message += 'For structured figures, use the exact saved values key/value/unit. '
    if text_only:
        # A saved-draft patch accepts 20 edits; one text-only annotation uses three.
        edits = [{'path':path+'/'+key, 'op':'remove'} for path in text_only[:6] for key in ('metric','value','unit')]
        message += ('For figures independently found in the cited page text, keep statement + evidence_ids for source review. '
                    'Example publish_saved_report edits='+json.dumps(edits, separators=(',',':'))+'.')
        if len(text_only) > 6:
            message += ' Apply the same three removals for the remaining verified text figures in groups of up to six claims: '+', '.join(text_only[6:])+'.'
    return ValueError(message)


def validate_claims(claims, lookup, *, claim_paths=None):
    checked = set()
    numeric = 0
    errors = []
    records_by_id = {}
    for index, claim in enumerate(claims):
        path = claim_paths[index] if claim_paths is not None else None
        # One validation invocation inspects one source snapshot. Do not replay
        # a many-thousand-row collection again for every metric on that source.
        # The cache is local; the next submission revalidates original inputs.
        for rid in claim.evidence_ids:
            if rid not in records_by_id:records_by_id[rid]=lookup(rid)
        records = [records_by_id[rid] for rid in claim.evidence_ids]
        checked.update(claim.evidence_ids)
        reason = _numeric_issue(claim, records)
        if reason:
            errors.append((reason, claim, records, path, index))
        elif claim.metric is not None:
            numeric += 1
    if errors:
        raise _numeric_issues_error(errors)
    return checked, numeric
