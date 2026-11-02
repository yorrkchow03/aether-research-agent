"""Connection lifetime and per-source isolation, entirely offline."""
import contextlib
import io
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import httpx
from research_data.library.store import ResearchLibrary
from research_data.library.http import PublicCapture
from research_data.library.text import TextCollector


class TrackingTransport(httpx.BaseTransport):
 def __init__(self):self.closed=0;self.requests=[];self.active=0;self.maximum=0;self.lock=threading.Lock()
 def handle_request(self,request):
  with self.lock:self.active+=1;self.maximum=max(self.maximum,self.active);self.requests.append(str(request.url))
  try:
   time.sleep(0.005)
   return httpx.Response(403 if request.url.path=='/bad' else 200,content=b'publisher fixture',headers={'content-type':'text/plain'})
  finally:
   with self.lock:self.active-=1
 def close(self):self.closed+=1


class HalfYearHTTPTests(unittest.TestCase):
 def test_optin_client_is_reused_and_closed_once(self):
  with tempfile.TemporaryDirectory() as root:
   lib=ResearchLibrary(root);transport=TrackingTransport();capture=PublicCapture(lib,transport=transport,reuse_connections=True)
   with patch('research_data.library.http.time.sleep'):
    capture.fetch('fixture','https://example.com/a');client=next(iter(capture.clients.values()))
    capture.fetch('fixture','https://example.com/b');self.assertIs(next(iter(capture.clients.values())),client)
    capture.fetch('fixture','https://example.com/a')
   self.assertEqual(len(transport.requests),2);self.assertEqual(transport.closed,0)
   capture.close();self.assertEqual(transport.closed,1);self.assertTrue(client.is_closed);self.assertEqual(capture.clients,{})
   capture.close();self.assertEqual(transport.closed,1)

 def test_default_fetch_lifetime_is_unchanged(self):
  with tempfile.TemporaryDirectory() as root:
   lib=ResearchLibrary(root);transport=TrackingTransport();capture=PublicCapture(lib,transport=transport)
   capture.fetch('fixture','https://example.com/a')
   self.assertEqual(transport.closed,1);self.assertEqual(capture.clients,{})

 def test_failed_source_does_not_stop_next_source_or_leak_client(self):
  with tempfile.TemporaryDirectory() as root:
   lib=ResearchLibrary(root);transport=TrackingTransport();worker=TextCollector(lib);worker.http=PublicCapture(lib,transport=transport)
   def source(name):
    worker.http.fetch(name,'https://example.com/'+('bad' if name=='ethereum' else 'good'))
    lib.job('text:'+name,'collected',discovery_complete=True)
   worker.source=source
   with patch('research_data.library.http.time.sleep'),contextlib.redirect_stdout(io.StringIO()):worker.run(['ethereum','bitcoin_core'])
   with lib.connect(readonly=True) as c:states=dict(c.execute("SELECT job_id,status FROM jobs"))
   self.assertEqual(states['text:ethereum'],'failed');self.assertEqual(states['text:bitcoin_core'],'collected')
   self.assertEqual(transport.closed,1);self.assertEqual(transport.maximum,1);self.assertFalse(worker.http.reuse_connections);self.assertEqual(worker.http.clients,{})


if __name__=='__main__':unittest.main()
