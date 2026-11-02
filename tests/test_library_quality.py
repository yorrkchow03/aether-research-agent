"""Regress actual defects found in the captured monthly library."""
import json,tempfile,unittest
from pathlib import Path
from research_data.library.store import ResearchLibrary,instant
from research_data.library.time import timestamp
from research_data.library.repair import h15_release_after,rate_calendar
from research_data.library.text import html_text,nonarticle_reason,reconcile_discovery
from research_data.library.quality import SourceAudit

class LibraryQualityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.l=ResearchLibrary(Path(self.tmp.name)/'library')
 def test_date_only_is_utc_not_local_timezone(self):
  self.assertEqual(instant('2026-09-28'),'2026-09-28T00:00:00.000000Z')
  self.assertEqual(instant('2026-09-28T08:00:00+08:00'),instant('2026-09-28'))
 def test_macro_calendar_and_release_deadline(self):
  dates=rate_calendar(timestamp('2026-09-26'),timestamp('2026-10-26'))
  self.assertEqual(len(dates),19);self.assertNotIn('2026-10-12',dates)
  self.assertEqual(h15_release_after('2026-10-23').isoformat(),'2026-10-26T20:15:00+00:00')
 def test_publisher_date_line_and_body_container(self):
  raw=b'<html><head><title>Report</title></head><body><nav>menu</nav><div id="article"><h1>ETH</h1><p>October 23, 2026</p><p>Original source paragraph.</p></div><footer>footer</footer></body></html>'
  title,text,meta=html_text(raw,body_only=True)
  self.assertIn('Original source paragraph.',text);self.assertNotIn('menu',text);self.assertNotIn('footer',text)
  self.assertEqual(meta['published_at'],'2026-10-23T00:00:00+00:00')
 def test_nonarticle_quarantine_preserves_original_evidence(self):
  cap=self.l.capture('ethereum','https://blog.ethereum.org',b'raw')
  rid=self.l.put_document('ethereum','https://blog.ethereum.org','Hub','navigation',cap,published_at='2026-10-01')
  reconcile_discovery(self.l)
  self.assertEqual(self.l.search_documents('navigation')['total'],0)
  self.assertEqual(self.l.evidence(rid)['text'],'navigation')
  self.assertIsNone(nonarticle_reason('ethereum','https://blog.ethereum.org/2026/10/01/update'))
  self.assertIsNone(nonarticle_reason('coindesk','https://www.coindesk.com/daybook-us/2026/10/01/market-update'))
  self.assertIsNone(nonarticle_reason('coindesk','https://www.coindesk.com/news-analysis/2026/10/01/market-update'))
  self.assertIsNotNone(nonarticle_reason('coindesk','https://www.coindesk.com/videos/market-update'))
 def test_raw_response_comparison_catches_changed_numeric_fact(self):
  self.l.register_dataset('market.coinbase.eth.hourly',provider='coinbase',frequency='1h',asset='ETH',scope='fixture',units={k:'USD' for k in ['low_usd','high_usd','open_usd','close_usd','volume_asset']})
  data=[[int(timestamp('2026-10-01').timestamp()),99,103,100,102,7]]
  cap=self.l.capture('coinbase','https://example.com/candles',json.dumps(data).encode())
  rid=self.l.put('market.coinbase.eth.hourly','2026-10-01',dict(zip(['low_usd','high_usd','open_usd','close_usd','volume_asset'],data[0][1:])),cap)
  row=self.l.evidence(rid);self.assertTrue(SourceAudit(self.l).compare(row));row['values']['close_usd']='101'
  with self.assertRaisesRegex(ValueError,'mismatch'):SourceAudit(self.l).compare(row)
 def test_quarantined_date_error_never_enters_series(self):
  self.l.register_dataset('macro.test',provider='p',frequency='business_day',asset='USD',scope='fixture',units={'rate':'percent'})
  cap=self.l.capture('p','https://example.com/rates',b'raw');rid=self.l.put('macro.test','2026-09-27T16:00:00Z',{'rate':3},cap)
  with self.l.connect() as c:c.execute('INSERT INTO relations VALUES(?,?,?,?)',(rid,'excluded_from_research','utc_date_reparse_v2','{}'))
  self.assertEqual(self.l.series('macro.test')['total'],0);self.assertTrue(self.l.contains(rid))
  self.assertIn('utc_date_reparse_v2',self.l.evidence(rid)['library_exclusion_reasons'])
 def test_legacy_raw_and_recipe_are_portable_in_restore(self):
  raw=b'original JSON';sha=self.l.object(raw);code=self.l.object(b'print(1)')
  body={'record_id':'old','raw_path':'/unavailable/old.json','source':{'sha256':sha},'library_recipe_object':code}
  with self.l.connect() as c:c.execute('INSERT INTO legacy VALUES(?,?)',('old',json.dumps(body)))
  target=Path(self.tmp.name)/'restored';self.l.backup(target);row=ResearchLibrary(target,create=False).evidence('old')
  self.assertEqual(Path(row['raw_path']).read_bytes(),raw);self.assertEqual(Path(row['recipe_path']).read_bytes(),b'print(1)')

 def test_freeze_preserves_gaps_and_hashes_all_delivery_files(self):
  import hashlib
  from research_data.library.registry import install
  from research_data.library.verify import freeze_library
  install(self.l);target=Path(self.tmp.name)/'frozen';result=freeze_library(self.l,target)
  self.assertEqual(result['state'],'available_data_with_explicit_unresolved_requirements')
  manifest=json.loads((target/'DATASET_MANIFEST.json').read_text())
  self.assertIn('GAPS.json',manifest['files']);self.assertIn('DISCOVERY.json',manifest['files'])
  for name,sha in manifest['files'].items():self.assertEqual(hashlib.sha256((target/name).read_bytes()).hexdigest(),sha)
  with self.assertRaises(ValueError):freeze_library(self.l,target)

 def test_freeze_refuses_active_collection_and_recursive_backup(self):
  from research_data.library.verify import freeze_library
  self.l.job('text:test','running')
  with self.assertRaisesRegex(ValueError,'running'):freeze_library(self.l,Path(self.tmp.name)/'frozen')
  with self.assertRaisesRegex(ValueError,'outside'):self.l.backup(self.l.root/'recursive')

 def test_staking_metric_requests_use_the_documented_consensus_asset(self):
  import contextlib,io
  from research_data.library.numeric import NumericCollector
  collector=NumericCollector(self.l);calls=[]
  def cm(metric,asset='eth',warmup=0):calls.append((metric,asset));return {}
  collector.cm=cm
  with contextlib.redirect_stdout(io.StringIO()):collector.network()
  self.assertIn(('SplyStkedNtv','eth_cl'),calls)
  self.assertIn(('ValidatorActOngCnt','eth_cl'),calls)
  self.assertIn(('SplyCur','eth'),calls)

 def test_nontext_forum_action_is_distinct_from_missing_regular_post(self):
  from research_data.library.text import empty_forum_post_state
  self.assertEqual(empty_forum_post_state({'post_type':3,'action_code':'visible.enabled'}),('excluded','publisher_nontext_system_action'))
  self.assertEqual(empty_forum_post_state({'post_type':1,'cooked':''})[0],'body_unavailable')
  self.assertEqual(empty_forum_post_state({'post_type':3})[0],'body_unavailable')
  url='https://governance.aave.com/t/example/1/2'
  self.l.discover('aave',url,status='excluded',reason='publisher_nontext_system_action',availability_state='legitimate_no_text_observation',capture_id='saved-api')
  reconcile_discovery(self.l);reconcile_discovery(self.l)
  with self.l.connect(readonly=True) as c:row=c.execute('SELECT reason,body FROM discoveries WHERE url=?',(url,)).fetchone()
  self.assertEqual(row['reason'],'selection:publisher_nontext_system_action')
  self.assertEqual(json.loads(row['body'])['capture_id'],'saved-api')

 def test_fed_discovery_includes_speeches_and_testimony_not_only_press_releases(self):
  from research_data.library.text import TextCollector
  c=TextCollector(self.l);calls=[];articles=[]
  def fetch(source,url):
   calls.append(url)
   path='/newsevents/pressreleases/monetary20260930a.htm' if '2026-press.htm' in url else '/newsevents/speech/example20261001a.htm' if '2026-speeches.htm' in url else '/newsevents/testimony/example20261002a.htm'
   return 'fixture',('<a href="'+path+'">Monetary policy</a>').encode()
  c.http.fetch=fetch;c.article=lambda name,url,date,label:articles.append(url)
  result=c.index('federal_reserve','https://www.federalreserve.gov','unused')
  self.assertEqual(len(calls),3);self.assertEqual(len(articles),3);self.assertTrue(result['discovery_complete'])

 def test_ether_keyword_does_not_match_whether_or_together(self):
  from research_data.library.text import RELEVANT,ETH_MENTION
  negative='Whether robots can work together remains uncertain; mistaking a demo for deployment is premature.'
  self.assertIsNone(RELEVANT.search(negative));self.assertIsNone(ETH_MENTION.search(negative))
  self.assertIsNotNone(RELEVANT.search('Ether staking and Ethereum activity'))
  self.assertIsNotNone(ETH_MENTION.search('ETH has an upcoming upgrade'))

 def test_identical_text_preserves_sources_and_links_actual_versions(self):
  from research_data.library.relations import link_documents
  text=' '.join(['Ethereum evidence is preserved with its original source.']*15)
  first=self.l.capture('one','https://one.example/a',text.encode(),retrieved_at='2026-10-02')
  second=self.l.capture('two','https://two.example/a',text.encode(),retrieved_at='2026-10-03')
  a=self.l.put_document('one','https://one.example/a','Original',text,first,published_at='2026-10-01')
  b=self.l.put_document('two','https://two.example/a','Another source',text,second,published_at='2026-10-01')
  corrected=self.l.capture('one','https://one.example/a',(text+' Corrected.').encode(),retrieved_at='2026-10-04')
  new=self.l.put_document('one','https://one.example/a','Original',text+' Corrected.',corrected,published_at='2026-10-01')
  before=link_documents(self.l);after=link_documents(self.l)
  self.assertEqual(before,after);self.assertEqual(before['identical_normalized_text'],1)
  self.assertEqual(self.l.search_documents()['total'],2)
  self.assertEqual(self.l.evidence(a)['text'],self.l.evidence(b)['text'])
  self.assertTrue(any(r['predicate']=='content_revision_of' and r['object']==a for r in self.l.record_links(new)['results']))
  self.assertEqual(self.l.record_links(a,known_by='2026-10-02')['total'],0)

 def test_later_exclusion_does_not_erase_an_earlier_strict_source(self):
  first=self.l.capture('publisher','https://example.com/article',b'Ethereum source',retrieved_at='2026-10-02')
  rid=self.l.put_document('publisher','https://example.com/article','Notice','Ethereum source',first,published_at='2026-10-01')
  doc=self.l.evidence(rid)['document_id']
  with self.l.connect() as c:c.execute('INSERT INTO relations VALUES(?,?,?,?)',(doc,'excluded_from_research','later_publisher_change',json.dumps({'effective_known_at':'2026-10-04T00:00:00.000000Z'})))
  self.assertEqual(self.l.search_documents()['total'],0)
  self.assertEqual(self.l.search_documents(mode='strict',known_by='2026-10-03')['total'],1)
  self.assertEqual(self.l.search_documents(mode='strict',known_by='2026-10-05')['total'],0)

 def test_missing_document_relationship_target_fails_integrity_audit(self):
  from research_data.library.registry import install
  from research_data.library.verify import verify_library
  install(self.l)
  with self.l.connect() as c:c.execute('INSERT INTO relations VALUES(?,?,?,?)',('missing-a','content_revision_of','missing-b','{}'))
  self.assertFalse(verify_library(self.l)['integrity_passed'])

 def test_semantic_main_keeps_minutes_after_intro_without_global_navigation(self):
  raw=b'<html><title>Minutes</title><nav>Global navigation</nav><div role="main" id="content"><div id="article"><p>Meeting introduction.</p></div><p>Full monetary policy discussion.</p><p>Final policy decision.</p></div><footer>Global footer</footer></html>'
  _,legacy,_=html_text(raw,body_only=True)
  _,current,meta=html_text(raw,body_only=True,scope_version=4)
  self.assertNotIn('Final policy decision.',legacy)
  self.assertIn('Final policy decision.',current);self.assertIn('Full monetary policy discussion.',current)
  self.assertNotIn('Global navigation',current);self.assertNotIn('Global footer',current)
  self.assertEqual(meta['body_extraction'],'publisher_semantic_main')

 def test_semantic_main_handles_self_closing_images_without_early_body_end(self):
  raw=b'<html><nav>Buy crypto</nav><div id="main"><h1>Research report</h1><img src="chart.png"/><p>Actual research paragraph.</p><br/><p>Final original paragraph.</p></div><footer>Platform products</footer></html>'
  _,body,meta=html_text(raw,body_only=True,scope_version=4)
  self.assertIn('Research report',body);self.assertIn('Final original paragraph.',body)
  self.assertNotIn('Buy crypto',body);self.assertNotIn('Platform products',body)

if __name__=='__main__':unittest.main()
