"""Bounded public HTTP requests with immutable response snapshots."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dumps(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, default=lambda x: str(x) if isinstance(x, Decimal) else x, **kwargs)


class SourceError(RuntimeError):
    pass


class Snapshot:
    def __init__(self, metadata, body):
        self.metadata = metadata
        self.body = body

    @property
    def source_id(self):
        return self.metadata["source_id"]

    def json(self):
        obj = json.loads(self.body, parse_float=Decimal)
        if isinstance(obj, dict) and (obj.get("error") or str(obj.get("code", "0")) not in ("0", "200")):
            raise SourceError(f"Provider returned an application error: {str(obj)[:180]}")
        return obj

    def text(self):
        return self.body.decode("utf-8-sig", errors="replace")


class Fetcher:
    def __init__(self, run_dir: Path, timeout=20):
        self.run_dir = run_dir
        self.timeout = timeout
        self.sources = []
        self.lock = threading.Lock()
        self.host_locks = {}
        self.host_last = {}
        (run_dir / "raw").mkdir(parents=True)

    def fetch(self, name, url, *, payload=None, headers=None, optional=False):
        host = urlsplit(url).netloc
        with self.lock:
            host_lock = self.host_locks.setdefault(host, threading.Lock())
        with host_lock:
            interval = 1.5 if "coingecko" in host else 0.2
            delay = max(0, interval - (time.monotonic() - self.host_last.get(host, 0)))
            time.sleep(delay)
            self.host_last[host] = time.monotonic()
        request_body = dumps(payload).encode() if payload is not None else None
        request_headers = {"User-Agent": "CryptoResearchAgent/0.1 (academic public-data research)", "Accept": "*/*"}
        if request_body is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        body, status, response_headers, error = b"", None, {}, None
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=request_body, headers=request_headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    status = response.status
                    response_headers = {k: response.headers[k] for k in ("Content-Type", "Date", "ETag", "Last-Modified") if k in response.headers}
                    body = response.read(64_000_001)
                    if len(body) > 64_000_000:
                        raise SourceError("Response exceeds the 64 MB bound")
                error = None
                break
            except urllib.error.HTTPError as exc:
                status = exc.code
                body = exc.read(1_000_000)
                response_headers = {"Content-Type": exc.headers.get("Content-Type", "")}
                error = f"HTTP {exc.code}"
                if status not in (429, 500, 502, 503, 504):
                    break
            except (OSError, SourceError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                time.sleep(1)
        retrieved_at = utcnow()
        digest = hashlib.sha256(body).hexdigest()
        source_id = f"{name}:{digest[:16]}"
        relative_path = f"raw/{name}-{digest[:16]}.bin"
        if body:
            (self.run_dir / relative_path).write_bytes(body)
        metadata = {
            "source_id": source_id, "name": name, "url": url,
            "method": "POST" if payload is not None else "GET", "request_body": payload,
            "retrieved_at": retrieved_at, "http_status": status, "response_headers": response_headers,
            "sha256": digest if body else None, "bytes": len(body),
            "path": relative_path if body else None, "status": "failed" if error else "downloaded",
            "error": error, "optional": optional,
        }
        # API secrets are sent in headers only and never recorded.
        with self.lock:
            self.sources.append(metadata)
        if error:
            raise SourceError(f"{name}: {error}")
        return Snapshot(metadata, body)
