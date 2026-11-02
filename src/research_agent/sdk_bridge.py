"""Job-scoped local socket joining terminal Python to canonical source storage."""
from __future__ import annotations

import json
import os
import socketserver
import tempfile
import threading
import uuid
from pathlib import Path


class ResearchSDKServer:
    """Own one local connection point for a job; never expose LLM credentials.

    Unix file permissions restrict access to the same OS user. This is a personal
    host service, not an OS sandbox. Publication and ledger writes are not exposed.
    """

    def __init__(self, service, settings, cancel=None):
        self.service = service
        self.settings = settings
        self.cancel = cancel
        self.server = None
        self.thread = None
        # macOS Unix socket names have a small length limit; keep this path short.
        self.directory = tempfile.TemporaryDirectory(prefix='aether-sdk-', dir='/tmp')
        self.path = str(Path(self.directory.name) / 'research.sock')

    def start(self):
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(115)
                event = {'name': 'research_sdk', 'call_id': 'sdk-' + uuid.uuid4().hex}
                try:
                    body = self.rfile.readline(65537)
                    if len(body) > 65536 or not body.endswith(b'\n'):
                        raise ValueError('Expected one JSON request under 64 KiB')
                    request = json.loads(body)
                    operation = request['operation']
                    arguments = request.get('arguments', {})
                    if not isinstance(operation, str) or not isinstance(arguments, dict):
                        raise ValueError('Invalid research SDK request')
                    if owner.cancel and owner.cancel.is_set():
                        raise ValueError('Research task was cancelled')
                    event['name'] = 'sdk:' + operation
                    if owner.service.event_sink:
                        owner.service.event_sink('tool_started', {**event,
                            'arguments_preview': owner.settings.redact(json.dumps(arguments))[:2500]})
                    result = owner.service.sdk_call(operation, arguments)
                    working=getattr(owner.service,'working_memory',None);job_id=getattr(owner.service,'job_id',None)
                    if working and job_id:
                        try:working.observe_tool(job_id,event['name'],result,tool_call_id=event['call_id'])
                        except Exception as exc:
                            if owner.service.event_sink:owner.service.event_sink('working_memory_error',{'stage':'sdk_result','error_type':type(exc).__name__})
                    packet = {'ok': True, 'result': result}
                    if owner.service.event_sink:
                        owner.service.event_sink('tool_finished', {**event, 'status': 'success'})
                except Exception as exc:
                    error = owner.settings.redact(str(exc))[:2000]
                    working=getattr(owner.service,'working_memory',None);job_id=getattr(owner.service,'job_id',None)
                    if working and job_id:
                        try:working.observe_tool(job_id,event['name'],{'status':'error','error':owner.settings.redact(str(exc))},status='error',tool_call_id=event['call_id'])
                        except Exception:pass
                    packet = {'ok': False, 'error': error}
                    if owner.service.event_sink:
                        owner.service.event_sink('tool_failed', {**event, 'error': error})
                try:
                    self.wfile.write(json.dumps(packet, ensure_ascii=False).encode() + b'\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The research shell may have hit its own deadline.

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
            block_on_close = False

        try:
            self.server = Server(self.path, Handler)
            os.chmod(self.path, 0o600)
            self.thread = threading.Thread(target=self.server.serve_forever,
                kwargs={'poll_interval': .1}, daemon=True)
            self.thread.start()
        except Exception:
            self.close()
            raise
        return self.path

    def close(self):
        if self.server:
            if self.thread and self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.directory.cleanup()
