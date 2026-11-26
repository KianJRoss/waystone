"""Own the GUI-free controller engine child. Construction never starts input capture."""
from pathlib import Path
import os
from PySide6.QtCore import QObject, Property, Signal, Slot, QProcess, QTimer

class ControllerEngineHost(QObject):
    changed = Signal()
    ready = Signal()

    def __init__(self, executable, data_dir, legacy_data_dir=None, *, fixture=False,
                 process_factory=QProcess, parent=None):
        super().__init__(parent)
        self.executable = Path(executable).absolute()
        self.data_dir = Path(data_dir).absolute()
        self.legacy = Path(legacy_data_dir).absolute() if legacy_data_dir else None
        self.fixture = fixture
        self.factory = process_factory
        self._process = None
        self._ready = False
        self._buffer = b''
        self._status = 'Controller engine has not started.'
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(20000)
        self._deadline.timeout.connect(self._timeout)

    @Property(bool, notify=changed)
    def running(self):
        return self._process is not None and self._process.state() != QProcess.NotRunning

    @Property(bool, notify=changed)
    def available(self):
        return self.running and self._ready

    @Property(str, notify=changed)
    def status(self):
        return self._status

    def _set_status(self, message):
        self._status = message
        self.changed.emit()

    @Slot()
    def start(self):
        if self.running:
            return
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            self._set_status('The native controller engine is not packaged in this release.')
            return
        if self.fixture and self.legacy:
            self._set_status('Fixture mode cannot use legacy settings.')
            return
        if not self.fixture and not (self.data_dir / 'profiles.json').is_file():
            self._set_status('Existing controller profiles must be migrated before starting.')
            return
        if self.legacy and not self.legacy.is_dir():
            self._set_status('The configured legacy controller directory is missing.')
            return
        if not self.fixture:
            try:
                from native_services.unified_controller_control import _default_socket_path
                import socket
                sock_path = _default_socket_path()
                if sock_path.is_socket() or sock_path.exists():
                    alive = False
                    try:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        s.settimeout(0.2)
                        s.connect(str(sock_path))
                        s.close()
                        alive = True
                    except Exception:
                        alive = False
                    if alive:
                        self._ready = True
                        self._set_status('Connected to running controller engine.')
                        self.ready.emit()
                        return
                    else:
                        sock_path.unlink()
            except Exception:
                pass
        p = self.factory(self)
        self._process = p
        self._buffer = b''
        self._ready = False
        p.setProgram(str(self.executable))
        args = ['--data-dir', str(self.data_dir)]
        if self.legacy:
            args += ['--legacy-data-dir', str(self.legacy)]
        if self.fixture:
            args.append('--fixture')
        p.setArguments(args)
        p.setProcessChannelMode(QProcess.SeparateChannels)
        p.readyReadStandardOutput.connect(self._read_stdout)
        p.readyReadStandardError.connect(lambda: p.readAllStandardError())
        p.errorOccurred.connect(self._error)
        p.finished.connect(self._finished)
        self._set_status('Starting the Waystone-owned controller engine…')
        self._deadline.start()
        p.start()

    def _read_stdout(self):
        p = self._process
        if p is None:
            return
        self._buffer += bytes(p.readAllStandardOutput())
        if len(self._buffer) > 65536:
            self.shutdown()
            self._set_status('Controller engine sent an invalid startup response; stopped without retry.')
            return
        while b'\n' in self._buffer:
            line, self._buffer = self._buffer.split(b'\n', 1)
            if line.rstrip(b'\r') == b'WAYSTONE_ENGINE_READY=1' and not self._ready:
                self._ready = True
                self._deadline.stop()
                self._set_status('Native controller engine ready; connecting controls…')
                self.ready.emit()

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self._deadline.stop()
            self._ready = False
            self._set_status('Native controller engine could not start. No fallback engine was launched.')

    def _finished(self, code, exit_status):
        self._deadline.stop()
        self._ready = False
        p, self._process = self._process, None
        if p is not None:
            p.deleteLater()
        self._set_status(f'Controller engine stopped (exit {code}). No automatic restart was attempted.')

    def _timeout(self):
        self.shutdown()
        self._set_status('Controller engine readiness timed out; stopped without retry.')

    @Slot()
    def shutdown(self):
        self._deadline.stop()
        self._ready = False
        p = self._process
        if p is None:
            return
        p.write(b'shutdown\n')
        p.closeWriteChannel()
        # Bounded application-exit cleanup; only this host's owned child is signaled.
        if not p.waitForFinished(8000):
            p.terminate()
            if not p.waitForFinished(3000):
                p.kill()
                p.waitForFinished(2000)
        self.changed.emit()
