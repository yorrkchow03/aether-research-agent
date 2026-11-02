"""Authorized personal local shell. This is host execution, not an OS sandbox."""
import os
import json
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse


class ResearchShell(LocalShellBackend):
    def __init__(self, workspace, settings, cancel=None):
        # Do not inherit credentials, proxy credentials or tracing environment.
        env = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
               "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "RESEARCH_WORKSPACE": str(workspace)}
        super().__init__(root_dir=workspace, virtual_mode=True, env=env, inherit_env=False, timeout=60, max_output_bytes=18000)
        self._workspace_alias=Path(workspace).absolute()
        self.settings = settings; self.cancel = cancel

    def attach_research_sdk(self, socket_path):
        """Attach only this job's local capability, never a provider credential."""
        self._env['AETHER_RESEARCH_SOCKET'] = str(socket_path)

    def _resolve_path(self, key):
        # The real terminal prints host-absolute workspace paths. Accept those
        # as aliases for the same virtual file, never as a second nested tree.
        path=Path(key)
        resolved=path.resolve() if path.is_absolute() else None
        if path.is_absolute() and (path.is_relative_to(self.cwd) or
                path.is_relative_to(self._workspace_alias) or resolved.is_relative_to(self.cwd)):
            if '..' in path.parts:raise ValueError('Path traversal not allowed')
            if not resolved.is_relative_to(self.cwd):raise ValueError('Path escapes the task workspace')
            return resolved
        return super()._resolve_path(key)

    def replay_recipe(self, body, rows):
        """Check a self-contained recipe using only its supplied input snapshots.

        A fresh cwd catches accidental dependencies on exploratory notes or an
        attached research SDK. This remains local host execution, not an OS
        sandbox or proof that arbitrary code has no external dependencies.
        """
        with tempfile.TemporaryDirectory(prefix='aether-declared-inputs-') as directory:
            root=Path(directory);recipe=root/'recipe.py';recipe.write_bytes(body)
            inputs=[]
            for row in rows:
                path=root/(row['record_id']+'.json')
                path.write_text(json.dumps(row,ensure_ascii=False));inputs.append(str(path))
            replay=ResearchShell(root,self.settings,self.cancel)
            return replay.execute(shlex.join(['python','-I',str(recipe),*inputs]),timeout=60)

    def execute(self, command, *, timeout=None):
        limit = min(timeout or 60, 120)
        if not isinstance(command, str) or not command.strip() or len(command) > 20000 or limit <= 0:
            return ExecuteResponse(output="Invalid command or timeout", exit_code=1, truncated=False)
        with tempfile.TemporaryFile() as capture:
            process = subprocess.Popen(command, shell=True, cwd=self.cwd, env=self._env,
                                       stdin=subprocess.DEVNULL, stdout=capture, stderr=subprocess.STDOUT, start_new_session=True)
            start = time.monotonic(); reason = None
            while process.poll() is None:
                capture.seek(0, os.SEEK_END)
                if self.cancel and self.cancel.is_set():reason = "cancelled"
                elif time.monotonic() - start > limit:reason = "timeout"
                elif capture.tell() > 1_000_000:reason = "output limit exceeded"
                if reason:
                    try:os.killpg(process.pid, signal.SIGKILL)
                    except (ProcessLookupError,PermissionError):
                        try:process.kill()
                        except ProcessLookupError:pass
                    process.wait(timeout=5); break
                time.sleep(.05)
            # Clean up background descendants of a command, too.
            try:os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError,PermissionError):pass
            capture.seek(0); raw = capture.read(self._max_output_bytes + 1)
        output = self.settings.redact(raw[:self._max_output_bytes].decode("utf-8", errors="replace"))
        if reason:output += "\nExecution stopped: " + reason
        if process.returncode and 'FileNotFoundError' in output and any(p in output for p in ('/data/','/notes/','/reports/','/memory/','/skills/')):
            output += ('\nPath recovery hint: /data, /notes, /reports, /memory and /skills are virtual paths for file tools. '
                'In this terminal use ./data/... (or the matching relative directory), or build a path from '
                'os.environ["RESEARCH_WORKSPACE"]. Do not repeat the same absolute virtual path. '
                'The command was not rewritten; inspect the existing source file before retrying.')
        return ExecuteResponse(output=output or "<no output>", exit_code=124 if reason else process.returncode,
                               truncated=len(raw) > self._max_output_bytes or reason == "output limit exceeded")
