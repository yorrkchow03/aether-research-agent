"""Personal host shell or fail-closed confined OS sandbox, selected by the host."""
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
        attached research SDK. Confined workspaces keep their OS boundary; a
        local-owner replay alone is not proof of method correctness.
        """
        parent=None
        if getattr(self.settings,'restricted_execution_root',None):
            from .execution_safety import execution_temp_parent
            parent=execution_temp_parent(self.settings)
        with tempfile.TemporaryDirectory(prefix='aether-declared-inputs-',dir=parent) as directory:
            root=Path(directory);recipe=root/'recipe.py';recipe.write_bytes(body)
            inputs=[]
            for row in rows:
                path=root/(row['record_id']+'.json')
                path.write_text(json.dumps(row,ensure_ascii=False));inputs.append(str(path))
            replay=ResearchShell(root,self.settings,self.cancel)
            return replay.execute(shlex.join(['python','-I',str(recipe),*inputs]),timeout=60)

    def execute(self, command, *, timeout=None):
        """Run with Bash pipefail and the host-selected execution boundary.

        File-tool /data/... paths are virtual. Python/terminal code uses ./data/...
        or RESEARCH_WORKSPACE. Pipeline failures propagate; explicit shell error
        handling remains under the caller's control. No command is rewritten.
        """
        limit = min(timeout or 60, 120)
        if not isinstance(command, str) or not command.strip() or len(command) > 20000 or limit <= 0:
            return ExecuteResponse(output="Invalid command or timeout", exit_code=1, truncated=False)
        if getattr(self.settings,'restricted_execution_root',None):
            try:
                from .execution_safety import execution_context
                with execution_context(self.cwd,command,self.settings,self._env,
                        sdk_socket=self._env.get('AETHER_RESEARCH_SOCKET')) as (argv,env,temporary):
                    return self._run_command(command,argv,env,temporary,limit)
            except (OSError,ValueError) as exc:
                return ExecuteResponse(output='Restricted execution failed; host fallback is disabled: '+self.settings.redact(str(exc)),exit_code=126,truncated=False)
        return self._run_command(command,['/bin/bash','-o','pipefail','-c',command],self._env,None,limit)

    def _run_command(self,command,argv,env,capture_dir,limit):
        with tempfile.TemporaryFile(dir=capture_dir) as capture:
            process = subprocess.Popen(argv, cwd=self.cwd, env=env,
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
        if not output and process.returncode:
            output=f'Command exited with status {process.returncode} without captured output. If diagnostics were redirected, rerun without that redirection to inspect the error. Pipeline failures propagate.'
        if process.returncode and 'FileNotFoundError' in output and any(p in output for p in ('/data/','/notes/','/reports/','/memory/','/skills/')):
            output += ('\nPath recovery hint: /data, /notes, /reports, /memory and /skills are virtual paths for file tools. '
                'In this terminal use ./data/... (or the matching relative directory), or build a path from '
                'os.environ["RESEARCH_WORKSPACE"]. Do not repeat the same absolute virtual path. '
                'The command was not rewritten; inspect the existing source file before retrying.')
        if process.returncode and 'TypeError' in output and 'str' in output and 'unsupported operand type' in output:
            output+=('\nNumeric parsing hint: financial source values can be exact decimal strings. '
                'Inspect data_contract and convert selected values with Decimal before arithmetic; '
                'None/missing stays unknown. Reuse an applicable saved calculation instead of redoing all metrics.')
        return ExecuteResponse(output=output or "<no output>", exit_code=124 if reason else process.returncode,
                               truncated=len(raw) > self._max_output_bytes or reason == "output limit exceeded")
