import hashlib,json,tempfile,unittest,shutil
from pathlib import Path
from research_agent.audit import BundleBuilder,canonical
from research_agent.replay import replay_bundle
from research_agent.settings import Settings

class ReplayTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  w=self.root/'workspace';(w/'reports').mkdir(parents=True);run=self.root/'data/run';run.mkdir(parents=True)
  raw=b'{"value":42}';digest=hashlib.sha256(raw).hexdigest();p=run/'raw.bin';p.write_bytes(raw)
  source={'source_id':'s1','sha256':digest,'name':'fixture','url':'https://example.org','retrieved_at':'2026-10-13T00:00:00Z'}
  (run/'manifest.json').write_text(json.dumps({'sources':[]}))
  inputrow={'record_id':'live-input','source':source,'raw_path':str(p),'values':{'value':'42'},'units':{'value':'count'}}
  recipe=w/'calc.py';recipe.write_text('import json,sys\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[1]).read_text())\nv=json.loads(Path(r["raw_path"]).read_text())["value"]\nprint(json.dumps({"values":{"value":v},"units":{"value":"count"}}))\n')
  calc={'record_id':'live-derived','values':{'value':'42'},'units':{'value':'count'},'recipe_path':str(recipe),'recipe_sha256':hashlib.sha256(recipe.read_bytes()).hexdigest(),'input_record_ids':['live-input']}
  report={'report':{'title':'fixture','summary':'One fixture value','observations':[{'statement':'fixture value42','evidence_ids':['live-derived'],'metric':'value','value':'42','unit':'count'}],'judgment':'insufficient_evidence','judgment_reason':'fixture only','limitations':['fixture']},'created_at':'2026-10-14T00:00:00Z','cutoff':'2026-10-13T00:00:00Z','mode':'observed','research_mode':'current','review':{'status':'not_configured'}}
  (w/'reports/research-0123456789ab.json').write_text(json.dumps(report));(w/'reports/research-0123456789ab.md').write_text('fixture42')
  rows={'live-input':inputrow,'live-derived':calc}
  self.package=BundleBuilder(self.root,self.root/'audit').build(w,run,'/reports/research-0123456789ab.md',rows.__getitem__,{})
  shutil.rmtree(w);shutil.rmtree(run)
 def test_cold_replay_after_original_sources_are_removed(self):
  result=replay_bundle(self.package['path'],Settings(),self.package,True)
  self.assertEqual(result['status'],'passed');self.assertEqual(result['calculations_recomputed'],1);self.assertEqual(result['numeric_validation']['numeric_claims_checked'],1)
 def test_saved_code_is_not_silently_executed(self):
  result=replay_bundle(self.package['path'],Settings(),self.package,False)
  self.assertEqual(result['status'],'partial');self.assertEqual(result['calculation_checks'][0]['status'],'not_run')
 def test_numeric_mismatch_is_detected_even_with_consistent_new_manifest(self):
  # Simulates internally consistent but numerically incorrect unanchored evidence.
  directory=Path(self.package['path']);p=directory/'records/live-derived.json';row=json.loads(p.read_text());row['values']['value']='43';p.write_bytes(canonical(row))
  p=directory/'report/report.json';r=json.loads(p.read_text());r['report']['observations'][0]['value']='43';p.write_bytes(canonical(r))
  p=directory/'manifest.json';manifest=json.loads(p.read_text())
  for entry in manifest['files']:
   data=(directory/entry['path']).read_bytes();entry.update(sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
  p.write_bytes(canonical(manifest))
  result=replay_bundle(directory,Settings(),execute_recipes=True)
  self.assertEqual(result['status'],'failed');self.assertEqual(result['calculation_checks'][0]['status'],'failed')

if __name__=='__main__':unittest.main()
