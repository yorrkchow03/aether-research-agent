"""Portable evidence bundles and integrity verification, distinct from evidence truth."""
from __future__ import annotations
import hashlib
import json
import platform
import shutil
import tempfile
import zipfile
from pathlib import Path
from research_data.http import dumps,utcnow
from .advice import advice_evidence_ids
from .research_explanations import explanation_evidence_ids


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def sha(data):return hashlib.sha256(data).hexdigest()


def report_evidence_ids(report):
    ids={i for c in [*report.get('observations',[]),*report.get('key_changes',[]),*report.get('interpretations',[])] for i in c['evidence_ids']}
    ids.update(i for f in report.get('forecasts',[]) for i in [*f.get('supporting_evidence_ids',[]),*f.get('counter_evidence_ids',[])])
    ids.update(advice_evidence_ids(report.get('advice')))
    ids.update(explanation_evidence_ids(report.get('explanations')))
    d=report.get('purchase_decision')
    if d:
        ids.update(i for c in [*d.get('conditions_to_consider',[]),*d.get('reasons_to_wait',[])] for i in c['evidence_ids'])
        ids.update(d.get('simulation_evidence_ids',[]))
    return ids


class BundleBuilder:
    def __init__(self,project_root,output_root,forbidden_strings=(),software_root=None,knowledge_root=None):
        self.root=Path(project_root).resolve();self.output_root=Path(output_root);self.output_root.mkdir(parents=True,exist_ok=True)
        self.software_root=Path(software_root).resolve() if software_root else self.root
        self.knowledge_root=Path(knowledge_root or self.root/'runtime/knowledge').resolve()
        self.forbidden=[s.encode() for s in forbidden_strings if s]
        wallet=self.root/'.audit-tools/sepolia_wallet.json'
        if wallet.exists():
            private_key=json.loads(wallet.read_text()).get('private_key','')
            self.forbidden.extend(s.encode() for s in (private_key,private_key.removeprefix('0x')) if s)

    def build(self,workspace,run_dir,report_path,lookup,config):
        workspace=Path(workspace).resolve();run_dir=Path(run_dir).resolve();source_report=(workspace/report_path.lstrip('/')).resolve()
        if not source_report.is_relative_to(workspace/'reports') or source_report.suffix!='.md':raise ValueError('Select a saved local report')
        structured=source_report.with_suffix('.json');bundle=json.loads(structured.read_text())
        cfg={'format':'aether-json-v1','hash_algorithm':'SHA-256','report_created_at':bundle['created_at'],
             'cutoff':bundle['cutoff'],'availability_mode':bundle['mode'],'research_mode':bundle['research_mode'],
             'analysis_python':platform.python_version(),'application':'aether','config':config,
             'integrity_scope':'Integrity, provenance and replay references; not source truth, forecast correctness or proof of a historical publication date'}
        config_bytes=canonical(cfg);package_id=source_report.stem+'-'+sha(config_bytes)[:10]
        destination=self.output_root/package_id;files={};records={};sources={};calculations=[]
        temp=Path(tempfile.mkdtemp(prefix='aether-bundle-',dir=self.output_root))
        def add_bytes(name,body,kind):
            if any(secret in body for secret in self.forbidden):raise ValueError('Credential detected in an export candidate; package refused')
            path=(temp/name).resolve()
            if not path.is_relative_to(temp.resolve()):raise ValueError('Invalid bundle path')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
            files[name]={'path':name,'sha256':sha(body),'bytes':len(body),'kind':kind}
        def add_file(name,path,kind,expected=None):
            path=Path(path).resolve();allowed=[workspace,self.root/'data',self.root/'src',self.root/'scripts',self.root/'contracts',run_dir]
            if self.software_root!=self.root:allowed.append(self.software_root)
            # Shared memory contains public snapshots/recipes, not arbitrary runtime or credentials.
            for part in ('raw','recipes'):allowed.append(self.knowledge_root/part)
            if not any(path.is_relative_to(p) for p in allowed) and path not in (self.root/'requirements.lock.txt',self.root/'requirements.audit.lock.txt',self.root/'pyproject.toml'):
                raise ValueError('Audit source outside approved project evidence/code paths')
            data=path.read_bytes()
            if expected and sha(data)!=expected:raise ValueError('Evidence changed before packaging: '+path.name)
            add_bytes(name,data,kind)
        source_index={s['source_id']:s for s in json.loads((run_dir/'manifest.json').read_text())['sources']}
        def add_record(rid,stack=()):
            if rid in stack:raise ValueError('Cycle in calculation input lineage')
            if rid in records:return
            row=lookup(rid);name='records/'+rid+'.json';add_bytes(name,canonical(row),'evidence_record');records[rid]=name
            if row.get('source'):
                s=row['source'];sources[s['source_id']]=s
                if row.get('raw_path'):add_file('raw/'+s['sha256']+'.bin',row['raw_path'],'raw_response',s['sha256'])
                elif rid.startswith('web-'):add_file('raw/'+s['sha256']+'.bin',workspace/'data/web/raw'/(s['sha256']+'.json'),'search_response',s['sha256'])
            for ref in row.get('evidence',[]):
                s=source_index.get(ref['source_id']) or sources.get(ref['source_id'])
                if not s:raise ValueError('Missing source lineage: '+ref['source_id'])
                sources[s['source_id']]=s
                if s.get('path'):add_file('raw/'+s['sha256']+'.bin',run_dir/s['path'],'raw_response',s['sha256'])
            for inp in row.get('input_record_ids',[]):add_record(inp,(*stack,rid))
            if row.get('recipe_path'):
                recipe='recipes/'+rid+'.py';add_file(recipe,row['recipe_path'],'calculation_code',row['recipe_sha256'])
                calculations.append({'record_id':rid,'recipe':recipe,'input_ids':row['input_record_ids'],'method':row.get('method')})
            elif rid.startswith('calc-'):calculations.append({'record_id':rid,'trusted_method':row.get('calculation_version'),'input_ids':row['input_record_ids']})
            elif row.get('simulation'):calculations.append({'record_id':rid,'trusted_method':'paper-v1','input_ids':row['input_record_ids']})
        try:
            add_file('report/report.md',source_report,'report');add_file('report/report.json',structured,'structured_report')
            review=bundle.get('review',{})
            ids=report_evidence_ids(bundle['report'])
            ids.update(q['record_id'] for v in review.get('verdicts',[]) for q in v.get('quotes',[]))
            for rid in sorted(ids):add_record(rid)
            if review.get('review_id'):add_file('review/'+review['review_id']+'.json',workspace/'reviews'/(review['review_id']+'.json'),'evidence_review')
            add_bytes('config.json',config_bytes,'configuration')
            add_bytes('replay_index.json',canonical({'records':records,'sources':sources,'calculations':calculations}),'replay_index')
            for path in (self.software_root/'src').rglob('*'):
                if path.is_file() and path.suffix in ('.py','.js','.css','.html','.md'):
                    add_file('software/'+str(path.relative_to(self.software_root/'src')),path,'software')
            for path in (self.software_root/'contracts').glob('*'):
                if path.is_file() and path.suffix in ('.sol','.json'):add_file('contracts/'+path.name,path,'contract')
            for name in ('requirements.lock.txt','requirements.audit.lock.txt','pyproject.toml'):
                if (self.software_root/name).exists():add_file('environment/'+name,self.software_root/name,'environment')
            if (self.software_root/'source_manifest.json').exists():add_file('environment/source_manifest.json',self.software_root/'source_manifest.json','software_snapshot')
            manifest={'format':'aether-bundle-v1','package_id':package_id,'report_id':source_report.stem,'created_at':bundle['created_at'],
                      'hash_algorithm':'SHA-256','canonicalization':'aether-json-v1: sorted UTF-8 JSON keys, compact separators, finite numbers',
                      'report_path':'report/report.md','structured_report_path':'report/report.json','config_path':'config.json',
                      'files':sorted(files.values(),key=lambda f:f['path']),'record_count':len(records),'source_count':len(sources)}
            (temp/'manifest.json').write_bytes(canonical(manifest))
            anchors={'package_id':package_id,'report_hash':files['report/report.md']['sha256'],
                     'manifest_hash':sha((temp/'manifest.json').read_bytes()),'config_hash':sha(config_bytes)}
            (temp/'integrity_reference.json').write_bytes(canonical({**anchors,'scope':'Locally computed digests; no public chain anchoring claimed'}))
            if destination.exists():
                if (destination/'manifest.json').read_bytes()!=(temp/'manifest.json').read_bytes():raise ValueError('Bundle version ID collision; refuse overwrite')
                shutil.rmtree(temp)
            else:temp.rename(destination)
            zip_path=self.output_root/(package_id+'.zip')
            with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
                for path in sorted(destination.rglob('*')):
                    if path.is_file():
                        info=zipfile.ZipInfo(str(path.relative_to(destination)));info.compress_type=zipfile.ZIP_DEFLATED
                        z.writestr(info,path.read_bytes())
            verification=verify_bundle(destination,anchors)
            return {**anchors,'path':str(destination),'zip_path':str(zip_path),'files':len(files),'records':len(records),
                    'verification':verification,'chain_status':'not_anchored','created_at':utcnow()}
        except Exception:
            if temp.exists():shutil.rmtree(temp)
            raise


