"""Host the app-owned, original Avalonia controller studio; never a second mapper.

Only Xcb can reparent X11 foreign windows. Construction is inert; opening explicitly
starts one packaged module. Its stdin EOF owns graceful runtime shutdown. Closing
Waystone normally keeps this module alive by hiding Waystone (explicit Quit exits).
"""
from pathlib import Path
import os
import re
from PySide6.QtCore import QObject, Property, Signal, Slot, QProcess, QProcessEnvironment, QTimer
from PySide6.QtGui import QGuiApplication, QWindow

_HANDSHAKE = re.compile(rb'^WAYSTONE_CONTROLLER_XID=0x([0-9a-fA-F]{1,16})$')


def parse_handshake(line):
    match = _HANDSHAKE.fullmatch(line.strip())
    return int(match[1], 16) if match and int(match[1], 16) else None


class ControllerStudioHost(QObject):
    changed = Signal()
    windowChanged = Signal()

    def __init__(self, executable=None, data_dir=None, legacy_data_dir=None, *,
                 fixture=False, process_factory=QProcess, window_factory=None,
                 platform=None, parent=None):
        super().__init__(parent)
        self._executable = Path(executable).absolute() if executable else None
        self._data_dir = Path(data_dir).absolute() if data_dir else None
        self._legacy = Path(legacy_data_dir).absolute() if legacy_data_dir else None
        self._fixture = fixture
        self._factory = process_factory
        self._window_factory = window_factory or QWindow.fromWinId
        self._platform = platform
        self._process = None
        self._window = None
        self._buffer = b''
        self._status = 'Original controller studio has not been opened.'
        self._starting = False
        self._shutting_down = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(20000)
        self._timer.timeout.connect(self._startup_timeout)

    @Property(QObject, notify=windowChanged)
    def nativeWindow(self):
        return self._window

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(bool, notify=changed)
    def starting(self):
        return self._starting

    @Property(bool, notify=changed)
    def running(self):
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def _set_status(self, text):
        self._status = text
        self.changed.emit()

    @Slot()
    def open(self):
        if self._shutting_down:
            return
        if self.running:
            self._process.write(b'show\n')
            return
        platform = self._platform or QGuiApplication.platformName()
        if platform != 'xcb':
            self._set_status('Original controller UI needs Waystone’s X11/XWayland launch backend. No external app was started.')
            return
        if self._executable is None or not self._executable.is_file() or not os.access(self._executable, os.X_OK):
            self._set_status('The app-owned controller studio is not packaged in this Waystone build.')
            return
        if self._data_dir is None:
            self._set_status('Controller data directory is not configured. Existing settings were left unchanged.')
            return
        if self._fixture and self._legacy:
            self._set_status('Fixture mode cannot touch legacy settings.')
            return
        if not self._fixture and not (self._data_dir / 'profiles.json').is_file():
            self._set_status('Existing controller settings must be migrated before starting. No default profile was substituted.')
            return
        process = self._factory(self)
        self._process = process
        self._buffer = b''
        process.setProgram(str(self._executable))
        args = ['--data-dir', str(self._data_dir)]
        if self._legacy:
            args.extend(['--legacy-data-dir', str(self._legacy)])
        if self._fixture:
            args.append('--fixture')
        process.setArguments(args)
        env = QProcessEnvironment.systemEnvironment()
        env.remove('WAYLAND_DISPLAY')
        env.insert('QT_QPA_PLATFORM', 'xcb')
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read_stdout)
        # Drain errors but never expose arbitrary profile/input/process text in the UI.
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        self._starting = True
        self._set_status('Opening the original controller studio…')
        self._timer.start()
        process.start()

    def _read_stdout(self):
        self._buffer += bytes(self._process.readAllStandardOutput())
        if len(self._buffer) > 65536:
            self._buffer = b''
            self._set_status('Controller startup sent an invalid handshake. No automatic restart was attempted.')
            self.shutdown()
            return
        while b'\n' in self._buffer:
            line, self._buffer = self._buffer.split(b'\n', 1)
            xid = parse_handshake(line)
            if xid is None or self._window is not None:
                continue
            window = self._window_factory(xid)
            if window is None:
                self._set_status('This Qt backend cannot embed the original controller window.')
                self.shutdown()
                return
            self._window = window
            self._starting = False
            self._timer.stop()
            self.windowChanged.emit()
            self._set_status('Original UnifiedController studio · Waystone-owned runtime')

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self._starting = False
            self._timer.stop()
            self._set_status('The packaged controller studio could not start. No external installation was launched.')

    def _finished(self, code, exit_status):
        self._timer.stop()
        self._starting = False
        self._window = None
        self.windowChanged.emit()
        self._set_status(f'Controller studio stopped (exit {code}). Open Controllers to restart explicitly.')
        if self._process:
            self._process.deleteLater()
        self._process = None

    def _startup_timeout(self):
        self._starting = False
        self._set_status('Controller studio did not become ready. It was stopped; no duplicate runtime was started.')
        self.shutdown()

    @Slot()
    def shutdown(self):
        self._timer.stop()
        process = self._process
        if process is None:
            return
        self._shutting_down = True
        # Closing the pipe is the native component’s graceful shutdown contract.
        process.write(b'shutdown\n')
        process.closeWriteChannel()
        if not process.waitForFinished(8000):
            # Owned child only; normal native cleanup gets eight seconds first.
            process.terminate()
            if not process.waitForFinished(3000):
                process.kill()
                process.waitForFinished(2000)
        self._shutting_down = False
