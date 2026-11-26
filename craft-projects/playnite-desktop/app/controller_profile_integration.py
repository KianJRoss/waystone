"""GUI-thread wiring of file assignments, canonical tracker events and native CAS."""
from PySide6.QtCore import QObject, Slot
from controller_profile_runtime import ControllerProfileRuntime


class ControllerProfileIntegration(QObject):
    def __init__(self, assignments, activation, canonicalize=None, parent=None, *, native_writers=()):
        super().__init__(parent)
        self.assignments = assignments
        self.activation = activation
        self._canonicalize = canonicalize or (lambda key: key)
        self._native_writers = tuple(native_writers)
        self._deferred_for_writer = False
        for writer in self._native_writers: writer.changed.connect(self._nativeStateChanged)
        self.runtime = ControllerProfileRuntime(self._resolve, self._activate)
        assignments.documentChanged.connect(self.reconcile)
        activation.finished.connect(self._completed)
        activation.observed.connect(self._observed)
        activation.changed.connect(self._publish)
        self._publish()

    def _activate(self, name, ident):
        if any(writer.busy or writer.uncertain for writer in self._native_writers):
            self._deferred_for_writer = not any(writer.uncertain for writer in self._native_writers)
            return False
        self._deferred_for_writer = False
        if not self.assignments.ensureRuntimeMetadata(): return False
        return self.activation.request(name, str(ident))

    def mutationAllowed(self, writer=None):
        if self.runtime.pending_request_id is not None or self.runtime.uncertain or self.activation.uncertain or getattr(self.activation, 'busy', False): return False
        return not any(item.uncertain or (item is not writer and item.busy) for item in self._native_writers)

    @Slot()
    def _nativeStateChanged(self):
        if any(item.uncertain for item in self._native_writers):
            self._deferred_for_writer = False
        self._publish()
        # A known-not-sent runtime selection may follow completion of a busy
        # editor read/write. Lost replies never trigger an automatic replay.
        if self._deferred_for_writer and not any(item.busy or item.uncertain for item in self._native_writers):
            self._deferred_for_writer = False
            self.runtime.reconcile()
            self._publish()

    def _resolve(self, game_id):
        if not self.assignments.available:
            raise ValueError('Profile storage unavailable')
        canonical = self._canonicalize(game_id) if game_id else ''
        return self.assignments.resolveProfile(canonical or game_id or '') or None

    @Slot(str, str, str)
    def observe(self, session_id, game_id, state):
        # Keep original canonical identity immutable; apply existing explicit ID
        # link resolution only when resolving, never infer identity from titles.
        self.runtime.observe(session_id, game_id, state)
        self._publish()

    @Slot()
    def reconcile(self):
        self.runtime.reconcile()
        self._publish()

    @Slot(result=bool)
    def refresh(self):
        # Explicit user/startup read. Native readback, not local JSON, clears
        # an uncertain mutation before any selection can be reconciled.
        if not self.assignments.refresh():
            self._publish()
            return False
        return self.activation.refresh()

    @Slot(str, bool, bool)
    def _completed(self, request_id, success, uncertain):
        try: ident = int(request_id)
        except (ValueError, TypeError): return
        self.runtime.complete(ident, success, uncertain=uncertain)
        if success: self.assignments.refresh()
        self._publish()

    @Slot(str)
    def _observed(self, profile):
        if self.runtime.pending_request_id is not None: return
        self.runtime.reset_from_observation(profile)
        self.runtime.reconcile()
        self._publish()

    @Slot(str)
    def observeEditorActive(self, profile):
        # Readback from an explicit legacy editor selection updates our baseline
        # but does not immediately undo the user's temporary manual choice.
        if self.runtime.pending_request_id is None and not self.runtime.uncertain and not self.activation.uncertain and not any(item.uncertain for item in self._native_writers):
            self.runtime.reset_from_observation(profile)
            self._publish()

    def _publish(self):
        blocked = not self.mutationAllowed()
        self.assignments.setEngineMutationBlocked(bool(blocked))
        game_id = next(reversed(self.runtime.active_sessions.values()), '')
        self.assignments.setRuntimeGame(self._canonicalize(game_id) or game_id if game_id else '')
        if self.runtime.uncertain or self.activation.uncertain or any(item.uncertain for item in self._native_writers):
            message = 'Engine selection uncertain · Refresh in Controllers to reconcile.'
        elif self.runtime.pending_request_id is not None:
            message = 'Applying ' + (self.runtime.pending_profile or '') + ' · awaiting native readback'
        elif self.runtime.status in ('rejected', 'resolution_failed'):
            message = 'Configured selection not applied · ' + self.activation.status
        elif self.runtime.active_profile:
            message = 'Native active profile read back: ' + self.runtime.active_profile
        else:
            message = self.activation.status
        self.assignments.setRuntimeStatus(message)
