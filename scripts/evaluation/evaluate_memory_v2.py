"""Freeze and replay small source-grounded memory diagnostics, without provider calls.

Real sources, authored retrieval labels, controlled synthetic streams and live
business acceptance are separate denominators. No model is downloaded here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from time import perf_counter
import tempfile

from research_data.http import utcnow
from research_data.library.time import timestamp

ROOT=Path(__file__).resolve().parents[2]
SCHEMA='memory-v2-frozen-diagnostic-v1'
DEFAULT_OUTPUT=ROOT/'local/memory-upgrade/v2/evaluation'
REAL_IDS=[
 'page-546a7ebd8e4cad2fd9923418','page-9794078ccdabb208adf9ff50',
 'page-07345dd93a22d9281f0d07b0','page-f1cea43ff2153503093ab512',
 'page-3f64d24a4a565fec9c3ec8b6','page-909e6e40e7d37c42856274e5',
 'page-9f39396e41297803b4e37a2d','page-3ba96b7df44b5b10e44b5dfb',
 'page-3402fd35e34e63571edecc89','page-54dd0aac87d099a6a4726dca',
 'page-126dfccc9f6355f0bc45cc40','page-bc252ee1f123bafeb6bfb27d',
 'page-ecbc797ed6a57c6268c9ded3','page-3751c7f7352c16a315c805d1',
 'page-ad9467c2beb272e547137edf','page-925014c4c04b2612687cdbc1',
]
QUERY_SPECS=[
 ('hoodi-delay','Hoodi fork postponed replacement activation date',[(REAL_IDS[0],'A new activation date has not been set.')],'development_previously_seen'),
 ('private-broadcast-wallet','Do ordinary wallet RPC transfers expose the sender IP address in the private transaction relay issue?',[(REAL_IDS[7],'Wallet RPCs (sendtoaddress, sendall, etc.) do not use private\nbroadcast and are not affected.')],'new_ai_authored_diagnostic'),
 ('bitcoin-release-after-warning','Bitcoin private transaction broadcast fix software release 31.1',[(REAL_IDS[5],'Bitcoin Core version 31.1 is now available for download.'),(REAL_IDS[7],'A fix is\nforthcoming and will be released with 31.1.')],'new_ai_authored_diagnostic'),
 ('aave-activation-vote','Aave V4 risk steward permissions activated networks after a governance vote',[(REAL_IDS[10],'The Aave V4 Risk Stewards are now active across Ethereum, Avalanche, and Base after AIP 523 execution.'),(REAL_IDS[8],'Voting will begin in less than 24 hours.')],'new_ai_authored_diagnostic'),
 ('stablecoin-identity-proposal','customer identity verification requirements for payment tokens proposal comments',[(REAL_IDS[14],'requested comment on a proposal to require certain payment stablecoin issuers to maintain an effective customer identification program.')],'new_ai_authored_diagnostic'),
 ('stablecoin-reserve-proposal','payment stablecoin capital reserve backing Treasury assets application requirements',[(REAL_IDS[15],'fully back their stablecoins with certain permissible reserve assets')],'new_ai_authored_diagnostic'),
 ('anchor-auditor','Who conducted the security assessment for the Anchor validator client?',[(REAL_IDS[13],'security audit was performed by Sigma Prime’s information security services team')],'new_ai_authored_diagnostic'),
 ('zkapi-chinese','保护隐私的 API 计费如何证明请求有足够余额，不暴露支付者身份？',[(REAL_IDS[3],'authorize bounded usage with zero-knowledge proofs instead of an identity.')],'development_previously_seen'),
]
PROTOCOL={'schema':SCHEMA,'context_budget':3600,'result_limit':4,'candidate_limit':30,
 'source_limit':100,'rrf_k':60,'availability_mode':'observed','full_cutoff':'2026-10-28T00:00:00Z',
 'packing':'same EvidenceMemory exact excerpts and context budget; hybrid uses host retrieval_scores/hybrid_relevance when installed',
 'label_author':'Codex AI-authored exact-span diagnostic labels; not human truth',
 'not_heldout':True,'new_independent_samples_claimed':False,
 'selection_note':'Sixteen fixed originals selected by bounded saved-library source/topic discovery, before comparing policies. EF/Hoodi cases already inspected are development. New query wording is AI-authored diagnostic, not an untouched test.',
 'time_scope':'Sources are actually captured versions. Their old publication dates do not prove old system availability; the Hoodi announcement capture already contains the October22 update. No earlier uncorrected snapshot is fabricated.',
 'metrics':['target_source_in_candidates','all_required_sources_in_pack','required_exact_span_present','strict_future_source_excluded','candidate_rank','context_bytes_estimate','latency_seconds'],
 'semantic_accuracy_measured':False,'investment_quality_measured':False,
 'synthetic_cases':['same_entity_different_network','original_then_correction','later_acquired_source','future_correction_notice','duplicate_card','damaged_span','console_fake_review','failed_tool_recovery']}


def canonical(value):
 return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def sha(value):
 return hashlib.sha256(value if isinstance(value,bytes) else canonical(value).encode()).hexdigest()


def require(condition,message):
 if not condition:raise ValueError(message)


def json_read(path):return json.loads(Path(path).read_text())


def freeze_rows(output,rows,queries,*,notices=None,source_scope='synthetic_unit_fixture'):
 """Write a new immutable input manifest; never overwrite prior diagnostics."""
 output=Path(output)
 require(not output.exists(),'Choose a fresh frozen diagnostic directory')
 by_id={row['record_id']:row for row in rows};require(len(by_id)==len(rows),'Duplicate source IDs in freeze')
 prepared=[]
 for case in queries:
  case=deepcopy(case)
  for passage in case.get('required_passages',[]):
   source=by_id[passage['record_id']];text=source['text'];quote=passage['text']
   start=text.find(quote);require(start>=0,'Required exact passage is absent: '+case['case_id'])
   passage.update(start=start,end=start+len(quote),sha256=hashlib.sha256(quote.encode()).hexdigest())
  prepared.append(case)
 output.mkdir(parents=True)
 files={'sources.json':{'schema':SCHEMA,'scope':source_scope,'sources':rows},
        'queries.json':{'schema':SCHEMA,'queries':prepared},'protocol.json':PROTOCOL,
        'notices.json':notices or {'reports':{},'replacements':{},'items':[]}}
 for name,value in files.items():(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
 manifest={'schema':SCHEMA,'frozen_at':utcnow(),'source_count':len(rows),'query_count':len(prepared),
  'source_scope':source_scope,'human_ground_truth':False,'heldout':False,
  'files':{name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in sorted(files)}}
 (output/'MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
 (output/'MANIFEST.sha256').write_text(hashlib.sha256((output/'MANIFEST.json').read_bytes()).hexdigest()+'\n')
 return manifest


def load_frozen(output):
 output=Path(output);manifest=json_read(output/'MANIFEST.json')
 require(hashlib.sha256((output/'MANIFEST.json').read_bytes()).hexdigest()==(output/'MANIFEST.sha256').read_text().strip(),'Frozen manifest changed')
 for name,digest in manifest['files'].items():
  path=(output/name).resolve();require(path.is_relative_to(output.resolve()),'Frozen path escapes its root')
  require(hashlib.sha256(path.read_bytes()).hexdigest()==digest,'Frozen input changed: '+name)
 rows=json_read(output/'sources.json')['sources'];queries=json_read(output/'queries.json')['queries']
 for case in queries:
  for target in case.get('required_passages',[]):
   row=next(r for r in rows if r['record_id']==target['record_id'])
   require(row['text'][target['start']:target['end']]==target['text'],'Frozen source passage binding changed')
 return manifest,rows,queries,json_read(output/'protocol.json'),json_read(output/'notices.json')


def freeze_real(output,library):
 from research_agent.library_router import open_library
 lib=open_library(ROOT,library);require(lib is not None,'Install the selected saved library first')
 rows=[lib.evidence(rid) for rid in REAL_IDS]
 cases=[{'case_id':name,'query':query,'label_scope':scope,'asset':'ALL',
         'required_passages':[{'record_id':rid,'text':text} for rid,text in passages]}
        for name,query,passages,scope in QUERY_SPECS]
 # Read the four actually saved feedback episodes using bounded read-only SQL.
 promotion=json_read(ROOT/'local/report-quality/v1/PROMOTION.json')
 path=ROOT/'runtime/report-feedback/report-feedback.sqlite'
 items=[];reports={};replacements={}
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
  for entry in promotion['feedback']:
   feedback_id=entry['feedback_id'];row=db.execute('SELECT body FROM feedback WHERE feedback_id=?',(feedback_id,)).fetchone()
   require(row is not None,'Saved feedback episode missing')
   body=json.loads(row[0]);body['events']=[json.loads(v[0]) for v in db.execute('SELECT body FROM events WHERE feedback_id=? ORDER BY rowid',(feedback_id,))]
   items.append(body)
   rid=body['report_id'];paths=list((ROOT/'runtime/sessions').glob('*/workspace/reports/'+rid+'.json'))
   require(len(paths)==1,'Expected one actual report original: '+rid);reports[rid]=json_read(paths[0])
   for event in body['events']:
    if event['kind']=='resolve':
     replacement=event['replacement_report_id'];paths=list((ROOT/'runtime/sessions').glob('*/workspace/reports/'+replacement+'.json'))
     require(len(paths)==1,'Expected one saved replacement');replacements[replacement]=json_read(paths[0])
 return freeze_rows(output,rows,cases,notices={'reports':reports,'replacements':replacements,'items':items},
  source_scope='Sixteen actual saved publisher/forum records, not authenticated publisher truth or universal news coverage')



def build_services(root,rows,encoder=None):
 from threading import RLock
 from research_agent.knowledge import KnowledgeStore
 from research_agent.event_memory_index import EventMemoryIndex
 from research_agent.hybrid_memory import HybridMemoryRetriever
 from research_agent.evidence_memory import EvidenceMemory
 from research_agent.tools import ResearchTools
 root=Path(root);store=KnowledgeStore(root/'knowledge')
 for row in rows:store.register(row)
 index=EventMemoryIndex(root/'index',source_lookup=store.get)
 cards=[]
 for row in rows:
  index.upsert_source(row['record_id'])
  # Uniform source windows, not target-span/answer-aware card construction.
  text=row['text']
  for start in range(0,len(text),1800):
   cards.append(index.record_card(row['record_id'],start=start,end=min(start+1800,len(text))))
 retriever=HybridMemoryRetriever(index,encoder=encoder,encoder_id=getattr(encoder,'model_id',None),
  cache_fingerprint=getattr(encoder,'fingerprint',None))
 services={}
 for policy in ('lexical','hybrid'):
  service=ResearchTools.__new__(ResearchTools)
  service.knowledge=store;service.mode='observed';service.cutoff=PROTOCOL['full_cutoff']
  service.workspace=root/policy;service.workspace.mkdir(parents=True,exist_ok=True)
  service.evidence_memory=EvidenceMemory(store);service.retrieval_judgments=None
  service.report_judgments=None;service.judgment_mode='off';service.lock=RLock();service.used_records=set()
  service.library=None;service.allow_live=True;service.memory_retriever=retriever if policy=='hybrid' else None
  services[policy]=service
 return store,index,retriever,services,{'source_records':len(rows),'uniform_cards':len(cards),'card_construction':'non-overlapping1800character windows, no answer-aware anchors'}


def span_scores(case,packet):
 selected={row['record_id']:row for row in packet.get('results',[])}
 checks=[]
 for target in case.get('required_passages',[]):
  row=selected.get(target['record_id']);excerpt=(row or {}).get('excerpt') or ''
  checks.append({'record_id':target['record_id'],'start':target['start'],'end':target['end'],
    'required_span_sha256':target['sha256'],'source_selected':row is not None,
    'exact_span_in_excerpt':target['text'] in excerpt,
    'selected_slices':(row or {}).get('source_slices',[])})
 return {'all_required_sources_in_pack':bool(checks) and all(c['source_selected'] for c in checks),
  'all_required_spans_in_pack':bool(checks) and all(c['exact_span_in_excerpt'] for c in checks),'passages':checks}


def notice_diagnostics(notices):
 from research_agent.memory_notices import attach_report_notices
 result=[]
 for item in notices.get('items',[]):
  rid=item['report_id'];body=notices['reports'][rid]
  reviews=[e for e in item['events'] if e.get('kind')=='review']
  require(reviews,'Actual correction episode has no review')
  earliest=min(timestamp(e['known_at']) for e in reviews)
  last=max(timestamp(e['known_at']) for e in item['events'])
  row={'report_id':rid,'summary':body['report']['summary'],'created_at':body['created_at']}
  provider=lambda report_id,cutoff=None:[deepcopy(item)]
  lookup=lambda report_id:notices.get('replacements',{}).get(report_id)
  before=attach_report_notices(row,body,provider,cutoff=(earliest-timedelta(microseconds=1)).isoformat(),report_lookup=lookup)
  after=attach_report_notices(row,body,provider,cutoff=(last+timedelta(microseconds=1)).isoformat(),report_lookup=lookup)
  result.append({'feedback_id':item['feedback_id'],'report_id':rid,
    'label_type':'actual_saved_attributed_review_not_human_truth',
    'before_review_notices':len(before['memory_notices']),'after_review_notices':len(after['memory_notices']),
    'before_memory_status':before['memory_status'],'after_memory_status':after['memory_status'],
    'replacement_ids_after':after['next_report_ids'],
    'original_summary_preserved':before['summary']==after['summary']==row['summary'],
    'passed':not before['memory_notices'] and bool(after['memory_notices']) and before['summary']==after['summary']})
 return result


def synthetic_diagnostics():
 from unittest.mock import patch
 from research_agent.event_memory_index import EventMemoryIndex
 from research_agent.hybrid_memory import HybridMemoryRetriever
 from research_agent.memory_notices import attach_report_notices
 from research_agent.working_memory import WorkingMemory
 a='page-'+'a'*24;b='page-'+'b'*24
 def source(rid,text,known='2026-01-01T00:00:00Z',url=None):
  return {'record_id':rid,'title':'Atlas synthetic notice','text':text,'asset':'ETH',
   'published_at':'2026-01-01T00:00:00Z','retrieved_at':known,'source_kind':'page',
   'url':url or 'https://example.org/'+rid}
 def run(name,fn):
  try:return {'case_id':name,'kind':'controlled_synthetic_contract','passed':bool(fn()),'error':None}
  except Exception as exc:return {'case_id':name,'kind':'controlled_synthetic_contract','passed':False,'error':type(exc).__name__+': '+str(exc)[:300]}
 def different_network():
  rows={a:source(a,'Atlas upgrade on Ethereum.'),b:source(b,'Atlas upgrade on Base.')}
  with tempfile.TemporaryDirectory() as tmp:
   idx=EventMemoryIndex(tmp,source_lookup=lambda rid:deepcopy(rows[rid]))
   for rid in rows:idx.upsert_source(rid)
   idx.record_card(a,start=0,end=len(rows[a]['text']),entity_keys=['ethereum:atlas'])
   idx.record_card(b,start=0,end=len(rows[b]['text']),entity_keys=['base:atlas'])
   found=idx.query('',entity_key='ethereum:atlas')
   return [v['evidence_id'] for v in found['results']]==[a] and found['event_equivalence_confirmed'] is False
 def correction():
  rows={a:source(a,'Atlas activation planned for tomorrow.'),b:source(b,'Atlas activation postponed.','2026-01-02T00:00:00Z')}
  with tempfile.TemporaryDirectory() as tmp:
   idx=EventMemoryIndex(tmp,source_lookup=lambda rid:deepcopy(rows[rid]))
   with patch('research_agent.event_memory_index.utcnow',return_value='2026-01-01T01:00:00Z'):
    idx.upsert_source(a);card=idx.record_card(a,start=0,end=len(rows[a]['text']),summary='Derived planned activation.')
   with patch('research_agent.event_memory_index.utcnow',return_value='2026-01-02T01:00:00Z'):
    idx.upsert_source(b);idx.link_sources(b,a,'corrects',receipt_id=b)
   earlier=idx.read_card(card['card_id'],cutoff='2026-01-01T23:00:00Z',mode='strict')
   later=idx.read_card(card['card_id'],cutoff='2026-01-02T02:00:00Z',mode='strict')
   found=HybridMemoryRetriever(idx).query('Atlas',cutoff='2026-01-02T02:00:00Z',mode='strict',limit=2)
   return earlier['summary_status']=='derived_unverified' and later['summary_status']=='stale' and set(found['source_ids'])=={a,b}
 def later_acquired():
  rows={a:source(a,'Atlas original.','2026-01-03T00:00:00Z')}
  with tempfile.TemporaryDirectory() as tmp:
   idx=EventMemoryIndex(tmp,source_lookup=lambda rid:deepcopy(rows[rid]));idx.upsert_source(a)
   strict=idx.eligible_sources(cutoff='2026-01-02T00:00:00Z',mode='strict')
   observed=idx.eligible_sources(cutoff='2026-01-02T00:00:00Z',mode='observed')
   return not strict['sources'] and len(observed['sources'])==1
 def future_notice():
  body={'created_at':'2026-01-01T00:00:00Z','report':{'summary':'Atlas planned','observations':[{'evidence_ids':[a]}]}}
  item={'feedback_id':'feedback-synthetic','report_id':'report-synthetic','report_sha256':sha(body),'known_at':'2026-01-02T00:00:00Z',
    'claim_id':'S1','events':[{'kind':'review','known_at':'2026-01-03T00:00:00Z','assessment':'needs_correction','reviewer_kind':'programmatic','evidence_ids':[b],'correction':'Check the updated source.'}]}
  row={'report_id':'report-synthetic','summary':'Atlas planned'}
  before=attach_report_notices(row,body,lambda *a,**k:[item],cutoff='2026-01-02T12:00:00Z')
  after=attach_report_notices(row,body,lambda *a,**k:[item],cutoff='2026-01-04T00:00:00Z')
  return not before['memory_notices'] and len(after['memory_notices'])==1
 def duplicate():
  rows={a:source(a,'Atlas unchanged exact source.')}
  with tempfile.TemporaryDirectory() as tmp:
   idx=EventMemoryIndex(tmp,source_lookup=lambda rid:deepcopy(rows[rid]));idx.upsert_source(a)
   one=idx.record_card(a,start=0,end=10);two=idx.record_card(a,start=0,end=10)
   return one['card_id']==two['card_id'] and two['duplicate'] and idx.query('')['matching_card_count']==1
 def damaged():
  rows={a:source(a,'Atlas immutable source.')}
  with tempfile.TemporaryDirectory() as tmp:
   idx=EventMemoryIndex(tmp,source_lookup=lambda rid:deepcopy(rows[rid]));idx.upsert_source(a)
   card=idx.record_card(a,start=0,end=10);rows[a]['text']='Changed evidence under same ID.'
   try:idx.read_card(card['card_id'])
   except ValueError:return True
   return False
 def fake_review():
  with tempfile.TemporaryDirectory() as tmp:
   state=WorkingMemory(tmp);state.begin('synthetic','Contract test')
   state.observe_tool('synthetic','execute',{'status':'saved','report_path':'fake','review':{'status':'reviewed'}})
   research=state.state('synthetic')['research_state']
   return not research['checks'] and not research['publications']
 def recovery():
  with tempfile.TemporaryDirectory() as tmp:
   state=WorkingMemory(tmp);state.begin('synthetic','Contract test')
   state.observe_tool('synthetic','request_public_api',{'error':'Historical access unavailable'},status='error',tool_call_id='failed')
   payload={'record':source(a,'Atlas captured original.')}
   state.observe_tool('synthetic','read_evidence',payload,tool_call_id='fixed')
   again=WorkingMemory(tmp).observe_tool('synthetic','read_evidence',payload,tool_call_id='fixed')
   research=state.state('synthetic')['research_state']
   return again['duplicate'] and len(research['observations'])==2 and len(research['unknowns'])==1 and len(research['sources'])==1
 return [run(name,fn) for name,fn in [('same_entity_different_network',different_network),('original_then_correction',correction),
  ('later_acquired_source',later_acquired),('future_correction_notice',future_notice),('duplicate_card',duplicate),
  ('damaged_span',damaged),('console_fake_review',fake_review),('failed_tool_recovery',recovery)]]


def implementation_hashes():
 files=['scripts/evaluation/evaluate_memory_v2.py',*['src/research_agent/'+name+'.py' for name in
   ('knowledge','memory_notices','evidence_memory','memory_selection','event_memory_index','hybrid_memory','local_memory_encoder','tools','working_memory','research_state')]]
 return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files if (ROOT/name).is_file()}


def evaluate(output,*,encoder=None,run_name=None,encoder_error=None):
 manifest,rows,queries,protocol,notices=load_frozen(output);output=Path(output)
 implementation_before=implementation_hashes()
 run_name=run_name or ('local-encoder' if encoder is not None else 'dense-not-available')
 require(run_name.replace('-','').replace('_','').isalnum(),'Use a simple run name')
 destination=output/'runs'/run_name;require(not destination.exists(),'Preserve prior diagnostic attempts; choose a new run name')
 destination.mkdir(parents=True)
 results=[];temporal=[];cards=[]
 with tempfile.TemporaryDirectory(prefix='aether-memory-v2-diagnostic-') as temporary:
  store,index,retriever,services,build=build_services(temporary,rows,encoder)
  for case_index,case in enumerate(queries):
   for policy in (('lexical','hybrid') if case_index%2==0 else ('hybrid','lexical')):
    service=services[policy];started=perf_counter()
    try:
     packet=service.news_pack(query=case['query'],asset='ALL',cutoff=protocol['full_cutoff'],limit=protocol['result_limit'],
       budget=protocol['context_budget'],related_queries=[])
     elapsed=perf_counter()-started
     ranks=store.candidate_news(case['query'],'ALL',protocol['full_cutoff'],mode='observed')['candidates'] if policy=='lexical' else retriever.query(case['query'],asset='ALL',cutoff=protocol['full_cutoff'],limit=30)['results']
     results.append({'case_id':case['case_id'],'policy':policy,'status':'completed','label_scope':case['label_scope'],
      'latency_seconds':elapsed,'source_ids':packet.get('source_ids',[]),
      'candidate_ranks':[{'rank':n+1,'record_id':row['record_id'],'ranking':row.get('ranking')} for n,row in enumerate(ranks)],
      **span_scores(case,packet),'selection':packet['selection'],'hybrid_retrieval':packet.get('hybrid_retrieval'),
      'gaps':packet.get('gaps',[]),'context_utf8_bytes':len(canonical(packet.get('results',[])).encode()),
      'context_cost_scope':'Serialized bytes and existing UTF8/3 heuristic, not actual provider tokens',
      'packet':packet})
    except Exception as exc:
     results.append({'case_id':case['case_id'],'policy':policy,'status':'error','label_scope':case['label_scope'],
      'latency_seconds':perf_counter()-started,'source_ids':[],'candidate_ranks':[],
      'all_required_sources_in_pack':False,'all_required_spans_in_pack':False,
      'error':type(exc).__name__+': '+str(exc)[:600]})
   card_packet=index.query(case['query'],cutoff=protocol['full_cutoff'],mode='observed',asset='ALL',limit=4)
   cards.append({'case_id':case['case_id'],'mode':'observed_retrospective_derived_navigation',
    'card_ids':[r['card_id'] for r in card_packet['results']],
    'spans':[{'evidence_id':r['evidence_id'],'start':r['start'],'end':r['end'],'span_sha256':r['span_sha256'],
      'known_at':r['known_at'],'summary_status':r['summary_status']} for r in card_packet['results']],
    'event_equivalence_confirmed':card_packet['event_equivalence_confirmed']})
  # Target acquisition probes test source-time filtering, not answer accuracy.
  for case in [queries[i] for i in (0,2,3,6) if i<len(queries)]:
   target=case['required_passages'][0]['record_id'];row=next(r for r in rows if r['record_id']==target)
   cutoff=(timestamp(row.get('available_at') or row.get('retrieved_at'))-timedelta(microseconds=1)).isoformat()
   for policy,service in services.items():
    service.mode='strict';service.cutoff=cutoff
    try:
     packet=service.news_pack(query=case['query'],asset='ALL',cutoff=cutoff,limit=4,budget=protocol['context_budget'],related_queries=[])
     temporal.append({'case_id':case['case_id'],'policy':policy,'cutoff':cutoff,'target_id':target,
       'selected_ids':packet.get('source_ids',[]),'target_excluded':target not in packet.get('source_ids',[]),'status':'completed'})
    except Exception as exc:temporal.append({'case_id':case['case_id'],'policy':policy,'cutoff':cutoff,'target_id':target,'target_excluded':False,'status':'error','error':type(exc).__name__})
  # Cards created today are not pretended to have existed at the old source cutoff.
  strict_cards=index.query('',cutoff=protocol['full_cutoff'],mode='strict',limit=30)
  derived_strict={'cutoff':protocol['full_cutoff'],'cards_built_at_actual_run_time':True,
    'strict_card_count':len(strict_cards['results']),'scope':'A zero card count before construction is a correct gap, not evidence that old originals were unavailable.'}
 synthetic=synthetic_diagnostics();notice_rows=notice_diagnostics(notices)
 summaries={}
 for policy in ('lexical','hybrid'):
  selected=[r for r in results if r['policy']==policy]
  summaries[policy]={'query_denominator':len(queries),'completed':sum(r['status']=='completed' for r in selected),
   'errors':sum(r['status']=='error' for r in selected),'all_required_sources':sum(r['all_required_sources_in_pack'] for r in selected),
   'all_required_spans':sum(r['all_required_spans_in_pack'] for r in selected),
   'empty_or_error':sum(not r['source_ids'] for r in selected),
   'latency_seconds_sum':sum(r['latency_seconds'] for r in selected)}
 external=output/'acceptance_status.json'
 if not external.exists():external.write_text(json.dumps({'new_live_pro':{'status':'pending'},'independent_review':{'status':'pending'},'human_review':{'status':'not_performed','labels':0}},indent=2)+'\n')
 dense_statuses={}
 for item in results:
  if item['policy']=='hybrid':
   key=(item.get('hybrid_retrieval') or {}).get('dense_status','not_reported')
   dense_statuses[key]=dense_statuses.get(key,0)+1
 implementation_after=implementation_hashes()
 receipt={'schema':SCHEMA,'status':'diagnostic_complete','model_provider_calls':0,'network_requests':0,
  'frozen_manifest_sha256':hashlib.sha256((output/'MANIFEST.json').read_bytes()).hexdigest(),
  'encoder_status':'local_pretrained_used' if dense_statuses.get('ok',0) else 'dense_unavailable' if encoder is not None else 'dense_not_available',
  'dense_statuses':dense_statuses,'encoder_initialization_error':encoder_error,
  'encoder_id':getattr(encoder,'model_id',None),'encoder_fingerprint':getattr(encoder,'fingerprint',None),
  'denominators':{'source_records':len(rows),'real_queries':len(queries),'real_query_policy_outputs':len(results),
   'real_time_policy_probes':len(temporal),'actual_saved_feedback_episodes':len(notice_rows),'synthetic_contracts':len(synthetic)},
  'policy_results':summaries,'synthetic_passed':sum(r['passed'] for r in synthetic),
  'notice_episodes_passed':sum(r['passed'] for r in notice_rows),'strict_source_probes_passed':sum(r['target_excluded'] for r in temporal),
  'build':build,'derived_card_clock_probe':derived_strict,'external_acceptance':json_read(external),
  'implementation_sha256':implementation_before,'implementation_unchanged_during_run':implementation_before==implementation_after,
  'human_ground_truth':False,'heldout_accuracy_claimed':False,'semantic_answer_accuracy_measured':False,
  'scope':'AI-authored exact-span retrieval/navigation diagnostics. No generated report quality, independent human labels, investment outcomes or general semantic improvement claim.'}
 files={'per_query.json':results,'temporal.json':temporal,'event_cards.json':cards,'synthetic.json':synthetic,'actual_notices.json':notice_rows,'receipt.json':receipt}
 for name,value in files.items():(destination/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
 receipt['artifacts']={name:hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in files if name!='receipt.json'}
 (destination/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
 load_frozen(output)
 return receipt

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
 p.add_argument('--library',default='data/library-halfyear-20260426-20261026')
 p.add_argument('--freeze',action='store_true')
 p.add_argument('--encoder-path',type=Path)
 p.add_argument('--run-name')
 args=p.parse_args()
 if args.freeze:
  manifest=freeze_real(args.output,args.library)
  print(json.dumps({'status':'frozen_before_policy_evaluation','sources':manifest['source_count'],'queries':manifest['query_count']}));return
 encoder=None;encoder_error=None
 if args.encoder_path:
  try:
   from research_agent.local_memory_encoder import LocalMemoryEncoder
   encoder=LocalMemoryEncoder(args.encoder_path)
  except (ValueError,ImportError,OSError) as exc:
   encoder_error={'reason':'local_encoder_not_available','error_type':type(exc).__name__,'detail':str(exc)[:300]}
 receipt=evaluate(args.output,encoder=encoder,run_name=args.run_name,encoder_error=encoder_error)
 print(json.dumps({k:receipt[k] for k in ['status','encoder_status','denominators','policy_results','synthetic_passed','notice_episodes_passed','strict_source_probes_passed']},ensure_ascii=False))

if __name__=='__main__':main()
