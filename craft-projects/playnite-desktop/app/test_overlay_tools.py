"""Unit and integration tests for Waystone Overlay v2 slice 3 compact Tools panels.

Offline, Qt offscreen, strictly isolated with injected fakes/mocks:
- Zero real system audio changes (mock pactl runner)
- Zero real desktop screen captures (mock capturer)
- Zero real downloads started (fake tracker)
- Zero real Ludusavi / save disk mutations (fake save management)
"""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import pytest

from PySide6.QtCore import QObject, Signal, Property, Slot, Qt, QMetaObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest

import overlay_tools
from overlay_tools import OverlayToolsBridge, TERMINAL_DOWNLOAD_PHASES
from test_desktop_overlay import qapp, create_engine_with_context, FakeSocialView, FakeVoiceView, load_desktop_overlay, call_qml

APP_DIR = Path(__file__).resolve().parent


class FakeDownloadsTracker(QObject):
    changed = Signal()

    def __init__(self, jobs=None, parent=None):
        super().__init__(parent)
        self._jobs = jobs or []
        self.cancelled_ids = []
        self.paused_ids = []
        self.resumed_ids = []

    @Property('QVariantList', notify=changed)
    def jobs(self):
        return list(self._jobs)

    @Slot(str)
    def cancel(self, job_id):
        self.cancelled_ids.append(job_id)
        for j in self._jobs:
            if j.get('id') == job_id:
                j['phase'] = 'cancelled'
        self.changed.emit()

    @Slot(str, result=bool)
    def pause(self, job_id):
        self.paused_ids.append(job_id)
        return True

    @Slot(str, result=bool)
    def resume(self, job_id):
        self.resumed_ids.append(job_id)
        return True


class FakeLaunchView(QObject):
    gameStateChanged = Signal()

    def __init__(self, selected_id="game-42", title="Cyber Adventure 2077", running=True, pid=12345, parent=None):
        super().__init__(parent)
        self.selectedId = selected_id
        self.selectedTitle = title
        self.gameState = "running" if running else "idle"
        self.lastPid = pid  # the real LaunchView property


class FakeSaveManagement(QObject):
    jobStateChanged = Signal()
    operationCompleted = Signal(str, bool, str)

    def __init__(self, available=True, parent=None):
        super().__init__(parent)
        self.toolAvailable = available
        self.toolStatus = "Ludusavi 0.22.0 ready" if available else "Ludusavi not found"
        self.isJobRunning = False
        self.backup_calls = []

    @Slot(str, str, result=bool)
    def backupGameAsync(self, game_id, title):
        self.backup_calls.append((game_id, title))
        self.isJobRunning = True
        self.jobStateChanged.emit()
        return True


# =============================================================================
# 1. Unit Tests for OverlayToolsBridge
# =============================================================================

def test_downloads_bridge_filtering_and_actions(qapp):
    jobs = [
        {"id": "1", "title": "Game A", "phase": "downloading", "percent": 45, "provider_label": "Steam"},
        {"id": "2", "title": "Game B", "phase": "completed", "percent": 100, "provider_label": "GOG"},
        {"id": "3", "title": "Game C", "phase": "queued", "percent": 0, "provider_label": "Xbox"},
    ]
    tracker = FakeDownloadsTracker(jobs, parent=qapp)
    bridge = OverlayToolsBridge(game_installs=tracker, parent=qapp)

    # Active downloads should filter out 'completed'
    active = bridge.activeDownloads
    assert len(active) == 2
    assert active[0]["id"] == "1"
    assert active[1]["id"] == "3"
    assert bridge.activeDownloadCount == 2
    assert bridge.hasActiveDownloads is True

    # Test cancel
    assert bridge.cancelDownload("1") is True
    assert tracker.cancelled_ids == ["1"]
    assert len(bridge.activeDownloads) == 1
    assert bridge.activeDownloadCount == 1

    # Test pause and resume
    assert bridge.pauseDownload("3") is True
    assert tracker.paused_ids == ["3"]
    assert bridge.resumeDownload("3") is True
    assert tracker.resumed_ids == ["3"]


