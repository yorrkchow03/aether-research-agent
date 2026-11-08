"""Create the local environment, configure a private key and start Aether."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import venv
import webbrowser

ROOT=Path(__file__).resolve().parents[1]


def environment_python(root=ROOT):
    return root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')


def dependency_digest(root=ROOT):
    return hashlib.sha256(b'\0'.join((root/name).read_bytes() for name in
        ('requirements.lock.txt','pyproject.toml'))).hexdigest()


def install(root=ROOT):
    if not (3,12)<=sys.version_info[:2]<(3,15):
        raise ValueError('Aether requires Python 3.12, 3.13 or 3.14.')
    python=environment_python(root)
    if not python.is_file():
        print('Creating a local Python environment...',flush=True)
        venv.EnvBuilder(with_pip=True).create(root/'.venv')
    marker=root/'.venv/.aether-dependencies'
    digest=dependency_digest(root)
    if marker.exists() and marker.read_text().strip()==digest:
        return python
    print('Installing pinned dependencies...',flush=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(root/'requirements.lock.txt')],cwd=root,check=True)
    subprocess.run([str(python),'-m','pip','install','--no-deps','-e',str(root)],cwd=root,check=True)
    subprocess.run([str(python),'-m','pip','check'],cwd=root,check=True)
    marker.write_text(digest+'\n')
    return python


def configure(*,no_input=False):
    from dotenv import dotenv_values,set_key
    config=ROOT/'.env'
    if config.is_symlink():
        raise ValueError('Use a regular local .env file.')
    if not config.exists():
        config.write_bytes((ROOT/'.env.example').read_bytes())
    config.chmod(0o600)
    settings=dotenv_values(config)
    if settings.get('ARK_API_KEY'):
        return
    if no_input or not sys.stdin.isatty():
        raise ValueError('Set ARK_API_KEY in your local .env, then rerun. The key is never required in the browser.')
    print('Enter your own Volcengine Ark Agent Plan API key. It stays in this local .env.')
    key=getpass.getpass('Ark Agent Plan API key: ').strip()
    if not key or any(char in key for char in '\r\n\0'):
        raise ValueError('A nonempty single-line key is required.')
    set_key(str(config),'ARK_API_KEY',key,quote_mode='always')
    config.chmod(0o600)


def run(args):
    configure(no_input=args.no_input)
    from research_agent.server import create_app
    import uvicorn
    url=f'http://127.0.0.1:{args.port}/'
    print(f'Aether is available at {url}\nKeep this terminal open. Press Ctrl+C to stop.',flush=True)
    if not args.no_browser:
        threading.Timer(1.5,lambda:webbrowser.open(url)).start()
    uvicorn.run(create_app(port=args.port),host='127.0.0.1',port=args.port,log_level='warning')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--no-input',action='store_true')
    parser.add_argument('--install-only',action='store_true')
    parser.add_argument('--run',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('--port must be between 1024 and 65535')
    try:
        if args.run:
            run(args)
            return 0
        python=install()
        if args.install_only:
            print('Local environment ready. Run ./start.sh to configure your key and open Aether.')
            return 0
        command=[str(python),str(Path(__file__).resolve()),'--run','--port',str(args.port)]
        if args.no_browser:command.append('--no-browser')
        if args.no_input:command.append('--no-input')
        return subprocess.call(command,cwd=ROOT)
    except (ValueError,subprocess.CalledProcessError,OSError) as error:
        print('Setup could not complete: '+str(error),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
