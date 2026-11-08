"""Small descriptive parsing hints; never coerce or compute source values."""
from decimal import Decimal,InvalidOperation


def numeric_contract(row):
    fields={}
    def inspect(obj,prefix):
        if not isinstance(obj,dict):return
        for key,value in obj.items():
            if len(fields)>=16:break
            if not isinstance(value,str) or len(value)>100:continue
            try:number=Decimal(value)
            except InvalidOperation:continue
            if number.is_finite():fields[prefix+key]='decimal_string'
    inspect(row.get('values'),'values.')
    for key in ('series','records'):
        rows=row.get(key)
        if isinstance(rows,list):
            for item in rows[:3]:
                inspect(item,key+'[].')
                if isinstance(item,dict):inspect(item.get('values'),key+'[].values.')
    if not fields:return None
    return {'sample_field_types':fields,'sample_only':True,
        'precision':'Source decimal strings are retained exactly. Null/missing values are unknown, not zero.',
        'python':'from decimal import Decimal; value = Decimal(raw_value)  # check for None first',
        'analysis':'Inspect scope, period, venue and units. Reuse an applicable saved calculation; recompute only missing metrics or a deliberate check.'}
