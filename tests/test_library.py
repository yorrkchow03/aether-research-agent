"""Library contracts: real SQLite, immutable sources, time and revision queries."""
import json,tempfile,unittest
from pathlib import Path
from research_data.library import ResearchLibrary
from research_data.library.http import PublicCapture
from research_data.library.store import WINDOW_START,WINDOW_END
from research_data.library.derive import feature_values

class LibraryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.lib=ResearchLibrary(Path(self.tmp.name)/'library')
  self.lib.register_dataset('market.test',provider='test',frequency='1h',asset='ETH',scope='fixture',units={'price':'USD'})
  self.cap=self.lib.capture('test','https://example.com/data',b'{"price":"100"}',retrieved_at='2026-10-26T12:00:00Z')

 def test_identical_observation_and_object_are_idempotent(self):
  a=self.lib.put('market.test','2026-10-01',{'price':'100'},self.cap)
  b=self.lib.put('market.test','2026-10-01',{'price':'100'},self.cap)
  self.assertEqual(a,b)
  with self.lib.connect(readonly=True) as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM objects').fetchone()[0],1)

 def test_strict_does_not_backdate_historical_capture(self):
  self.lib.put('market.test','2026-10-01',{'price':100},self.cap)
  self.assertEqual(self.lib.series('market.test')['total'],1)
  self.assertEqual(self.lib.series('market.test',mode='strict')['total'],0)
  self.assertEqual(self.lib.series('market.test',mode='strict',known_by='2026-10-26T12:00:00Z')['total'],1)

 def test_revision_as_of_and_exact_microseconds(self):
  first=self.lib.capture('test','https://example.com/a',b'old',retrieved_at='2026-10-02T00:00:00.000001Z')
  second=self.lib.capture('test','https://example.com/a',b'new',retrieved_at='2026-10-02T00:00:00.000002Z')
  self.lib.put('market.test','2026-10-01',{'price':1},first)
  latest=self.lib.put('market.test','2026-10-01',{'price':2},second)
  self.assertEqual(self.lib.series('market.test')['records'][0]['record_id'],latest)
  self.assertEqual(self.lib.series('market.test',mode='strict',known_by='2026-10-02T00:00:00.000001Z')['records'][0]['values']['price'],'1')

 def test_half_open_window_and_closed_intervals(self):
  self.lib.put('market.test',WINDOW_START,{'price':1},self.cap,period_end='2026-09-26T01:00:00Z')
  self.lib.put('market.test',WINDOW_END,{'price':2},self.cap)
  self.lib.put('market.test','2026-10-25T23:00:00Z',{'price':3},self.cap,period_end='2026-10-26T01:00:00Z')
  self.assertEqual(self.lib.series('market.test')['total'],1)

 def test_nonfinite_boolean_and_unknown_unit_rejected(self):
  for value in [float('nan'),float('inf'),True]:
   with self.assertRaises(ValueError):self.lib.put('market.test',WINDOW_START,{'price':value},self.cap)
  with self.assertRaises(ValueError):self.lib.put('market.test',WINDOW_START,{'other':1},self.cap)

 def test_failed_capture_never_establishes_facts(self):
  cap=self.lib.capture('test','https://example.com/forbidden',b'forbidden',status=403,error='HTTP 403')
  with self.assertRaises(ValueError):self.lib.put('market.test',WINDOW_START,{'price':1},cap)

 def test_tampering_blocks_evidence(self):
  rid=self.lib.put('market.test',WINDOW_START,{'price':100},self.cap)
  evidence=self.lib.evidence(rid);Path(evidence['raw_path']).write_bytes(b'changed')
  with self.assertRaises(ValueError):self.lib.evidence(rid)

 def test_document_revision_does_not_recall_superseded_words(self):
  first=self.lib.capture('publisher','https://example.com/article',b'first',retrieved_at='2026-10-02')
  second=self.lib.capture('publisher','https://example.com/article',b'corrected',retrieved_at='2026-10-03')
  old=self.lib.put_document('publisher','https://example.com/article','Notice','upgrade confirmed',first,published_at='2026-10-01')
  new=self.lib.put_document('publisher','https://example.com/article','Notice','upgrade postponed',second,published_at='2026-10-01')
  self.assertNotEqual(old,new)
  self.assertEqual(self.lib.search_documents('confirmed')['total'],0)
  self.assertEqual(self.lib.search_documents('confirmed',mode='strict',known_by='2026-10-02')['total'],1)
  self.assertEqual(self.lib.search_documents('postponed')['total'],1)

 def test_bad_publication_date_and_blank_article_rejected(self):
  with self.assertRaises(ValueError):self.lib.put_document('p','https://example.com','t','text',self.cap,published_at='1970-01-01')
  with self.assertRaises(ValueError):self.lib.put_document('p','https://example.com','t',' ',self.cap,published_at='2026-10-01')

 def test_query_ranking_keeps_older_relevant_source_ahead_of_recent_generic_posts(self):
  target=self.lib.put_document('p','https://example.com/target','Glamsterdam Hoodi postponed','Specific original upgrade notice.',self.cap,published_at='2026-09-27')
  for i in range(80):self.lib.put_document('p',f'https://example.com/generic/{i}','Ethereum updates','Hoodi development and other Ethereum discussions.',self.cap,published_at='2026-10-25')
  packet=self.lib.search_documents('Glamsterdam Hoodi postponed',limit=5)
  self.assertEqual(packet['results'][0]['record_id'],target)
  self.assertEqual(packet['retrieval']['ranking'],'bm25_eligible_versions')
  self.assertNotEqual(self.lib.search_documents(limit=1)['results'][0]['record_id'],target)

 def test_strict_ranking_corpus_excludes_future_acquisitions(self):
  early=self.lib.capture('p','https://example.com/early',b'old',retrieved_at='2026-10-02')
  self.lib.put_document('p','https://example.com/one','Glamsterdam update','Hoodi postponed and upgrade timing.',early,published_at='2026-10-01')
  self.lib.put_document('p','https://example.com/two','Hoodi notice','Glamsterdam timing.',early,published_at='2026-10-01')
  before=self.lib.search_documents('Glamsterdam Hoodi',mode='strict',known_by='2026-10-03')
  future=self.lib.capture('p','https://example.com/future',b'future',retrieved_at='2026-10-20')
  for i in range(20):self.lib.put_document('p',f'https://example.com/future/{i}','Glamsterdam '*20,'Hoodi '*50,future,published_at='2026-10-01')
  after=self.lib.search_documents('Glamsterdam Hoodi',mode='strict',known_by='2026-10-03')
  self.assertEqual(before,after);self.assertEqual(after['retrieval']['eligible_corpus_documents'],2)

 def test_complete_count_does_not_hide_null_field(self):
  self.lib.put('market.test',WINDOW_START,{'price':None},self.cap)
  coverage=self.lib.coverage();self.assertFalse(coverage['core_complete'])
  self.assertEqual(coverage['datasets'][0]['null_counts']['price'],1)

 def test_backup_restore_and_source_hash(self):
  rid=self.lib.put('market.test',WINDOW_START,{'price':100},self.cap)
  dest=Path(self.tmp.name)/'backup';manifest=self.lib.backup(dest)
  restored=ResearchLibrary(dest,create=False)
  self.assertEqual(restored.evidence(rid)['values'],{'price':'100'})
  self.assertIn('research.sqlite',manifest)
  self.assertEqual(restored.series('market.test')['total'],1)

 def test_http_rejects_credentials_private_ips_and_ports(self):
  fetcher=PublicCapture(self.lib)
  for url in ['https://user:password@example.com/a','https://127.0.0.1/a','https://localhost/a','https://example.com:8765/']:
   with self.assertRaises(ValueError):fetcher.fetch('test',url)

 def test_feature_prefix_does_not_use_future(self):
  rows=[{'values':{'close_usd':str(100+i),'low_usd':str(99+i),'high_usd':str(101+i),'volume_asset':'10'}} for i in range(40)]
  baseline=feature_values(rows,30,False)
  rows[-1]['values']['close_usd']='100000'
  self.assertEqual(feature_values(rows,30,False),baseline)

if __name__=='__main__':unittest.main()
