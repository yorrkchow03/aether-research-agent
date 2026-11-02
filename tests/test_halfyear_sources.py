"""Deterministic source/calendar/continuity tests; fixtures are not real data."""
import json
import tempfile
from decimal import Decimal
import unittest
from datetime import timedelta
from pathlib import Path
from research_data.library.store import ResearchLibrary,instant
from research_data.library.registry import install
from research_data.library.repair import rate_calendar
from research_data.library.derive import contiguous_lookback,derive_all
from research_data.library.quality import SourceAudit
from research_data.library.ratio_proxy import derive_proxy,audit_proxy,DATASET


class HalfYearSourceTests(unittest.TestCase):
 def test_native_effr_calendar_is_distinct_from_treasury_market_calendar(self):
  with tempfile.TemporaryDirectory() as root:
   l=ResearchLibrary(root,window_start='2026-04-26T00:00:00Z',window_end='2026-10-26T00:00:00Z')
   effr=rate_calendar(l.start,l.end,instrument='effr');treasury=rate_calendar(l.start,l.end)
   self.assertEqual(len(effr),126);self.assertEqual(len(treasury),125)
   self.assertIn('2026-07-03',effr);self.assertNotIn('2026-07-03',treasury)
   self.assertNotIn('2026-10-12',effr)

 def test_independent_bitstamp_and_treasury_original_values_are_auditable(self):
  with tempfile.TemporaryDirectory() as root:
   l=ResearchLibrary(root);install(l)
   l.register_dataset('market.bitstamp.eth.hourly',provider='bitstamp',frequency='1h',asset='ETH',units={**{k+'_usd':'USD' for k in ('open','high','low','close')},'volume_asset':'ETH'},scope='Synthetic independent-venue fixture',required=False)
   payload={'data':{'ohlc':[{'timestamp':'1790380800','open':'10.01','high':'11','low':'9','close':'10.02','volume':'20.33'}]}}
   cid=l.capture('bitstamp','https://www.bitstamp.net/fixture',json.dumps(payload).encode())
   rid=l.put('market.bitstamp.eth.hourly',1790380800,{'open_usd':'10.01','high_usd':'11','low_usd':'9','close_usd':'10.02','volume_asset':'20.33'},cid)
   audit=SourceAudit(l);row=l.evidence(rid);self.assertTrue(audit.compare(row))
   row['values']['close_usd']='10.03'
   with self.assertRaisesRegex(ValueError,'Source value mismatch'):audit.compare(row)
   raw=b'<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><properties><d:NEW_DATE>2026-09-28T00:00:00</d:NEW_DATE><d:BC_2YEAR>3.45</d:BC_2YEAR></properties></feed>'
   cid=l.capture('us_treasury','https://home.treasury.gov/fixture',raw)
   l.register_dataset('macro.treasury.cmt2.daily',provider='us_treasury',frequency='business_day',units={'rate_percent':'percent/year'},scope='Synthetic Treasury fixture',required=False)
   rid=l.put('macro.treasury.cmt2.daily','2026-09-28',{'rate_percent':'3.45'},cid,metadata={'original_field':'BC_2YEAR'})
   self.assertTrue(audit.compare(l.evidence(rid)))

 def test_missing_hour_invalidates_only_lookbacks_that_cross_it(self):
  with tempfile.TemporaryDirectory() as root:
   l=ResearchLibrary(root);begin=l.start-timedelta(days=30)
   rows=[{'observed_at':instant(begin+timedelta(hours=i))} for i in range(1500) if i!=725]
   self.assertTrue(contiguous_lookback(rows,720,True))
   self.assertFalse(contiguous_lookback(rows,725,True))
   self.assertFalse(contiguous_lookback(rows,1444,True))
   self.assertTrue(contiguous_lookback(rows,1445,True))

 def test_proxy_is_separate_and_zero_denominator_stays_unknown(self):
  with tempfile.TemporaryDirectory() as root:
   l=ResearchLibrary(root);install(l)
   for d,field,unit in [('network.etherscan.gas.daily','gas_used','gas/day'),('network.etherscan.burn.daily','execution_fee_burn_eth','ETH/day')]:
    l.register_dataset(d,provider='etherscan',frequency='1d',units={field:unit},scope='Synthetic ratio-input fixture',required=False)
   cid=l.capture('etherscan','https://etherscan.io/fixture',b'fixture')
   for date,gas in [('2026-09-26','1000000'),('2026-09-27','0')]:
    l.put('network.etherscan.gas.daily',date,{'gas_used':gas},cid)
    l.put('network.etherscan.burn.daily',date,{'execution_fee_burn_eth':'0.010'},cid,metadata={'original_row':{'BurntFees':'0.010'}})
   result=derive_proxy(l);self.assertEqual(result['observations'],1);self.assertEqual(len(result['unknown_denominator_dates']),1)
   row=l.series(DATASET)['records'][0];audit_proxy(l,row)
   self.assertEqual(Decimal(row['values']['burn_to_gas_ratio_wei_per_gas']),Decimal('10000000000'))
   self.assertEqual(l.series('network.ethereum.base_fee.daily')['total'],0)
   self.assertIn('uncertain',row['library_metadata']['scope_status'])
   row['values']['burn_to_gas_ratio_wei_per_gas']='0'
   with self.assertRaisesRegex(ValueError,'differs from saved Decimal'):audit_proxy(l,row)

 def test_sec_archive_follows_next_until_past_fixed_start(self):
  from research_data.library.text import TextCollector
  with tempfile.TemporaryDirectory() as root:
   l=ResearchLibrary(root);worker=TextCollector(l);visited=[];articles=[]
   first=b'<table><tr><td><time datetime="2026-10-01T00:00:00Z"></time></td><td><a href="/newsroom/press-releases/2026-current">ETH custody</a></td></tr></table><a href="?page=1">Next</a>'
   second=b'<table><tr><td><time datetime="2026-09-25T00:00:00Z"></time></td><td><a href="/newsroom/press-releases/2026-older">Older</a></td></tr></table><a href="?page=2">Next</a>'
   def fetch(source,url):
    visited.append(url);return 'fixture',second if 'page=1' in url else first
   worker.http.fetch=fetch;worker.article=lambda *args:articles.append(args)
   result=worker.sec_archive('sec','https://www.sec.gov')
   self.assertTrue(result['discovery_complete']);self.assertEqual(len(visited),2);self.assertEqual(len(articles),1)
   self.assertNotIn('?page=2',visited)

 def test_oi_audit_retains_unknown_changes_and_rejects_cross_gap_claim(self):
  from research_data.library.verify import verify_library
  with tempfile.TemporaryDirectory() as root:
   l=ResearchLibrary(root);install(l);base=l.start
   rows=[[str(int((base+timedelta(hours=i)).timestamp()*1000)),str(v),str(v),'100'] for i,v in [(0,10),(1,11),(3,12)]]
   cid=l.capture('okx','https://www.okx.com/fixture',json.dumps({'data':rows}).encode())
   dataset='derivatives.okx.eth.oi.hourly'
   for i,v in [(0,10),(1,11),(3,12)]:
    values={'oi_contracts':str(v),'oi_eth':str(v),'oi_usd':'100'}
    if i==1:values.update(oi_change_usd='0',oi_change_fraction='0')
    l.put(dataset,base+timedelta(hours=i),values,cid)
   report=verify_library(l);self.assertTrue(report['integrity_passed'],report['errors']);self.assertEqual(report['checks']['oi_changes_unknown_missing_exact_prior'],2)
   bad=l.put(dataset,base+timedelta(hours=3),{'oi_contracts':'12','oi_eth':'12','oi_usd':'100','oi_change_usd':'0','oi_change_fraction':'0'},cid,metadata={'fixture':'invalid cross-gap claim'})
   errors=verify_library(l)['errors'];self.assertTrue(any('OI change asserted without a valid preceding-hour' in str(x) for x in errors))

 def test_oi_collector_repairs_only_sparse_internal_history_holes(self):
  from research_data.library.numeric import NumericCollector
  from urllib.parse import parse_qs,urlsplit
  with tempfile.TemporaryDirectory() as root:
   l=ResearchLibrary(root,window_start='2026-04-26T00:00:00Z',window_end='2026-10-26T00:00:00Z');worker=NumericCollector(l);calls=[]
   points=[l.end-timedelta(hours=i) for i in range(7,0,-1)];missing=points[3]
   def response(source,url):
    calls.append(url);repair=parse_qs(urlsplit(url).query).get('limit')==['10'];selected=points if repair else [t for t in points if t!=missing]
    payload={'data':[[str(int(t.timestamp()*1000)),'10','1','100'] for t in selected]};cid=l.capture(source,url,json.dumps(payload).encode());return cid,payload
   worker.http.json=response;result=worker._oi()
   self.assertEqual(sum(parse_qs(urlsplit(url).query).get('limit')==['10'] for url in calls),1)
   self.assertLess(len(calls),60);self.assertFalse(result['source_range_complete']);self.assertEqual(l.series('derivatives.okx.eth.oi.hourly')['total'],7)
   self.assertIn(instant(l.start).replace('.000000Z','Z'),result['unresolved_intervals'])


if __name__=='__main__':unittest.main()