def verify_bundle(directory,expected=None):
    directory=Path(directory).resolve();issues=[]
    try:manifest=json.loads((directory/'manifest.json').read_text())
    except (ValueError,OSError):return {'status':'failed','issues':[{'kind':'missing_or_invalid_manifest'}]}
    listed=set()
    for entry in manifest.get('files',[]):
        name=entry['path'];p=(directory/name).resolve()
        if not p.is_relative_to(directory) or name in listed:
            issues.append({'path':name,'kind':'invalid_or_duplicate_path'});continue
        listed.add(name)
        if not p.is_file():issues.append({'path':name,'kind':'missing_file'});continue
        raw=p.read_bytes()
        if sha(raw)!=entry['sha256'] or len(raw)!=entry['bytes']:issues.append({'path':name,'kind':'content_changed'})
    for name in ('report/report.md','report/report.json','config.json','replay_index.json'):
        if name not in listed:issues.append({'path':name,'kind':'required_file_not_in_manifest'})
    unexpected=[str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() and str(p.relative_to(directory)) not in listed|{'manifest.json','integrity_reference.json','chain_receipt.json'}]
    if unexpected:issues.append({'kind':'unlisted_files','paths':unexpected})
    actual={'manifest_hash':sha((directory/'manifest.json').read_bytes())}
    for name,key in ((manifest.get('report_path','report/report.md'),'report_hash'),(manifest.get('config_path','config.json'),'config_hash')):
        path=(directory/name).resolve()
        if path.is_relative_to(directory) and path.is_file():actual[key]=sha(path.read_bytes())
    if expected:
        for key in ('report_hash','manifest_hash','config_hash'):
            if actual.get(key)!=expected.get(key):issues.append({'kind':'anchor_digest_mismatch','hash':key})
    return {'status':'passed' if not issues else 'failed','issues':issues,'files_checked':len(listed),'hashes':actual,
            'expected_digests_supplied':bool(expected),'scope':'File integrity only; expected digests must be authenticated separately to establish anchoring'}
