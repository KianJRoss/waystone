"""Automated tests for GameOverlay and ActiveSpeakerWidget in Waystone.

Verifies:
1. Steam-like In-Game Hub window properties, Theme adoption, and external toggle contract.
2. ReturnToGameRequested signal emission on B/back, Escape, and Resume action.
3. Hub navigation actions (Resume, Controllers, Library, Downloads, Settings).
4. Active speaker semantics:
   - Voice unavailable state honestly says 'Voice unavailable' (never infers speaking from membership).
   - Voice connected state accurately reflects speakingMembers list from remote XUIDs.
   - Mute/unmute peer interactions.
5. No focus traps between Hub and Party sections.
"""
import os
import sys
from pathlib import Path
import pytest
from PySide6.QtCore import QObject, Signal, Property, Slot, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest, QSignalSpy

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class FakeSocialView(QObject):
    partyStateChanged = Signal()
    friendsChanged = Signal()

    def __init__(self, party_id="party-123", members=None, friends=None, parent=None):
        super().__init__(parent)
        self._party_id = party_id
        self._members = members or [
            {"xuid": "10001", "gamertag": "PlayerOne", "role": "leader"},
            {"xuid": "10002", "gamertag": "PlayerTwo", "role": "member"},
            {"xuid": "10003", "gamertag": "PlayerThree", "role": "member"},
        ]
        self._friends = friends or [
            {"xuid": "10001", "gamertag": "PlayerOne"},
            {"xuid": "10002", "gamertag": "PlayerTwo"},
            {"xuid": "10003", "gamertag": "PlayerThree"},
        ]

    @Property("QVariantMap", notify=partyStateChanged)
    def partyState(self):
        return {
            "phase": "connected" if self._party_id else "idle",
            "partyId": self._party_id,
            "members": self._members,
            "error": ""
        }

    @Property("QVariantList", notify=friendsChanged)
    def friends(self):
        return self._friends

    def set_party(self, party_id, members=None):
        self._party_id = party_id
        if members is not None:
            self._members = members
        self.partyStateChanged.emit()


class FakeVoiceView(QObject):
    changed = Signal()

    def __init__(self, available=True, connected=True, speaking=None, muted=None, parent=None):
        super().__init__(parent)
        self._available = available
        self._connected = connected
        self._speaking_available = True
        self._speaking_members = speaking or []
        self._muted_members = muted or []
        self.mute_calls = []

    @Property(bool, notify=changed)
    def available(self):
        return self._available

    @Property(bool, notify=changed)
    def connected(self):
        return self._connected

    @Property(bool, notify=changed)
    def speakingAvailable(self):
        return self._available and self._connected and self._speaking_available

    @Property("QStringList", notify=changed)
    def speakingMembers(self):
        return self._speaking_members

    @Property("QStringList", notify=changed)
    def mutedMembers(self):
        return self._muted_members

    @Slot(str, bool)
    def mutePeer(self, xuid, mute):
        self.mute_calls.append((xuid, mute))
        if mute and xuid not in self._muted_members:
            self._muted_members.append(xuid)
        elif not mute and xuid in self._muted_members:
            self._muted_members.remove(xuid)
        self.changed.emit()

    def set_speaking(self, speaking_list):
        self._speaking_members = list(speaking_list)
        self.changed.emit()

    def set_connection(self, available, connected):
        self._available = available
        self._connected = connected
        self.changed.emit()


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance() or QGuiApplication([])
    yield app


def create_game_overlay(engine, social=None, voice=None, current_game=None):
    qml_file = APP_DIR / "GameOverlay.qml"
    component = QQmlComponent(engine, str(qml_file))
    if component.isError():
        pytest.fail(f"Component errors: {component.errors()}")
    overlay = component.create()
    assert overlay is not None

    engine._comp = component
    engine._overlay = overlay

    if social is not None:
        overlay.setProperty("socialView", social)
    if voice is not None:
        overlay.setProperty("voiceView", voice)
    if current_game is not None:
        overlay.setProperty("currentGame", current_game)
    return overlay


