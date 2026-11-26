"""Read-only windows into existing UA workers, tiled beside the local director.

Only GET observation requests are made. Closing a view never cancels or reruns
work. Herdr placement uses this frontend's own context, never remote MCP env.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Callable

from .client import RouterClient
from .herdr import Herdr

_TERMINAL_ESCAPE = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]')


def display_text(value: Any, limit: int = 16000) -> str:
    """Render untrusted worker text without terminal control sequences."""
    text = _TERMINAL_ESCAPE.sub('', str(value))
    return ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))[:limit]


class ObservationClient:
    def __init__(self, client: RouterClient | None = None):
        self.client = client or RouterClient(os.environ.get('HERALD_URL', 'http://127.0.0.1:8790'), timeout=10)
        self.token: str | None = None

    def __call__(self, conversation_id: str, after: int) -> dict:
        for refresh in (False, True):
            if not self.token or refresh:
                html = self.client.relay_request('GET', 'main', '/', timeout=10).text
                match = re.search(r'name=[\'\"]app-token[\'\"]\s+content=[\'\"]([^\'\"]+)', html, re.I)
                if not match:
                    match = re.search(r'content=[\'\"]([^\'\"]+)[\'\"]\s+name=[\'\"]app-token[\'\"]', html, re.I)
                if not match:
                    raise RuntimeError('UA app token is unavailable')
                self.token = match[1]
            try:
                return self.client.relay_request('GET', 'main', '/api/agents/observations',
                    params={'conversation_id': conversation_id, 'after': after}, timeout=10,
                    headers={'x-ultimate-assistant-token': self.token, 'x-assistant-token': self.token}).json()
            except Exception as exc:
                if not refresh and getattr(getattr(exc, 'response', None), 'status_code', None) in (401, 403):
                    continue
                raise
        raise RuntimeError('Observation authentication failed')


class RouterObservationClient:
    def __init__(self, client=None):
        self.client = client or RouterClient(os.environ.get('HERALD_URL', 'http://127.0.0.1:8790'))

    def __call__(self, conversation_id, after):
        return self.client.observation_events(conversation_id, after)


class CombinedObservationClient:
    """Separate durable source cursors; a failed source never hides the other."""
    def __init__(self):
        self.sources = {'ua': ObservationClient(), 'herald': RouterObservationClient()}
        self.cursors = {name: 0 for name in self.sources}
        self.seq = 0

    def __call__(self, conversation_id, after):
        events, failures = [], 0
        for name, source in self.sources.items():
            try:
                batch = source(conversation_id, self.cursors[name])
            except Exception:
                failures += 1
                continue
            for event in batch.get('events', []):
                self.seq += 1
                events.append({**event, 'seq': self.seq, 'attempt_id': name + ':' + event['attempt_id']})
            self.cursors[name] = batch.get('cursor', self.cursors[name])
        if failures == len(self.sources):
            raise RuntimeError('Observation sources unavailable')
        return {'events': events, 'cursor': self.seq}


class WorkerObserver:
    """Frontend-owned read-only view manager. No execution API is available here."""
    def __init__(self, conversation_id: str, fetch: Callable | None = None, *,
                 cli: Any = None, root: Path | None = None, interval: float = 0.7,
                 max_tiles: int = 6, source: str = "both", on_error: Callable[[str], None] | None = None):
        self.conversation_id = conversation_id
        self.fetch = fetch or (RouterObservationClient() if source == "router" else
                               ObservationClient() if source == "ua" else CombinedObservationClient())
        self.cli = cli or Herdr()
        self.interval = interval
        self.max_tiles = max(1, min(max_tiles, 12))
        self.on_error = on_error or (lambda message: None)
        self.anchor = os.environ.get('HERDR_PANE_ID')
        self.workspace = os.environ.get('HERDR_WORKSPACE_ID')
        self.tab = os.environ.get('HERDR_TAB_ID')
        self.root = root
        self.cursor = 0
        self.owner = uuid.uuid4().hex
        self.views: dict[str, dict] = {}
        self.retired: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    def start(self) -> 'WorkerObserver':
        if self._thread and self._thread.is_alive():
            return self
        if os.environ.get('HERDR_ENV') != '1':
            return self  # Normal terminal/headless usage remains supported.
        if not all((self.anchor, self.workspace, self.tab)):
            self.on_error('Worker tiles need the local frontend pane, tab and workspace context')
            return self
        if self.root is None:
            self.root = Path(tempfile.mkdtemp(prefix='herald-worker-views-'))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != 'nt':
            self.root.chmod(0o700)
        self._thread = threading.Thread(target=self._loop, daemon=True, name='ua-worker-observer')
        self._thread.start()
        return self

    def stop(self, *, close_views: bool = True) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=12)
        for view in self.views.values():
            if close_views and view.get('pane'):
                try:
                    current = self.cli.call('agent', 'get', view['pane']).get('agent', {})
                    # Only close a pane still occupied by this manager's own
                    # observer. A user-replaced occupant belongs to the user.
                    if (current.get('tokens') or {}).get('observer_owner') == self.owner:
                        self.cli.call('pane', 'close', view['pane'])
                        view['detached'] = True
                except Exception:
                    pass
            if view.get('path') and not view.get('detached'):
                self._append(view, {'event': 'detached', 'data': {'label': 'View detached; task continues under its director.'}})
                view['detached'] = True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
                self._last_error = None
            except Exception:
                # Network bodies/credentials must never be sent to panes or stderr.
                message = 'Worker view connection unavailable; retrying observation only.'
                if message != self._last_error:
                    self.on_error(message)
                    self._last_error = message
            for view in self.views.values():
                if view.get('path'):
                    view['path'].touch()  # Local viewer heartbeat; never a worker mutation.
            self._stop.wait(self.interval)

    def _target(self) -> tuple[str, str]:
        layout = self.cli.call('pane', 'layout', '--pane', self.anchor)['layout']
        if layout.get('tab_id') != self.tab:
            raise RuntimeError('Frontend moved to a different tab; observation placement stopped')
        panes = {p['pane_id']: p['rect'] for p in layout.get('panes', [])}
        candidates = [(v['pane'], panes[v['pane']]) for v in self.views.values()
                      if v.get('pane') in panes]
        if not candidates:
            return self.anchor, 'right'
        pane, rect = max(candidates, key=lambda pair: pair[1]['width'] * pair[1]['height'])
        direction = 'right' if rect['width'] >= rect['height'] * 2.6 else 'down'
        return pane, direction

    @staticmethod
    def _append(view: dict, event: dict) -> None:
        with view['path'].open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=True) + '\n')

    def _create(self, attempt: str, agent: str) -> dict:
        # Record before mutation: a lost CLI acknowledgement must not create a
        # second pane. Observation can fail without replaying the actual worker.
        view: dict = {'pane': None, 'path': None, 'ended': False}
        # Completed view processes keep reading, so a later worker can reuse its
        # tile without typing commands into a shell or duplicating a task.
        if len(self.views) >= self.max_tiles:
            idle = next(((key, item) for key, item in self.views.items()
                         if item.get('ended') and item.get('pane')), None)
            if idle is None:
                raise RuntimeError('All observation tiles are busy')
            key, view = idle
            del self.views[key]
            self.retired.add(key)
            view['ended'] = False
            self.views[attempt] = view
            self.cli.call('pane', 'rename', view['pane'], display_text(agent, 50) or 'Worker')
            return view
        self.views[attempt] = view
        import hashlib
        name = hashlib.sha256(attempt.encode()).hexdigest()[:24]
        path = self.root / (name + '.jsonl')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        view['path'] = path
        target, direction = self._target()
        result = self.cli.call('pane', 'split', '--pane', target, '--direction', direction,
                               '--ratio', '0.5', '--no-focus', '--cwd', str(Path.cwd()),
                               '--env', f'HERALD_OBSERVER_FEED={path}',
                               '--env', f'HERALD_OBSERVER_OWNER={self.owner}',
                               '--env', f'PYTHONPATH={os.pathsep.join(sys.path)}')
        pane = result['pane']['pane_id']
        view['pane'] = pane
        self.cli.call('pane', 'rename', pane, display_text(agent, 50) or 'Worker')
        args = [sys.executable, '-m', 'herald.herdr_observer', '--view']
        command = ('& ' + ' '.join("'" + a.replace("'", "''") + "'" for a in args)
                   if os.name == 'nt' else shlex.join(args))
        self.cli.call('pane', 'run', pane, command)
        return view

    def poll_once(self) -> None:
        batch = self.fetch(self.conversation_id, self.cursor)
        if self._stop.is_set():
            return
        events = batch.get('events', [])
        for event in events:
            seq = event.get('seq')
            if not isinstance(seq, int) or seq <= self.cursor:
                continue
            data = event.get('data') or {}
            # Enforce scope again locally even if a misconfigured endpoint leaks events.
            if data.get('conversation_id') != self.conversation_id:
                self.cursor = seq
                continue
            attempt = event.get('attempt_id')
            if not isinstance(attempt, str) or not attempt:
                self.cursor = seq
                continue
            if attempt in self.retired:
                self.cursor = seq
                continue
            if attempt not in self.views:
                if len(self.views) >= self.max_tiles and not any(v.get('ended') for v in self.views.values()):
                    self.on_error('Worker tile limit reached; remaining runs continue under the director.')
                    self.retired.add(attempt)
                    self.cursor = seq
                    continue
                try:
                    self._create(attempt, str(event.get('agent') or 'Worker'))
                except Exception:
                    self.on_error('Worker tile creation failed; work continues. No task was retried.')
            view = self.views.get(attempt)
            if view and view.get('path') and view.get('pane'):
                # Files contain display events only, never auth tokens/request payloads.
                self._append(view, event)
                if event.get('event') in ('complete', 'error'):
                    view['ended'] = True
            self.cursor = seq
        cursor = batch.get('cursor', self.cursor)
        if isinstance(cursor, int):
            self.cursor = max(self.cursor, cursor)


def start_local_observer(scope: str):
    """Best-effort local SDK/CLI display; never block the existing controller."""
    if os.environ.get('HERDR_ENV') != '1':
        return None
    try:
        from .observations import ObservationStore
        return WorkerObserver(scope, fetch=ObservationStore().events).start()
    except Exception:
        return None


def render_event(event: dict) -> tuple[str, bool]:
    kind = event.get('event', '')
    data = event.get('data') or {}
    lines = []
    if kind == 'created':
        lines.append(f"{event.get('agent', 'Worker')} · observing · {event.get('attempt_id', '')}")
    if kind in ('created', 'progress', 'tool'):
        label = data.get('label') or data.get('status') or data.get('tool')
        if label:
            lines.append(str(label))
        location = {**(data.get('execution') or {}), **data}
        for key in ('model', 'execution_host', 'inference_host'):
            if location.get(key):
                lines.append(f'{key}: {location[key]}')
    elif kind == 'commentary':
        lines.append(str(data.get('text') or data.get('label') or ''))
    elif kind == 'complete':
        lines.extend(['Completed', str(data.get('reply') or data.get('summary') or '')])
    elif kind == 'error':
        lines.extend(['Failed', str(data.get('error') or data.get('label') or 'Worker failed')])
    elif kind == 'detached':
        lines.append(str(data.get('label', 'View detached; task continues.')))
    return display_text('\n'.join(lines)), kind in ('complete', 'error', 'detached')


def view_feed(path: Path) -> None:
    # Feed location is passed only through this local pane's environment.
    from .herdr import Herdr
    cli = Herdr()
    pane = os.environ.get('HERDR_PANE_ID')
    def report(state: str, message: str, event: dict | None = None) -> None:
        if not pane:
            return
        try:
            cli.call('pane', 'report-agent', pane, '--source', 'herald:worker-view',
                     '--agent', 'Herald', '--state', state, '--message', message,
                     '--seq', str(time.time_ns() // 1000))
            tokens = ['--token', 'execution_mode=observer', '--token', 'read_only=true',
                      '--token', 'observer_owner=' + os.environ.get('HERALD_OBSERVER_OWNER', '')]
            if event:
                data = event.get('data') or {}
                location = {**(data.get('execution') or {}), **data}
                for key, value in {'run_id': event.get('attempt_id'), 'profile': event.get('agent'),
                                   **{key: location.get(key) for key in ('model', 'execution_host', 'inference_host')}}.items():
                    if value:
                        tokens.extend(['--token', f'{key}={display_text(value, 200)}'])
            cli.call('pane', 'report-metadata', pane, '--source', 'herald:worker-view', *tokens)
        except Exception:
            pass
    report('working', 'Observing worker')
    print('Worker view · read only · director owns execution\n', flush=True)
    with path.open(encoding='utf-8') as stream:
        while True:
            position = stream.tell()
            line = stream.readline()
            if not line.endswith('\n'):
                stream.seek(position)
                if time.time() - path.stat().st_mtime > 60:
                    print('View disconnected; the director still owns the task.', flush=True)
                    report('idle', 'Observation disconnected')
                    return
                time.sleep(0.2)
                continue
            try:
                event = json.loads(line)
                text, done = render_event(event)
            except (ValueError, TypeError, AttributeError):
                continue
            if event.get('event') == 'created':
                report('working', display_text(event.get('agent', 'Worker'), 80), event)
                print('\n' + '─' * 40, flush=True)
            if text:
                print(text, flush=True)
            if done:
                report('idle', 'View detached' if event.get('event') == 'detached' else 'Worker finished', event)
                if event.get('event') == 'detached':
                    return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--view', action='store_true')
    parser.add_argument('--conversation')
    parser.add_argument('--source', choices=['ua', 'router', 'both'], default='both')
    args = parser.parse_args()
    if args.view:
        view_feed(Path(os.environ['HERALD_OBSERVER_FEED']))
        return
    if not args.conversation:
        parser.error('--conversation is required')
    observer = WorkerObserver(args.conversation, source=args.source,
                              on_error=lambda message: print(message, file=sys.stderr)).start()
    try:
        # Pi owns the pipe. EOF on shutdown detaches displays without affecting work.
        for _line in sys.stdin:
            pass
    finally:
        observer.stop()


if __name__ == '__main__':
    main()
