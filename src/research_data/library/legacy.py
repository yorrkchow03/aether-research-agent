"""Read-only import of actual evidence and successful published research outputs."""
import json,sqlite3,hashlib
from pathlib import Path
from research_data.http import dumps

def import_legacy(library,project):
 project=Path(project);path=project/'runtime/knowledge/knowledge.sqlite'
 source=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
 imported=0;missing=[]
 for record in source.execute('SELECT record_id,body FROM evidence'):
  row=json.loads(record['body'])
  if row.get('raw_path'):
   raw_path=Path(row['raw_path'])
   if raw_path.is_file():
    raw=raw_path.read_bytes();sha=library.object(raw)
    if row.get('source',{}).get('sha256') not in (None,sha):
     missing.append({'record_id':record['record_id'],'reason':'raw_hash_mismatch'});continue
    row['raw_path']=str(library.root/'objects'/sha[:2]/(sha+'.bin'))
   else:missing.append({'record_id':record['record_id'],'reason':'raw_file_unavailable'})
  if row.get('recipe_path') and Path(row['recipe_path']).is_file():
   code=Path(row['recipe_path']).read_bytes()
   if row.get('recipe_sha256') and hashlib.sha256(code).hexdigest()!=row['recipe_sha256']:
    missing.append({'record_id':record['record_id'],'reason':'recipe_hash_mismatch'})
   else:row['library_recipe_object']=library.object(code)
  with library.connect() as c:
   old=c.execute('SELECT body FROM legacy WHERE record_id=?',(record['record_id'],)).fetchone()
   if old:
    prior=json.loads(old[0]);prior.pop('library_recipe_object',None);compare=dict(row);compare.pop('library_recipe_object',None)
    if prior!=compare:raise ValueError('Legacy ID rebound')
   c.execute('INSERT OR REPLACE INTO legacy VALUES(?,?)',(record['record_id'],dumps(row,sort_keys=True)))
  imported+=1
 output_count={}
 for table in ['reports','answers']:
  n=0
  for row in source.execute(f'SELECT * FROM {table}'):
   body=json.loads(row['body']);identity=row['report_id' if table=='reports' else 'answer_id']
   found=set()
   def visit(value):
    if isinstance(value,dict):
     for k,v in value.items():
      if k in ('record_id','source_record_id') and isinstance(v,str):found.add(v)
      if k in ('record_ids','source_ids','input_record_ids','evidence_ids','source_evidence_ids') and isinstance(v,list):found.update(x for x in v if isinstance(x,str))
      visit(v)
    elif isinstance(value,list):
     for v in value:visit(v)
   visit(body)
   with library.connect() as c:
    c.execute('INSERT OR IGNORE INTO research_outputs VALUES(?,?,?,?)',(identity,table,row['created_at'],dumps(body,sort_keys=True)))
    for rid in found:c.execute('INSERT OR IGNORE INTO output_citations VALUES(?,?)',(identity,rid))
    c.execute('DELETE FROM output_fts WHERE output_id=?',(identity,))
    content=body.get('report') or body.get('draft') or {}
    c.execute('INSERT INTO output_fts VALUES(?,?)',(identity,dumps(content)))
    def claims(value,path=''):
     if isinstance(value,dict):
      if isinstance(value.get('statement'),str):
       c.execute('INSERT OR IGNORE INTO output_claims VALUES(?,?,?)',(identity,path,dumps(value,sort_keys=True)))
       for evidence in value.get('evidence_ids',[]):c.execute('INSERT OR IGNORE INTO claim_citations VALUES(?,?,?)',(identity,path,evidence))
      for key,v in value.items():claims(v,path+'/'+key)
     elif isinstance(value,list):
      for i,v in enumerate(value):claims(v,path+'/'+str(i))
    claims(content)
   n+=1
  output_count[table]=n
 source.close()
 # Import normalized historical aliases once from the selected immutable archive.
 # Raw objects are shared by hash; do not duplicate their payload in every row.
 pointer=project/'data/latest.json';archive_count=0
 if pointer.is_file():
  selected=json.loads(pointer.read_text());run=Path(selected['run_dir'])
  if not run.is_absolute():run=project/run
  manifest=json.loads((run/'manifest.json').read_text());sources={r['source_id']:r for r in manifest['sources']};raw_sources={}
  for sid,meta in sources.items():
   if not meta.get('path'):continue
   p=run/meta['path']
   if p.is_file():
    raw=p.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if meta.get('sha256')!=sha:raise ValueError('Historical raw-source hash mismatch')
    library.object(raw);raw_sources[sid]={**meta,'sha256':sha,'raw_path':str(library.root/'objects'/sha[:2]/(sha+'.bin'))}
  db=sqlite3.connect((run/'research.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
  with library.connect() as c:
   for (payload,) in db.execute('SELECT payload_json FROM records'):
    row=json.loads(payload)
    refs=row.get('evidence',[])
    if refs and refs[0]['source_id'] in raw_sources:
     src=raw_sources[refs[0]['source_id']]
     row['source']={k:src[k] for k in ('source_id','url','sha256','retrieved_at')};row['source']['name']=src.get('name',src['source_id'])
     row['raw_path']=src['raw_path']
    old=c.execute('SELECT body FROM legacy WHERE record_id=?',(row['record_id'],)).fetchone()
    body=dumps(row,sort_keys=True)
    if old and old[0]!=body:raise ValueError('Historical alias ID changed')
    c.execute('INSERT OR IGNORE INTO legacy VALUES(?,?)',(row['record_id'],body));archive_count+=1
  db.close()
 # Some older published reports referenced workspace-only calculations/context.
 # Restore the actual saved JSON, preserving IDs, instead of inventing reports.
 recovered=0;unresolved=[]
 with library.connect(readonly=True) as c:pending=[r[0] for r in c.execute('SELECT DISTINCT record_id FROM output_citations')]
 checked=set()
 while pending:
  rid=pending.pop()
  if rid in checked:continue
  checked.add(rid)
  if library.contains(rid):
   pending.extend(library.evidence(rid).get('input_record_ids',[]));continue
  paths=list((project/'runtime/sessions').glob('*/workspace/data/**/'+rid+'.json'))+list((project/'data/audit').glob('*/records/'+rid+'.json'))
  if not paths:unresolved.append(rid);continue
  variants=[json.loads(p.read_text()) for p in paths];row=variants[0]
  if row.get('record_id')!=rid:
   missing.append({'record_id':rid,'reason':'workspace_alias_conflict'});continue
  if any(v!=row for v in variants):
   ids=[]
   for p,variant in zip(paths,variants):
    raw=p.read_bytes();sha=library.object(raw);vid='legacy-'+hashlib.sha256(dumps(variant,sort_keys=True).encode()).hexdigest()[:24]
    version={**variant,'record_id':vid,'legacy_original_id':rid,'source':{'source_id':'legacy-file-'+sha[:24],'name':'Actual version of legacy report dependency','url':'urn:aether:legacy-record:'+rid,'sha256':sha},'raw_path':str(library.root/'objects'/sha[:2]/(sha+'.bin'))}
    with library.connect() as c:
     c.execute('INSERT OR IGNORE INTO legacy VALUES(?,?)',(vid,dumps(version,sort_keys=True)))
     if 'audit' in p.parts:
      for (oid,) in c.execute('SELECT output_id FROM research_outputs'):
       if oid in str(p):c.execute('INSERT OR IGNORE INTO relations VALUES(?,?,?,?)',(oid,'citation_version:'+rid,vid,'{}'))
    ids.append(vid)
   alias={'record_id':rid,'dataset_id':'local.ambiguous_legacy_alias','values':{},'units':{},'variants':sorted(set(ids)),'quality_flags':['same_legacy_ID_has_distinct_saved_versions'],'scope':'Resolve the report-specific citation_version relation; no one version is silently selected'}
   with library.connect() as c:c.execute('INSERT OR IGNORE INTO legacy VALUES(?,?)',(rid,dumps(alias,sort_keys=True)))
   missing.append({'record_id':rid,'reason':'workspace_alias_conflict_preserved_as_explicit_versions','versions':alias['variants']});recovered+=1;continue
  raw=paths[0].read_bytes();sha=library.object(raw)
  row={**row,'source':{'source_id':'legacy-file-'+sha[:24],'name':'Actual saved published-report dependency','url':'urn:aether:legacy-record:'+rid,'sha256':sha},'raw_path':str(library.root/'objects'/sha[:2]/(sha+'.bin')),'library_legacy_acquisition':'Imported now; original system acquisition time not authenticated'}
  with library.connect() as c:c.execute('INSERT INTO legacy VALUES(?,?)',(rid,dumps(row,sort_keys=True)))
  pending.extend(row.get('input_record_ids',[]));recovered+=1
 result={'evidence':imported,'outputs':output_count,'raw_issues':missing,'scope':'Original IDs preserved; no generated historical reports or synthetic holdings'}
 result['historical_aliases']=archive_count
 result.update(recovered_workspace_dependencies=recovered,unresolved_citation_dependencies=sorted(set(unresolved)))
 library.job('legacy-import','complete' if not missing else 'partial',**result)
 return result
