"""Append-only report feedback and inspectable correction receipts; no model calls."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from research_data.http import utcnow
from .reports import ReportDraft, validate_report
from .review import check_review, evidence_text, review_targets


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = {'outdated', 'source', 'calculation', 'unclear', 'other'}
REVIEWERS = {'independent_model', 'programmatic', 'user'}
ASSESSMENTS = {'needs_correction', 'needs_more_evidence', 'not_reproduced'}
SCOPE = ('Feedback and review are attributed findings, not independently established truth. '
         'An AI finding is not human review. Resolution links a source-reviewed replacement; '
         'it does not rewrite the original or prove investment performance.')


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _instant(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise ValueError('An ISO timestamp with an explicit timezone is required') from None
    if parsed.tzinfo is None:
        raise ValueError('An ISO timestamp with an explicit timezone is required')
    return parsed.astimezone(timezone.utc)


def _now():
    return _instant(utcnow()).isoformat().replace('+00:00', 'Z')


class ReportFeedbackStore:
    def __init__(self, root, *, report_lookup, evidence_lookup, redact=None, experience=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'report-feedback.sqlite'
        self.project_root = PROJECT_ROOT.resolve()
        self.report_lookup = report_lookup
        self.evidence_lookup = evidence_lookup
        self.redact = redact or (lambda text: str(text))
        self.experience = experience
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS feedback(feedback_id TEXT PRIMARY KEY,identity_hash TEXT UNIQUE NOT NULL,report_id TEXT NOT NULL,known_at TEXT NOT NULL,body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,identity_hash TEXT UNIQUE NOT NULL,feedback_id TEXT NOT NULL REFERENCES feedback(feedback_id),known_at TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS feedback_reports ON feedback(report_id)')
            db.execute('CREATE INDEX IF NOT EXISTS feedback_events ON events(feedback_id)')
            for table in ('feedback', 'events'):
                for operation in ('UPDATE', 'DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Report feedback is append-only'); END")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _text(self, value, name, maximum=4000, allow_empty=False):
        if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
            raise ValueError(f'{name} requires bounded nonblank text')
        text = self.redact(value)
        if not isinstance(text, str):
            raise ValueError('Redaction must return text')
        # Also cover known provider key formats if no application redactor was supplied.
        text = re.sub(r'\b(?:ark-[A-Za-z0-9-]{12,}|apikey_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b', '[REDACTED]', text)
        if len(text) > maximum or (not allow_empty and not text.strip()):
            raise ValueError(f'{name} must remain meaningful after redaction')
        return text

    @staticmethod
    def _identifier(value, label):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value):
            raise ValueError('Invalid ' + label)
        return value

    def _report(self, report_id):
        self._identifier(report_id, 'report ID')
        try:
            body = self.report_lookup(report_id)
        except (KeyError, ValueError):
            raise ValueError('Saved report not found') from None
        if not isinstance(body, dict) or not isinstance(body.get('report'), dict) or not body.get('created_at'):
            raise ValueError('A real saved report with its creation time is required')
        _instant(body['created_at'])
        draft = ReportDraft.model_validate(body['report'])
        targets = {target['claim_id']: target for target in review_targets(draft)}
        return body, draft, targets

    def _get(self, feedback_id, cutoff=None):
        self._identifier(feedback_id, 'feedback ID')
        before = _instant(cutoff or _now())
        with self.connect() as db:
            row = db.execute('SELECT body FROM feedback WHERE feedback_id=?', (feedback_id,)).fetchone()
            events = db.execute('SELECT body FROM events WHERE feedback_id=? ORDER BY rowid', (feedback_id,)).fetchall()
        if not row:
            raise ValueError('Feedback not found')
        result = json.loads(row['body'])
        if _instant(result['known_at']) > before:
            raise ValueError('Feedback was not known at this cutoff')
        eligible = [json.loads(row['body']) for row in events if _instant(json.loads(row['body'])['known_at']) <= before]
        result.update(status='unverified', events=eligible, reviews=[], latest_review=None,
                      resolution=None, experience=None, scope=SCOPE)
        for event in eligible:
            if event['kind'] == 'review':
                result['reviews'].append(event)
                result['latest_review'] = event
                result['status'] = 'reviewed'
            elif event['kind'] == 'resolve':
                result['resolution'] = event
                result['status'] = 'resolved'
            elif event['kind'] in ('experience_recorded', 'experience_failed'):
                result['experience'] = event
        result['last_event_id'] = eligible[-1]['event_id'] if eligible else None
        result['last_known_at'] = eligible[-1]['known_at'] if eligible else result['known_at']
        return result

    def submit(self, report_id, note, claim_id='S1', category='other', actor='user'):
        body, _, targets = self._report(report_id)
        if claim_id not in targets:
            raise ValueError('Feedback target is not a claim in this report')
        if category not in CATEGORIES:
            raise ValueError('Invalid feedback category')
        identity = {'report_id': report_id, 'report_sha256': _sha(body), 'claim_id': claim_id,
                    'target_sha256': _sha(targets[claim_id]), 'category': category,
                    'note': self._text(note, 'Feedback note'), 'actor': self._text(actor, 'Actor', 80)}
        fingerprint = _sha(identity)
        feedback_id = 'feedback-' + fingerprint[:24]
        known_at = _now()
        if _instant(body['created_at']) > _instant(known_at):
            raise ValueError('Cannot submit feedback on a future report')
        target = json.loads(self._text(_canonical(targets[claim_id]), 'Target snapshot', 100000))
        saved = {**identity, 'feedback_id': feedback_id, 'known_at': known_at,
                 'report_created_at': body['created_at'], 'target': target}
        with self.connect() as db:
            inserted = db.execute('INSERT OR IGNORE INTO feedback VALUES(?,?,?,?,?)',
                (feedback_id, fingerprint, report_id, known_at, _canonical(saved))).rowcount
        return {**self._get(feedback_id), 'duplicate': not bool(inserted)}

    def list_for_report(self, report_id, cutoff=None):
        self._identifier(report_id, 'report ID')
        before = _instant(cutoff or _now())
        with self.connect() as db:
            rows = db.execute('SELECT feedback_id,known_at FROM feedback WHERE report_id=? ORDER BY rowid', (report_id,)).fetchall()
        return [self._get(row['feedback_id'], before) for row in rows if _instant(row['known_at']) <= before]

    def _receipt(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.project_root / path
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError('Review receipt does not exist') from None
        forbidden = {'secret', 'secrets', 'credentials', 'config', '.git', '.ssh', '.aws'}
        if path.stem.casefold() in forbidden or any(part.casefold() in forbidden or part.casefold().startswith('.env') for part in path.parts):
            raise ValueError('Secrets and configuration cannot be review receipts')
        local = any(path.is_relative_to(self.project_root / 'local' / group) for group in ('report-quality','memory-upgrade'))
        session = path.relative_to(self.project_root / 'runtime' / 'sessions') if path.is_relative_to(self.project_root / 'runtime' / 'sessions') else None
        in_reviews = session is not None and len(session.parts) >= 4 and session.parts[1:3] == ('workspace', 'reviews')
        if not (local or in_reviews) or path.suffix.lower() not in ('.md', '.json', '.txt'):
            raise ValueError('Receipt must be a project report-quality/memory-upgrade or session review text file')
        if not path.is_file() or not 1 <= path.stat().st_size <= 2_000_000:
            raise ValueError('Receipt is missing, empty or exceeds 2 MB')
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Receipt must be UTF-8 text') from None
        text = self._text(text, 'Receipt', 2_000_000)
        if path.suffix.lower() == '.json':
            try:
                json.loads(text)
            except ValueError:
                raise ValueError('JSON receipt must remain parseable after redaction') from None
        content = text.encode()
        sha = hashlib.sha256(content).hexdigest()
        directory = self.root / 'receipts'
        directory.mkdir(exist_ok=True)
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError('Saved receipt directory cannot escape the feedback store')
        target = directory / (sha + path.suffix.lower())
        if target.is_symlink():
            raise ValueError('Saved receipt cannot be a symlink')
        if target.exists() and target.read_bytes() != content:
            raise ValueError('Saved feedback receipt integrity failed')
        if not target.exists():
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_bytes(content)
            temporary.replace(target)
        return {'original_path': str(path), 'original_sha256': hashlib.sha256(raw).hexdigest(),
                'path': str(target), 'sha256': sha, 'redacted': content != raw}

    def _evidence(self, evidence_ids, known_at):
        if not isinstance(evidence_ids, (list, tuple)) or not 1 <= len(evidence_ids) <= 40:
            raise ValueError('Review needs 1..40 actual evidence IDs')
        snapshots = []
        for record_id in dict.fromkeys(evidence_ids):
            self._identifier(record_id, 'evidence ID')
            try:
                row = self.evidence_lookup(record_id)
            except (KeyError, ValueError):
                raise ValueError('Saved review evidence not found: ' + record_id) from None
            if not isinstance(row, dict) or row.get('record_id') != record_id:
                raise ValueError('Evidence lookup did not return the requested saved record')
            for key in ('retrieved_at', 'available_at'):
                if row.get(key) and _instant(row[key]) > _instant(known_at):
                    raise ValueError('Future evidence cannot support this feedback review')
            snapshots.append({'record_id': record_id, 'sha256': _sha(row)})
        return snapshots

    def _append(self, feedback_id, kind, payload, known_at, previous=None):
        identity = {'feedback_id': feedback_id, 'kind': kind, **payload}
        fingerprint = _sha(identity)
        event = {**identity, 'event_id': 'feedback-event-' + fingerprint[:24], 'known_at': known_at}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT body FROM events WHERE identity_hash=?', (fingerprint,)).fetchone()
            if old:
                return json.loads(old['body']), True
            last = db.execute('SELECT body FROM events WHERE feedback_id=? ORDER BY rowid DESC LIMIT 1', (feedback_id,)).fetchone()
            last = json.loads(last['body']) if last else None
            if (last['event_id'] if last else None) != previous:
                raise ValueError('Feedback changed concurrently; reload before appending')
            if last and _instant(last['known_at']) > _instant(known_at):
                raise ValueError('A feedback event cannot precede its existing history')
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                (event['event_id'], fingerprint, feedback_id, known_at, kind, _canonical(event)))
        return event, False

    def _context_record(self, record_id, reviewed_at):
        if not isinstance(record_id, str) or not record_id.startswith('context-'):
            raise ValueError('Saved review cannot add arbitrary source evidence')
        try:
            row = self.evidence_lookup(record_id)
        except (KeyError, ValueError):
            raise ValueError('Saved task context was not found') from None
        if not isinstance(row, dict) or row.get('record_id') != record_id or row.get('dataset_id') != 'local.user_research_parameters' or not isinstance(row.get('profile'), dict):
            raise ValueError('Additional review context must be a real task-parameter record')
        for key in ('retrieved_at', 'available_at'):
            if row.get(key) and _instant(row[key]) > _instant(reviewed_at):
                raise ValueError('Task context was not available to the saved source review')

    def review(self, feedback_id, *, reviewer_kind, receipt_path, evidence_ids, assessment, correction=''):
        current = self._get(feedback_id)
        if current['status'] == 'resolved':
            raise ValueError('Resolved feedback cannot be silently reopened')
        if reviewer_kind not in REVIEWERS or assessment not in ASSESSMENTS:
            raise ValueError('Invalid reviewer kind or assessment')
        correction = self._text(correction, 'Correction', allow_empty=True)
        if assessment == 'needs_correction' and not correction.strip():
            raise ValueError('A correction assessment needs a meaningful correction')
        known_at = _now()
        receipt = self._receipt(receipt_path)
        evidence = self._evidence(evidence_ids, known_at)
        event, duplicate = self._append(feedback_id, 'review', {
            'reviewer_kind': reviewer_kind, 'assessment': assessment, 'correction': correction,
            'evidence_ids': [row['record_id'] for row in evidence], 'evidence': evidence,
            'receipt': receipt, 'scope': SCOPE,
        }, known_at, current['last_event_id'])
        if not duplicate and self.experience and assessment == 'needs_correction':
            try:
                memory = self.experience.record_correction(
                    f"Report {current['report_id']} {current['claim_id']}: {current['category']}",
                    current['note'], correction, receipts=[receipt['path']], evidence_ids=event['evidence_ids'],
                    affected_outputs=[current['report_id']],
                    applicability=['Review the saved source and relevant time before reusing this report claim.'],
                    kind='source_correction')
                payload = {'review_event_id': event['event_id'], 'correction_id': memory['correction_id']}
                kind = 'experience_recorded'
            except Exception as error:
                payload = {'review_event_id': event['event_id'], 'error': self._text((str(error) or type(error).__name__)[:2000], 'Experience failure', 2000)}
                kind = 'experience_failed'
            self._append(feedback_id, kind, payload, _now(), event['event_id'])
        return {**self._get(feedback_id), 'duplicate': duplicate}

    def resolve(self, feedback_id, replacement_report_id):
        current = self._get(feedback_id)
        if current['status'] == 'resolved':
            if current['resolution']['replacement_report_id'] != replacement_report_id:
                raise ValueError('Resolved feedback already points to another replacement')
            return {**current, 'duplicate': True}
        review = current['latest_review']
        if not review or review['assessment'] != 'needs_correction' or not review['correction'].strip():
            raise ValueError('Resolution requires a source-backed correction review')
        if replacement_report_id == current['report_id']:
            raise ValueError('A replacement must be a distinct saved report')
        original, _, _ = self._report(current['report_id'])
        if _sha(original) != current['report_sha256']:
            raise ValueError('Original report no longer matches the feedback binding')
        path = Path(review['receipt']['path']).resolve()
        if not path.is_relative_to(self.root / 'receipts') or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != review['receipt']['sha256']:
            raise ValueError('Saved review receipt failed integrity checks')
        known_at = _now()
        actual_evidence = self._evidence(review['evidence_ids'], known_at)
        if actual_evidence != review['evidence']:
            raise ValueError('Review evidence no longer matches the saved hashes')
        replacement, draft, targets = self._report(replacement_report_id)
        if _instant(replacement['created_at']) > _instant(known_at):
            raise ValueError('A future report cannot resolve current feedback')
        if (replacement.get('review') or {}).get('status') != 'reviewed':
            raise ValueError('Replacement must already have a saved source review')
        reviewed_at = replacement['review'].get('reviewed_at')
        if not reviewed_at or _instant(reviewed_at) > _instant(known_at):
            raise ValueError('Replacement source review must already be known')
        saved_targets = replacement['review'].get('targets')
        if saved_targets is not None:
            if not isinstance(saved_targets, list) or len(saved_targets) != len(targets):
                raise ValueError('Saved review targets must match the replacement draft')
            inspected = {}
            for target in saved_targets:
                if not isinstance(target, dict) or target.get('claim_id') in inspected or target.get('claim_id') not in targets:
                    raise ValueError('Saved review target IDs differ from the replacement draft')
                base = targets[target['claim_id']]
                if {key: value for key, value in target.items() if key != 'evidence_ids'} != {key: value for key, value in base.items() if key != 'evidence_ids'}:
                    raise ValueError('Saved review target content differs from the replacement draft')
                ids = target.get('evidence_ids')
                if not isinstance(ids, list) or ids[:len(base['evidence_ids'])] != base['evidence_ids']:
                    raise ValueError('Saved review cannot remove or change original target evidence')
                extras = ids[len(base['evidence_ids']):]
                if extras and (base['kind'] not in ('summary', 'judgment') or len(set(extras)) != len(extras)):
                    raise ValueError('Only summary/judgment targets may add task context')
                for rid in extras:
                    if rid in base['evidence_ids']:
                        raise ValueError('Saved review cannot add arbitrary source evidence')
                    self._context_record(rid, reviewed_at)
                inspected[target['claim_id']] = target
            targets = inspected
        else:
            # Public report bundles omit the reviewer's full target/text payload.
            # Reconstruct only its permitted context additions from actual quotes.
            for verdict in replacement['review'].get('verdicts', []):
                target = targets.get(verdict.get('claim_id'))
                if target is None or target['kind'] not in ('summary', 'judgment'):
                    continue
                for quote in verdict.get('quotes', []):
                    rid = quote.get('record_id')
                    if isinstance(rid, str) and rid.startswith('context-') and rid not in target['evidence_ids']:
                        self._context_record(rid, reviewed_at)
                        target['evidence_ids'] = [*target['evidence_ids'], rid]
        cited = {rid for target in targets.values() for rid in target['evidence_ids']}
        if not set(review['evidence_ids']).issubset(cited):
            raise ValueError('Replacement must cite every correction-review evidence record')
        validate_report(draft, self.evidence_lookup, as_of=replacement['created_at'])
        texts = {rid: evidence_text(self.evidence_lookup(rid)) for rid in cited}
        saved_texts = replacement['review'].get('evidence_text')
        if saved_texts is not None and (not isinstance(saved_texts, dict) or any(saved_texts.get(rid) != text for rid, text in texts.items())):
            raise ValueError('Saved source review text differs from the current evidence snapshot')
        checked = check_review({'verdicts': replacement['review'].get('verdicts', [])}, list(targets.values()), texts)
        if checked['status'] != 'reviewed':
            raise ValueError('Replacement source review still requires revision')
        _, duplicate = self._append(feedback_id, 'resolve', {
            'review_event_id': review['event_id'], 'replacement_report_id': replacement_report_id,
            'replacement_report_sha256': _sha(replacement), 'source_review_checked': True,
            'evidence_ids': review['evidence_ids'], 'scope': SCOPE,
        }, known_at, current['last_event_id'])
        return {**self._get(feedback_id), 'duplicate': duplicate}
