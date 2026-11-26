"""Tests for Waystone Overlay Quick Library (Xbox-guide style game switcher).

Covers:
1. OverlayLibraryBridge Python unit tests:
   - Now Playing card detection, session playtime formatting, resume, quit confirmation, safe termination.
   - Recent games ordering (play session store + library last_activity).
   - Pinned / favorites filtering.
   - All installed games list, search query filtering by substring and acronym.
   - Game selection and game card detail fields.
   - Safe game switcher: "Switch to X? Y will be closed." (waits for Y to close before launching X).
2. QML integration tests:
   - DesktopOverlay with library window, toolbar toggle, tools action.
   - Controller navigation across sections, modals, and search.
   - Zero QML warnings.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

import pytest
from PySide6.QtCore import QObject, Signal, Property, Slot, Qt, QRectF
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QQuickItem
from PySide6.QtTest import QTest

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from overlay_library import OverlayLibraryBridge, _format_duration, _format_total_playtime, _format_last_played
from test_desktop_overlay import qapp, create_engine_with_context, FakeSocialView, FakeVoiceView, load_desktop_overlay


class FakeLibraryModel(QObject):
    layoutChanged = Signal()
    organizerChanged = Signal()

    def __init__(self, games=None, parent=None):
        super().__init__(parent)
        self.games = games or []
        self._media_urls = {}
        self._overrides = {}

    def _display_title(self, g):
        return g.get("title", "")

    def _effective_variants(self, g):
        return g.get("variants", [])

    def _urls(self, g):
        return {
            "coverUrl": "",
            "backgroundUrl": "",
            "iconUrl": "",
        }


class FakeLaunchView(QObject):
    """Fake of LaunchView's running-game API: activeGame / stopActiveGame / pollGameState."""
    changed = Signal()

    def __init__(self, running_id="", running_title="", parent=None, closes_on_stop=True, stop_ok=True):
        super().__init__(parent)
        self._active = {}
        if running_id:
            self._active = {"sessionId": "sess-1", "state": "running", "title": running_title,
                            "gameId": running_id, "targetId": running_id, "provider": "steam",
                            "launchTime": time.time() - 120, "runningSince": time.time() - 90}
        self.closes_on_stop = closes_on_stop
        self.stop_ok = stop_ok
        self.stop_calls = 0

    @Property("QVariantMap", notify=changed)
    def activeGame(self):
        return self._active

    @Slot(result=bool)
    def stopActiveGame(self):
        self.stop_calls += 1
        return self.stop_ok

    @Slot()
    def pollGameState(self):
        # The tracker notices the stopped game on a later poll.
        if self.stop_calls and self.closes_on_stop and self._active:
            self._active = {}
            self.changed.emit()


def make_sample_games():
    return [
        {
            "id": "game-elden-ring",
            "title": "Elden Ring",
            "variants": [
                {
                    "source": "Steam", "source_id": "1245620", "platform": "Linux",
                    "status": "installed", "metadata": {"playtime_seconds": 360000, "last_activity": "2026-11-23T18:00:00Z", "favorite": True}
                }
            ]
        },
        {
            "id": "game-mario-kart-wii",
            "title": "Mario Kart Wii",
            "variants": [
                {
                    "source": "Local", "source_id": "mkw", "platform": "Wii",
                    "status": "installed", "metadata": {"playtime_seconds": 7200, "last_activity": "2026-11-20T10:00:00Z", "favorite": True}
                }
            ]
        },
        {
            "id": "game-balatro",
            "title": "Balatro",
            "variants": [
                {
                    "source": "Steam", "source_id": "2379780", "platform": "Linux",
                    "status": "installed", "metadata": {"playtime_seconds": 18000, "last_activity": "2026-11-24T02:00:00Z", "favorite": False}
                }
            ]
        },
        {
            "id": "game-cyberpunk",
            "title": "Cyberpunk 2077",
            "variants": [
                {
                    "source": "Heroic", "source_id": "cp2077", "platform": "PC (Windows)",
                    "status": "not-installed", "metadata": {"playtime_seconds": 54000, "last_activity": "2025-01-01T00:00:00Z", "favorite": False}
                }
            ]
        },
    ]


# ==============================================================================
# Unit Tests for OverlayLibraryBridge
# ==============================================================================

