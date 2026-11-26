import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Property, Signal, Slot, QSettings, QTemporaryDir
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from voice_media_view import VoiceMediaView

APP = Path(__file__).resolve().parent
if str(APP) not in sys.path: sys.path.insert(0, str(APP))


class Social(QObject):
    connectionChanged = Signal()
    def __init__(self): super().__init__(); self._party = {"partyId": "", "members": []}; self.calls=[]
    @Property(bool, notify=connectionChanged)
    def connected(self): return True
    @Property("QVariantMap", notify=connectionChanged)
    def partyState(self): return self._party
    @Slot(str)
    def leaveParty(self, party): self.calls.append(("leave", party))


class FakeVoice(QObject):
    changed = Signal()
    def __init__(self): super().__init__(); self.calls=[]
    @Property(bool, constant=True)
    def available(self): return True
    @Property(bool, notify=changed)
    def sessionActive(self): return False
    @Property(bool, notify=changed)
    def connected(self): return True
    @Property(bool, notify=changed)
    def muted(self): return True
    @Property(bool, notify=changed)
    def speakingAvailable(self): return True
    @Property("QStringList", notify=changed)
    def speakingMembers(self): return ["101"]
    @Property("QStringList", notify=changed)
    def mutedMembers(self): return []
    @Property("QVariantList", notify=changed)
    def audioInputs(self): return [{"id":"mic","label":"Mic"}]
    @Property("QVariantList", notify=changed)
    def audioOutputs(self): return [{"id":"out","label":"Headphones"}]
    @Property(str, notify=changed)
    def inputId(self): return ""
    @Property(str, notify=changed)
    def outputId(self): return ""
    @Property(str, notify=changed)
    def status(self): return "Connected · microphone muted"
    @Property("QVariantMap", notify=changed)
    def peerVolumes(self): return {"101":100}
    @Slot(str)
    def startParty(self, party): self.calls.append(("start", party))
    @Slot()
    def leave(self): self.calls.append(("leave",))
    @Slot(str, bool)
    def mutePeer(self, peer, muted): self.calls.append(("mute", peer, muted))
    @Slot(str, int)
    def setPeerVolume(self, peer, percent): self.calls.append(("volume", peer, percent))
    @Slot(bool)
    def setMuted(self, muted): self.calls.append(("mic", muted))
    @Slot()
    def refreshDevices(self): self.calls.append(("refresh",))
    @Slot(str, str)
    def selectDevices(self, input_id, output_id): self.calls.append(("devices", input_id, output_id))


def test_peer_volume_is_validated_persisted_and_forwarded():
    app = QGuiApplication.instance() or QGuiApplication([])
    temp = QTemporaryDir()
    settings_file = str(Path(temp.path()) / "settings.ini")
    settings = QSettings(settings_file, QSettings.Format.IniFormat)
    view = VoiceMediaView(Social(), available=True, settings=settings)
    try:
        view.activate(); view.receive(json.dumps({"source":"xodus-voice", "nonce":view.nonce, "event":"ready"}))
        view.receive(json.dumps({"source":"xodus-voice", "nonce":view.nonce, "event":"state", "state":{"connected":True,"muted":True}}))
        commands=[]; view.command.connect(commands.append)
        view.setPeerVolume("101", 165)
        assert view.peerVolumes == {"101": 165}
        assert json.loads(commands[-1])["command"] == {"type":"setPeerVolume", "xuid":"101", "percent":165}
        restored = VoiceMediaView(Social(), available=True, settings=QSettings(settings_file, QSettings.Format.IniFormat))
        assert restored.peerVolumes == {"101":165}
        restored.shutdown()
    finally: view.shutdown()


def test_party_panel_exposes_create_leave_volume_mute_devices_and_deafen():
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(APP))
    fake = FakeVoice()
    social = Social()
    social._party = {"partyId":"test-party", "members":[{"xuid":"101","gamertag":"Player"}]}
    engine.rootContext().setContextProperty("socialView", social)
    engine.rootContext().setContextProperty("voiceView", fake)
    component = QQmlComponent(engine, str(APP / "OverlayPartyPanel.qml"))
    panel = component.create()
    assert panel is not None, "\n".join(e.toString() for e in component.errors())
    panel.setProperty("socialView", social)
    panel.setProperty("voiceView", fake)
    panel.setProperty("width", 380); panel.setProperty("height", 400)
    app.processEvents()
    try:
        create = panel.findChild(QObject, "overlayPartyCreate")
        assert create is not None
        children = [o.objectName() for o in panel.findChildren(QObject)]
        assert "overlayInputDevice" in children
        assert len(panel.property("members")) == 1
        panel.setProperty("hasActiveFocus", True)
        panel.setProperty("selectedIndex", 0)
        from PySide6.QtCore import QMetaObject, Q_ARG
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "right"))
        assert fake.calls[-1] == ("volume", "101", 110)
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "accept"))
        assert fake.calls[-1] == ("mute", "101", True)
        panel.setProperty("selectedIndex", -1); panel.setProperty("selectedControl", 1)
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "accept"))
        assert fake.calls[-1] == ("mic", False)
        assert QMetaObject.invokeMethod(panel, "createOrJoin")
        assert fake.calls[-1] == ("start", "test-party")
        assert QMetaObject.invokeMethod(panel, "leaveParty")
        assert social.calls[-1] == ("leave", "test-party")
        assert QMetaObject.invokeMethod(panel, "refreshDevices")
        assert fake.calls[-1] == ("refresh",)
        assert QMetaObject.invokeMethod(panel, "chooseInput", Q_ARG("QVariant", "mic"))
        assert fake.calls[-1] == ("devices", "mic", "")
        assert QMetaObject.invokeMethod(panel, "chooseOutput", Q_ARG("QVariant", "out"))
        assert fake.calls[-1] == ("devices", "", "out")
        assert QMetaObject.invokeMethod(panel, "toggleDeafen")
        assert fake.calls[-1] == ("mute", "101", True)
        assert QMetaObject.invokeMethod(panel, "toggleDeafen")
        assert fake.calls[-1] == ("mute", "101", False)
    finally: engine.deleteLater()
