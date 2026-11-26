"""Tests for DesktopOverlay and OverlayWidgetWindow (offline, Qt offscreen)."""
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal, Property, Slot, Qt, QMetaObject, Q_ARG, QPoint, QPointF, QRectF
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QQuickItem
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class FakeSocialView(QObject):
    partyStateChanged = Signal()
    friendsChanged = Signal()

    def __init__(self, party_id="party-123", members=None, friends=None, parent=None):
        super().__init__(parent)
        self.calls = []
        self._party_id = party_id
        self._members = members or [
            {"xuid": "10001", "gamertag": "PlayerOne", "role": "leader"},
            {"xuid": "10002", "gamertag": "PlayerTwo", "role": "member"},
        ]
        self._friends = friends or [
            {"xuid": "10001", "gamertag": "PlayerOne", "online": True, "presence": "Playing a game", "currentGame": "Example Game", "joinablePartyId": "party-join-1"},
            {"xuid": "10002", "gamertag": "PlayerTwo"},
            {"xuid": "10003", "gamertag": "PlayerThree"},
        ]

    @Slot()
    def connectAccount(self): self.calls.append("connect")
    @Slot()
    def createParty(self): self.calls.append("create")
    @Slot()
    def leaveParty(self): self.calls.append("leave")
    @Property(bool, notify=partyStateChanged)
    def connected(self): return True
    @Slot(str, str)
    def inviteToParty(self, party, xuid): self.calls.append(("invite", party, xuid))
    @Slot(str, str)
    def sendMessage(self, xuid, text): self.calls.append(("message", xuid, text))
    @Slot()
    def refreshFriends(self): self.calls.append("refresh_friends")
    @Slot()
    def refreshParty(self): self.calls.append("refresh_party")

    @Property("QVariantMap", notify=partyStateChanged)
    def partyState(self):
        return {
            "phase": "connected" if self._party_id else "idle",
            "partyId": self._party_id,
            "members": self._members,
            "error": "",
        }

    @Property("QVariantList", notify=friendsChanged)
    def friends(self):
        return self._friends


class FakeVoiceView(QObject):
    changed = Signal()

    def __init__(self, available=True, connected=True, parent=None):
        super().__init__(parent)
        self.calls = []
        self._available = available
        self._connected = connected
        self._muted = True

    @Slot()
    def connectVoice(self): self.calls.append("connect_voice")
    @Slot(bool)
    def setMuted(self, value): self.calls.append("mute")
    @Slot(str)
    def startParty(self, party): self.calls.append(("start", party))
    @Slot()
    def leave(self): self.calls.append("leave")
    @Slot(str, bool)
    def mutePeer(self, peer, muted): self.calls.append(("peer_mute", peer, muted))
    @Slot(str, int)
    def setPeerVolume(self, peer, percent): self.calls.append(("peer_volume", peer, percent))
    @Slot()
    def refreshDevices(self): self.calls.append("refresh_devices")
    @Slot(str, str)
    def selectDevices(self, input_id, output_id): self.calls.append(("select_devices", input_id, output_id))

    @Property(bool, notify=changed)
    def available(self):
        return self._available

    @Property(bool, notify=changed)
    def connected(self):
        return self._connected

    @Property(bool, notify=changed)
    def sessionActive(self): return True
    @Property(bool, notify=changed)
    def muted(self): return self._muted
    @Property(str, notify=changed)
    def status(self): return "Connected · microphone muted"
    @Property("QVariantMap", notify=changed)
    def peerVolumes(self): return {"10001":100,"10002":100}
    @Property("QVariantList", notify=changed)
    def audioInputs(self): return [{"id":"mock-mic","label":"USB mic"}]
    @Property("QVariantList", notify=changed)
    def audioOutputs(self): return [{"id":"mock-output","label":"Headset"}]
    @Property(str, notify=changed)
    def inputId(self): return ""
    @Property(str, notify=changed)
    def outputId(self): return ""

    @Property("QVariantList", notify=changed)
    def speakingMembers(self):
        return []

    @Property("QVariantList", notify=changed)
    def mutedMembers(self):
        return []

    @Property(bool, notify=changed)
    def speakingAvailable(self):
        return True


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance() or QGuiApplication([])
    yield app


def create_engine_with_context(social, voice):
    engine = QQmlApplicationEngine()
    engine._warnings = []
    engine.warnings.connect(lambda items: engine._warnings.extend(x.toString() for x in items))
    engine.rootContext().setContextProperty("socialView", social)
    engine.rootContext().setContextProperty("voiceView", voice)
    return engine