def test_duration_and_playtime_formatting():
    assert _format_duration(0) == "0m"
    assert _format_duration(45) == "< 1m"
    assert _format_duration(60) == "1m"
    assert _format_duration(3600) == "1h 0m"
    assert _format_duration(3665) == "1h 1m"

    assert _format_total_playtime(0) == "Never played"
    assert _format_total_playtime(30) == "1 mins"
    assert _format_total_playtime(7200) == "2.0 hours"

    assert _format_last_played("") == ""
    assert _format_last_played("2020-05-10T12:00:00Z") == "May 10, 2020"


def test_now_playing_detection_and_quit(qapp):
    model = FakeLibraryModel(make_sample_games(), parent=qapp)
    launch = FakeLaunchView(running_id="game-elden-ring", running_title="Elden Ring", parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=qapp)

    assert bridge.hasRunningGame is True
    running = bridge.runningGame
    assert running["id"] == "game-elden-ring"
    assert running["title"] == "Elden Ring"
    assert running["sessionDuration"] == "1m"

    assert bridge.isQuitting is False
    bridge.requestQuit()
    assert bridge.isQuitting is True
    bridge.cancelQuit()
    assert bridge.isQuitting is False and launch.stop_calls == 0

    bridge.requestQuit()
    bridge.confirmQuit()
    assert bridge.isQuitting is False
    assert launch.stop_calls == 1
    # Nothing is marked closed by the overlay: the tracker decides when the game is gone.
    assert bridge.hasRunningGame is True
    launch.pollGameState()
    assert bridge.hasRunningGame is False


def test_recent_and_pinned_games_ordering(qapp):
    games = make_sample_games()
    model = FakeLibraryModel(games, parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, parent=qapp)

    recent = bridge.recentGames
    assert len(recent) == 4
    # Balatro was 2026-11-24, Elden Ring was 2026-11-23, MKW was 2026-11-20
    assert recent[0]["id"] == "game-balatro"
    assert recent[1]["id"] == "game-elden-ring"
    assert recent[2]["id"] == "game-mario-kart-wii"

    # Pinned games
    pinned = bridge.pinnedGames
    assert len(pinned) == 2
    pinned_ids = {p["id"] for p in pinned}
    assert pinned_ids == {"game-elden-ring", "game-mario-kart-wii"}


def test_all_installed_games_and_search_query(qapp):
    games = make_sample_games()
    model = FakeLibraryModel(games, parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, parent=qapp)

    # Installed games filter out Cyberpunk (which is not-installed)
    installed = bridge.installedGames
    assert len(installed) == 3
    assert bridge.installedCount == 3
    assert all(g["isInstalled"] for g in installed)

    # Substring search
    bridge.setSearchQuery("bal")
    assert len(bridge.searchResults) == 1
    assert bridge.searchResults[0]["title"] == "Balatro"

    # Acronym search: 'mkw' for Mario Kart Wii
    bridge.setSearchQuery("mkw")
    assert len(bridge.searchResults) == 1
    assert bridge.searchResults[0]["title"] == "Mario Kart Wii"

    # Reset search
    bridge.setSearchQuery("")
    assert len(bridge.searchResults) == 3

    # The QML list reads a list model (thousands of rows are never converted at once).
    model_rows = bridge.searchModel
    assert model_rows.rowCount() == 3 and bridge.searchResultCount == 3
    bridge.setSearchQuery("mkw")
    assert model_rows.rowCount() == 1 and bridge.searchResultId(0) == "game-mario-kart-wii"
    assert bridge.searchResultId(5) == ""


def test_game_detail_card_selection(qapp):
    games = make_sample_games()
    model = FakeLibraryModel(games, parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, parent=qapp)

    assert bridge.selectedGame == {}
    bridge.selectGame("game-balatro")
    selected = bridge.selectedGame
    assert selected["id"] == "game-balatro"
    assert selected["title"] == "Balatro"
    assert selected["platform"] == "Linux"
    assert "coverUrl" in selected

    bridge.clearSelectedGame()
    assert bridge.selectedGame == {}


