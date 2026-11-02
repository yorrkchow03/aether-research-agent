"""Optional pretrained static embeddings loaded from a verified local directory.

No model download, provider client, API key, training or executable remote code.
Install/download preparation is a separate explicit command. Similarity is a
retrieval signal, never evidence of truth, event identity or source authority.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from threading import RLock

_CACHE={}
_LOCK=RLock()
FILES={'config.json','model.safetensors','tokenizer.json','README.md'}

class LocalMemoryEncoder:
    def __init__(self,path):
        self.path=Path(path).resolve()
        manifest=self.path/'AETHER_MODEL_MANIFEST.json'
        if not manifest.is_file():raise ValueError('Local memory encoder is not prepared')
        self.manifest=json.loads(manifest.read_text())
        if self.manifest.get('schema')!='aether-local-encoder-v1' or set(self.manifest.get('files',{}))!=FILES:
            raise ValueError('Invalid local memory encoder manifest')
        self.model_id=self.manifest['repository']+'@'+self.manifest['revision']
        self.fingerprint=hashlib.sha256(json.dumps(self.manifest['files'],sort_keys=True).encode()).hexdigest()
        self.preprocessing='model2vec-static-normalized-max4096-v1'

    def encode(self,texts):
        if not isinstance(texts,list) or not texts or len(texts)>2000 or any(not isinstance(t,str) or len(t)>100000 for t in texts):
            raise ValueError('Supply a bounded list of public source texts')
        key=(str(self.path),self.fingerprint)
        with _LOCK:
            if key not in _CACHE:
                for name,expected in self.manifest['files'].items():
                    p=self.path/name
                    if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
                        raise ValueError('Local encoder payload hash mismatch')
                from model2vec import StaticModel
                _CACHE[key]=StaticModel.from_pretrained(self.path,normalize=True,force_download=False,max_length=4096)
            # Static tokenizer/model is shared per process; serialize its use.
            return _CACHE[key].encode(texts)
