"""Best-effort durable observation of existing Herald work, never execution control."""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import closing
from functools import wraps
import json
import logging
import os
from pathlib import Path
import sqlite3
import uuid

from .sanitization import sanitize_error

CURRENT: ContextVar = ContextVar('herald_observation', default=None)
log = logging.getLogger(__name__)
ALLOWED = {'label','text','status','model','execution_host','inference_host','parent_run_id',
           'director_id','conversation_id','reply','summary','error','tool','tool_name','workspace'}


def store_path() -> Path:
    root = Path(os.environ.get('HERALD_DATA_DIR', str(Path.home()/'.herald')))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root/'observations.sqlite3'


class ObservationStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS observation_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, attempt TEXT NOT NULL, agent TEXT NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS observation_scope_seq ON observation_events(scope,seq)')
        if os.name != 'nt': self.path.chmod(0o600)

    def append(self, scope, attempt, agent, event, data):
        clean = {key: sanitize_error(value)[:64000 if key in ('reply','summary') else 4000]
                 for key,value in data.items() if key in ALLOWED and isinstance(value,(str,int,float,bool))}
        clean['conversation_id'] = scope
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('INSERT INTO observation_events(scope,attempt,agent,event,data) VALUES (?,?,?,?,?)',
                       (scope,attempt,agent,event,json.dumps(clean)))

    def events(self, scope, after=0, limit=500):
        if after < 0 or not 1 <= limit <= 500: raise ValueError('Invalid cursor or limit')
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            rows=db.execute('SELECT seq,attempt,agent,event,data FROM observation_events WHERE scope=? AND seq>? ORDER BY seq LIMIT ?', (scope,after,limit)).fetchall()
        return {'events':[{'seq':r[0],'attempt_id':r[1],'agent':r[2],'event':r[3],'data':json.loads(r[4])} for r in rows],
                'cursor':rows[-1][0] if rows else after}


class Observation:
    def __init__(self, agent, *, scope=None, attempt_id=None, model=None, store=None, silent=False):
        parent = CURRENT.get()
        self.scope = scope or (parent.scope if parent else 'herald')
        self.attempt = attempt_id or str(uuid.uuid4())
        self.agent, self.model = agent, model
        self.parent = parent.attempt if parent else None
        self.store = store
        self.silent = silent
        self.finished = False
        self.token = None

    def emit(self, event, **data):
        if self.silent or (self.finished and event not in ("complete", "error")):return
        try:
            if self.store is None: self.store = ObservationStore()
            self.store.append(self.scope,self.attempt,self.agent,event,
                {'parent_run_id':self.parent or '', 'model':self.model or '', **data})
        except Exception:
            log.warning('Worker observation unavailable; execution continues')

    def __enter__(self):
        self.token = CURRENT.set(self)
        self.emit('created',label=f'Starting {self.agent}',status='running')
        return self

    def finish(self, result=None):
        if self.finished: return
        self.finished = True
        if isinstance(result,dict):
            error=result.get('error')
            text=result.get('reply') or result.get('content') or result.get('summary') or result.get('output')
            if not error and result.get('status') in ('failed','needs_revision'): error=result.get('status')
        else:
            error=getattr(result,'error',None)
            text=getattr(result,'output',None) or getattr(result,'summary',None) or (result if isinstance(result,str) else None)
            if getattr(result,'success',True) is False and not error: error='Worker did not complete successfully'
        self.emit('error' if error else 'complete',status='failed' if error else 'completed',
                  **({'error':str(error)} if error else {'reply':str(text or 'Work completed; see the controller result for details.')}))

    def __exit__(self, kind, value, traceback):
        try:
            self.finish({'error':str(value)} if kind else None)
        finally:
            CURRENT.reset(self.token)


def observed(agent, *, scope=None, nested=True):
    """Observe one existing synchronous call. Never catch/retry its execution."""
    def decorate(function):
        @wraps(function)
        def run(*args,**kwargs):
            if not nested and CURRENT.get() is not None:
                return function(*args,**kwargs)
            label=agent(*args,**kwargs) if callable(agent) else agent
            with Observation(label,scope=scope,attempt_id=kwargs.get('attempt_id'),model=kwargs.get('backend_model')) as observation:
                result=function(*args,**kwargs)
                observation.finish(result)
                return result
        return run
    return decorate


def record_progress(kind, payload):
    observation=CURRENT.get()
    if observation is None:return
    text=(payload.get('text') or payload.get('commentary')) if kind in ('commentary','assistant_commentary') else None
    tool=payload.get('tool') or payload.get('tool_name')
    event='commentary' if text else ('tool' if tool else 'progress')
    observation.emit(event,label=payload.get('label') or kind.replace('_',' '),
                     **({'text':text} if isinstance(text,str) else {}),
                     **({'tool_name':str(tool)} if tool else {}))
