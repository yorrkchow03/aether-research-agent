"""Offline pre-push checks on source candidates or actual Git index bytes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values


ROOT=Path(__file__).resolve().parents[2]
EXPORT=runpy.run_path(str(Path(__file__).with_name('export_source.py')))
FORBIDDEN_ROOTS={'.git','.venv','.audit-venv','.audit-tools','data','runtime',
    'reports','logs','local','vendor','dist','build','tmp','output','outputs','workspaces'}


def candidate_bytes(root):
    root=Path(root).resolve()
    if (root/'.git').exists():
        result=subprocess.run(['git','ls-files','--cached','-z'],cwd=root,capture_output=True,check=True)
        names=[value.decode() for value in result.stdout.split(b'\0') if value]
        if not names:raise ValueError('Git index is empty; stage the reviewed source files before checking')
        result={}
        for name in names:
            mode=subprocess.run(['git','ls-files','-s','--',name],cwd=root,capture_output=True,check=True).stdout
            if mode.startswith((b'120000',b'160000')):raise ValueError('Symlink or submodule in candidate index: '+name)
            result[name]=subprocess.run(['git','show',':'+name],cwd=root,capture_output=True,check=True).stdout
        return result,'git_index'
    return {str(p.relative_to(root)):p.read_bytes() for p in EXPORT['source_files'](root)},'source_allowlist'


def check_repository(root, *, secret_values=()):
    root=Path(root).resolve();files,mode=candidate_bytes(root);issues=[]
    allowlisted={str(p.relative_to(root)) for p in EXPORT['source_files'](root)}
    secrets=[value.encode() if isinstance(value,str) else value for value in secret_values if value and len(value)>=12]
    for key,value in dotenv_values(root/'.env').items():
        if value and len(value)>=12 and any(tag in key.upper() for tag in ('KEY','TOKEN','SECRET','PASSWORD')):
            secrets.append(value.encode())
    for name,body in files.items():
        path=Path(name)
        if (name not in allowlisted or path.parts[0] in FORBIDDEN_ROOTS
                or '__pycache__' in path.parts or path.name=='.DS_Store'
                or (path.name.startswith('.env') and name!='.env.example')):
            issues.append({'path':name,'check':'outside_source_boundary'})
        if any(secret in body for secret in secrets) or any(p.search(body) for p in EXPORT['SECRET_PATTERNS']):
            issues.append({'path':name,'check':'credential_or_personal_path_pattern'})
        if name=='.env.example':
            for key,value in dotenv_values(stream=__import__('io').StringIO(body.decode())).items():
                if value and re.search(r'(?:^|_)(?:API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY)$',key.upper()):
                    issues.append({'path':name,'check':'nonempty_template_credential'})
        if path.suffix.lower()!='.md':continue
        text=re.sub(r'```.*?```','',body.decode('utf-8'),flags=re.S)
        for match in re.finditer(r'!?\[[^\]\n]*\]\(([^)\n]+)\)',text):
            raw=match.group(1).strip()
            target=raw[1:raw.index('>')] if raw.startswith('<') and '>' in raw else raw.split(' ',1)[0]
            if urlsplit(target).scheme or target.startswith(('#','/')):continue
            relative=unquote(urlsplit(target).path)
            if not relative:continue
            resolved=(root/path.parent/relative).resolve()
            if not resolved.is_relative_to(root):
                issues.append({'path':name,'check':'link_outside_repository','target':target});continue
            link=str(resolved.relative_to(root))
            # Check committed bytes, not an ignored file that happens to exist locally.
            if link not in files and not any(item.startswith(link.rstrip('/')+'/') for item in files):
                issues.append({'path':name,'check':'missing_relative_link','target':target})
    for name in ('README.md','pyproject.toml','.gitignore','.env.example',
                 'docs/README.md','.github/workflows/ci.yml'):
        if name not in files:issues.append({'path':name,'check':'required_file_missing'})
    return {'status':'passed' if not issues else 'failed','mode':mode,'files_checked':len(files),
        'markdown_files_checked':sum(Path(name).suffix=='.md' for name in files),
        'issues':issues,'provider_calls':0,
        'scope':'Candidate source boundary, known credential patterns and local Markdown targets; not a guarantee that every possible secret or runtime error is detectable.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    result=check_repository(args.root)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
