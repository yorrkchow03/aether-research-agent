"""Real terminal failures, local SDK loading and decimal source fidelity."""
from decimal import Decimal
import json,os,shlex,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from research_agent.data_contract import numeric_contract
from research_agent.research_sdk import ResearchClient
from research_agent.settings import Settings
from research_agent.terminal import ResearchShell


class TerminalContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'task space';(self.root/'data').mkdir(parents=True)
        self.row={'values':{'close_usd':'2234.610000000001','missing':None},'units':{'close_usd':'USD'}}
        self.path=self.root/'data/sample.json';self.path.write_text(json.dumps(self.row))
        self.shell=ResearchShell(self.root,Settings())

    def test_redirected_pipeline_failure_does_not_report_success(self):
        code="raise FileNotFoundError('/data/not-real.json')"
        result=self.shell.execute('python -c '+shlex.quote(code)+' 2>/dev/null | cat')
        self.assertNotEqual(result.exit_code,0)
        self.assertIn('without captured output',result.output)
        handled=self.shell.execute('false | true || printf handled')
        self.assertEqual(handled.exit_code,0)
        self.assertEqual(handled.output,'handled')

    def test_workspace_loader_handles_virtual_path_without_network_or_value_coercion(self):
        code="from research_agent.research_sdk import ResearchClient; from decimal import Decimal; r=ResearchClient('unused').read_json({'data_file':'/data/sample.json'}); print(Decimal(r['values']['close_usd'])+Decimal('1')); print(r['values']['missing'])"
        result=self.shell.execute('python -c '+shlex.quote(code))
        self.assertEqual(result.exit_code,0,result.output)
        self.assertEqual(result.output.splitlines(),['2235.610000000001','None'])

    def test_failed_string_arithmetic_has_concrete_parsing_hint(self):
        result=self.shell.execute('python -c '+shlex.quote("print('2234.61'/'2248.34')"))
        self.assertNotEqual(result.exit_code,0)
        self.assertIn('TypeError',result.output)
        self.assertIn('Decimal',result.output)

    def test_local_loader_paths_and_traversal_are_explicit(self):
        client=ResearchClient('unused')
        with patch.dict(os.environ,{'RESEARCH_WORKSPACE':str(self.root)}),patch('socket.socket',side_effect=AssertionError('No network')):
            for path in ('/data/sample.json','./data/sample.json',str(self.path),{'series_file':{'path':'/data/sample.json'}}):
                self.assertEqual(client.read_json(path),self.row)
            outside=Path(self.temp.name)/'outside.json';outside.write_text('{}')
            (self.root/'data/escape').symlink_to(outside)
            for path in ('../outside.json',str(outside),'/data/escape'):
                with self.assertRaisesRegex(ValueError,'escapes'):client.read_json(path)
            large=self.root/'data/large.json';large.write_text(json.dumps({'text':'a'*2000}))
            with self.assertRaisesRegex(ValueError,'size bound'):client.read_json('/data/large.json',max_bytes=1024)

    def test_sample_contract_preserves_exact_numbers_and_unknowns(self):
        row={'values':{'price':'1.230000000000000001','missing':None,'invalid':'NaN','label':'ETH'},
             'series':[{'close_usd':'2.5000','observed_at':'2026-11-07T00:00:00Z'}]}
        before=json.dumps(row,sort_keys=True)
        contract=numeric_contract(row)
        self.assertEqual(contract['sample_field_types'],{'values.price':'decimal_string','series[].close_usd':'decimal_string'})
        self.assertTrue(contract['sample_only'])
        self.assertEqual(json.dumps(row,sort_keys=True),before)
        self.assertIsNone(numeric_contract({'values':{'missing':None}}))


if __name__=='__main__':unittest.main()
