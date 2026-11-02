"""Restore a frozen library to a new folder and verify its hashes and queries."""
import argparse,hashlib,json,shutil
from pathlib import Path
from research_data.library.store import ResearchLibrary
from research_data.library.verify import verify_library

def verify(snapshot,restored,receipt):
 snapshot=Path(snapshot).resolve();restored=Path(restored).resolve()
 if restored.exists():raise ValueError('Use a new restore destination')
 manifest=json.loads((snapshot/'DATASET_MANIFEST.json').read_text());files=manifest['files']
 for name,sha in files.items():
  p=(snapshot/name).resolve()
  if not p.is_relative_to(snapshot) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('Snapshot hash failure: '+name)
 shutil.copytree(snapshot,restored)
 for name,sha in files.items():
  if hashlib.sha256((restored/name).read_bytes()).hexdigest()!=sha:raise ValueError('Restore hash failure: '+name)
 original=ResearchLibrary(snapshot,create=False);copy=ResearchLibrary(restored,create=False)
 assert original.coverage()==copy.coverage()
 for dataset in original.datasets():
  end='2026-10-28T00:00:00Z' if dataset['frequency']=='snapshot' else '2026-10-26T00:00:00Z'
  assert original.series(dataset['dataset_id'],end=end,limit=10000)==copy.series(dataset['dataset_id'],end=end,limit=10000)
 assert original.search_documents('Ethereum',limit=100)==copy.search_documents('Ethereum',limit=100)
 assert original.search_outputs('ETH',limit=30)==copy.search_outputs('ETH',limit=30)
 with copy.connect(readonly=True) as c:
  for (rid,) in c.execute('SELECT min(record_id) FROM legacy WHERE json_extract(body,\'$.source.sha256\') IS NOT NULL GROUP BY json_extract(body,\'$.source.sha256\')'):
   row=copy.evidence(rid)
   if row.get('raw_path'):assert Path(row['raw_path']).is_relative_to(restored)
   if row.get('library_recipe_object'):assert Path(row['recipe_path']).is_relative_to(restored)
 audit=verify_library(copy);assert audit['integrity_passed'],audit['errors'][:5]
 for name,sha in files.items():assert hashlib.sha256((snapshot/name).read_bytes()).hexdigest()==sha
 result={'passed':True,'tracked_files_hash_verified':len(files),'dataset_queries_compared':len(original.datasets()),'coverage_equal':True,'text_query_equal':True,'saved_research_query_equal':True,'restored_integrity_passed':audit['integrity_passed'],'core_complete':audit['core_complete'],'scope':'Actual separate-folder restore, portable legacy raw/recipes, all dataset queries, hash and integrity checks. External access gaps remain gaps.'}
 Path(receipt).write_text(json.dumps(result,indent=2)+'\n');return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path,default=Path('data/datasets/aether-month-20260926-20261026-v1'));p.add_argument('--restore',type=Path,default=Path('local/data-library/month-20260926-20261026/restored'));p.add_argument('--receipt',type=Path,default=Path('local/data-library/month-20260926-20261026/RESTORE.json'));a=p.parse_args();print(json.dumps(verify(a.snapshot,a.restore,a.receipt),indent=2))