def test_volume_bridge_with_mock_audio_runner(qapp, monkeypatch):
    monkeypatch.setattr(overlay_tools, "_process_tree", lambda root: {root, 12399} if root == 12345 else set())
    # Mock pactl outputs
    sinks_json = json.dumps([{
        "name": "alsa_output.pci-0000_00_1f.3.analog-stereo",
        "mute": False,
        "volume": {"front-left": {"value_percent": "78%"}, "front-right": {"value_percent": "78%"}}
    }])
    sink_inputs_json = json.dumps([
        {
            "index": 12,
            "mute": False,
            "volume": {"front-left": {"value_percent": "65%"}},
            "properties": {"application.process.id": "12399"}  # wine child of the launched game (12345)
        },
        {
            "index": 14,
            "mute": True,
            "volume": {"front-left": {"value_percent": "90%"}},
            "properties": {"application.process.id": str(os.getpid())}  # Voice PID
        }
    ])

    executed_cmds = []

    class MockProcess:
        def __init__(self, code, out):
            self.returncode = code
            self.stdout = out

    def mock_runner(args):
        executed_cmds.append(list(args))
        if args == ["pactl", "-f", "json", "list", "sinks"]:
            return MockProcess(0, sinks_json)
        if args == ["pactl", "get-default-sink"]:
            return MockProcess(0, "alsa_output.pci-0000_00_1f.3.analog-stereo")
        if args == ["pactl", "-f", "json", "list", "sink-inputs"]:
            return MockProcess(0, sink_inputs_json)
        return MockProcess(0, "")

    launch = FakeLaunchView(running=True, pid=12345, parent=qapp)
    bridge = OverlayToolsBridge(
        launch_view=launch,
        audio_runner=mock_runner,
        parent=qapp
    )

    # Master volume queried
    assert bridge.masterAvailable is True
    assert bridge.masterVolume == 78
    assert bridge.masterMuted is False

    # Game volume stream matched PID 12345
    assert bridge.gameAvailable is True
    assert bridge.gameVolume == 65
    assert bridge.gameMuted is False

    # Test setting volumes
    bridge.setMasterVolume(85)
    assert bridge.masterVolume == 85
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "85%"] in executed_cmds

    bridge.setMasterMuted(True)
    assert bridge.masterMuted is True
    assert ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"] in executed_cmds

    bridge.setGameVolume(50)
    assert bridge.gameVolume == 50
    assert ["pactl", "set-sink-input-volume", "12", "50%"] in executed_cmds


def test_volume_bridge_hides_when_pactl_unavailable(qapp):
    # Runner returning error simulates pactl missing / broken
    class FailProcess:
        returncode = -1
        stdout = ""

    bridge = OverlayToolsBridge(audio_runner=lambda args: FailProcess(), parent=qapp)
    assert bridge.masterAvailable is False
    assert bridge.gameAvailable is False
    assert bridge.chatAvailable is False


def test_screenshot_bridge_isolated(tmp_path, qapp):
    shots_dir = tmp_path / "Pictures" / "Waystone"
    captured_paths = []

    def fake_capturer(dest: Path) -> bool:
        captured_paths.append(dest)
        dest.write_text("fake_png_data")
        return True

    bridge = OverlayToolsBridge(capturer=fake_capturer, screenshots_dir=shots_dir, parent=qapp)
    assert bridge.hasScreenshot is False

    # Take screenshot
    path = bridge.takeScreenshot()
    assert path != ""
    assert Path(path).is_file()
    assert Path(path).parent == shots_dir
    assert bridge.hasScreenshot is True
    assert bridge.lastScreenshot == path
    assert bridge.lastScreenshotUrl == "file://" + path
    assert bridge.lastScreenshotTime != ""
    assert len(captured_paths) == 1


def test_saves_quick_backup_isolated(qapp):
    save_mgr = FakeSaveManagement(available=True, parent=qapp)
    launch = FakeLaunchView(selected_id="game-99", title="Super Metroid Remake", running=True, parent=qapp)
    bridge = OverlayToolsBridge(save_management=save_mgr, launch_view=launch, parent=qapp)

    assert bridge.saveBackupAvailable is True
    assert bridge.currentRunningTitle == "Super Metroid Remake"

    # Dispatch backup
    ok = bridge.backupCurrentGame()
    assert ok is True
    assert save_mgr.backup_calls == [("game-99", "Super Metroid Remake")]
    assert bridge.saveBackupRunning is True

    # Complete operation
    save_mgr.operationCompleted.emit("backup", True, "Backup successful")
    assert bridge.saveBackupRunning is False
    assert "Saved successfully" in bridge.saveBackupStatus
    assert bridge.lastBackupTime != ""


