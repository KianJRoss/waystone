"""Bounded virtual-output bridge: async existing-engine commands, exact-ID intent.

No device/engine creation, controller grabs, game launches or socket I/O during
construction/selection. File edits preserve unknown profile document fields.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QObject, Property, QThread, QTimer, Signal, Slot
from native_services.unified_controller_control import VIRTUAL_PAD_TYPES, validate_output_state
import controller_profile_assignments as profile_model

_TYPES = tuple(zip(VIRTUAL_PAD_TYPES, ('Xbox 360', 'Xbox One', 'Xbox Series', 'DualShock 4', 'DualSense')))
_PROFILE_FIELD = 'virtual_gamepad_driver'        # per controller profile (Kian 2026-11-25: pad type lives in a profile)
# Passthrough (Kian 2026-11-25: "straight passthrough on competitive game and not even worry about the controller unless
# they want to"): the game gets the real controller; Waystone hides its own virtual pad from that game instead.
PASSTHROUGH = 'passthrough'
_PASSTHROUGH_LABEL = 'Off · real controller only'
# USB identities the engine gives its virtual pads (components/controller_studio/.../Output/*)
_VIRTUAL_IDS = {'xbox360': (0x045e, 0x028e), 'xboxone': (0x045e, 0x02ea), 'xboxseries': (0x045e, 0x0b12),
                'dualshock4': (0x054c, 0x05c4), 'dualsense': (0x054c, 0x0ce6)}


def _code(exc):
    value = getattr(exc, 'code', '')
    return value if isinstance(value, str) and re.fullmatch(r'ERR_[A-Z0-9_]{1,60}', value) else 'ERR_OUTPUT_UNAVAILABLE'


def _game_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 1024
            and not any(ord(c) < 32 for c in value))


class _OutputJob(QThread):
    def __init__(self, client, kind, desired, force, persist, observation, parent, launch_game_id=None):
        super().__init__(parent)
        self.client, self.kind, self.desired = client, kind, desired
        self.force, self.persist, self.observation = force, persist, observation
        self.launch_game_id = launch_game_id
        self.result = None

    def run(self):
        state, failure, uncertain, attempted = None, None, False, False
        try:
            if self.kind != 'refresh':
                ids, known = self.observation
                self.client.request('output_game_state', active_game_ids=ids, observation_known=known)
            if self.desired is not None:
                attempted = True
                self.client.request('output_set', type=self.desired, force=self.force,
                                    persist_default=self.persist)
            if self.kind == 'launch' and self.launch_game_id is not None:
                ids, known = self.observation
                if known:
                    ids = sorted(set(ids) | {self.launch_game_id})
                self.client.request('output_game_state', active_game_ids=ids, observation_known=known)
        except Exception as exc:
            failure = _code(exc)
            uncertain = attempted and getattr(exc, 'uncertain', True) is not False
        # Always read actual state, including after a rejected/ambiguous write.
        try:
            state = validate_output_state(self.client.request('output_get'))
            if self.desired is not None and failure is None:
                if state['current_type'] != self.desired or self.persist and state['default_type'] != self.desired:
                    failure, uncertain = 'ERR_OUTPUT_VERIFY_FAILED', True
        except Exception as exc:
            failure = failure or _code(exc)
            uncertain = uncertain or attempted
        self.result = (failure is None, uncertain, state, failure or '', attempted)


class VirtualPadView(QObject):
    changed = Signal()
    documentChanged = Signal()
    operationFinished = Signal(bool)

    def __init__(self, path=None, client=None, parent=None, anticheat=None):
        super().__init__(parent)
        self._anticheat = anticheat          # AntiCheatIndex; loaded on first use when None
        self._launch_mode = ''
        self._path = Path(path) if path is not None else None
        self._client, self._job = client, None
        self._state = None
        self._available = False
        self._doc = {}
        self._storage_error = ''
        self._status = 'Virtual pad has not been observed; engine availability is unverified.'
        self._error = ''
        self._uncertain = False
        self._closed = False
        self._game = ''
        self._sessions = {}  # sid -> (immutable canonical id, state, ever_running)
        self._terminal = set()
        self._restore_pending = False
        self._callback = None
        self._legacy_allowed = False
        self._launch_waiter = None
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._heartbeat)
        self._load()

    def _read(self):
        if self._path is None:
            return {}
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return {}
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('Profile document exceeds safe read limit.')
        doc = json.loads(raw)
        if not isinstance(doc, dict) or not isinstance(doc.get('profiles', {}), dict):
            raise ValueError('Invalid virtual pad assignment document.')
        return doc

    def _load(self):
        try:
            self._doc = self._read()
            self._storage_error = ''
            return True
        except (OSError, ValueError, TypeError):
            self._storage_error = 'Virtual pad settings could not be read safely. Nothing was changed.'
            return False

    @Property('QVariantList', constant=True)
    def choices(self):
        return [{'value': value, 'label': label} for value, label in _TYPES] + \
               [{'value': PASSTHROUGH, 'label': _PASSTHROUGH_LABEL}]

    @Property(str, notify=changed)
    def currentType(self):
        # Uncertainty means a requested write may not have been applied; it
        # does not invalidate a successfully validated output_get readback.
        # Keep the observed type visible while `uncertain` separately blocks
        # further writes/launches until explicit refresh.
        return (self._state or {}).get('current_type') or ''

    @Property(str, notify=changed)
    def defaultType(self):
        if self._state:
            return self._state['default_type']
        value = self._doc.get('virtual_gamepad_driver', 'xbox360')
        return value if value in VIRTUAL_PAD_TYPES else ''


    @Property(bool, notify=changed)
    def available(self): return self._available

    @Property(bool, notify=changed)
    def busy(self): return self._job is not None

    @Property(bool, notify=changed)
    def uncertain(self): return self._uncertain

    @Property(str, notify=changed)
    def status(self): return self._status

    @Property(str, notify=changed)
    def error(self): return self._error or self._storage_error

    @Slot(result='QVariantMap')
    def launchEnvironment(self):
        """Return per-game filters only after a fresh connected virtual-pad readback.

        The caller invokes this only after the correlated prelaunch guard
        succeeds, so these values are inherited by that game process alone.
        Why the result is what it is stays in `lastFilterReason` (logged by the launcher).
        """
        env, self._filter_reason = self._launch_filters()
        return env

    def _launch_filters(self):
        state = self._state
        if self._closed or self._uncertain or not self._available or not state:
            return {}, ('closed' if self._closed else 'outcome uncertain' if self._uncertain
                        else 'engine not available' if not self._available else 'no engine readback')
        if state.get('game_state') != 'active':
            return {}, f"engine game state is {state.get('game_state')!r}, not active"
        if self._launch_mode == PASSTHROUGH:
            # Real controller to the game; hide only the engine's virtual pad, and only if no real pad shares its id.
            ids = _VIRTUAL_IDS.get(state.get('current_type'))
            if ids is None:
                return {}, f"unknown virtual pad type {state.get('current_type')!r}"
            if ids in self._physical_ids():
                return {}, 'a real controller shares the virtual pad id'
            pair = f"0x{ids[0]:04x}/0x{ids[1]:04x}"
            return {'SDL_GAMECONTROLLER_IGNORE_DEVICES': pair, 'SDL_JOYSTICK_BLACKLIST_DEVICES': pair}, \
                'passthrough: virtual pad hidden'
        if state.get('current_type') not in VIRTUAL_PAD_TYPES:
            return {}, f"unknown virtual pad type {state.get('current_type')!r}"
        slots = state.get('slots', [])
        if not any(slot.get('connected') is True and slot.get('state') == 'connected' for slot in slots):
            return {}, f"no connected virtual pad slot: {slots!r}"
        pairs = sorted({f"0x{device['vendor_id']:04x}/0x{device['product_id']:04x}"
                        for device in state.get('owned_devices', [])
                        if device.get('connected') is True and device.get('virtual') is False
                        and device['vendor_id'] > 0 and device['product_id'] > 0})
        if not pairs:
            return {}, 'engine reports no real controller to hide'
        identifiers = ','.join(pairs)
        return {
            'SDL_GAMECONTROLLER_IGNORE_DEVICES': identifiers,
            # SDL's joystick-side counterpart is named BLACKLIST, not IGNORE.
            'SDL_JOYSTICK_BLACKLIST_DEVICES': identifiers,
            # This is only inherited by this one game process, never Waystone.
            'PROTON_DISABLE_HIDRAW': '1',
        }, 'virtual pad: real controller hidden'

    @Property(str, notify=changed)
    def lastFilterReason(self): return getattr(self, '_filter_reason', '')

    @Property(str, notify=changed)
    def selectedGameId(self): return self._game

    @Property(str, notify=changed)
    def resolvedType(self): return self.resolveType(self._game)

    @Slot(str)
    def selectGame(self, game_id):
        self._game = game_id
        self.changed.emit()

    @Slot()
    def reloadSettings(self):
        """Re-read profiles.json after a profile's pad type changed elsewhere (controller menu)."""
        self._load()
        self.changed.emit()

    @Slot(str, result=str)
    def profileForGame(self, game_id):
        """The controller profile a game uses: its own, else the general default, else the engine's active one."""
        name = profile_model.resolve(self._doc, game_id or None)
        if not name:
            active = self._doc.get('active_profile')
            name = active if isinstance(active, str) and active in (self._doc.get('profiles') or {}) else ''
        return name or ''

    @Slot(str, result=str)
    def profileType(self, profile_name):
        """A profile's own pad type, or '' when it follows the default."""
        body = (self._doc.get('profiles') or {}).get(profile_name)
        value = body.get(_PROFILE_FIELD) if isinstance(body, dict) else None
        return value if value in VIRTUAL_PAD_TYPES or value == PASSTHROUGH else ''

    def _index(self):
        if self._anticheat is None:
            try:
                from native_services.anticheat_games import load_index
                self._anticheat = load_index()
            except Exception:
                from native_services.anticheat_games import AntiCheatIndex
                self._anticheat = AntiCheatIndex()
        return self._anticheat

    @Slot(str, result=str)
    def anticheatFor(self, title):
        """Anti-cheat names for a game title ('' when none known)."""
        return ', '.join(self._index().anticheats(title)) if title else ''

    @Slot(str, str, result=str)
    def resolveTypeFor(self, game_id, title):
        """What a game gets: its controller profile's choice; else passthrough for anti-cheat games; else the
        default virtual pad type."""
        chosen = self.profileType(self.profileForGame(game_id))
        if chosen:
            return chosen
        if title and self._index().anticheats(title):
            return PASSTHROUGH
        return self.defaultType

    @Slot(str, result=str)
    def resolveType(self, game_id):
        """Pad type for a game from its controller profile, else the default type (no title known)."""
        return self.resolveTypeFor(game_id, '')

    def _physical_ids(self):
        return {(d['vendor_id'], d['product_id']) for d in (self._state or {}).get('owned_devices', [])
                if d.get('connected') is True and d.get('virtual') is False}

    def _observation(self):
        if any(state == 'unknown' or state == 'failed' and running
               for _, state, running in self._sessions.values()):
            return [], False
        return sorted({game for game, state, _ in self._sessions.values()
                       if state in ('launching', 'running')}), True

    @Slot(str, str, str)
    def observe(self, session_id, game_id, state):
        if not _game_id(session_id) or not _game_id(game_id) or state not in ('launching', 'running', 'unknown', 'failed', 'closed'):
            return
        previous = self._sessions.get(session_id)
        if session_id in self._terminal:
            return
        if previous is not None and previous[0] != game_id:
            # An impossible identity change is uncertainty, never a confirmed exit.
            self._sessions[session_id] = (previous[0], 'unknown', previous[2])
        elif state == 'closed' or state == 'failed' and not (previous and previous[2]):
            self._terminal.add(session_id)
            self._sessions.pop(session_id, None)
            if previous is not None and not self._sessions:
                self._restore_pending = True
        else:
            self._sessions[session_id] = (game_id, state, state == 'running' or bool(previous and previous[2]))
        # An explicit tracker update can invalidate an old idle lease now, not
        # merely after the timer's next five-second heartbeat.
        if self._timer.isActive(): self._heartbeat()

    def _start(self, kind, desired=None, force=False, persist=False, callback=None, legacy_allowed=False,
               observation=None, launch_game_id=None):
        if self._closed or self.busy:
            return False
        if self._client is None:
            from native_services.unified_controller_control import ControllerControlClient
            self._client = ControllerControlClient()
        self._callback, self._legacy_allowed = callback, legacy_allowed
        job = _OutputJob(self._client, kind, desired, force, persist,
                         self._observation() if observation is None else observation, self, launch_game_id)
        self._job = job
        job.finished.connect(self._finished)
        self._status = 'Reading native virtual pad…' if desired is None else 'Preparing native virtual pad…'
        job.start()
        self.changed.emit()
        return True

    @Slot(result=bool)
    def refresh(self):
        if not self._load():
            self.changed.emit()
        return self._start('refresh')

    @Slot()
    def start(self):
        if not self._closed and not self._timer.isActive():
            self._timer.start()
            self._heartbeat()

    @Slot()
    def _heartbeat(self):
        if self._closed or self.busy:
            return
        # Restore only on an observed final exit, never startup/UI selection.
        desired = None
        if self._restore_pending and not self._sessions and not self._uncertain:
            self._restore_pending = False  # rejection/uncertainty is never auto-retried
            if self._load() and self.defaultType in VIRTUAL_PAD_TYPES:
                desired = self.defaultType
        self._start('heartbeat', desired)

    @Slot(str, result=bool)
    @Slot(str, bool, result=bool)
    def setDefaultType(self, pad_type, force=False):
        return self._apply(pad_type, force, True)

    @Slot(str, result=bool)
    @Slot(str, bool, result=bool)
    def applyType(self, pad_type, force=False):
        return self._apply(pad_type, force, False)

    def _apply(self, pad_type, force, persist):
        if self._uncertain or pad_type not in VIRTUAL_PAD_TYPES or type(force) is not bool:
            self._error = 'Refresh uncertain output state, then explicitly choose a supported pad type.'
            self.changed.emit()
            return False
        return self._start('set', pad_type, force, persist)

    def prepareLaunch(self, game_id, callback, title=''):
        """Async pre-spawn admission; callback(success, message), never force.

        Legacy keyboard/mouse/Xbox360 launches may continue unverified ONLY if
        no assignment/nonlegacy default/known mismatched output requires a pad.
        """
        if self.busy and self._job.kind in ('refresh', 'heartbeat') and self._launch_waiter is None:
            # A user's Play may wait for one bounded observation, never queue
            # behind another switch. Resolve intent afresh when that read settles.
            self._launch_waiter = (game_id, callback, title)
            return True
        if self._closed or self.busy or self._uncertain or not self._load():
            callback(False, 'Virtual pad operation pending, uncertain or settings unreadable. Refresh and retry Play.')
            return False
        configured = self._doc.get('virtual_gamepad_driver', 'xbox360')
        desired = self.resolveTypeFor(game_id, title)
        if desired == PASSTHROUGH:
            # Never block Play for a passthrough game. Switch the virtual pad only if it would collide with a real
            # pad's id (then hiding it would hide the real one too).
            self._launch_mode = PASSTHROUGH
            physical = self._physical_ids()
            current = self.currentType or configured
            switch_to = None
            if _VIRTUAL_IDS.get(current) in physical:
                switch_to = next((t for t in VIRTUAL_PAD_TYPES if _VIRTUAL_IDS[t] not in physical), None)
            return self._start('launch', switch_to, False, False, callback, True,
                               self._observation(), launch_game_id=game_id)
        self._launch_mode = 'virtual'
        if configured not in VIRTUAL_PAD_TYPES or desired not in VIRTUAL_PAD_TYPES:
            callback(False, 'Configured virtual pad type is unsupported. Choose a supported type before Play.')
            return False
        legacy = (not self.profileType(self.profileForGame(game_id)) and configured == 'xbox360'
                  and desired == 'xbox360' and (not self.currentType or self.currentType == desired))
        # The job reconciles current tracked games, applies any requested type
        # while the engine is idle, then reserves this game before readback/spawn.
        return self._start('launch', desired, False, False, callback, legacy,
                           self._observation(), launch_game_id=game_id)

    @Slot()
    def _finished(self):
        job = self._job
        if job is None: return
        self._job = None
        callback, legacy = self._callback, self._legacy_allowed
        self._callback, self._legacy_allowed = None, False
        success, uncertain, state, code, attempted = job.result or (False, job.desired is not None, None, 'ERR_OUTPUT_UNAVAILABLE', False)
        kind, desired = job.kind, job.desired
        job.deleteLater()
        if self._closed: return
        self._available = state is not None
        if state is not None:
            self._state = state
        else:
            # A failed readback invalidates any prior observation. In
            # particular, never display a pre-write type as though it were
            # current after an ambiguous write followed by a read failure.
            self._state = None
        if kind == 'refresh' and success:
            self._uncertain = False
        else:
            self._uncertain = self._uncertain or uncertain
        self._load()
        if success:
            self._error = ''
            self._status = 'Native virtual pad read back: ' + (self.currentType or 'not created')
        else:
            self._error = code
            self._status = ('Virtual pad outcome uncertain. Refresh to reconcile; no retry was sent.' if self._uncertain
                            else 'Virtual pad unavailable or change rejected: ' + code + '. No retry was sent.')
        # No sent switch, no known mismatch, no explicit intent: optional legacy
        # launch continues, without claiming a pad was configured/applied.
        # A legacy launch (no per-game type, Xbox 360 default) behaves as before W16 on any pad failure,
        # e.g. an engine timeout: Play must never be blocked for games that never asked for a pad type.
        # launchEnvironment() stays empty (no fresh connected readback), so nothing is hidden from the game.
        fallback = kind == 'launch' and legacy and not success
        if fallback:
            self._status = 'Virtual pad unavailable/unverified. Continuing legacy launch without changing output.'
        self.changed.emit()
        if desired is not None: self.operationFinished.emit(success)
        if callback is not None:
            callback(success or fallback, '' if success or fallback else self._status)
        waiter, self._launch_waiter = self._launch_waiter, None
        if waiter is not None:
            # The guard callback remains correlated by LaunchView; a changed UI
            # selection can never turn this completion into a late spawn.
            self.prepareLaunch(*waiter)
        # A tracker event during an in-flight operation must not leave the old
        # lease live until the next timer; send the latest observation only.
        if self._timer.isActive() and not self.busy and job.observation != self._observation():
            QTimer.singleShot(0, self._heartbeat)

    @Slot()
    def shutdown(self):
        self._closed = True
        self._timer.stop()
        self._callback = None
        self._launch_waiter = None
        if self._job is not None:
            # At most heartbeat + switch + readback, each client call <=30s.
            if not self._job.wait(91000):
                raise RuntimeError('Virtual pad operation is still settling')
            self._job.deleteLater()
            self._job = None