def test_overlay_lifecycle_and_external_contract(qapp):
    engine = QQmlApplicationEngine()
    overlay = create_game_overlay(engine)
    spy_return = QSignalSpy(overlay.returnToGameRequested)

    try:
        assert not overlay.property("visible")

        # open() shows overlay
        overlay.open()
        QTest.qWait(20)
        assert overlay.property("visible")

        # closeOverlay() hides overlay and emits returnToGameRequested
        overlay.closeOverlay()
        QTest.qWait(20)
        assert not overlay.property("visible")
        assert spy_return.count() == 1

        # toggle() alternates visibility
        overlay.toggle()
        QTest.qWait(20)
        assert overlay.property("visible")

        overlay.toggle()
        QTest.qWait(20)
        assert not overlay.property("visible")
        assert spy_return.count() == 2
    finally:
        overlay.deleteLater()
        engine.deleteLater()


def test_hub_navigation_and_signals(qapp):
    engine = QQmlApplicationEngine()
    overlay = create_game_overlay(engine)

    spy_return = QSignalSpy(overlay.returnToGameRequested)
    spy_controllers = QSignalSpy(overlay.controllersRequested)
    spy_library = QSignalSpy(overlay.libraryRequested)
    spy_downloads = QSignalSpy(overlay.downloadsRequested)
    spy_settings = QSignalSpy(overlay.settingsRequested)

    try:
        overlay.open()
        assert overlay.property("activeSection") == "hub"
        assert overlay.property("hubIndex") == 0

        # Hub 0: Resume
        assert overlay.navigate("accept") is True
        assert not overlay.property("visible")
        assert spy_return.count() == 1

        # Re-open and move to 1: Controllers
        overlay.open()
        assert overlay.navigate("right") is True
        assert overlay.property("hubIndex") == 1
        assert overlay.navigate("accept") is True
        assert spy_controllers.count() == 1
        assert not overlay.property("visible")

        # Re-open and move to 2: Library
        overlay.open()
        overlay.setProperty("hubIndex", 2)
        assert overlay.navigate("accept") is True
        assert spy_library.count() == 1
        assert not overlay.property("visible")

        # Re-open and move to 3: Downloads
        overlay.open()
        overlay.setProperty("hubIndex", 3)
        assert overlay.navigate("accept") is True
        assert spy_downloads.count() == 1
        assert not overlay.property("visible")

        # Re-open and move to 4: Settings
        overlay.open()
        overlay.setProperty("hubIndex", 4)
        assert overlay.navigate("accept") is True
        assert spy_settings.count() == 1
        assert not overlay.property("visible")
    finally:
        overlay.deleteLater()
        engine.deleteLater()


def test_active_speaker_unavailable_honesty(qapp):
    engine = QQmlApplicationEngine()
    social = FakeSocialView(party_id="party-test-1", parent=engine)
    # Voice is offline / unavailable
    voice = FakeVoiceView(available=False, connected=False, parent=engine)
    overlay = create_game_overlay(engine, social=social, voice=voice)

    try:
        speaker_widget = overlay.findChild(QObject, "overlaySpeakerWidget")
        assert speaker_widget is not None
        assert speaker_widget.property("inParty") is True
        assert speaker_widget.property("voiceConnected") is False

        # MUST honestly declare voice unavailable, NEVER infer speaking from membership
        status_for_10001 = speaker_widget.memberVoiceStatus("10001")
        assert status_for_10001 == "Voice unavailable"
        assert speaker_widget.isMemberSpeaking("10001") is False

        status_for_10002 = speaker_widget.memberVoiceStatus("10002")
        assert status_for_10002 == "Voice unavailable"
        assert speaker_widget.isMemberSpeaking("10002") is False
    finally:
        overlay.deleteLater()
        engine.deleteLater()


