"""Build an allowlisted, credential-checked source directory. Never commits or pushes."""
from __future__ import annotations
import argparse,hashlib,json,re
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]
TOP=('README.md','docs/README.md','CHANGELOG.md','CONTRIBUTING.md','SECURITY.md','THIRD_PARTY_NOTICES.md','pyproject.toml','requirements.in','requirements.lock.txt','requirements.audit.in','requirements.audit.lock.txt','requirements.memory.in','requirements.memory.lock.txt','.env.example','.gitignore','.gitattributes','.editorconfig','Start_Aether.command')
DIRECTORIES=('src','tests','scripts','contracts','examples','.github','docs/architecture','docs/development','docs/guides')
SUFFIXES={'.py','.html','.css','.js','.md','.json','.txt','.csv','.sol','.toml','.in','.yml','.yaml','.sh','.command'}
PRIVATE_DOCS={'docs/development/PRODUCT_EVOLUTION_PLAN.md'}
PUBLIC_ASSETS={
    'docs/architecture/figures/aether-agent-framework-zh.svg',
    'docs/architecture/figures/aether-agent-framework-zh.png',
}
SECRET_PATTERNS=[
    re.compile(rb'ark-[a-f0-9-]{25,}',re.I),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|apikey_[0-9a-f]{24,}_[0-9a-f]{24,})\b'),
    re.compile(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
]

def source_files(root):
    for name in TOP:
        if (root/name).is_symlink():raise ValueError('Symlink in publishable source: '+name)
    files=[root/name for name in TOP if (root/name).is_file()]
    for directory in DIRECTORIES:
        for p in (root/directory).rglob('*'):
            if str(p.relative_to(root)) in PRIVATE_DOCS:continue
            if any(part=='__pycache__' or part.endswith('.egg-info') for part in p.relative_to(root).parts):continue
            if p.is_symlink():raise ValueError('Symlink in publishable source: '+str(p.relative_to(root)))
            if p.is_file() and (p.suffix in SUFFIXES or str(p.relative_to(root)) in PUBLIC_ASSETS):files.append(p)
    return sorted(set(files))

def export_source(root,output):
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Output exists; choose a new --output directory for review')
    cfg=dotenv_values(root/'.env');secrets=[v.encode() for k,v in cfg.items() if v and any(tag in k.upper() for tag in ('API_KEY','SECRET','TOKEN')) and len(v)>=12]
    patterns=SECRET_PATTERNS
    files=source_files(root);contents={}
    for p in files:
        data=p.read_bytes();relative=str(p.relative_to(root))
        if any(secret in data for secret in secrets) or any(pattern.search(data) for pattern in patterns):raise ValueError('Credential or personal path detected in '+relative+'; contents withheld')
        contents[relative]=data
    # Validate before creating output, so failure leaves no partial publishable tree.
    output.mkdir(parents=True)
    for relative,data in contents.items():
        target=output/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        if relative=='Start_Aether.command':target.chmod(0o755)
    manifest={'schema':'aether-source-v1','files':{name:hashlib.sha256(data).hexdigest() for name,data in contents.items()},'scope':'Public source only; excludes credentials, private state, downloads, upstream checkout and generated research artifacts','checks':['explicit_allowlist','configured_credentials','provider_key_and_private_key_patterns','personal_absolute_paths','no_symlinks'],'no_git_operations':True}
    (output/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return {'output':str(output),'files':len(contents),'checked':True,'git_operations':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'dist/aether-source');args=parser.parse_args()
    print(json.dumps(export_source(ROOT,args.output),indent=2))
