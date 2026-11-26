"""Qt host for Xodus's NEXUS_AUTH_V1 external sign-in transport."""
from __future__ import annotations
import json, os, stat
from pathlib import Path
from shiboken6 import getCppPointer
from PySide6.QtCore import QObject, Property, Signal, Slot, QProcess, QProcessEnvironment, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineQuick import QQuickWebEngineProfile
from native_services.xodus_auth_protocol import XodusAuthProtocol, XodusAuthProtocolError, AUTH_HOSTS, FRAME_LIMIT

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; MSAppHost/3.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.102 Safari/537.36 Edge/18.26100"
MAX_OUTPUT = 2 * 1024 * 1024

class _AuthRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent=None):
        super().__init__(parent); self._headers = {}
    def setHeaders(self, headers):
        self._headers = dict(headers)
    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() != "https" or url.host().lower() not in AUTH_HOSTS:
            return
        for name, value in self._headers.items():
            info.setHttpHeader(name.encode("ascii"), value.encode("ascii"))


class _AuthWebBridge(QObject):
    """Narrow WebChannel surface: the sign-in page can only return its IPC payload."""
    def __init__(self, service, parent=None):
        super().__init__(parent); self._service = service

    @Slot(str)
    def notify(self, payload):
        sid = self._service._active
        if sid is not None:
            self._service.sendPayload(sid, payload)


