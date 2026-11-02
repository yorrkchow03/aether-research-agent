import json,tempfile,unittest
import httpx
from research_agent.investment.jev import JevProvider,JevSettings,checked_choice,TASKS,ENDPOINT

class JevTests(unittest.TestCase):
    def response(self,confidence=.9):return {'model':'jev-1.13.0','answers':{'judgment':{'type':'choice','choice':'planned','probabilities':{'confirmed':.05,'planned':.85,'delayed':.02,'cancelled':.01,'uncertain':.07},'confidence':confidence}},'usage':{'input_tokens':50,'output_tokens':10}}
    def test_low_confidence_abstains_and_rejects_invalid_probability(self):
        self.assertTrue(checked_choice(self.response(.3),TASKS['event_status'],.8)['abstained'])
        bad=self.response();bad['answers']['judgment']['probabilities']['planned']=float('nan')
        with self.assertRaises(ValueError):checked_choice(bad,TASKS['event_status'],.8)
        bad=self.response();bad['answers']['judgment']['choice']='confirmed'
        with self.assertRaises(ValueError):checked_choice(bad,TASKS['event_status'],.8)
    def test_wire_contract_credential_boundary_and_saved_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen=[]
            def handler(request):seen.append(request);return httpx.Response(200,json=self.response())
            provider=JevProvider(JevSettings('typesafe-fixture'),tmp,httpx.MockTransport(handler))
            result=provider.evaluate('event_status','Did it occur?',{'record_id':'page-'+'a'*24,'text':'The upgrade is scheduled; date not decided.'})
            self.assertEqual(str(seen[0].url),ENDPOINT);self.assertEqual(seen[0].headers['Authorization'],'Bearer typesafe-fixture')
            body=json.loads(seen[0].content);self.assertEqual(body['questions']['judgment']['criteria'],TASKS['event_status'])
            self.assertEqual(result['judgment']['choice'],'planned');self.assertNotIn('typesafe-fixture',json.dumps(result))
            with self.assertRaises(ValueError):provider.evaluate('event_status','x',{'record_id':'live-'+'b'*24,'data_category':'private_portfolio','text':'private'})
    def test_unconfigured_makes_no_call_or_fake_jev_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider=JevProvider(JevSettings(),tmp,httpx.MockTransport(lambda r:self.fail('No call allowed')))
            self.assertEqual(provider.evaluate('source_support','claim',{'record_id':'x','text':'source'})['status'],'unconfigured')