def test_safe_game_switcher_waits_for_previous_game_to_close(qapp):
    model = FakeLibraryModel(make_sample_games(), parent=qapp)
    launch = FakeLaunchView(running_id="game-elden-ring", running_title="Elden Ring", parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=qapp)
    launched = []
    bridge.launchRequested.connect(launched.append)

    bridge.playGame("game-balatro")
    assert bridge.isSwitching is True and bridge.switchClosing is False
    assert bridge.switchStatus == "Switch to Balatro? Elden Ring will be closed."
    assert launched == [] and launch.stop_calls == 0

    bridge.cancelSwitch()
    assert bridge.isSwitching is False and launched == []

    bridge.playGame("game-balatro")
    bridge.confirmSwitch()
    assert launch.stop_calls == 1
    QTest.qWait(400)
    assert bridge.isSwitching is False
    assert launched == ["game-balatro"]


def test_switch_never_starts_second_game_while_first_still_runs(qapp):
    model = FakeLibraryModel(make_sample_games(), parent=qapp)
    launch = FakeLaunchView(running_id="game-elden-ring", running_title="Elden Ring", parent=qapp, closes_on_stop=False)
    now = [1000.0]
    bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=qapp, clock=lambda: now[0])
    launched, errors = [], []
    bridge.launchRequested.connect(launched.append)
    bridge.launchError.connect(errors.append)

    bridge.playGame("game-balatro")
    bridge.confirmSwitch()
    assert bridge.switchClosing is True and bridge.switchStatus == "Closing Elden Ring…"
    QTest.qWait(300)
    assert launched == [] and bridge.isSwitching is True
    now[0] += bridge.SWITCH_TIMEOUT_SECONDS + 1
    QTest.qWait(300)
    assert launched == []
    assert bridge.switchClosing is False
    assert "still running" in bridge.switchStatus and errors
    bridge.cancelSwitch()
    assert bridge.isSwitching is False


def test_switch_aborts_when_stop_cannot_be_sent(qapp):
    model = FakeLibraryModel(make_sample_games(), parent=qapp)
    launch = FakeLaunchView(running_id="game-elden-ring", running_title="Elden Ring", parent=qapp, stop_ok=False)
    bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=qapp)
    launched = []
    bridge.launchRequested.connect(launched.append)
    bridge.playGame("game-balatro")
    bridge.confirmSwitch()
    QTest.qWait(300)
    assert launched == [] and bridge.switchClosing is False and "could not" in bridge.switchStatus


def test_selecting_running_game_resumes(qapp):
    model = FakeLibraryModel(make_sample_games(), parent=qapp)
    launch = FakeLaunchView(running_id="game-elden-ring", running_title="Elden Ring", parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=qapp)
    closed, launched = [], []
    bridge.closeOverlayRequested.connect(lambda: closed.append(True))
    bridge.launchRequested.connect(launched.append)
    bridge.playGame("game-elden-ring")
    assert closed and launched == [] and bridge.isSwitching is False


def test_direct_launch_when_no_game_running(qapp):
    model = FakeLibraryModel(make_sample_games(), parent=qapp)
    launch = FakeLaunchView(parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=qapp)
    launched = []
    bridge.launchRequested.connect(launched.append)
    bridge.playGame("game-mario-kart-wii")
    assert bridge.isSwitching is False
    assert launched == ["game-mario-kart-wii"]


def test_rows_are_cached_until_library_changes(qapp):
    model = FakeLibraryModel(make_sample_games(), parent=qapp)
    calls = []
    original = model._urls
    model._urls = lambda g: (calls.append(g["id"]), original(g))[1]
    bridge = OverlayLibraryBridge(library_model=model, parent=qapp)
    for _ in range(5):
        bridge.recentGames, bridge.pinnedGames, bridge.installedGames, bridge.searchResults, bridge.installedCount
    assert len(calls) == 4
    model.organizerChanged.emit()
    QTest.qWait(400)
    bridge.installedGames
    assert len(calls) == 8


