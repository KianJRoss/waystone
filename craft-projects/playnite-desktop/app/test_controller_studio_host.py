import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QProcess
from PySide6.QtGui import QGuiApplication
from controller_studio_host import ControllerStudioHost, parse_handshake

_APP = None

def app():
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication([])
    return _APP

class Process(QObject):
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, QProcess.ExitStatus)
    errorOccurred = Signal(QProcess.ProcessError)
    def __init__(self, parent):
        super().__init__(parent);self.calls=[];self.output=b'';self.alive=False
    def setProgram(self, value):self.program=value
    def setArguments(self, value):self.args=value
    def setProcessEnvironment(self, value):self.env=value
    def setProcessChannelMode(self, value):pass
    def start(self):self.alive=True;self.calls.append('start')
    def state(self):return QProcess.Running if self.alive else QProcess.NotRunning
    def write(self, value):self.calls.append(value)
    def closeWriteChannel(self):self.calls.append('eof')
    def waitForFinished(self, timeout):self.alive=False;return True
    def readAllStandardOutput(self):value=self.output;self.output=b'';return value
    def readAllStandardError(self):return b''

def make(tmp_path, **kw):
    app();exe=tmp_path/'studio';exe.write_text('fixture');exe.chmod(0o700)
    data=tmp_path/'data';data.mkdir();(data/'profiles.json').write_text('{}')
    return ControllerStudioHost(exe, data, process_factory=Process, platform='xcb', **kw)

def test_handshake_strict():
    assert parse_handshake(b'WAYSTONE_CONTROLLER_XID=0x12a')==298
    for invalid in (b'0x12',b'WAYSTONE_CONTROLLER_XID=0x0', b'noise WAYSTONE_CONTROLLER_XID=0x123',b'WAYSTONE_CONTROLLER_XID=0x12;bad'):
        assert parse_handshake(invalid) is None

def test_construction_inert(tmp_path):
    h=make(tmp_path);assert not h.running and h.nativeWindow is None
    h.shutdown()

def test_backend_refusal_no_start(tmp_path):
    h=make(tmp_path);h._platform='wayland';h.open()
    assert not h.running and 'X11/XWayland' in h.status

def test_no_legacy_app_fallback(tmp_path):
    h=make(tmp_path);h._executable=tmp_path/'missing';h.open()
    assert not h.running and 'not packaged' in h.status

def test_unmigrated_profiles_refuse(tmp_path):
    h=make(tmp_path);(h._data_dir/'profiles.json').unlink();h.open()
    assert not h.running and 'migrated' in h.status

def test_fixture_cannot_lock_live_legacy(tmp_path):
    h=make(tmp_path,fixture=True,legacy_data_dir=tmp_path/'legacy');h.open()
    assert not h.running and 'cannot touch legacy' in h.status

def test_one_child_handshake_and_shutdown(tmp_path):
    native=QObject();h=make(tmp_path,window_factory=lambda xid: native)
    h.open();p=h._process
    assert p.calls==['start'] and h.running
    assert p.env.value('QT_QPA_PLATFORM')=='xcb' and not p.env.contains('WAYLAND_DISPLAY')
    p.output=b'WAYSTONE_CONTROLLER_XID=0x123\n';p.readyReadStandardOutput.emit()
    assert h.nativeWindow is native and not h.starting
    h.open();assert p.calls==['start',b'show\n']
    h.shutdown();assert p.calls[-2:]==[b'shutdown\n','eof']

def test_partial_handshake_waits_for_newline(tmp_path):
    native=QObject();h=make(tmp_path,window_factory=lambda xid: native);h.open();p=h._process
    p.output=b'WAYSTONE_CONTROLLER_XID=0x1';p.readyReadStandardOutput.emit();assert h.nativeWindow is None
    p.output=b'23\n';p.readyReadStandardOutput.emit();assert h.nativeWindow is native
    h.shutdown()

def test_ui_embeds_original_not_old_json_editor():
    here=Path(__file__).parent
    main = (here/'Main.qml').read_text()
    assert 'Component { id: embeddedControllers; ControllerStudio {' in main
    assert 'pageId: "controllers"' in main
    assert 'id: controllerDashboard' in main and 'parent: workspaceContainer' in main
    assert 'Component { id: nativeControllers; NativeControllerStudio {' in main
    assert 'WindowContainer' not in (here/'NativeControllerStudio.qml').read_text()
    ui=(here/'ControllerStudio.qml').read_text()
    assert 'WindowContainer' in ui and 'nativeWindow' in ui
    assert 'ControllerMappingEditor' not in ui and 'TextArea' not in ui