def test_saves_quick_backup_when_no_game_running(qapp):
    save_mgr = FakeSaveManagement(available=True, parent=qapp)
    launch = FakeLaunchView(running=False, parent=qapp)
    bridge = OverlayToolsBridge(save_management=save_mgr, launch_view=launch, parent=qapp)

    ok = bridge.backupCurrentGame()
    assert ok is False
    assert "No running game detected" in bridge.saveBackupStatus
    assert len(save_mgr.backup_calls) == 0


def test_performance_bridge_with_stats_reader(qapp):
    # Reader returns active telemetry
    reader_data = {"fps": 59.8, "frametime_ms": 16.7, "source": "mangohud"}
    bridge = OverlayToolsBridge(stats_reader=lambda: reader_data, parent=qapp)

    assert bridge.performanceAvailable is True
    assert bridge.fps == 59.8
    assert bridge.frametimeMs == 16.7
    assert bridge.performanceSource == "mangohud"

    # Telemetry unavailable -> hidden
    bridge._stats_reader = lambda: None
    bridge.refreshPerformance()
    assert bridge.performanceAvailable is False


# =============================================================================
# 2. QML Component Offline Render Tests
# =============================================================================

@pytest.fixture
def qml_engine(qapp):
    engine = QQmlApplicationEngine()
    engine._components = []
    yield engine
    for c in engine._components:
        c.deleteLater()


def load_component(engine, file_name, initial_properties=None):
    qml_file = APP_DIR / file_name
    component = QQmlComponent(engine, str(qml_file))
    if component.isError():
        pytest.fail(f"Error loading {file_name}: {component.errors()}")
    engine._components.append(component)
    if initial_properties:
        obj = component.createWithInitialProperties(initial_properties)
    else:
        obj = component.create()
    if obj is None:
        pytest.fail(f"Failed to create object from {file_name}: {component.errors()}")
    return obj


def test_qml_overlay_tool_downloads_panel(qml_engine, qapp):
    tracker = FakeDownloadsTracker([
        {"id": "dl-1", "title": "Stardew Valley", "percent": 75, "phase": "downloading", "provider_label": "GOG"}
    ], parent=qapp)
    bridge = OverlayToolsBridge(game_installs=tracker, parent=qapp)

    win = QQuickWindow()
    win.resize(320, 320)
    panel = load_component(qml_engine, "OverlayToolDownloads.qml", {"toolsBridge": bridge})
    panel.setParentItem(win.contentItem())
    panel.setProperty("width", 320)
    panel.setProperty("height", 320)
    win.show()
    QTest.qWait(50)
    try:
        assert panel.objectName() == "overlayToolDownloads"
        jobs_list = panel.findChild(QObject, "overlayDownloadsList")
        assert jobs_list is not None
        assert jobs_list.property("count") == 1

        # Check secondary link
        full_btn = panel.findChild(QObject, "overlayDownloadsOpenFull")
        assert full_btn is not None
        assert "Open full Downloads" in full_btn.property("text")

        # Cancel button inside delegate
        content_item = jobs_list.property("contentItem")
        cancel_btn = None
        for ch in content_item.childItems():
            cancel_btn = ch.findChild(QObject, "overlayDownloadCancelBtn")
            if cancel_btn:
                break
        assert cancel_btn is not None
        QMetaObject.invokeMethod(cancel_btn, "clicked")
        assert tracker.cancelled_ids == ["dl-1"]
    finally:
        win.close()
        panel.deleteLater()
        win.deleteLater()


def test_qml_overlay_tool_controller_panel(qml_engine, qapp):
    bridge = OverlayToolsBridge(parent=qapp)
    panel = load_component(qml_engine, "OverlayToolController.qml", {"toolsBridge": bridge})
    try:
        assert panel.objectName() == "overlayToolController"
        studio = panel.findChild(QObject, "overlayUnifiedControllerStudio")
        wizard = panel.findChild(QObject, "overlayControllerBindWizard")
        assert studio is not None
        assert wizard is not None
        assert studio.property("compact") is True
        assert wizard.property("visible") is False
        assert panel.findChild(QObject, "overlayControllerBindAll") is not None
        assert panel.findChild(QObject, "overlayControllerUndo") is not None
        assert panel.findChild(QObject, "overlayControllerRevert") is not None
        full_btn = panel.findChild(QObject, "overlayControllerOpenFull")
        assert full_btn is not None
        assert "Full page" in full_btn.property("text")
    finally:
        panel.deleteLater()


