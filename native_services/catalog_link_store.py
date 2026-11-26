"""Reversible, app-owned canonical identity links; never reads imported sources."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3

APP_ID = 0x434C4B31
VERSION = 1
MAX_IDS = 100_000


class Conflict(ValueError):
    """A preview is stale, a request ID collides, or undo is no longer safe."""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _text(value, maximum=512):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError('Expected bounded, nonempty text without edge whitespace/control characters')
    return value


def _groups(groups):
    if not isinstance(groups, list) or not 1 <= len(groups) <= 500:
        raise ValueError('Expected 1..500 explicitly approved groups')
    out, seen = [], set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'canonical_ids', 'representative_id'}:
            raise ValueError('Each group needs canonical_ids and representative_id')
        ids = group['canonical_ids']
        if not isinstance(ids, list) or len(ids) < 2 or len(ids) > MAX_IDS:
            raise ValueError('Each group needs at least two canonical IDs')
        ids = [_text(x) for x in ids]
        if len(ids) != len(set(ids)):
            raise ValueError('Repeated canonical ID in group')
        rep = _text(group['representative_id'])
        if rep not in ids:
            raise ValueError('Representative must be a member of its group')
        if seen.intersection(ids):
            raise ValueError('Groups must be disjoint')
        seen.update(ids)
        out.append({'canonical_ids': sorted(ids), 'representative_id': rep})
    return json.loads(_json(out))


@dataclass(frozen=True)
class Plan:
    revision: int
    groups_json: str
    before_json: str
    after_json: str

    def as_dict(self):
        return {'revision': self.revision, 'groups': json.loads(self.groups_json),
                'before': json.loads(self.before_json), 'after': json.loads(self.after_json)}


class CatalogLinkStore:
    """Own a separate SQLite overlay; only exact IDs supplied at create are linkable."""
    def __init__(self, selected_app_db):
        path = Path(selected_app_db)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError('Select an existing absolute app-owned database (no symlink)')
        self.path = path
        with self._connect() as db:
            if db.execute('PRAGMA application_id').fetchone()[0] != APP_ID or db.execute('PRAGMA user_version').fetchone()[0] != VERSION:
                raise ValueError('Not a supported Catalog Link app-owned database')
            db.execute('SELECT revision FROM link_meta').fetchone()

    @classmethod
    def create(cls, selected_app_db, canonical_ids):
        path = Path(selected_app_db)
        if not path.is_absolute(): raise ValueError('Select an absolute NEW app-owned database path')
        # An empty first library is valid; identities are added when games appear.
        if not isinstance(canonical_ids, (list, tuple)) or not 0 <= len(canonical_ids) <= MAX_IDS:
            raise ValueError('Supply 0..100000 exact canonical catalog IDs')
        ids = [_text(v) for v in canonical_ids]
        if len(ids) != len(set(ids)): raise ValueError('Repeated canonical IDs')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
        try:
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(f'''PRAGMA application_id={APP_ID}; PRAGMA user_version={VERSION};
                CREATE TABLE link_meta(revision INTEGER NOT NULL); INSERT INTO link_meta VALUES(0);
                CREATE TABLE canonical_ids(id TEXT PRIMARY KEY);
                CREATE TABLE links(member_id TEXT PRIMARY KEY REFERENCES canonical_ids(id), representative_id TEXT NOT NULL REFERENCES canonical_ids(id), CHECK(member_id<>representative_id));
                CREATE TABLE audit(id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, intent TEXT NOT NULL, kind TEXT NOT NULL, target INTEGER, revision INTEGER NOT NULL, before_state TEXT NOT NULL, after_state TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE UNIQUE INDEX one_undo ON audit(target) WHERE kind='undo';''')
                db.executemany('INSERT INTO canonical_ids VALUES(?)', ((v,) for v in ids))
        except BaseException:
            path.unlink(missing_ok=True); raise
        return cls(path)

    @contextmanager
    def _connect(self):
        if self.path.is_symlink() or not self.path.is_file(): raise ValueError('App database disappeared or became a symlink')
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if db.execute('PRAGMA application_id').fetchone()[0] != APP_ID or db.execute('PRAGMA user_version').fetchone()[0] != VERSION:
                raise ValueError('Not a supported Catalog Link database')
            db.execute('PRAGMA foreign_keys=ON'); yield db
        finally: db.close()

    def canonical_ids(self):
        with self._connect() as db: return tuple(r[0] for r in db.execute('SELECT id FROM canonical_ids ORDER BY id'))

    def register_ids(self, canonical_ids):
        """Add identities that appeared after creation; never removes one (links survive library changes)."""
        ids = {_text(v) for v in canonical_ids}
        with self._connect() as db:
            known = {r[0] for r in db.execute('SELECT id FROM canonical_ids')}
            new = sorted(ids - known)
            if len(known) + len(new) > MAX_IDS: raise ValueError('Supply bounded exact canonical IDs')
            if new:
                with db: db.executemany('INSERT OR IGNORE INTO canonical_ids VALUES(?)', ((v,) for v in new))
            return len(new)

    def resolution(self):
        """Return a detached member->representative mapping; source variants are untouched."""
        with self._connect() as db: return {r[0]: r[1] for r in db.execute('SELECT member_id,representative_id FROM links ORDER BY member_id')}

    def snapshot(self):
        with self._connect() as db:
            # Revision and resolution must describe one SQLite snapshot even if
            # another process applies a link concurrently.
            db.execute('BEGIN')
            return {'revision': db.execute('SELECT revision FROM link_meta').fetchone()[0],
                    'resolution': {r[0]: r[1] for r in db.execute(
                        'SELECT member_id,representative_id FROM links ORDER BY member_id')}}

    def _plan(self, db, groups):
        before = {r['member_id']: r['representative_id'] for r in db.execute('SELECT * FROM links')}
        after = dict(before); allids = set()
        for g in groups:
            ids, rep = g['canonical_ids'], g['representative_id']
            for ident in ids:
                if not db.execute('SELECT 1 FROM canonical_ids WHERE id=?', (ident,)).fetchone(): raise ValueError('Unknown canonical ID: ' + ident)
            if allids.intersection(ids): raise ValueError('Groups must be disjoint')
            allids.update(ids)
            if any(i in before or i in before.values() for i in ids): raise ValueError('Group conflicts with an existing link')
            for ident in ids:
                if ident != rep: after[ident] = rep
        return Plan(db.execute('SELECT revision FROM link_meta').fetchone()[0], _json(groups), _json(before), _json(after))

    def preview(self, groups):
        groups = _groups(groups)
        with self._connect() as db:
            db.execute('BEGIN'); return self._plan(db, groups)

    @staticmethod
    def _result(row):
        return {'audit_id': row['id'], 'request_id': row['request_id'], 'kind': row['kind'], 'target': row['target'], 'revision': row['revision'], 'before': json.loads(row['before_state']), 'after': json.loads(row['after_state'])}

    @staticmethod
    def _existing(db, request_id, intent):
        row = db.execute('SELECT * FROM audit WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row['intent'] != intent: raise Conflict('Request ID already used for a different operation')
            return CatalogLinkStore._result(row)

    def _write(self, db, request_id, intent, kind, target, revision, before, after):
        db.execute('DELETE FROM links')
        db.executemany('INSERT INTO links(member_id,representative_id) VALUES(?,?)', sorted(json.loads(after).items()))
        db.execute('UPDATE link_meta SET revision=?', (revision + 1,))
        cur = db.execute('INSERT INTO audit(request_id,intent,kind,target,revision,before_state,after_state) VALUES(?,?,?,?,?,?,?)', (request_id,intent,kind,target,revision+1,before,after))
        return self._result(db.execute('SELECT * FROM audit WHERE id=?', (cur.lastrowid,)).fetchone())

    def apply(self, plan: Plan, request_id: str):
        _text(request_id,128)
        if not isinstance(plan,Plan) or type(plan.revision) is not int or plan.revision < 0: raise ValueError('Expected preview Plan')
        intent = _json({'kind':'apply','plan':plan.as_dict()})
        with self._connect() as db, db:
            db.execute('BEGIN IMMEDIATE'); old=self._existing(db,request_id,intent)
            if old: return old
            try: current=self._plan(db,_groups(json.loads(plan.groups_json)))
            except (ValueError,TypeError,json.JSONDecodeError) as e: raise Conflict('Preview is stale or modified; preview again') from e
            if current != plan: raise Conflict('Preview is stale or modified; preview again')
            return self._write(db,request_id,intent,'apply',None,plan.revision,plan.before_json,plan.after_json)

    def undo(self, audit_id: int, request_id: str):
        _text(request_id,128)
        if type(audit_id) is not int or audit_id <= 0: raise ValueError('Expected positive audit ID')
        intent=_json({'kind':'undo','audit_id':audit_id})
        with self._connect() as db, db:
            db.execute('BEGIN IMMEDIATE'); old=self._existing(db,request_id,intent)
            if old: return old
            row=db.execute('SELECT * FROM audit WHERE id=?',(audit_id,)).fetchone(); rev=db.execute('SELECT revision FROM link_meta').fetchone()[0]
            if not row or row['kind']!='apply' or row['revision']!=rev: raise Conflict('Only the latest unchanged apply can be undone')
            return self._write(db,request_id,intent,'undo',audit_id,rev,row['after_state'],row['before_state'])

    def audit(self):
        with self._connect() as db: return [self._result(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 1000')]
