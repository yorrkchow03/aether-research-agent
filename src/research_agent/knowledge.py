"""Local evidence receipts and versioned news retrieval across research sessions."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager, closing
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import parse_qsl,urlsplit,urlunsplit,urlencode

from research_data.http import dumps,utcnow
from research_data.normalize import timestamp,iso


def canonical_url(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise ValueError('Invalid source URL')
    query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path or '/',urlencode(sorted(query)),''))


def identify_assets(row):
    text=' '.join(str(row.get(k) or '') for k in ('asset','title','url','text'))
    assets=[]
    for asset,pattern in [('ETH',r'\bETH\b|Ethereum|以太坊'),('BTC',r'\bBTC\b|Bitcoin|比特币')]:
        if re.search(pattern,text,re.I):assets.append(asset)
    return assets


def instant_us(value):
    if value is None:return None
    try:
        delta=timestamp(value)-datetime(1970,1,1,tzinfo=timezone.utc)
        return (delta.days*86400+delta.seconds)*1000000+delta.microseconds
    except (ValueError,TypeError,OverflowError):return None


def capture_us(body):
    """Conservative saved-version acquisition clock, before ranking statistics."""
    try:
        row=json.loads(body)
        values=[row.get(k) for k in ('first_seen_at','available_at','retrieved_at','acquired_at')]
        values.append((row.get('source') or {}).get('retrieved_at'))
        clocks=[instant_us(value) for value in values if value]
        return max(clocks) if clocks and all(value is not None for value in clocks) else None
    except (ValueError,TypeError,AttributeError):return None


def source_content_fingerprint(row):
    """Exact complete-source identity; excerpt hashes must never identify a revision.

    Acquisition clocks intentionally do not enter this fingerprint. A publication
    date or content change does, so later corrections remain separate evidence.
    Callers must supply a freshly reopened saved source, not a generated summary.
    """
    if not isinstance(row.get('text'),str):return None
    published=row.get('published_at')
    try:published=iso(timestamp(published)) if published else None
    except (ValueError,TypeError,OverflowError):return None
    return hashlib.sha256(dumps({'title':row.get('title') or '', 'text':row['text'],
                                'published_at':published},sort_keys=True).encode()).hexdigest()


def news_coverage(rows,*,start=None,end=None,pool_truncated=False):
    """Bounded inventory of captured material, never a claim of complete news coverage."""
    start_time=timestamp(start) if start else None;end_time=timestamp(end) if end else None
    counts=Counter();days=Counter();hosts=set();origins=set();events=set();unassigned=0
    for row in rows:
        raw=row.get('url') or row.get('source',{}).get('url')
        if raw:
            try:
                url=canonical_url(raw);origins.add(url);hosts.add(urlsplit(url).hostname)
            except (ValueError,TypeError):pass
        event=row.get('event_id')
        if event:events.add(str(event))
        else:unassigned+=1
        value=row.get('published_at')
        try:date=timestamp(value) if value else None
        except (ValueError,TypeError,OverflowError):date=None
        role=('unknown_time' if date is None else 'background' if start_time and date<start_time
              else 'later_update' if end_time and date>=end_time else 'in_window' if start_time or end_time else 'unscoped')
        counts[role]+=1
        if role=='in_window':days[iso(date)[:10]]+=1
    issues=[]
    if (start or end) and not counts['in_window']:issues.append('no_captured_in_window_documents')
    if counts['unknown_time']:issues.append('publication_time_unknown')
    if pool_truncated:issues.append('candidate_pool_truncated_not_complete_window_coverage')
    return {'start':start,'end_exclusive':end,'document_count':len(rows),
        'time_roles':{key:counts[key] for key in ('in_window','background','later_update','unknown_time','unscoped')},
        'unique_origin_urls':len(origins),'publisher_host_count':len(hosts),'publisher_host_preview':sorted(hosts)[:12],
        'explicit_event_count':len(events),'documents_without_explicit_event':unassigned,
        'publication_day_counts':dict(sorted(days.items())[:31]),'publication_days_omitted':max(0,len(days)-31),
        'gaps':issues,'scope':'Captured candidate/selected inventory only. URLs/hosts are not proof of independent reporting; absent dates do not mean no real events. Publication timestamps remain publisher metadata.'}


def _diverse_news(rows,limit):
    """Breadth-first date/source navigation; no guessed topics or financial ranking."""
    selected=[];remaining=list(rows);days=Counter();hosts=Counter()
    while remaining and len(selected)<limit:
        def key(index):
            row=remaining[index];day=(row.get('published_at') or '')[:10]
            try:host=urlsplit(row.get('url') or '').hostname or ''
            except ValueError:host=''
            return days[day],hosts[host],index
        index=min(range(len(remaining)),key=key);row=remaining.pop(index);selected.append(row)
        days[(row.get('published_at') or '')[:10]]+=1
        try:hosts[urlsplit(row.get('url') or '').hostname or '']+=1
        except ValueError:hosts['']+=1
    return selected


class KnowledgeStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'knowledge.sqlite'
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS evidence(record_id TEXT PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS evidence_market_dataset ON evidence(json_extract(body,'$.dataset_id'))
                    WHERE kind='data';
                CREATE TABLE IF NOT EXISTS documents(doc_id TEXT PRIMARY KEY,url TEXT NOT NULL,kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(version_id TEXT PRIMARY KEY,doc_id TEXT NOT NULL,record_id TEXT NOT NULL,
                    title TEXT NOT NULL,text TEXT NOT NULL,published_at TEXT,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
                    assets TEXT NOT NULL, UNIQUE(doc_id,version_id));
                CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(version_id UNINDEXED,title,text);
                CREATE TABLE IF NOT EXISTS events(event_id TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL,
                    available_at TEXT NOT NULL,PRIMARY KEY(event_id,version));
                CREATE TABLE IF NOT EXISTS reports(report_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS answers(answer_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,body TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_output_receipts(output_id TEXT PRIMARY KEY,kind TEXT NOT NULL,known_at TEXT NOT NULL,digest TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row
        c.create_function('instant_us',1,instant_us,deterministic=True)
        c.create_function('capture_us',1,capture_us,deterministic=True)
        c.execute('PRAGMA journal_mode=WAL')
        try:
            with c:yield c
        finally:c.close()

    def register(self,row,raw_path=None,*,deduplicate_feed=False):
        """A stable ID cannot later be rebound to different normalized facts."""
        row=json.loads(dumps(row));rid=row['record_id']
        if not re.fullmatch(r'(live|page|web|context)-[a-f0-9]{24}',rid):raise ValueError('Unknown persistent evidence ID')
        raw_path=raw_path or row.get('raw_path')
        if raw_path and row.get('source',{}).get('sha256'):
            raw=Path(raw_path).read_bytes();digest=hashlib.sha256(raw).hexdigest()
            if digest!=row['source']['sha256']:raise ValueError('Raw source hash mismatch')
            target=self.root/'raw'/(digest+'.bin');target.parent.mkdir(exist_ok=True)
            if target.exists() and target.read_bytes()!=raw:raise ValueError('Existing raw snapshot differs')
            if not target.exists():target.write_bytes(raw)
            row['raw_path']=str(target)
        if row.get('recipe_path'):
            code=Path(row['recipe_path']).read_bytes();sha=hashlib.sha256(code).hexdigest()
            if sha!=row['recipe_sha256']:raise ValueError('Recipe hash mismatch')
            target=self.root/'recipes'/(sha+'.py');target.parent.mkdir(exist_ok=True)
            if target.exists() and target.read_bytes()!=code:raise ValueError('Saved recipe was modified')
            if not target.exists():target.write_bytes(code)
            row['recipe_path']=str(target)
        body=dumps(row,sort_keys=True);digest=hashlib.sha256(body.encode()).hexdigest()
        seen=iso(timestamp(row.get('retrieved_at') or utcnow()))
        kind='page' if rid.startswith('page-') else ('feed_item' if row.get('source_kind')=='feed_item' else 'search_excerpt') if rid.startswith('web-') else 'data'
        with self.connect() as c:
            if deduplicate_feed:
                if kind!='feed_item':raise ValueError('Feed deduplication requires a feed item')
                c.execute('BEGIN IMMEDIATE')
                _,_,version=self._news_identity(row,kind)
                current=c.execute('SELECT record_id FROM versions WHERE version_id=?',(version,)).fetchone()
                if current:
                    self._index_news(c,row,seen,kind)
                    return current['record_id']
            existing=c.execute('SELECT digest FROM evidence WHERE record_id=?',(rid,)).fetchone()
            if existing and existing['digest']!=digest:raise ValueError('Evidence receipt mismatch: normalized record was modified')
            c.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',(rid,body,digest,seen,kind))
            if kind!='data':self._index_news(c,row,seen,kind)
        observer=getattr(self,'source_observer',None)
        if observer and kind!='data':
            try:observer(self.get(rid))
            except Exception as exc:
                self.memory_issues=[*getattr(self,'memory_issues',[]),{'record_id':rid,'component':'source_index','error_type':type(exc).__name__}][-30:]
        return rid

    def _index_news(self,c,row,seen,kind):
        url,did,vid=self._news_identity(row,kind)
        c.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?)',(did,url,kind))
        published=None
        if row.get('published_at'):
            try:published=iso(timestamp(row['published_at']))
            except (ValueError,TypeError):pass
        c.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?,?,?,?)',
                  (vid,did,row['record_id'],row.get('title') or url,row.get('text') or '',published,seen,seen,dumps(identify_assets(row))))
        c.execute('UPDATE versions SET last_seen_at=CASE WHEN instant_us(last_seen_at)<instant_us(?) THEN ? ELSE last_seen_at END WHERE version_id=?',(seen,seen,vid))
        if not c.execute('SELECT 1 FROM news_fts WHERE version_id=?',(vid,)).fetchone():
            c.execute('INSERT INTO news_fts VALUES(?,?,?)',(vid,row.get('title') or url,row.get('text') or ''))

    @staticmethod
    def _news_identity(row,kind):
        url=canonical_url(row.get('url') or row['source']['url'])
        did=hashlib.sha256((kind+'|'+url).encode()).hexdigest()[:24]
        content=dumps({k:row.get(k) for k in ('title','text','published_at')},sort_keys=True)
        return url,did,hashlib.sha256((did+content).encode()).hexdigest()[:24]

    def observe_feed_item(self,row):
        """Keep the first receipt for identical publisher text and track resightings."""
        return self.register(row,deduplicate_feed=True)

    def get(self,rid):
        with self.connect() as c:r=c.execute('SELECT * FROM evidence WHERE record_id=?',(rid,)).fetchone()
        if not r:
            library=getattr(self,'library',None)
            if library and library.contains(rid):return library.evidence(rid)
            raise KeyError('Persistent evidence is unavailable: '+rid)
        if hashlib.sha256(r['body'].encode()).hexdigest()!=r['digest']:raise ValueError('Persistent evidence digest mismatch')
        row=json.loads(r['body'])
        if row.get('raw_path') and row.get('source',{}).get('sha256'):
            if hashlib.sha256(Path(row['raw_path']).read_bytes()).hexdigest()!=row['source']['sha256']:raise ValueError('Persistent raw evidence hash mismatch')
        if row.get('recipe_path') and hashlib.sha256(Path(row['recipe_path']).read_bytes()).hexdigest()!=row['recipe_sha256']:
            raise ValueError('Persistent recipe hash mismatch')
        return row

    def contains(self,rid):
        with self.connect() as c:return bool(c.execute('SELECT 1 FROM evidence WHERE record_id=?',(rid,)).fetchone())

    def get_many(self,record_ids):
        """Fresh bulk canonical validation, not a persistent trust cache."""
        ids=list(dict.fromkeys(record_ids))
        if len(ids)>10000:raise ValueError('Bound bulk evidence to10000IDs')
        records={};errors={};local={};verified={}
        with self.connect() as c:
            for begin in range(0,len(ids),500):
                batch=ids[begin:begin+500]
                local.update({r['record_id']:r for r in c.execute('SELECT * FROM evidence WHERE record_id IN ('+','.join('?' for _ in batch)+')',batch)})
        from research_data.library.store import ResearchLibrary
        for rid,r in local.items():
            try:
                if hashlib.sha256(r['body'].encode()).hexdigest()!=r['digest']:raise ValueError('Persistent evidence digest mismatch')
                row=json.loads(r['body'])
                if row.get('raw_path') and row.get('source',{}).get('sha256'):
                    ResearchLibrary._verify_bulk_file(Path(row['raw_path']),row['source']['sha256'],verified)
                if row.get('recipe_path'):
                    ResearchLibrary._verify_bulk_file(Path(row['recipe_path']),row['recipe_sha256'],verified)
                records[rid]=row
            except (ValueError,KeyError,OSError) as exc:errors[rid]=type(exc).__name__
        pending=[rid for rid in ids if rid not in local]
        library=getattr(self,'library',None)
        if library and pending:
            packet=library.evidence_many(pending);records.update(packet['records']);errors.update(packet['errors'])
        else:errors.update({rid:'KeyError' for rid in pending})
        return {'records':records,'errors':errors}

    def previous_market_record(self,current,min_age_seconds=300):
        """Comparable saved snapshot; this irregular interval is not a 24h series."""
        end=instant_us(current['observed_at'])-min_age_seconds*1000000
        with self.connect() as c:
            rows=c.execute("SELECT record_id,body FROM evidence WHERE kind='data' AND json_extract(body,'$.dataset_id')=? AND instant_us(json_extract(body,'$.observed_at'))<=? ORDER BY instant_us(json_extract(body,'$.observed_at')) DESC LIMIT 20",(current['dataset_id'],end)).fetchall()
        for row in rows:
            prior=json.loads(row['body'])
            if all(prior.get(k)==current.get(k) for k in ('schema_version','instrument','provider','quote_currency')):
                return self.get(row['record_id'])
        return None

    def market_captures(self, dataset_id, *, as_of, limit):
        """Read immutable captures by actual observation and acquisition clocks."""
        with self.connect() as c:
            rows = c.execute("""
                SELECT record_id FROM evidence
                WHERE kind='data' AND json_extract(body,'$.dataset_id')=?
                  AND instant_us(first_seen_at)<=instant_us(?)
                  AND instant_us(json_extract(body,'$.observed_at'))<=instant_us(?)
                  AND instant_us(json_extract(body,'$.available_at'))<=instant_us(?)
                ORDER BY instant_us(json_extract(body,'$.observed_at')) DESC,
                         instant_us(first_seen_at) DESC, record_id
                LIMIT ?
            """, (dataset_id, as_of, as_of, as_of, limit)).fetchall()
        return self.get_many([row['record_id'] for row in rows])

    def verify_local(self,row):
        with self.connect() as c:r=c.execute('SELECT digest FROM evidence WHERE record_id=?',(row['record_id'],)).fetchone()
        if r and hashlib.sha256(dumps(row,sort_keys=True).encode()).hexdigest()!=r['digest']:
            raise ValueError('Evidence receipt mismatch: local normalized record was modified')

    def search_news(self,query='',asset='ETH',cutoff=None,limit=8,original_only=False,material_only=False):
        return self._news_candidates(query,asset,cutoff,limit,original_only,material_only)

    def _news_candidates(self,query='',asset='ETH',cutoff=None,limit=8,original_only=False,material_only=False,ceiling=30,availability_mode='strict',start=None,end=None,diversify=False,acquisition_cutoff=None,source_url=None):
        if asset not in ('ETH','BTC','ALL'):raise ValueError('Supported assets are ETH, BTC, ALL')
        if not 1<=limit<=ceiling:raise ValueError('Limit must be 1..'+str(ceiling))
        if availability_mode not in ('observed','strict'):raise ValueError('Unknown memory availability mode')
        cutoff=iso(timestamp(cutoff or utcnow()))
        known_by=cutoff if availability_mode=='strict' else utcnow()
        if acquisition_cutoff:known_by=iso(min(timestamp(known_by),timestamp(acquisition_cutoff)))
        terms=re.findall(r'[\w]+',query,flags=re.UNICODE)[:20]
        match=' OR '.join('"'+t.replace('"','""')+'"' for t in terms)
        params=[];where=['instant_us(v.first_seen_at)<=instant_us(?)','(v.published_at IS NULL OR instant_us(v.published_at)<=instant_us(?))',
            'capture_us(e.body)<=instant_us(?)'];params.extend([known_by,cutoff,known_by])
        if start:
            where.append('instant_us(v.published_at)>=instant_us(?)');params.append(start)
        if end:
            where.append('instant_us(v.published_at)<instant_us(?)');params.append(end)
        if asset!='ALL':where.append('v.assets LIKE ?');params.append('%"'+asset+'"%')
        if original_only:where.append("d.kind='page'")
        if material_only:where.append("d.kind IN ('page','feed_item')")
        if source_url:
            where.append('d.doc_id=?')
            params.append(hashlib.sha256(('page|'+source_url).encode()).hexdigest()[:24])
        # Select the newest eligible version per URL/source kind, not its future revision.
        where.append('NOT EXISTS (SELECT 1 FROM versions later JOIN evidence le ON le.record_id=later.record_id WHERE later.doc_id=v.doc_id AND (instant_us(later.first_seen_at)>instant_us(v.first_seen_at) OR (instant_us(later.first_seen_at)=instant_us(v.first_seen_at) AND later.rowid>v.rowid)) AND instant_us(later.first_seen_at)<=instant_us(?) AND (later.published_at IS NULL OR instant_us(later.published_at)<=instant_us(?)) AND capture_us(le.body)<=instant_us(?))');params.extend([known_by,cutoff,known_by])
        sql='SELECT v.*,d.url,d.kind FROM versions v JOIN documents d ON d.doc_id=v.doc_id JOIN evidence e ON e.record_id=v.record_id WHERE '+ ' AND '.join(where)+' ORDER BY instant_us(coalesce(v.published_at,v.first_seen_at)) DESC,v.version_id'
        with self.connect() as c:rows=list(c.execute(sql,params).fetchall())
        if match and rows:
            # Rank BEFORE the candidate cap, using only eligible latest versions.
            # Persistent FTS corpus statistics include later acquisitions and would
            # change past rankings even if an outer WHERE hid those future rows.
            with closing(sqlite3.connect(':memory:')) as ranking:
                ranking.execute('CREATE VIRTUAL TABLE eligible USING fts5(record_id UNINDEXED,title,text)')
                ranking.executemany('INSERT INTO eligible VALUES(?,?,?)',((r['record_id'],r['title'],r['text']) for r in rows))
                scores=dict(ranking.execute('SELECT record_id,bm25(eligible,0,4,1) FROM eligible WHERE eligible MATCH ?',(match,)))
            rows=[r for r in rows if r['record_id'] in scores]
            rows.sort(key=lambda r:scores[r['record_id']])
        results=[{'record_id':r['record_id'],'version_id':r['version_id'],'title':r['title'],'url':r['url'],
                 'excerpt':r['text'][:1800],'published_at':r['published_at'],'first_seen_at':r['first_seen_at'],
                 'last_seen_at':r['last_seen_at'],'source_kind':r['kind'],'assets':json.loads(r['assets']),
                 'publication_time_verified':False} for r in rows]
        selected=_diverse_news(results,limit) if diversify else results[:limit]
        originals={r['record_id']:r for r in rows}
        for row in selected:row['source_content_sha256']=source_content_fingerprint(dict(originals[row['record_id']]))
        return selected

    def news_originals_by_url(self,url,*,cutoff,acquisition_cutoff,start=None,end=None):
        """Find saved original IDs without fuzzy retrieval or a network request."""
        normalized=canonical_url(url)
        local=self._news_candidates(asset='ALL',cutoff=cutoff,original_only=True,
            availability_mode='observed',acquisition_cutoff=acquisition_cutoff,
            start=start,end=end,source_url=normalized,limit=1)
        library=getattr(self,'library',None)
        archived=[]
        if library:
            lower=max(timestamp(start or library.window_start),timestamp(library.window_start))
            upper=min(timestamp(end or cutoff),timestamp(cutoff),timestamp(library.window_end))
            if lower<upper:
                urls=tuple(dict.fromkeys((url,normalized))) if not urlsplit(url).fragment else (url,)
                archived=library.search_documents(start=lower,end=upper,mode='strict',
                    known_by=acquisition_cutoff,exact_urls=urls,limit=8)['results']
        return list(dict.fromkeys(row['record_id'] for row in [*local,*archived]))

    def event_history(self,asset='ETH',cutoff=None,limit=120):
        if asset not in ('ETH','BTC','ALL'):raise ValueError('Supported assets are ETH, BTC, ALL')
        if not 1<=limit<=150:raise ValueError('Limit must be 1..150')
        cutoff=iso(timestamp(cutoff or utcnow()))
        with self.connect() as c:
            rows=c.execute("SELECT body FROM events WHERE instant_us(available_at)<=instant_us(?) AND (?='ALL' OR json_extract(body,'$.asset')=?) ORDER BY instant_us(available_at) DESC LIMIT ?", (cutoff,asset,asset,limit)).fetchall()
        return [json.loads(row['body']) for row in rows]

    def _related_updates(self,record_ids,asset,cutoff):
        """Find newest revisions for these sources, even outside the recent timeline.

        Candidate evidence is bounded upstream. SQL matches explicit saved IDs;
        neither lexical similarity nor an analyst's title creates a correction.
        """
        if not record_ids:return []
        placeholders=','.join('?' for _ in record_ids)
        sql=f'''SELECT DISTINCT latest.body FROM events old
            JOIN json_each(old.body,'$.evidence_ids') ids
            JOIN events latest ON latest.event_id=old.event_id
            WHERE ids.value IN ({placeholders})
              AND instant_us(old.available_at)<=instant_us(?)
              AND instant_us(latest.available_at)<=instant_us(?)
              AND (?='ALL' OR json_extract(old.body,'$.asset')=?)
              AND old.version<latest.version
              AND latest.version=(SELECT max(newer.version) FROM events newer
                  WHERE newer.event_id=old.event_id
                  AND instant_us(newer.available_at)<=instant_us(?))'''
        with self.connect() as c:
            rows=c.execute(sql,(*record_ids,cutoff,cutoff,asset,asset,cutoff)).fetchall()
        return [json.loads(row['body']) for row in rows]

    def candidate_news(self,query='',asset='ETH',cutoff=None,original_only=False,
                       material_only=False,include_updates=True,mode='strict',start=None,end=None,acquisition_cutoff=None):
        """Bounded recall with explicit update dependencies, before context selection."""
        if mode not in ('observed','strict'):raise ValueError('Unknown memory availability mode')
        cutoff=iso(timestamp(cutoff or utcnow()))
        if start and timestamp(start)>=timestamp(end or cutoff):
            raise ValueError('News start must precede the exclusive end')
        if end and timestamp(end)>timestamp(cutoff):
            raise ValueError('News end cannot exceed the research cutoff')
        candidates=self._news_candidates(query,asset,cutoff,limit=60,
                                         original_only=original_only,material_only=material_only,
                                         ceiling=120,availability_mode=mode,start=start,end=end,acquisition_cutoff=acquisition_cutoff)
        # A scoped weekly/monthly study must not silently equate a narrow lexical
        # query with coverage of its entire window. Keep independent breadth
        # candidates alongside lexical matches, under the same eligibility rules.
        window_breadth=bool(start and end)
        broad=self._news_candidates('',asset,cutoff,limit=60,original_only=original_only,
            material_only=material_only,ceiling=120,availability_mode=mode,start=start,end=end,
            diversify=True,acquisition_cutoff=acquisition_cutoff) if window_breadth else []
        breadth_truncated=len(broad)>=60
        library=getattr(self,'library',None)
        library_known_by=min((cutoff,acquisition_cutoff),key=timestamp) if mode=='strict' and acquisition_cutoff else cutoff if mode=='strict' else acquisition_cutoff
        library_mode='strict' if library_known_by else mode
        if library:
            library_start=max(timestamp(start or library.window_start),timestamp(library.window_start))
            library_end=min(timestamp(end or cutoff),timestamp(cutoff),timestamp(library.window_end))
            if library_start<library_end:
                monthly=library.search_documents(query,asset=None if asset=='ALL' else asset,
                    start=library_start,end=library_end,
                    mode=library_mode,known_by=library_known_by,limit=60)['results']
                if original_only:monthly=[row for row in monthly if row['record_id'].startswith('page-')]
                if material_only:monthly=[row for row in monthly if row.get('source_kind')!='search_excerpt']
                existing={r['record_id'] for r in candidates}
                candidates.extend(r for r in monthly if r['record_id'] not in existing)
                if window_breadth:
                    # Up to eight disjoint time strata prevent a busy final day
                    # from displacing the start of a month before reranking.
                    strata=min(8,max(1,(library_end-library_start).days+1))
                    span=library_end-library_start
                    for index in range(strata):
                        packet=library.search_documents('',asset=None if asset=='ALL' else asset,
                            start=library_start+span*index/strata,end=library_start+span*(index+1)/strata,
                            mode=library_mode,known_by=library_known_by,limit=8)
                        extra=packet['results']
                        if original_only:extra=[row for row in extra if row['record_id'].startswith('page-')]
                        if material_only:extra=[row for row in extra if row.get('source_kind')!='search_excerpt']
                        broad.extend(extra)
                        breadth_truncated=breadth_truncated or bool(packet.get('next_offset'))
        raw_count=len({row['record_id'] for row in [*candidates,*broad]})
        if window_breadth:
            diverse=_diverse_news(list({row['record_id']:row for row in broad}.values()),60)
            mixed=[];seen=set()
            for index in range(max(len(candidates),len(diverse))):
                for group in (candidates,diverse):
                    if index<len(group) and group[index]['record_id'] not in seen:
                        mixed.append(group[index]);seen.add(group[index]['record_id'])
            candidates=mixed[:120]
        by_id={row['record_id']:row for row in candidates}
        dependencies={}
        if include_updates:
            update_cutoff=cutoff if mode=='strict' else utcnow()
            if acquisition_cutoff:update_cutoff=iso(min(timestamp(update_cutoff),timestamp(acquisition_cutoff)))
            for latest in self._related_updates(list(by_id),asset,update_cutoff):
                latest_ids=latest.get('evidence_ids',[])
                with self.connect() as c:
                    old=c.execute('SELECT body FROM events WHERE event_id=? AND version<? AND instant_us(available_at)<=instant_us(?)',
                                  (latest['event_id'],latest['version'],update_cutoff)).fetchall()
                old_ids={rid for event in old for rid in json.loads(event['body']).get('evidence_ids',[])}
                relevant=old_ids.intersection(by_id)
                if not relevant:continue
                for rid in relevant:dependencies[rid]=list(dict.fromkeys([*dependencies.get(rid,[]),*latest_ids]))
                for rid in latest_ids:
                    if rid in by_id:continue
                    if len(by_id)>=120:continue
                    try:record=self.get(rid)
                    except (KeyError,ValueError,FileNotFoundError):continue
                    if original_only and not rid.startswith('page-'):continue
                    known=record.get('retrieved_at')
                    if not known or timestamp(known)>timestamp(update_cutoff):continue
                    if record.get('published_at') and timestamp(record['published_at'])>timestamp(cutoff):continue
                    by_id[rid]={'record_id':rid,'title':record.get('title') or record.get('url') or rid,
                                'url':record.get('url') or record.get('source',{}).get('url'),
                                'excerpt':(record.get('text') or '')[:1800],
                                'source_content_sha256':source_content_fingerprint(record),
                                'published_at':record.get('published_at'),'first_seen_at':known,
                                'related_update_context':True,
                                'source_kind':'page' if rid.startswith('page-') else 'search_excerpt',
                                'publication_time_verified':False}
        for row in by_id.values():
            if row.get('url'):row['origin_key']=canonical_url(row['url'])
        truncated=(raw_count>120 or breadth_truncated) if window_breadth else len(candidates)>=(120 if library else 60)
        return {'candidates':list(by_id.values()),'dependencies':dependencies,
                'candidate_pool_limit':120 if library or window_breadth else 60,
                'candidate_pool_at_limit':truncated,
                'expanded_pool_limit':120,'as_of':cutoff,'mode':mode,
                'coverage':news_coverage(list(by_id.values()),start=start,end=end,pool_truncated=truncated),
                'breadth_recall':{'enabled':window_breadth,'bounded_unique_candidates_seen':raw_count,
                    'scope':'Explicit-window lexical candidates interleaved with date/source breadth; candidates are navigation, not assessed relevance or complete news coverage.'},
                'retrieval':'BM25 over source/time-filtered latest versions; eligibility before ranking statistics; explicit windows also include bounded date/source breadth'}

    def select_news(self,query='',asset='ETH',cutoff=None,limit=8,original_only=False,
                    budget=2400,policy=None,include_updates=True,*,material_only=False,mode='strict',start=None,end=None):
        from .memory_selection import select_evidence
        policy=policy or ('relevance' if query.strip() else 'coverage')
        pool=self.candidate_news(query,asset,cutoff,original_only=original_only,
            material_only=material_only,include_updates=include_updates,mode=mode,start=start,end=end)
        cutoff=pool['as_of']
        candidates=pool['candidates'];dependencies=pool['dependencies']
        result=select_evidence(candidates,query,budget=budget,limit=limit,
                               policy=policy,as_of=cutoff if mode=='strict' else None,dependencies=dependencies)
        result['selection'].update({k:pool[k] for k in ('candidate_pool_limit','candidate_pool_at_limit','expanded_pool_limit','retrieval')})
        result['coverage']={'candidates':pool['coverage'],
            'selected':news_coverage(result['results'],start=start,end=end)}
        result.update(as_of=cutoff,mode=mode,scope='Locally captured evidence; observed mode permits retrospective acquired material and strict mode requires acquisition by cutoff. Correction links follow explicit reviewed event revisions. No proof of historical public availability or publisher truth.')
        return result

    def record_event(self,event,lookup):
        """Append source-linked analyst event classifications; preserve corrections."""
        event=dict(event);ids=event.get('evidence_ids') or []
        if not 1<=len(ids)<=10:raise ValueError('Events need 1..10 actual evidence IDs')
        if event.get('asset') not in ('ETH','BTC'):raise ValueError('Unsupported event asset')
        if event.get('status') not in ('confirmed','unconfirmed','planned','delayed','cancelled','superseded'):
            raise ValueError('Invalid event status')
        if not event.get('title') or not event.get('summary'):raise ValueError('Event title and summary are required')
        if not event.get('review_id'):raise ValueError('Event classification requires a saved evidence review')
        for rid in ids:
            row=lookup(rid)
            if not rid.startswith(('page-','web-')):raise ValueError('Event evidence must identify a saved announcement/news source')
            self.register({k:v for k,v in row.items() if k!='persistent_raw_path'})
        available=utcnow();event_id=event.get('event_id') or hashlib.sha256((event['asset']+'|'+event['title'].casefold()).encode()).hexdigest()[:24]
        if not re.fullmatch(r'[a-f0-9]{24}',event_id):raise ValueError('Invalid event ID')
        event.update(event_id=event_id,available_at=available,classification_scope='Source-reviewed analyst classification; not a guarantee of real-world truth')
        with self.connect() as c:
            prior=c.execute('SELECT body FROM events WHERE event_id=? ORDER BY version DESC LIMIT 1',(event_id,)).fetchone()
            if prior and json.loads(prior['body'])['asset']!=event['asset']:raise ValueError('A revised event cannot change its asset')
            version=c.execute('SELECT coalesce(max(version),0)+1 FROM events WHERE event_id=?',(event_id,)).fetchone()[0]
            event['version']=version;c.execute('INSERT INTO events VALUES(?,?,?,?)',(event_id,version,dumps(event),available))
        return event

    def events(self,asset='ETH',cutoff=None,limit=30):
        cutoff=iso(timestamp(cutoff or utcnow()))
        with self.connect() as c:
            rows=c.execute('SELECT body FROM events e WHERE instant_us(available_at)<=instant_us(?) AND version=(SELECT max(version) FROM events x WHERE x.event_id=e.event_id AND instant_us(x.available_at)<=instant_us(?)) ORDER BY instant_us(available_at) DESC',(cutoff,cutoff)).fetchall()
        return [e for r in rows if (e:=json.loads(r['body']))['asset']==asset][:limit]

    def retain_report(self,report_id,session_id,body):
        serialized=dumps(body)
        with self.connect() as c:
            old=c.execute('SELECT body FROM reports WHERE report_id=?',(report_id,)).fetchone()
            if old and old['body']!=serialized:raise ValueError('Saved report ID cannot be rebound to different content')
            c.execute('INSERT OR IGNORE INTO reports VALUES(?,?,?,?)',(report_id,session_id,serialized,body.get('created_at') or utcnow()))
            c.execute('INSERT OR IGNORE INTO memory_output_receipts VALUES(?,?,?,?)',(report_id,'report',utcnow(),hashlib.sha256(dumps(body,sort_keys=True).encode()).hexdigest()))

    def _output_receipt(self,output_id,body):
        with self.connect() as c:row=c.execute('SELECT * FROM memory_output_receipts WHERE output_id=?',(output_id,)).fetchone()
        if not row:return None
        if hashlib.sha256(dumps(body,sort_keys=True).encode()).hexdigest()!=row['digest']:raise ValueError('Output acquisition receipt binding failed')
        return dict(row)

    def _research_notice(self,row,body,cutoff=None):
        from .memory_notices import attach_report_notices
        provider=getattr(self,'notice_provider',None) or getattr(self,'report_feedback',None)
        return attach_report_notices(row,body,provider,cutoff=cutoff,report_lookup=self.report)

    def search_reports(self,query='',limit=5,cutoff=None,mode='observed'):
        from .report_presentation import report_display_summary, report_display_title

        if not 1<=limit<=100:raise ValueError('Report recall limit must be 1..100')
        if mode not in ('observed','strict'):raise ValueError('Unknown output memory mode')
        cutoff=cutoff or utcnow();before=timestamp(cutoff)
        with self.connect() as c:rows=c.execute('SELECT * FROM reports ORDER BY created_at DESC').fetchall()
        terms=re.findall(r'\w+',query.lower())
        result=[]
        for r in rows:
            if timestamp(r['created_at'])>before:continue
            body=json.loads(r['body']);draft=body.get('report',{})
            receipt=self._output_receipt(r['report_id'],body)
            if mode=='strict' and (not receipt or timestamp(receipt['known_at'])>before):continue
            title=body.get('initial_report_label') or report_display_title(draft)
            if terms and not any(t in (dumps(draft)+' '+title).lower() for t in terms):continue
            row={'report_id':r['report_id'],'session_id':r['session_id'],'title':title,
                'summary':report_display_summary(draft),'created_at':r['created_at'],'review_status':body.get('review',{}).get('status'),'delivery_kind':draft.get('delivery_kind','full'),
                'acquired_at':receipt['known_at'] if receipt else None,'availability_mode':mode,
                'scope':'Earlier saved AI research, not a new factual source or current personal context; inspect original evidence and review notices.'}
            result.append(self._research_notice(row,body,cutoff))
            if len(result)>=limit:break
        return result

    def report(self,report_id):
        with self.connect() as c:
            row=c.execute('SELECT body FROM reports WHERE report_id=?',(report_id,)).fetchone()
        if not row:raise ValueError('Saved report not found')
        return json.loads(row['body'])

    def previous_report_for_leads(self, *, asset, session_id, as_of, window_start=None,
                                  window_end=None, series_id=None, series_report_ids=(), mode='observed'):
        """Select a saved, reviewed earlier period within this knowledge store."""
        if mode not in ('observed', 'strict') or asset not in ('ETH', 'BTC', 'ALL'):
            raise ValueError('Invalid report lead scope')
        assets = ['BTC', 'ETH'] if asset == 'ALL' else [asset]
        params = dict(session=session_id, series=series_id or None, series_reports=dumps(list(series_report_ids)),
                      assets=dumps(assets), asset_count=len(assets), cutoff=as_of,
                      start=window_start, end=window_end, strict=mode == 'strict')
        with self.connect() as db:
            row = db.execute("""
                WITH candidates AS (
                    SELECT r.report_id, r.session_id, r.created_at, r.body,
                           json_extract(r.body, '$.report.scope.assets') AS assets,
                           instant_us(json_extract(r.body, '$.report.scope.start')) AS period_start,
                           instant_us(json_extract(r.body, '$.report.scope.end')) AS period_end,
                           CASE
                             WHEN r.report_id IN (SELECT value FROM json_each(:series_reports))
                               OR :series IN (json_extract(r.body,'$.run_metadata.series_id'),
                                              json_extract(r.body,'$.run_metadata.subscription_id'))
                             THEN 0 WHEN r.session_id=:session THEN 1 ELSE 2
                           END AS priority
                    FROM reports r LEFT JOIN memory_output_receipts receipt
                       ON receipt.output_id=r.report_id AND receipt.kind='report'
                    WHERE json_extract(r.body,'$.review.status')='reviewed'
                      AND coalesce(json_extract(r.body,'$.report.delivery_kind'),'full') IN ('full','minimal')
                      AND instant_us(r.created_at)<=instant_us(:cutoff)
                      AND (NOT :strict OR instant_us(receipt.known_at)<=instant_us(:cutoff))
                )
                SELECT * FROM candidates
                WHERE period_start < period_end AND period_end <= instant_us(:cutoff)
                  AND (:start IS NULL OR period_start < instant_us(:start))
                  AND (:end IS NULL OR period_end < instant_us(:end))
                  AND json_array_length(assets)=:asset_count
                  AND (SELECT count(DISTINCT value) FROM json_each(assets)
                        WHERE value IN (SELECT value FROM json_each(:assets)))=:asset_count
                ORDER BY priority, period_end DESC, instant_us(created_at) DESC, report_id
                LIMIT 1
            """, params).fetchone()
        if row is None:
            return None
        body = json.loads(row['body'])
        self._output_receipt(row['report_id'], body)
        return {'report_id': row['report_id'], 'session_id': row['session_id'],
                'created_at': row['created_at'], 'body': body,
                'selection_basis': ('same_series', 'same_session', 'workspace_asset')[row['priority']]}

    def retain_answer(self,answer_id,session_id,body):
        if body.get('review',{}).get('status')!='reviewed':
            raise ValueError('Only source-reviewed answers belong in research recall')
        serialized=dumps(body)
        with self.connect() as c:
            old=c.execute('SELECT body FROM answers WHERE answer_id=?',(answer_id,)).fetchone()
            if old and old['body']!=serialized:raise ValueError('Saved answer ID cannot be rebound to different content')
            c.execute('INSERT OR IGNORE INTO answers VALUES(?,?,?,?)',
                      (answer_id,session_id,serialized,body['created_at']))
            c.execute('INSERT OR IGNORE INTO memory_output_receipts VALUES(?,?,?,?)',(answer_id,'answer',utcnow(),hashlib.sha256(dumps(body,sort_keys=True).encode()).hexdigest()))

    def search_research(self,query='',limit=8,cutoff=None,mode='observed'):
        if not 1<=limit<=30:raise ValueError('Research recall limit must be 1..30')
        if mode not in ('observed','strict'):raise ValueError('Unknown output memory mode')
        from .answers import answer_evidence_ids,AnswerDraft
        from .memory_notices import original_evidence_ids
        cutoff=cutoff or utcnow();before=timestamp(cutoff)
        rows=[{'kind':'report',**row} for row in self.search_reports(query,limit,cutoff=cutoff,mode=mode)]
        terms=re.findall(r'\w+',query.casefold())
        with self.connect() as c:
            answers=c.execute('SELECT * FROM answers ORDER BY created_at DESC').fetchall()
        for row in answers:
            if timestamp(row['created_at'])>before:continue
            body=json.loads(row['body']);draft=body['draft']
            receipt=self._output_receipt(row['answer_id'],body)
            if mode=='strict' and (not receipt or timestamp(receipt['known_at'])>before):continue
            if terms and not any(term in dumps(draft).casefold() for term in terms):continue
            advice=draft.get('advice') or {}
            summary=advice.get('summary') or ' '.join(c['statement'] for c in draft.get('facts',[]))
            ids=answer_evidence_ids(AnswerDraft.model_validate(draft))
            rows.append({'kind':'answer','answer_id':row['answer_id'],'session_id':row['session_id'],
                'summary':summary[:1200],'created_at':row['created_at'],'review_status':'reviewed',
                'source_evidence_ids':ids,'original_source_evidence_ids':list(ids),
                'scope':'Earlier source-reviewed AI reasoning, not a new factual source or current personal context; inspect underlying evidence and dates.'})
        library=getattr(self,'library',None)
        if library and mode!='strict':
            seen={r.get('report_id') or r.get('answer_id') for r in rows}
            kwargs={'limit':limit}
            # Filter in the library query before its limit, then again here.
            if cutoff:
                from datetime import timedelta
                kwargs['end']=iso(before+timedelta(microseconds=1))
            for item in library.search_outputs(query,**kwargs)['results']:
                if item['output_id'] in seen or timestamp(item['created_at'])>before:continue
                item=dict(item)
                if item.get('kind')=='report':
                    try:body=library.output(item['output_id'])['body']
                    except (ValueError,KeyError,OSError):body={}
                    item=self._research_notice(item,body,cutoff)
                else:item['original_source_evidence_ids']=list(item.get('source_evidence_ids',[]))
                rows.append(item)
        return sorted(rows,key=lambda row:timestamp(row['created_at']),reverse=True)[:limit]
