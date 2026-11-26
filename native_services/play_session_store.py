"""App-owned durable playtime, independent of imported launcher statistics.

One writer per database; open once at app startup, not once per poll. All inputs
come from the caller. No process inspection, launch, or default production path.
Only intervals bounded by two verified running observations sharing a non-wrapper
(PID, starttime) count. Unknown gaps and the final unobserved exit tail do not.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Callable
import uuid

from .game_process_tracker import TrackedGameSession, is_excluded_wrapper


@dataclass(frozen=True)
class GameIdentity:
    """Explicit app ID plus explicit provider/product ID; never a title match."""
    canonical_game_id: str
    provider: str
    product_id: str

    def __post_init__(self):
        for name in ('canonical_game_id', 'provider', 'product_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a nonempty string')
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, 'provider', self.provider.lower())


def _number(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('time/count must be finite and nonnegative')
    return value


class PlaySessionStore:
    """SQLite session ledger. Reopen recovers unfinished sessions as interrupted.

    sample_id must identify one observation, retained unchanged for retries. A new
    sample cannot travel backwards in monotonic time within this store lifetime.
    max_gap_seconds bounds gaps from suspended UI timers; skipped gaps are not
    retroactively recovered. Wall time is audit-only and never computes duration.
    """
    def __init__(self, path: str | Path, *, monotonic: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time, max_gap_seconds: float = 30.0):
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self.max_gap_seconds = _number(max_gap_seconds)
        if not self.max_gap_seconds:
            raise ValueError('max_gap_seconds must be positive')
        self.run_id = uuid.uuid4().hex
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS play_store_meta(version INTEGER NOT NULL);
            INSERT INTO play_store_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM play_store_meta);
            CREATE TABLE IF NOT EXISTS play_products(
                provider TEXT NOT NULL, product_id TEXT NOT NULL, game_id TEXT NOT NULL,
                PRIMARY KEY(provider, product_id));
            CREATE TABLE IF NOT EXISTS play_sessions(
                session_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
                provider TEXT NOT NULL, product_id TEXT NOT NULL,
                state TEXT NOT NULL, seconds REAL NOT NULL DEFAULT 0,
                ever_running INTEGER NOT NULL DEFAULT 0,
                run_id TEXT, last_mono REAL, last_processes TEXT NOT NULL DEFAULT '[]',
                last_verified INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS play_events(
                session_id TEXT NOT NULL REFERENCES play_sessions(session_id),
                sample_id TEXT NOT NULL, payload TEXT NOT NULL,
                delta_seconds REAL NOT NULL, observed_at REAL NOT NULL,
                PRIMARY KEY(session_id, sample_id));
            CREATE TABLE IF NOT EXISTS play_imports(
                game_id TEXT NOT NULL, source TEXT NOT NULL,
                seconds REAL NOT NULL, completed_sessions INTEGER NOT NULL,
                PRIMARY KEY(game_id, source));
        ''')
        if self.db.execute('SELECT version FROM play_store_meta').fetchone()[0] != 1:
            self.db.close()
            raise ValueError('unsupported playtime schema')
        with self.db:
            self.db.execute("UPDATE play_sessions SET state='interrupted', last_verified=0 "
                            "WHERE state IN ('launching','running','unknown')")

    def close(self) -> None:
        """Close DB only. No inferred completion or extra elapsed time."""
        self.db.close()

    def start(self, session: TrackedGameSession, identity: GameIdentity, *, sample_id: str,
              monotonic_at: float | None = None, observed_at: float | None = None,
              verified: bool = True, launcher_is_game: bool = False) -> dict:
        """Register tracker result; launcher_is_game opts into a verified direct game/emulator PID."""
        return self.poll(session, identity, sample_id=sample_id, monotonic_at=monotonic_at,
                         observed_at=observed_at, verified=verified, launcher_is_game=launcher_is_game)

    def stop(self, session: TrackedGameSession, identity: GameIdentity, **kwargs) -> dict:
        """Persist a tracker-confirmed terminal observation; never stops a process."""
        if session.state not in ('closed', 'failed'):
            raise ValueError('stop requires a tracker closed/failed observation')
        return self.poll(session, identity, **kwargs)

    def poll(self, session: TrackedGameSession, identity: GameIdentity, *, sample_id: str,
             monotonic_at: float | None = None, observed_at: float | None = None,
             verified: bool = True, launcher_is_game: bool = False) -> dict:
        if not isinstance(sample_id, str) or not sample_id.strip() or not session.session_id:
            raise ValueError('session_id and sample_id are required')
        if session.state not in ('launching', 'running', 'closed', 'failed', 'unknown'):
            raise ValueError('unsupported tracker state')
        mono = _number(self.monotonic() if monotonic_at is None else monotonic_at)
        wall = _number(self.wall_clock() if observed_at is None else observed_at)
        processes = sorted({(p.pid, p.starttime) for p in session.tracked_processes.values()
                            if p.pid > 0 and p.starttime >= 0
                            and (p.pid != session.launcher_pid or launcher_is_game)
                            and not is_excluded_wrapper(p.comm, custom_exclusions=session.exclude_processes)})
        active = bool(verified and session.state == 'running' and processes)
        payload = json.dumps([identity.canonical_game_id, identity.provider, identity.product_id,
                              session.state, mono, processes, active, launcher_is_game], separators=(',', ':'))
        sid = session.session_id
        with self.db:
            # IMMEDIATE locks before reading so concurrent transactions cannot credit twice.
            self.db.execute('BEGIN IMMEDIATE')
            previous = self.db.execute('SELECT payload FROM play_events WHERE session_id=? AND sample_id=?',
                                       (sid, sample_id)).fetchone()
            if previous:
                if previous['payload'] != payload:
                    raise ValueError('sample_id reused with different observation')
                return self.session(sid)
            product = self.db.execute('SELECT game_id FROM play_products WHERE provider=? AND product_id=?',
                                      (identity.provider, identity.product_id)).fetchone()
            if product and product['game_id'] != identity.canonical_game_id:
                raise ValueError('provider/product already belongs to another canonical game')
            self.db.execute('INSERT OR IGNORE INTO play_products VALUES(?,?,?)',
                            (identity.provider, identity.product_id, identity.canonical_game_id))
            old = self.db.execute('SELECT * FROM play_sessions WHERE session_id=?', (sid,)).fetchone()
            delta = 0.0
            if old:
                if (old['game_id'], old['provider'], old['product_id']) != (
                        identity.canonical_game_id, identity.provider, identity.product_id):
                    raise ValueError('session identity cannot change')
                if old['state'] in ('closed', 'failed'):
                    raise ValueError('terminal session requires a new session_id')
                if old['run_id'] == self.run_id:
                    if mono < old['last_mono']:
                        raise ValueError('out-of-order monotonic observation')
                    gap = mono - old['last_mono']
                    old_processes = {tuple(p) for p in json.loads(old['last_processes'])}
                    if active and old['last_verified'] and old_processes.intersection(processes) \
                            and gap <= self.max_gap_seconds:
                        delta = gap
                self.db.execute('''UPDATE play_sessions SET state=?, seconds=seconds+?,
                    ever_running=MAX(ever_running,?), run_id=?, last_mono=?, last_processes=?,
                    last_verified=?, updated_at=? WHERE session_id=?''',
                    (session.state, delta, int(active), self.run_id, mono, json.dumps(processes),
                     int(active), wall, sid))
            else:
                self.db.execute('''INSERT INTO play_sessions(session_id,game_id,provider,product_id,
                    state,ever_running,run_id,last_mono,last_processes,last_verified,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (sid, identity.canonical_game_id, identity.provider, identity.product_id,
                     session.state, int(active), self.run_id, mono, json.dumps(processes), int(active), wall, wall))
            self.db.execute('INSERT INTO play_events VALUES(?,?,?,?,?)', (sid, sample_id, payload, delta, wall))
        return self.session(sid)

    def session(self, session_id: str) -> dict:
        row = self.db.execute('SELECT * FROM play_sessions WHERE session_id=?', (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return dict(row)

    def set_imported_totals(self, game_id: str, *, source: str, seconds: float,
                            completed_sessions: int = 0) -> None:
        """Replace one migration baseline; never credit it to native session totals.

        source should be one stable migration dataset ID, not a new ID per import.
        Different sources remain distinct; callers must NOT sum overlapping imports.
        """
        seconds = _number(seconds)
        if not game_id.strip() or not source.strip():
            raise ValueError('game_id/source required')
        if isinstance(completed_sessions, bool) or not isinstance(completed_sessions, int) or completed_sessions < 0:
            raise ValueError('completed_sessions must be a nonnegative integer')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO play_imports VALUES(?,?,?,?)',
                            (game_id, source, seconds, completed_sessions))

    def totals(self, game_id: str) -> dict:
        rows = self.db.execute('SELECT state,seconds,ever_running,last_verified FROM play_sessions WHERE game_id=?',
                               (game_id,)).fetchall()
        completed = [r for r in rows if r['state'] == 'closed' and r['ever_running']]
        live = [r for r in rows if r['state'] == 'running' and r['last_verified']]
        return {
            'native_seconds': sum(r['seconds'] for r in rows),
            'completed_seconds': sum(r['seconds'] for r in completed),
            'completed_sessions': len(completed),
            'live_seconds': sum(r['seconds'] for r in live),
            'live_sessions': len(live),
            'interrupted_seconds': sum(r['seconds'] for r in rows if r['state'] == 'interrupted'),
            'imported_totals': [dict(r) for r in self.db.execute(
                'SELECT source,seconds,completed_sessions FROM play_imports WHERE game_id=? ORDER BY source',
                (game_id,))],
        }