def test_qml_overlay_tool_volume_panel(qml_engine, qapp):
    class MockProcess:
        returncode = 0
        stdout = json.dumps([{
            "name": "def_sink",
            "mute": False,
            "volume": {"front-left": {"value_percent": "70%"}}
        }])

    bridge = OverlayToolsBridge(audio_runner=lambda args: MockProcess(), parent=qapp)
    panel = load_component(qml_engine, "OverlayToolVolume.qml", {"toolsBridge": bridge})
    try:
        assert panel.objectName() == "overlayToolVolume"
        master_slider = panel.findChild(QObject, "overlayMasterVolumeSlider")
        assert master_slider is not None
        assert master_slider.property("value") == 70
        mute_btn = panel.findChild(QObject, "overlayMasterMuteBtn")
        assert mute_btn is not None
        assert mute_btn.property("text") == "Mute"
    finally:
        panel.deleteLater()


def test_qml_overlay_tool_screenshot_panel(tmp_path, qml_engine, qapp):
    shots_dir = tmp_path / "Pictures" / "Waystone"
    bridge = OverlayToolsBridge(
        capturer=lambda p: p.write_text("png") and True,
        screenshots_dir=shots_dir,
        parent=qapp
    )
    panel = load_component(qml_engine, "OverlayToolScreenshot.qml", {"toolsBridge": bridge})
    try:
        assert panel.objectName() == "overlayToolScreenshot"
        btn = panel.findChild(QObject, "overlayTakeScreenshotBtn")
        assert btn is not None
        QMetaObject.invokeMethod(btn, "clicked")
        assert bridge.hasScreenshot is True

        thumb = panel.findChild(QObject, "overlayScreenshotThumbnail")
        assert thumb is not None
        assert thumb.property("visible") is True
        assert thumb.property("source").toString().startswith("file://")
    finally:
        panel.deleteLater()


def test_qml_overlay_tool_saves_panel(qml_engine, qapp):
    save_mgr = FakeSaveManagement(available=True, parent=qapp)
    launch = FakeLaunchView(selected_id="g1", title="Hollow Knight", running=True, parent=qapp)
    bridge = OverlayToolsBridge(save_management=save_mgr, launch_view=launch, parent=qapp)

    panel = load_component(qml_engine, "OverlayToolSaves.qml", {"toolsBridge": bridge})
    try:
        assert panel.objectName() == "overlayToolSaves"
        btn = panel.findChild(QObject, "overlayBackupSaveBtn")
        assert btn is not None
        assert btn.property("enabled") is True
        status_text = panel.findChild(QObject, "overlaySaveStatusText")
        assert status_text is not None
        assert status_text.property("text") == "Ready"
    finally:
        panel.deleteLater()


def test_qml_overlay_tool_performance_panel(qml_engine, qapp):
    bridge = OverlayToolsBridge(stats_reader=lambda: {"fps": 60.0, "frametime_ms": 16.6, "source": "gamescope"}, parent=qapp)
    panel = load_component(qml_engine, "OverlayToolPerformance.qml", {"toolsBridge": bridge})
    try:
        assert panel.objectName() == "overlayToolPerformance"
        fps_text = panel.findChild(QObject, "overlayFpsValue")
        ft_text = panel.findChild(QObject, "overlayFrametimeValue")
        assert fps_text is not None
        assert "60" in fps_text.property("text")
        assert ft_text is not None
        assert "16.6" in ft_text.property("text")
    finally:
        panel.deleteLater()


# =============================================================================
# 3. Integration Tests with Full DesktopOverlay
# =============================================================================

