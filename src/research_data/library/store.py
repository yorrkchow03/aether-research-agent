"""Typed SQLite/FTS catalog over immutable public-source objects.

Provider timestamps and first local acquisition are deliberately separate.
Opening an application does not capture data or refresh an accepted snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager,closing
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research_data.http import dumps, utcnow
from research_data.normalize import iso, timestamp
from .time import timestamp

WINDOW_START = '2026-09-26T00:00:00Z'
WINDOW_END = '2026-10-26T00:00:00Z'


def digest(value):
    return hashlib.sha256(dumps(value, sort_keys=True).encode()).hexdigest()


def instant(value):
    return timestamp(value).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class ResearchLibrary:
    def __init__(self, root, *, create=True, window_start=None, window_end=None):
        self.root = Path(root).resolve()
        self.path = self.root / 'research.sqlite'
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / 'objects').mkdir(exist_ok=True)
            with self.connect() as c:
                c.executescript('''
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(source_id TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS objects(sha256 TEXT PRIMARY KEY,bytes INTEGER NOT NULL,path TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS captures(capture_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,url TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,status INTEGER,sha256 TEXT,error TEXT,headers TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS capture_url ON captures(url,retrieved_at);
                CREATE TABLE IF NOT EXISTS datasets(dataset_id TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(record_id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,period_end TEXT NOT NULL,known_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,capture_id TEXT NOT NULL,body TEXT NOT NULL,
                    UNIQUE(dataset_id,observed_at,content_hash));
                CREATE INDEX IF NOT EXISTS observation_time ON observations(dataset_id,observed_at,known_at);
                CREATE TABLE IF NOT EXISTS observation_captures(record_id TEXT NOT NULL,capture_id TEXT NOT NULL,
                    PRIMARY KEY(record_id,capture_id));
                CREATE TABLE IF NOT EXISTS documents(document_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,url TEXT NOT NULL,kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS document_versions(record_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,published_at TEXT,known_at TEXT NOT NULL,capture_id TEXT NOT NULL,
                    body TEXT NOT NULL,UNIQUE(document_id,content_hash));
                CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(record_id UNINDEXED,title,text);
                CREATE TABLE IF NOT EXISTS discoveries(source_id TEXT NOT NULL,url TEXT NOT NULL,published_at TEXT,
                    status TEXT NOT NULL,reason TEXT,body TEXT NOT NULL,PRIMARY KEY(source_id,url));
                CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,status TEXT NOT NULL,body TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS legacy(record_id TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_outputs(output_id TEXT PRIMARY KEY,kind TEXT NOT NULL,created_at TEXT NOT NULL,
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS output_citations(output_id TEXT NOT NULL,record_id TEXT NOT NULL,
                    PRIMARY KEY(output_id,record_id));
                CREATE VIRTUAL TABLE IF NOT EXISTS output_fts USING fts5(output_id UNINDEXED,text);
                CREATE TABLE IF NOT EXISTS output_claims(output_id TEXT NOT NULL,claim_path TEXT NOT NULL,body TEXT NOT NULL,
                    PRIMARY KEY(output_id,claim_path));
                CREATE TABLE IF NOT EXISTS claim_citations(output_id TEXT NOT NULL,claim_path TEXT NOT NULL,record_id TEXT NOT NULL,
                    PRIMARY KEY(output_id,claim_path,record_id));
                CREATE TABLE IF NOT EXISTS relations(subject TEXT NOT NULL,predicate TEXT NOT NULL,object TEXT NOT NULL,
                    body TEXT NOT NULL,PRIMARY KEY(subject,predicate,object));
                ''')
                existing=dict(c.execute('SELECT key,value FROM settings'))
                chosen_start=existing.get('window_start',window_start or WINDOW_START)
                chosen_end=existing.get('window_end',window_end or WINDOW_END)
                self._validate_window(chosen_start,chosen_end)
                for key, value in [('window_start', chosen_start), ('window_end', chosen_end), ('schema', 'aether-library-v1')]:
                    previous = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                    if previous and previous[0] != value:
                        raise ValueError('Library window/schema cannot be silently changed')
                    c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (key,value))
        with self.connect(readonly=True) as c:
            settings=dict(c.execute('SELECT key,value FROM settings'))
        self.window_start=settings['window_start'];self.window_end=settings['window_end']
        self._validate_window(self.window_start,self.window_end)
        if window_start is not None and timestamp(window_start)!=timestamp(self.window_start):
            raise ValueError('Library window cannot be silently changed')
        if window_end is not None and timestamp(window_end)!=timestamp(self.window_end):
            raise ValueError('Library window cannot be silently changed')
        self.start=timestamp(self.window_start);self.end=timestamp(self.window_end)
        self.days=(self.end-self.start).days;self.hours=self.days*24

    @staticmethod
    def _validate_window(start,end):
        start=timestamp(start);end=timestamp(end)
        if end<=start or any((x.hour,x.minute,x.second,x.microsecond)!=(0,0,0,0) for x in (start,end)):
            raise ValueError('Library requires increasing closed UTC-day boundaries')

    @contextmanager
    def connect(self, readonly=False):
        c = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=30) if readonly else sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        if not readonly:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def register_source(self, source_id, **metadata):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (source_id,dumps(metadata,sort_keys=True)))

    def register_dataset(self, dataset_id, **metadata):
        metadata = {'dataset_id':dataset_id, 'required':True, **metadata}
        with self.connect() as c:
            previous = c.execute('SELECT body FROM datasets WHERE dataset_id=?',(dataset_id,)).fetchone()
            if previous and previous[0] != dumps(metadata,sort_keys=True):
                raise ValueError('Dataset contract changed; use a versioned dataset identity')
            c.execute('INSERT OR IGNORE INTO datasets VALUES(?,?)',(dataset_id,dumps(metadata,sort_keys=True)))

    def object(self, raw):
        sha = hashlib.sha256(raw).hexdigest()
        path = self.root/'objects'/sha[:2]/(sha+'.bin')
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
                raise ValueError('Raw object integrity mismatch')
        else:
            import uuid
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temporary.write_bytes(raw);temporary.replace(path)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?)',(sha,len(raw),str(path.relative_to(self.root))))
        return sha

    def capture(self, source_id, url, raw=b'', *, status=200, retrieved_at=None, headers=None, error=None):
        acquired=instant(retrieved_at or utcnow());sha=self.object(raw) if raw else None
        cid='capture-'+digest([source_id,url,sha,acquired,status,error])[:24]
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO captures VALUES(?,?,?,?,?,?,?,?)',
                (cid,source_id,url,acquired,status,sha,error,dumps(headers or {})))
        return cid

    def capture_info(self, capture_id):
        with self.connect(readonly=True) as c:
            row=c.execute('SELECT c.*,o.path FROM captures c LEFT JOIN objects o USING(sha256) WHERE capture_id=?',(capture_id,)).fetchone()
        if not row:raise KeyError('Unknown capture')
        return dict(row)

    def raw(self, capture_id):
        row=self.capture_info(capture_id)
        if not row['path']:raise ValueError('Capture has no source body')
        path=(self.root/row['path']).resolve()
        if not path.is_relative_to(self.root/'objects'):raise ValueError('Invalid object path')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=row['sha256']:raise ValueError('Source hash mismatch')
        return body

    def cached(self,url):
        with self.connect(readonly=True) as c:
            row=c.execute('SELECT capture_id FROM captures WHERE url=? AND status=200 AND error IS NULL ORDER BY retrieved_at DESC LIMIT 1',(url,)).fetchone()
        return row[0] if row else None

    def put(self, dataset_id, observed_at, values, capture_id, *, period_end=None, input_ids=None, metadata=None):
        observed=instant(observed_at);end=instant(period_end or observed)
        if timestamp(end)<timestamp(observed):raise ValueError('Negative observation interval')
        with self.connect(readonly=True) as c:
            contract=c.execute('SELECT body FROM datasets WHERE dataset_id=?',(dataset_id,)).fetchone()
        if not contract:raise KeyError('Register dataset first')
        contract=json.loads(contract[0]);normal={}
        for key,value in values.items():
            if value is None:normal[key]=None;continue
            try:number=Decimal(str(value))
            except InvalidOperation:raise ValueError('Numeric observations require numbers') from None
            if isinstance(value,bool) or not number.is_finite():raise ValueError('Nonfinite/boolean observation')
            normal[key]=str(number)
        if set(normal)-set(contract['units']):raise ValueError('Unspecified observation units')
        cap=self.capture_info(capture_id)
        if cap['status']!=200 or cap['error'] or not cap['sha256']:raise ValueError('Failed capture cannot establish observations')
        content=digest([dataset_id,observed,end,normal,metadata or {},input_ids or []])
        rid='live-'+content[:24]
        row={'record_id':rid,'dataset_id':dataset_id,'asset':contract.get('asset'),
            'observed_at':observed,'period_end':end,'retrieved_at':cap['retrieved_at'],'first_seen_at':cap['retrieved_at'],
            'available_at':cap['retrieved_at'],'published_at':None,'values':normal,'units':contract['units'],
            'provider':contract['provider'],'scope':contract['scope'],'sampling':contract['frequency'],
            'source':{'source_id':capture_id,'name':cap['source_id'],'url':cap['url'],'sha256':cap['sha256'],'retrieved_at':cap['retrieved_at']},
            'quality_flags':['retrospective_current_retrieval','not_authenticated_historical_availability'],
            'input_record_ids':input_ids or [],'library_metadata':metadata or {},'library_capture_id':capture_id}
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?)',
                (rid,dataset_id,observed,end,cap['retrieved_at'],content,capture_id,dumps(row,sort_keys=True)))
            c.execute('INSERT OR IGNORE INTO observation_captures VALUES(?,?)',(rid,capture_id))
        return rid

    def discover(self, source_id, url, *, published_at=None, status='discovered', reason=None, **metadata):
        if published_at:
            published_at=instant(published_at)
        with self.connect() as c:
            previous=c.execute('SELECT status FROM discoveries WHERE source_id=? AND url=?',(source_id,url)).fetchone()
            if previous and previous[0]=='saved' and status=='discovered':return
            c.execute('INSERT OR REPLACE INTO discoveries VALUES(?,?,?,?,?,?)',
                (source_id,url,published_at,status,reason,dumps(metadata,sort_keys=True)))

    def put_document(self, source_id, url, title, text, capture_id, *, published_at=None, kind='article', assets=None, metadata=None):
        if not text.strip():raise ValueError('Empty document is not a full article')
        if published_at:
            published_at=instant(published_at)
            if timestamp(published_at).year<2000:raise ValueError('Invalid modern publication date')
        cap=self.capture_info(capture_id)
        if cap['status']!=200 or cap['error']:raise ValueError('Failed article capture')
        did='document-'+digest([source_id,url,kind])[:24]
        content=digest([title,text,published_at,metadata or {}]);rid='page-'+digest([did,content])[:24]
        row={'record_id':rid,'document_id':did,'dataset_id':'library.documents.'+source_id,'title':title,'text':text,
            'url':url,'source_kind':kind,'asset':(assets or ['ETH'])[0],'assets':assets or ['ETH'],
            'published_at':published_at,'observed_at':published_at or cap['retrieved_at'],'retrieved_at':cap['retrieved_at'],
            'available_at':cap['retrieved_at'],'first_seen_at':cap['retrieved_at'],'values':{},'units':{},
            'source':{'source_id':capture_id,'name':source_id,'url':url,'sha256':cap['sha256'],'retrieved_at':cap['retrieved_at']},
            'quality_flags':['captured_original_page','publication_time_claimed_by_source_not_independently_authenticated'],
            'scope':'Publisher material captured now; not authenticated historical version','library_capture_id':capture_id,
            'library_metadata':metadata or {}}
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?)',(did,source_id,url,kind))
            existing=c.execute('SELECT record_id FROM document_versions WHERE document_id=? AND content_hash=?',(did,content)).fetchone()
            if existing:return existing[0]
            c.execute('INSERT INTO document_versions VALUES(?,?,?,?,?,?,?)',(rid,did,content,published_at,cap['retrieved_at'],capture_id,dumps(row,sort_keys=True)))
            c.execute('INSERT INTO document_fts VALUES(?,?,?)',(rid,title,text))
        self.discover(source_id,url,published_at=published_at,status='saved',record_id=rid)
        return rid

    def evidence(self, record_id):
        with self.connect(readonly=True) as c:
            return self._evidence_in_connection(c,record_id,{})

    def evidence_many(self, record_ids):
        """Same source checks as evidence(), sharing one read-only connection.

        A raw object shared by many forum posts is hashed once per unchanged
        file state in this call. No integrity result survives the call.
        """
        ids=list(dict.fromkeys(record_ids))
        if len(ids)>10000:raise ValueError('Bound bulk evidence to10000IDs')
        records={};errors={};verified={}
        with self.connect(readonly=True) as c:
            for rid in ids:
                try:records[rid]=self._evidence_in_connection(c,rid,verified)
                except (ValueError,KeyError,OSError) as exc:errors[rid]=type(exc).__name__
        return {'records':records,'errors':errors}

    def _evidence_in_connection(self,c,record_id,verified):
        row=None
        for table in ['observations','document_versions','legacy']:
            row=c.execute(f'SELECT body FROM {table} WHERE record_id=?',(record_id,)).fetchone()
            if row:break
        if not row:raise KeyError('Unknown library evidence ID')
        result=json.loads(row[0])
        excluded=[r[0] for r in c.execute("SELECT object FROM relations WHERE predicate='excluded_from_research' AND subject IN (?,?)",(record_id,result.get('document_id','')))]
        if excluded:result['library_exclusion_reasons']=excluded
        if result.get('library_capture_id'):
            cap=c.execute('SELECT c.*,o.path FROM captures c LEFT JOIN objects o USING(sha256) WHERE capture_id=?',(result['library_capture_id'],)).fetchone()
            if not cap:raise KeyError('Unknown capture')
            if not cap['path']:raise ValueError('Capture has no source body')
            path=(self.root/cap['path']).resolve()
            if not path.is_relative_to(self.root/'objects'):raise ValueError('Invalid object path')
            self._verify_bulk_file(path,cap['sha256'],verified)
            result['raw_path']=str(path)
        elif result.get('raw_path') and result.get('source',{}).get('sha256'):
            sha=result['source']['sha256'];portable=self.root/'objects'/sha[:2]/(sha+'.bin')
            path=portable if portable.is_file() else Path(result['raw_path'])
            self._verify_bulk_file(path,sha,verified)
            result['raw_path']=str(path)
        if result.get('library_recipe_object'):
            result['recipe_path']=str(self.root/'objects'/result['library_recipe_object'][:2]/(result['library_recipe_object']+'.bin'))
        return result

    @staticmethod
    def _verify_bulk_file(path,expected,verified):
        stat=path.stat();key=(str(path),expected,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        if key not in verified:
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Source hash mismatch')
            verified[key]=True

    def series(self, dataset_id, *, start=None, end=None, mode='observed', known_by=None, limit=2000, offset=0):
        if mode not in ('observed','strict'):raise ValueError('Invalid availability mode')
        if not 1<=limit<=10000 or offset<0:raise ValueError('Invalid pagination')
        start=instant(start or self.window_start);end=instant(end or self.window_end);cutoff=instant(known_by or end)
        with self.connect(readonly=True) as c:
            contract=c.execute('SELECT body FROM datasets WHERE dataset_id=?',(dataset_id,)).fetchone()
            if not contract:raise KeyError('Unknown library dataset')
            params=[dataset_id,start,end]
            extra=' AND known_at<=?' if mode=='strict' else ''
            if extra:params.append(cutoff)
            rows=c.execute("SELECT * FROM observations WHERE dataset_id=? AND observed_at>=? AND observed_at<? AND NOT EXISTS (SELECT 1 FROM relations x WHERE x.subject=observations.record_id AND x.predicate='excluded_from_research')"+extra+' ORDER BY observed_at,known_at DESC,rowid DESC',params).fetchall()
        unique={}
        for row in rows:
            if row['period_end']<=end and row['observed_at'] not in unique:unique[row['observed_at']]=json.loads(row['body'])
        selected=list(unique.values())[offset:offset+limit]
        return {'dataset':json.loads(contract[0]),'start':start,'end':end,'mode':mode,'known_by':cutoff if mode=='strict' else None,
            'total':len(unique),'records':selected,'next_offset':offset+limit if offset+limit<len(unique) else None,
            'scope':'Retrospective observations; acquisition time does not authenticate original historical availability' if mode=='observed' else 'Only versions locally acquired by known_by'}

    def search_documents(self,query='',*,asset=None,start=None,end=None,kind=None,source=None,mode='observed',known_by=None,limit=30,offset=0):
        if mode not in ('observed','strict') or not 1<=limit<=100 or offset<0:raise ValueError('Invalid document query')
        import re
        start=start or self.window_start;end=end or self.window_end
        terms=re.findall(r'[\w]+',query)[:20]
        where=['v.published_at>=?','v.published_at<?'];params=[instant(start),instant(end)];join=''
        exclusion="NOT EXISTS (SELECT 1 FROM relations x WHERE x.subject=d.document_id AND x.predicate='excluded_from_research'"
        if mode=='strict':
            exclusion+=" AND COALESCE(json_extract(x.body,'$.effective_known_at'),v.known_at)<=?";params.append(instant(known_by or end))
        where.append(exclusion+')')
        if asset:where.append("json_extract(v.body,'$.assets') LIKE ?");params.append('%"'+asset.upper()+'"%')
        if kind:where.append('d.kind=?');params.append(kind)
        if source:where.append('d.source_id=?');params.append(source)
        if mode=='strict':where.append('v.known_at<=?');params.append(instant(known_by or end))
        latest="NOT EXISTS (SELECT 1 FROM document_versions newer WHERE newer.document_id=v.document_id AND (newer.known_at>v.known_at OR (newer.known_at=v.known_at AND newer.rowid>v.rowid))"
        latest+=' AND newer.published_at>=? AND newer.published_at<?'
        params.extend([instant(start),instant(end)])
        if mode=='strict':latest+=' AND newer.known_at<=?';params.append(instant(known_by or end))
        where.append(latest+')')
        with self.connect(readonly=True) as c:
            rows=c.execute('SELECT v.body,v.document_id FROM document_versions v JOIN documents d USING(document_id)'+join+' WHERE '+' AND '.join(where)+' ORDER BY v.published_at DESC,v.known_at DESC',params).fetchall()
        unique={}
        for row in rows:
            unique.setdefault(row['document_id'],json.loads(row['body']))
        eligible=list(unique.values());corpus_count=len(eligible)
        if terms and eligible:
            # Rank only eligible latest versions. Global FTS statistics include
            # later captures/superseded text even when SQL filters their rows.
            # This small local snapshot preserves point-in-time corpus statistics
            # and keeps relevant older articles in the bounded memory pool.
            by_id={r['record_id']:r for r in eligible}
            with closing(sqlite3.connect(':memory:')) as ranking:
                ranking.execute('PRAGMA temp_store=MEMORY')
                ranking.execute('CREATE VIRTUAL TABLE eligible_fts USING fts5(record_id UNINDEXED,title,text)')
                ranking.executemany('INSERT INTO eligible_fts VALUES(?,?,?)',((r['record_id'],r['title'],r['text']) for r in eligible))
                scores=dict(ranking.execute('SELECT record_id,bm25(eligible_fts,0,4,1) FROM eligible_fts WHERE eligible_fts MATCH ?',(' OR '.join('"'+t+'"' for t in terms),)))
            eligible=[r for r in eligible if r['record_id'] in scores]
            # Stable recency breaks equal BM25 ties; no-query browsing stays recent.
            eligible.sort(key=lambda r:scores[r['record_id']])
        results=eligible[offset:offset+limit]
        return {'total':len(eligible),'results':[{**{k:v for k,v in row.items() if k!='text'},'excerpt':row['text'][:1800]} for row in results],
            'next_offset':offset+limit if offset+limit<len(eligible) else None,'mode':mode,'publication_time_verified':False,
            'retrieval':{'ranking':'bm25_eligible_versions' if terms else 'latest_publication','eligible_corpus_documents':corpus_count,'statistics_scope':'Only source/time/availability-filtered latest eligible versions; no future or superseded corpus statistics'}}

    def contains(self,record_id):
        with self.connect(readonly=True) as c:
            return any(c.execute(f'SELECT 1 FROM {table} WHERE record_id=?',(record_id,)).fetchone() for table in ['observations','document_versions','legacy'])

    def exclusion_reasons(self,record_id):
        with self.connect(readonly=True) as c:
            return [r[0] for r in c.execute("SELECT object FROM relations WHERE predicate='excluded_from_research' AND (subject=? OR subject IN (SELECT document_id FROM document_versions WHERE record_id=?))",(record_id,record_id))]

    def record_links(self,record_id,*,known_by=None,limit=30,offset=0):
        if not 1<=limit<=200 or offset<0:raise ValueError('Invalid relationship pagination')
        with self.connect(readonly=True) as c:
            where='(subject=? OR object=?)';params=[record_id,record_id]
            if known_by:
                where+=" AND predicate IN ('content_revision_of','metadata_revision_of','identical_normalized_text','report_attachment_of') AND subject IN (SELECT record_id FROM document_versions WHERE known_at<=?) AND object IN (SELECT record_id FROM document_versions WHERE known_at<=?)"
                params.extend([instant(known_by)]*2)
            rows=c.execute('SELECT * FROM relations WHERE '+where+' ORDER BY predicate,subject,object',params).fetchall()
        return {'total':len(rows),'results':[{**dict(r),'body':json.loads(r['body'])} for r in rows[offset:offset+limit]],'next_offset':offset+limit if offset+limit<len(rows) else None}

    def search_outputs(self,query='',*,start=None,end=None,limit=8,offset=0):
        import re
        start=start or self.window_start;end=end or self.window_end
        if not 1<=limit<=100 or offset<0:raise ValueError('Invalid output pagination')
        terms=re.findall(r'\w+',query)[:20];join='';where=['o.created_at>=?','o.created_at<?'];params=[instant(start),instant(end)]
        if terms:
            join=' JOIN output_fts f ON f.output_id=o.output_id';where.append('output_fts MATCH ?');params.append(' OR '.join('"'+t+'"' for t in terms))
        with self.connect(readonly=True) as c:
            rows=c.execute('SELECT o.* FROM research_outputs o'+join+' WHERE '+' AND '.join(where)+' ORDER BY o.created_at DESC',params).fetchall()
            citations={r['output_id']:[] for r in rows}
            for r in c.execute('SELECT * FROM output_citations'):
                if r['output_id'] in citations:citations[r['output_id']].append(r['record_id'])
        results=[]
        for row in rows[offset:offset+limit]:
            body=json.loads(row['body']);content=body.get('report') or body.get('draft') or {}
            summary=content.get('judgment_reason') or body.get('rendered') or dumps(content)
            results.append({'output_id':row['output_id'],'report_id' if row['kind']=='reports' else 'answer_id':row['output_id'],'kind':'report' if row['kind']=='reports' else 'answer','created_at':row['created_at'],'summary':summary[:1200],
                'source_evidence_ids':citations[row['output_id']], 'scope':'Actual saved earlier research. Read underlying evidence; this is not a new factual source or current portfolio context.'})
        return {'total':len(rows),'results':results,'next_offset':offset+limit if offset+limit<len(rows) else None}

    def output(self,output_id):
        with self.connect(readonly=True) as c:
            row=c.execute('SELECT * FROM research_outputs WHERE output_id=?',(output_id,)).fetchone()
            if not row:raise KeyError('Unknown saved research output')
            claims=[{'claim_path':r['claim_path'],'claim':json.loads(r['body'])} for r in c.execute('SELECT * FROM output_claims WHERE output_id=?',(output_id,))]
            links=[dict(r) for r in c.execute('SELECT * FROM claim_citations WHERE output_id=?',(output_id,))]
            citations=[r[0] for r in c.execute('SELECT record_id FROM output_citations WHERE output_id=?',(output_id,))]
            versions=[dict(r) for r in c.execute("SELECT * FROM relations WHERE subject=? AND predicate LIKE 'citation_version:%'",(output_id,))]
        return {'output_id':output_id,'kind':row['kind'],'created_at':row['created_at'],'body':json.loads(row['body']),'claims':claims,'claim_citations':links,'source_evidence_ids':citations,'citation_versions':versions}

    def job(self,job_id,status,**details):
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)',(job_id,status,dumps(details,sort_keys=True),utcnow()))

    def datasets(self):
        with self.connect(readonly=True) as c:
            return [json.loads(row[0]) for row in c.execute('SELECT body FROM datasets ORDER BY dataset_id')]

    def coverage(self):
        output=[]
        with self.connect(readonly=True) as c:
            jobs={row['job_id']:{'status':row['status'],**json.loads(row['body'])} for row in c.execute('SELECT * FROM jobs')}
            for contract in self.datasets():
                packet=self.series(contract['dataset_id'],end=utcnow() if contract['frequency']=='snapshot' else self.window_end,limit=10000)
                rows=packet['records'];nulls={key:sum(row['values'].get(key) is None for row in rows) for key in contract['units']}
                frequency=contract['frequency'];expected=self.hours if frequency=='1h' else self.days if frequency=='1d' else contract.get('expected')
                count=len(rows)
                complete=(expected is not None and count==expected and not any(nulls.values()))
                if frequency=='snapshot':complete=count>0
                job=jobs.get(contract['dataset_id'],{})
                if frequency=='calendar':
                    complete=bool(job.get('source_range_complete')) and count==job.get('expected_observations')
                if frequency in ('business_day','settlement','provider_daily','batch'):
                    complete=bool(job.get('source_range_complete')) and count>0 and not any(nulls.values())
                if frequency=='provider_daily':complete=complete and len({r['observed_at'][:10] for r in rows})==self.days
                if job.get('expected_source_dates'):
                    expected_dates=set(job['expected_source_dates'])
                    seen_dates={r['observed_at'][:10] for r in rows}
                    pending=set(job.get('pending_publication_dates',[]))
                    complete=complete and seen_dates==expected_dates-pending
                status='complete' if complete else 'unresolved_missing'
                output.append({**contract,'records':count,'expected':expected,'null_counts':nulls,'status':status,
                    'availability_state':'legitimate_no_observation' if frequency=='calendar' and complete and count==0 else 'awaiting_publication' if complete and job.get('pending_publication_dates') else status,
                    'pending_publication_dates':job.get('pending_publication_dates',[]),'nonobservation_dates':job.get('nonobservation_dates',[]),'job':job})
            text=[dict(row) for row in c.execute('SELECT source_id,status,COUNT(*) AS count FROM discoveries GROUP BY source_id,status')]
        required=[r for r in output if r.get('required')]
        return {'window_start':self.window_start,'window_end':self.window_end,'datasets':output,'text_discoveries':text,
            'core_complete':bool(required) and all(r['status']=='complete' for r in required),'jobs':jobs,
            'scope':'Coverage of registered contracts; source discovery and semantic acceptance are separate gates'}

    def backup(self,destination):
        import shutil
        target=Path(destination)
        if target.resolve().is_relative_to(self.root):raise ValueError('Backup destination must be outside the working library')
        if target.exists():raise ValueError('Backup destination exists')
        target.mkdir(parents=True)
        with self.connect(readonly=True) as source:
            dest=sqlite3.connect(target/'research.sqlite');source.backup(dest);dest.close()
        shutil.copytree(self.root/'objects',target/'objects')
        manifest={str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}
        (target/'BACKUP_MANIFEST.json').write_text(dumps(manifest,indent=2)+'\n')
        return manifest
