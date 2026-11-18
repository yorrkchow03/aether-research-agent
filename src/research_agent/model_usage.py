"""Read provider-reported token counts across compatible response shapes."""


def response_usage(message):
    usage=getattr(message,'usage_metadata',None)
    if usage:return usage
    raw=(getattr(message,'response_metadata',None) or {}).get('token_usage')
    return completion_usage(raw)


def completion_usage(raw):
    if hasattr(raw,'model_dump'):raw=raw.model_dump()
    if not isinstance(raw,dict):return None
    def valid(value):return isinstance(value,int) and not isinstance(value,bool) and value>=0
    inputs,outputs=raw.get('prompt_tokens'),raw.get('completion_tokens')
    if not valid(inputs) or not valid(outputs):return None
    result={'input_tokens':inputs,'output_tokens':outputs,
        'total_tokens':raw['total_tokens'] if valid(raw.get('total_tokens')) else inputs+outputs}
    details=raw.get('prompt_tokens_details')
    cached=details.get('cached_tokens') if isinstance(details,dict) else None
    if valid(cached):result['input_token_details']={'cache_read':cached}
    return result
