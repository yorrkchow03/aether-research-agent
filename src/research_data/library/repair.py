"""Source-adaptive repairs with explicit provenance, never fabricated fills."""
from __future__ import annotations
import csv,io
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path
from .store import WINDOW_START,WINDOW_END,instant
from .numeric import NumericCollector
from research_data.normalize import timestamp
from .time import timestamp

def repair_public_sources(library,*,refresh=False):
 worker=NumericCollector(library,refresh=refresh);http=worker.http
 start=library.start;end=library.end

 def effr():
  url='https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate='+start.date().isoformat()+'&endDate='+(end-timedelta(days=1)).date().isoformat()
  cid,obj=http.json('federal_reserve',url);dates=[]
  for row in obj['refRates']:
   at=timestamp(row['effectiveDate'])
   if start<=at<end:
    if row['type']!='EFFR':raise ValueError('Unexpected interest-rate instrument')
    library.put('macro.fred.dff',at,{'rate_percent':row['percentRate']},cid,metadata={'original_series':'NYFed EFFR','fred_equivalent_series':'DFF','original_unit':'percent','revision_indicator':row.get('revisionIndicator'),'source_direct_not_fred_csv':True});dates.append(row['effectiveDate'])
  expected=rate_calendar(start,end,instrument='effr')
  return {'source_range_complete':set(dates)==set(expected),'observations':len(dates),'expected_source_dates':expected,'nonobservation_dates':nonbusiness_dates(start,end,instrument='effr'),'source':'Federal Reserve Bank of New York EFFR'}
 worker.job('macro.fred.dff',effr)

 url='https://www.federalreserve.gov/datadownload/Output.aspx?rel=H15&series=bf17364827e38702b42a58cf8eaa3f78&lastobs='+str(max(60,library.days+40))+'&from=&to=&filetype=csv&label=include&layout=seriescolumn'
 try:
  cid,raw=http.fetch('federal_reserve',url)
  if not raw.strip():raise ValueError('H15 returned an empty HTTP200 body; no CSV observations were acquired')
  rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
  header_index=next(i for i,r in enumerate(rows) if r and r[0]=='Time Period');header=rows[header_index]
  if not rows[1][1].startswith('Percent'):raise ValueError('Unexpected H15 rate units')
  for series,code in [('dgs2','RIFLGFCY02_N.B'),('dgs10','RIFLGFCY10_N.B')]:
   def treasury(s=series,k=code):
    col=header.index(k);dates=[];explicit_missing=[]
    for row in rows[header_index+1:]:
     if not row or not start<=timestamp(row[0])<end:continue
     if row[col] in ('ND','NA','','.'):explicit_missing.append(row[0]);continue
     library.put('macro.fred.'+s,row[0],{'rate_percent':row[col]},cid,metadata={'original_series':'H15/H15/'+k,'original_unit':'Percent:_Per_Year','source_direct_not_fred_csv':True});dates.append(row[0])
    expected=rate_calendar(start,end);missing=sorted(set(expected)-set(dates));last=max(dates) if dates else None
    # Pending is only a trailing publication gap. Internal missing dates stay unresolved.
    acquired=timestamp(library.capture_info(cid)['retrieved_at'])
    pending=[date for date in missing if last and date>last and acquired<h15_release_after(date)]
    return {'source_range_complete':bool(dates) and not (set(missing)-set(pending)),'observations':len(dates),'expected_source_dates':expected,
            'source_explicit_missing_dates':explicit_missing,'pending_publication_dates':pending,'publication_basis':'Pending only before the next business-day 16:15 America/New_York H15 release; overdue absent dates remain unresolved','nonobservation_dates':nonbusiness_dates(start,end)}
   worker.job('macro.fred.'+series,treasury)
 except Exception as exc:
  for series in ['dgs2','dgs10']:library.job('macro.fred.'+series,'failed',error='Federal Reserve H15 alternate: '+str(exc))

 # These are explicit alternative datasets. A source-defined execution fee burn
 # is not silently relabelled as total supply burn or average base fee.
 for name,path,field,unit in [('gas','gasused','gas_used','gas/day'),('burn','dailyethburnt','execution_fee_burn_eth','ETH/day')]:
  dataset='network.etherscan.'+name+'.daily'
  library.register_dataset(dataset,provider='etherscan',frequency='1d',asset='ETH',scope='Etherscan publisher chart '+path+'; source-specific daily aggregate, not an authenticated historical publication',units={field:unit},required=False)
  def chart(d=dataset,p=path,k=field):
   cid,raw=http.fetch('etherscan','https://etherscan.io/chart/'+p+'?output=csv');reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')));count=0
   for row in reader:
    if 'UnixTimeStamp' in row:at=timestamp(int(row['UnixTimeStamp']));value=row['Value']
    else:at=datetime.strptime(row['Date(UTC)'],'%m/%d/%Y').replace(tzinfo=timezone.utc);value=row['BurntFees']
    if start<=at<end:
     library.put(d,at,{k:value},cid,period_end=at+timedelta(days=1),metadata={'original_chart':p,'original_row':row});count+=1
   return {'source_range_complete':count==library.days,'observations':count}
  worker.job(dataset,chart)
 return library.coverage()

FED_HOLIDAYS_2026={'2026-01-01','2026-01-19','2026-02-16','2026-05-25','2026-06-19','2026-07-03','2026-09-07','2026-10-12','2026-11-11','2026-11-26','2026-12-25'}

