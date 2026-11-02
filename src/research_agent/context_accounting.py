"""Offline tokenizer proxy calibrated against actual provider input usage.

Doubao's tokenizer is not exposed here: o200k is explicitly a proxy, never
reported as exact Doubao tokens. Missing provider usage is unknown, not zero.
"""
import hashlib,json,math,os,tempfile
from pathlib import Path
from threading import RLock

_URL='https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
_SHA='446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'

def _cached_encoding():
    # Never let tiktoken implicitly download a vocabulary in a research task.
    try:
        import tiktoken
        if 'o200k_base' in tiktoken.registry.ENCODINGS:return tiktoken.registry.ENCODINGS['o200k_base']
        directory=os.environ.get('TIKTOKEN_CACHE_DIR') or os.environ.get('DATA_GYM_CACHE_DIR') or str(Path(tempfile.gettempdir())/'data-gym-cache')
        cache=Path(directory)/hashlib.sha1(_URL.encode()).hexdigest()
        if cache.is_file() and hashlib.sha256(cache.read_bytes()).hexdigest()==_SHA:return tiktoken.get_encoding('o200k_base')
    except (ImportError,OSError,ValueError):pass
    return None

class ContextAccounting:
    def __init__(self,emit=lambda *_:None):
        self.emit=emit;self.encoding=_cached_encoding();self.factor=1.0;self.observations=0;self.lock=RLock()
        self.unit='o200k_tokenizer_proxy' if self.encoding else 'UTF8_bytes_div3_proxy'

    @staticmethod
    def _serialize(messages,tools=None):
        from langchain_core.messages import message_to_dict
        rows=[message_to_dict(m) if hasattr(m,'type') else m for m in messages]
        schemas=[]
        for tool in tools or []:
            if isinstance(tool,dict):schemas.append(tool)
            else:
                schema=getattr(tool,'args_schema',None)
                schemas.append({'name':getattr(tool,'name',type(tool).__name__),'description':getattr(tool,'description',''),
                    'parameters':schema.model_json_schema() if schema and hasattr(schema,'model_json_schema') else {}})
        return json.dumps({'messages':rows,'tools':schemas},ensure_ascii=False,sort_keys=True,default=str)

    def estimate(self,messages,tools=None):
        text=self._serialize(messages,tools)
        raw=len(self.encoding.encode(text,disallowed_special=())) if self.encoding else math.ceil(len(text.encode())/3)
        with self.lock:factor=self.factor
        return {'proxy_tokens':max(1,raw),'estimated_input_tokens':max(1,math.ceil(raw*factor)),
            'estimator':self.unit,'calibration_factor':factor,'observations':self.observations,
            'scope':'Offline approximate full request, calibrated on prior provider usage; not the exact Doubao tokenizer or monetary cost.'}

    def counter(self,messages,*,tools=None):return self.estimate(messages,tools)['estimated_input_tokens']

    def observe(self,estimate,usage):
        actual=(usage or {}).get('input_tokens')
        if not isinstance(actual,int) or isinstance(actual,bool) or actual<=0:
            return {'status':'unknown','provider_input_tokens':None}
        ratio=max(.5,min(3.0,actual/max(1,estimate['proxy_tokens'])))
        with self.lock:
            self.factor=ratio if not self.observations else .7*self.factor+.3*ratio
            self.observations+=1
        result={'status':'observed','provider_input_tokens':actual,'proxy_tokens':estimate['proxy_tokens'],
            'calibration_factor':self.factor,'observations':self.observations,'estimator':self.unit}
        self.emit('context_calibrated',result);return result
