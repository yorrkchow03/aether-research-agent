"""Credential-free resumable collection. Shared per-host pacing and bounded retry."""
from __future__ import annotations
import ipaddress,json,threading,time
from contextlib import nullcontext
from urllib.parse import urlsplit,urljoin
import httpx

class PublicCapture:
 def __init__(self,library,*,refresh=False,transport=None,reuse_connections=False):
  self.library=library;self.refresh=refresh;self.transport=transport;self.lock=threading.Lock();self.hosts={};self.last={};self.reuse_connections=reuse_connections;self.clients={}

 def close(self):
  # Call only after the source workers have returned. The collector's finally
  # block handles both successful completion and per-source failures.
  with self.lock:clients=list(self.clients.values());self.clients.clear()
  for client in clients:client.close()

 def _client(self,host,accept_json):
  options={'timeout':45,'follow_redirects':False,'transport':self.transport,
      'headers':{'User-Agent':'AetherAcademicResearch/0.3 (public research archive)','Accept':'application/json' if accept_json else '*/*',**({'Content-Type':'application/json'} if accept_json else {})}}
  if not self.reuse_connections:return httpx.Client(**options)
  key=(host,bool(accept_json))
  with self.lock:
   if key not in self.clients:self.clients[key]=httpx.Client(**options)
   client=self.clients[key]
  return nullcontext(client)

 def fetch(self,source_id,url,*,force=False,accept_json=False):
  parsed=urlsplit(url)
  if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Use a credential-free public HTTPS URL')
  if parsed.hostname in ('localhost','metadata.google.internal') or parsed.hostname.endswith('.local'):raise ValueError('Private host is forbidden')
  try:
   if not ipaddress.ip_address(parsed.hostname).is_global:raise ValueError('Private IP is forbidden')
  except ValueError as exc:
   if 'Private IP' in str(exc):raise
  if not self.refresh and not force:
   cached=self.library.cached(url)
   if cached:return cached,self.library.raw(cached)
  host=parsed.hostname
  with self.lock:lock=self.hosts.setdefault(host,threading.Lock())
  with lock:
   time.sleep(max(0,1.1-(time.monotonic()-self.last.get(host,0))))
   self.last[host]=time.monotonic()
   current=url
   with self._client(host,accept_json) as client:
    for attempt in range(3):
     try:
      for redirect in range(5):
       with client.stream('GET',current) as response:
        status=response.status_code;headers={k:v for k,v in response.headers.items() if k in ('content-type','date','etag','last-modified','retry-after')}
        body=bytearray()
        for chunk in response.iter_bytes():
         body.extend(chunk)
         if len(body)>32_000_000:raise ValueError('Source exceeds 32 MB bound')
       cid=self.library.capture(source_id,current,bytes(body),status=status,headers=headers,
                                error=None if status==200 else 'HTTP '+str(status))
       if status in (301,302,303,307,308):
        target=urljoin(current,response.headers.get('location',''))
        target_host=urlsplit(target).hostname
        if urlsplit(target).scheme!='https' or target_host not in (host,'www.'+host,host.removeprefix('www.')):raise ValueError('Cross-host redirect requires an explicit source entry')
        current=target;continue
       break
      if status==200:return cid,bytes(body)
      if status not in (429,500,502,503,504):raise RuntimeError(f'{source_id}: HTTP {status}')
      if attempt==2:raise RuntimeError(f'{source_id}: HTTP {status} after bounded retries')
      retry=headers.get('retry-after','')
      time.sleep(min(20,float(retry) if retry.isdigit() else 2**(attempt+1)))
     except httpx.HTTPError as exc:
      self.library.capture(source_id,current,status=None,error=type(exc).__name__+': '+str(exc))
      if attempt==2:raise RuntimeError(source_id+': '+type(exc).__name__) from None
      time.sleep(2**(attempt+1))
   raise RuntimeError('No successful response')

 def json(self,source_id,url,**kwargs):
  from decimal import Decimal
  cid,body=self.fetch(source_id,url,accept_json=True,**kwargs);value=json.loads(body,parse_float=Decimal)
  if isinstance(value,dict) and (value.get('error') or str(value.get('code','0')) not in ('0','200')):
   raise ValueError(source_id+': application error '+str(value.get('error') or value.get('msg'))[:300])
  return cid,value
