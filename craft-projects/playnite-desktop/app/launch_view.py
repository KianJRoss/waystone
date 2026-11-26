"""Presentation and execution adapter for game launch profiles, ROM inventory, and client handoff.

Bounded execution boundary:
- Uses backend services: native_services.launch_profiles.LaunchProfileStore,
  native_services.launch_profiles.launch, and native_services.rom_inventory.RomInventoryStore.
- Never executes games or emulators during scans, queries, or view creation.
- Explicit Play trigger required.
- Injection boundary: accepts mock/fake spawn callable in tests or production;
  never spawns actual game processes during testing or scanning.
- Arguments are strictly string arrays (list[str]), never raw shell string concatenations.
- Validates executable presence and X_OK before attempting handoff.
- Tracks status (idle, handed_off, failed), last PID, and actionable fix suggestions.
"""
from __future__ import annotations

import logging
import os
import copy
import json
import platform
import re
import shlex
import shutil
import stat
import sys
import uuid
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer

# Ensure native_services can be imported from candidate root
CANDIDATE_ROOT = Path(__file__).resolve().parents[3]
if str(CANDIDATE_ROOT) not in sys.path:
    sys.path.insert(0, str(CANDIDATE_ROOT))

try:
    from native_services.launch_profiles import (
        LaunchProfile,
        LaunchProfileStore,
        LaunchIntent,
        LaunchResult,
        launch,
    )
    from native_services.launcher_owner import build_owned_launch_intent
    from native_services import game_monitor
except ImportError:
    LaunchProfile = None
    LaunchProfileStore = None
    LaunchIntent = None
    LaunchResult = None
    launch = None
    build_owned_launch_intent = None
    game_monitor = None

try:
    from native_services.rom_inventory import (
        RomInventoryStore,
        RomInventoryScanner,
        RomRecord,
    )
except ImportError:
    RomInventoryStore = None
    RomInventoryScanner = None
    RomRecord = None

MAX_TEXT_LEN = 1024
MAX_DISPLAY_RECORDS = 20000


def _safe_text(value: Any, max_len: int = MAX_TEXT_LEN) -> str:
    """Format safe display text. For paths, IDs, and titles, do not truncate."""
    if not isinstance(value, str):
        return ''
    cleaned = ''.join(c if c.isprintable() else ' ' for c in value).strip()
    return cleaned


def _sanitize_args(args: Any) -> list[str]:
    """Ensure args is a safe list of non-empty string arguments."""
    if isinstance(args, str):
        try:
            tokens = shlex.split(args)
        except ValueError:
            tokens = args.split()
        return [_safe_text(t) for t in tokens if _safe_text(t)]
    if isinstance(args, (list, tuple)):
        result = []
        for item in args:
            s = _safe_text(str(item))
            if s:
                result.append(s)
        return result
    return []


_GAME_SCOPE = re.compile(r'/(waystone-game-[0-9a-f]{32}\.scope)$')


def _game_scope_of(pid: int) -> str | None:
    try:
        for line in Path(f'/proc/{pid}/cgroup').read_text(encoding='utf-8').splitlines():
            match = _GAME_SCOPE.search(line.strip())
            if match:
                return match.group(1)
    except OSError:
        pass
    return None


