"""Bounded transport compatibility for coarsely reported Jev distributions.

The raw scores are never normalized or substituted. This tests existence of a
possible two-decimal pre-rounded distribution, not vendor-confirmed precision,
calibration or the truth of a selected label. Boundary0/1values remain exact.
"""
from decimal import Decimal

VERSION='bounded-two-decimal-feasibility-v1'

def probability_mass_metadata(probabilities,selected,provider):
    if not isinstance(probabilities,dict) or not probabilities or selected not in probabilities:raise ValueError('Invalid probability option map')
    values={k:Decimal(str(v)) for k,v in probabilities.items()}
    if any(not v.is_finite() or not 0<=v<=1 for v in values.values()):raise ValueError('Invalid probability value')
    if values[selected]+Decimal('0.00000001')<max(values.values()):raise ValueError('Selected option is not a maximum')
    mass=sum(values.values());kind='normalized_within_tolerance'
    if abs(mass-1)>Decimal('0.0001'):
        if provider!='jev' or any(v*100!=(v*100).to_integral_value() for v in values.values()):
            raise ValueError('Probabilities do not sum to one')
        bounds={k:(v-Decimal('.005'),v+Decimal('.005')) if 0<v<1 else (v,v) for k,v in values.items()}
        lo,hi=bounds[selected];others=[b for key,b in bounds.items() if key!=selected]
        # Max feasible selected mass while leaving every other lower bound.
        chosen=min(hi,Decimal(1)-sum(b[0] for b in others))
        if chosen<lo or any(b[0]>chosen for b in others) or chosen+sum(min(b[1],chosen) for b in others)<1:
            raise ValueError('Probabilities do not fit bounded two-decimal rounding')
        kind='bounded_two_decimal_rounding_compatible'
    return {'probability_mass':float(mass),'probability_representation':kind,
        'probabilities_normalized':False,'probability_validation_version':VERSION,
        'probability_scope':'Raw provider values retained. Rounded-compatible scores are not an exact normalized distribution or a correctness certificate.'}
