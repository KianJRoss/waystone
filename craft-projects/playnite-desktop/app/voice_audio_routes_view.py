"""Explicit Linux voice routing; no probing/capture/global audio changes on startup."""
from pathlib import Path
import os
import shutil
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread, QTimer


def owned_voice_pids(proc=Path('/proc'), root=None):
    """Only this UI and its QtWebEngine descendants, never a launched game's PID."""
    root = os.getpid() if root is None else root
    found = {root}; pending = [root]; visited = set()
    while pending and len(visited) < 512:
        pid = pending.pop()
        if pid in visited: continue
        visited.add(pid)
        try:
            children = (proc / str(pid) / 'task' / str(pid) / 'children').read_text().split()
        except (OSError, ValueError): continue
        for raw in children[:128]:
            if not raw.isdecimal(): continue
            child = int(raw)
            try:
                executable = (proc / raw / 'exe').resolve(strict=True)
                uid = (proc / raw).stat().st_uid
            except OSError: continue
            if uid == os.getuid() and executable.name == 'QtWebEngineProcess':
                found.add(child); pending.append(child)
    return found


class _AudioJob(QThread):
    def __init__(self, fn, parent):
        super().__init__(parent); self.fn = fn; self.result = None
    def run(self):
        try: self.result = {'ok': True, 'data': self.fn()}
        except Exception: self.result = {'ok': False}


class VoiceAudioRoutesView(QObject):
    changed = Signal()
    def __init__(self, voice, router=None, available=None, pids=None, parent=None):
        super().__init__(parent)
        self._voice = voice; self._router = router; self._pids = pids or owned_voice_pids
        self._available = bool(shutil.which('pactl')) if available is None else available
        self._inputs = []; self._outputs = []; self._input = self._output = ''
        self._status = 'Refresh devices to choose your microphone and speakers.'
        self._job = None; self._operation = ''; self._requested = None
        self._selection = False; self._closed = False
        self._timer = QTimer(self); self._timer.setInterval(1500)
        self._timer.timeout.connect(self._route_if_active)
        voice.changed.connect(self._voice_changed)
    @Property(bool, constant=True)
    def available(self): return self._available
    @Property(bool, notify=changed)
    def busy(self): return self._job is not None
    @Property('QVariantList', notify=changed)
    def inputs(self): return [dict(r) for r in self._inputs]
    @Property('QVariantList', notify=changed)
    def outputs(self): return [dict(r) for r in self._outputs]
    @Property(str, notify=changed)
    def inputId(self): return self._input
    @Property(str, notify=changed)
    def outputId(self): return self._output
    @Property(str, notify=changed)
    def status(self): return self._status
    def _backend(self):
        if self._router is None:
            from native_services.voice_audio_routes import VoiceAudioRouter
            self._router = VoiceAudioRouter(timeout=1.0)
        return self._router
    def _start(self, operation, fn):
        if self.busy or self._closed or not self.available: return False
        self._operation = operation
        self._job = _AudioJob(fn, self)
        self._job.finished.connect(self._finished)
        self._job.start(); self.changed.emit(); return True
    @Slot()
    def refreshDevices(self):
        if self._start('inventory', lambda: self._backend().get_devices()):
            self._status = 'Reading system audio devices…'; self.changed.emit()
    @Slot(str)
    def selectInput(self, value): self._select(value, self._output)
    @Slot(str)
    def selectOutput(self, value): self._select(self._input, value)
    def _select(self, input_id, output_id):
        if self.busy or self._closed: return
        if input_id not in {r['id'] for r in self._inputs} | {''}: return
        if output_id not in {r['id'] for r in self._outputs} | {''}: return
        self._requested = (input_id, output_id)
        pids = self._pids() if self._voice.connected else set()
        if self._start('selection', lambda: self._backend().route_selected(input_id, output_id, pids)):
            self._status = 'Applying Waystone voice routing…'; self.changed.emit()
    @Slot()
    def _voice_changed(self):
        if self._selection and self._voice.connected and not self._closed:
            self._timer.start(); self._route_if_active()
        else: self._timer.stop()
    @Slot()
    def _route_if_active(self):
        if self.busy or not self._selection or not self._voice.connected: return
        input_id, output_id = self._input, self._output
        pids = self._pids()
        self._start('route', lambda: self._backend().route_selected(input_id, output_id, pids))
    @Slot()
    def _finished(self):
        job = self._job
        if job is None: return
        result = job.result or {'ok': False}
        self._job = None; job.deleteLater()
        if self._closed: return
        data = result.get('data', {})
        if not result['ok'] or not isinstance(data, dict) or data.get('errors') or data.get('error'):
            self._status = 'Audio routing unavailable or device disconnected. Refresh and choose again.'
            self._timer.stop(); self._selection = False
            if self._voice.connected: self._voice.setMuted(True)
        elif self._operation == 'inventory':
            def rows(key):
                values = data.get(key, [])
                if not isinstance(values, list) or len(values) > 128: raise ValueError()
                result = []
                for r in values:
                    name, label = r['name'], r.get('description') or r['name']
                    if not isinstance(name, str) or not name or len(name) > 512 or not isinstance(label, str) or len(label)>256: raise ValueError()
                    if any(ord(c) < 32 for c in name + label): raise ValueError()
                    result.append({'id': name, 'label': label})
                return result
            try:
                inputs, outputs = rows('sources'), rows('sinks')
                self._inputs, self._outputs = inputs, outputs
                missing = ((self._input and self._input not in {r['id'] for r in inputs}) or
                           (self._output and self._output not in {r['id'] for r in outputs}))
                self._status = 'Selected device disconnected. Choose another device.' if missing else 'System devices ready. Only Waystone voice streams will be moved.'
                if missing: self._selection = False; self._timer.stop()
            except (KeyError, TypeError, ValueError):
                self._status = 'Audio inventory unavailable. Refresh to retry.'
        else:
            if self._operation == 'selection' and self._requested is not None:
                self._input, self._output = self._requested; self._selection = True
            self._status = ('Waystone voice routing active.' if self._voice.connected else
                            'Selection saved for this session. It will apply when voice connects.')
            if self._selection and self._voice.connected: self._timer.start()
        self.changed.emit()
    @Slot()
    def shutdown(self):
        self._closed = True; self._timer.stop()
        if self._job is not None:
            # pactl calls have finite timeouts; never destroy a running QThread.
            self._job.wait(); self._job.deleteLater(); self._job = None