def _proc_starttime(pid: int) -> int | None:
    try:
        raw = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8')
        return int(raw[raw.rindex(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _stop_session_processes(launcher_pid, pids, idents) -> bool:
    import signal
    import subprocess
    own = {os.getpid(), os.getppid()}
    scopes = {scope for pid in pids if pid not in own for scope in [_game_scope_of(pid)] if scope}
    for scope in sorted(scopes):
        try:
            subprocess.Popen(['systemctl', '--user', 'stop', scope], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            scopes.discard(scope)
    sent = bool(scopes)
    # Launchers are started in their own session, so their process group id is their own pid.
    if launcher_pid and launcher_pid not in own:
        try:
            if os.getpgid(launcher_pid) == launcher_pid and launcher_pid != os.getpgid(0):
                os.killpg(launcher_pid, signal.SIGTERM)
                sent = True
        except OSError:
            pass
    for pid, ident in idents.items():
        if pid in own or _proc_starttime(pid) != getattr(ident, 'starttime', None):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            sent = True
        except OSError:
            pass
    return sent


_OWNER_STATUS_NAME = re.compile(r'^[A-Za-z0-9_.-]+-[0-9a-f]{12}\.json$')


def _prune_owner_status_files(root: Path, max_age_seconds: float = 3 * 86400) -> None:
    """Remove old per-launch status files written by the launcher owner (never other status files)."""
    try:
        cutoff = time.time() - max_age_seconds
        for path in root.glob('*.json'):
            if _OWNER_STATUS_NAME.match(path.name) and path.stat().st_mtime < cutoff:
                path.unlink()
    except OSError:
        pass


_LOG = logging.getLogger('waystone.launch')

class LaunchView(QObject):
    """QObject adapter providing launch profiles, ROM inventory management, and client handoff."""

    changed = Signal()
    launched = Signal(str, int)          # target_id, pid
    launchFailed = Signal(str, str)      # target_id, error_message
    profilesChanged = Signal()
    romsChanged = Signal()
    playtimeChanged = Signal()
    monitorsChanged = Signal()
    controllerSessionObserved = Signal(str, str, str)  # session id, immutable canonical id, state

    def __init__(
        self,
        launch_profile_root: Optional[str | Path] = None,
        rom_config_dir: Optional[str | Path] = None,
        spawn_fn: Optional[Callable] = None,
        parent: Optional[QObject] = None,
        process_tracker=None,
        play_session_store=None,
        play_session_path: Optional[str | Path] = None,
        playtime_verifier: Optional[Callable] = None,
        epic_engine_path: Optional[str | Path] = None,
        epic_config_root: Optional[str | Path] = None,
        stop_fn: Optional[Callable] = None,
        launcher_owner_enabled: bool | None = None,
        launcher_status_root: Optional[str | Path] = None,
        monitor_settings=None,
        monitor_backend=None,
    ):
        super().__init__(parent)
        self._spawn_fn = spawn_fn
        self._prelaunch_guard = None
        self._guard_takes_title = False
        self._launch_environment_provider = None
        self._pending_launch = None
        self._launch_selection_revision = 0
        self._continuing_guarded_launch = False
        # Fake spawns never inspect /proc or invoke Hyprland unless a test opts in.
        self._launcher_owner_enabled = (spawn_fn is None) if launcher_owner_enabled is None else bool(launcher_owner_enabled)
        self._launcher_status_root = Path(launcher_status_root).expanduser() if launcher_status_root else (
            Path.home() / '.local' / 'state' / 'waystone' / 'launch-status')
        self._active_status_file: str | None = None
        # Which monitor games open on (Settings > Games, per game next to Play). Production only by default:
        # fake spawns never touch the real settings file or the compositor unless a test injects them.
        if game_monitor is not None and (monitor_settings is not None or spawn_fn is None):
            self._monitor_settings = monitor_settings or game_monitor.GameMonitorSettings()
            self._monitor_backend = monitor_backend or game_monitor.HyprlandMonitors()
            self._placer = game_monitor.GameWindowPlacer(self._monitor_backend)
        else:
            self._monitor_settings = self._monitor_backend = self._placer = None
        self._monitor_cache: list[dict] = []
        self._monitor_revision = 0
        if self._monitor_backend is not None:
            self.refreshMonitors()
        self._epic_engine_path = str(epic_engine_path) if epic_engine_path else None
        self._epic_config_root = str(epic_config_root) if epic_config_root else None
        self._epic_plan_identity = None
        self._epic_runtime_error = ''
        # Fake spawns never inspect the host unless a fixture tracker is explicit.
        from native_services.game_process_tracker import GameProcessTracker
        self._tracker = process_tracker if process_tracker is not None else (GameProcessTracker() if spawn_fn is None else None)
        if play_session_store is not None and play_session_path is not None:
            raise ValueError('Inject either play_session_store or play_session_path, not both')
        self._owns_playtime_store = play_session_path is not None
        if play_session_path is not None:
            from native_services.play_session_store import PlaySessionStore
            play_session_store = PlaySessionStore(play_session_path)
        self._playtime_store = play_session_store
        self._playtime_verifier = playtime_verifier
        self._playtime_sessions = {}
        self._playtime_terminal = set()
        self._playtime_sequence = 0
        self._playtime_run = uuid.uuid4().hex
        self._playtime_error = ''
        self._playtime_status = 'ready' if play_session_store is not None else 'disabled'
        self._tracking_sessions = {}
        self._owner_status_files: dict[str, str] = {}
        self._session_info = {}  # session id -> what was launched (title, canonical game id) for activeGame
        self._stop_fn = stop_fn
        self._controller_profile_sessions = {}
        self._controller_profile_running = set()
        self._tracking_error = ''
        self._tracking_timer = QTimer(self)
        self._tracking_timer.setInterval(1000)
        self._tracking_timer.timeout.connect(self.pollGameState)

        # Initialize launch profile store
        if launch_profile_root is not None and LaunchProfileStore is not None:
            self._profile_store = LaunchProfileStore(launch_profile_root)
            self._profile_store.seed_reviewed_recipes()
        else:
            self._profile_store = None

        # Initialize ROM inventory store
        if rom_config_dir is not None and RomInventoryStore is not None:
            self._rom_store = RomInventoryStore(rom_config_dir)
        else:
            self._rom_store = None

        self._target_id: str = ''
        self._platform_or_provider: str = ''
        self._target_type: str = 'native_game'  # 'native_game', 'rom', 'custom'
        self._target_path: Optional[str] = None
        self._title: str = ''
        self._installation_details: dict[str, Any] = {}

        self._active_profile: Optional[LaunchProfile] = None
        self._alternative_profiles: list[LaunchProfile] = []
        self._active_profile_id: str = ''

        self._launch_status: str = 'idle'  # 'idle', 'handed_off', 'failed'
        self._last_pid: int = 0
        self._last_error: str = ''
        self._can_fix: bool = False
        self._fix_suggestion: str = ''

        # ROM Inventory state
        self._rom_records: list[RomRecord] = []
        # Load only our bounded prior local scan, without walking HDDs on startup.
        if rom_config_dir is not None:
            cache = Path(rom_config_dir) / 'local-rom-cache.json'
            try:
                if cache.is_file() and not cache.is_symlink() and cache.stat().st_size <= 4 * 1024 * 1024:
                    data = json.loads(cache.read_bytes())
                    if data.get('kind') == 'local-rom-cache-v1' and isinstance(data.get('records'), list) and len(data['records']) <= 20000:
                        self._rom_records = [RomRecord(**row) for row in data['records']]
            except (OSError, ValueError, TypeError, KeyError):
                self._rom_records = []

        self._selected_rom_id: str = ''
        self._rom_filter_text: str = ''
        self._rom_filter_platform: str = ''

    # Properties for Launch View bindings

    @Property(str, notify=changed)
    def selectedTargetId(self) -> str:
        return self._target_id

    @Property(str, notify=changed)
    def selectedVariantKey(self) -> str:
        return getattr(self, '_variant_key', '')

    @Property(str, notify=changed)
    def selectedTitle(self) -> str:
        return self._title

    @Property(str, notify=changed)
    def launchStatus(self) -> str:
        return self._launch_status

    def _tracking_key(self):
        return (self._target_type, self._platform_or_provider, self._target_id, self._target_path)

    @Property(str, notify=changed)
    def gameState(self) -> str:
        if self._tracking_error:return 'unknown'
        sid = self._tracking_sessions.get(self._tracking_key())
        session = self._tracker.get_session(sid) if self._tracker is not None and sid else None
        owner_stage = self._owner_stage(sid) if sid else None
        if owner_stage in ('starting', 'preparing', 'game-starting', 'attention'):
            return 'launching'
        if owner_stage == 'done':
            return 'closed'
        if owner_stage == 'failed':
            return 'failed'
        return session.state if session is not None else ('unknown' if self._launch_status == 'handed_off' else 'idle')

    def _owner_stage(self, session_id: str | None) -> str | None:
        path = self._owner_status_files.get(session_id or '')
        if not path:
            return None
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding='utf-8')[:4096])
        except (OSError, ValueError):
            return None
        stage = data.get('stage') if isinstance(data, dict) else None
        return stage if isinstance(stage, str) else None

    @Property(str, notify=changed)
    def playLabel(self) -> str:
        return {'running': 'Playing', 'launching': 'Starting…', 'closed': 'Play',
                'failed': 'Retry launch'}.get(self.gameState, 'Play')

    @Property(str, notify=changed)
    def launchDetail(self) -> str:
        """What a launcher wrapper (e.g. tools/mo2_launch.py) reports about the session: its status file is named
        in the active profile's extra_metadata['status_file']. Empty when there is none or nothing is running."""
        if self.gameState not in ('launching', 'running'):
            return ''
        meta = (self._active_profile.extra_metadata or {}) if self._active_profile else {}
        sid = self._tracking_sessions.get(self._tracking_key())
        path = self._active_status_file or self._owner_status_files.get(sid or '') or (meta.get('status_file') if isinstance(meta, dict) else None)
        if not isinstance(path, str) or not path:
            return ''
        try:
            from pathlib import Path as _Path
            data = json.loads(_Path(path).expanduser().read_text(encoding='utf-8')[:4096])
        except (OSError, ValueError):
            return ''
        text = data.get('text') if isinstance(data, dict) else None
        return _safe_text(text)[:120] if isinstance(text, str) else ''

    def _selected_playtime_identity(self):
        from native_services.play_session_store import GameIdentity
        if not self._target_id or not self._platform_or_provider:
            return None
        provider = ('rom:' if self._target_type == 'rom' else '') + self._platform_or_provider
        product = str(self._installation_details.get('product_id') or self._target_id)
        canonical = self._installation_details.get('canonical_game_id')
        # Provider/product identity is a stable fallback; never use a title or path.
        canonical = str(canonical) if canonical else json.dumps([provider, product], separators=(',', ':'))
        return GameIdentity(canonical, provider, product)

    @Property('QVariantMap', notify=playtimeChanged)
    def selectedPlaytimeTotals(self):
        identity = self._selected_playtime_identity()
        if self._playtime_store is None or identity is None:
            return {}
        try:
            return self._playtime_store.totals(identity.canonical_game_id)
        except Exception:
            return {}

    @Property(str, notify=playtimeChanged)
    def playtimeStatus(self):
        return self._playtime_status

    @Property(str, notify=playtimeChanged)
    def playtimeError(self):
        return self._playtime_error

    def _persist_playtime(self, session, *, start=False, uncertain=False):
        sid = session.session_id
        if self._playtime_store is None or sid not in self._playtime_sessions or sid in self._playtime_terminal:
            return
        identity, direct_game = self._playtime_sessions[sid]
        try:
            snapshot = copy.deepcopy(session)
            if uncertain:
                snapshot.state = 'unknown'
            verified = False
            if snapshot.state == 'running' and not uncertain:
                # The current tracker hides permission-denied retention. Never infer
                # verification from its state alone; an explicit verifier is required.
                verified = bool(self._playtime_verifier and self._playtime_verifier(snapshot))
                self._playtime_status = 'tracking' if verified else 'blocked'
                self._playtime_error = '' if verified else 'Tracker process confidence unavailable; no playtime credited'
            elif uncertain or snapshot.state == 'unknown':
                self._playtime_status = 'blocked'
                self._playtime_error = 'Application state could not be verified; no playtime credited'
            else:
                self._playtime_status = 'ready'
                self._playtime_error = ''
            self._playtime_sequence += 1
            event_id = f'{self._playtime_run}:{self._playtime_sequence}'
            operation = self._playtime_store.start if start else self._playtime_store.poll
            if snapshot.state in ('closed', 'failed'):
                operation = self._playtime_store.stop
            operation(snapshot, identity, sample_id=event_id,
                      monotonic_at=self._playtime_store.monotonic(),
                      observed_at=self._playtime_store.wall_clock(),
                      verified=verified, launcher_is_game=direct_game)
            if snapshot.state in ('closed', 'failed'):
                self._playtime_terminal.add(sid)
        except Exception:
            self._playtime_status = 'error'
            self._playtime_error = 'Playtime observation could not be persisted'
        self.playtimeChanged.emit()

    @Slot()
    def closePlaytimeStore(self):
        """Shutdown hook; caller-owned injected stores remain open. No fake exit."""
        self._tracking_timer.stop()
        if self._owns_playtime_store and self._playtime_store is not None:
            self._playtime_store.close()
            self._playtime_store = None
            self._playtime_status = 'disabled'
            self.playtimeChanged.emit()

    @Slot()
    def pollGameState(self):
        if self._tracker is None:return
        active = False
        had_error = False
        for sid in dict.fromkeys([*self._tracking_sessions.values(), *self._controller_profile_sessions]):
            try:
                session = self._tracker.poll_session(sid)
                owner_stage = self._owner_stage(sid)
                owner_active = owner_stage in ('starting', 'preparing', 'game-starting', 'attention', 'playing', 'closing')
                active = active or owner_active or (session.state in ('launching', 'running', 'unknown') and owner_stage not in ('done', 'failed'))
                self._persist_playtime(session)
                game_id = self._controller_profile_sessions.get(sid)
                if game_id:
                    if session.state == 'running': self._controller_profile_running.add(sid)
                    self.controllerSessionObserved.emit(sid, game_id, session.state)
                    if session.state == 'failed' and sid in self._controller_profile_running: active = True
                    if session.state == 'closed' or (session.state == 'failed' and sid not in self._controller_profile_running):
                        self._controller_profile_sessions.pop(sid, None)
                        self._controller_profile_running.discard(sid)
            except Exception:
                had_error = True
                game_id = self._controller_profile_sessions.get(sid)
                if game_id: self.controllerSessionObserved.emit(sid, game_id, 'unknown')
                # Break the ledger's continuity at the failed observation, rather
                # than silently bridging it on the next successful timer tick.
                previous = self._tracker.get_session(sid)
                if previous is not None:
                    self._persist_playtime(previous, uncertain=True)
                active = True
        self._tracking_error = 'Application state could not be verified' if had_error else ''
        if self._placer is not None and self._placer.pending:
            def game_pids(sid):
                session = self._tracker.get_session(sid)
                if session is None or session.state in ('closed', 'failed'):
                    return None
                return list(session.tracked_processes)
            try:
                self._placer.poll(game_pids, time.monotonic())
            except Exception:
                pass
            active = active or self._placer.pending
        if not active:self._tracking_timer.stop()
        self.changed.emit()

    # ---- Game monitor (Settings > Games and the per-game choice next to Play) ----

    def _monitor_game_id(self) -> str:
        return str(self._installation_details.get('canonical_game_id') or self._variant_key or self._target_id or '')

    @Property('QVariantList', notify=monitorsChanged)
    def monitorChoices(self) -> list:
        """Connected monitors as {label, description}, for the pickers."""
        if game_monitor is None:
            return []
        return [{'label': game_monitor.label(m), 'description': m['description']} for m in self._monitor_cache]

    @Property(str, notify=monitorsChanged)
    def defaultMonitor(self) -> str:
        return self._monitor_settings.default if self._monitor_settings is not None else ''

    @Property(str, notify=monitorsChanged)
    def defaultMonitorLabel(self) -> str:
        return self._monitor_label(self.defaultMonitor) if self.defaultMonitor else 'Wherever the game opens'

    @Property(int, notify=monitorsChanged)
    def monitorRevision(self) -> int:
        """Bumps whenever monitor settings change, so QML bindings on gameMonitorFor() re-evaluate."""
        return self._monitor_revision

    @Slot(str, result=str)
    def gameMonitorFor(self, game_id: str) -> str:
        """A game's choice: '' = default, 'any' = wherever it opens, else a monitor description."""
        return self._monitor_settings.override(game_id) if self._monitor_settings is not None and game_id else ''

    def _monitor_label(self, description: str) -> str:
        for m in self._monitor_cache:
            if m['description'] == description:
                return game_monitor.label(m)
        return f'{description} (not connected)' if description else ''

    @Slot()
    def refreshMonitors(self) -> None:
        try:
            self._monitor_cache = self._monitor_backend.monitors() if self._monitor_backend is not None else []
        except Exception:
            self._monitor_cache = []
        self.monitorsChanged.emit()

    @Slot(str)
    def setDefaultMonitor(self, description: str) -> None:
        if self._monitor_settings is not None:
            self._monitor_settings.set_default(description)
            self._monitor_revision += 1
            self.monitorsChanged.emit()

    @Slot(str, str)
    def setGameMonitorFor(self, game_id: str, choice: str) -> None:
        if self._monitor_settings is not None and game_id:
            self._monitor_settings.set_override(game_id, choice)
            self._monitor_revision += 1
            self.monitorsChanged.emit()

    def _remember_session(self, session_id: str) -> None:
        identity = self._selected_playtime_identity()
        self._session_info[session_id] = {
            'title': self._title,
            'gameId': str(self._installation_details.get('canonical_game_id') or (identity.canonical_game_id if identity else '')),
            'targetId': self._target_id,
            'provider': self._platform_or_provider,
        }

    def _active_session(self):
        """The newest tracked session that is still starting or running, whichever game is selected."""
        if self._tracker is None:
            return None
        best = None
        for sid in self._tracking_sessions.values():
            session = self._tracker.get_session(sid)
            owner_stage = self._owner_stage(sid)
            owner_active = owner_stage in ('starting', 'preparing', 'game-starting', 'attention', 'playing', 'closing')
            if session is not None and (session.state in ('launching', 'running') or owner_active):
                if owner_stage in ('done', 'failed'):
                    continue
                if best is None or session.launch_time > best.launch_time:
                    best = session
        return best

    @Property('QVariantMap', notify=changed)
    def activeGame(self) -> dict[str, Any]:
        """The game Waystone launched that is running now (empty when none), independent of the selected target."""
        session = self._active_session()
        if session is None:
            return {}
        info = self._session_info.get(session.session_id, {})
        stage = self._owner_stage(session.session_id)
        effective_state = {'starting': 'launching', 'preparing': 'launching', 'game-starting': 'launching', 'attention': 'launching',
                           'done': 'closed', 'failed': 'failed'}.get(stage, session.state)
        return {'sessionId': session.session_id, 'state': effective_state,
                'title': info.get('title') or session.target_id, 'gameId': info.get('gameId', ''),
                'targetId': session.target_id, 'provider': session.provider_or_platform,
                'launchTime': float(session.launch_time or 0), 'runningSince': float(session.running_since or 0)}

    @Slot(result=bool)
    def stopActiveGame(self) -> bool:
        """Ask the running game to close. Only processes of that tracked session are signalled: its own
        waystone-game-*.scope when it has one (the launcher and everything it started), otherwise the launcher's
        own process group and the tracked game processes (start times checked, so a reused PID is never hit).
        The session closes when the tracker sees the processes gone; nothing is marked closed here."""
        session = self._active_session()
        if session is None:
            return False
        pids = [p for p in [session.launcher_pid, *session.tracked_processes.keys()] if p]
        idents = dict(session.tracked_processes)
        if self._stop_fn is not None:
            return bool(self._stop_fn(session.session_id, pids))
        return _stop_session_processes(session.launcher_pid, pids, idents)

    @Property(int, notify=changed)
    def lastPid(self) -> int:
        return self._last_pid

    @Property(str, notify=changed)
    def lastError(self) -> str:
        return self._last_error

    @Property(bool, notify=changed)
    def canFix(self) -> bool:
        return self._can_fix

    @Property(str, notify=changed)
    def fixSuggestion(self) -> str:
        return self._fix_suggestion

    @Property('QVariantList', notify=profilesChanged)
    def availableProfiles(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._alternative_profiles]

    @Property('QVariantMap', notify=profilesChanged)
    def activeProfile(self) -> dict[str, Any]:
        if self._active_profile:
            return self._active_profile.to_dict()
        return {}

    @Property(int, notify=profilesChanged)
    def profileCount(self) -> int:
        return len(self._alternative_profiles)

    # Properties for ROM Inventory

    @Property('QVariantList', notify=romsChanged)
    def romRecords(self) -> list[dict[str, Any]]:
        results = []
        for r in self._rom_records[:MAX_DISPLAY_RECORDS]:
            results.append(r.to_dict())
        return results

    @Property(int, notify=romsChanged)
    def romCount(self) -> int:
        return len(self._rom_records)

    @Property('QStringList', notify=romsChanged)
    def romRoots(self) -> list[str]:
        if self._rom_store:
            return list(self._rom_store.roots)
        return []

    @Slot(str, str, result=bool)
    def configureEpicDirectLaunch(self, engine_path: str, config_root: str) -> bool:
        if not isinstance(engine_path, str) or not isinstance(config_root, str):
            return False
        if not Path(engine_path).is_absolute() or not Path(config_root).is_absolute():
            return False
        self._epic_engine_path = engine_path
        self._epic_config_root = config_root
        self.changed.emit()
        return self.epicDirectLaunchAvailable

    @Property(bool, notify=changed)
    def epicDirectLaunchAvailable(self) -> bool:
        return bool(self._epic_engine_path and self._epic_config_root and self._profile_store)

    @Property(bool, notify=changed)
    def isEpicDirectTarget(self) -> bool:
        """Epic games installed in Waystone's own Legendary launch through it. Games another launcher installed
        (Heroic's Legendary) launch through that launcher, which Waystone keeps hidden and tracks (W13)."""
        return (self._target_type == "native_game" and self._platform_or_provider in {"heroic-epic", "epic"}
                and self._epic_installed_here(self._target_id))

    def _epic_installed_here(self, app_name: str) -> bool:
        if not self._epic_config_root or not app_name:
            return False
        path = Path(self._epic_config_root) / "installed.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        cached = getattr(self, "_epic_installed_cache", None)
        if cached is None or cached[0] != mtime:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                names = frozenset(data) if isinstance(data, dict) else frozenset()
            except (OSError, ValueError):
                names = frozenset()
            cached = (mtime, names)
            self._epic_installed_cache = cached
        return app_name in cached[1]

    @Property(str, notify=profilesChanged)
    def epicRuntimeStatus(self) -> str:
        if self._platform_or_provider not in {"heroic-epic", "epic"}:
            return ""
        if self._epic_runtime_error:
            return self._epic_runtime_error
        runtime = (self._active_profile.extra_metadata or {}).get("waystone_epic_runtime") if self._active_profile else None
        if not isinstance(runtime, dict):
            return "Choose a launch runtime in Waystone before playing."
        if runtime.get("kind") == "native":
            return "Native runtime selected; the local manifest must identify this as a Linux title."
        if runtime.get("kind") == "wine":
            return "Waystone Wine runtime selected for this game."
        return "Choose a launch runtime in Waystone before playing."

    @Slot(str, str, str, result=bool)
    def configureEpicRuntime(self, kind: str, wine_binary: str = "", wine_prefix: str = "") -> bool:
        """Save an explicit per-game Legendary runtime in Waystone's launch-profile store."""
        if (not self.epicDirectLaunchAvailable or self._target_type != "native_game"
                or self._platform_or_provider not in {"heroic-epic", "epic"}
                or not self._target_id):
            return False
        self._epic_runtime_error = ""
        try:
            from native_services.epic_direct_launch import RuntimeSelection
            selection = RuntimeSelection(kind=kind,
                wine_binary=wine_binary or None, wine_prefix=wine_prefix or None)
            if kind == "native" and (wine_binary or wine_prefix):
                raise ValueError("Native runtime cannot include Wine paths.")
            if kind == "wine":
                wine_path = Path(selection.wine_binary or "")
                prefix_path = Path(selection.wine_prefix or "")
                wine_stat = wine_path.lstat()
                prefix_stat = prefix_path.lstat()
                if (not stat.S_ISREG(wine_stat.st_mode) or stat.S_ISLNK(wine_stat.st_mode)
                        or not os.access(wine_path, os.X_OK)):
                    raise ValueError("Select an existing executable Wine binary.")
                if (not stat.S_ISDIR(prefix_stat.st_mode) or stat.S_ISLNK(prefix_stat.st_mode)
                        or not os.access(prefix_path, os.X_OK | os.R_OK)):
                    raise ValueError("Select an accessible existing Wine prefix directory.")
        except OSError:
            self._epic_runtime_error = "Wine binary or prefix does not exist or cannot be accessed."
            self.changed.emit()
            self.profilesChanged.emit()
            return False
        except (TypeError, ValueError) as exc:
            self._epic_runtime_error = str(exc)
            self.changed.emit()
            self.profilesChanged.emit()
            return False
        import hashlib
        token = hashlib.sha256(self._target_id.encode("utf-8")).hexdigest()[:20]
        profile_id = "waystone-epic-" + token
        profile = LaunchProfile(
            profile_id=profile_id,
            display_name="Waystone Epic direct launch",
            target_type="native_game",
            platform_or_provider=self._platform_or_provider,
            executable=self._epic_engine_path,
            argv_template=("launch", "{game_id}"),
            env={"LEGENDARY_CONFIG_PATH": self._epic_config_root},
            is_default=False, is_app_owned=True,
            supported_platforms=(self._platform_or_provider,),
            extra_metadata={"waystone_epic_runtime": {
                "kind": selection.kind, "wine_binary": selection.wine_binary,
                "wine_prefix": selection.wine_prefix}},
        )
        try:
            existing = self._profile_store.get_profile(profile_id)
            if existing:
                self._profile_store.update_profile(profile)
            else:
                self._profile_store.create_profile(profile)
            self._profile_store.set_game_override(self._target_id, profile_id)
            self._resolve_profiles_for_target()
            self.changed.emit()
            self.profilesChanged.emit()
            return True
        except Exception:
            self._epic_runtime_error = "Waystone could not save the Epic runtime profile."
            self.changed.emit()
            self.profilesChanged.emit()
            return False

    # Slots for Target / Selection

    @Slot(str, 'QVariantMap')
    def setTarget(self, target_id: str, details: dict[str, Any]) -> None:
        """Select a target (native game or ROM) and resolve its launch profiles."""
        # Any selection change invalidates a pending async controller continuation.
        self._launch_selection_revision += 1
        self._pending_launch = None
        self._installation_details = dict(details) if isinstance(details, dict) else {}
        self._epic_runtime_error = ''
        self._title = _safe_text(self._installation_details.get('title', ''))

        # Exact un-truncated keys
        product_id = self._installation_details.get('product_id')
        self._variant_key = str(self._installation_details.get('variant_key') or target_id)
        # Use exact product_id as the launch target_id when available; retain variant_key
        self._target_id = str(product_id) if product_id else str(target_id)

        # Determine target type and platform/provider
        provider = self._installation_details.get('provider', '')
        platform_name = self._installation_details.get('platform', '')
        rom_path = self._installation_details.get('rom_path') or self._installation_details.get('file_path')

        if rom_path:
            self._target_type = 'rom'
            self._platform_or_provider = _safe_text(platform_name).lower()
            self._target_path = str(rom_path)
        else:
            self._target_type = 'native_game'
            self._platform_or_provider = _safe_text(provider).lower()
            self._target_path = None

        self._launch_status = 'idle'
        self._active_status_file = None
        self._last_pid = 0
        self._last_error = ''
        self._can_fix = False
        self._fix_suggestion = ''

        self._resolve_profiles_for_target()
        self._adopt_running_launcher()
        self.playtimeChanged.emit()
        self.changed.emit()
        self.profilesChanged.emit()
        self.monitorsChanged.emit()

    def _adopt_running_launcher(self) -> None:
        """Pick up a session that a launcher wrapper is still running (e.g. after Waystone restarted): its status
        file names the launcher's PID; if that process is alive, track it like a fresh launch."""
        if self._tracker is None or self._platform_or_provider != 'local' or not self._active_profile:
            return
        if self._tracking_sessions.get(self._tracking_key()):
            return
        meta = self._active_profile.extra_metadata or {}
        path = meta.get('status_file') if isinstance(meta, dict) else None
        if not isinstance(path, str) or not path:
            return
        try:
            from pathlib import Path as _Path
            data = json.loads(_Path(path).expanduser().read_text(encoding='utf-8')[:4096])
            pid = int(data.get('launcher_pid') or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            return
        if pid <= 0 or data.get('stage') in ('done', 'failed') or not os.path.exists(f'/proc/{pid}'):
            return
        try:
            session = self._tracker.start_tracking(self._target_id, self._platform_or_provider,
                                                   target_type=self._target_type, launcher_pid=pid,
                                                   prelaunch_pids=self._tracker.capture_prelaunch_pids())
        except Exception:
            return
        self._tracking_sessions[self._tracking_key()] = session.session_id
        self._remember_session(session.session_id)
        self._launch_status = 'handed_off'
        self._last_pid = pid
        if not self._tracking_timer.isActive():
            self._tracking_timer.start()

    @Slot(str, 'QVariantMap')
    def setInstallation(self, installation_id: str, details: dict[str, Any]) -> None:
        """Compatibility slot matching prior signature."""
        self.setTarget(installation_id, details)

    @Slot(str)
    def selectProfile(self, profile_id: str) -> bool:
        """Switch active profile among available alternatives and record association."""
        clean_id = _safe_text(profile_id)
        self._launch_selection_revision += 1
        self._pending_launch = None
        for p in self._alternative_profiles:
            if p.profile_id == clean_id:
                self._active_profile = p
                self._active_profile_id = clean_id
                if self._profile_store and self._target_id:
                    self._profile_store.set_game_override(self._target_id, clean_id)
                self._check_active_profile_health()
                self.changed.emit()
                self.profilesChanged.emit()
                return True
        return False

    def setPrelaunchGuard(self, guard):
        """Inject async guard(canonical_id, completion); no controller work by default.

        Completion is correlated to an immutable selection revision. A late result
        never launches a newly selected game; validation reruns after admission.
        """
        self._prelaunch_guard = guard
        # A guard may also take the game's title (the virtual pad uses it to give anti-cheat games the real pad).
        self._guard_takes_title = False
        if guard is not None:
            import inspect
            try:
                params = [p for p in inspect.signature(guard).parameters.values()
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                self._guard_takes_title = len(params) >= 3
            except (TypeError, ValueError):
                self._guard_takes_title = False

    def setLaunchEnvironmentProvider(self, provider):
        """Inject a per-launch environment source; never changes the parent environment."""
        self._launch_environment_provider = provider

    @staticmethod
    def _merge_controller_launch_environment(intent, additions):
        if not isinstance(additions, dict):
            return intent
        allowed = ('SDL_GAMECONTROLLER_IGNORE_DEVICES', 'SDL_JOYSTICK_BLACKLIST_DEVICES', 'PROTON_DISABLE_HIDRAW')
        env = dict(intent.env)
        for name in allowed:
            value = additions.get(name)
            if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(char) < 32 for char in value):
                continue
            current = env.get(name, '')
            if name == 'PROTON_DISABLE_HIDRAW' and current.strip().lower() in ('1', 'true', 'yes'):
                continue  # preserve an explicit broader user setting
            if current.strip().startswith('@'):
                continue  # SDL's @file syntax cannot be safely concatenated
            items = []
            for entry in (current + ',' + value).split(','):
                entry = entry.strip()
                if entry and entry not in items:
                    items.append(entry)
            if items:
                env[name] = ','.join(items)
        return replace(intent, env=env)

    def _prepare_virtual_pad(self):
        identity = self._selected_playtime_identity()
        if identity is None:
            return self._set_error('Canonical game identity is unavailable for virtual pad preparation')
        token = object()
        revision = self._launch_selection_revision
        self._pending_launch = token
        self._launch_status = 'preparing_controller'
        self.changed.emit()

        def completed(success, message=''):
            if self._pending_launch is not token or revision != self._launch_selection_revision:
                return
            self._pending_launch = None
            if success is not True:
                self._set_error(message or 'Virtual pad could not be prepared. Refresh Controllers and retry Play.')
                return
            self._continuing_guarded_launch = True
            try:
                self.play()
            finally:
                self._continuing_guarded_launch = False

        try:
            accepted = (self._prelaunch_guard(identity.canonical_game_id, completed, self._title)
                        if self._guard_takes_title else self._prelaunch_guard(identity.canonical_game_id, completed))
        except Exception:
            completed(False, 'Virtual pad preparation failed safely. No game was launched.')
            return {'status': 'failed', 'error': self._last_error}
        if accepted is not True and self._pending_launch is token:
            completed(False, 'Virtual pad preparation is busy or unavailable. Retry Play after it settles.')
        return {'status': 'preparing_controller', 'target_id': self._target_id}

    # Slot for Explicit Play Execution

    @Slot(result='QVariantMap')
    def play(self) -> dict[str, Any]:
        """Explicit user Play action.
        
        Validates intent using LaunchProfileStore, then calls launch() with injected or default spawn.
        Never executes during queries or tests.
        """
        if self._pending_launch is not None:
            return {'status': 'preparing_controller', 'target_id': self._target_id, 'error': 'Virtual pad preparation is already pending'}

        if not self._target_id:
            return self._set_error('No game or ROM selected')

        if not self._profile_store:
            return self._set_error('Launch profile store is not configured')

        if self.gameState in ('launching', 'running'):
            return {'status': 'already_running', 'target_id': self._target_id, 'error': 'This game is already starting or running'}

        # Epic is launched by Waystone's managed Legendary engine when configured;
        # never silently fall back to the Heroic URI/profile.
        direct_epic = bool(self.epicDirectLaunchAvailable and self.isEpicDirectTarget)
        epic_plan = None
        if direct_epic:
            runtime_data = (self._active_profile.extra_metadata or {}).get("waystone_epic_runtime") if self._active_profile else None
            if not isinstance(runtime_data, dict):
                return self._set_error("Choose a Waystone Epic launch runtime before playing; Heroic will not be opened.")
            try:
                from native_services.epic_direct_launch import RuntimeSelection, plan_epic_direct_launch
                selection = RuntimeSelection(
                    kind=runtime_data.get("kind"),
                    wine_binary=runtime_data.get("wine_binary"),
                    wine_prefix=runtime_data.get("wine_prefix"),
                )
                epic_plan = plan_epic_direct_launch(
                    engine_path=self._epic_engine_path, config_root=self._epic_config_root,
                    app_name=self._target_id, runtime=selection)
                intent = epic_plan.launch_intent
            except (TypeError, ValueError, OSError) as exc:
                return self._set_error(f"Waystone direct Epic launch is not ready: {exc}")
        else:
            intent = self._profile_store.build_launch_intent(
                target_id=self._target_id,
                platform_or_provider=self._platform_or_provider,
                target_path=self._target_path,
                target_type=self._target_type,
                profile_id=self._active_profile_id or None,
            )

        if not intent.verified_contract:
            self._can_fix = True
            self._fix_suggestion = intent.error or 'Invalid launch intent configuration'
            return self._set_error(intent.error or 'Unverified launch contract')

        if self._prelaunch_guard is not None and not self._continuing_guarded_launch:
            return self._prepare_virtual_pad()

        # The guard's successful readback is fresh at this point. Attach only
        # validated engine-owned physical VID/PID filters to this game process;
        # never mutate os.environ or affect independently launched apps.
        if self._launch_environment_provider is not None and self._continuing_guarded_launch:
            additions, reason = {}, ''
            try:
                additions = self._launch_environment_provider()
                reason = str(getattr(getattr(self._launch_environment_provider, '__self__', None),
                                     'lastFilterReason', '') or '')
                intent = self._merge_controller_launch_environment(intent, additions)
            except Exception as exc:
                reason = f'filter failed: {exc}'  # Output filters are optional when the engine has no owned-device evidence.
            # One line per launch in the service log: which controller the game is told to ignore, and why.
            _LOG.warning('Controller filter for %s: %s (%s)', self._target_id,
                         {k: v for k, v in (additions or {}).items()} or 'none', reason or 'no reason given')

        owner_wrapped = False
        details = self._installation_details
        game_folder = _safe_text(details.get('install_path') or details.get('game_folder') or details.get('path') or '') or None
        game_executable = _safe_text(details.get('game_executable') or details.get('process_name') or '') or None
        if (self._launcher_owner_enabled and build_owned_launch_intent is not None and not game_folder
                and str(self._platform_or_provider).startswith('heroic')):
            # The Library Play path passes only ids: find the folder in Heroic's own install lists.
            try:
                from native_services.launcher_owner import heroic_install_info
                info = heroic_install_info(self._target_id)
                game_folder = info.get('install_path') or None
                game_executable = game_executable or info.get('executable') or None
            except Exception:
                pass
        if self._launcher_owner_enabled and build_owned_launch_intent is not None:
            status_root = self._launcher_status_root
            _prune_owner_status_files(status_root)
            safe_id = re.sub(r'[^A-Za-z0-9_.-]+', '_', self._target_id)[:72] or 'game'
            status_path = status_root / f'{self._platform_or_provider}-{safe_id}-{uuid.uuid4().hex[:12]}.json'
            wrapped = build_owned_launch_intent(
                intent, provider=self._platform_or_provider, game_id=self._target_id,
                status_file=status_path, game_executable=game_executable, game_folder=game_folder,
            )
            if wrapped is not None:
                intent = wrapped
                owner_wrapped = True
                self._active_status_file = str(status_path)

        # Execute handoff
        # 'local' entries run through a launcher wrapper that lives exactly as long as the game (e.g. mo2_launch.py),
        # so the launched process itself is the session.
        trackable = self._tracker is not None and (self._target_type == 'rom' or self._platform_or_provider in ('steam', 'heroic-epic', 'heroic-sideload', 'epic', 'local'))
        prelaunch = self._tracker.capture_prelaunch_pids() if trackable else None
        target_monitor = None
        if self._placer is not None and self._monitor_settings is not None:
            try:
                target_monitor = self._placer.prepare(self._monitor_settings.effective(self._monitor_game_id()))
            except Exception:
                target_monitor = None
        result = launch(intent, spawn=self._spawn_fn)
        if result.status == 'handed_off':
            self._launch_status = 'handed_off'
            self._last_pid = result.pid or 0
            self._last_error = ''
            self._can_fix = False
            self._fix_suggestion = ''
            self._tracking_error = ''
            if trackable:
                try:
                    session = self._tracker.start_tracking(self._target_id, self._platform_or_provider,
                        target_type=self._target_type, launcher_pid=result.pid,
                        target_executable=(game_executable if owner_wrapped else (intent.executable if self._target_type == 'rom' else None)),
                        target_payload=self._target_path,
                        game_folder=game_folder if owner_wrapped else None,
                        launcher_pid_is_game=not owner_wrapped,
                        startup_timeout=86400 if owner_wrapped else None,
                        prelaunch_pids=prelaunch)
                    self._tracking_sessions[self._tracking_key()] = session.session_id
                    if self._placer is not None and target_monitor:
                        self._placer.track(session.session_id, target_monitor, time.monotonic())
                    if owner_wrapped and self._active_status_file:
                        self._owner_status_files[session.session_id] = self._active_status_file
                    self._remember_session(session.session_id)
                    profile_identity = self._selected_playtime_identity()
                    if profile_identity is not None:
                        self._controller_profile_sessions[session.session_id] = profile_identity.canonical_game_id
                        if session.state == 'running': self._controller_profile_running.add(session.session_id)
                        self.controllerSessionObserved.emit(session.session_id, profile_identity.canonical_game_id, session.state)
                    if self._playtime_store is not None:
                        identity = self._selected_playtime_identity()
                        if identity is not None:
                            direct_game = (self._target_type == 'rom' and
                                self._installation_details.get('playtime_direct_game_process') is True)
                            self._playtime_sessions[session.session_id] = (identity, direct_game)
                            self._persist_playtime(session, start=True)
                    self._tracking_timer.start()
                except Exception:
                    self._tracking_error = 'Game launch was sent; application state could not be verified'
                    identity = self._selected_playtime_identity()
                    if identity is not None:
                        self.controllerSessionObserved.emit('unverified-' + uuid.uuid4().hex, identity.canonical_game_id, 'unknown')
            elif self._prelaunch_guard is not None:
                identity = self._selected_playtime_identity()
                if identity is not None:
                    self.controllerSessionObserved.emit('untracked-' + uuid.uuid4().hex, identity.canonical_game_id, 'unknown')
            self.changed.emit()
            self.launched.emit(self._target_id, self._last_pid)
            return result.to_dict()
        else:
            self._can_fix = True
            self._fix_suggestion = result.error or 'Launch handoff failed'
            return self._set_error(result.error or 'Launch failed')

    # Slot for Profile Editing

    @Slot('QVariantMap', result=bool)
    def updateProfile(self, profile_data: dict[str, Any]) -> bool:
        """Edit or create an app-owned launch profile."""
        if not self._profile_store or not isinstance(profile_data, dict):
            return False

        profile_id = _safe_text(profile_data.get('profile_id', ''))
        if not profile_id:
            profile_id = f'custom-{uuid.uuid4().hex[:8]}'

        display_name = _safe_text(profile_data.get('display_name', profile_data.get('name', 'Custom Profile')))
        target_type = _safe_text(profile_data.get('target_type', self._target_type or 'custom'))
        platform_or_provider = _safe_text(profile_data.get('platform_or_provider', self._platform_or_provider or 'custom')).lower()
        executable = _safe_text(profile_data.get('executable', ''))
        raw_args = profile_data.get('argv_template') or profile_data.get('args', [])
        argv_template = tuple(_sanitize_args(raw_args))
        cwd = _safe_text(profile_data.get('cwd', '')) or None
        
        env_raw = profile_data.get('env', {})
        env = {}
        if isinstance(env_raw, dict):
            for k, v in env_raw.items():
                if isinstance(k, str) and isinstance(v, str):
                    env[k] = v

        is_default = bool(profile_data.get('is_default', False))
        supported_platforms = tuple(str(p).lower() for p in profile_data.get('supported_platforms', (platform_or_provider,)))
        supported_extensions = tuple(str(e).lower() for e in profile_data.get('supported_extensions', ()))

        new_profile = LaunchProfile(
            profile_id=profile_id,
            display_name=display_name,
            target_type=target_type,
            platform_or_provider=platform_or_provider,
            executable=executable,
            argv_template=argv_template,
            cwd=cwd,
            env=env,
            target_host=None,
            is_default=is_default,
            is_app_owned=True,
            supported_platforms=supported_platforms,
            supported_extensions=supported_extensions,
        )

        existing = self._profile_store.get_profile(profile_id)
        if existing:
            self._profile_store.update_profile(new_profile)
        else:
            self._profile_store.create_profile(new_profile)

        # Set game association if target selected
        if self._target_id:
            self._profile_store.set_game_override(self._target_id, profile_id)

        self._resolve_profiles_for_target()
        self.changed.emit()
        self.profilesChanged.emit()
        return True

    @Slot(str, result=bool)
    def deleteProfile(self, profile_id: str) -> bool:
        if not self._profile_store:
            return False
        success = self._profile_store.delete_profile(profile_id)
        if success:
            self._resolve_profiles_for_target()
            self.changed.emit()
            self.profilesChanged.emit()
        return success

    @Slot(result='QVariantMap')
    def diagnoseInstallation(self) -> dict[str, Any]:
        """Diagnose configuration health of current target and active profile."""
        if not self._target_id:
            return {'healthy': False, 'reason': 'No installation selected'}

        if not self._active_profile:
            return {
                'healthy': False,
                'reason': 'No launch profile configured for this target',
                'can_fix': True,
                'suggestion': 'Create or select a launch profile',
            }

        prof = self._active_profile
        exe_path = Path(prof.executable)
        if not prof.executable or (not exe_path.is_file() and not shutil.which(prof.executable)):
            return {
                'healthy': False,
                'reason': f'Executable not found: {prof.executable}',
                'can_fix': True,
                'suggestion': f'Install or locate executable "{exe_path.name}"',
            }

        resolved_exe = str(exe_path.resolve()) if exe_path.is_file() else (shutil.which(prof.executable) or '')
        if not os.access(resolved_exe, os.X_OK):
            return {
                'healthy': False,
                'reason': f'Executable lacks execution permission: {resolved_exe}',
                'can_fix': True,
                'suggestion': f'Grant execution permission using chmod +x {resolved_exe}',
            }

        if prof.cwd and not Path(prof.cwd).is_dir():
            return {
                'healthy': False,
                'reason': f'Configured working directory does not exist: {prof.cwd}',
                'can_fix': True,
                'suggestion': 'Correct the working directory in profile settings',
            }

        if self._target_path and not Path(self._target_path).is_file():
            return {
                'healthy': False,
                'reason': f'ROM payload file is missing: {self._target_path}',
                'can_fix': True,
                'suggestion': 'Locate the missing ROM file or rescan ROM library',
            }

        return {
            'healthy': True,
            'reason': 'Ready to play',
            'can_fix': False,
            'suggestion': '',
            'executable': resolved_exe,
            'profile_id': prof.profile_id,
        }

    # ROM Inventory Slots

    @Slot(str, result=bool)
    def addRomRoot(self, root_path: str) -> bool:
        """Add an explicit user-selected ROM scan root."""
        clean_path = _safe_text(root_path)
        if not self._rom_store or not clean_path:
            return False
        p = Path(clean_path).resolve()
        if not p.is_dir():
            return False
        added = self._rom_store.add_root(p)
        if added:
            self.romsChanged.emit()
        return added

    @Slot(str, result=bool)
    def removeRomRoot(self, root_path: str) -> bool:
        clean_path = _safe_text(root_path)
        if not self._rom_store or not clean_path:
            return False
        removed = self._rom_store.remove_root(clean_path)
        if removed:
            self.romsChanged.emit()
        return removed

    @Property(bool, notify=romsChanged)
    def romScannerBusy(self) -> bool:
        return getattr(self, '_rom_busy', False)

    @Property(str, notify=romsChanged)
    def selectedRomId(self) -> str:
        return getattr(self, '_selected_rom_id', '')

    @Slot(result=int)
    def scanRoms(self) -> int:
        """Explicit on-demand scan of configured ROM roots. Never runs at startup."""
        if not self._rom_store:
            return 0
        self._rom_busy = True
        self.romsChanged.emit()
        try:
            self._rom_records = self._rom_store.scan_inventory()
        finally:
            self._rom_busy = False
            self.romsChanged.emit()
        return len(self._rom_records)

    @Slot(str)
    def selectRom(self, rom_id: str) -> None:
        """Select a ROM from scanned inventory as the current play target."""
        clean_id = _safe_text(rom_id)
        for r in self._rom_records:
            if r.rom_id == clean_id:
                self._selected_rom_id = clean_id
                details = {
                    'title': r.file_name,
                    'platform': r.platform,
                    'rom_path': r.file_path,
                    'rom_id': r.rom_id,
                    'extension': r.extension,
                }
                self.setTarget(r.rom_id, details)
                self.romsChanged.emit()
                return

    # Internal helpers

    def _resolve_profiles_for_target(self) -> None:
        if not self._profile_store:
            self._active_profile = None
            self._alternative_profiles = []
            self._active_profile_id = ''
            return

        default_prof, candidates = self._profile_store.resolve_profiles(
            target_id=self._target_id,
            platform_or_provider=self._platform_or_provider,
            target_type=self._target_type,
        )

        self._active_profile = default_prof
        self._alternative_profiles = candidates
        self._active_profile_id = default_prof.profile_id if default_prof else ''
        self._check_active_profile_health()

    def _check_active_profile_health(self) -> None:
        diag = self.diagnoseInstallation()
        self._can_fix = bool(diag.get('can_fix', False))
        self._fix_suggestion = str(diag.get('suggestion', ''))

    def _set_error(self, message: str) -> dict[str, Any]:
        self._launch_status = 'failed'
        self._last_error = _safe_text(message)
        self.changed.emit()
        self.launchFailed.emit(self._target_id, self._last_error)
        return {'status': 'failed', 'error': self._last_error}
