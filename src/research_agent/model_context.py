"""Provider-documented context capacities and local compaction policy.

The automatic trigger is an operating default, not a claim of measured optimal
quality. Explicit settings remain supported within the known model capacity.
"""
from numbers import Integral


_MILLION_CONTEXT_MODELS = frozenset({
    'doubao-seed-2.1-pro', 'doubao-seed-2-1-pro-260915',
    'doubao-seed-2.1-lite', 'doubao-seed-2-1-lite-260915',
    'deepseek-v4-pro', 'deepseek-v4-pro-ga-260813',
    'kimi-k3.1-pro', 'kimi-k3-1-pro-261024',
})
DEFAULT_OUTPUT_TOKENS = 8192


def documented_context_capacity(model_id):
    """Return capacity only for verified current Plan aliases/revisions."""
    return 1024000 if model_id in _MILLION_CONTEXT_MODELS else None


def context_policy(model=None, *, model_id=None, trigger_tokens=None, keep_tokens=None):
    profile = getattr(model, 'profile', None) or {}
    capacity = profile.get('max_input_tokens') if isinstance(profile, dict) else None
    identity = model_id or getattr(model, 'model_name', None) or getattr(model, 'model', None)
    if not isinstance(capacity, Integral) or isinstance(capacity, bool) or capacity < 16000:
        capacity = documented_context_capacity(identity)
    known = capacity is not None
    capacity = int(capacity or 128000)
    reserve = max(16384, int(getattr(model, 'max_tokens', None) or DEFAULT_OUTPUT_TOKENS) + 8192)
    automatic = trigger_tokens is None
    if automatic:
        trigger_tokens = capacity // 2
    if (isinstance(trigger_tokens, bool) or not isinstance(trigger_tokens, Integral)
            or not 1 <= trigger_tokens <= capacity - reserve):
        raise ValueError('Context trigger must leave output/system headroom within the model context capacity')
    if keep_tokens is None:
        keep_tokens = min(65536, max(1, int(trigger_tokens) // 8))
    if (isinstance(keep_tokens, bool) or not isinstance(keep_tokens, Integral)
            or not 1 <= keep_tokens < trigger_tokens):
        raise ValueError('Recent context retention must be positive and below the compaction trigger')
    return {'capacity_tokens': capacity, 'capacity_basis': 'model_profile_or_verified_Plan_revision' if known else 'conservative_unknown_model_fallback',
            'trigger_tokens': int(trigger_tokens), 'keep_tokens': int(keep_tokens),
            'trigger_mode': 'auto_50_percent' if automatic else 'explicit',
            'summary_input_trim_tokens': None,
            'scope': 'Local operating policy with headroom; not a provider limit or an empirically optimal threshold.'}
