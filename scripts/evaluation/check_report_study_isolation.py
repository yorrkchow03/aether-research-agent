"""Local macOS isolation probe; synthetic data only, no provider request."""
import argparse,json,shlex,socket,tempfile,threading
from pathlib import Path
from research_agent.report_study_runtime import FrozenResearchShell
from research_agent.settings import Settings

ROOT=Path(__file__).resolve().parents[2]

def check(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='guard-',dir=output) as directory:
        base=Path(directory);work=base/'workspace';work.mkdir()
        private=base/'private.txt';private.write_text('synthetic-not-a-real-secret')
        (work/'allowed.txt').write_text('own-workspace')
        with tempfile.TemporaryDirectory(prefix='aether-study-socket-') as td:
            sockets=[];paths=[]
            for n in range(2):
                path=str(Path(td)/('sdk'+str(n)+'.sock'));server=socket.socket(socket.AF_UNIX)
                server.bind(path);server.listen();sockets.append(server);paths.append(path)
            def serve():
                connection,_=sockets[0].accept();connection.sendall(b'own-sdk-ok');connection.close()
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            code='''import socket,json,pathlib
out={}
s=socket.socket(socket.AF_UNIX);s.settimeout(2);s.connect(PATHS[0]);out['own_sdk']=s.recv(99).decode();s.close()
for name,f in [('foreign_sdk',lambda:socket.socket(socket.AF_UNIX).connect(PATHS[1])),('ip_network',lambda:socket.create_connection(('127.0.0.1',9),timeout=1)),('private_store',lambda:pathlib.Path(PRIVATE).read_text()),('private_write',lambda:pathlib.Path(PRIVATE).write_text('changed'))]:
 try:f();out[name]='UNEXPECTED_ACCESS'
 except Exception as e:out[name]=type(e).__name__
out['workspace_read']=pathlib.Path('allowed.txt').read_text()
pathlib.Path('created.txt').write_text('allowed')
print(json.dumps(out))
'''.replace('PATHS',repr(paths)).replace('PRIVATE',repr(str(private)))
            shell=FrozenResearchShell(work,Settings(),project_root=ROOT);shell.attach_research_sdk(paths[0])
            # This inspectable file is not the executable source of policy.
            shell.profile.write_text('(version 1)\n(allow default)\n')
            result=shell.execute(shlex.join(['python','-c',code]))
            for server in sockets:server.close()
            observed=json.loads(result.output) if result.exit_code==0 else {'error':result.output}
            expected={'own_sdk':'own-sdk-ok','foreign_sdk':'PermissionError','ip_network':'PermissionError',
                'private_store':'PermissionError','private_write':'PermissionError','workspace_read':'own-workspace'}
            receipt={'passed':result.exit_code==0 and observed==expected,'observed':observed,
                'profile_tampered_before_command':True,'provider_calls':0,
                'scope':'macOS child-command guards and exact SDK socket; not a general proof against arbitrary hostile code.'}
    (output/'RESULT.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'local/report-flow-study/v1/guard-preflight-final')
    args=parser.parse_args();result=check(args.output);print(json.dumps(result,indent=2))
    if not result['passed']:raise SystemExit(1)