class XodusAuthService(QObject):
    """Launches Xodus login in private stdio mode and hosts its Microsoft pages in Waystone."""
    pageRequested = Signal(int, str)
    pageClosed = Signal(int)
    contextRequested = Signal(int, str)
    finished = Signal(bool)
    changed = Signal()

    def __init__(self, binary, parent=None):
        super().__init__(parent)
        self.binary = Path(binary).expanduser()
        self._process = None; self._protocol = None; self._buffer = bytearray()
        self._total = 0; self._active = None; self._running = False; self._requested = False
        self._message = "Xbox sign-in is not connected."
        self._profile = None
        self._interceptor = _AuthRequestInterceptor(self)
        self._web_bridge = _AuthWebBridge(self, self)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(600000)
        self._timer.timeout.connect(lambda: self._fail("Xbox sign-in timed out."))

    @staticmethod
    def _same_profile(first, second):
        if first is None or second is None:
            return False
        try:
            return getCppPointer(first)[0] == getCppPointer(second)[0]
        except (RuntimeError, TypeError, IndexError):
            return first is second

    @Slot(QQuickWebEngineProfile, result=bool)
    def attachProfile(self, profile):
        if profile is None or (self._profile is not None and not self._same_profile(self._profile, profile)):
            return False
        self._profile = profile
        profile.setOffTheRecord(True)
        profile.setHttpCacheType(QQuickWebEngineProfile.HttpCacheType.NoCache)
        profile.setPersistentCookiesPolicy(QQuickWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        profile.setHttpUserAgent(USER_AGENT)
        profile.setUrlRequestInterceptor(self._interceptor)
        if self._requested:
            self._launch()
        return True

    @Slot(QQuickWebEngineProfile)
    def detachProfile(self, profile):
        if not self._same_profile(profile, self._profile):
            return
        if self._running:
            self.cancel()
        try:
            profile.setUrlRequestInterceptor(None)
        except RuntimeError:
            pass
        self._profile = None

    @Property(QObject, constant=True)
    def webBridge(self): return self._web_bridge
    @Property(bool, notify=changed)
    def running(self): return self._running
    @Property(str, notify=changed)
    def message(self): return self._message
    @Property(int, notify=changed)
    def activeSession(self): return self._active or 0
    @Property('QVariantMap', notify=changed)
    def activeHeaders(self):
        return dict(self._interceptor._headers)

    @Slot(str, result=bool)
    def trustedAuthUrl(self, value):
        from native_services.xodus_auth_protocol import trusted_auth_url
        return trusted_auth_url(value)

    @Slot(result=bool)
    def beginLogin(self):
        if self._running or not self.binary.is_absolute() or not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            self._message = "The configured Xodus backend is unavailable or already busy."
            self.changed.emit(); return False
        self._protocol = XodusAuthProtocol(); self._buffer.clear(); self._total = 0; self._active = None
        self._running = True; self._requested = True
        self._message = "Complete Microsoft sign-in in this Waystone panel."
        self._timer.start(); self.changed.emit()
        if self._profile is not None:
            self._launch()
        return True

    def _launch(self):
        if not self._running or not self._requested or self._profile is None or self._process is not None:
            return
        process = QProcess(self); self._process = process
        process.setProgram(str(self.binary))
        process.setArguments(["--external-webview-stdio", "login"])
        env = QProcessEnvironment.systemEnvironment()
        env.insert("XODUS_LOG", "off"); env.insert("RUST_LOG", "off")
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._discard_stderr)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._process_finished)
        process.start()

    def _write_event(self, frame):
        process = self._process
        if process is None or process.state() != QProcess.ProcessState.Running: return False
        try:
            data = (json.dumps(frame, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
            if len(data) > FRAME_LIMIT: return False
            return process.write(data) == len(data)
        except (TypeError, ValueError, OSError): return False

    @Slot(int, str, result=bool)
    def sendPayload(self, session, payload):
        if (not self._running or session != self._active or not isinstance(payload, str)
                or len(payload.encode("utf-8")) > 65536):
            return False
        if not self._write_event({"type": "ipc", "session": session, "payload": payload}):
            self._fail("The Xbox sign-in connection was interrupted."); return False
        return True

    @Slot()
    def cancel(self):
        if not self._running: return
        sid = self._active
        if sid is not None: self._write_event({"type": "closed", "session": sid})
        self._finish(False, "Xbox sign-in cancelled.", terminate=True)

    def _read_stdout(self):
        process = self._process
        if process is None: return
        self._total += process.bytesAvailable()
        if self._total > MAX_OUTPUT:
            self._fail("Xodus sign-in returned too much data."); return
        self._buffer.extend(bytes(process.readAllStandardOutput()))
        if len(self._buffer) > FRAME_LIMIT and b"\n" not in self._buffer:
            self._fail("Xodus sign-in frame exceeded its limit."); return
        while b"\n" in self._buffer:
            raw, _, rest = self._buffer.partition(b"\n"); self._buffer = bytearray(rest)
            if len(raw) > FRAME_LIMIT:
                self._fail("Xodus sign-in frame exceeded its limit."); return
            try:
                line = raw.decode("utf-8")
                frame = self._protocol.accept(line)
                if frame is None: continue
                if frame["type"] == "ready":
                    if not self._write_event({"type": "hello", "version": 1}):
                        raise XodusAuthProtocolError("Sign-in handshake failed")
                elif frame["type"] == "open":
                    self._active = frame["session"]
                    self._interceptor.setHeaders(frame["headers"])
                    self._message = "Complete Microsoft sign-in in this Waystone panel."
                    self.pageRequested.emit(frame["session"], frame["url"]); self.changed.emit()
                elif frame["type"] == "close":
                    self._active = None; self._interceptor.setHeaders({})
                    self.pageClosed.emit(frame["session"]); self.changed.emit()
                elif frame["type"] == "context":
                    self.contextRequested.emit(frame["session"], frame["context"])
            except (UnicodeDecodeError, XodusAuthProtocolError, RuntimeError):
                self._fail("Xodus sign-in protocol was rejected."); return

    def _discard_stderr(self):
        if self._process is not None:
            self._process.readAllStandardError()  # Never surface backend output; it may contain credentials.

    def _process_error(self, _error):
        if self._running: self._finish(False, "Could not start or communicate with Xodus sign-in.", terminate=True)

    def _process_finished(self, code, _status):
        self._discard_stderr()
        process = self._process
        success = bool(code == 0 and self._protocol and self._protocol.ready
                       and self._protocol.latest > 0 and self._protocol.active is None
                       and not self._buffer)
        if success:
            self._finish(True, "Signed in with Xbox through Waystone and Xodus.")
        elif self._running:
            self._finish(False, "Xbox sign-in did not complete. Check the Xodus backend and system keyring.")
        if process is not None: process.deleteLater()
        self._process = None

    def _fail(self, message):
        if self._running: self._finish(False, message, terminate=True)

    def _finish(self, success, message, terminate=False):
        if not self._running: return
        self._running = False; self._requested = False; self._message = message; self._timer.stop()
        sid = self._active; self._active = None; self._interceptor.setHeaders({})
        if sid is not None: self.pageClosed.emit(sid)
        if terminate and self._process is not None:
            self._process.terminate()
            QTimer.singleShot(1500, self._kill_if_running)
        if self._profile is not None:
            self._profile.cookieStore().deleteAllCookies(); self._profile.clearHttpCache()
        self.changed.emit(); self.finished.emit(bool(success))

    def _kill_if_running(self):
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()

    def shutdown(self):
        self.cancel()