def rate_calendar(start,end,*,instrument='treasury'):
 # Federal Reserve/Treasury holiday inside this fixed window. Keep this
 # explicit and versioned; ETF exchange calendars differ.
 holidays=FED_HOLIDAYS_2026-{'2026-07-03'} if instrument=='effr' else FED_HOLIDAYS_2026;out=[];at=start
 while at<end:
  date=at.date().isoformat()
  if at.weekday()<5 and date not in holidays:out.append(date)
  at+=timedelta(days=1)
 return out

def nonbusiness_dates(start,end,*,instrument='treasury'):
 expected=set(rate_calendar(start,end,instrument=instrument));out=[];at=start
 while at<end:
  date=at.date().isoformat()
  if date not in expected:out.append({'date':date,'state':'legitimate_no_observation','reason':'weekend' if at.weekday()>=5 else ('Federal Reserve Bank holiday' if instrument=='effr' else 'Treasury market holiday')})
  at+=timedelta(days=1)
 return out


def h15_release_after(date):
 at=timestamp(date)+timedelta(days=1)
 while at.weekday()>=5 or at.date().isoformat() in FED_HOLIDAYS_2026:at+=timedelta(days=1)
 return datetime(at.year,at.month,at.day,16,15,tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)


def collect_calendar(library):
 import re
 from .http import PublicCapture
 from .text import TextCollector
 http=PublicCapture(library)
 cid,raw=http.fetch('federal_reserve','https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm')
 html=raw.decode(errors='replace')
 section=re.search(r'2026 FOMC Meetings</a>(.*?)2025 FOMC Meetings</a>',html,re.S)
 if not section:raise ValueError('Official 2026 FOMC section unavailable')
 meetings=[]
 for month,days in re.findall(r'fomc-meeting__month[^>]*><strong>(\w+)</strong></div>\s*<div[^>]*fomc-meeting__date[^>]*>([\d\-*]+)</div>',section[1]):
  values=re.findall(r'\d+',days)
  begin=datetime.strptime(f'{month} {values[0]} 2026','%B %d %Y').replace(tzinfo=timezone.utc)
  stop=datetime.strptime(f'{month} {values[-1]} 2026','%B %d %Y').replace(tzinfo=timezone.utc)+timedelta(days=1)
  meetings.append({'start_date':begin.date().isoformat(),'end_date_inclusive':(stop-timedelta(days=1)).date().isoformat(),'publication_timezone':'America/New_York','scope':'Scheduled meeting dates, not a prediction of decisions'})
 if len(meetings)!=8:raise ValueError('Unexpected official annual meeting count; review parser')
 name='macro.federal_reserve.fomc.calendar'
 from .registry import install
 install(library)
 within=[m for m in meetings if m['start_date']<library.window_end[:10] and m['end_date_inclusive']>=library.window_start[:10]]
 for meeting in within:
  library.put(name,meeting['start_date'],{'scheduled_meeting':1},cid,period_end=timestamp(meeting['end_date_inclusive'])+timedelta(days=1),metadata=meeting)
 details={'source_range_complete':True,'expected_observations':len(within),'source_capture':cid,'calendar':meetings,'window_state':'scheduled_observations' if within else 'legitimate_no_observation','reason':'Official scheduled meetings intersecting configured window; no daily zeros generated'}
 library.job(name,'collected',**details)
 # Minutes released inside the window refer to a meeting before the window.
 # Publication date is read from the calendar release note, not the meeting URL.
 text=TextCollector(library)
 for link,month,day,year in re.findall(r'href="([^"]*fomcminutes\d+\.htm)"[^>]*>HTML</a>\s*<br> \(Released (\w+) (\d+), (\d+)\)',section[1]):
  date=datetime.strptime(f'{month} {day} {year}','%B %d %Y').replace(tzinfo=timezone.utc)
  if library.start<=date<library.end:text.article('federal_reserve','https://www.federalreserve.gov'+link,date)
 return details


def derive_mean_fee(library):
 import hashlib
 from research_data.http import dumps
 name='analysis.eth.network.daily'
 library.register_dataset(name,provider='derived',frequency='1d',asset='ETH',scope='Arithmetic daily fee per transaction, total Coin Metrics native fees / Coin Metrics transaction count. Not execution base fee or a separately licensed provider mean.',units={'mean_fee_eth':'ETH/transaction'},required=False)
 inputs=library.series('network.coinmetrics.eth.daily',limit=1000)['records']
 code=Path(__file__).read_bytes();sha=library.object(code)
 cid=library.capture('calculation:mean-fee-v1','urn:aether:calculation:'+name,dumps({'calculation_version':'mean-fee-v1','implementation_sha256':sha,'implementation_object':sha,'input_ids':[r['record_id'] for r in inputs]}).encode())
 for row in inputs:
  count=Decimal(row['values']['transaction_count']);fee=Decimal(row['values']['total_fees_eth'])
  library.put(name,row['observed_at'],{'mean_fee_eth':fee/count if count>0 else None},cid,period_end=row['period_end'],input_ids=[row['record_id']],metadata={'calculation_version':'mean-fee-v1','formula':'FeeTotNtv / TxCnt'})
 library.job(name,'collected',source_range_complete=len(inputs)==library.days,observations=len(inputs))