def test_active_speaker_connected_tracking_and_mute(qapp):
    engine = QQmlApplicationEngine()
    social = FakeSocialView(party_id="party-test-2", parent=engine)
    voice = FakeVoiceView(available=True, connected=True, speaking=["10002"], muted=["10003"], parent=engine)
    overlay = create_game_overlay(engine, social=social, voice=voice)

    try:
        speaker_widget = overlay.findChild(QObject, "overlaySpeakerWidget")
        assert speaker_widget is not None
        assert speaker_widget.property("voiceConnected") is True

        # Member 10001: Quiet
        assert speaker_widget.isMemberSpeaking("10001") is False
        assert speaker_widget.memberVoiceStatus("10001") == "Quiet"

        # Member 10002: Speaking (in speakingMembers)
        assert speaker_widget.isMemberSpeaking("10002") is True
        assert speaker_widget.memberVoiceStatus("10002") == "Speaking"

        # Member 10003: Muted (in mutedMembers)
        assert speaker_widget.isMemberMuted("10003") is True
        assert speaker_widget.memberVoiceStatus("10003") == "Muted"

        # Dynamic speaking change: 10001 starts speaking, 10002 stops
        voice.set_speaking(["10001"])
        assert speaker_widget.isMemberSpeaking("10001") is True
        assert speaker_widget.memberVoiceStatus("10001") == "Speaking"
        assert speaker_widget.isMemberSpeaking("10002") is False
        assert speaker_widget.memberVoiceStatus("10002") == "Quiet"

        # Controller interaction in party widget: mute selected
        overlay.setProperty("activeSection", "party")
        speaker_widget.setProperty("selectedIndex", 1)  # Index 1 is PlayerTwo (10002)
        assert overlay.navigate("accept") is True
        assert len(voice.mute_calls) == 1
        assert voice.mute_calls[0] == ("10002", True)
    finally:
        overlay.deleteLater()
        engine.deleteLater()


def test_no_party_state(qapp):
    engine = QQmlApplicationEngine()
    social = FakeSocialView(party_id="", members=[], parent=engine)
    voice = FakeVoiceView(available=True, connected=False, parent=engine)
    overlay = create_game_overlay(engine, social=social, voice=voice)

    try:
        speaker_widget = overlay.findChild(QObject, "overlaySpeakerWidget")
        assert speaker_widget is not None
        assert speaker_widget.property("inParty") is False
        assert speaker_widget.property("memberCount") == 0
    finally:
        overlay.deleteLater()
        engine.deleteLater()


def test_overlay_back_and_section_navigation(qapp):
    engine = QQmlApplicationEngine()
    overlay = create_game_overlay(engine)
    spy_return = QSignalSpy(overlay.returnToGameRequested)

    try:
        overlay.open()
        assert overlay.property("visible")

        # next_section switches between hub and party
        assert overlay.navigate("next_section") is True
        assert overlay.property("activeSection") == "party"

        assert overlay.navigate("next_section") is True
        assert overlay.property("activeSection") == "hub"

        # Left/Right transitions
        overlay.setProperty("hubIndex", overlay.property("hubCount") - 1)
        assert overlay.navigate("right") is True
        assert overlay.property("activeSection") == "party"

        assert overlay.navigate("left") is True
        assert overlay.property("activeSection") == "hub"

        # 'back' or 'menu' closes overlay from any section
        assert overlay.navigate("back") is True
        assert not overlay.property("visible")
        assert spy_return.count() == 1
    finally:
        overlay.deleteLater()
        engine.deleteLater()


def test_connected_party_without_activity_support_never_guesses_speaking(qapp):
    engine=QQmlApplicationEngine()
    social=FakeSocialView(party_id="fixture-party",parent=engine)
    voice=FakeVoiceView(available=True,connected=True,speaking=["10002"],parent=engine)
    voice._speaking_available=False
    overlay=create_game_overlay(engine,social=social,voice=voice)
    try:
        widget=overlay.findChild(QObject,"overlaySpeakerWidget")
        assert not widget.isMemberSpeaking("10002")
        assert widget.memberVoiceStatus("10002")=="Activity unavailable"
    finally:
        overlay.close();overlay.deleteLater();engine.deleteLater();QTest.qWait(20)
