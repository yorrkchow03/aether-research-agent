"""macOS Seatbelt execution for remote workspaces; unavailable means denied.

This is an OS boundary, not a shell-command denylist. Descendants inherit the
same file, process and socket restrictions. The local owner's shell remains
unchanged unless restricted_execution_root is explicitly set by the host.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import DeleteResult, EditResult, WriteResult
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.tools import tool


SANDBOX_EXEC=Path('/usr/bin/sandbox-exec')
PUBLIC_PROJECT=Path(__file__).resolve().parents[2]
PROTECTED_WORKSPACE_NAMES=('reports','drafts','reviews','skills')


class RestrictedFilesystemBackend(FilesystemBackend):
    """Native file tools share the shell's canonical-output write boundary.

    Native permission rules match virtual arguments. Check their resolved paths
    too, so case-insensitive aliases and workspace symlinks cannot turn notes
    writes into changes to canonical outputs. Hardlinked files are read-only to
    file tools because their other names can be outside this writable area.
    """
    def _mutation_error(self,path,*,unlink=False):
        try:
            target=self._resolve_path(path)
            parts=target.relative_to(self.cwd).parts
            if not parts or parts[0].casefold() in PROTECTED_WORKSPACE_NAMES:
                return 'Error: permission denied for write on canonical workspace output'
            if not unlink and target.is_file() and target.stat().st_nlink>1:
                return 'Error: permission denied for write on a hardlinked file'
        except (ValueError,OSError,RuntimeError):
            return 'Error: permission denied for an unsafe workspace path'
        return None

    def write(self,file_path,content):
        error=self._mutation_error(file_path)
        return WriteResult(error=error) if error else super().write(file_path,content)

    def edit(self,file_path,old_string,new_string,replace_all=False):
        error=self._mutation_error(file_path)
        return EditResult(error=error) if error else super().edit(file_path,old_string,new_string,replace_all=replace_all)

    def delete(self,file_path):
        error=self._mutation_error(file_path,unlink=True)
        return DeleteResult(error=error) if error else super().delete(file_path)


class RestrictedFilesystemMiddleware(FilesystemMiddleware):
    """Keep native file permissions and one separately OS-confined executor.

    Deep Agents 0.7 rejects permission rules on execution backends and hides
    even an explicitly supplied execute tool on file-only backends. File tools
    therefore use the native file-only backend, while this narrow compatibility
    override preserves the host's separately injected sandbox tool. No native
    execute implementation is registered by this middleware.
    """
    @property
    def name(self):
        # Replace, rather than stack with, the factory's native middleware.
        return 'FilesystemMiddleware'

    def __init__(self,backend,permissions):
        super().__init__(backend=backend,_permissions=permissions,
            tools=['ls','read_file','write_file','edit_file','delete','glob','grep'],
            tool_token_limit_before_evict=100000)

    def _unsupported_tools_and_execution_state(self,tool_names):
        unsupported,_,_=super()._unsupported_tools_and_execution_state(tool_names-{'execute'})
        return unsupported,'execute' in tool_names,self.backend


def restricted_execute_tool(shell):
    """Expose only the already OS-restricted shell, never a host fallback."""
    if restricted_root(shell.settings) is None:
        raise ValueError('A restricted shell is required')

    @tool(response_format='content_and_artifact')
    def execute(command:str,timeout:int|None=None)->tuple[str,dict]:
        """Run a command in this workspace's OS sandbox; return output, exit_code and truncated. Use ./data and ./notes paths in Python, or RESEARCH_WORKSPACE. Internet, other users, shared databases and canonical reports/drafts/reviews/skills writes are denied. Use publish_saved_report for validated report revisions."""
        if restricted_root(shell.settings) is None:
            return (json.dumps({'output':'Restricted execution is unavailable; host fallback is disabled',
                                'exit_code':126,'truncated':False}),{'exit_code':126})
        result=shell.execute(command,timeout=timeout)
        return (json.dumps({'output':result.output,'exit_code':result.exit_code,
                            'truncated':result.truncated},ensure_ascii=False),
                {'exit_code':result.exit_code,'truncated':result.truncated})
    return execute


def _quoted(path):
    return json.dumps(str(path),ensure_ascii=False)


def _paths(operation,paths,filter_name='subpath'):
    unique=sorted(set(str(Path(p)) for p in paths))
    return '(allow '+operation+' '+ ' '.join('('+filter_name+' '+_quoted(p)+')' for p in unique)+')'


def restricted_root(settings):
    value=getattr(settings,'restricted_execution_root',None)
    return Path(value).resolve() if value else None


