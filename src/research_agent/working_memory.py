"""Durable task notes and complete redacted tool artifacts with bounded views.

This is derived working state, not a source of verified financial facts. Full
messages/checkpoints remain available; compaction changes only a model input view.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from langchain_core.messages import HumanMessage, message_to_dict
from research_data.http import dumps, utcnow


def _serial(value):
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _issue_navigation(rows, maximum):
    """Index all review targets before fitting bounded, attributed previews."""
    targets=list(dict.fromkeys(str(row.get('review_family') or 'report')+':'+row['claim_id']
        for row in rows if row.get('kind')=='review_issue' and row.get('claim_id')))
    result={'unresolved_count':len(rows), 'review_targets':targets, 'items':[],
        'details':'Full state revision above retains every issue and its exact artifact pointer.'}
    ordered=sorted(enumerate(rows),key=lambda pair:(pair[1].get('kind')!='review_issue',-pair[0]))
    for _,row in ordered:
        item={k:row[k] for k in ('kind','claim_id','claim_path','issue_kind','tool','artifact_id')
            if row.get(k)}
        for key in ('correction','error','reason'):
            if not row.get(key):continue
            text=str(row[key]);item[key]=text if len(text)<=180 else text[:180]+' [Preview; read full state.]'
            break
        candidate={**result,'items':[*result['items'],item]}
        if len(dumps(candidate))<=maximum:
            result=candidate
    result['omitted_item_count']=len(rows)-len(result['items'])
    return result


class WorkingMemory:
    def __init__(self, root, *, redact=None, evidence_lookup=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.redact = redact or (lambda value: str(value))
        self.evidence_lookup = evidence_lookup
        self.path = self.root / 'working.sqlite'
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS tasks(job_id TEXT PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts(job_id TEXT NOT NULL,artifact_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(job_id,artifact_id));
                CREATE TABLE IF NOT EXISTS tool_observations(job_id TEXT NOT NULL,observation_id TEXT NOT NULL,
                    tool_call_id TEXT,body TEXT NOT NULL,PRIMARY KEY(job_id,observation_id));
                CREATE UNIQUE INDEX IF NOT EXISTS observed_tool_calls ON tool_observations(job_id,tool_call_id)
                    WHERE tool_call_id IS NOT NULL;
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def _job(job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job_id):
            raise ValueError('Invalid working-memory job ID')
        return job_id

    def begin(self, job_id, goal, cutoff=None, plan=None, *, mode='observed'):
        from .research_state import empty_state
        self._job(job_id)
        if mode not in ('observed','strict'):
            raise ValueError('Unknown working-memory mode')
        state = {'job_id': job_id, 'goal': self.redact(goal), 'cutoff': cutoff, 'mode': mode,
            'plan': json.loads(self.redact(_serial(plan or []))), 'todos': [], 'facts': [], 'unknowns': [], 'conflicts': [],
            'active_artifacts': [], 'deferred_checks': [], 'status': 'active',
            'research_state': empty_state(),
            'created_at': utcnow(), 'updated_at': utcnow(),
            'scope': 'Derived task state. Evidence-linked notes require original-source checks; tool output is not automatically a fact.'}
        with self.connect() as c:
            prior = c.execute('SELECT body FROM tasks WHERE job_id=?', (job_id,)).fetchone()
            if prior:
                existing = json.loads(prior['body'])
                if existing['goal'] != state['goal'] or existing.get('cutoff') != cutoff or existing.get('mode') != mode:
                    raise ValueError('A working-memory job cannot be rebound to a different task')
                return existing
            c.execute('INSERT INTO tasks VALUES(?,?,1)', (job_id, dumps(state, sort_keys=True)))
        return state

    def state(self, job_id):
        self._job(job_id)
        with self.connect() as c:
            row = c.execute('SELECT body,revision FROM tasks WHERE job_id=?', (job_id,)).fetchone()
        if not row:
            raise KeyError('Unknown working-memory task')
        return {**json.loads(row['body']), 'revision': row['revision']}

    def update(self, job_id, **fields):
        self._job(job_id)
        allowed = {'plan', 'todos', 'facts', 'unknowns', 'conflicts', 'active_artifacts', 'deferred_checks', 'status'}
        if set(fields) - allowed:
            raise ValueError('Unsupported working-memory fields')
        for fact in fields.get('facts', []):
            if not isinstance(fact, dict) or not fact.get('statement') or not fact.get('evidence_ids'):
                raise ValueError('Task fact notes require statement and actual evidence references')
        clean = json.loads(self.redact(_serial(fields)))
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body,revision FROM tasks WHERE job_id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError('Unknown working-memory task')
            state = {**json.loads(row['body']), **clean, 'updated_at': utcnow()}
            c.execute('UPDATE tasks SET body=?,revision=? WHERE job_id=?',
                      (dumps(state, sort_keys=True), row['revision']+1, job_id))
        return {**state, 'revision': row['revision']+1}

    def observe_tool(self, job_id, tool_name, payload, status='success', tool_call_id=None):
        """Persist and reduce an actual host result; never expose as an LLM tool.

        Same-call retries with the same body are idempotent. Different contents
        cannot silently reuse a tool call ID. The raw (redacted) artifact remains
        available even if result interpretation fails.
        """
        from .research_state import derive_observation, digest, envelope, merge_observation
        self._job(job_id)
        if tool_call_id is not None and (not isinstance(tool_call_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',tool_call_id)):
            raise ValueError('Invalid actual tool call ID')
        clean = self.redact(_serial(payload))
        receipt = self.record_tool(job_id,tool_name,clean)
        identity = {'tool_name':tool_name,'status':status,'payload_sha256':receipt['sha256'],'tool_call_id':tool_call_id}
        oid = 'observation-' + digest(identity)[:24]
        change = derive_observation(tool_name,envelope(clean) or clean,status=status,
            artifact_id=receipt['artifact_id'],observation_id=oid)
        observation = {**identity,'observation_id':oid,'artifact_id':receipt['artifact_id'],
            'known_at':utcnow(),'scope':'Actual host-result envelope, not semantic truth.'}
        duplicate=False
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body,revision FROM tasks WHERE job_id=?',(job_id,)).fetchone()
            if not row:raise KeyError('Unknown working-memory task')
            existing=c.execute('SELECT body FROM tool_observations WHERE job_id=? AND observation_id=?',(job_id,oid)).fetchone()
            if existing:
                duplicate=True;observation=json.loads(existing['body']);revision=row['revision']
            else:
                if tool_call_id and c.execute('SELECT 1 FROM tool_observations WHERE job_id=? AND tool_call_id=?',(job_id,tool_call_id)).fetchone():
                    raise ValueError('An observed tool call ID cannot be rebound to different output')
                state=json.loads(row['body']);state['research_state']=merge_observation(state.get('research_state'),change,observation)
                active=state.get('active_artifacts',[])
                if receipt['artifact_id'] not in active:active.append(receipt['artifact_id'])
                state['active_artifacts']=active;state['updated_at']=utcnow();revision=row['revision']+1
                c.execute('INSERT INTO tool_observations VALUES(?,?,?,?)',(job_id,oid,tool_call_id,dumps(observation,sort_keys=True)))
                c.execute('UPDATE tasks SET body=?,revision=? WHERE job_id=?',(dumps(state,sort_keys=True),revision,job_id))
        return {**observation,'duplicate':duplicate,'artifact':receipt,'research_state_revision':revision,
            'derived_counts':{key:len(value) for key,value in change.items()}}

    def add_note(self, job_id, statement, evidence_ids=None, *, kind='derived_claim', origin='model_note'):
        """Explicit model-authored state note; references do not certify its prose."""
        from .research_state import ID, digest, empty_state
        self._job(job_id)
        if kind not in ('derived_claim','question','next_check'):
            raise ValueError('Unknown derived research note kind')
        if not isinstance(statement,str) or not 1<=len(statement.strip())<=2000:
            raise ValueError('A bounded meaningful derived note is required')
        if not isinstance(origin,str) or not 1<=len(origin)<=160:
            raise ValueError('A bounded note origin is required')
        if evidence_ids is not None and (not isinstance(evidence_ids,list) or any(not isinstance(rid,str) for rid in evidence_ids)):
            raise ValueError('Note evidence references must be a list of source IDs')
        ids=list(dict.fromkeys(evidence_ids or []))
        if len(ids)>20 or any(not isinstance(rid,str) or not ID.fullmatch(rid) for rid in ids):
            raise ValueError('Notes require bounded source IDs, not copied source text')
        if kind=='derived_claim' and not ids:
            raise ValueError('A derived claim note requires source references')
        if self.evidence_lookup:
            for rid in ids:self.evidence_lookup(rid)
        identity=json.loads(self.redact(_serial({'statement':statement,'evidence_ids':ids,'kind':kind,'origin':origin})))
        nid='note-'+digest(identity)[:24]
        note={**identity,'entry_id':nid,'status':'unverified_derived_note','known_at':utcnow(),
            'references_checked':bool(self.evidence_lookup),'scope':'Model/process note, not a verified source fact.'}
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body,revision FROM tasks WHERE job_id=?',(job_id,)).fetchone()
            if not row:raise KeyError('Unknown working-memory task')
            state=json.loads(row['body']);research=state.setdefault('research_state',empty_state())
            old=next((r for r in research['derived_notes'] if r['entry_id']==nid),None)
            if old:return {**old,'duplicate':True,'research_state_revision':row['revision']}
            research['derived_notes'].append(note);state['updated_at']=utcnow()
            c.execute('UPDATE tasks SET body=?,revision=? WHERE job_id=?',(dumps(state,sort_keys=True),row['revision']+1,job_id))
        return {**note,'duplicate':False,'research_state_revision':row['revision']+1}

    def state_snapshot(self, job_id):
        """Write an immutable per-revision JSON view, preserving the SQL source."""
        state=self.state(job_id);body=dumps(state,sort_keys=True,indent=2).encode()
        directory=self.root/'states'/job_id;directory.mkdir(parents=True,exist_ok=True)
        if not directory.resolve().is_relative_to(self.root):raise ValueError('Working state directory escaped its root')
        path=directory/('revision-'+str(state['revision'])+'.json')
        if path.is_symlink():raise ValueError('Working state snapshot cannot be a symlink')
        if path.exists():
            if path.read_bytes()!=body:raise ValueError('Working state revision snapshot mismatch')
        else:
            temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
            temporary.write_bytes(body);temporary.replace(path)
        return {'path':str(path),'relative_path':str(path.relative_to(self.root)),
            'sha256':hashlib.sha256(body).hexdigest(),'revision':state['revision']}

    def progressive_tool_view(self, job_id, tool_name, payload, status='success', max_chars=6000):
        """Bound the complete model result; keep the exact SDK result on disk.

        Explicit filesystem expansions already have backend line limits and do
        not get recursively re-offloaded. Other large outputs, including review
        and failures, expose structured navigation and atomic actionable fields.
        """
        from .research_state import envelope
        from .tool_result_views import compact_tool_result
        if not 800<=max_chars<=180000:raise ValueError('Tool view budget must be 800..180000 characters')
        text=self.redact(_serial(payload));obj=envelope(text)
        # An explicit file read is already a requested expansion. Offloading its
        # decorated response again creates recursive artifacts and unreadable JSON.
        if (tool_name in ('read_file','ls','glob','grep','edit_file','write_file','write_todos')
                or len(text)<=max_chars):
            return text
        receipt=self.record_tool(job_id,tool_name,text)
        return compact_tool_result(tool_name,text,obj,receipt,status=status,
            max_chars=max_chars,root=self.root)

    def record_tool(self, job_id, name, output, evidence_ids=None):
        self.state(job_id)
        raw = self.redact(_serial(output)).encode('utf-8')
        sha = hashlib.sha256(raw).hexdigest()
        directory = self.root / 'artifacts' / job_id
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError('Working artifact directory escaped its task root')
        path = directory / (sha + '.txt')
        if path.is_symlink():raise ValueError('Working artifact cannot be a symlink')
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError('Working artifact hash mismatch')
        if not path.exists():
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_bytes(raw)
            temporary.replace(path)
        ids = list(dict.fromkeys(evidence_ids or []))[:150]
        receipt = {'artifact_id': 'artifact-' + sha[:24], 'sha256': sha,
            'path': str(path), 'tool': str(name)[:150], 'bytes': len(raw),
            'evidence_ids': ids, 'created_at': utcnow(),
            'scope': 'Saved redacted tool output, not a verified claim or new source.'}
        # Identical content is deduplicated; tool provenance remains a set.
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT body FROM artifacts WHERE job_id=? AND artifact_id=?',
                            (job_id, receipt['artifact_id'])).fetchone()
            if old:
                previous = json.loads(old['body'])
                previous['tools'] = sorted(set(previous.get('tools', [previous['tool']])) | {receipt['tool']})
                previous['evidence_ids'] = list(dict.fromkeys(previous['evidence_ids'] + ids))
                receipt = previous
                c.execute('UPDATE artifacts SET body=? WHERE job_id=? AND artifact_id=?',
                          (dumps(receipt, sort_keys=True), job_id, receipt['artifact_id']))
            else:
                receipt['tools'] = [receipt['tool']]
                c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',
                          (job_id, receipt['artifact_id'], receipt['created_at'], dumps(receipt, sort_keys=True)))
        return receipt

    def artifacts(self, job_id, limit=30):
        self._job(job_id)
        if not 1 <= limit <= 500:
            raise ValueError('Artifact limit must be 1..500')
        with self.connect() as c:
            rows = c.execute('SELECT body FROM artifacts WHERE job_id=? ORDER BY created_at DESC,artifact_id LIMIT ?',
                             (job_id, limit)).fetchall()
        return [json.loads(row['body']) for row in rows]

    def read_artifact(self, job_id, artifact_id):
        with self.connect() as c:
            row = c.execute('SELECT body FROM artifacts WHERE job_id=? AND artifact_id=?',
                            (self._job(job_id), artifact_id)).fetchone()
        if not row:
            raise KeyError('Unknown task artifact')
        receipt = json.loads(row['body'])
        path = Path(receipt['path']).resolve()
        if not path.is_relative_to(self.root / 'artifacts' / job_id):
            raise ValueError('Working artifact path escaped its task')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt['sha256']:
            raise ValueError('Working artifact hash mismatch')
        return {**receipt, 'text': raw.decode('utf-8')}

    def context(self, job_id, budget_chars=6000):
        """Priority-ordered navigation, with explicit pointers for omitted state."""
        if not 256 <= budget_chars <= 32000:
            raise ValueError('Working context budget must be 256..32000 characters')
        from .research_state import empty_state
        state=self.state(job_id);snapshot=self.state_snapshot(job_id)
        research=state.get('research_state') or empty_state()
        header='Working task memory (derived, not source evidence).\nFull state relative to memory root: '+snapshot['relative_path']+'\n'
        # A long user request must not crowd unresolved conflicts out of memory.
        goal=state['goal'];goal_preview=goal if len(goal)<=300 else goal[:300]+' [Read original request.]'
        fields=[('Goal','goal',goal_preview),('Cutoff/mode','time_scope',str(state.get('cutoff'))+' / '+state.get('mode','observed'))]
        critical=[row for row in research.get('unknowns',[]) if row.get('status') != 'superseded']
        # A single long failure must not hide every later correction. Put the
        # full target index first; issue details remain in immutable artifacts.
        critical_limit=min(3600,max(900,budget_chars//2))
        critical_view=_issue_navigation(critical,critical_limit) if critical else []
        fields += [('Unresolved tool/review issues','research_unknowns',critical_view),
            ('Conflicts to preserve','conflicts',[*state.get('conflicts',[]),*research.get('conflicts',[])]),
            ('Unknowns','unknowns',state.get('unknowns',[])),
            ('Saved publication receipts','publications',research.get('publications',[])),
            ('Check receipts (not truth certificates)','checks',research.get('checks',[])[-8:]),
            ('Source pointers; inspect originals','sources',[{k:r[k] for k in ('evidence_id','source_status','artifact_id','published_at','retrieved_at','observed_at','period_end') if k in r} for r in research.get('sources',[])[-8:]]),
            ('Explicit unverified research notes','derived_notes',research.get('derived_notes',[])[-6:]),
            ('Deferred checks','deferred_checks',state.get('deferred_checks',[])),
            ('Plan','plan',state.get('plan',[])),('Todos','todos',state.get('todos',[])),
            ('Evidence-linked notes (not automatic verification)','facts',state.get('facts',[])),
            ('Active artifacts','active_artifacts',state.get('active_artifacts',[])[-8:])]
        artifacts=self.artifacts(job_id,limit=4)
        fields.append(('Recent saved outputs','artifacts',[{k:r[k] for k in ('artifact_id','tool','path','bytes','evidence_ids')} for r in artifacts]))
        counts={key:len(research.get(key,[])) for key in ('sources','checks','unknowns','conflicts','publications','derived_notes')}
        summary='Persisted research counts: '+', '.join(k+'='+str(v) for k,v in counts.items() if v)+'.\n' if any(counts.values()) else ''
        reserve='[View omits state; read full revision file before resolving or citing omitted material.]'
        # Keep the state reference intact under the minimum context budget.
        pieces=[header];spent=len(header);omitted=[];truncated_fields=[]
        if spent+len(summary)+len(reserve)+1<=budget_chars:
            pieces.append(summary);spent+=len(summary)
        elif summary:omitted.append('research_counts')
        for label,key,value in fields:
            if not value:continue
            text=value if isinstance(value,str) else dumps(value)
            if key=='research_unknowns' and (critical_view.get('omitted_item_count')
                    or any('[Preview;' in str(item) for item in critical_view.get('items',[]))):
                truncated_fields.append(key)
            if key!='research_unknowns' and len(text)>1600:
                truncated_fields.append(key)
                if isinstance(value,list):
                    selected=[]
                    for item in value:
                        if len(dumps([*selected,item]))>1450:break
                        selected.append(item)
                    text=dumps(selected)+' ['+str(len(value)-len(selected))+' whole items omitted; read full state.]'
                else:
                    text='[Long field omitted; read full state.]'
            line=label+': '+text+'\n'
            if spent+len(line)+len(reserve)+1<=budget_chars:
                pieces.append(line);spent+=len(line)
            else:omitted.append(key)
        if len(goal)>300:truncated_fields.append('goal')
        # References are appended before any padding. No blind cut through a
        # conflict pair, numeric field, condition or review-rejection message.
        if omitted or truncated_fields:
            marker=reserve
            if spent+len(marker)>budget_chars:
                pieces=[header];spent=len(header)
            pieces.append(marker)
        text=''.join(pieces)
        if len(text)>budget_chars:
            # Extremely long valid job IDs can leave only a compact state pointer.
            text='Derived memory; read full state: '+snapshot['relative_path']+'\n[Other state omitted.]'
        return {'text':text,'characters':len(text),'truncated':bool(omitted or truncated_fields),
            'field_truncations':truncated_fields,'omitted_fields':omitted,
            'state_revision':state['revision'],'state_file':snapshot['path'],
            'state_sha256':snapshot['sha256'],'research_counts':counts,
            'artifact_count_in_view':len(artifacts),'estimated_tokens':(len(text.encode('utf-8'))+2)//3,
            'token_estimate_scope':'UTF-8 bytes/3 heuristic, not provider token usage',
            'scope':'Derived priority view; omitted state remains in a complete revision snapshot.'}

    def compact_messages(self, job_id, messages, *, max_tool_chars=2500,
                         keep_groups=6, max_history_chars=24000):
        """Return a shorter protocol-valid view, preserving full originals in artifacts.

        Whole assistant/tool groups are kept together. System messages, the last
        human request and tool-call arguments are protected; if these alone exceed
        the target, report the excess rather than silently altering instructions.
        """
        if max_tool_chars < 256 or keep_groups < 2 or max_history_chars < 1000:
            raise ValueError('Invalid working-message compaction bounds')
        self.state(job_id)
        compacted = []
        archived = []
        for message in messages:
            if getattr(message, 'type', None) == 'tool' and len(_serial(message.content)) > max_tool_chars:
                receipt = self.record_tool(job_id, getattr(message, 'name', None) or 'tool', message.content)
                content = self.redact(_serial(message.content))
                preview = content[:max_tool_chars//2] + '\n...\n' + content[-max_tool_chars//2:]
                replacement = ('Complete redacted tool output stored in task artifact ' + receipt['artifact_id'] +
                    ' at ' + receipt['path'] + ' (SHA256 ' + receipt['sha256'] + ').\n' +
                    preview + '\n[Preview only. Read the full artifact and original evidence before citing omitted details.]')
                compacted.append(message.model_copy(update={'content': replacement}))
                archived.append(receipt['artifact_id'])
            else:
                compacted.append(message)
        before = len(_serial([message_to_dict(m) for m in messages]))
        size = lambda rows: len(_serial([message_to_dict(m) for m in rows]))
        if size(compacted) > max_history_chars:
            groups = []
            for i, message in enumerate(compacted):
                if getattr(message, 'type', None) == 'tool' and groups and getattr(compacted[groups[-1][0]], 'type', None) == 'ai':
                    groups[-1].append(i)
                else:
                    groups.append([i])
            required = {i for i, m in enumerate(compacted) if getattr(m, 'type', None) == 'system'}
            humans = [i for i, m in enumerate(compacted) if getattr(m, 'type', None) == 'human']
            if humans:
                required.add(humans[-1])
            required.update(i for group in groups[-keep_groups:] for i in group)
            removed = [m for i, m in enumerate(messages) if i not in required]
            if removed:
                receipt = self.record_tool(job_id, 'earlier_conversation', [message_to_dict(m) for m in removed])
                archived.append(receipt['artifact_id'])
                notice = HumanMessage(content='Earlier conversation is archived at ' + receipt['path'] +
                    ' (SHA256 ' + receipt['sha256'] + '). Use current task memory and saved source IDs; reread full originals when needed. This is a context view, not deletion of checkpoints.')
                compacted = [notice, *[m for i, m in enumerate(compacted) if i in required]]
        after = size(compacted)
        return {'messages': compacted, 'before_characters': before, 'after_characters': after,
            'archived_artifact_ids': list(dict.fromkeys(archived)),
            'target_characters': max_history_chars, 'protected_content_exceeds_target': after > max_history_chars,
            'scope': 'Complete redacted artifacts, bounded tool previews and complete protocol groups; protected instructions/arguments can exceed the soft history target.'}
