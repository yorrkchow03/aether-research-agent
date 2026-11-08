"""Offline real agent-loop checks for isolated public source reading."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock,patch

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
from pydantic import Field
import httpx
from openai import APIConnectionError
from langchain_openai.chat_models.base import OpenAITimeoutError

from research_agent.evidence_reader import build_evidence_reader,validate_reader_result,_ReaderBudget,ReaderResult,ReaderAccessError,write_reader_artifacts,_compact_reader_output
from research_agent.tools import ResearchTools


RID='page-'+'a'*24
OTHER='page-'+'b'*24
QUOTE='The synthetic activation remains unconfirmed.'


class ReaderModel(FakeMessagesListChatModel):
    http_client:Any=None
    calls:list=Field(default_factory=list)
    bound_tool_names:list=Field(default_factory=list)
    def bind_tools(self,tools,**kwargs):
        self.bound_tool_names=[getattr(t,'name',None) or
            (t.get('function',{}).get('name') or t.get('name')) if isinstance(t,dict)
            else getattr(t,'name',getattr(t,'__name__',None)) for t in tools]
        return self
    def _generate(self,messages,*args,**kwargs):
        self.calls.append(deepcopy(messages))
        return super()._generate(messages,*args,**kwargs)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.rows={RID:{'record_id':RID,'text':'SOURCE-BODY-ONLY-MARKER. '+QUOTE,
            'title':'Synthetic Ethereum announcement','source_kind':'page',
            'published_at':'2026-10-02T00:00:00Z','url':'https://synthetic.invalid/announcement'},
            OTHER:{'record_id':OTHER,'text':'Another synthetic source.','source_kind':'page',
            'published_at':'2026-10-03T00:00:00Z','url':'https://synthetic.invalid/other'}}
        self.reads=[];self.searches=[];self.events=[]
        def lookup(rid):self.reads.append(rid);return deepcopy(self.rows[rid])
        def search(**kwargs):
            self.searches.append(kwargs)
            return {'results':[{'record_id':RID,'excerpt':'SYNTHETIC search navigation.'}]}
        self.service=SimpleNamespace(lookup=lookup,news_pack=search,
            _public_judgment_row=ResearchTools._public_judgment_row,
            _public_semantic_question=ResearchTools._public_semantic_question)

    def output(self,quote=QUOTE,rid=RID):
        return {'findings':[{'finding':'SYNTHETIC status remains qualified.',
            'citations':[{'evidence_id':rid,'quote':quote}]}],'conflicts':[],'gaps':[]}

    def read_response(self,rid=RID,query=''):
        return AIMessage(content='',tool_calls=[{'name':'read_evidence','args':{'record_id':rid,'query':query},'id':'read'}])

    def typed_response(self,payload):
        return AIMessage(content='',tool_calls=[{'name':'ReaderResult','args':payload,'id':'reader-result'}],
            usage_metadata={'input_tokens':120,'output_tokens':35,'total_tokens':155},
            response_metadata={'model_name':'doubao-seed-2.1-pro','finish_reason':'tool_calls'})

    def invoke(self,responses,*,cancel=None,question='Public Ethereum weekly review 2026-10-01 through 2026-10-08.',redact=str):
        model=ReaderModel(responses=responses)
        worker=build_evidence_reader(model,self.service,self.root,
            emit=lambda kind,data:self.events.append((kind,data)),cancel=cancel,redact=redact)
        result=worker['runnable'].invoke({'messages':[HumanMessage(content=question)]})
        result=json.loads(result['messages'][-1].content)
        receipts=list((self.root/'memory/readers').glob('*/trace.json'))
        return result,model,json.loads(receipts[-1].read_text()) if receipts else None

    def test_exact_quote_reopens_saved_public_original_and_binds_offsets_hash_and_clocks(self):
        result,model,receipt=self.invoke([self.read_response(),self.typed_response(self.output())])
        self.assertEqual(result['status'],'completed',result)
        citation=result['findings'][0]['citations'][0]
        self.assertEqual(citation['sha256'],hashlib.sha256(QUOTE.encode()).hexdigest())
        self.assertEqual(self.rows[RID]['text'][citation['start']:citation['end']],QUOTE)
        self.assertEqual(citation['published_at'],self.rows[RID]['published_at'])
        self.assertEqual(receipt['read_ids'],[RID])
        self.assertEqual(receipt['model_calls'],2)
        self.assertEqual(receipt['tool_calls'],1)
        self.assertIn('SOURCE-BODY-ONLY-MARKER',json.dumps(receipt))
        self.assertIn('ReaderResult',model.bound_tool_names)
        self.assertTrue(any(message['type']=='ai' and message['data'].get('tool_calls',[{}])[0].get('name')=='ReaderResult'
            for message in receipt['messages'] if message['data'].get('tool_calls')))
        finished=[data for kind,data in self.events if kind=='reader_model_finished']
        self.assertEqual(finished[-1]['usage'],{'input_tokens':120,'output_tokens':35,'total_tokens':155})
        self.assertEqual(finished[-1]['actual_model'],'doubao-seed-2.1-pro')

    def test_opened_catalog_and_compact_packet_from_real_worker(self):
        result,_,_=self.invoke([self.read_response(),self.typed_response(self.output())])
        full=json.loads((self.root/result['artifact'].lstrip('/')).read_text())
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),6000)
        self.assertEqual(result['opened_source_ids_preview'],[RID])
        self.assertEqual(full['opened_source_catalog'][0]['record_id'],RID)
        self.assertEqual(full['opened_source_catalog'][0]['source_status'],'opened')
        self.assertEqual(full['opened_source_catalog'][0]['relevance'],'not validated')
        self.assertIn('not semantic support',full['review_scope'])
        self.assertIn('without source-quote binding',full['review_scope'])
        self.assertEqual(full['findings'],result['findings'])

    def test_r5_saved_receipt_offline_catalog_replay_and_native_read(self):
        source=Path(__file__).resolve().parents[1]/'local/long-study/20261108/round5/arms/doubao-seed-2.1-pro/runtime/sessions/46fe85d06e074c29b732a3ee7e37cb67/workspace/memory/readers/727d5068d35a4a3c8e5fc333dee31c94/trace.json'
        if not source.is_file():self.skipTest('Optional saved R5 trace unavailable')
        raw=source.read_bytes();old_hash=hashlib.sha256(raw).hexdigest();receipt=json.loads(raw)
        original=deepcopy(receipt['full_validated_result']['findings'])
        full=write_reader_artifacts(self.root,'offline-replay',receipt)
        self.assertEqual(len(full['opened_source_catalog']),8)
        self.assertEqual(full['findings'],original)
        self.assertEqual(len(original),3)
        packet=deepcopy(receipt['full_validated_result'])
        packet.update(opened_source_count=8,opened_source_ids_preview=sorted(receipt['read_ids'])[:3],
            opened_source_catalog={'file_path':'/memory/readers/offline-replay/validated-result.json',
                'json_pointer':'/opened_source_catalog','scope':'Opened-source metadata only; relevance is not validated.'},
            review_scope='Finding quotes are identity-checked, not semantically reviewed. Conflicts and gaps are unbound model notes; verify originals.')
        _compact_reader_output(packet)
        packet.update(opened_source_count=8,validated_finding_count=3,
                      omitted_finding_count=packet.get('omitted_findings',0))
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),6000)
        self.assertTrue(packet['findings'])
        self.assertEqual(packet['findings'],original[:len(packet['findings'])])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),old_hash)
        navigation=json.loads((self.root/'memory/readers/offline-replay/navigation.json').read_text())
        for row in full['opened_source_catalog']:
            self.assertEqual(row['ranges']['file_path'],'/memory/readers/offline-replay/navigation.json')
            self.assertEqual(navigation['opened_sources'][row['record_id']]['source_slices'],
                             receipt['opened_sources'][row['record_id']]['source_slices'])
        from deepagents.middleware.filesystem import FilesystemMiddleware
        from test_planning import ScriptedModel
        backend=FilesystemBackend(root_dir=self.root,virtual_mode=True)
        model=ScriptedModel(responses=[AIMessage(content='',tool_calls=[{'id':'open','name':'read_file',
            'args':{'file_path':'/memory/readers/offline-replay/validated-result.json'}}]),AIMessage(content='Done.')])
        graph=create_deep_agent(model,backend=backend,middleware=[FilesystemMiddleware(backend=backend,tool_token_limit_before_evict=100000)])
        state=graph.invoke({'messages':[HumanMessage(content='Read the catalog.')]})
        opened=next(m for m in state['messages'] if isinstance(m,ToolMessage) and m.tool_call_id=='open')
        self.assertEqual(opened.status,'success')
        self.assertIn('opened_source_catalog',opened.content)
        self.assertIn(full['opened_source_catalog'][0]['record_id'],opened.content)

    def test_unopened_source_or_invented_quote_cannot_be_returned_as_completed_finding(self):
        with self.assertRaisesRegex(ValueError,'not opened'):
            validate_reader_result(self.output(),self.service,set())
        with self.assertRaisesRegex(ValueError,'match'):
            validate_reader_result(self.output(quote='SYNTHETIC nonexistent quote.'),self.service,{RID})
        result,_,_=self.invoke([self.read_response(OTHER),self.typed_response(self.output())])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['findings'],[])

    def test_invalid_query_full_text_is_returned_to_worker_then_repaired(self):
        bad=AIMessage(content='',tool_calls=[{'name':'read_evidence','args':{
            'record_id':RID,'query':'activation','full_text':True},'id':'bad-read'}])
        result,model,receipt=self.invoke([bad,self.read_response(),self.typed_response(self.output())])
        self.assertEqual(result['status'],'completed',result)
        errors=[m for m in model.calls[1] if isinstance(m,ToolMessage) and m.status=='error']
        self.assertEqual(errors[0].tool_call_id,'bad-read')
        self.assertIn('Choose a focused query',errors[0].content)
        self.assertEqual(receipt['tool_calls'],2)
        self.assertEqual(receipt['read_ids'],[RID])

    def test_failed_parameter_combination_does_not_authorize_citation(self):
        bad=AIMessage(content='',tool_calls=[{'name':'read_evidence','args':{
            'record_id':RID,'query':'activation','full_text':True},'id':'bad-read'}])
        result,_,receipt=self.invoke([bad,self.typed_response(self.output())])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(receipt['read_ids'],[])

    def test_pending_source_reads_reuses_one_result_repair_then_performs_real_read(self):
        pending=AIMessage(content='{"findings":[],"conflicts":[],"gaps":["Pending source reads"]}')
        result,model,receipt=self.invoke([pending,self.read_response(),self.typed_response(self.output())])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(receipt['model_calls'],3)
        self.assertEqual(receipt['tool_calls'],1)
        self.assertEqual(receipt['read_ids'],[RID])
        repairs=[row for row in receipt['intermediate_records'] if row['kind']=='result_repair']
        self.assertEqual(len(repairs),1)
        self.assertIn('No source inspection',repairs[0]['error'])
        finished=next(data for kind,data in self.events if kind=='reader_finished')
        self.assertEqual(finished['opened_source_count'],1)
        self.assertEqual(finished['validated_finding_count'],1)
        self.assertEqual(finished['omitted_finding_count'],0)
        self.assertEqual(result['opened_source_count'],1)
        self.assertEqual(result['validated_finding_count'],1)

    def test_repeated_empty_pending_result_is_incomplete_without_fake_findings(self):
        result,model,receipt=self.invoke([self.typed_response({'findings':[],
            'gaps':['Pending source reads']})])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['findings'],[])
        self.assertTrue(result['artifact'].endswith('/navigation.json'))
        self.assertEqual(receipt['model_calls'],2)
        self.assertEqual(receipt['tool_calls'],0)
        self.assertEqual(len([r for r in receipt['intermediate_records'] if r['kind']=='result_repair']),1)
        finished=next(data for kind,data in self.events if kind=='reader_finished')
        self.assertEqual(finished['opened_source_count'],0)
        self.assertEqual(finished['validated_finding_count'],0)

    def test_failed_empty_recall_is_incomplete_not_no_evidence(self):
        self.service.news_pack=lambda **_: {'status':'error','results':[],'error':'Synthetic local source unavailable'}
        recall=AIMessage(content='',tool_calls=[{'name':'recall_memory','args':{'query':'Ethereum status'},'id':'failed-recall'}])
        result,_,receipt=self.invoke([recall,self.typed_response({'findings':[],
            'gaps':['Synthetic local source unavailable.']})])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['findings'],[])
        self.assertEqual(result['opened_source_count'],0)
        self.assertEqual(result['validated_finding_count'],0)
        self.assertEqual(receipt['tool_calls'],1)
        self.assertEqual(receipt['read_ids'],[])

    def test_display_overflow_projects_locally_without_another_model_call(self):
        oversized=self.output()
        oversized['findings'][0]['citations']*=4
        oversized['conflicts']=['long '*100]
        result,model,receipt=self.invoke([self.read_response(),AIMessage(content=json.dumps(oversized)),
            self.typed_response(self.output())])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(receipt['model_calls'],2)
        self.assertEqual(receipt['tool_calls'],1)
        self.assertFalse(any(r['kind']=='result_repair' for r in receipt['intermediate_records']))
        self.assertIn('/findings/0/citations/3',result['details_in_artifact'])
        self.assertIn('/conflicts/0',result['details_in_artifact'])
        self.assertIsNotNone(receipt['full_validated_result'])

    def test_bad_finding_is_excluded_without_losing_other_exact_findings(self):
        payload=self.output();payload['findings'].append({'finding':'Unsupported extra finding',
            'citations':[{'evidence_id':RID,'quote':'Invented source quotation.'}]})
        result,model,receipt=self.invoke([self.read_response(),AIMessage(content=json.dumps(payload))])
        self.assertEqual(result['status'],'partial')
        self.assertEqual(len(result['findings']),1)
        self.assertEqual(result['rejected_findings'][0]['path'],'/findings/1')
        self.assertEqual(receipt['model_calls'],2)

    def test_native_typed_cardinality_overflow_uses_same_local_projection(self):
        payload=self.output();payload['findings'][0]['citations']*=4
        result,model,receipt=self.invoke([self.read_response(),self.typed_response(payload)])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(receipt['model_calls'],2)
        self.assertIn('/findings/0/citations/3',result['details_in_artifact'])
        self.assertEqual(receipt['unknown_usage_attempts'],0)
        self.assertFalse(any(kind=='reader_model_failed' for kind,_ in self.events))
        self.assertEqual([data for kind,data in self.events if kind=='reader_model_finished'][-1]['usage'],
            {'input_tokens':120,'output_tokens':35,'total_tokens':155})

    def test_private_sources_are_rejected_even_if_model_claims_to_have_opened_them(self):
        for fields in ({'private':True},{'dataset_id':'local.portfolio.positions'},{'profile':{'budget':1}}):
            with self.subTest(fields=fields):
                self.rows[RID].update(fields)
                with self.assertRaisesRegex(ValueError,'private'):
                    validate_reader_result(self.output(),self.service,{RID})
                for key in fields:self.rows[RID].pop(key)

    def test_quote_must_be_in_actual_returned_slice_and_search_excerpt_cannot_be_original(self):
        with self.assertRaisesRegex(ValueError,'outside'):
            validate_reader_result(self.output(),self.service,{RID:['SOURCE-BODY-ONLY-MARKER.']})
        result=validate_reader_result(self.output(),self.service,{RID:[QUOTE]})
        self.assertEqual(result['source_ids'],[RID])
        self.rows[RID]['source_kind']='search_excerpt'
        with self.assertRaisesRegex(ValueError,'original'):
            validate_reader_result(self.output(),self.service,{RID:[QUOTE]})

    def test_repeated_quote_offsets_bind_to_opened_occurrence_not_first_full_text_match(self):
        text='Older synthetic section: '+QUOTE+'\nLater synthetic section: '+QUOTE
        self.rows[RID]['text']=text
        start=text.index('Later synthetic section:')
        opened={'start':start,'end':len(text),'text':text[start:]}
        result=validate_reader_result(self.output(),self.service,{RID:[opened]})
        citation=result['findings'][0]['citations'][0]
        self.assertGreater(citation['start'],text.index(QUOTE))
        self.assertEqual(citation['start'],text.rindex(QUOTE))
        self.assertEqual(text[citation['start']:citation['end']],QUOTE)
        with self.assertRaisesRegex(ValueError,'no longer matches'):
            validate_reader_result(self.output(),self.service,{RID:[{**opened,'start':0}]})

    def test_private_delegation_is_rejected_before_model_or_source_access(self):
        for question in ('My portfolio contains 5 ETH; assess my personal risk tolerance.',
                         '我的持仓有5 ETH，请结合我的风险偏好研究。',
                         'I currently hold 124.73 ETH; compare my cost basis.',
                         '我目前持有124.73 ETH，请研究一下。'):
            with self.subTest(question=question):
                result,model,receipt=self.invoke([AIMessage(content='must not execute')],question=question)
                self.assertEqual(result['status'],'incomplete')
                self.assertEqual(model.calls,[])
                self.assertEqual(self.reads,[])
                self.assertIsNone(receipt)

    def test_public_delegation_allows_disclosed_amounts_and_negated_privacy_prefix(self):
        question=('PUBLIC SOURCE QUESTION (no personal/account/portfolio data involved). '
            'Read the saved public Ethereum forum report about 124.73 ETH in protocol rewards, '
            'compare the public treasury portfolio discussion, and retain original qualifications. '
            'No private account details are supplied. Open '+RID+'.')
        recall=AIMessage(content='',tool_calls=[{'name':'recall_memory',
            'args':{'query':'Public Ethereum forum report 124.73 ETH treasury portfolio rewards',
                    'start':'2026-10-01T00:00:00Z','end':'2026-10-08T00:00:00Z'},'id':'public-recall'}])
        with patch.object(self.service,'_public_semantic_question',side_effect=AssertionError('Jev guard is not the same-model reader policy')):
            result,model,receipt=self.invoke([recall,self.read_response(),self.typed_response(self.output())],question=question)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(model.calls),3)
        self.assertEqual(receipt['read_ids'],[RID])
        self.assertEqual(len(self.searches),1)
        self.assertIn('124.73 ETH',self.searches[0]['query'])
        citation=result['findings'][0]['citations'][0]
        self.assertEqual(self.rows[RID]['text'][citation['start']:citation['end']],QUOTE)

    def test_secret_key_delegation_is_rejected_before_model_call_or_artifact_write(self):
        for question in ('My API key is SYNTHETIC-NEVER-A-REAL-CREDENTIAL; read Ethereum news.',
                         'Read Ethereum sources with authorization Bearer SYNTHETIC.',
                         'Read Ethereum sources using 密钥 SYNTHETIC.'):
            with self.subTest(question=question):
                result,model,receipt=self.invoke([self.typed_response({'findings':[]})],question=question)
                self.assertEqual(result['status'],'incomplete')
                self.assertEqual(model.calls,[])
                self.assertIsNone(receipt)
        literal='SYNTHETIC-CONFIGURED-SECRET'
        result,model,receipt=self.invoke([self.typed_response({'findings':[]})],
            question='Read Ethereum announcements '+literal,redact=lambda text:str(text).replace(literal,'[REDACTED]'))
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(model.calls,[])
        self.assertIsNone(receipt)

    def test_failed_source_view_does_not_authorize_a_later_citation(self):
        with patch('research_agent.source_views.focused_source_view',side_effect=ValueError('SYNTHETIC source view failure')):
            result,model,receipt=self.invoke([self.read_response(),self.typed_response(self.output())])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(receipt['read_ids'],[])

    def test_cancellation_after_source_read_preserves_actual_intermediate_records(self):
        cancel=Event()
        def lookup(rid):cancel.set();return deepcopy(self.rows[rid])
        self.service.lookup=lookup
        result,model,receipt=self.invoke([self.read_response(),AIMessage(content='must not execute')],cancel=cancel)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(len(model.calls),1)
        self.assertEqual(receipt['read_ids'],[RID])
        self.assertTrue(any(record['kind']=='tool_result' for record in receipt['intermediate_records']))

    def test_search_preserves_explicit_window_and_never_forwards_private_query(self):
        call=AIMessage(content='',tool_calls=[{'name':'recall_memory','args':{'query':'Ethereum protocol status',
            'start':'2026-10-01T00:00:00Z','end':'2026-10-08T00:00:00Z','max_results':32,
            'kind':'news','asset':'ETH','original_only':True,'as_of':'2026-10-08T00:00:00Z'},'id':'recall'}])
        result,model,_=self.invoke([call,self.typed_response({'findings':[],'gaps':['SYNTHETIC insufficient evidence']})])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(self.searches[0]['start'],'2026-10-01T00:00:00Z')
        self.assertEqual(self.searches[0]['end'],'2026-10-08T00:00:00Z')
        self.assertEqual(self.searches[0]['limit'],32)
        self.assertTrue(self.searches[0]['material_only'])
        self.assertTrue(self.searches[0]['original_only'])
        self.assertEqual(self.searches[0]['cutoff'],'2026-10-08T00:00:00Z')
        before=len(self.searches)
        private=call.model_copy(deep=True)
        private.tool_calls[0]['args']['query']='My portfolio contains 5 ETH'
        result,model,_=self.invoke([private,self.typed_response({'findings':[],'gaps':['Private query rejected.']})])
        self.assertEqual(len(self.searches),before)
        self.assertEqual(result['status'],'incomplete')

    def test_reader_generated_search_cannot_forward_secret_patterns_or_configured_literals(self):
        literal='SYNTHETIC-CONFIGURED-SECRET'
        for query in ('Ethereum API key is SYNTHETIC-NONCREDENTIAL','Ethereum '+literal):
            with self.subTest(query=query):
                call=AIMessage(content='',tool_calls=[{'name':'recall_memory','args':{'query':query},'id':'recall-secret'}])
                result,_,_=self.invoke([call,self.typed_response({'findings':[]})],
                    redact=lambda text:str(text).replace(literal,'[REDACTED]'))
                self.assertEqual(result['status'],'incomplete')
        self.assertEqual(self.searches,[])

    def test_pre_cancelled_reader_makes_no_model_or_source_calls(self):
        cancel=Event();cancel.set()
        result,model,receipt=self.invoke([AIMessage(content='must not execute')],cancel=cancel)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(model.calls,[])
        self.assertEqual(self.reads,[])
        self.assertEqual(receipt['model_calls'],0)

    def test_native_deepagents_task_isolates_parent_history_and_worker_source_body(self):
        reader=ReaderModel(responses=[self.read_response(),self.typed_response(self.output())])
        spec=build_evidence_reader(reader,self.service,self.root)
        lead=ReaderModel(responses=[AIMessage(content='',tool_calls=[{
            'name':'task','args':{'subagent_type':'evidence-reader',
            'description':'Public ETH review 2026-10-01 through 2026-10-08. Read '+RID},'id':'delegate'}]),
            AIMessage(content='Synthetic report uses source-bound worker findings.')])
        graph=create_deep_agent(lead,system_prompt='PARENT-PRIVATE-SYSTEM-MARKER',
            backend=FilesystemBackend(root_dir=self.root,virtual_mode=True),subagents=[spec])
        result=graph.invoke({'messages':[HumanMessage(content='PARENT-PRIVATE-HISTORY-MARKER')]},config={'recursion_limit':12})
        self.assertEqual(result['messages'][-1].content,'Synthetic report uses source-bound worker findings.')
        self.assertEqual(len(reader.calls),2)
        child_context=str(reader.calls)
        self.assertNotIn('PARENT-PRIVATE-SYSTEM-MARKER',child_context)
        self.assertNotIn('PARENT-PRIVATE-HISTORY-MARKER',child_context)
        self.assertNotIn('SOURCE-BODY-ONLY-MARKER',str(lead.calls[-1]))
        self.assertIn(QUOTE,str(lead.calls[-1]))
        self.assertTrue(any(isinstance(m,ToolMessage) and '"status": "completed"' in str(m.content)
            for m in result['messages']))

    def test_native_parent_reads_complete_validated_findings_without_loading_debug_trace(self):
        identifier='c'*32
        payload=self.output();payload['findings']=[{
            'finding':str(i)+': '+'Qualified source-backed interpretation. '*12,
            'citations':[{'evidence_id':RID,'quote':QUOTE} for _ in range(3)]} for i in range(6)]
        reader=ReaderModel(responses=[self.read_response(),self.typed_response(payload)])
        spec=build_evidence_reader(reader,self.service,self.root)
        result_path='/memory/readers/'+identifier+'/validated-result.json'
        lead=ReaderModel(responses=[AIMessage(content='',tool_calls=[{
            'name':'task','args':{'subagent_type':'evidence-reader',
            'description':'Public ETH review; read '+RID},'id':'delegate'}]),
            AIMessage(content='',tool_calls=[{'name':'read_file',
                'args':{'file_path':result_path,'limit':1000},'id':'read-result'}]),
            AIMessage(content='SYNTHETIC complete bounded result read.')])
        graph=create_deep_agent(lead,backend=FilesystemBackend(root_dir=self.root,virtual_mode=True),subagents=[spec])
        with patch('research_agent.evidence_reader.uuid4',return_value=SimpleNamespace(hex=identifier)):
            result=graph.invoke({'messages':[HumanMessage(content='Public Ethereum report')]},config={'recursion_limit':14})
        returned=json.loads(next(m.content for m in result['messages'] if isinstance(m,ToolMessage) and m.name=='task'))
        self.assertEqual(returned['artifact'],result_path)
        self.assertEqual(returned['next_read']['file_path'],result_path)
        self.assertGreater(returned['omitted_findings'],0)
        packet=json.loads((self.root/result_path.lstrip('/')).read_text())
        self.assertEqual(len(packet['findings']),6)
        for finding in packet['findings']:
            for quote in finding['citations']:
                self.assertEqual(self.rows[RID]['text'][quote['start']:quote['end']],QUOTE)
                self.assertEqual(quote['sha256'],hashlib.sha256(QUOTE.encode()).hexdigest())
        self.assertNotIn('SOURCE-BODY-ONLY-MARKER',str(lead.calls[-1]))
        self.assertNotIn('model_requests',str(lead.calls[-1]))
        self.assertNotIn('intermediate_records',str(lead.calls[-1]))
        trace=json.loads((self.root/returned['debug_trace'].lstrip('/')).read_text())
        self.assertIn('SOURCE-BODY-ONLY-MARKER',str(trace))
        self.assertIn('model_requests',trace)
        read=next(m for m in result['messages'] if isinstance(m,ToolMessage) and m.tool_call_id=='read-result')
        self.assertLess(len(read.content),30000)

    def test_three_reader_invocations_are_bounded_and_do_not_loop_on_delegation(self):
        self.service.news_pack=lambda **_: {'results':[]}
        recall=AIMessage(content='',tool_calls=[{'name':'recall_memory','args':{'query':'Ethereum status'},'id':'empty-search'}])
        model=ReaderModel(responses=[recall,self.typed_response({'findings':[],'gaps':['SYNTHETIC empty corpus']})])
        spec=build_evidence_reader(model,self.service,self.root)
        outputs=[]
        for i in range(4):
            out=spec['runnable'].invoke({'messages':[HumanMessage(content='Ethereum public status')]})
            outputs.append(json.loads(out['messages'][-1].content))
        self.assertEqual(len(model.calls),6)
        self.assertTrue(all(result['status']=='no_evidence' for result in outputs[:3]))
        self.assertIn('delegation limit',str(outputs[-1]).lower())
        self.assertEqual(len(list((self.root/'memory/readers').glob('*/trace.json'))),3)

    def test_reader_source_call_budget_counts_only_executed_calls(self):
        budget=_ReaderBudget(lambda *_:None,None,'synthetic')
        handler=Mock(return_value=object())
        request=SimpleNamespace(tool_call={'name':'read_evidence','id':'synthetic','args':{'record_id':RID}})
        for _ in range(48):budget.wrap_tool_call(request,handler)
        with self.assertRaisesRegex(RuntimeError,'source-call budget'):budget.wrap_tool_call(request,handler)
        self.assertEqual(handler.call_count,48)
        self.assertEqual(budget.source_calls,48)

    def test_reader_model_budget_counts_only_executed_calls_and_rejects_truncation(self):
        budget=_ReaderBudget(lambda *_:None,None,'synthetic')
        handler=Mock(return_value=SimpleNamespace(result=[AIMessage(content='synthetic')]))
        for _ in range(12):budget.wrap_model_call(None,handler)
        with self.assertRaisesRegex(RuntimeError,'model budget'):budget.wrap_model_call(None,handler)
        self.assertEqual(handler.call_count,12)
        self.assertEqual(budget.calls,12)
        truncated=SimpleNamespace(result=[AIMessage(content='truncated',response_metadata={'finish_reason':'length'})])
        with self.assertRaisesRegex(ValueError,'Truncated'):
            _ReaderBudget(lambda *_:None,None,'other').wrap_model_call(None,lambda _:truncated)

    def test_transport_timeout_retries_same_context_without_repeating_source_tools(self):
        generate=ReaderModel._generate;failed=[];snapshots=[]
        def fail_once(model,messages,*args,**kwargs):
            if any(isinstance(m,ToolMessage) and m.name=='read_evidence' for m in messages) and not failed:
                failed.append(True);model.calls.append(deepcopy(messages))
                snapshots.append(json.loads(next((self.root/'memory/readers').glob('*/trace.json')).read_text()))
                raise OpenAITimeoutError(httpx.Request('POST','https://synthetic.invalid/model'))
            return generate(model,messages,*args,**kwargs)
        with patch.object(ReaderModel,'_generate',fail_once):
            result,model,receipt=self.invoke([self.read_response(),self.typed_response(self.output())])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(receipt['model_calls'],3)
        self.assertEqual(receipt['transport_retries'],1)
        self.assertEqual(receipt['tool_calls'],1)
        self.assertEqual(receipt['unknown_usage_attempts'],1)
        self.assertEqual(model.calls[1],model.calls[2])
        requested=[r for r in receipt['intermediate_records'] if r['kind']=='model_requested']
        self.assertEqual([r['request_id'] for r in requested],['1','2','2'])
        failure=next(data for kind,data in self.events if kind=='reader_model_failed')
        self.assertNotIn('kind',failure)
        self.assertIsNone(failure['usage'])
        self.assertIsNone(failure['billing']['input_tokens'])
        self.assertIsNone(failure['billing']['estimated_afp'])
        self.assertFalse(failure['billing']['complete_usage'])
        self.assertEqual(failure['round'],2)
        self.assertEqual(failure['attempt'],1)
        self.assertFalse(failure['transport_recovery'])
        self.assertEqual(len([1 for kind,_ in self.events if kind=='reader_model_finished']),2)
        # The checkpoint exists before the failure, even before invoke returns.
        self.assertEqual(snapshots[0]['result']['status'],'running')
        self.assertEqual(snapshots[0]['read_ids'],[RID])
        self.assertIn('SOURCE-BODY-ONLY-MARKER',str(snapshots[0]['messages']))

    def test_second_transport_failure_preserves_opened_ranges_without_inventing_findings(self):
        generate=ReaderModel._generate
        def fail_after_read(model,messages,*args,**kwargs):
            if any(isinstance(m,ToolMessage) and m.name=='read_evidence' for m in messages):
                model.calls.append(deepcopy(messages))
                raise OpenAITimeoutError(httpx.Request('POST','https://synthetic.invalid/model'))
            return generate(model,messages,*args,**kwargs)
        with patch.object(ReaderModel,'_generate',fail_after_read):
            result,model,receipt=self.invoke([self.read_response()])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['findings'],[])
        self.assertEqual(receipt['model_calls'],3)
        self.assertEqual(receipt['tool_calls'],1)
        self.assertEqual(receipt['transport_retries'],1)
        self.assertEqual(receipt['unknown_usage_attempts'],2)
        self.assertEqual(result['opened_sources'][0]['record_id'],RID)
        self.assertEqual(result['navigation']['json_pointer'],'/opened_sources')
        self.assertIn('not findings',result['navigation']['scope'])
        self.assertTrue(result['artifact'].endswith('/navigation.json'))
        self.assertEqual(result['next_read']['file_path'],result['artifact'])
        navigation=json.loads((self.root/result['artifact'].lstrip('/')).read_text())
        self.assertIn(RID,navigation['opened_sources'])
        self.assertNotIn('model_requests',navigation)
        self.assertNotIn('SOURCE-BODY-ONLY-MARKER',str(navigation))
        self.assertTrue(receipt['messages'])
        span=receipt['opened_sources'][RID]['source_slices'][0]
        original=self.rows[RID]['text'][span['start']:span['end']]
        self.assertEqual(span['sha256'],hashlib.sha256(original.encode()).hexdigest())
        failures=[data for kind,data in self.events if kind=='reader_model_failed']
        self.assertEqual([row['attempt'] for row in failures],[1,2])
        self.assertTrue(failures[-1]['transport_recovery'])
        self.assertTrue(all(row['usage'] is None for row in failures))
        self.assertEqual(model.calls[1],model.calls[2])

    def test_only_one_connection_recovery_per_worker_uses_exact_request_object(self):
        request=SimpleNamespace(messages=[HumanMessage(content='Public ETH review')],system_message=None)
        error=APIConnectionError(request=httpx.Request('POST','https://synthetic.invalid/model'))
        completed=SimpleNamespace(result=[AIMessage(content='SYNTHETIC result')])
        handler=Mock(side_effect=[error,completed,error])
        budget=_ReaderBudget(lambda *_:None,None,'connection')
        self.assertIs(budget.wrap_model_call(request,handler),completed)
        self.assertIs(handler.call_args_list[0].args[0],request)
        self.assertIs(handler.call_args_list[1].args[0],request)
        with self.assertRaises(APIConnectionError):budget.wrap_model_call(request,handler)
        self.assertEqual(handler.call_count,3)
        self.assertEqual(budget.transport_retries,1)

    def test_non_transport_failures_never_retry(self):
        for error in (ReaderAccessError('Private source denied'),ValueError('Invalid schema'),
                      RuntimeError('Evidence reading source-call budget reached')):
            with self.subTest(error=type(error).__name__):
                budget=_ReaderBudget(lambda *_:None,None,'no-retry')
                handler=Mock(side_effect=error)
                with self.assertRaises(type(error)):budget.wrap_model_call(None,handler)
                self.assertEqual(handler.call_count,1)
                self.assertEqual(budget.transport_retries,0)

    def test_transport_retry_obeys_cancellation_wall_and_model_budgets(self):
        for bound in ('cancel','wall','model'):
            with self.subTest(bound=bound):
                clock=[0.0];cancel=Event()
                with patch('research_agent.evidence_reader.perf_counter',side_effect=lambda:clock[0]):
                    budget=_ReaderBudget(lambda *_:None,cancel,'bound')
                    if bound=='model':budget.calls=11
                    def fail(_):
                        if bound=='cancel':cancel.set()
                        if bound=='wall':clock[0]=361.
                        raise OpenAITimeoutError(httpx.Request('POST','https://synthetic.invalid/model'))
                    handler=Mock(side_effect=fail)
                    with self.assertRaises(RuntimeError):budget.wrap_model_call(None,handler)
                    self.assertEqual(handler.call_count,1)
                    self.assertEqual(budget.transport_retries,0)

    def test_ark_response_metadata_usage_is_recorded_when_normalized_usage_is_absent(self):
        from research_agent.semantic_costs import token_count
        response=AIMessage(content='SYNTHETIC completed text',response_metadata={
            'model_name':'doubao-seed-2-1-pro-260915','finish_reason':'stop',
            'token_usage':{'prompt_tokens':1544,'completion_tokens':138,'total_tokens':1682,
                'prompt_tokens_details':{'cached_tokens':1336}}})
        self.assertIsNone(response.usage_metadata)
        budget=_ReaderBudget(lambda kind,data:self.events.append((kind,data)),None,'ark-metadata')
        budget.wrap_model_call(None,lambda _:SimpleNamespace(result=[response]))
        finished=next(data for kind,data in self.events if kind=='reader_model_finished')
        self.assertEqual(token_count(finished['usage'],'input_tokens'),1544)
        self.assertEqual(token_count(finished['usage'],'output_tokens'),138)
        self.assertEqual(finished['billing']['input_tokens'],1544)
        self.assertEqual(finished['billing']['output_tokens'],138)
        self.assertIsNotNone(finished['billing']['estimated_afp'])

    def test_soft_wall_accepts_already_completed_response_and_blocks_new_calls(self):
        clock=[0.0]
        with patch('research_agent.evidence_reader.perf_counter',side_effect=lambda:clock[0]):
            budget=_ReaderBudget(lambda *_:None,None,'late-completion')
            completed=SimpleNamespace(result=[self.typed_response({'findings':[],'gaps':['SYNTHETIC no matching source.']})])
            def handler(_):clock[0]=361.;return completed
            self.assertIs(budget.wrap_model_call(None,handler),completed)
            self.assertEqual(budget.calls,1)
            with self.assertRaisesRegex(RuntimeError,'soft wall'):
                budget.wrap_model_call(None,lambda _:self.fail('No new call after soft wall'))
            with self.assertRaisesRegex(RuntimeError,'soft wall'):
                budget.wrap_tool_call(SimpleNamespace(tool_call={}),lambda _:self.fail('No new tool after soft wall'))
            self.assertEqual(budget.calls,1)
            self.assertEqual(budget.source_calls,0)

    def test_actual_typed_agent_returns_valid_completed_result_after_soft_wall(self):
        clock=[0.0];generate=ReaderModel._generate
        def finish_late(model,*args,**kwargs):
            value=generate(model,*args,**kwargs)
            if len(model.calls)==2:clock[0]=361.
            return value
        with patch('research_agent.evidence_reader.perf_counter',side_effect=lambda:clock[0]), \
             patch.object(ReaderModel,'_generate',finish_late):
            result,model,receipt=self.invoke([self.read_response(),self.typed_response({'findings':[],
                'gaps':['SYNTHETIC completed review; no responsive original in this fixture.']})])
        self.assertEqual(result['status'],'no_evidence')
        self.assertEqual(len(model.calls),2)
        self.assertEqual(receipt['latency_seconds'],361.)
        self.assertEqual(receipt['model_calls'],2)

    def test_reader_uses_native_result_tool_without_json_content_forcing(self):
        class ArkStub(SimpleNamespace):
            def model_copy(self,*,update):return ArkStub(**(vars(self)|update))
        wire_client=SimpleNamespace(chat=SimpleNamespace(completions=object()))
        root_client=Mock();root_client.with_options.return_value=wire_client
        model=ArkStub(root_client=root_client,client='original-parent-client',
            model_kwargs={'extra_body':{'thinking':{'type':'disabled'}},'seed':7,
                'response_format':{'type':'json_object'}})
        valid={'findings':[],'gaps':['SYNTHETIC corpus has no responsive source.']}
        agent=SimpleNamespace(invoke=lambda *_args,**_kwargs:{'messages':[AIMessage(content=json.dumps(valid))]})
        with patch('research_agent.evidence_reader.create_agent',return_value=agent) as create:
            worker=build_evidence_reader(model,self.service,self.root)
            result=worker['runnable'].invoke({'messages':[HumanMessage(content='Public Ethereum review')]})
        output=json.loads(result['messages'][-1].content)
        self.assertEqual(output['status'],'incomplete')
        root_client.with_options.assert_called_once_with(timeout=40,max_retries=0)
        clone=create.call_args.args[0]
        self.assertIs(clone.client,wire_client.chat.completions)
        self.assertNotIn('response_format',clone.model_kwargs)
        self.assertEqual(clone.model_kwargs['seed'],7)
        self.assertEqual(clone.model_kwargs['extra_body'],model.model_kwargs['extra_body'])
        self.assertEqual(model.model_kwargs['response_format'],{'type':'json_object'})
        self.assertEqual(model.client,'original-parent-client')
        strategy=create.call_args.kwargs['response_format']
        self.assertIs(strategy.schema,ReaderResult)
        self.assertTrue(callable(strategy.handle_errors))

    def test_native_reader_payload_contains_source_and_result_tools_without_json_mode(self):
        from langchain_openai import ChatOpenAI
        parent=ChatOpenAI(model='kimi-k3.1-pro',api_key='synthetic-test-key',
            model_kwargs={'response_format':{'type':'json_object'}})
        empty={'findings':[],'gaps':['SYNTHETIC no source inspection.']}
        agent=SimpleNamespace(invoke=lambda *_args,**_kwargs:{'messages':[AIMessage(content=json.dumps(empty))]})
        with patch('research_agent.evidence_reader.create_agent',return_value=agent) as create:
            worker=build_evidence_reader(parent,self.service,self.root)
            worker['runnable'].invoke({'messages':[HumanMessage(content='Read the supplied original.')]})
        reader=create.call_args.args[0]
        source={'type':'function','function':{'name':'read_evidence','description':'Read a saved original.',
            'parameters':{'type':'object','properties':{'record_id':{'type':'string'}},'required':['record_id']}}}
        bound=reader.bind_tools([source,ReaderResult],tool_choice='any')
        payload=reader._get_request_payload([HumanMessage(content='Read the supplied original.')],**bound.kwargs)
        self.assertNotIn('response_format',payload)
        self.assertEqual(payload['tool_choice'],'required')
        self.assertEqual({t['function']['name'] for t in payload['tools']},{'read_evidence','ReaderResult'})

    def test_schema_repair_before_any_read_explicitly_requests_first_source_call(self):
        malformed=self.typed_response({'findings':[],'unexpected_field':'SYNTHETIC schema failure'})
        result,model,receipt=self.invoke([malformed,self.read_response(),self.typed_response(self.output())])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(receipt['read_ids'],[RID])
        feedback=str(model.calls[1])
        self.assertIn('No source tool has run. Call read_evidence now',feedback)
        self.assertNotIn('Do not repeat reading',feedback)
        self.assertEqual(sum(r['kind']=='result_repair' for r in receipt['intermediate_records']),1)

    def test_free_json_fallback_still_rejects_unopened_citation_and_parent_schema(self):
        for payload in (self.output(),{'title':'Wrong parent report schema','sources':['fabricated']}):
            with self.subTest(payload=payload):
                agent=SimpleNamespace(invoke=lambda *_args,**_kwargs:{'messages':[AIMessage(content=json.dumps(payload))]})
                with patch('research_agent.evidence_reader.create_agent',return_value=agent):
                    result,model,receipt=self.invoke([AIMessage(content='unused')])
                self.assertEqual(result['status'],'incomplete')
                self.assertEqual(result['findings'],[])

    def test_strict_typed_result_rejects_parent_schema_drift_and_unbound_findings(self):
        for payload in ({'title':'Parent requested title','sources':['fabricated']},
                        {'findings':[{'finding':'SYNTHETIC unsupported conclusion.'}]},
                        {'findings':[{'finding':'SYNTHETIC unsupported conclusion.','citations':[{'source_id':RID,'quote':QUOTE}]}]}):
            with self.subTest(payload=payload):
                result,model,receipt=self.invoke([self.typed_response(payload)],
                    question='Public Ethereum review; parent requests title and sources fields.')
                self.assertEqual(result['status'],'incomplete')
                self.assertEqual(result['findings'],[])
                self.assertEqual(len(model.calls),2)
                self.assertTrue(any(record['kind']=='worker_failure' for record in receipt['intermediate_records']))

    def test_reader_schema_rejects_unbounded_gap_and_conflict_strings(self):
        for key in ('gaps','conflicts'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                ReaderResult.model_validate({'findings':[],key:['unbounded '*10000]})


if __name__=='__main__':unittest.main()