def load_desktop_overlay(engine):
    qml_file = APP_DIR / "DesktopOverlay.qml"
    component = QQmlComponent(engine, str(qml_file))
    if component.isError():
        pytest.fail(f"Component errors: {component.errors()}")
    if not hasattr(engine, "_components"): engine._components = []
    engine._components.append(component)
    obj = component.create()
    if obj is None:
        pytest.fail("Failed to create DesktopOverlay instance")
    obj.setParent(engine)
    obj.setProperty("socialView", engine.rootContext().contextProperty("socialView"))
    obj.setProperty("voiceView", engine.rootContext().contextProperty("voiceView"))
    return obj


def call_qml(obj, method, *args):
    if not args:
        return QMetaObject.invokeMethod(obj, method)
    # simple single arg support
    return QMetaObject.invokeMethod(obj, method, Q_ARG(type(args[0]), args[0]))


def test_distinct_widget_items_share_one_native_surface(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        QTest.qWait(50)
        toolbar = manager.findChild(QObject, "overlayToolbarWindow")
        party = manager.findChild(QObject, "overlayPartyWindow")
        friends = manager.findChild(QObject, "overlayFriendsWindow")
        tools = manager.findChild(QObject, "overlayToolsWindow")
        assert toolbar is not None
        assert party is not None
        assert friends is not None
        assert tools is not None
        assert toolbar != party
        assert party != friends
        assert friends != tools
        assert toolbar.objectName() == "overlayToolbarWindow"
        assert party.objectName() == "overlayPartyWindow"
        assert friends.objectName() == "overlayFriendsWindow"
        assert tools.objectName() == "overlayToolsWindow"
        surface = manager.findChild(QQuickWindow, "overlaySurfaceWindow")
        assert surface is not None
        assert manager.findChildren(QQuickWindow) == [surface]
        for item in (toolbar, party, friends, tools):
            assert isinstance(item, QQuickItem)
            assert item.parentItem() == surface.contentItem()
            assert item.window() == surface
    finally:
        call_qml(manager, "closeOverlay")


def test_controller_studio_expands_in_overlay_and_restores_tool_geometry(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        tools = manager.findChild(QObject, "overlayToolsWindow")
        assert tools is not None
        before = (tools.property("x"), tools.property("y"), tools.property("width"), tools.property("height"))
        manager.toolAction(2, False)
        QTest.qWait(30)
        assert tools.property("activeTool") == "controllers"
        assert tools.property("width") > before[2]
        assert tools.property("height") > before[3]
        panel = manager.findChild(QObject, "overlayToolControllerPanel")
        assert panel is not None
        assert panel.findChild(QObject, "overlayUnifiedControllerStudio") is not None
        tools.closeTool()
        assert tools.property("activeTool") == ""
        after = (tools.property("x"), tools.property("y"), tools.property("width"), tools.property("height"))
        assert after == before
    finally:
        call_qml(manager, "closeOverlay")
        QTest.qWait(30)
        manager.deleteLater()
        engine.deleteLater()
        QTest.qWait(20)


def test_pin_persists_after_close_overlay(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        QTest.qWait(50)
        party = manager.findChild(QObject, "overlayPartyWindow")
        assert party is not None
        party.setProperty("pinned", True)
        party.setProperty("shown", True)
        QTest.qWait(20)
        assert party.property("pinned") is True
        call_qml(manager, "closeOverlay")
        QTest.qWait(50)
        assert party.property("pinned") is True
        assert party.property("visible") is True
    finally:
        try:
            call_qml(manager, "closeOverlay")
        except Exception:
            pass
        QTest.qWait(30)
        manager.deleteLater()
        engine.deleteLater()
        QTest.qWait(20)


def test_closing_one_leaves_others(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        QTest.qWait(50)
        party = manager.findChild(QObject, "overlayPartyWindow")
        friends = manager.findChild(QObject, "overlayFriendsWindow")
        assert party is not None
        assert friends is not None
        friends.setProperty("pinned", False)
        friends.setProperty("shown", False)
        friends.setProperty("visible", False)
        QTest.qWait(20)
        assert friends.property("shown") is False
        assert party.property("shown") is True
    finally:
        call_qml(manager, "closeOverlay")
        QTest.qWait(30)
        manager.deleteLater()
        engine.deleteLater()
        QTest.qWait(20)


def test_independent_xy_width(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        QTest.qWait(50)
        party = manager.findChild(QObject, "overlayPartyWindow")
        friends = manager.findChild(QObject, "overlayFriendsWindow")
        assert party is not None
        assert friends is not None
        party.setProperty("x", 10)
        party.setProperty("y", 20)
        party.setProperty("width", 400)
        friends.setProperty("x", 500)
        friends.setProperty("y", 600)
        friends.setProperty("width", 300)
        QTest.qWait(20)
        assert int(party.property("x")) == 10
        assert int(party.property("y")) == 20
        assert int(party.property("width")) == 400
        assert int(friends.property("x")) == 500
        assert int(friends.property("y")) == 600
        assert int(friends.property("width")) == 300
    finally:
        call_qml(manager, "closeOverlay")
        QTest.qWait(30)
        manager.deleteLater()
        engine.deleteLater()
        QTest.qWait(20)


def test_no_services_invoked_on_construction_open(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        QTest.qWait(30)
        call_qml(manager, "toggle")
        QTest.qWait(30)
        call_qml(manager, "toggle")
        QTest.qWait(30)
        assert manager.property("editing") is True
        assert social.calls == [] and voice.calls == []
        assert engine._warnings == [], engine._warnings
    finally:
        try:
            call_qml(manager, "closeOverlay")
        except Exception:
            pass
        QTest.qWait(20)
        manager.deleteLater()
        engine.deleteLater()
        QTest.qWait(20)


def test_navigate_and_back_closure_signals(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    try:
        from PySide6.QtTest import QSignalSpy
        spy_back = QSignalSpy(manager.returnToGameRequested)
        call_qml(manager, "open")
        QTest.qWait(30)
        QMetaObject.invokeMethod(manager, "navigate", Q_ARG(str, "down"))
        assert manager.navigate("back") is True
        QTest.qWait(30)
        assert not manager.property("editing")
        assert spy_back.count() == 1
    finally:
        try:
            call_qml(manager, "closeOverlay")
        except Exception:
            pass
        QTest.qWait(20)
        manager.deleteLater()
        engine.deleteLater()
        QTest.qWait(20)


def test_editing_drag_and_resize_handles_change_only_their_own_item(qapp):
    engine = create_engine_with_context(FakeSocialView(parent=qapp), FakeVoiceView(parent=qapp))
    manager = load_desktop_overlay(engine)
    manager.setProperty("boundsOverride", QRectF(0, 0, 1200, 900))
    party = None
    try:
        manager.open(); QTest.qWait(30)
        surface = manager.findChild(QQuickWindow, "overlaySurfaceWindow")
        party = manager.findChild(QQuickItem, "overlayPartyWindow")
        friends = manager.findChild(QQuickItem, "overlayFriendsWindow")
        party.setProperty("pinned", True)
        QTest.qWait(30)
        original = (party.x(), party.y(), party.width(), party.height())
        other = (friends.x(), friends.y(), friends.width(), friends.height())
        surface_geometry = (surface.x(), surface.y(), surface.width(), surface.height())
        start = party.mapToScene(QPointF(30, 15)).toPoint()
        end = start + QPoint(35, 25)
        QTest.mousePress(surface, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(surface, end)
        QTest.mouseRelease(surface, Qt.LeftButton, Qt.NoModifier, end)
        area = party.property("layoutBounds")
        def snapped(value, low, high):
            value = max(low, min(high, value))
            if abs(value-low) <= 18: return low
            if abs(value-high) <= 18: return high
            return max(low, min(high, low + round((value-low)/16)*16))
        assert (party.x(), party.y()) == (snapped(original[0]+35, area.x(), area.right()-party.width()),
                                        snapped(original[1]+25, area.y(), area.bottom()-party.height()))
        assert (party.width(), party.height()) == original[2:]
        w,h=party.width(),party.height()
        start = party.mapToScene(QPointF(w-8, h-8)).toPoint()
        end = start + QPoint(20, 15)
        QTest.mousePress(surface, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(surface, end)
        QTest.mouseRelease(surface, Qt.LeftButton, Qt.NoModifier, end)
        assert (party.width(),party.height()) == (round((w+20)/16)*16,round((h+15)/16)*16)
        assert (friends.x(),friends.y(),friends.width(),friends.height()) == other
        assert (surface.x(),surface.y(),surface.width(),surface.height()) == surface_geometry
        assert engine._warnings == [], engine._warnings
    finally:
        if party is not None: party.setProperty("shown",False)
        manager.deleteLater();engine.deleteLater();QTest.qWait(20)