def test_launch_view_active_game_and_stop_only_touch_tracked_session(tmp_path):
    from launch_view import LaunchView
    from native_services.game_process_tracker import TrackedGameSession

    class Tracker:
        def __init__(self):
            self.sessions = {}
        def get_session(self, sid):
            return self.sessions.get(sid)

    tracker = Tracker()
    stops = []
    view = LaunchView(launch_profile_root=tmp_path / "profiles", spawn_fn=lambda *a, **k: None,
                      process_tracker=tracker, stop_fn=lambda sid, pids: stops.append((sid, pids)) or True)
    assert view.activeGame == {} and view.stopActiveGame() is False
    view.setTarget("1245620", {"canonical_game_id": "game-elden-ring", "product_id": "1245620",
                                "provider": "Steam", "title": "Elden Ring"})
    session = TrackedGameSession("s1", "1245620", "steam", "native_game", "running", launcher_pid=4242,
                                 launch_time=100.0, running_since=110.0)
    tracker.sessions["s1"] = session
    view._tracking_sessions[view._tracking_key()] = "s1"
    view._remember_session("s1")
    # Selecting another game in the Library does not hide the running one.
    view.setTarget("2379780", {"canonical_game_id": "game-balatro", "product_id": "2379780",
                                "provider": "Steam", "title": "Balatro"})
    active = view.activeGame
    assert active["title"] == "Elden Ring" and active["gameId"] == "game-elden-ring" and active["state"] == "running"
    assert view.stopActiveGame() is True
    assert stops == [("s1", [4242])]
    assert session.state == "running"  # only the tracker closes sessions
    session.state = "closed"
    assert view.activeGame == {}


def test_stop_session_processes_never_signals_waystone_itself(monkeypatch):
    import launch_view
    sent = []
    monkeypatch.setattr(launch_view.os, "kill", lambda pid, sig: sent.append(("kill", pid)))
    monkeypatch.setattr(launch_view.os, "killpg", lambda pid, sig: sent.append(("killpg", pid)))
    monkeypatch.setattr(launch_view, "_game_scope_of", lambda pid: None)
    own = os.getpid()
    assert launch_view._stop_session_processes(own, [own], {}) is False
    assert sent == []


# ==============================================================================
# QML Integration Tests with DesktopOverlay
# ==============================================================================

def test_qml_desktop_overlay_library_widget_contract(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)

    games = make_sample_games()
    model = FakeLibraryModel(games, parent=qapp)
    launch = FakeLaunchView(parent=qapp)
    bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=qapp)
    engine.rootContext().setContextProperty("overlayLibrary", bridge)

    manager = load_desktop_overlay(engine)
    try:
        manager.open()
        QTest.qWait(50)

        # 1. Overlay items exist
        library_window = manager.findChild(QQuickItem, "overlayLibraryWindow")
        assert library_window is not None
        assert library_window.property("widgetId") == "library"
        assert library_window.property("widgetTitle") == "Library"

        library_panel = manager.findChild(QQuickItem, "overlayLibraryPanel")
        assert library_panel is not None

        # 2. When no game is running, library window is opened
        assert library_window.property("shown") is True

        # 3. Toolbar button exists and toggles library
        toolbar = manager.findChild(QQuickItem, "overlayToolbarWindow")
        toggle_btn = toolbar.findChild(QObject, "overlayToggleLibrary")
        assert toggle_btn is not None

        # 4. Search field interaction
        search_field = library_panel.findChild(QObject, "overlayLibrarySearchField")
        assert search_field is not None
        search_field.setProperty("text", "Elden")
        QTest.qWait(30)
        assert bridge.searchQuery == "Elden"
        assert len(bridge.searchResults) == 1

        # 5. Controller navigation in library panel
        search_field.setProperty("text", "")
        assert library_panel.navigate("details") is True  # Y opens search with on-screen letters
        assert library_panel.property("lettersOpen") is True
        for action in ("right", "accept"):  # B
            assert library_panel.navigate(action) is True
        QTest.qWait(30)
        assert bridge.searchQuery == "b" and [g["title"] for g in bridge.searchResults] == ["Balatro"]
        for _ in range(4):
            library_panel.navigate("down")  # down to the action row
        assert library_panel.property("letterIndex") >= 36
        assert library_panel.navigate("back") is True  # B closes the letters, keeps the query
        assert library_panel.property("lettersOpen") is False and bridge.searchQuery == "b"
        assert library_panel.navigate("back") is True  # B again clears the search
        QTest.qWait(20)
        assert bridge.searchQuery == ""

        # D-pad navigate down through sections
        assert library_panel.navigate("down") is True
        assert library_panel.navigate("down") is True
        assert library_panel.navigate("accept") is True  # Selects game
        QTest.qWait(20)
        assert bridge.selectedGame != {}

        # Back closes detail card
        assert library_panel.navigate("back") is True
        assert bridge.selectedGame == {}

        # 6. Zero QML warnings
        assert engine._warnings == []
    finally:
        manager.closeOverlay()
        QTest.qWait(30)
        manager.deleteLater()
        engine.deleteLater()
        QTest.qWait(20)
