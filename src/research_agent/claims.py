"""Shared source and numeric contract for observations and advice evidence."""
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase

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


def validate_claims(claims, lookup):
    checked = set()
    numeric = 0
    records_by_id = {}
    for claim in claims:
        # One validation invocation inspects one source snapshot. Do not replay
        # a many-thousand-row collection again for every metric on that source.
        # The cache is local; the next submission revalidates original inputs.
        for rid in claim.evidence_ids:
            if rid not in records_by_id:records_by_id[rid]=lookup(rid)
        records = [records_by_id[rid] for rid in claim.evidence_ids]
        checked.update(claim.evidence_ids)
        if (claim.metric is None) != (claim.value is None):
            raise ValueError("Numeric claims require both metric and value")
        if claim.unit is not None and claim.metric is None:
            raise ValueError("A unit requires a metric and value")
        if claim.metric is not None:
            candidates = []
            for record in records:
                raw = record.get('values', {}).get(claim.metric)
                if raw is None:
                    continue
                try:value = Decimal(str(raw))
                except (InvalidOperation, ValueError):continue
                units = {unit for pattern, unit in record.get('units', {}).items()
                         if fnmatchcase(claim.metric, pattern)}
                if value.is_finite() and claim.unit in units:
                    candidates.append(value)
            if claim.unit is None or not candidates:
                raise ValueError("Numeric unit or cited field mismatch: " + claim.metric)
            try:actual = Decimal(claim.value)
            except (InvalidOperation, ValueError):
                raise ValueError('Numeric claim is not a decimal value: ' + claim.metric) from None
            if not actual.is_finite() or not any(
                abs(actual - value) <= max(abs(value) * Decimal('1e-10'), Decimal('1e-12'))
                for value in candidates
            ):
                raise ValueError("Numeric value differs from saved evidence: " + claim.metric)
            numeric += 1
    return checked, numeric
