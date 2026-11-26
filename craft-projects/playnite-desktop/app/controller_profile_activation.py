"""Dedicated async profile selection over the existing native control protocol.

This is a client adapter, not an engine or a second controller backend. It never
touches ControllerView, its editing selection, or QML drafts. Construction does
not instantiate a client or perform I/O. Requests are never queued or replayed.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QObject, Property, QThread, Signal, Slot


_REVISION = re.compile(r'[a-fA-F0-9]{64}')
_ERROR = re.compile(r'ERR_[A-Z0-9_]{1,60}')
# At most three sequential native requests, each bounded by the client's 30s
# maximum. The normal lazily constructed client uses its existing 5s default.
_SHUTDOWN_WAIT_MS = 91000


def _code(exc):
    code = getattr(exc, 'code', '')
    return code if isinstance(code, str) and _ERROR.fullmatch(code) else 'ERR_CONTROLLER_UNAVAILABLE'


def _inventory(client):
    data = client.request('profiles')
    if not isinstance(data, dict):
        raise ValueError('invalid inventory')
    names, active, revision = data.get('names'), data.get('active_profile'), data.get('revision')
    if (not isinstance(names, list) or not 0 < len(names) <= 256
            or not all(isinstance(n, str) and 0 < len(n) <= 256 for n in names)
            or len(set(names)) != len(names)
            or not isinstance(active, str) or active not in names
            or not isinstance(revision, str) or not _REVISION.fullmatch(revision)):
        raise ValueError('invalid inventory')
    return names, active, revision


class _ActivationJob(QThread):
    def __init__(self, client, profile, request_id, parent):
        super().__init__(parent)
        self.client = client
        self.profile = profile  # None means explicit read-only refresh
        self.request_id = request_id
        self.result = None

    def run(self):
        try:
            names, active, revision = _inventory(self.client)
        except Exception as exc:
            # Even an exception marked uncertain cannot imply a profile mutation
            # here: this job has issued only a read.
            self.result = (False, False, '', _code(exc))
            return
        if self.profile is None or active == self.profile:
            self.result = (True, False, active, '')
            return
        if self.profile not in names:
            self.result = (False, False, active, 'ERR_PROFILE_NOT_FOUND')
            return
        try:
            self.client.request('profile_select', name=self.profile, expected_revision=revision)
        except Exception as exc:
            native_uncertain = getattr(exc, 'uncertain', None)
            # Preserve explicit native certainty; unknown exceptions during the
            # mutation call cannot establish that no bytes/effect reached it.
            uncertain = native_uncertain if type(native_uncertain) is bool else True
            self.result = (False, uncertain, '' if uncertain else active, _code(exc))
            return
        try:
            _, observed, _ = _inventory(self.client)
            if observed != self.profile:
                raise ValueError('selection not read back')
        except Exception:
            # Selection may be committed, even if readback failed or differs.
            self.result = (False, True, '', 'ERR_CONTROLLER_VERIFY_FAILED')
            return
        self.result = (True, False, observed, '')


class ControllerProfileActivation(QObject):
    """Explicit native selection with CAS and correlated verified completion.

    request(name, id) -> True means an async job started, not profile activation.
    finished(id, success, uncertain) is emitted only for accepted requests, never
    refresh. IDs are non-empty strings, so integer runtime IDs need str(id) at
    the bridge and conversion back before ControllerProfileRuntime.complete.

    After uncertainty, request returns False until an explicit successful refresh
    supplies native readback through observed(name). Refresh itself never writes.
    ``available`` means a valid inventory was read, not verified physical input.
    """
    changed = Signal()
    finished = Signal(str, bool, bool)
    observed = Signal(str)

    def __init__(self, client=None, parent=None, *, shutdown_timeout_ms=_SHUTDOWN_WAIT_MS):
        super().__init__(parent)
        if type(shutdown_timeout_ms) is not int or not 0 < shutdown_timeout_ms <= _SHUTDOWN_WAIT_MS:
            raise ValueError('shutdown timeout must be bounded and positive')
        self._client = client
        self._job = None
        self._closed = False
        self._available = False
        self._active = ''
        self._uncertain = False
        self._status = 'Controller profile runtime has not connected.'
        self._shutdown_timeout_ms = shutdown_timeout_ms

    @Property(bool, notify=changed)
    def busy(self):
        return self._job is not None

    @Property(bool, notify=changed)
    def available(self):
        return self._available

    @Property(bool, notify=changed)
    def uncertain(self):
        return self._uncertain

    @Property(str, notify=changed)
    def activeProfile(self):
        return self._active

    @Property(str, notify=changed)
    def status(self):
        return self._status

    def _start(self, profile, request_id):
        if self._closed or self.busy:
            return False
        if self._client is None:
            from native_services.unified_controller_control import ControllerControlClient
            self._client = ControllerControlClient()
        job = _ActivationJob(self._client, profile, request_id, self)
        self._job = job
        job.finished.connect(self._finished)
        self._status = 'Reading native active profile…' if profile is None else 'Selecting native controller profile…'
        job.start()
        self.changed.emit()
        return True

    @Slot(str, str, result=bool)
    def request(self, profile_name, request_id):
        if self._closed or self.busy or self._uncertain:
            return False
        if (not isinstance(profile_name, str) or not profile_name.strip() or len(profile_name) > 256
                or any(ord(c) < 32 for c in profile_name)
                or not isinstance(request_id, str) or not request_id or len(request_id) > 256
                or any(ord(c) < 32 for c in request_id)):
            self._status = 'Invalid profile selection. No command was sent.'
            self.changed.emit()
            return False
        return self._start(profile_name, request_id)

    @Slot(result=bool)
    def refresh(self):
        """Read-only reconciliation; never queues a pending mutation."""
        return self._start(None, '')

    @Slot()
    def _finished(self):
        job = self._job
        if job is None:
            return
        self._job = None
        success, uncertain, active, code = job.result or (
            False, job.profile is not None, '', 'ERR_CONTROLLER_UNAVAILABLE')
        profile, request_id = job.profile, job.request_id
        job.deleteLater()
        if self._closed:
            return
        if success:
            self._available = True
            self._active = active
            self._uncertain = False
            self._status = 'Native active profile read back: ' + active
        else:
            self._available = False
            self._uncertain = self._uncertain or uncertain
            if uncertain:
                self._active = ''
                self._status = 'Profile outcome uncertain. Refresh to reconcile; no retry was sent.'
            else:
                self._status = 'Profile selection unavailable or rejected: ' + code + '. No retry was sent.'
        self.changed.emit()
        if profile is not None:
            self.finished.emit(request_id, success, uncertain)
        elif success:
            self.observed.emit(active)

    @Slot()
    def shutdown(self):
        """Close admission, bounded wait, no thread termination or fake completion.

        Injected clients must honor their own bounded request timeouts. If they
        do not settle, retain this object and call shutdown again after settlement;
        a running QThread is never destroyed or forcibly terminated here.
        """
        self._closed = True
        job = self._job
        if job is None:
            return
        if not job.wait(self._shutdown_timeout_ms):
            raise RuntimeError('Controller profile activation is still settling')
        self._job = None
        job.deleteLater()
