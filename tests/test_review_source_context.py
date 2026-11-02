"""Post-confirmation product regression: auditors must see relevant long text.

All sources/targets are synthetic. No held-out rubric, provider, or key is read.
"""
from copy import deepcopy
import hashlib,json,tempfile,unittest
import os,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from research_agent.review import (evidence_text,quote_index,exact_source_quote,
    EvidenceReviewer,REVIEW_VERSION,SOURCE_TEXT_BUDGET)
from research_agent.knowledge import KnowledgeStore
from research_agent.settings import Settings


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


class ReviewSourceContextTests(unittest.TestCase):
    def row(self,text,rid='page-'+'a'*24):
        return {'record_id':rid,'title':'Quarterly source context fixture',
            'url':'https://example.invalid/quarterly','text':text,
            'published_at':'2026-06-30T00:00:00Z','retrieved_at':'2026-10-28T00:00:00Z',
            'source':{'source_id':'synthetic','url':'https://example.invalid/quarterly','sha256':'a'*64}}
    def segments(self,context):
        return context.get('text_segments') or ([{'start':0,'end':len(context['text']),'text':context['text'],
            'sha256':digest(context['text'])}] if context.get('text') else [])
    def assert_bound(self,original,context):
        spans=self.segments(context);previous=0
        for span in spans:
            self.assertGreaterEqual(span['start'],previous)
            self.assertLess(span['start'],span['end'])
            self.assertEqual(span['text'],original[span['start']:span['end']])
            self.assertEqual(span['sha256'],digest(span['text']))
            previous=span['end']
        amount=sum(s['end']-s['start'] for s in spans)
        self.assertLessEqual(amount,SOURCE_TEXT_BUDGET)
        metadata=context['text_context']
        self.assertEqual(metadata['selected_source_characters'],amount)
        self.assertEqual(metadata['full_text_sha256'],digest(original))
        self.assertEqual(metadata['full_text_characters'],len(original))
        self.assertEqual(metadata['offset_unit'],'unicode_characters')

    def test_short_original_text_and_metadata_remain_available(self):
        row=self.row('A grant remains conditional on a later approval.\nNo execution is established.')
        context=json.loads(evidence_text(row))
        self.assertEqual(context['text'],row['text'])
        self.assertEqual(context['title'],row['title'])
        self.assertEqual(context['url'],row['url'])
        self.assertFalse(context['text_context']['source_text_truncated'])
        self.assertEqual(context['text_context']['omitted_ranges'],[])
        self.assert_bound(row['text'],context)
        self.assertTrue(exact_source_quote('conditional on a later approval.\nNo execution',evidence_text(row)))

    def test_long_source_tail_amount_and_final_condition_are_not_prefix_clipped(self):
        text='Quarterly opening.\n'+('Generic project descriptions. '*1700)+'\nQuarter total: $84,123,456.78.\nFinal condition: pending audit, not a completed transfer.'
        context=json.loads(evidence_text(self.row(text),targets=[{'statement':'The quarter total was $84,123,456.78; its audit remains pending.'}]))
        visible='\n'.join(s['text'] for s in self.segments(context))
        self.assertIn('$84,123,456.78',visible)
        self.assertIn('pending audit, not a completed transfer',visible)
        self.assertIn('Quarterly opening.',visible)
        self.assertTrue(context['text_context']['source_text_truncated'])
        self.assert_bound(text,context)

    def test_multiple_claims_retrieve_separated_middle_positions(self):
        first='ZEBRACAP tranche: $12,345,678 was proposed, not paid.'
        second='ORCHIDPOLICY rate was 4.75%, subject to review.'
        text=('Unrelated opening. '*1000)+first+('Generic filler. '*2000)+second+('Generic closing. '*1600)
        context=json.loads(evidence_text(self.row(text),targets=[
            {'statement':'ZEBRACAP tranche proposes $12345678.'},
            {'statement':'ORCHIDPOLICY specifies a 4.75% rate.'}]))
        visible='\n'.join(s['text'] for s in self.segments(context))
        self.assertIn(first,visible);self.assertIn(second,visible)
        self.assert_bound(text,context)

    def test_unicode_offsets_hash_actual_characters(self):
        fact='独特校验锚点：提案金额为1234567.89，尚未完成。'
        text='背景🍀内容。'*5000+fact+'后续普通文本。'*3500
        context=json.loads(evidence_text(self.row(text),focus='独特校验锚点 1234567.89'))
        self.assertIn(fact,'\n'.join(s['text'] for s in self.segments(context)))
        self.assert_bound(text,context)

    def test_total_budget_and_nonoverlap_hold_for_many_disjoint_claims(self):
        text='\n'.join('BLOCK'+str(i)+' has LABEL'+str(i)+' and amount '+str(700000+i)+'. '+('plain background '*60) for i in range(100))
        targets=[{'statement':'LABEL'+str(i)+' amount '+str(700000+i)} for i in range(50)]
        context=json.loads(evidence_text(self.row(text),targets=targets))
        self.assert_bound(text,context)
        self.assertTrue(context['text_context']['omitted_ranges'])

    def test_boundary_exactly_budget_is_full_and_one_over_is_explicitly_bounded(self):
        for length in (SOURCE_TEXT_BUDGET,SOURCE_TEXT_BUDGET+1):
            with self.subTest(length=length):
                text='x'*length;context=json.loads(evidence_text(self.row(text)))
                self.assert_bound(text,context)
                self.assertEqual(context['text_context']['source_text_truncated'],length>SOURCE_TEXT_BUDGET)

    def test_original_record_and_persistent_copy_are_never_overwritten(self):
        row=self.row('Existing original. '*1600+'A tail amount of $99,999.00.')
        before=deepcopy(row)
        with tempfile.TemporaryDirectory() as directory:
            store=KnowledgeStore(Path(directory)/'knowledge');store.register(row)
            saved=store.get(row['record_id'])
            evidence_text(saved,focus='tail amount 99999.00')
            self.assertEqual(store.get(row['record_id']),saved)
        self.assertEqual(row,before)

    def test_quotations_never_join_across_omitted_segments(self):
        text='HEADINDEPENDENT-'+('a'*20000)+'MIDDLEANCHOR-'+('b'*20000)+'TAILINDEPENDENT'
        serialized=evidence_text(self.row(text),focus='MIDDLEANCHOR')
        obj=json.loads(serialized);spans=self.segments(obj)
        self.assertGreaterEqual(len(spans),2)
        left,right=next((a,b) for a,b in zip(spans,spans[1:]) if a['end']<b['start'])
        stitched=left['text'][-30:]+'\n[…]\n'+right['text'][:30]
        self.assertFalse(exact_source_quote(stitched,serialized))
        self.assertFalse(exact_source_quote(obj['text_context']['scope'],serialized))
        for quote in quote_index({'R1':serialized})['R1'].values():
            self.assertTrue(exact_source_quote(quote,serialized),quote)
            self.assertNotIn('Omitted passages remain unknown',quote)

    def test_long_index_exposes_tail_quote_and_title_without_omission_markers(self):
        text='Opening.\n'+('Unrelated content. '*2000)+'\nFINAL AMOUNT IS $91,234,567.89, not a promise of payment.'
        row=self.row(text);serialized=evidence_text(row,focus='final amount 91234567.89')
        index=quote_index({'R1':serialized})['R1']
        self.assertTrue(any('$91,234,567.89' in value for value in index.values()))
        self.assertTrue(any(row['title'] in value for value in index.values()))
        self.assertTrue(all(exact_source_quote(value,serialized) for value in index.values()))

    def test_url_fallback_preserves_saved_source_url(self):
        row=self.row('An original fact.');del row['url']
        context=json.loads(evidence_text(row))
        self.assertEqual(context['url'],row['source']['url'])

    def test_default_long_context_includes_head_and_tail_without_claims(self):
        text='FIRST_SOURCE_LINE\n'+('background '*3000)+'\nLAST_SOURCE_LINE'
        context=json.loads(evidence_text(self.row(text)))
        visible='\n'.join(s['text'] for s in self.segments(context))
        self.assertIn('FIRST_SOURCE_LINE',visible);self.assertIn('LAST_SOURCE_LINE',visible)
        self.assert_bound(text,context)

    def test_nontext_evidence_fields_keep_their_existing_numeric_contract(self):
        row={'record_id':'data-id','values':{'price_usd':'42'},'units':{'price_usd':'USD'}}
        serialized=evidence_text(row,targets=[{'statement':'price_usd is 42 USD'}])
        self.assertEqual(json.loads(serialized),row)
        quotes=quote_index({'R1':serialized})['R1']
        self.assertTrue(any('price_usd' in value and '42' in value for value in quotes.values()))
        self.assertTrue(all(exact_source_quote(value,serialized) for value in quotes.values()))

    def test_review_groups_focus_by_the_source_actually_cited(self):
        records={'source-a':self.row('Source A fact.','source-a'), 'source-b':self.row('Source B fact.','source-b')}
        targets=[{'claim_id':'O1','kind':'observation','statement':'Exclusive A anchor','evidence_ids':['source-a']},
            {'claim_id':'O2','kind':'observation','statement':'Exclusive B anchor','evidence_ids':['source-b']}]
        requests=[]
        class Fake:
            http_client=SimpleNamespace(close=Mock())
            def invoke(self,messages,**options):
                payload=json.loads(messages[-1].content);requests.append(payload)
                response={'verdicts':[{'claim_id':target['claim_id'],'status':'insufficient',
                    'reason':'Synthetic source-aware test, no factual grade.','quotes':[]} for target in payload['targets']]}
                return SimpleNamespace(content=json.dumps(response),response_metadata={'model_name':'synthetic-pro'},usage_metadata={'input_tokens':10,'output_tokens':10})
        with patch('research_agent.review.create_ark_model',return_value=Fake()),patch('research_agent.review.evidence_text',wraps=evidence_text) as serializer:
            reviewer=EvidenceReviewer(Settings());result=reviewer.review_claims(targets,records.__getitem__)
        self.assertEqual(len(serializer.call_args_list),2)
        calls={call.args[0]['record_id']:[t['claim_id'] for t in call.kwargs['targets']] for call in serializer.call_args_list}
        self.assertEqual(calls,{'source-a':['O1'],'source-b':['O2']})
        self.assertEqual(result['review_version'],REVIEW_VERSION)
        self.assertTrue(REVIEW_VERSION.startswith('support-v6-'))
        self.assertEqual(len(requests),1)

    def test_changed_review_version_cannot_reuse_a_prior_cached_verdict(self):
        row=self.row('Source fact.');targets=[{'claim_id':'O1','kind':'observation','statement':'Source fact.','evidence_ids':['r']}]
        requests=[]
        class Fake:
            http_client=SimpleNamespace(close=Mock())
            def invoke(self,messages,**options):
                requests.append(messages)
                return SimpleNamespace(content=json.dumps({'verdicts':[{'claim_id':'O1','status':'insufficient',
                    'reason':'Synthetic cache check.','quotes':[]}]}),response_metadata={'model_name':'synthetic-pro'},usage_metadata={'input_tokens':1,'output_tokens':1})
        with patch('research_agent.review.create_ark_model',return_value=Fake()):
            reviewer=EvidenceReviewer(Settings())
            with patch('research_agent.review.REVIEW_VERSION','support-v5-synthetic-cache'):
                first=reviewer.review_claims(deepcopy(targets),lambda _:row)
            second=reviewer.review_claims(deepcopy(targets),lambda _:row)
        self.assertEqual(len(requests),2)
        self.assertNotEqual(first['review_id'],second['review_id'])
        self.assertEqual(second['review_version'],REVIEW_VERSION)

    def test_span_selection_is_identical_across_python_hash_seeds(self):
        script='''import json
from research_agent.review import evidence_text
text='\\n'.join('BLOCK'+str(i)+' orchid zebra funding review '+str(100000+i)+' '+('plain repeated words '*35) for i in range(140))
targets=[{'statement':'zebra orchid BLOCK'+str(i)+' funding '+str(100000+i)} for i in (13,44,72,101,125)]
obj=json.loads(evidence_text({'record_id':'synthetic','text':text},targets=targets))
print(json.dumps({'segments':[(s['start'],s['end'],s['sha256']) for s in obj['text_segments']], 'context':obj['text_context']},sort_keys=True))
'''
        outputs=[]
        for seed in ('1','7','12345'):
            env={key:value for key,value in os.environ.items() if not any(marker in key.upper() for marker in ('API_KEY','SECRET','TOKEN'))}
            env['PYTHONHASHSEED']=seed
            run=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            outputs.append(run.stdout)
        self.assertEqual(outputs,[outputs[0]]*3)


if __name__=='__main__':unittest.main()
