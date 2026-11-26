import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Property, Signal, Slot, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

APP = Path(__file__).resolve().parent


class Social(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__(); self.calls=[]
        self._friends = [
            {"xuid":"202","gamertag":"Offline","online":False},
            {"xuid":"101","gamertag":"Online","online":True,"presence":"In game","currentGame":"Example Game","joinablePartyId":"join-1"},
        ]
    @Property(bool, notify=changed)
    def connected(self): return True
    @Property("QVariantList", notify=changed)
    def friends(self): return self._friends
    @Property("QVariantMap", notify=changed)
    def partyState(self): return {"partyId":"party-1","members":[]}
    @Slot(str)
    def refreshFriends(self): self.calls.append(("refresh",))
    @Slot(str, str)
    def inviteToParty(self, party, xuid): self.calls.append(("invite",party,xuid))
    @Slot(str, str)
    def sendMessage(self, xuid, text): self.calls.append(("message",xuid,text))


class Voice(QObject):
    changed = Signal()
    def __init__(self): super().__init__(); self.calls=[]
    @Property(bool, constant=True)
    def available(self): return True
    @Property(bool, notify=changed)
    def sessionActive(self): return False
    @Slot(str)
    def startParty(self, party): self.calls.append(("join",party))


def make_panel():
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine(); engine.addImportPath(str(APP))
    social, voice = Social(), Voice()
    engine.rootContext().setContextProperty("socialView", social)
    engine.rootContext().setContextProperty("voiceView", voice)
    component = QQmlComponent(engine, str(APP / "OverlayFriendsPanel.qml"))
    panel = component.create()
    assert panel is not None, "\n".join(error.toString() for error in component.errors())
    panel.setParent(engine)
    panel.setProperty("socialView", social); panel.setProperty("voiceView", voice)
    panel.setProperty("width", 360); panel.setProperty("height", 340)
    app.processEvents()
    return panel, social, voice, engine


def test_friends_sorted_online_first_and_presence_game_kept():
    panel, _, _, engine = make_panel()
    try:
        rows = panel.property("friends").toVariant()
        assert [row["gamertag"] for row in rows] == ["Online", "Offline"]
        assert rows[0]["currentGame"] == "Example Game"
    finally: engine.deleteLater()


def test_controller_actions_join_message_and_no_fake_nonprovider_actions():
    panel, social, voice, engine = make_panel()
    try:
        # Online friend actions are invite, join, message; d-pad cycles action, A runs it.
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "right"))
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "accept"))
        assert voice.calls == [("join", "join-1")]
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "right"))
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "accept"))
        panel.setProperty("quickReply", "gg")
        assert QMetaObject.invokeMethod(panel, "sendReply")
        assert social.calls == [("message", "101", "gg")]
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "left"))
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "left"))
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "accept"))
        assert social.calls[-1] == ("invite", "party-1", "101")
        # Offline rows remain visible but have no join/invite affordance without proof.
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "down"))
        assert QMetaObject.invokeMethod(panel, "navigate", Q_ARG("QVariant", "accept"))
        assert len(social.calls) == 2
    finally: engine.deleteLater()