def test_desktop_overlay_tools_compact_hub_navigation(qapp):
    social = FakeSocialView(parent=qapp)
    voice = FakeVoiceView(parent=qapp)
    engine = create_engine_with_context(social, voice)

    # Attach fake overlayTools bridge
    tracker = FakeDownloadsTracker([
        {"id": "j1", "title": "Game 1", "percent": 50, "phase": "downloading", "provider_label": "Steam"}
    ], parent=qapp)
    bridge = OverlayToolsBridge(
        game_installs=tracker,
        stats_reader=lambda: {"fps": 60.0, "frametime_ms": 16.6},
        parent=qapp
    )
    engine.rootContext().setContextProperty("overlayTools", bridge)

    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        manager.setProperty("navigationWidget", 3)
        manager.selectWidget(3)
        QTest.qWait(40)

        tools = manager.findChild(QObject, "overlayToolsWindow")
        assert tools is not None
        assert tools.property("activeTool") == ""

        # Test selecting Downloads opens compact Downloads panel inside overlay
        manager.setProperty("toolIndex", 1)
        assert manager.navigate("accept") is True
        assert manager.property("editing") is True
        assert tools.property("activeTool") == "downloads"

        # Verify downloads panel is active and visible
        downloads_panel = tools.findChild(QObject, "overlayToolDownloadsPanel")
        assert downloads_panel is not None
        assert downloads_panel.property("visible") is True

        # Test B / back returns to Tools Hub Menu without closing overlay
        assert manager.navigate("back") is True
        assert manager.property("editing") is True
        assert tools.property("activeTool") == ""

        # Test selecting Volume opens compact Volume panel
        manager.setProperty("toolIndex", 4)
        assert manager.navigate("accept") is True
        assert tools.property("activeTool") == "volume"
        vol_panel = tools.findChild(QObject, "overlayToolVolumePanel")
        assert vol_panel is not None
        assert vol_panel.property("visible") is True

        # Back to hub
        assert manager.navigate("back") is True
        assert tools.property("activeTool") == ""

        # Test selecting Screenshot opens compact Screenshot panel
        manager.setProperty("toolIndex", 5)
        assert manager.navigate("accept") is True
        assert tools.property("activeTool") == "screenshot"
        shot_panel = tools.findChild(QObject, "overlayToolScreenshotPanel")
        assert shot_panel is not None
        assert shot_panel.property("visible") is True

        # Back to hub
        assert manager.navigate("back") is True
        assert tools.property("activeTool") == ""

        # Test selecting Saves opens compact Saves panel
        manager.setProperty("toolIndex", 6)
        assert manager.navigate("accept") is True
        assert tools.property("activeTool") == "saves"
        saves_panel = tools.findChild(QObject, "overlayToolSavesPanel")
        assert saves_panel is not None
        assert saves_panel.property("visible") is True

        # Back to hub
        assert manager.navigate("back") is True
        assert tools.property("activeTool") == ""

        # Test selecting Performance opens compact Performance panel
        manager.setProperty("toolIndex", 7)
        assert manager.navigate("accept") is True
        assert tools.property("activeTool") == "performance"
        perf_panel = tools.findChild(QObject, "overlayToolPerformancePanel")
        assert perf_panel is not None
        assert perf_panel.property("visible") is True

        # Back to hub
        assert manager.navigate("back") is True
        assert tools.property("activeTool") == ""

        # Pressing back on the hub closes the overlay
        assert manager.navigate("back") is True
        assert manager.property("editing") is False
    finally:
        call_qml(manager, "closeOverlay")



def test_process_tree_follows_children(tmp_path):
    proc = tmp_path / "proc"
    for pid, ppid in ((100, 1), (101, 100), (102, 101), (200, 1)):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "stat").write_text(f"{pid} (wine (x)) S {ppid} 0 0")
    assert overlay_tools._process_tree(100, proc) == {100, 101, 102}
    assert overlay_tools._process_tree(999, proc) == set()


def test_screenshot_hides_overlay_and_uses_grim(tmp_path, qapp, monkeypatch):
    calls, events = [], []

    class Done:
        def __init__(self, code=0, out=""):
            self.returncode, self.stdout = code, out

    def fake_run(args, **kw):
        calls.append(args)
        if args[:2] == ["hyprctl", "-j"]:
            return Done(0, json.dumps([{"name": "DP-2", "focused": True}, {"name": "HDMI-A-1", "focused": False}]))
        Path(args[-1]).write_bytes(b"png")
        events.append("grab")
        return Done(0)

    class Surface:
        visible = True
        def isVisible(self): return self.visible
        def hide(self): self.visible = False; events.append("hide")
        def show(self): self.visible = True; events.append("show")

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(overlay_tools.shutil, "which", lambda name: "/usr/bin/" + name)
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    bridge = OverlayToolsBridge(screenshots_dir=tmp_path, parent=qapp)
    path = bridge.takeScreenshot(Surface())
    assert path and Path(path).is_file()
    assert events == ["hide", "grab", "show"]          # the overlay is not in the shot
    assert ["grim", "-o", "DP-2", path] in calls        # focused monitor only
