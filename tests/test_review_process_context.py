"""Offline same-job process receipt and native virtual pointer checks."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from research_agent.review_source_navigation import review_process_context,source_repair_navigation
from research_agent.tools import ResearchTools
from test_planning import ScriptedModel


class ProcessContextTests(unittest.TestCase):
    def test_saved_r6_same_job_replay(self):
        root=Path(__file__).resolve().parents[1]/'local/long-study/20261108/round6/arms/doubao-seed-2.1-pro/runtime/sessions/aa8b70616ae6456c9d79ec85093cc58e/workspace'
        if not root.is_dir():self.skipTest('Optional R6 workspace unavailable')
        job='13eae07663534919b93e9a90e5c8a96e'
        db=sqlite3.connect('file:'+str(root/'memory/task-state/working.sqlite')+'?mode=ro',uri=True)
        state=json.loads(db.execute('select body from tasks where job_id=?',(job,)).fetchone()[0]);db.close()
        with TemporaryDirectory() as tmp:
            workspace=Path(tmp)
            shutil.copytree(root/'memory/readers',workspace/'memory/readers')
            class Working:
                def state(self,j):
                    if j!=job:raise KeyError(j)
                    return state
                def read_artifact(self,j,aid):
                    if j!=job:raise KeyError(j)
                    matches=list((root/'memory/task-state/artifacts'/j).glob(aid.removeprefix('artifact-')+'*.txt'))
                    if len(matches)!=1:raise KeyError(aid)
                    data=matches[0].read_bytes();assert hashlib.sha256(data).hexdigest()==matches[0].stem
                    target=workspace/'memory/task-state/artifacts'/j/matches[0].name
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
                    return {'path':str(target),'text':data.decode()}
                def state_snapshot(self,j):
                    path=workspace/'memory/task-state/state.json';path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_text(json.dumps(state));return {'path':str(path)}
            def lookup(rid):return json.loads((root/'data/live'/(rid+'.json')).read_text())
            service=SimpleNamespace(workspace=workspace,working_memory=Working(),job_id=job,
                lookup=lookup,_public_judgment_row=ResearchTools._public_judgment_row)
            context=review_process_context(service)
            self.assertLessEqual(len(json.dumps(context,ensure_ascii=False)),4000)
            coverage=next(f for f in context['facts'] if f['kind']=='retrieval_coverage')
            self.assertEqual(coverage['data']['document_count'],101)
            self.assertIn('candidate_pool_truncated_not_complete_window_coverage',coverage['data']['gaps'])
            reader=next(f for f in context['facts'] if f['kind']=='recorded_source_inspection' and '/readers/' in f['pointer']['file_path'])
            self.assertEqual(len(reader['data']['sources']),13)
            original_review=json.loads((root/'reviews/review-f5470c7fa7de2680df95b2ea.json').read_text())
            candidates=source_repair_navigation(service,original_review)
            failure_source=next(row for row in candidates['candidates_by_target']['K2'] if row['record_id']=='page-1d599926d1ead04fe31480d6')
            self.assertEqual(failure_source['source_status'],'opened')
            backend=FilesystemBackend(root_dir=workspace,virtual_mode=True)
            model=ScriptedModel(responses=[AIMessage(content='',tool_calls=[{'id':'open','name':'read_file','args':{'file_path':reader['pointer']['file_path']}}]),AIMessage(content='Done.')])
            graph=create_deep_agent(model,backend=backend,middleware=[FilesystemMiddleware(backend=backend,tool_token_limit_before_evict=100000)])
            result=graph.invoke({'messages':[HumanMessage(content='Read the process pointer.')]})
            opened=next(m for m in result['messages'] if isinstance(m,ToolMessage) and m.tool_call_id=='open')
            self.assertEqual(opened.status,'success');self.assertIn('opened_sources',opened.content)

    def test_missing_memory_is_not_invented(self):
        self.assertIsNone(review_process_context(SimpleNamespace()))

    def test_private_and_unobserved_reader_sources_excluded(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);reader='a'*32;other='b'*32
            public='page-'+'1'*24;private='page-'+'2'*24;unseen='page-'+'3'*24
            for ident,ids in ((reader,[public,private]),(other,[unseen])):
                path=root/'memory/readers'/ident/'navigation.json';path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'reader_id':ident,'opened_sources':{rid:{} for rid in ids}}))
            payload=json.dumps({'reader_id':reader});artifact=root/'task.json';artifact.write_text(payload)
            class Working:
                def state(self,job):return {'research_state':{'observations':[{'status':'success','tool_name':'task','artifact_id':'only'}]}}
                def read_artifact(self,job,aid):return {'path':str(artifact),'text':payload}
            def lookup(rid):return {'record_id':rid,'url':'https://example.test/'+rid,
                'source_kind':'article','private':rid==private}
            service=SimpleNamespace(workspace=root,working_memory=Working(),job_id='one',lookup=lookup,
                _public_judgment_row=ResearchTools._public_judgment_row)
            context=review_process_context(service)
            rows=context['facts'][0]['data']['sources']
            self.assertEqual([row['record_id'] for row in rows],[public])
            self.assertNotIn(unseen,json.dumps(context))


if __name__=='__main__':unittest.main()
