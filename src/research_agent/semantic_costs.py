"""Versioned public-list-price estimates, separate from account debits.

Unknown usage is never zero. Ark Agent Plan AFP is not a cash price, and
reported cached input is not subtracted using an unrelated pay-as-you-go rate.
"""
from __future__ import annotations
from decimal import Decimal
import math

PRICE_SNAPSHOT={
    'checked_at':'2026-10-28',
    'jev':{'models':['jev-1.14.0','jev-1.13.0'],'currency':'USD',
        'input_per_million':'0.042','output_per_million':'0',
        'source':'https://docs.typesafe.ai/models',
        'shared_state_source':'https://docs.typesafe.ai/cookbooks/parallel_questions'},
    'doubao':{'model':'doubao-seed-2.1-pro','unit':'AFP',
        'input_coefficient':'2.5','output_coefficient':'2.5','denominator':'10000',
        'source':'https://docs.volcengine.com/docs/ark/agent-plan-personal-afp-credits-billing-rules?lang=zh',
        'cache_discount':'No independent Agent Plan cache coefficient verified; do not subtract cached token subsets.'}}


def token_count(usage,name):
    value=(usage or {}).get(name)
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or int(value)!=value:return None
    return int(value)


def estimate_cost(result):
    provider=result.get('provider');usage=result.get('usage')
    sent=bool(result.get('billable_request_made')) and not result.get('cache_hit')
    base={'pricing_checked_at':PRICE_SNAPSHOT['checked_at'],'provider':provider,
        'new_request_submitted':sent,'estimated_usd':None,'estimated_afp':None,
        'actual_account_debit':None,'input_tokens':token_count(usage,'input_tokens'),
        'output_tokens':token_count(usage,'output_tokens'),'complete_usage':False,
        'scope':'Published coefficient estimate on reported usage, not verified account debits, taxes or subscription amortization.'}
    if not sent:
        return base|{'estimated_usd':'0','estimated_afp':'0' if provider=='doubao' else None,
            'complete_usage':True,'scope':'No new provider request; historical cached request costs are separate.'}
    inputs,outputs=base['input_tokens'],base['output_tokens']
    if provider=='jev':
        model=result.get('model') or result.get('requested_model')
        quote=PRICE_SNAPSHOT['jev'];base['pricing_source']=quote['source']
        if model in quote['models'] and inputs is not None:
            base['estimated_usd']=format(Decimal(inputs)*Decimal(quote['input_per_million'])/Decimal(1000000),'f')
            base['complete_usage']=True  # Output is free under this pinned quote.
        base['scope']+=' State is shared across questions: do not multiply token usage by question count.'
    elif provider=='doubao':
        base['pricing_source']=PRICE_SNAPSHOT['doubao']['source']
        if inputs is not None and outputs is not None:
            quote=PRICE_SNAPSHOT['doubao']
            base['estimated_afp']=format((Decimal(inputs)*Decimal(quote['input_coefficient'])+Decimal(outputs)*Decimal(quote['output_coefficient']))/Decimal(quote['denominator']),'f')
            base['complete_usage']=True
        base['cache_token_details']=(usage or {}).get('input_token_details')
        base['scope']+=' AFP is a nominal listed-coefficient estimate without unverified cache discounts; cash cost remains unknown.'
    # Earlier/failed transport attempts may have usage beyond the final response.
    physical=result.get('physical_request_count')
    if physical is not None and physical>1:
        base['complete_usage']=False
        base['scope']+=' Final-response estimate only; earlier transport attempts may have unreported usage.'
    elif physical is None:
        base['complete_usage']=False
        base['scope']+=' Physical request attempts are unobserved; completeness cannot be established.'
    return base
