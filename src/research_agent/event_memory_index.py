"""Rebuildable source-span/event memory index; original evidence remains authoritative.

No model, network, fact extraction or automatic event equivalence is performed.
All card text is an exact saved-source span. Summaries/links are dated derived
annotations; newer captures mark dependent summaries dirty rather than overwrite
originals. Historical ranking is constructed from eligible originals only.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from research_data.http import utcnow
from research_data.library.time import timestamp

VERSION = 'event-memory-index-v2'
SCOPE = ('Derived source navigation, not a new evidence record, confirmed event, '
         'publisher authentication or investment conclusion. Reopen original evidence IDs.')
PREDICATES = {'corrects', 'supersedes', 'contradicts', 'unresolved_conflict',
              'content_revision_of', 'related_candidate', 'same_event_candidate'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256((value if isinstance(value, str) else _json(value)).encode()).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\w][\w.:-]{0,159}', value):
        raise ValueError('Invalid memory/source identifier')
    return value


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\w][\w.:-]{0,119}', value):
        raise ValueError('Use a bounded entity/event key, not source instructions')
    return value.casefold()


def _time(value):
    if value is None:
        return None
    # Unknown time must stay unknown; naive dates are not authenticated clocks.
    if isinstance(value, str) and not re.search(r'(Z|[+-]\d\d:\d\d)$', value):
        raise ValueError('Use timezone-qualified ISO memory timestamps')
    return timestamp(value).isoformat().replace('+00:00', 'Z')


def _source_snapshot(row):
    if not isinstance(row, dict) or not row.get('record_id'):
        raise ValueError('Use an actual saved source record')
    _id(row['record_id'])
    if row.get('private') or row.get('profile') is not None or row['record_id'].startswith('context-') or str(row.get('dataset_id', '')).startswith(('private.', 'local.user_', 'local.portfolio')):
        raise ValueError('Private user context cannot enter public event memory')
    if row.get('quarantined') or row.get('exclusion_reasons') or row.get('library_exclusion_reasons') or 'quarantined' in (row.get('quality_flags') or []):
        raise ValueError('Quarantined source cannot enter active event memory')
    text = row.get('text') or row.get('excerpt')
    if not isinstance(text, str) or not text.strip() or len(text) > 5_000_000:
        raise ValueError('Event memory needs bounded saved original text')
    known = [row.get(k) for k in ('available_at', 'first_seen_at', 'retrieved_at')]
    known.append((row.get('source') or {}).get('retrieved_at'))
    known = [_time(v) for v in known if v]
    acquired = max(known, key=timestamp) if known else None
    source = row.get('source') or {}
    result = {k: row[k] for k in ('record_id', 'dataset_id', 'title', 'url', 'document_id',
        'version_id', 'source_kind', 'asset', 'assets', 'observed_at') if k in row}
    result.update(text=text, published_at=_time(row.get('published_at')), acquired_at=acquired,
        source={k:source[k] for k in ('name', 'url', 'source_id', 'sha256') if k in source},
        original_text_sha256=_sha(text))
    url = result.get('url') or source.get('url')
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Saved source URL must be public HTTP(S)')
        # Retain fragments: different posts in one forum thread remain distinct.
        result['canonical_url'] = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, parsed.fragment))
    result['document_key'] = row.get('document_id') or result.get('canonical_url') or row['record_id']
    if not result.get('assets') and not result.get('asset'):
        public_text=(row.get('title') or '')+' '+text
        result['assets']=[asset for asset,pattern in [('ETH',r'\bETH\b|Ethereum|以太坊'),('BTC',r'\bBTC\b|Bitcoin|比特币')] if re.search(pattern,public_text,re.I)]
        result['asset_tag_basis']='lexical_navigation_hint_not_verified_classification'
    return result


class EventMemoryIndex:
    def __init__(self, root, *, source_lookup, redact=None,source_lookup_many=None):
        self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'event-memory.sqlite'
        self.source_lookup = source_lookup
        self.source_lookup_many=source_lookup_many
        self.redact = redact or str
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sources(record_id TEXT PRIMARY KEY,body TEXT NOT NULL,digest TEXT NOT NULL,indexed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cards(card_id TEXT PRIMARY KEY,evidence_id TEXT NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL,known_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS links(link_id TEXT PRIMARY KEY,body TEXT NOT NULL,digest TEXT NOT NULL,known_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS invalidations(invalidation_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL,known_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS card_source ON cards(evidence_id);
                CREATE INDEX IF NOT EXISTS dirty_target ON invalidations(target_id);
                CREATE INDEX IF NOT EXISTS memory_document_key ON sources(json_extract(body,'$.document_key'));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30); db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _read(row):
        if not row or _sha(row['body']) != row['digest']:
            raise ValueError('Derived memory row integrity failed')
        return json.loads(row['body'])

    def upsert_source(self, row_or_id):
        """Index an immutable original and idempotently replay its dirty projection."""
        rid = row_or_id if isinstance(row_or_id, str) else row_or_id['record_id']
        actual = _source_snapshot(self.source_lookup(_id(rid)))
        if isinstance(row_or_id, dict) and _source_snapshot(row_or_id) != actual:
            raise ValueError('Supplied source differs from saved evidence')
        serialized = _json(actual); digest = _sha(serialized); now = _time(utcnow())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM sources WHERE record_id=?', (rid,)).fetchone()
            duplicate = old is not None
            if old:
                self._read(old)
                if old['digest'] != digest:
                    raise ValueError('An indexed evidence ID cannot be rebound to a new version')
                indexed_at=old['indexed_at']
            else:
                indexed_at=now
                db.execute('INSERT INTO sources VALUES(?,?,?,?)', (rid, serialized, digest, indexed_at))
            siblings = [self._read(r) for r in db.execute("SELECT * FROM sources WHERE record_id!=? AND json_extract(body,'$.document_key')=?",(rid,actual['document_key']))]
        changed = []
        for prior in siblings:
            if prior['document_key'] == actual['document_key'] and prior['original_text_sha256'] != actual['original_text_sha256'] and actual['acquired_at'] and prior['acquired_at'] and timestamp(actual['acquired_at'])>timestamp(prior['acquired_at']):
                # The immutable source can have committed before a failed projection.
                # A duplicate retry must finish that projection at its ORIGINAL clock.
                changed.extend(self.mark_dirty(prior['record_id'], reason='new_source_version_captured', replacement_id=rid, change_known_at=indexed_at)['dirty_card_ids'])
        return {'record_id':rid, 'duplicate':duplicate, 'indexed_at':indexed_at, 'source_sha256':digest,
                'dirty_card_ids':sorted(set(changed))}

    def source(self, evidence_id):
        with self.connect() as db:
            record = db.execute('SELECT * FROM sources WHERE record_id=?', (_id(evidence_id),)).fetchone()
        saved = self._read(record)
        actual = _source_snapshot(self.source_lookup(evidence_id))
        if actual != saved:
            raise ValueError('Indexed source no longer matches its original evidence')
        return saved

    def record_card(self, evidence_id, *, start, end, entity_keys=None, event_key=None,
                    event_time=None, valid_from=None, valid_to=None, summary=None, depends_on=None):
        source = self.source(evidence_id); text = source['text']
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text) or end-start > 8000:
            raise ValueError('Supply a bounded exact original character span')
        entities = sorted(set(_key(v) for v in (entity_keys or [])))
        if len(entities)>20:raise ValueError('At most20entity keys per card')
        event = _key(event_key) if event_key is not None else None
        begin, finish = _time(valid_from), _time(valid_to)
        if begin and finish and timestamp(begin)>=timestamp(finish):raise ValueError('Invalid source-claimed validity interval')
        dependencies = list(dict.fromkeys([evidence_id, *(depends_on or [])]))
        if len(dependencies)>30:raise ValueError('Bound card dependencies to30')
        dependency_hashes = {}
        for rid in dependencies:
            _id(rid)
            if rid.startswith('memory-card-'):
                self.read_card(rid)
                with self.connect() as db:
                    saved_dependency = db.execute('SELECT * FROM cards WHERE card_id=?',(rid,)).fetchone()
                dependency_hashes[rid] = _sha(self._read(saved_dependency))
            else:dependency_hashes[rid] = _sha(self.source(rid))
        if summary is not None and (not isinstance(summary, str) or not summary.strip() or len(summary)>3000):
            raise ValueError('Summary must be bounded derived text')
        body = {'evidence_id':evidence_id, 'source_snapshot_sha256':_sha(source),
            'source_text_sha256':source['original_text_sha256'], 'start':start, 'end':end,
            'span_sha256':_sha(text[start:end]), 'entity_keys':entities, 'event_key':event,
            'event_time':_time(event_time), 'valid_from':begin, 'valid_to':finish,
            'summary':self.redact(summary) if summary else None, 'depends_on':dependencies,
            'depends_on_snapshot_sha256':dependency_hashes,
            'kind':'derived_source_claim_card', 'scope':SCOPE}
        cid = 'memory-card-'+_sha(body)[:24]
        body.update(card_id=cid, known_at=_time(utcnow()))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM cards WHERE card_id=?', (cid,)).fetchone()
            duplicate=old is not None
            if old:body=self._read(old)
            else:db.execute('INSERT INTO cards VALUES(?,?,?,?,?)', (cid, evidence_id, _json(body), _sha(body), body['known_at']))
        if summary:
            # Backfilling an old dependent summary after its update was indexed
            # must not make that summary appear fresh again.
            with self.connect() as db:
                source_rows=list(db.execute('SELECT * FROM sources'))
                snapshots=[self._read(row) for row in source_rows]
                source_clocks={row['record_id']:row['indexed_at'] for row in source_rows}
                links=[self._read(row) for row in db.execute('SELECT * FROM links')]
            for dependency in dependencies:
                if dependency.startswith('memory-card-'):continue
                original=self.source(dependency)
                for newer in snapshots:
                    if newer['document_key']==original['document_key'] and newer['original_text_sha256']!=original['original_text_sha256'] and newer['acquired_at'] and original['acquired_at'] and timestamp(newer['acquired_at'])>timestamp(original['acquired_at']):
                        self.mark_dirty(dependency,reason='new_source_version_already_indexed',replacement_id=newer['record_id'],change_known_at=source_clocks[newer['record_id']])
                for link in links:
                    if link['object']==dependency and link['predicate'] in ('corrects','supersedes','content_revision_of'):
                        self.mark_dirty(dependency,reason='declared_update_already_indexed',replacement_id=link['subject'],change_known_at=link['known_at'])
        return {**self.read_card(cid), 'duplicate':duplicate}

    def link_sources(self, subject, object, predicate, *, receipt_id=None):
        if subject==object or predicate not in PREDICATES:raise ValueError('Invalid source relationship')
        self.source(subject); self.source(object)
        if receipt_id is None:raise ValueError('A relationship needs an actual saved source receipt ID')
        receipt = _source_snapshot(self.source_lookup(_id(receipt_id)))
        now = _time(utcnow())
        if receipt['acquired_at'] and timestamp(receipt['acquired_at'])>timestamp(now):
            raise ValueError('A future receipt cannot justify a current relationship')
        body = {'subject':subject, 'object':object, 'predicate':predicate,
                'receipt_id':receipt_id, 'receipt_sha256':_sha(receipt), 'verification':'declared_annotation_not_independent_truth',
                'event_equivalence_confirmed':False, 'scope':SCOPE}
        lid = 'memory-link-'+_sha(body)[:24]; body.update(link_id=lid, known_at=now)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM links WHERE link_id=?',(lid,)).fetchone()
            duplicate=old is not None
            if old:body=self._read(old)
            else:db.execute('INSERT INTO links VALUES(?,?,?,?)',(lid,_json(body),_sha(body),body['known_at']))
        if predicate in ('corrects','supersedes','content_revision_of'):
            # Also replay duplicates after a source/link commit followed by a
            # lock, process interruption or another projection failure.
            self.mark_dirty(object,reason='declared_'+predicate,replacement_id=subject,change_known_at=body['known_at'])
        return {**body,'duplicate':duplicate}

    def mark_dirty(self, source_id, *, reason, replacement_id=None, change_known_at=None):
        """Project a persisted change idempotently without moving its event clock.

        ``known_at`` belongs to the immutable source/link change. ``projected_at``
        records when this materialized view was written or recovered. A caller
        cannot supply an arbitrary past time: replay clocks must resolve to the
        replacement source or an actual saved structural relationship.
        """
        _id(source_id)
        if source_id.startswith('memory-card-'):self.read_card(source_id)
        else:self.source(source_id)
        if replacement_id:self.source(replacement_id)
        if not isinstance(reason,str) or not 1<=len(reason)<=200:raise ValueError('Bound invalidation reason')
        projected_at=_time(utcnow());replay_clock=_time(change_known_at) if change_known_at is not None else projected_at
        if timestamp(replay_clock)>timestamp(projected_at):raise ValueError('A future change cannot be projected now')
        with self.connect() as db:
            cards=[self._read(row) for row in db.execute('SELECT * FROM cards')]
            if change_known_at is not None:
                allowed=set()
                if replacement_id:
                    if reason in ('new_source_version_captured','new_source_version_already_indexed'):
                        original=self.source(source_id);replacement_source=self.source(replacement_id)
                        if original['document_key']!=replacement_source['document_key']:
                            raise ValueError('Source-version replay requires the same saved document')
                        replacement=db.execute('SELECT indexed_at FROM sources WHERE record_id=?',(replacement_id,)).fetchone()
                        if replacement:allowed.add(replacement['indexed_at'])
                    else:
                        for row in db.execute('SELECT * FROM links'):
                            link=self._read(row)
                            expected_reason=reason in ('declared_'+link['predicate'],'declared_update_already_indexed')
                            if expected_reason and link['subject']==replacement_id and link['object']==source_id and link['predicate'] in ('corrects','supersedes','content_revision_of'):
                                allowed.add(link['known_at'])
                if replay_clock not in allowed:
                    raise ValueError('Change replay clock must match its actual persisted source/link')
        visited={source_id};frontier={source_id};dirty=[];created=0
        while frontier:
            new=[card for card in cards if card['card_id'] not in visited and set(card['depends_on']).intersection(frontier)]
            frontier=set()
            for card in new:
                visited.add(card['card_id'])
                if replacement_id in card['depends_on'] and timestamp(card['known_at'])>=timestamp(replay_clock):
                    # Explicit refresh already saw this particular source/link.
                    # Replaying an old change must not invalidate it using today.
                    continue
                frontier.add(card['card_id'])
                if card['summary'] is None:continue
                identity={'target_id':card['card_id'],'source_id':source_id,'replacement_id':replacement_id,'known_at':replay_clock}
                value={**identity,'reason':self.redact(reason),'projected_at':projected_at,
                       'scope':'Deterministic dependency projection of an already-known source/link; original claim remains historical evidence.'}
                iid='memory-dirty-'+_sha(identity)[:24]
                with self.connect() as db:
                    cursor=db.execute('INSERT OR IGNORE INTO invalidations VALUES(?,?,?,?,?)',(iid,card['card_id'],_json(value),_sha(value),replay_clock))
                    created+=cursor.rowcount
                dirty.append(card['card_id'])
        return {'dirty_card_ids':sorted(dirty),'source_id':source_id,'known_at':replay_clock,
                'projected_at':projected_at,'new_invalidations':created}

    @staticmethod
    def _eligible(source, cutoff, mode):
        before=timestamp(cutoff)
        if source.get('published_at') and timestamp(source['published_at'])>before:return False
        if mode=='strict' and (not source.get('acquired_at') or timestamp(source['acquired_at'])>before):return False
        return True

    def read_card(self, card_id, *, cutoff=None, mode='observed'):
        if mode not in ('observed','strict'):raise ValueError('Unknown memory mode')
        as_of=_time(cutoff or utcnow())
        with self.connect() as db:
            row=db.execute('SELECT * FROM cards WHERE card_id=?',(_id(card_id),)).fetchone()
            invalidations=db.execute('SELECT * FROM invalidations WHERE target_id=?',(card_id,)).fetchall()
        card=self._read(row);source=self.source(card['evidence_id'])
        if not self._eligible(source,as_of,mode) or (mode=='strict' and timestamp(card['known_at'])>timestamp(as_of)):
            raise ValueError('Memory card was not eligible at the cutoff')
        if card['source_snapshot_sha256']!=_sha(source) or card['source_text_sha256']!=source['original_text_sha256']:
            raise ValueError('Card source/version binding failed')
        for rid, expected in card['depends_on_snapshot_sha256'].items():
            if rid.startswith('memory-card-'):
                self.read_card(rid,cutoff=as_of,mode=mode)
                with self.connect() as db:
                    saved_dependency = db.execute('SELECT * FROM cards WHERE card_id=?',(rid,)).fetchone()
                actual_dependency = self._read(saved_dependency)
            else:
                actual_dependency = self.source(rid)
                if not self._eligible(actual_dependency,as_of,mode):
                    raise ValueError('Card dependency was not eligible at the cutoff')
            if _sha(actual_dependency)!=expected:raise ValueError('Card dependency/version binding failed')
        span=source['text'][card['start']:card['end']]
        if _sha(span)!=card['span_sha256']:raise ValueError('Card source span changed')
        dirty=[]
        for invalidation in invalidations:
            if mode=='strict' and timestamp(invalidation['known_at'])>timestamp(as_of):continue
            change=self._read(invalidation)
            replacement=change.get('replacement_id')
            if replacement and not self._eligible(self.source(replacement),as_of,mode):continue
            if mode=='strict' and change.get('projected_at') and timestamp(change['projected_at'])>timestamp(as_of):
                # Materialized-view repair time is telemetry, not historical
                # source knowledge. Keep the original change clock only.
                change={k:v for k,v in change.items() if k!='projected_at'}
            dirty.append(change)
        return {**card,'record_id':card['evidence_id'],'excerpt':span,'summary_status':'stale' if dirty else 'derived_unverified' if card['summary'] else 'not_present',
                'summary_usable_as_current':bool(card['summary'] and not dirty),'dirty_reasons':dirty,
                'published_at':source.get('published_at'),'acquired_at':source.get('acquired_at'),
                'source_title':source.get('title'),'source_url':source.get('url') or source.get('source',{}).get('url'),
                'publication_status':'claimed_by_source_not_independently_authenticated' if source.get('published_at') else 'unknown',
                'claimed_validity_at_cutoff':('outside_claimed_interval' if (card.get('valid_from') and timestamp(card['valid_from'])>timestamp(as_of)) or (card.get('valid_to') and timestamp(card['valid_to'])<=timestamp(as_of)) else 'within_claimed_interval') if card.get('valid_from') or card.get('valid_to') else 'unknown',
                'event_time_relation':'future_claim_or_schedule_not_occurrence' if card.get('event_time') and timestamp(card['event_time'])>timestamp(as_of) else 'past_or_current_claim_not_occurrence' if card.get('event_time') else 'unknown'}

    def eligible_sources(self, *, cutoff=None, mode='observed', asset='ALL', source_limit=10000):
        """Independently enumerate the indexed corpus, not a keyword shortlist."""
        if mode not in ('observed','strict') or asset not in ('ETH','BTC','ALL') or not 1<=source_limit<=10000:
            raise ValueError('Invalid indexed-corpus query bounds')
        as_of=_time(cutoff or utcnow());rows=[];gaps=[]
        with self.connect() as db:stored=db.execute('SELECT * FROM sources ORDER BY record_id').fetchall()
        eligible=[];snapshots={}
        for record in stored:
            source=self._read(record)
            if not self._eligible(source,as_of,mode):continue
            assets=source.get('assets') or ([source['asset']] if source.get('asset') else [])
            if asset!='ALL' and asset not in assets:continue
            eligible.append(source['record_id'])
            snapshots[source['record_id']]=source
        if len(eligible)>source_limit:gaps.append({'reason':'indexed_corpus_query_cap','eligible':len(eligible),'used':source_limit})
        selected=eligible[:source_limit]
        bulk=self.source_lookup_many(selected) if self.source_lookup_many and selected else None
        for rid in selected:
            try:
                if bulk is None:source=self.source(rid)
                else:
                    if rid not in bulk['records']:raise ValueError('Original source failed bulk integrity validation')
                    source=_source_snapshot(bulk['records'][rid])
                    if source!=snapshots[rid]:raise ValueError('Indexed source no longer matches its original evidence')
                rows.append(source)
            except (ValueError,KeyError,OSError) as exc:gaps.append({'reason':'source_integrity_failed','record_id':rid,'error_type':type(exc).__name__})
        return {'sources':rows,'as_of':as_of,'mode':mode,'eligible_source_count':len(eligible),'indexed_source_count':len(eligible) if mode=='strict' else len(stored),
                'indexed_source_count_scope':'cutoff_eligible_only' if mode=='strict' else 'all_indexed',
                'gaps':gaps,'scope':SCOPE}

    def query(self, query='', *, cutoff=None, mode='observed', asset='ALL', limit=8, entity_key=None, event_key=None):
        if not isinstance(query,str) or len(query)>2000 or not 1<=limit<=30:raise ValueError('Invalid event-memory query')
        entity=_key(entity_key) if entity_key is not None else None;event=_key(event_key) if event_key is not None else None
        packet=self.eligible_sources(cutoff=cutoff,mode=mode,asset=asset)
        sources={r['record_id']:r for r in packet.pop('sources')};cards=[]
        with self.connect() as db:stored=db.execute('SELECT * FROM cards ORDER BY card_id').fetchall()
        for row in stored:
            if row['evidence_id'] not in sources:continue
            if mode=='strict' and timestamp(row['known_at'])>timestamp(packet['as_of']):continue
            try:card=self.read_card(row['card_id'],cutoff=packet['as_of'],mode=mode)
            except (ValueError,KeyError,OSError) as exc:
                packet['gaps'].append({'reason':'card_integrity_failed','error_type':type(exc).__name__});continue
            if entity and entity not in card['entity_keys']:continue
            if event and card['event_key']!=event:continue
            cards.append(card)
        # FTS corpus is built after source/card/clock filters: future IDF cannot leak.
        terms=re.findall(r'\w+',query)[:20];rank={}
        if cards and terms:
            with closing(sqlite3.connect(':memory:')) as db:
                db.execute('CREATE VIRTUAL TABLE eligible USING fts5(card_id UNINDEXED,text)')
                db.executemany('INSERT INTO eligible VALUES(?,?)',[(c['card_id'],' '.join([c['source_title'] or '',c['excerpt'],*c['entity_keys']])) for c in cards])
                match=' OR '.join('"'+t.replace('"','""')+'"' for t in terms)
                rank=dict(db.execute('SELECT card_id,bm25(eligible) FROM eligible WHERE eligible MATCH ?',(match,)))
            cards=[c for c in cards if c['card_id'] in rank]
        cards.sort(key=lambda c:(rank.get(c['card_id'],0),-timestamp(c.get('published_at') or c.get('acquired_at') or c['known_at']).timestamp(),c['card_id']))
        selected=cards[:limit]
        for position,card in enumerate(selected,1):
            card['ranking']={'keyword':{'rank':position,'score':rank.get(card['card_id'],0.0)}}
        return {**packet,'results':selected,'matching_card_count':len(cards),'query':query,
                'retrieval':'source-span BM25 over cutoff-eligible cards','event_equivalence_confirmed':False}

    def eligible_links(self, evidence_ids, *, cutoff=None, mode='observed'):
        as_of=_time(cutoff or utcnow());ids=set(evidence_ids);found=[]
        if mode not in ('observed','strict'):raise ValueError('Unknown memory mode')
        with self.connect() as db:rows=db.execute('SELECT * FROM links ORDER BY link_id').fetchall()
        for row in rows:
            if mode=='strict' and timestamp(row['known_at'])>timestamp(as_of):continue
            link=self._read(row)
            if link['subject'] not in ids or link['object'] not in ids:continue
            receipt = _source_snapshot(self.source_lookup(link['receipt_id']))
            if _sha(receipt)!=link['receipt_sha256']:raise ValueError('Relationship receipt no longer matches')
            if not self._eligible(receipt,as_of,mode):continue
            # No endpoint IDs from future records are disclosed.
            if all(self._eligible(self.source(rid),as_of,mode) for rid in (link['subject'],link['object'])):found.append(link)
        return found
