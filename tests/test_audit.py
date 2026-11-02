import hashlib,json,tempfile,unittest,shutil
from pathlib import Path
from research_agent.audit import BundleBuilder,verify_bundle,canonical

class AuditTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.run=self.root/'data/run';(self.run/'raw').mkdir(parents=True);self.w=self.root/'workspace';(self.w/'reports').mkdir(parents=True)
  raw=b'{"actual_value":42}';p=self.run/'raw/response.bin';p.write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
  (self.run/'manifest.json').write_text(json.dumps({'sources':[{'source_id':'s1','path':'raw/response.bin','sha256':digest,'url':'https://example.org/public','retrieved_at':'2026-10-14T00:00:00Z'}]}))
  self.row={'record_id':'r1','values':{'value':'42'},'units':{'value':'count'},'evidence':[{'source_id':'s1','locator':'/actual_value'}]}
  report={'report':{'observations':[{'evidence_ids':['r1']}],'interpretations':[],'forecasts':[]},'created_at':'2026-10-14T00:00:00Z','cutoff':'2026-10-13T00:00:00Z','mode':'observed','research_mode':'historical'}
  (self.w/'reports/research-0123456789ab.json').write_text(json.dumps(report));(self.w/'reports/research-0123456789ab.md').write_text('Actual value42')
  self.builder=BundleBuilder(self.root,self.root/'data/audit',('fixture-private-key',))
 def build(self):return self.builder.build(self.w,self.run,'/reports/research-0123456789ab.md',lambda rid:self.row,{'model':'fixture'})
 def test_portable_manifest_covers_report_record_raw_config_and_sources(self):
  r=self.build();self.assertEqual(r['verification']['status'],'passed');self.assertTrue(Path(r['zip_path']).is_file())
  idx=json.loads((Path(r['path'])/'replay_index.json').read_text());self.assertEqual(idx['records']['r1'],'records/r1.json');self.assertIn('s1',idx['sources'])
 def test_modified_report_and_deleted_source_are_detected(self):
  r=self.build();d=Path(r['path']);(d/'report/report.md').write_text('changed');v=verify_bundle(d,r)
  self.assertEqual(v['status'],'failed');self.assertTrue(any(e['kind']=='anchor_digest_mismatch' for e in v['issues']))
  raw=next((d/'raw').glob('*'));raw.unlink();v=verify_bundle(d,r);self.assertTrue(any(e['kind']=='missing_file' for e in v['issues']))
 def test_altered_manifest_cannot_pass_against_original_digests(self):
  r=self.build();p=Path(r['path'])/'manifest.json';d=json.loads(p.read_text());d['record_count']=999;p.write_bytes(canonical(d))
  self.assertTrue(any(e.get('hash')=='manifest_hash' for e in verify_bundle(p.parent,r)['issues']))
 def test_package_rejects_credential_or_preexisting_source_tampering(self):
  (self.w/'reports/research-0123456789ab.md').write_text('fixture-private-key')
  with self.assertRaises(ValueError):self.build()
  (self.w/'reports/research-0123456789ab.md').write_text('public report');(self.run/'raw/response.bin').write_bytes(b'changed original')
  with self.assertRaises(ValueError):self.build()
 def test_path_traversal_in_manifest_and_extra_files_are_reported(self):
  r=self.build();d=Path(r['path']);(d/'unlisted.txt').write_text('unlisted')
  self.assertTrue(any(e['kind']=='unlisted_files' for e in verify_bundle(d,r)['issues']))
  p=d/'manifest.json';m=json.loads(p.read_text());m['files'][0]['path']='../../outside';p.write_bytes(canonical(m))
  self.assertTrue(any(e['kind']=='invalid_or_duplicate_path' for e in verify_bundle(d,r)['issues']))
 def test_shared_news_snapshot_is_exportable_but_other_runtime_files_are_not(self):
  raw=b'public announcement';digest=hashlib.sha256(raw).hexdigest();path=self.root/'runtime/knowledge/raw'/(digest+'.bin');path.parent.mkdir(parents=True);path.write_bytes(raw)
  self.row.update(raw_path=str(path),source={'source_id':'new-source','sha256':digest,'url':'https://example.org/news'});self.row['evidence']=[]
  self.assertEqual(self.build()['verification']['status'],'passed')
  secret=self.root/'runtime/private-config.bin';secret.write_bytes(raw);self.row['raw_path']=str(secret)
  with self.assertRaises(ValueError):self.build()

if __name__=='__main__':unittest.main()
