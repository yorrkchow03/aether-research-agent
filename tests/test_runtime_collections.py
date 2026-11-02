"""Long-series Python inputs retain exact source rows and task boundaries."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest

from fixtures import project_fixture
from research_agent.library_collections import collection_digest, validate_collection
from research_agent.claims import Claim, validate_claims
from research_agent.reports import distinct_sources
from research_agent.settings import Settings
from research_agent.terminal import ResearchShell
from research_agent.tools import ResearchTools
from research_data.library import ResearchLibrary


class RuntimeCollectionTests(unittest.TestCase):
    def test_source_receipt_dedup_preserves_different_versions_of_the_same_url(self):
        original={'name':'Synthetic source','url':'https://example.org/input','retrieved_at':'2026-10-27T00:00:00Z','sha256':'a'}
        changed={**original,'sha256':'b','retrieved_at':'2026-10-28T00:00:00Z'}
        self.assertEqual(distinct_sources([original]*4392+[changed]),[original,changed])

    def test_source_snapshot_is_shared_within_validation_but_never_across_submissions(self):
        calls=[];source={'record_id':'fixture','values':{'a':'1','b':'2'},'units':{'a':'count','b':'count'}}
        def lookup(rid):calls.append(rid);return source
        claims=[Claim(statement='Synthetic metric',evidence_ids=['fixture'],metric=k,value=v,unit='count')
                for k,v in [('a','1'),('b','2')]]
        self.assertEqual(validate_claims(claims,lookup)[1],2)
        self.assertEqual(calls,['fixture'])
        source['values']['a']='3'
        with self.assertRaisesRegex(ValueError,'differs from saved evidence'):
            validate_claims(claims,lookup)
        self.assertEqual(calls,['fixture','fixture'])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=project_fixture(Path(self.tmp.name)/'project')
        self.library=ResearchLibrary(self.root/'data/library')
        self.library.register_dataset('hourly.synthetic',provider='fixture',frequency='1h',
            scope='Synthetic input for source-integrity tests only',asset='ETH',units={'close':'USD'})
        capture=self.library.capture('fixture','https://example.org/synthetic',b'{"synthetic":true}',
            retrieved_at='2026-10-27T00:00:00Z')
        start=datetime(2026,10,1,tzinfo=timezone.utc)
        for i in range(48):
            at=start+timedelta(hours=i)
            self.library.put('hourly.synthetic',at.isoformat(),{'close':100+i},capture,
                period_end=(at+timedelta(hours=1)).isoformat())
        run=Path(json.loads((self.root/'data/latest.json').read_text())['run_dir'])
        workspace=self.root/'workspace';workspace.mkdir();(workspace/'notes').mkdir()
        self.service=ResearchTools(run,workspace,'2026-10-26T00:00:00Z',allow_live=True,
            library=self.library,shell=ResearchShell(workspace,Settings(),threading.Event()))

    def packet(self,**kwargs):
        return self.service.sdk_call('library-series',{'dataset_id':'hourly.synthetic',**kwargs})

    def row(self,packet):
        return self.service.lookup(packet['collection_record_id'])

    def test_long_history_is_one_verifiable_python_input_without_false_coverage_claim(self):
        packet=self.packet();self.assertEqual(packet['page_observations'],48)
        self.assertTrue(packet['query_result_fully_materialized'])
        row=self.row(packet)
        self.assertEqual(row['values'],{'observation_count':'48'})
        self.assertEqual(len(row['input_record_ids']),48)
        self.assertIn('does not certify missing market dates',row['query']['scope'])
        recipe=self.service.workspace/'notes/change.py'
        recipe.write_text('import json,sys\nfrom decimal import Decimal\nr=json.load(open(sys.argv[1]))["records"]\nv=Decimal(r[-1]["values"]["close"])/Decimal(r[0]["values"]["close"])-1\nprint(json.dumps({"values":{"change":str(v)},"units":{"change":"fraction"}}))\n')
        result=self.service.register_calculation('/notes/change.py',[packet['collection_record_id']],
            'Last synthetic close / first synthetic close minus one; integrity fixture, not market research')
        self.assertEqual(result['values']['change'],'0.47')
        self.assertEqual(self.service.lookup(result['record_id'])['input_record_ids'],[packet['collection_record_id']])
        self.assertEqual(self.packet()['collection_record_id'],packet['collection_record_id'])

    def test_exploratory_files_cannot_silently_become_undeclared_recipe_inputs(self):
        packet=self.packet()
        (self.service.workspace/'notes/undeclared.json').write_text('{"invented":999}')
        recipe=self.service.workspace/'notes/hidden.py'
        recipe.write_text('import json\nv=json.load(open("notes/undeclared.json"))["invented"]\nprint(json.dumps({"values":{"result":v},"units":{"result":"count"}}))\n')
        with self.assertRaisesRegex(ValueError,'declared inputs in a fresh working directory'):
            self.service.register_calculation('/notes/hidden.py',[packet['collection_record_id']],
                'Must not certify an unregistered exploratory file')
        self.assertEqual(list((self.service.workspace/'data/live').glob('*.json')),[])

    def test_pagination_preserves_incomplete_query_scope(self):
        first=self.packet(limit=12);second=self.packet(limit=12,offset=12)
        self.assertFalse(first['query_result_fully_materialized'])
        self.assertFalse(second['query_result_fully_materialized'])
        self.assertEqual(self.row(second)['query']['offset'],12)
        self.assertNotEqual(first['collection_record_id'],second['collection_record_id'])

    def test_recomputed_hash_cannot_legitimize_changed_source_values(self):
        row=copy.deepcopy(self.row(self.packet()))
        row['records'][0]['values']['close']='99999'
        digest=collection_digest(row);row.update(collection_sha256=digest,record_id='series-'+digest[:24])
        with self.assertRaisesRegex(ValueError,'differs from the original source'):
            validate_collection(row,self.library,self.service.cutoff,self.service.mode)

    def test_derived_metadata_and_late_acquisition_cannot_bypass_source_scope(self):
        row=copy.deepcopy(self.row(self.packet()))
        row['values']['observation_count']='999'
        with self.assertRaisesRegex(ValueError,'count metadata'):
            validate_collection(row,self.library,self.service.cutoff,self.service.mode)
        row=self.row(self.packet());row['mode']='strict'
        digest=collection_digest(row);row.update(collection_sha256=digest,record_id='series-'+digest[:24])
        with self.assertRaisesRegex(ValueError,'strict cutoff'):
            validate_collection(row,self.library,self.service.cutoff,'strict')
        self.service.mode='strict'
        strict=self.packet(mode='observed',known_by='2030-01-01')
        self.assertEqual(strict['total'],0)
        self.assertNotIn('collection_record_id',strict)


if __name__=='__main__':unittest.main()