def sandbox_profile(workspace,settings,*,temp_root,sdk_socket=None):
    root=restricted_root(settings)
    if root is None:raise ValueError('A remote execution root is required')
    work=Path(workspace).resolve();temporary=Path(temp_root).resolve()
    if work==root or not work.is_relative_to(root) or not temporary.is_relative_to(root):
        raise ValueError('The execution workspace and temporary directory must be inside the execution root')
    if not work.is_dir() or not temporary.is_dir():raise ValueError('Execution directories are unavailable')
    public=[PUBLIC_PROJECT/'src',PUBLIC_PROJECT/'.venv',PUBLIC_PROJECT/'local/models']
    system=[Path('/bin'),Path('/sbin'),Path('/usr/bin'),Path('/usr/sbin'),Path('/usr/lib'),
            Path('/usr/share/zoneinfo'),Path('/System/Library'),Path('/Library/Frameworks/Python.framework')]
    reads=[root,*public,*system]
    # Canonical aliases matter for the framework executable and /var -> /private/var.
    reads+= [p.resolve() for p in reads]
    ancestors={Path('/')}
    for path in [*reads,temporary,work]:ancestors.update(path.parents)
    lines=['(version 1)','(deny default)',
        '(allow process-exec process-fork)',
        '(allow process-info* (target self))',
        '(allow sysctl-read (sysctl-name-prefix "hw.") (sysctl-name "kern.ostype") (sysctl-name "kern.osrelease") (sysctl-name "kern.osversion") (sysctl-name "kern.osproductversion") (sysctl-name "kern.version"))',
        _paths('file-read*',reads),
        _paths('file-map-executable',[*public,*system,*[p.resolve() for p in public+system]]),
        _paths('file-read-metadata',ancestors,'literal'),
        '(allow file-read-data (literal "/"))',
        _paths('file-read*',[Path('/dev/null'),Path('/dev/urandom'),Path('/dev/random')],'literal'),
        _paths('file-write*',[work,temporary]),
        # Host SDK services also consume workspace paths. Never let an untrusted
        # command plant directory links for those services to follow later.
        '(deny file-write-create (vnode-type SYMLINK))',
        '(allow file-write-data (literal "/dev/null"))',
        # These are harness outputs or executable skills, never arbitrary shell output.
        '(deny file-write* '+' '.join('(subpath '+_quoted(work/name)+')' for name in PROTECTED_WORKSPACE_NAMES)+')',
        # Even if a credential symlink appears below a execution root, never read it.
        '(deny file-read* (regex #"/\\.env($|\\.)") (regex #"/accounts\\.json$") (regex #"/credentials(/|$)"))',
    ]
    if sdk_socket:
        socket=Path(sdk_socket).resolve()
        if socket.name!='research.sock':raise ValueError('Unexpected job SDK socket')
        alias=Path(sdk_socket).absolute()
        lines.extend([_paths('file-read-metadata',[socket,socket.parent,alias,alias.parent,*alias.parents],'literal'),
                      '(allow network-outbound (literal '+_quoted(socket)+') (literal '+_quoted(Path(sdk_socket).absolute())+'))'])
    # No network-inbound/bind, Internet/TCP, Mach services, Apple Events or other
    # user's process inspection are permitted by this profile.
    return '\n'.join(lines)


def sandbox_argv(workspace,command,settings,*,temp_root,sdk_socket=None):
    if sys.platform!='darwin' or not SANDBOX_EXEC.is_file() or not os.access(SANDBOX_EXEC,os.X_OK):
        raise ValueError('Restricted execution is unavailable on this host; host-shell fallback is disabled')
    profile=sandbox_profile(workspace,settings,temp_root=temp_root,sdk_socket=sdk_socket)
    return [str(SANDBOX_EXEC),'-p',profile,'/bin/bash','-o','pipefail','-c',command]


def execution_temp_parent(settings):
    root=restricted_root(settings)
    if root is None:raise ValueError('Restricted execution root is missing')
    parent=root/'execution-temp'
    parent.mkdir(mode=0o700,exist_ok=True)
    if parent.is_symlink() or not parent.resolve().is_relative_to(root):
        raise ValueError('Tenant execution temporary directory is unsafe')
    return parent


@contextmanager
def execution_context(workspace,command,settings,env,*,sdk_socket=None):
    with tempfile.TemporaryDirectory(prefix='command-',dir=execution_temp_parent(settings)) as directory:
        temporary=Path(directory).resolve()
        argv=sandbox_argv(workspace,command,settings,temp_root=temporary,sdk_socket=sdk_socket)
        safe_env={**env,'TMPDIR':str(temporary),'TMP':str(temporary),'TEMP':str(temporary),
                  'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
                  'PYTHONPATH':str(PUBLIC_PROJECT/'src'),
                  'PYTHONTZPATH':str(Path('/usr/share/zoneinfo').resolve()),
                  'MPLCONFIGDIR':str(temporary/'matplotlib'),'XDG_CACHE_HOME':str(temporary/'cache')}
        yield argv,safe_env,temporary
