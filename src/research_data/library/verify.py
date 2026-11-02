"""Offline data-quality audit and immutable available-data snapshot publication."""
from __future__ import annotations
import hashlib,json,math,statistics
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from research_data.http import dumps,utcnow
from research_data.normalize import timestamp
from .store import WINDOW_START,WINDOW_END,instant
from .derive import feature_values,feature_contracts,contiguous_lookback
from .quality import SourceAudit
from .time import timestamp

def verify_library(library):
 errors=[];warnings=[];checks={};raw_cache={};source_audit=SourceAudit(library);compared_by_dataset={};contracts={r['dataset_id']:r for r in library.datasets()}
 active_documents=set();page=0
 while True:
  packet=library.search_documents(limit=100,offset=page);active_documents.update(r['record_id'] for r in packet['results'])
  if packet['next_offset'] is None:break
  page=packet['next_offset']
 with library.connect(readonly=True) as c:
  checks['sqlite_integrity']=c.execute('PRAGMA integrity_check').fetchone()[0]
  if checks['sqlite_integrity']!='ok':errors.append('SQLite integrity failure')
  objects=list(c.execute('SELECT * FROM objects'))
  captures={r['capture_id']:dict(r) for r in c.execute('SELECT * FROM captures')}
  observations=[dict(r) for r in c.execute('SELECT * FROM observations')]
  documents=[json.loads(r[0]) for r in c.execute('SELECT body FROM document_versions')]
  outputs=list(c.execute('SELECT * FROM research_outputs'))
  citations=list(c.execute('SELECT * FROM output_citations'))
  excluded_docs={r[0] for r in c.execute("SELECT subject FROM relations WHERE predicate='excluded_from_research'")}
  document_links=[dict(r) for r in c.execute("SELECT * FROM relations WHERE predicate IN ('content_revision_of','metadata_revision_of','identical_normalized_text')")]
 for row in objects:
  path=(library.root/row['path']).resolve()
  if not path.is_relative_to(library.root/'objects') or not path.is_file():errors.append('Missing/outside raw object '+row['sha256']);continue
  if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:errors.append('Changed raw object '+row['sha256'])
 checks['raw_objects_checked']=len(objects)
 def raw_json(cid):
  if cid not in raw_cache:
   raw_cache[cid]=json.loads(library.raw(cid),parse_float=Decimal)
  return raw_cache[cid]
 malformed=0;source_compared=0;warmup=0
 for item in observations:
  row=json.loads(item['body']);dataset=row['dataset_id'];meta=row.get('library_metadata',{})
  try:
   cap=captures[row['library_capture_id']]
   if row['source']['source_id']!=row['library_capture_id'] or row['source']['sha256']!=cap['sha256'] or row['source']['url']!=cap['url']:raise ValueError('Observation citation/source mapping differs from original capture')
   if row['units']!=contracts[dataset]['units']:raise ValueError('Stored units differ from registered dataset contract')
   if cap['status']!=200 or cap['error']:raise ValueError('Fact linked to unsuccessful request')
   for value in row['values'].values():
    if value is not None and not Decimal(value).is_finite():raise ValueError('Nonfinite numeric value')
   if timestamp(row['period_end'])<timestamp(row['observed_at']):raise ValueError('Negative time interval')
   if timestamp(row['observed_at'])<library.start:warmup+=1
   if row['record_id'] not in excluded_docs and source_audit.compare(row):compared_by_dataset[dataset]=compared_by_dataset.get(dataset,0)+1
   if dataset.startswith('market.coinbase.'):
    data=raw_json(row['library_capture_id']);by_time={instant(r[0]):r for r in data}
    original=by_time[row['observed_at']]
    expected=dict(zip(['low_usd','high_usd','open_usd','close_usd','volume_asset'],original[1:6]))
    if any(Decimal(row['values'][k])!=Decimal(str(v)) for k,v in expected.items()):raise ValueError('OHLCV does not match raw response')
    source_compared+=1
   for cid in meta.get('field_captures',{}).values():
    if cid not in captures or captures[cid]['status']!=200:raise ValueError('Missing per-field source capture')
   for parent in row.get('input_record_ids',[]):
    if not library.contains(parent):raise ValueError('Missing calculation parent '+parent)
  except (ValueError,KeyError,TypeError) as exc:
   malformed+=1;errors.append({'record_id':row['record_id'],'error':str(exc)})
 checks.update(observations_checked=len(observations),malformed_observations=malformed,raw_candles_compared=source_compared,warmup_observations=warmup)
 checks['raw_values_compared_by_dataset']=compared_by_dataset
 coverage=library.coverage();time_checks=[]
 if 'network.ethereum.execution.daily' in contracts:
  try:checks['native_execution_range']=source_audit.headers.complete_range(library.series('network.ethereum.execution.daily',limit=1000)['records'])
  except (ValueError,KeyError,TypeError) as exc:errors.append({'native_execution_range':str(exc)})
  if coverage['jobs'].get('ethereum-header-backfill',{}).get('source_range_complete') and not checks.get('native_execution_range',{}).get('full_month'):errors.append('Header job claims complete without a verified full monthly aggregate range')
  native=library.series('network.ethereum.execution.daily',limit=1000)['records'];cross_source=[]
  for reference,field,native_field in [('network.etherscan.gas.daily','gas_used','gas_used'),('network.etherscan.burn.daily','execution_fee_burn_eth','execution_base_fee_burn_eth')]:
   if reference not in contracts:continue
   index={r['observed_at']:r for r in library.series(reference,limit=1000)['records']};comparisons=[]
   for row in native:
    if row['observed_at'] not in index:continue
    from .header_quality import provider_difference
    other=index[row['observed_at']];comparison=provider_difference(row['values'][native_field],other['values'][field],allow_display_rounding=field=='execution_fee_burn_eth')
    comparisons.append({'observed_at':row['observed_at'],'native_id':row['record_id'],'reference_id':other['record_id'],'native_value':row['values'][native_field],'reference_value':other['values'][field],**comparison})
   different=sum(r['comparison']=='unexplained_difference' for r in comparisons)
   cross_source.append({'reference_dataset':reference,'aligned_days':len(comparisons),'exactly_equal_days':sum(r['comparison']=='exact' for r in comparisons),'display_precision_compatible_days':sum(r['comparison']=='compatible_with_displayed_precision' for r in comparisons),'unexplained_difference_days':different,'comparisons':comparisons,'scope':'Independent provider comparison with both original values retained. Gas counts require exact equality. Burn precision compatibility means only a difference within half the publisher-displayed decimal unit, not a claim about its rounding policy, scope equivalence or factual truth. Unexplained differences require review, never automatic value replacement.'})
   if different:warnings.append({'cross_source_review':reference,'differing_days':different})
  checks['native_execution_provider_comparisons']=cross_source
 for dataset in coverage['datasets']:
  if dataset['frequency'] not in ('1h','1d'):continue
  packet=library.series(dataset['dataset_id'],limit=10000);rows=packet['records'];step=timedelta(hours=1) if dataset['frequency']=='1h' else timedelta(days=1)
  expected={instant(library.start+i*step) for i in range(library.hours if step.total_seconds()==3600 else library.days)}
  seen={r['observed_at'] for r in rows};missing=sorted(expected-seen);extra=sorted(seen-expected)
  time_checks.append({'dataset_id':dataset['dataset_id'],'expected':len(expected),'observed':len(seen),'missing':missing,'unexpected':extra})
  if dataset['status']=='complete' and (missing or extra):errors.append('False complete interval coverage: '+dataset['dataset_id'])
 checks['interval_coverage']=time_checks
 recomputed=0
 for dataset,input_dataset,hourly in feature_contracts(library):
   inputs=library.series(input_dataset,start=library.start-timedelta(days=30 if hourly else 91),limit=10000)['records']
   index={r['observed_at']:i for i,r in enumerate(inputs)}
   actual=library.series(dataset,limit=10000)['records']
   for row in actual:
    try:
     i=index[row['observed_at']]
     if not contiguous_lookback(inputs,i,hourly):raise ValueError('Feature lookback crosses a missing source interval')
     expected=feature_values(inputs,i,hourly)
     for key,value in expected.items():
      got=row['values'].get(key)
      if value is None:
       if got is not None:raise ValueError('Undefined feature became a number')
      elif got is None or not math.isclose(float(got),value,rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Feature differs from saved inputs: '+key)
     recomputed+=1
    except (ValueError,KeyError,TypeError) as exc:errors.append({'record_id':row['record_id'],'calculation_error':str(exc)})
 checks['descriptive_feature_rows_recomputed']=recomputed
 for row in documents:
  try:
   library.raw(row['library_capture_id'])
   if not row['text'].strip():raise ValueError('Empty original text')
   if not row.get('published_at') or not library.start<=timestamp(row['published_at'])<library.end:raise ValueError('Document outside publication window')
   if row['source_kind']=='external_report_pdf':
    from .pdf_reports import extract_pdf
    text,meta=extract_pdf(library.raw(row['library_capture_id']))
    if row['text']!=text or any(row['library_metadata'].get(k)!=v for k,v in meta.items()):raise ValueError('PDF text/page scope differs from original attachment')
    parent=library.evidence(row['library_metadata']['parent_page_evidence_id'])
    if row['published_at']!=parent['published_at']:raise ValueError('PDF publication claim differs from parent publisher page')
    primary=parent.get('library_metadata',{}).get('publication_metadata',{}).get('publisher_primary',{})
    if primary.get('download_url')!=row['url'] or not primary.get('download_is_public_direct'):raise ValueError('PDF is not the publisher primary public attachment')
    if row['library_metadata']['original_pdf_sha256']!=row['source']['sha256']:raise ValueError('PDF original-byte fingerprint differs from source')
    if not any(r['predicate']=='report_attachment_of' and r['object']==parent['record_id'] for r in library.record_links(row['record_id'])['results']):raise ValueError('PDF attachment provenance link is missing')
   elif row['source_kind']=='forum_post':
    payload=raw_json(row['library_capture_id']);posts=payload.get('post_stream',{}).get('posts',payload.get('posts',[]))
    original=next((p for p in posts if p['id']==row['library_metadata']['post_id']),None)
    if not original:raise ValueError('Forum post absent in original response')
    if instant(original['created_at'])!=row['published_at']:raise ValueError('Forum publication timestamp differs from original response')
    from research_agent.public_web import PageText
    parser=PageText();parser.feed(original.get('cooked',''))
    if parser.text()!=row['text']:raise ValueError('Forum text differs from captured original')
   else:
    from .text import html_text
    extraction=row.get('library_metadata',{}).get('extraction')
    _,original,publication=html_text(library.raw(row['library_capture_id']),body_only=extraction in ('PageText-v2','PageText-v3-utc','PageText-v4-semantic','PageText-v5-publisher'),scope_version=4 if extraction in ('PageText-v4-semantic','PageText-v5-publisher') else 3,source_url=row['url'])
    if original!=row['text']:raise ValueError('Extracted text differs from saved original page')
    if row['record_id'] in active_documents:
     expected=publication.get('published_at') or row.get('library_metadata',{}).get('source_date_hint')
     if expected and instant(expected)!=row['published_at']:raise ValueError('Active publication timestamp differs from publisher metadata/dated listing')
  except (ValueError,KeyError,TypeError) as exc:errors.append({'record_id':row['record_id'],'document_error':str(exc)})
 checks['document_versions_checked']=len(documents)
 checks['active_original_documents']=len(active_documents)
 from .relations import normalized_text
 document_index={r['record_id']:r for r in documents}
 for link in document_links:
  before=document_index.get(link['object']);after=document_index.get(link['subject'])
  if not before or not after:errors.append('Document relationship references missing evidence');continue
  if link['predicate']=='identical_normalized_text':
   a=normalized_text(before['text']);b=normalized_text(after['text'])
   if a!=b or len(a)<500 or before['document_id']==after['document_id']:errors.append('Invalid identical-text relationship')
   if json.loads(link['body']).get('normalized_text_sha256')!=hashlib.sha256(a.encode()).hexdigest():errors.append('Identical-text fingerprint mismatch')
  else:
   changed=any(before.get(k)!=after.get(k) for k in ('title','text','published_at'))
   if before['document_id']!=after['document_id'] or (link['predicate']=='content_revision_of')!=changed:errors.append('Invalid document revision relationship')
 checks['document_relationships_checked']=len(document_links)
 checks['quarantined_records_or_documents']=len(excluded_docs)
 # Latest OI changes, relative prices and mean fee are independently
 # recomputed; old partial revisions remain available by evidence ID.
 oi=library.series('derivatives.okx.eth.oi.hourly',limit=10000)['records'] if any(r['dataset_id']=='derivatives.okx.eth.oi.hourly' for r in coverage['datasets']) else []
 oi_index={row['observed_at']:row for row in oi};change_count=0;unknown_change=0
 for row in oi:
  before_at=instant(timestamp(row['observed_at'])-timedelta(hours=1))
  prior=oi_index.get(before_at)
  previous=(prior['values'] if prior else source_audit.indexed(row['library_capture_id'],'oi').get(before_at))
  got=row['values'];fields=('oi_change_usd','oi_change_fraction')
  if not previous or Decimal(previous['oi_usd'])<=0:
   unknown_change+=1
   if any(got.get(key) is not None for key in fields):errors.append('OI change asserted without a valid preceding-hour observation: '+row['record_id'])
   continue
  before=Decimal(previous['oi_usd']);after=Decimal(got['oi_usd'])
  if Decimal(got.get('oi_change_usd','NaN'))!=after-before or Decimal(got.get('oi_change_fraction','NaN'))!=after/before-1:errors.append('OI change differs from exact preceding hour: '+row['record_id'])
  change_count+=1
 checks['oi_changes_recomputed']=change_count
 checks['oi_changes_unknown_missing_exact_prior']=unknown_change
 for row in (r for r in observations if r['dataset_id']=='analysis.eth.network.daily'):
  body=json.loads(row['body']);parent=library.evidence(body['input_record_ids'][0]);values=parent['values'];count=Decimal(values['transaction_count'])
  if count>0 and Decimal(body['values']['mean_fee_eth'])!=Decimal(values['total_fees_eth'])/count:errors.append('Mean fee differs from saved source')
 available={r['dataset_id'] for r in coverage['datasets']}
 if 'analysis.eth_btc.daily' in available:
  begin=library.start-timedelta(days=91)
  eth=library.series('market.coinbase.eth.daily',start=begin,limit=1000)['records'];btc=library.series('market.coinbase.btc.daily',start=begin,limit=1000)['records']
  e={r['observed_at']:Decimal(r['values']['close_usd']) for r in eth};b={r['observed_at']:Decimal(r['values']['close_usd']) for r in btc}
  relative=library.series('analysis.eth_btc.daily',limit=1000)['records']
  for row in relative:
   at=timestamp(row['observed_at']);now=instant(at);ratio=e[now]/b[now];values=row['values']
   expected={'eth_btc_ratio':ratio}
   for days in (7,30):
    then=instant(at-timedelta(days=days));expected[f'relative_return_{days}d']=ratio/(e[then]/b[then])-1
   x=[];y=[]
   for i in range(89,-1,-1):
    end=instant(at-timedelta(days=i));prior=instant(at-timedelta(days=i+1));x.append(float(e[end]/e[prior]-1));y.append(float(b[end]/b[prior]-1))
   expected['return_correlation_90d']=statistics.correlation(x,y)
   if any(not math.isclose(float(values[k]),float(v),rel_tol=1e-12,abs_tol=1e-12) for k,v in expected.items()):errors.append('Relative indicator mismatch: '+row['record_id'])
  checks['relative_indicator_rows_recomputed']=len(relative)
 if 'analysis.eth.funding' in available:
  rows=library.series('derivatives.okx.eth.funding',start=library.start-timedelta(days=30),limit=1000)['records'];history={};expected={}
  for i in range(1,len(rows)):
   row=rows[i];seconds=(timestamp(row['observed_at'])-timestamp(rows[i-1]['observed_at'])).total_seconds();rate=Decimal(row['values']['realized_rate_fraction']);group=history.setdefault(seconds,[]);group.append(rate)
   expected[row['observed_at']]={'latest_settled_rate_fraction':rate,'median_settled_rate_fraction':statistics.median(group),'empirical_percentile_fraction':sum(v<=rate for v in group)/len(group)}
  actual=library.series('analysis.eth.funding',limit=1000)['records']
  for row in actual:
   if any(not math.isclose(float(row['values'][k]),float(v),rel_tol=1e-12,abs_tol=1e-12) for k,v in expected[row['observed_at']].items()):errors.append('Funding prefix statistic mismatch: '+row['record_id'])
  checks['funding_statistic_rows_recomputed']=len(actual)
 unresolved_citations=[]
 for link in citations:
  if not library.contains(link['record_id']):unresolved_citations.append(dict(link))
 if unresolved_citations:warnings.append({'legacy_citations_not_in_main_knowledge_index':unresolved_citations})
 checks['existing_research_outputs']=len(outputs);checks['existing_output_citations']=len(citations)
 for row in coverage['datasets']:
  if row['status']!='complete':warnings.append({'dataset':row['dataset_id'],'status':row['status'],'records':row['records'],'null_counts':row['null_counts'],'job':row['job']})
 text_jobs={k:v for k,v in coverage['jobs'].items() if k.startswith('text:')}
 text_complete=len(text_jobs)==10 and all(v.get('discovery_complete') for v in text_jobs.values())
 failed_text=[r for r in coverage['text_discoveries'] if r['status'] not in ('saved','excluded')]
 if not text_complete:warnings.append({'text_discovery_incomplete':[k for k,v in text_jobs.items() if not v.get('discovery_complete')]})
 if failed_text:warnings.append({'text_acquisition_unresolved':failed_text})
 checks['strict_historical_sample_counts']={r['dataset_id']:library.series(r['dataset_id'],mode='strict',limit=1)['total'] for r in coverage['datasets'] if r['frequency']!='snapshot'}
 result={'schema':'aether-library-audit-v1','checked_at':utcnow(),'window_start':library.window_start,'window_end':library.window_end,
         'integrity_passed':not errors,'core_complete':coverage['core_complete'],'text_discovery_complete':text_complete,
         'all_acceptance_passed':not errors and coverage['core_complete'] and text_complete and not failed_text and not unresolved_citations,
         'checks':checks,'errors':errors,'warnings':warnings,'scope':'Offline source integrity, numerical recomputation and coverage checks; publisher factual truth and complete global news recall are not established'}
 return result

def freeze_library(library,destination=None):
 label='aether-month-20260926-20261026-v1' if (library.window_start,library.window_end)==(WINDOW_START,WINDOW_END) else 'aether-window-'+library.window_start[:10].replace('-','')+'-'+library.window_end[:10].replace('-','')+'-v1'
 destination=Path(destination) if destination else library.root.parent/'datasets'/label
 with library.connect(readonly=True) as c:
  running=[r[0] for r in c.execute("SELECT job_id FROM jobs WHERE status='running'")]
 if running:raise ValueError('Cannot freeze while collection jobs are running: '+str(running))
 report=verify_library(library)
 if not report['integrity_passed']:raise ValueError('Repair integrity/numeric failures before freezing')
 manifest=library.backup(destination)
 (destination/'ACCEPTANCE.json').write_text(dumps(report,indent=2)+'\n')
 coverage=library.coverage();(destination/'COVERAGE.json').write_text(dumps(coverage,indent=2)+'\n')
 from .delivery import delivery_metadata
 for name,body in delivery_metadata(library).items():(destination/name).write_text(dumps(body,indent=2)+'\n')
 state='complete' if report['all_acceptance_passed'] else 'available_data_with_explicit_unresolved_requirements'
 published={'schema':'aether-monthly-dataset-v1' if (library.window_start,library.window_end)==(WINDOW_START,WINDOW_END) else 'aether-research-dataset-v1','created_at':utcnow(),'state':state,'core_complete':report['core_complete'],
            'window_start':library.window_start,'window_end':library.window_end,'files':{str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob('*') if p.is_file()},
            'scope':'Available captured records and explicit gaps; no missing core metric is represented as acquired'}
 (destination/'DATASET_MANIFEST.json').write_text(dumps(published,indent=2)+'\n')
 return {'destination':str(destination),'state':state,'files':len(published['files']),'all_acceptance_passed':report['all_acceptance_passed']}
