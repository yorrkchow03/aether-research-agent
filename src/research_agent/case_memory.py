"""Immutable, condition-aware research-case navigation over original evidence.

Case summaries are attributed hypotheses, never new factual evidence or learned
trading rules. Outcome events have their own acquisition clocks and do not enter
retrieval scores. Strict recall filters by acquisition before scoring.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_data.http import dumps, utcnow
from .claims import Claim, validate_claims


VERSION = 'condition-aware-cases-v1'
MAX_OUTCOME_PAGE = 16
MAX_METADATA_ITEMS = 24
SCOPE = ('Research-case navigation, not original evidence, a validated market rule, '
         'causal inference or a probability of future investment success. Reopen the cited originals.')


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Case clocks require an ISO timestamp with explicit timezone')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Case clocks require an ISO timestamp with explicit timezone') from None
    if result.utcoffset() is None:
        raise ValueError('Case clocks require an explicit timezone')
    return result.astimezone(timezone.utc)


def _clock(value):
    return _time(value).isoformat().replace('+00:00', 'Z')


def _digest(value):
    return hashlib.sha256(dumps(value, sort_keys=True).encode('utf-8')).hexdigest()


class CaseCondition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=100)
    value: str | None = Field(default=None, max_length=200)
    statement: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def explicit_condition(self):
        if not self.key.strip() or not self.statement.strip():
            raise ValueError('A case condition needs a meaningful key and statement')
        if self.value is not None and (not self.value.strip() or not self.evidence_ids):
            raise ValueError('An asserted condition value requires original evidence; use null for unknown')
        return self


class CaseOrigin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['reviewed_report', 'reviewed_answer']
    id: str = Field(min_length=1, max_length=200)
    explanation_index: int = Field(ge=0, le=10)
    review_id: str = Field(min_length=1, max_length=200)
    review_status: Literal['reviewed']


class CaseDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=250)
    asset: str = Field(min_length=1, max_length=30)
    case_at: str
    horizon: str = Field(default='', max_length=250)
    horizon_days: int | None = Field(default=None, ge=1, le=365)
    hypothesis: Claim
    conditions: list[CaseCondition] = Field(min_length=1, max_length=12)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=20)
    invalidation: list[str] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=10)
    origin: CaseOrigin | str = ''

    @model_validator(mode='after')
    def meaningful_case(self):
        self.case_at = _clock(self.case_at)
        self.asset = self.asset.strip().upper()
        if not self.asset or not self.title.strip():
            raise ValueError('Case title and asset cannot be blank')
        if not self.horizon.strip() and self.horizon_days is None:
            raise ValueError('A research case requires an explicit horizon')
        if len({c.key.strip().casefold() for c in self.conditions}) != len(self.conditions):
            raise ValueError('Condition keys must be distinct')
        if any(not value.strip() or len(value) > 2000 for value in [*self.invalidation, *self.limitations]):
            raise ValueError('Invalidation and limitations need bounded nonblank entries')
        if isinstance(self.origin, str) and len(self.origin) > 250:
            raise ValueError('Origin text exceeds the bounded length')
        return self


class OutcomeDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    window_end: str
    assessment: Literal['supported', 'not_supported', 'mixed', 'inconclusive']
    observation: Claim
    limitations: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def valid_outcome(self):
        self.window_end = _clock(self.window_end)
        if any(not value.strip() or len(value) > 2000 for value in self.limitations):
            raise ValueError('Outcome limitations need bounded nonblank entries')
        return self


class CaseMemory:
    def __init__(self, root, *, evidence_lookup, redact=None, origin_feedback=None):
        if not callable(evidence_lookup):
            raise ValueError('Case memory requires an original-evidence resolver')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'cases.sqlite'
        self.evidence_lookup = evidence_lookup
        self.redact = redact or (lambda text: text)
        self.origin_feedback=origin_feedback
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS research_cases(case_id TEXT PRIMARY KEY,known_at TEXT NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS case_outcomes(outcome_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,known_at TEXT NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL)')
            c.execute('CREATE INDEX IF NOT EXISTS case_outcome_target ON case_outcomes(case_id,known_at)')
            for table in ('research_cases', 'case_outcomes'):
                for operation in ('UPDATE', 'DELETE'):
                    c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Research cases are append-only'); END")

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
    def _source_clocks(record):
        source = record.get('source') or {}
        acquired = [_clock(container[key]) for container in (record,source)
                    for key in ('retrieved_at','known_at') if container.get(key)]
        available = [_clock(container[key]) for container in (record,source)
                     for key in ('available_at','published_at','observed_at','period_end','calculated_at')
                     if container.get(key)]
        return {'acquired_at': max(acquired, key=_time) if acquired else None,
                'available_at': max(available, key=_time) if available else None}

    @staticmethod
    def _public(record, record_id):
        category=str(record.get('data_category','')).lower()
        dataset=str(record.get('dataset_id','')).lower()
        if (record.get('private') or record.get('profile') is not None
                or record_id.startswith('context-')
                or category in ('private_portfolio','private_user_context')
                or 'portfolio' in dataset or dataset.startswith(('local.user','private.'))):
            raise ValueError('Shared research cases cannot contain private portfolio or user-context lineage')

    def _refs(self, ids, *, now, require_receipts=True):
        """Snapshot all lineage; source dates do not substitute for acquisition."""
        rows = {}
        pending = list(dict.fromkeys(ids))
        while pending:
            rid = pending.pop(0)
            if rid in rows:
                continue
            if not isinstance(rid, str) or not rid or len(rid) > 200:
                raise ValueError('Invalid original-evidence ID')
            if len(rows) >= 100:
                raise ValueError('Case evidence lineage exceeds the bounded check')
            record = self.evidence_lookup(rid)
            if not isinstance(record, dict) or (record.get('record_id') not in (None, rid)):
                raise ValueError('Case evidence identity mismatch')
            self._public(record,rid)
            clocks = self._source_clocks(record)
            if require_receipts and not clocks['acquired_at']:
                raise ValueError('Case evidence requires an actual acquisition receipt: ' + rid)
            if any(_time(value) > now for value in clocks.values() if value):
                raise ValueError('Case evidence is unavailable at acquisition: ' + rid)
            rows[rid] = {'record_id': rid, 'sha256': _digest(record), **clocks}
            children = record.get('input_record_ids') or []
            if not isinstance(children, list):
                raise ValueError('Case evidence lineage must be an ID list')
            pending.extend(children)
        return [rows[rid] for rid in sorted(rows)]

    def _verify_refs(self, refs, *, before, mode):
        for ref in refs:
            record = self.evidence_lookup(ref['record_id'])
            self._public(record,ref['record_id'])
            if _digest(record) != ref['sha256']:
                raise ValueError('Original case evidence fingerprint mismatch')
            acquired, available = ref.get('acquired_at'), ref.get('available_at')
            if mode == 'strict' and (not acquired or _time(acquired) > before):
                raise ValueError('Case evidence was not acquired by the strict cutoff')
            if available and _time(available) > before:
                raise ValueError('Case evidence was not available by the cutoff')

    @staticmethod
    def _decode(row, id_key):
        if hashlib.sha256(row['body'].encode()).hexdigest() != row['digest']:
            raise ValueError('Case memory stored-record integrity failed')
        body = json.loads(row['body'])
        if body.get(id_key) != row[id_key] or body.get('known_at') != row['known_at']:
            raise ValueError('Case memory stored-record binding failed')
        return body

    def record_case(self, payload):
        """Host-reviewed research may retain navigation; callers cannot backdate it."""
        draft = CaseDraft.model_validate(payload)
        now = _clock(utcnow())
        if _time(draft.case_at) > _time(now):
            raise ValueError('Cannot retain a research case from the future')
        validate_claims([draft.hypothesis], self.evidence_lookup)
        ids = [*draft.evidence_ids, *draft.counterevidence_ids, *draft.hypothesis.evidence_ids]
        ids.extend(rid for condition in draft.conditions for rid in condition.evidence_ids)
        refs = self._refs(ids, now=_time(now))
        identity = {'case': draft.model_dump(), 'sources': refs, 'version': VERSION}
        # Redact only attributed case text. IDs and canonical source hashes remain
        # stable, so redaction cannot silently rebind the original evidence.
        identity['case'] = CaseDraft.model_validate(json.loads(self.redact(dumps(identity['case'])))).model_dump()
        cid = 'case-' + _digest(identity)[:24]
        body = {**identity, 'case_id': cid, 'known_at': now, 'scope': SCOPE,
                'availability': 'Acquired at known_at; case_at is the described observation time, not historical system possession.'}
        serialized = dumps(body, sort_keys=True)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO research_cases VALUES(?,?,?,?)',
                      (cid, now, serialized, hashlib.sha256(serialized.encode()).hexdigest()))
            row = c.execute('SELECT * FROM research_cases WHERE case_id=?', (cid,)).fetchone()
        return self._decode(row, 'case_id')

    def _case(self, case_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM research_cases WHERE case_id=?', (case_id,)).fetchone()
        if not row:
            raise ValueError('Unknown research case ID')
        return self._decode(row, 'case_id')

    def _origin_notices(self, case, cutoff):
        if not self.origin_feedback:return []
        try:return self.origin_feedback(case['case'].get('origin'),cutoff)[:12]
        except (ValueError,KeyError,TypeError,OSError):
            return [{'status':'unavailable','scope':'Origin feedback could not be checked; inspect the original report before reusing its reasoning.'}]

    def record_outcome(self, case_id, payload):
        """Host-only dated observation, not a business-model reward or win rate."""
        case = self._case(case_id)
        draft = OutcomeDraft.model_validate(payload)
        now = _clock(utcnow())
        if _time(case['known_at']) > _time(now):
            raise ValueError('Cannot assess a case not yet acquired')
        if not _time(case['case']['case_at']) <= _time(draft.window_end) <= _time(now):
            raise ValueError('Outcome window must follow the case and end by acquisition')
        self._verify_refs(case['sources'], before=_time(now), mode='strict')
        validate_claims([draft.observation], self.evidence_lookup)
        refs = self._refs(draft.observation.evidence_ids, now=_time(now))
        identity = {'case_id': case_id, 'case_sha256': _digest(case),
                    'outcome': json.loads(self.redact(dumps(draft.model_dump()))),
                    'sources': refs, 'version': VERSION}
        identity['outcome'] = OutcomeDraft.model_validate(identity['outcome']).model_dump()
        oid = 'case-outcome-' + _digest(identity)[:24]
        body = {**identity, 'outcome_id': oid, 'known_at': now,
                'scope': 'Attributed post-case observation; assessment does not establish causation, a win rate or an investment advantage.'}
        serialized = dumps(body, sort_keys=True)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO case_outcomes VALUES(?,?,?,?,?)',
                      (oid, case_id, now, serialized, hashlib.sha256(serialized.encode()).hexdigest()))
            row = c.execute('SELECT * FROM case_outcomes WHERE outcome_id=?', (oid,)).fetchone()
        return self._decode(row, 'outcome_id')

    def _eligible(self, body, before, mode):
        if _time(body['case']['case_at']) > before:
            return False
        return mode == 'retrospective' or _time(body['known_at']) <= before

    @staticmethod
    def _mode(mode):
        if mode not in ('strict', 'retrospective'):
            raise ValueError('Use strict acquisition-time recall or explicit retrospective reconstruction')

    @staticmethod
    def _sortable_clock(column):
        # Stored clocks are UTC with either no fraction or exactly six digits.
        # Normalize in SQLite rather than rounding to milliseconds with
        # julianday(): submillisecond future receipts must stay excluded.
        return (f"substr({column},1,19)||CASE WHEN substr({column},20,1)='.' "
                f"THEN substr({column},20,7) ELSE '.000000' END")

    def _outcomes(self, case, before, mode, *, limit=MAX_OUTCOME_PAGE, offset=0):
        if type(limit) is not int or not 1 <= limit <= MAX_OUTCOME_PAGE or type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError('Outcome reads require limit 1..16 and a bounded nonnegative offset')
        sortable_cutoff = before.strftime('%Y-%m-%dT%H:%M:%S.%f')
        window = "json_extract(body,'$.outcome.window_end')"
        where = 'case_id=? AND (' + self._sortable_clock(window) + ')<=?'
        args = [case['case_id'], sortable_cutoff]
        if mode == 'strict':
            where += ' AND (' + self._sortable_clock('known_at') + ')<=?'
            args.append(sortable_cutoff)
        with self.connect() as c:
            count = c.execute('SELECT COUNT(*) FROM case_outcomes WHERE ' + where, args).fetchone()[0]
            rows = c.execute('SELECT * FROM case_outcomes WHERE ' + where +
                             ' ORDER BY (' + self._sortable_clock('known_at') + '),outcome_id LIMIT ? OFFSET ?',
                             [*args, limit, offset]).fetchall()
        eligible, excluded = [], []
        for row in rows:
            # Future acquired outcomes are excluded before inspecting their text.
            if mode == 'strict' and _time(row['known_at']) > before:
                continue
            try:
                outcome = self._decode(row, 'outcome_id')
                if _time(outcome['outcome']['window_end']) > before:
                    continue
                if outcome['case_id'] != case['case_id'] or outcome['case_sha256'] != _digest(case):
                    raise ValueError('Outcome is not bound to this case version')
                self._verify_refs(outcome['sources'], before=before, mode=mode)
                eligible.append(outcome)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                excluded.append({'outcome_id': row['outcome_id'], 'reason': str(exc)[:400]})
        next_offset = offset + len(rows)
        page = {'candidate_count': count, 'offset': offset, 'limit': limit,
                'scanned_count': len(rows), 'returned_count': len(eligible),
                'excluded_count': len(excluded), 'has_more': next_offset < count,
                'next_offset': next_offset if next_offset < count else None,
                'scope': 'Cutoff-filtered candidate page; candidates still require source and integrity checks. '
                         'Use get(case_id, outcome_offset=next_offset) for later pages.'}
        return eligible, excluded, page

    def get(self, case_id, cutoff=None, mode='strict', *, outcome_limit=MAX_OUTCOME_PAGE,
            outcome_offset=0, budget_chars=64000):
        self._mode(mode)
        if type(budget_chars) is not int or not 512 <= budget_chars <= 256000:
            raise ValueError('Case read budget must be 512..256000 characters')
        before = _time(cutoff or utcnow())
        case = self._case(case_id)
        if not self._eligible(case, before, mode):
            raise ValueError('Research case is unavailable at the cutoff')
        self._verify_refs(case['sources'], before=before, mode=mode)
        outcomes, excluded, page = self._outcomes(case, before, mode, limit=outcome_limit, offset=outcome_offset)
        result = {**case, 'outcomes': outcomes, 'excluded_outcomes': excluded, 'outcome_page': page, 'query_mode': mode,
                'origin_notices':self._origin_notices(case,_clock(cutoff or utcnow())),
                'historically_known': _time(case['known_at']) <= before,
                'reconstruction_warning': 'Later-acquired retrospective reconstruction; not evidence of historical system knowledge.' if mode == 'retrospective' else None}
        if len(dumps(result, sort_keys=True)) > budget_chars:
            raise ValueError(f'Whole case read exceeds {budget_chars} characters. Case pointer: {case_id}. '
                             'Retry with outcome_limit=1 or a larger bounded budget_chars, and read the listed original evidence IDs. '
                             'Conditions and source associations were not truncated.')
        return result

    @staticmethod
    def _content_key(body):
        """Group repeated publications without discarding stored case versions."""
        case = body['case']
        content = {key: value for key, value in case.items() if key not in ('origin', 'case_at', 'title')}
        content['evidence_ids'] = sorted(set(content['evidence_ids']))
        content['counterevidence_ids'] = sorted(set(content['counterevidence_ids']))
        content['hypothesis'] = {**content['hypothesis'], 'evidence_ids': sorted(set(content['hypothesis']['evidence_ids']))}
        content['conditions'] = sorted([{**condition, 'evidence_ids': sorted(set(condition['evidence_ids']))}
                                        for condition in content['conditions']], key=lambda value: value['key'])
        return _digest({'content': content, 'source_fingerprints': sorted(
            (ref['record_id'], ref['sha256']) for ref in body['sources'])})

    @staticmethod
    def _context(context):
        if not isinstance(context, dict) or set(context) - {'asset', 'horizon_days', 'conditions'}:
            raise ValueError('Case context accepts asset, horizon_days and conditions only')
        conditions = context.get('conditions') or {}
        if not isinstance(conditions, dict) or len(conditions) > 20:
            raise ValueError('Context conditions must be a bounded key/value mapping')
        for key, value in conditions.items():
            if not isinstance(key, str) or not 1 <= len(key.strip()) <= 100 or (value is not None and (not isinstance(value, str) or not 1 <= len(value.strip()) <= 200)):
                raise ValueError('Context condition keys/values must be bounded strings or null')
        if context.get('asset') is not None and (not isinstance(context['asset'], str) or not 1 <= len(context['asset'].strip()) <= 30):
            raise ValueError('Context asset must be a bounded string')
        if context.get('horizon_days') is not None and (type(context['horizon_days']) is not int or not 1 <= context['horizon_days'] <= 365):
            raise ValueError('Context horizon_days must be 1..365')
        if len({key.strip().casefold() for key in conditions}) != len(conditions):
            raise ValueError('Context condition keys must be distinct after normalization')
        return {**context, 'conditions': {key.strip().casefold(): value.strip().casefold() if value is not None else None for key, value in conditions.items()}}

    @staticmethod
    def _match(case, context):
        case_values = {condition['key'].strip().casefold(): condition['value'].strip().casefold() if condition['value'] is not None else None
                       for condition in case['conditions']}
        matches, mismatches, unknown = [], [], []
        for key, value in context['conditions'].items():
            old = case_values.get(key)
            item = {'key': key, 'case_value': old, 'query_value': value}
            (unknown if old is None or value is None else matches if old == value else mismatches).append(item)
        asset_match = None if not context.get('asset') else case['asset'].casefold() == context['asset'].strip().casefold()
        if asset_match is False:
            mismatches.append({'key': 'asset', 'case_value': case['asset'], 'query_value': context['asset']})
        horizon = None
        if context.get('horizon_days') is not None and case.get('horizon_days') is not None:
            horizon = min(case['horizon_days'], context['horizon_days']) / max(case['horizon_days'], context['horizon_days'])
        return {'matching_conditions': matches, 'mismatching_conditions': mismatches,
                'unknown_conditions': unknown, 'asset_match': asset_match, 'horizon_similarity': horizon,
                'scope': 'Exact supplied-label comparisons; similarity is not predictive accuracy or semantic adjudication.'}

    def recall(self, query='', *, context=None, cutoff=None, mode='strict', limit=4, budget_chars=8000):
        self._mode(mode)
        if not isinstance(query, str) or len(query) > 4000 or type(limit) is not int or not 1 <= limit <= 12 or type(budget_chars) is not int or not 512 <= budget_chars <= 64000:
            raise ValueError('Invalid case recall bounds')
        context = self._context(context or {})
        query_cutoff = _clock(cutoff or utcnow())
        before = _time(query_cutoff)
        with self.connect() as c:
            # Acquisition filter is applied before loading text or calculating
            # lexical scores. SQL stores normalized UTC strings; Python rechecks.
            rows = c.execute('SELECT * FROM research_cases ORDER BY known_at,case_id').fetchall()
        candidates, excluded, excluded_count = [], [], 0
        for row in rows:
            if mode == 'strict' and _time(row['known_at']) > before:
                continue
            try:
                case = self._decode(row, 'case_id')
                if not self._eligible(case, before, mode):
                    continue
                self._verify_refs(case['sources'], before=before, mode=mode)
                candidates.append(case)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                excluded_count += 1
                if len(excluded) < MAX_METADATA_ITEMS:
                    excluded.append({'case_id': row['case_id'], 'reason': str(exc)[:400]})
        # Deduplicate only the already cutoff-eligible, source-verified candidates.
        # Earliest acquisition stays representative, so a later republished
        # explanation cannot replace historical context or crowd out other cases.
        groups = {}
        for candidate in candidates:
            groups.setdefault(self._content_key(candidate), []).append(candidate)
        distinct, duplicate_groups, duplicate_count, representative_duplicates = [], [], 0, {}
        for versions in groups.values():
            versions.sort(key=lambda value: (_time(value['known_at']), value['case_id']))
            distinct.append(versions[0])
            if len(versions) > 1:
                duplicate_count += len(versions) - 1
                representative_duplicates[versions[0]['case_id']] = {
                    'suppressed_count': len(versions) - 1,
                    'suppressed_case_ids': [value['case_id'] for value in versions[1:MAX_METADATA_ITEMS + 1]],
                    'outcomes_scope': 'Only representative outcomes are included in this recall; other retained versions may have separately dated outcomes. Read their case IDs explicitly.'}
                if len(duplicate_groups) < MAX_METADATA_ITEMS:
                    duplicate_groups.append({'kept_case_id': versions[0]['case_id'],
                        **representative_duplicates[versions[0]['case_id']],
                        'scope': 'Equivalent cited content and conditions; acquisition/origin versions remain individually readable.'})
        terms = set(re.findall(r'\w+', query.casefold()))
        ranked = []
        for body in distinct:
            case = body['case']
            # Deliberately exclude outcomes from both corpus and score.
            searchable = ' '.join([case['title'], case['asset'], case['hypothesis']['statement'], case['horizon'],
                                   *[condition['statement'] for condition in case['conditions']]])
            tokens = set(re.findall(r'\w+', searchable.casefold()))
            lexical = len(terms & tokens) / len(terms) if terms else 0.0
            match = self._match(case, context)
            condition_denominator = len(context['conditions'])
            condition_score = len(match['matching_conditions']) / condition_denominator if condition_denominator else 0.0
            score = .5 * lexical + .35 * condition_score + .1 * (match['horizon_similarity'] or 0) + .05 * (match['asset_match'] is True)
            if query.strip() and not lexical and not condition_score:
                continue
            ranked.append((score, body, match, lexical, condition_score))
        ranked.sort(key=lambda item: (-item[0], item[1]['case_id']))
        selected, snippets, skipped, spent = [], [], [], 0
        header = 'Historical research cases: hypotheses and conditions, not market evidence. Reopen originals.\n'
        if mode == 'retrospective':
            header += 'RETROSPECTIVE RECONSTRUCTION: later-acquired material may be included; not historical system knowledge.\n'
        for score, body, match, lexical, condition_score in ranked:
            outcomes, excluded_outcomes, outcome_page = self._outcomes(body, before, mode)
            item = {**body, 'match': match, 'outcomes': outcomes, 'excluded_outcomes': excluded_outcomes,
                    'origin_notices':self._origin_notices(body,query_cutoff),
                    'outcome_page': outcome_page,
                    'historically_known': _time(body['known_at']) <= before,
                    'score': score, 'score_components': {'query_term_coverage': lexical, 'matching_condition_fraction': condition_score},
                    'query_mode': mode}
            if body['case_id'] in representative_duplicates:
                item['duplicate_versions'] = representative_duplicates[body['case_id']]
            snippet = dumps(item, sort_keys=True) + '\n'
            if len(header) + spent + len(snippet) > budget_chars:
                if len(skipped) < MAX_METADATA_ITEMS:
                    skipped.append({'case_id': body['case_id'], 'reason': 'whole_case_exceeds_remaining_context_budget',
                                    'characters': len(snippet)})
                continue
            selected.append(item)
            snippets.append(snippet)
            spent += len(snippet)
            if len(selected) >= limit:
                break
        return {'results': selected, 'prompt_context': header + ''.join(snippets) if snippets else '',
                'characters': len(header) + spent if snippets else 0, 'eligible_count': len(candidates),
                'distinct_count': len(distinct), 'duplicate_suppressed_count': duplicate_count,
                'duplicate_groups': duplicate_groups,
                'excluded': excluded, 'excluded_count': excluded_count,
                'budget_skipped': skipped, 'known_by': query_cutoff,
                'query_mode': mode, 'selection_policy': VERSION,
                'scoring': '0.50 query-term coverage + 0.35 exact supplied-condition match fraction + 0.10 horizon ratio + 0.05 asset match; outcomes excluded.',
                'scope': SCOPE}
