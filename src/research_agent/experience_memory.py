"""Source-linked process corrections, never learned market laws or user facts."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from research_data.http import dumps, utcnow
from research_data.library.time import timestamp


class ExperienceMemory:
    def __init__(self, root, *, redact=None, evidence_lookup=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'experience.sqlite'
        self.redact = redact or (lambda value: str(value))
        self.evidence_lookup = evidence_lookup
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS corrections(correction_id TEXT PRIMARY KEY,known_at TEXT NOT NULL,body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS experience_events(event_id TEXT PRIMARY KEY,correction_id TEXT NOT NULL,known_at TEXT NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL)')
            c.execute('CREATE INDEX IF NOT EXISTS experience_event_target ON experience_events(correction_id,known_at)')
            for table in ('corrections','experience_events'):
                for operation in ('UPDATE','DELETE'):
                    c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Experience memory is append-only'); END")

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

    def record_correction(self, topic, mistake, correction, *, receipts, evidence_ids=None,
                          applicability=None, affected_outputs=None, kind='process'):
        if kind not in ('process', 'source_correction'):
            raise ValueError('Experience kind must be process or source_correction')
        if not all(isinstance(v, str) and 1 <= len(v) <= 4000 for v in (topic, mistake, correction)):
            raise ValueError('Correction text requires bounded nonempty strings')
        if not 1 <= len(receipts) <= 12:
            raise ValueError('A correction requires 1..12 actual saved source/review receipts')
        ids = list(dict.fromkeys(evidence_ids or []))
        if kind == 'source_correction' and (not ids or not self.evidence_lookup):
            raise ValueError('Source corrections require resolvable original evidence IDs')
        if self.evidence_lookup:
            for rid in ids:
                self.evidence_lookup(rid)
        saved = self._save_receipts(receipts)
        identity = {'topic': topic, 'mistake': mistake, 'correction': correction,
                    'receipt_hashes': sorted(r['sha256'] for r in saved), 'evidence_ids': ids,
                    'applicability': applicability or [], 'affected_outputs': affected_outputs or [], 'kind': kind}
        identity = json.loads(self.redact(dumps(identity, sort_keys=True)))
        cid = 'correction-' + hashlib.sha256(dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        body = {**identity, 'correction_id': cid, 'known_at': utcnow(), 'receipts': saved,
            'scope': 'Evidence-linked research-process guidance, not a financial fact, user preference, prediction, or independently adjudicated market rule.',
            'verification': 'Receipt bytes/hashes checked; semantic adjudication remains separate.'}
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO corrections VALUES(?,?,?)', (cid, body['known_at'], dumps(body, sort_keys=True)))
            old = c.execute('SELECT body FROM corrections WHERE correction_id=?', (cid,)).fetchone()
        return json.loads(old['body'])

    def _save_receipts(self,receipts):
        if not isinstance(receipts,(list,tuple)) or not 1<=len(receipts)<=12:
            raise ValueError('Use 1..12 saved text receipts')
        saved=[]
        for value in receipts:
            path = Path(value).resolve()
            if path.name.startswith('.env') or path.suffix.lower() in ('.key', '.pem', '.p12'):
                raise ValueError('Credentials cannot be experience receipts')
            if not path.is_file() or path.stat().st_size > 20_000_000:
                raise ValueError('Correction receipt is missing or exceeds the bounded text size')
            original = path.read_bytes()
            try:
                text = original.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('Use an inspectable text/JSON review receipt, not an opaque database') from None
            raw = self.redact(text).encode('utf-8')
            sha = hashlib.sha256(raw).hexdigest()
            target = self.root / 'receipts' / (sha + '.txt')
            target.parent.mkdir(exist_ok=True)
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                raise ValueError('Saved correction receipt hash mismatch')
            if not target.exists():
                temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
                temporary.write_bytes(raw); temporary.replace(target)
            saved.append({'original_path': str(path), 'original_sha256': hashlib.sha256(original).hexdigest(),
                          'path': str(target), 'sha256': sha, 'redacted': raw != original})
        return saved

    def _entry(self,correction_id):
        with self.connect() as c:
            row=c.execute('SELECT body FROM corrections WHERE correction_id=?',(correction_id,)).fetchone()
        if not row:raise ValueError('Unknown correction ID')
        return json.loads(row['body'])

    @staticmethod
    def _digest(value):
        return hashlib.sha256(dumps(value,sort_keys=True).encode()).hexdigest()

    def _append_event(self,correction_id,kind,payload,receipts):
        entry=self._entry(correction_id);now=utcnow()
        if timestamp(entry['known_at'])>timestamp(now):raise ValueError('Cannot update an experience not yet known')
        identity={'correction_id':correction_id,'correction_sha256':self._digest(entry),
            'kind':kind,'payload':payload,'receipt_hashes':[r['sha256'] for r in receipts]}
        identity=json.loads(self.redact(dumps(identity,sort_keys=True)))
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            previous=c.execute('SELECT body FROM experience_events WHERE correction_id=? ORDER BY rowid DESC LIMIT 1',(correction_id,)).fetchone()
            old_event=json.loads(previous['body']) if previous else None
            if old_event and timestamp(old_event['known_at'])>timestamp(now):
                raise ValueError('A new experience event cannot be backdated before its prior event')
            if old_event and all(old_event.get(key)==value for key,value in identity.items()):
                return old_event
            identity['previous_event_id']=old_event['event_id'] if old_event else None
            eid='experience-event-'+self._digest(identity)[:24]
            body={**identity,'event_id':eid,'known_at':now,'receipts':receipts,
                'scope':'Receipt-backed experience metadata; not financial truth, causal effectiveness or independently authenticated evaluation.'}
            serialized=dumps(body,sort_keys=True)
            c.execute('INSERT OR IGNORE INTO experience_events VALUES(?,?,?,?,?)',
                (eid,correction_id,now,serialized,hashlib.sha256(serialized.encode()).hexdigest()))
            old=c.execute('SELECT body FROM experience_events WHERE event_id=?',(eid,)).fetchone()
        return json.loads(old['body'])

    def record_lifecycle(self,correction_id,*,status,reason,receipts,replacement_id=None):
        """Append a review-backed lifecycle change; original entries are immutable."""
        if status not in ('retired','invalidated','superseded','active'):
            raise ValueError('Unknown experience lifecycle status')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=2000:
            raise ValueError('A lifecycle change needs a bounded reason')
        if status=='superseded':
            if not replacement_id or replacement_id==correction_id:raise ValueError('Use a distinct existing replacement')
            replacement=self._entry(replacement_id)
            if timestamp(replacement['known_at'])>timestamp(utcnow()):raise ValueError('Replacement is not known yet')
            # Follow the current append-only replacement chain, rejecting cycles.
            visited={correction_id};cursor=replacement_id
            while cursor:
                if cursor in visited:raise ValueError('Experience replacement cycle')
                visited.add(cursor)
                with self.connect() as c:
                    rows=c.execute("SELECT body FROM experience_events WHERE correction_id=? AND json_extract(body,'$.kind')='lifecycle' ORDER BY rowid DESC",(cursor,)).fetchall()
                event=next((json.loads(r['body']) for r in rows if timestamp(json.loads(r['body'])['known_at'])<=timestamp(utcnow())),None)
                cursor=event['payload'].get('replacement_id') if event and event['payload']['status']=='superseded' else None
        elif replacement_id is not None:raise ValueError('Only superseded experience has a replacement')
        return self._append_event(correction_id,'lifecycle',{'status':status,'reason':self.redact(reason),
            'replacement_id':replacement_id},self._save_receipts(receipts))

    def record_outcome(self,correction_id,receipt,*,check_id=None):
        """Derive bounded utility from an explicit saved paired workflow check.

        This API is for the host evaluator, never a business-model tool. It does
        not accept profitability, popularity, model confidence or unpaired task
        success as a reward. A saved JSON assertion is attributed evidence, not
        cryptographic proof that the evaluator itself was correct.
        """
        entry=self._entry(correction_id)
        if entry['kind']!='process':raise ValueError('Source corrections cannot be ranked by usefulness rewards')
        saved=self._save_receipts([receipt]);data=json.loads(Path(saved[0]['path']).read_text())
        if data.get('schema')!='aether-workflow-memory-feedback-v1' or data.get('verification')!='programmatic_paired_check':
            raise ValueError('Use an inspectable paired programmatic workflow-check receipt')
        if data.get('correction_id')!=correction_id:raise ValueError('Outcome receipt targets a different experience')
        if not isinstance(data.get('task_id'),str) or not 1<=len(data['task_id'])<=160:
            raise ValueError('Outcome receipt needs the frozen task identity')
        if not isinstance(data.get('check_id'),str) or not data['check_id'] or (check_id is not None and check_id!=data['check_id']):
            raise ValueError('Outcome check identity differs')
        if data.get('check_type') not in ('numeric_units','source_binding','time_cutoff','recipe_replay','task_completion','condition_preservation'):
            raise ValueError('Outcome check must concern research-process correctness')
        flags=[data.get('with_memory_passed'),data.get('without_memory_passed')]
        if any(type(flag) is not bool for flag in flags):raise ValueError('Paired workflow outcomes must be explicit booleans')
        outcome='helpful' if flags==[True,False] else 'harmful' if flags==[False,True] else 'neutral'
        for rid in data.get('evidence_ids',[]):
            if not self.evidence_lookup:raise ValueError('Outcome evidence IDs require a source resolver')
            self.evidence_lookup(rid)
        return self._append_event(correction_id,'workflow_outcome',
            {'check_id':data['check_id'],'task_id':data['task_id'],'check_type':data['check_type'],
             'with_memory_passed':flags[0],'without_memory_passed':flags[1],'outcome':outcome},saved)

    def _valid_receipts(self,receipts):
        try:
            for receipt in receipts:
                path=Path(receipt['path']).resolve()
                if not path.is_relative_to(self.root/'receipts') or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=receipt['sha256']:
                    return False
            return bool(receipts)
        except (OSError,KeyError,TypeError,ValueError):return False

    def recall(self, query='', cutoff=None, limit=4, budget_chars=3000, *, utility_weight=0.15):
        if not 1 <= limit <= 12 or not 256 <= budget_chars <= 16000:
            raise ValueError('Invalid correction recall bounds')
        if not 0<=utility_weight<=0.15:raise ValueError('Workflow utility is limited to a small relevance adjustment')
        before = timestamp(cutoff or utcnow())
        with self.connect() as c:
            stored_events=c.execute('SELECT correction_id,known_at,body,digest FROM experience_events ORDER BY rowid').fetchall()
            candidates = [json.loads(r['body']) for r in c.execute('SELECT body FROM corrections ORDER BY known_at,correction_id')]
        # Filter first: future corrections cannot affect lexical corpus statistics.
        candidates = [r for r in candidates if timestamp(r['known_at']) <= before]
        excluded, rows = [], []
        events={};invalid_targets=set()
        for item in stored_events:
            if timestamp(item['known_at'])>before:continue
            if hashlib.sha256(item['body'].encode()).hexdigest()!=item['digest']:
                invalid_targets.add(item['correction_id'])
                excluded.append({'correction_id':item['correction_id'],'reason':'experience_event_integrity_failed'});continue
            event=json.loads(item['body'])
            if event.get('correction_id')!=item['correction_id'] or event.get('known_at')!=item['known_at']:
                invalid_targets.add(item['correction_id'])
                excluded.append({'correction_id':item['correction_id'],'reason':'experience_event_binding_mismatch'});continue
            if not self._valid_receipts(event.get('receipts',[])):
                invalid_targets.add(item['correction_id'])
                excluded.append({'correction_id':event['correction_id'],'reason':'experience_event_receipt_integrity_failed'});continue
            events.setdefault(event['correction_id'],[]).append(event)
        for row in candidates:
            if row['correction_id'] in invalid_targets:continue
            if not self._valid_receipts(row['receipts']):
                excluded.append({'correction_id': row['correction_id'], 'reason': 'saved_receipt_integrity_failed'});continue
            matching=[]
            for event in events.get(row['correction_id'],[]):
                if event['correction_sha256']==self._digest(row):matching.append(event)
                else:excluded.append({'correction_id':row['correction_id'],'reason':'experience_event_binding_mismatch'})
            lifecycle=next((event for event in reversed(matching) if event['kind']=='lifecycle'),None)
            status=lifecycle['payload']['status'] if lifecycle else 'active'
            if status!='active':
                excluded.append({'correction_id':row['correction_id'],'reason':'experience_'+status,
                    'replacement_id':lifecycle['payload'].get('replacement_id')});continue
            outcomes={}
            for event in matching:
                if event['kind']=='workflow_outcome':
                    payload=event['payload'];outcomes[(payload['task_id'],payload['check_id'])]=payload['outcome']
            helpful=sum(value=='helpful' for value in outcomes.values())
            harmful=sum(value=='harmful' for value in outcomes.values())
            neutral=sum(value=='neutral' for value in outcomes.values())
            utility=(helpful-harmful)/(4+len(outcomes)) if row['kind']=='process' else 0.0
            rows.append({**row,'lifecycle_status':status,'lifecycle_known_at':lifecycle['known_at'] if lifecycle else row['known_at'],
                'workflow_utility':{'helpful':helpful,'harmful':harmful,'neutral':neutral,'paired_checks':len(outcomes),
                    'value':utility,'scope':'Smoothed paired workflow-check feedback, not investment utility or a causal guarantee.'}})
        scores = np.ones(len(rows))
        if rows and query.strip():
            texts = [' '.join([r['topic'], r['mistake'], r['correction'], *r['applicability']]) for r in rows]
            try:
                vectors = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), max_features=8000).fit_transform([*texts, query])
                scores = (vectors[:-1] @ vectors[-1].T).toarray().ravel()
            except ValueError:
                scores = np.zeros(len(rows))
        adjusted=[float(scores[i])*(1+utility_weight*rows[i]['workflow_utility']['value']) for i in range(len(rows))]
        ranked = sorted(range(len(rows)), key=lambda i: (-adjusted[i], rows[i]['correction_id']))
        selected, snippets, spent = [], [], 0
        header = 'Prior source-linked process corrections (not market evidence or permanent trading rules):\n'
        for index in ranked:
            if query.strip() and scores[index] <= 1e-12:
                continue
            row = rows[index]
            snippet = (row['correction_id'] + ' | ' + row['topic'] + '\nApply when: ' + '; '.join(row['applicability']) +
                       '\nCorrection: ' + row['correction'] + '\nReceipts: ' + ', '.join(r['sha256'] for r in row['receipts']) + '\n')
            if len(header) + spent + len(snippet) > budget_chars:
                continue
            selected.append({**row, 'relevance_score': float(scores[index]),'selection_score':adjusted[index]})
            snippets.append(snippet); spent += len(snippet)
            if len(selected) >= limit:
                break
        return {'results': selected, 'prompt_context': header + ''.join(snippets) if snippets else '',
                'excluded': excluded, 'known_by': cutoff, 'eligible_count': len(rows),
                'characters': len(header)+spent if snippets else 0,
                'selection_policy':'lexical_relevance_with_bounded_verified_workflow_feedback','utility_weight':utility_weight,
                'scope': 'Captured correction receipts by acquisition time; no learned financial pattern or semantic correctness guarantee.'}


def seed_project_corrections(project_root, store):
    """Import already saved diagnostic incidents only; absent files stay gaps."""
    root = Path(project_root).resolve()
    baseline = root / 'local/report-baseline/monthly-v1'
    draft = baseline / 'product-traces/f8d087095dde473db7b114f3eab86251/workspace/drafts/ba67953adf5c9b0922c3eff6.json'
    report = baseline / 'product-report/reports/monthly-report.md'
    cases = [
        ('shell_and_virtual_recipe_paths', 'A shell /notes write and host absolute file-tool path were confused.',
         'Use ./notes/name.py in the terminal and /notes/name.py in virtual file tools. Check writes before registering. Session-absolute recipe paths are accepted only inside that session notes.',
         [baseline / 'product-traces/9e238ef494bb4eb5a546d67acffe0606/jobs/92a26fea2b834bae92e9b509e0ec5c65/events.jsonl'],
         ['terminal calculations', 'recipe registration', 'file writes']),
        ('prose_date_matches_original_observation', 'A draft wrote 2025-10-25 although its cited record observed_at was 2026-10-25.',
         'Check dates in prose against the exact cited observation/publication/acquisition fields; a correct numeric field does not establish the prose year.',
         [baseline / 'frozen/stock_flow/report.json', baseline / 'frozen/stock_flow/selected_evidence.json'],
         ['report dates', 'TVL observations', 'source checking']),
        ('relative_returns_and_percentage_points', 'A draft mixed relative ETH/BTC return with subtraction of two percentage returns.',
         'Relative return is (1+r_asset)/(1+r_reference)-1; percentage-point difference is 100*(r_asset-r_reference). Keep their units and windows separate, or omit an unsupported comparison.',
         [draft, report], ['return calculations', 'benchmark comparisons', 'numeric report summaries']),
        ('return_intervals_and_close_counts', 'A draft described first/last among 30 daily closes as the same 30-period rolling return.',
         'Thirty observed closes contain 29 consecutive close-to-close intervals. A 30-period rolling return needs the preceding close; cite exact endpoints and calculation lineage.',
         [draft, report], ['daily returns', 'rolling windows', 'market report calculations']),
        ('future_recheck_criteria_are_not_occurred_facts', 'An evidence review treated future invalidation criteria as claims that those events already occurred.',
         'State monitoring and invalidation as prospective conditions, preserve their semantic role, and independently source any embedded present/past factual assertion. A future condition is not proof it happened.',
         [baseline / 'product-traces/f8d087095dde473db7b114f3eab86251/workspace/reviews/attempts/review-3efafdbf687b5fd27d78fb31-attempt-3.json', draft],
         ['conditional advice', 'review feedback', 'future monitoring criteria']),
    ]
    result = {'saved': [], 'missing': []}
    for topic, mistake, correction, receipts, applicability in cases:
        missing = [str(p.relative_to(root)) for p in receipts if not p.exists()]
        if missing:
            result['missing'].append({'topic': topic, 'receipts': missing})
            continue
        saved = store.record_correction(topic, mistake, correction, receipts=receipts, applicability=applicability)
        result['saved'].append(saved['correction_id'])
    return result
