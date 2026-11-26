"""Explicit, local Organizer overrides. Never opens an import or launcher database.

Only ``create`` initializes a NEW user-selected app database. Canonical identities
must be supplied by the catalog owner; titles/source claims never identify games.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3

APP_ID = 0x4F524731
FIELDS = frozenset(('tags', 'categories', 'favorite', 'hidden', 'title', 'platform',
                    'description', 'completion', 'libraries', 'genres', 'developers', 'publishers'))
MAX_GAMES = 100_000


class Conflict(ValueError):
    """A preview or undo is stale, or a request identifier was reused."""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _text(value, maximum=256):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError('Expected bounded, nonempty text without edge whitespace/control characters')
    return value


def _edits(edits):
    if not isinstance(edits, list) or not 1 <= len(edits) <= 500:
        raise ValueError('Expected 1..500 explicitly selected edits')
    result, seen = [], set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'game_id', 'changes'}:
            raise ValueError('Each edit needs only game_id and changes')
        game_id = _text(edit['game_id'])
        if game_id in seen:
            raise ValueError('Repeated canonical game ID')
        seen.add(game_id)
        changes = edit['changes']
        if not isinstance(changes, dict) or not changes or not set(changes) <= FIELDS:
            raise ValueError('Unsupported library metadata override field')
        for key, value in changes.items():
            if value is None:
                continue  # Remove the native override; do not modify imported metadata.
            if key in ('title', 'platform', 'completion'):
                _text(value, 512 if key == 'title' else 128)
            elif key == 'description':
                if (not isinstance(value, str) or len(value) > 16384
                        or any(ord(c) < 32 and c not in '\n\t' for c in value)):
                    raise ValueError('Description must be plain text up to 16384 characters')
            elif key in ('favorite', 'hidden'):
                if type(value) is not bool:
                    raise ValueError('Flags must be bool or null')
            else:
                if not isinstance(value, list) or len(value) > 100:
                    raise ValueError('Labels must be lists of at most 100 strings or null')
                for label in value:
                    _text(label, 100)
                if len(set(value)) != len(value):
                    raise ValueError('Duplicate labels')
        result.append({'game_id': game_id, 'changes': changes})
    # Detach inputs; preserve exact label spelling and order.
    return json.loads(_json(result))


@dataclass(frozen=True)
class Plan:
    revision: int
    edits_json: str
    before_json: str
    after_json: str

    def as_dict(self):
        return {'revision': self.revision, 'edits': json.loads(self.edits_json),
                'before': json.loads(self.before_json), 'after': json.loads(self.after_json)}


class OrganizerStore:
    def __init__(self, selected_app_db):
        path = Path(selected_app_db)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError('Select an existing absolute app-owned database (no symlink)')
        self.path = path
        with self._connect() as db:
            if db.execute('PRAGMA application_id').fetchone()[0] != APP_ID:
                raise ValueError('Not an Organizer app-owned database')
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('Unsupported Organizer database version')
            db.execute('SELECT revision FROM organizer_meta').fetchone()

    @classmethod
    def create(cls, selected_app_db, canonical_ids):
        path = Path(selected_app_db)
        if not path.is_absolute():
            raise ValueError('Select an absolute NEW app-owned database path')
        # A new user's own library can start empty (no launchers yet); games are added as they appear.
        if not isinstance(canonical_ids, (list, tuple)) or not 0 <= len(canonical_ids) <= MAX_GAMES:
            raise ValueError('Supply 0..100000 canonical catalog IDs')
        ids = [_text(value) for value in canonical_ids]
        if len(ids) != len(set(ids)):
            raise ValueError('Ambiguous canonical IDs')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(f'''
                    PRAGMA application_id={APP_ID}; PRAGMA user_version=1;
                    CREATE TABLE organizer_meta(revision INTEGER NOT NULL);
                    INSERT INTO organizer_meta VALUES(0);
                    CREATE TABLE canonical_games(game_id TEXT PRIMARY KEY);
                    CREATE TABLE overrides(game_id TEXT PRIMARY KEY REFERENCES canonical_games(game_id), state TEXT NOT NULL);
                    CREATE TABLE audit(id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                        intent TEXT NOT NULL, kind TEXT NOT NULL, target INTEGER,
                        revision INTEGER NOT NULL, before_state TEXT NOT NULL, after_state TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                    CREATE UNIQUE INDEX one_undo ON audit(target) WHERE kind='undo';
                ''')
                db.executemany('INSERT INTO canonical_games VALUES(?)', ((v,) for v in ids))
        except BaseException:
            path.unlink()
            raise
        return cls(path)

    @contextmanager
    def _connect(self):
        # mode=rw prevents accidental recreation if an app DB has disappeared.
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError('App database disappeared or became a symlink')
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            # Revalidate on every operation, including when a pathname was replaced.
            if (db.execute('PRAGMA application_id').fetchone()[0] != APP_ID
                    or db.execute('PRAGMA user_version').fetchone()[0] != 1):
                raise ValueError('Not a supported Organizer app-owned database')
            db.execute('PRAGMA foreign_keys=ON')
            yield db
        finally:
            db.close()

    def canonical_ids(self) -> tuple[str, ...]:
        """Return detached, sorted exact catalog identities; never mutate the registry."""
        with self._connect() as db:
            return tuple(row[0] for row in db.execute('SELECT game_id FROM canonical_games ORDER BY game_id'))

    def register_ids(self, canonical_ids) -> int:
        """Add catalog identities that appeared after creation (new imports/installs). Never removes one, so
        saved edits survive a game leaving and returning. An exact-match check here used to hide ALL edits
        as soon as the library gained a single game."""
        ids = {_text(value) for value in canonical_ids}
        with self._connect() as db:
            known = {row[0] for row in db.execute('SELECT game_id FROM canonical_games')}
            new = sorted(ids - known)
            if len(known) + len(new) > MAX_GAMES:
                raise ValueError('Supply 0..100000 canonical catalog IDs')
            if new:
                with db:
                    db.executemany('INSERT OR IGNORE INTO canonical_games VALUES(?)', ((v,) for v in new))
            return len(new)

    def snapshot(self):
        with self._connect() as db:
            db.execute('BEGIN')
            return {'revision': db.execute('SELECT revision FROM organizer_meta').fetchone()[0],
                    'overrides': {r['game_id']: json.loads(r['state']) for r in db.execute('SELECT * FROM overrides ORDER BY game_id')}}

    def _preview(self, db, edits):
        before, after = {}, {}
        for edit in edits:
            game_id = edit['game_id']
            if not db.execute('SELECT 1 FROM canonical_games WHERE game_id=?', (game_id,)).fetchone():
                raise ValueError('Unknown canonical game ID: ' + game_id)
            row = db.execute('SELECT state FROM overrides WHERE game_id=?', (game_id,)).fetchone()
            before[game_id] = json.loads(row[0]) if row else None
            state = dict(before[game_id] or {})
            for key, value in edit['changes'].items():
                if value is None:
                    state.pop(key, None)
                else:
                    state[key] = value
            after[game_id] = state or None
        return Plan(db.execute('SELECT revision FROM organizer_meta').fetchone()[0],
                    _json(edits), _json(before), _json(after))

    def preview(self, edits):
        edits = _edits(edits)
        with self._connect() as db:
            db.execute('BEGIN')
            return self._preview(db, edits)

    @staticmethod
    def _existing(db, request_id, intent):
        row = db.execute('SELECT * FROM audit WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row['intent'] != intent:
                raise Conflict('Request ID already used for a different operation')
            return OrganizerStore._result(row)

    @staticmethod
    def _result(row):
        return {'audit_id': row['id'], 'request_id': row['request_id'], 'kind': row['kind'],
                'target': row['target'], 'revision': row['revision'],
                'before': json.loads(row['before_state']), 'after': json.loads(row['after_state'])}

    @staticmethod
    def _write(db, request_id, intent, kind, target, revision, before, after):
        for game_id, state in json.loads(after).items():
            if state is None:
                db.execute('DELETE FROM overrides WHERE game_id=?', (game_id,))
            else:
                db.execute('INSERT INTO overrides VALUES(?,?) ON CONFLICT(game_id) DO UPDATE SET state=excluded.state', (game_id, _json(state)))
        db.execute('UPDATE organizer_meta SET revision=?', (revision + 1,))
        cursor = db.execute('INSERT INTO audit(request_id,intent,kind,target,revision,before_state,after_state) VALUES(?,?,?,?,?,?,?)',
                            (request_id, intent, kind, target, revision + 1, before, after))
        return OrganizerStore._result(db.execute('SELECT * FROM audit WHERE id=?', (cursor.lastrowid,)).fetchone())

    def apply(self, plan: Plan, request_id: str):
        _text(request_id, 128)
        if not isinstance(plan, Plan) or type(plan.revision) is not int or plan.revision < 0:
            raise ValueError('Expected an Organizer preview Plan')
        if any(not isinstance(v, str) or len(v) > 4_000_000 for v in (plan.edits_json, plan.before_json, plan.after_json)):
            raise ValueError('Invalid or oversized plan')
        edits = _edits(json.loads(plan.edits_json))
        intent = _json({'kind': 'apply', 'plan': plan.as_dict()})
        with self._connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            existing = self._existing(db, request_id, intent)
            if existing:
                return existing
            if self._preview(db, edits) != plan:
                raise Conflict('Preview is stale or modified; preview again')
            return self._write(db, request_id, intent, 'apply', None, plan.revision, plan.before_json, plan.after_json)

    def undo(self, audit_id: int, request_id: str):
        _text(request_id, 128)
        if type(audit_id) is not int or audit_id <= 0:
            raise ValueError('Expected positive audit ID')
        intent = _json({'kind': 'undo', 'audit_id': audit_id})
        with self._connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            existing = self._existing(db, request_id, intent)
            if existing:
                return existing
            row = db.execute('SELECT * FROM audit WHERE id=?', (audit_id,)).fetchone()
            revision = db.execute('SELECT revision FROM organizer_meta').fetchone()[0]
            if not row or row['kind'] != 'apply' or row['revision'] != revision:
                raise Conflict('Only the latest unchanged apply can be undone')
            return self._write(db, request_id, intent, 'undo', audit_id, revision, row['after_state'], row['before_state'])

    def audit(self):
        with self._connect() as db:
            return [self._result(row) for row in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 1000')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Explicit absolute app-owned DB path')
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create'); create.add_argument('--canonical-id', action='append', required=True)
    sub.add_parser('snapshot'); sub.add_parser('audit')
    preview = sub.add_parser('preview'); preview.add_argument('--edits', required=True, help='Inline JSON list')
    apply = sub.add_parser('apply'); apply.add_argument('--plan', required=True, help='Inline JSON from preview'); apply.add_argument('--request-id', required=True)
    undo = sub.add_parser('undo'); undo.add_argument('--audit-id', type=int, required=True); undo.add_argument('--request-id', required=True)
    args = parser.parse_args()
    store = OrganizerStore.create(args.db, args.canonical_id) if args.command == 'create' else OrganizerStore(args.db)
    if args.command in ('create', 'snapshot'):
        result = store.snapshot()
    elif args.command == 'preview':
        result = store.preview(json.loads(args.edits)).as_dict()
    elif args.command == 'apply':
        raw = json.loads(args.plan)
        if set(raw) != {'revision', 'edits', 'before', 'after'}:
            raise ValueError('Invalid plan envelope')
        result = store.apply(Plan(raw['revision'], _json(raw['edits']), _json(raw['before']), _json(raw['after'])), args.request_id)
    elif args.command == 'undo':
        result = store.undo(args.audit_id, args.request_id)
    else:
        result = store.audit()
    print(_json(result))


if __name__ == '__main__':
    main()
