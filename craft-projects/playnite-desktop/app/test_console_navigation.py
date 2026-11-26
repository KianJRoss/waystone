"""Automated tests for Big Screen (ConsoleLibrary) navigation, spatial focus, tabs, systems, game page, guide, settings, and launch safety."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from app import create_session
from ui_control import UiControl


class _FakeRom:
    def __init__(self, **kwargs):
        self._data = dict(kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self):
        return dict(self._data)


class _FakeControllerClient:
    """Controller socket fixture: tests never connect to a real user engine."""
    revision = "c" * 64
    profile = {"stick_processing": {
        "left": {"enabled": True, "inner_deadzone": 0.08, "outer_deadzone": 1.0, "anti_deadzone": 0.0, "curve": 1.0},
        "right": {"enabled": True, "inner_deadzone": 0.08, "outer_deadzone": 1.0, "anti_deadzone": 0.0, "curve": 1.0},
    }}

    def request(self, operation, **fields):
        if operation == "status":
            neutral = {"buttons": 0, "left_x": 0, "left_y": 0, "right_x": 0, "right_y": 0,
                       "left_trigger": 0, "right_trigger": 0}
            return {"running": True, "devices": [], "live": {"schema_version": 1, "input": neutral,
                    "output": neutral, "selected_source_id": None, "extended_buttons": []}}
        if operation == "profiles":
            return {"names": ["Default", "Couch Action", "N64 Classic"], "active_profile": "Default",
                    "revision": self.revision}
        if operation == "profile_get":
            return {"name": fields.get("name", "Default"), "profile": self.profile, "revision": self.revision}
        raise AssertionError(f"Unexpected fake controller operation: {operation}")


@dataclass
class _FakeSession:
    session_id: str = "sess-1"
    state: str = "running"
    target_id: str = "game-1"
    provider_or_platform: str = "Steam"
    launch_time: float = 100.0
    running_since: float = 100.0
    launcher_pid: int = 999999
    tracked_processes: dict = None

    def __post_init__(self):
        if self.tracked_processes is None:
            self.tracked_processes = {}


@pytest.fixture(scope="module")
def console_session(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("w17_console")
    settings_file = tmp_path / "settings.json"
    prev_settings = os.environ.get("WAYSTONE_SETTINGS_PATH")
    os.environ["WAYSTONE_SETTINGS_PATH"] = str(settings_file)
    app = QGuiApplication.instance() or QGuiApplication([])
    profiles_path = tmp_path / "controller_profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_profile": "Default",
                "active_profile": "Default",
                "profiles": {
                    "Default": {},
                    "Couch Action": {},
                    "N64 Classic": {},
                },
                "game_profiles": {},
            }
        ),
        encoding="utf-8",
    )
    engine, model, root, warnings = create_session(
        app,
        headless=True,
        controller_profiles_path=profiles_path,
    )
    root.resize(1600, 900)
    root.show()
    QTest.qWait(40)
    control = UiControl(root)
    yield app, engine, model, root, control, warnings, settings_file
    root.close()
    if hasattr(engine, "voice_media"):
        engine.voice_media.shutdown()
    if hasattr(engine, "social_view"):
        engine.social_view.shutdown()
    # Console activation starts the controller read timer. Join its in-flight
    # fetch before deleting the engine/QThread parent.
    for attr in (
        "controller_live",
        "controller_studio",
        "unified_controller",
        "controller_commands",
        "controller_profile_commands",
        "controller_device_commands",
        "controller_profile_activation",
        "organizer_report",
        "mod_browser",
        "game_progress",
        "provider_jobs",
    ):
        obj = getattr(engine, attr, None)
        if obj is not None and hasattr(obj, "shutdown"):
            obj.shutdown()
    if hasattr(engine, "live_updates") and engine.live_updates is not None:
        engine.live_updates.stop()
    engine.deleteLater()
    QTest.qWait(30)
    if prev_settings is None:
        os.environ.pop("WAYSTONE_SETTINGS_PATH", None)
    else:
        os.environ["WAYSTONE_SETTINGS_PATH"] = prev_settings


def test_console_focus_recovery_on_visibility_and_toggle(console_session):
    app, engine, model, root, control, warnings, _ = console_session
    warnings.clear()
    console = root.findChild(QObject, "consoleLibrary")
    assert console is not None
    if root.property("consoleMode"):
        root.toggleConsole()
        QTest.qWait(20)
    assert not root.property("consoleMode")
    assert not console.property("visible")

    control.ShowConsole()
    QTest.qWait(40)
    assert root.property("consoleMode")
    assert console.property("visible")
    assert console.property("currentTab") == 0
    assert console.property("focusId") == "homeHeroPlay"

    # Toggle back to desktop and re-open Big Screen
    root.toggleConsole()
    QTest.qWait(30)
    assert not root.property("consoleMode")
    assert not console.property("visible")

    root.toggleConsole()
    QTest.qWait(40)
    assert root.property("consoleMode")
    assert console.property("visible")
    assert not warnings


def test_big_screen_tab_switching_and_no_desktop_dialogs(console_session):
    """Verify LB/RB (Q/E and controller actions) switch across all 6 tabs and never open desktop dialogs."""
    app, engine, model, root, control, warnings, _ = console_session
    warnings.clear()
    control.ShowConsole()
    QTest.qWait(40)

    console = root.findChild(QObject, "consoleLibrary")
    console.switchTab(0)
    console.forceActiveFocus()
    QTest.qWait(20)

    expected_tabs = ["Home", "Library", "Systems", "Friends", "Controllers", "Settings"]
    raw_tabs = console.property("tabNames")
    tab_names = raw_tabs.toVariant() if hasattr(raw_tabs, "toVariant") else list(raw_tabs)
    assert tab_names == expected_tabs

    for idx in range(1, len(expected_tabs)):
        console.navigate("next_section")
        QTest.qWait(20)
        assert console.property("currentTab") == idx
        assert console.property("subView") == ""

    # Wrap around from Settings (5) -> Home (0)
    console.navigate("next_section")
    QTest.qWait(20)
    assert console.property("currentTab") == 0

    # Keyboard Q / E shortcuts
    console.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_E)
    QTest.qWait(20)
    assert console.property("currentTab") == 1

    QTest.keyClick(root, Qt.Key_Q)
    QTest.qWait(20)
    assert console.property("currentTab") == 0

    # Pressing B on a non-Home tab returns to Home first, then pressing B on Home exits to desktop
    console.navigate("next_section")
    QTest.qWait(20)
    assert console.property("currentTab") == 1
    console.navigate("back")
    QTest.qWait(20)
    assert root.property("consoleMode")
    assert console.property("currentTab") == 0

    console.navigate("back")
    QTest.qWait(30)
    assert not root.property("consoleMode")
    assert not warnings


def _assert_spatial_walk_reaches_all(console, expected_ids):
    """Verify every focusId in expected_ids is in active focusables and reachable via D-pad moves without getting stuck."""
    ids_csv = str(console.collectFocusableIds() or "")
    active_ids = [x for x in ids_csv.split(",") if x]
    for eid in expected_ids:
        assert eid in active_ids, f"Expected {eid} in active focusables {active_ids}"

    # Verify from every item in expected_ids, at least one direction moves to another item (no dead-end traps)
    if len(expected_ids) > 1:
        for start_id in expected_ids:
            console.setProperty("focusZone", "guide" if console.property("guideOpen") else "stage")
            console.setProperty("focusId", start_id)
            moved_neighbors = set()
            for direction in ("right", "down", "left", "up"):
                console.setProperty("focusId", start_id)
                if console.moveSpatial(direction):
                    landed = console.property("focusId")
                    if landed != start_id:
                        moved_neighbors.add(landed)
            assert moved_neighbors, f"Focus got stuck at {start_id}; no D-pad direction moved to a neighbor"


def test_big_screen_spatial_focus_walk_every_view_never_stuck(console_session):
    """Automated D-pad spatial walk test across every Big Screen view proving focus never gets stuck."""
    app, engine, model, root, control, warnings, _ = console_session
    warnings.clear()
    fake_controller = _FakeControllerClient()
    engine.unified_controller._client = fake_controller
    engine.controller_live._client = fake_controller
    # Seed fake ROMs so Systems carousel and System game list have items
    engine.launch_view._rom_records = [
        _FakeRom(rom_id="rom-sm64", platform="n64", file_name="Super Mario 64 (USA).z64", file_path="/roms/n64/sm64.z64", launchable=True, emulator_name="simple64"),
        _FakeRom(rom_id="rom-oot", platform="n64", file_name="The Legend of Zelda - Ocarina of Time (USA).z64", file_path="/roms/n64/oot.z64", launchable=True, emulator_name="simple64"),
        _FakeRom(rom_id="rom-ttyd", platform="gamecube", file_name="Paper Mario - The Thousand-Year Door (USA).iso", file_path="/roms/gc/ttyd.iso", launchable=True, emulator_name="Dolphin"),
        _FakeRom(rom_id="rom-mixed", platform="nintendo_multi", file_name="Mario Kart (USA).nes", file_path="/roms/mixed/mk.nes", launchable=False, emulator_name=""),
    ]
    engine.launch_view.romsChanged.emit()

    control.ShowConsole()
    QTest.qWait(40)
    console = root.findChild(QObject, "consoleLibrary")
    console.forceActiveFocus()

    # 1. Home tab spatial walk
    console.switchTab(0)
    QTest.qWait(30)
    _assert_spatial_walk_reaches_all(
        console,
        ["homeHeroPlay", "homeHeroDetails", "homePartyCard", "homeControllerCard", "homeRecent_0", "homeSystem_0"],
    )

    # 2. Library tab chips + grid walk
    console.switchTab(1)
    QTest.qWait(30)
    assert console.property("focusZone") == "libraryGrid"
    # Move up from top row of grid into filter chips
    console.navigate("up")
    QTest.qWait(20)
    assert console.property("focusZone") == "stage"
    assert str(console.property("focusId")).startswith("libraryChip_")
    _assert_spatial_walk_reaches_all(console, ["libraryChip_0", "libraryChip_1"])
    # Move back down into grid
    console.navigate("down")
    QTest.qWait(20)
    assert console.property("focusZone") == "libraryGrid"

    # 3. Systems carousel spatial walk
    console.switchTab(2)
    QTest.qWait(30)
    _assert_spatial_walk_reaches_all(console, ["systemCard_0", "systemCard_1", "systemNeedsSortingCard"])

    # 4. System detail view (ROM list + ROM detail buttons)
    console.openSystemDetail("n64")
    QTest.qWait(30)
    assert console.property("subView") == "system"
    assert console.property("focusZone") == "systemRomList"
    console.navigate("down")
    assert console.property("systemRomIndex") == 1
    console.navigate("up")
    assert console.property("systemRomIndex") == 0
    console.navigate("right")
    QTest.qWait(20)
    assert console.property("focusZone") == "stage"
    _assert_spatial_walk_reaches_all(console, ["romPlayButton", "romProfileButton"])
    console.navigate("left")
    QTest.qWait(20)
    assert console.property("focusZone") == "systemRomList"
    console.navigate("back")
    QTest.qWait(20)
    assert console.property("subView") == ""

    # 5. Game Page spatial walk
    console.openGamePage(model.idAt(0))
    QTest.qWait(30)
    assert console.property("subView") == "game"
    _assert_spatial_walk_reaches_all(
        console,
        [
            "gamePlayButton",
            "gamePinButton",
            "gameOptionsButton",
            "gamePlaytimeRow",
            "gameLastPlayedRow",
            "gameAchievementsRow",
            "gameMonitorRow",
            "gameControllerRow",
            "gameModsRow",
        ],
    )
    console.navigate("back")
    QTest.qWait(20)
    assert console.property("subView") == ""

    # 6. Friends tab spatial walk
    console.switchTab(3)
    QTest.qWait(30)
    _assert_spatial_walk_reaches_all(
        console,
        ["partyStartRow", "partyMicRow", "partyDeafenRow", "partyRefreshRow", "friendsConnectRow"],
    )

    # 7. Controllers tab spatial walk
    console.switchTab(4)
    QTest.qWait(30)
    _assert_spatial_walk_reaches_all(
        console,
        [
            "ctrlProfileRow",
            "ctrlVirtualPadRow",
            "ctrlBindWizardRow",
            "ctrlGyroRow",
            "ctrlRumbleRow",
            "ctrlGameProfileRow",
            "ctrlStickTuningRow",
            "ctrlTestRow",
        ],
    )
    # The formerly fake deadzone cycling row now opens the shared full-page editor.
    console.setProperty("focusId", "ctrlStickTuningRow")
    console.navigate("accept")
    QTest.qWait(50)
    stick_page = console.findChild(QObject, "bigScreenStickTuningPage")
    assert stick_page is not None and stick_page.property("visible")
    stick_editor = console.findChild(QObject, "bigScreenStickStudio")
    assert stick_editor is not None and stick_editor.property("activeTab") == "Sticks"
    if os.environ.get("WAYSTONE_CAPTURE_W21") == "1":
        evidence = Path(__file__).resolve().parents[3] / "research" / "w21"
        evidence.mkdir(parents=True, exist_ok=True)
        assert root.grabWindow().save(str(evidence / "big-screen.png"))
    stick_editor.setProperty("dirty", True)
    console.navigate("back")
    discard_dialog = console.findChild(QObject, "discardStickTuningDialog")
    assert discard_dialog is not None and discard_dialog.property("visible")
    discard_dialog.accept()
    QTest.qWait(30)
    assert not console.property("stickTuningOpen")

    # 8. Settings tab spatial walk
    console.switchTab(5)
    QTest.qWait(30)
    _assert_spatial_walk_reaches_all(
        console,
        [
            "setSoundEnabledRow",
            "setSoundPackRow",
            "setSoundVolumeRow",
            "setMonitorRow",
            "setStartBigScreenRow",
            "setAssistantRow",
            "setExitDesktopRow",
        ],
    )

    # 9. Guide drawer spatial walk
    console.navigate("menu")
    QTest.qWait(30)
    assert console.property("guideOpen")
    _assert_spatial_walk_reaches_all(
        console,
        [
            "guideHomeRow",
            "guideLibraryRow",
            "guideSystemsRow",
            "guideFriendsRow",
            "guideControllersRow",
            "guideSettingsRow",
            "guideSoundPackRow",
            "guideDesktopRow",
        ],
    )
    console.navigate("back")
    QTest.qWait(20)
    assert not console.property("guideOpen")
    assert not warnings


def test_big_screen_systems_carousel_gamelist_and_rom_launch(console_session, monkeypatch):
    """Verify Systems tab lists systems from romRecords, shows needs-sorting counts, and launches a ROM without starting an emulator."""
    app, engine, model, root, control, warnings, _ = console_session
    warnings.clear()
    engine.launch_view._rom_records = [
        _FakeRom(rom_id="rom-sm64", platform="n64", file_name="Super Mario 64 (USA).z64", file_path="/roms/n64/sm64.z64", launchable=True, emulator_name="simple64"),
        _FakeRom(rom_id="rom-oot", platform="n64", file_name="The Legend of Zelda - Ocarina of Time (USA).z64", file_path="/roms/n64/oot.z64", launchable=True, emulator_name="simple64"),
        _FakeRom(rom_id="rom-mix-1", platform="nintendo_multi", file_name="Mixed 1.nes", file_path="/roms/mix/1.nes", launchable=False, emulator_name=""),
        _FakeRom(rom_id="rom-unk-1", platform="unknown", file_name="Mystery.bin", file_path="/roms/unk/m.bin", launchable=False, emulator_name=""),
    ]
    engine.launch_view.romsChanged.emit()

    selected_roms = []
    play_calls = []

    def fake_select_rom(rom_id):
        selected_roms.append(rom_id)
        engine.launch_view._target_type = "rom"
        engine.launch_view._target_id = rom_id
        engine.launch_view._selected_rom_id = rom_id

    monkeypatch.setattr(engine.launch_view, "selectRom", fake_select_rom)
    monkeypatch.setattr(engine.launch_view, "play", lambda: play_calls.append("play"))

    control.ShowConsole()
    QTest.qWait(40)
    console = root.findChild(QObject, "consoleLibrary")

    console.switchTab(2)
    QTest.qWait(30)

    bs = engine.big_screen_view
    assert len(bs.systems) == 1
    assert bs.systems[0]["id"] == "n64"
    assert bs.systems[0]["count"] == 2
    assert bs.needsSortingNintendoCount == 1
    assert bs.needsSortingUnknownCount == 1
    assert "1 Nintendo files in a mixed folder and 1 unidentified ROMs — needs sorting" in bs.needsSortingNotice

    # Press A on the N64 system card to open its game list
    console.navigate("accept")
    QTest.qWait(30)
    assert console.property("subView") == "system"
    assert console.property("currentSystemId") == "n64"
    raw_roms = console.property("currentSystemRoms")
    roms = raw_roms.toVariant() if hasattr(raw_roms, "toVariant") else list(raw_roms)
    assert len(roms) == 2
    assert roms[0]["title"] == "Super Mario 64"

    # Press A on the first ROM to launch it
    console.navigate("accept")
    QTest.qWait(30)
    assert selected_roms == ["rom-sm64"]
    assert play_calls == ["play"]
    assert not warnings


def test_big_screen_game_page_monitor_controller_profile_and_pin(console_session, monkeypatch):
    """Verify Game Page Play, Pin, per-game Monitor picker, and per-game Controller Profile picker."""
    app, engine, model, root, control, warnings, _ = console_session
    warnings.clear()
    control.ShowConsole()
    QTest.qWait(40)
    console = root.findChild(QObject, "consoleLibrary")

    game_id = model.idAt(0)
    # Seed fake monitor settings & cache on launch_view
    from native_services import game_monitor
    monkeypatch.setattr(engine.launch_view, "_monitor_settings", game_monitor.GameMonitorSettings( _ ))
    monkeypatch.setattr(
        engine.launch_view,
        "_monitor_cache",
        [
            {"name": "DP-2", "description": "Desk 27", "width": 2560, "height": 1440},
            {"name": "HDMI-A-1", "description": "Living Room TV", "width": 3840, "height": 2160},
        ],
    )

    console.openGamePage(game_id)
    QTest.qWait(30)
    assert console.property("subView") == "game"
    assert console.property("detailGameId") == game_id

    # Toggle Pin: Pin is the library Favourite, set through the organizer (fake organizer store here).
    class FakeFavourites:
        organizerEnabled = True
        def __init__(self):
            self.values = {}
            self.applied = []
        def organizerEditValues(self, gid):
            return {"tags": "[]", "favorite": self.values.get(gid, False)}
        def previewOrganizer(self, gid, tags_json, favorite):
            self.pending = (gid, favorite)
        def applyOrganizer(self, gid):
            assert self.pending[0] == gid
            self.values[gid] = self.pending[1]
            self.applied.append(gid)
        def __getattr__(self, name):          # everything else from the real model
            return getattr(model, name)
    favourites = FakeFavourites()
    monkeypatch.setattr(engine.big_screen_view, "_library_model", favourites)
    assert not engine.big_screen_view.isPinned(game_id)
    console.setProperty("focusId", "gamePinButton")
    console.navigate("accept")
    QTest.qWait(20)
    assert engine.big_screen_view.isPinned(game_id)
    assert favourites.applied == [game_id]      # written as the library Favourite, not a private list

    # Cycle per-game monitor
    console.setProperty("focusId", "gameMonitorRow")
    console.navigate("accept")
    QTest.qWait(20)
    assert engine.launch_view.gameMonitorFor(game_id) == "Desk 27"
    console.navigate("accept")
    QTest.qWait(20)
    assert engine.launch_view.gameMonitorFor(game_id) == "Living Room TV"

    # Cycle per-game controller profile
    assert engine.controller_profile_assignments.assignmentForGame(game_id) == ""
    console.setProperty("focusId", "gameControllerRow")
    console.navigate("accept")
    QTest.qWait(20)
    assert engine.controller_profile_assignments.assignmentForGame(game_id) == "Default"
    console.navigate("accept")
    QTest.qWait(20)
    assert engine.controller_profile_assignments.assignmentForGame(game_id) == "Couch Action"

    # Test Play button safety when canPlay is True
    play_targets = []
    monkeypatch.setattr(engine.launch_view, "setTarget", lambda key, rec=None: play_targets.append((key, rec)))
    monkeypatch.setattr(engine.launch_view, "play", lambda: play_targets.append(("PLAY", None)))
    console.setProperty("canPlay", True)
    console.setProperty("focusId", "gamePlayButton")
    console.navigate("accept")
    QTest.qWait(20)
    assert any(item[0] == "PLAY" for item in play_targets)
    assert not warnings


def test_big_screen_controllers_inline_bind_wizard_and_settings(console_session):
    """Verify Controllers tab inline bind wizard (no desktop dialog) and Settings tab sound & persistence controls."""
    app, engine, model, root, control, warnings, settings_file = console_session
    warnings.clear()
    control.ShowConsole()
    QTest.qWait(40)
    console = root.findChild(QObject, "consoleLibrary")

    # Controllers tab -> open inline bind wizard
    console.switchTab(4)
    QTest.qWait(30)
    console.setProperty("focusId", "ctrlBindWizardRow")
    console.navigate("accept")
    QTest.qWait(30)
    assert console.property("bindWizardOpen")
    wizard_modal = root.findChild(QObject, "bigScreenBindWizardModal")
    assert wizard_modal is not None and wizard_modal.property("visible")

    # Press B (back) to close inline bind wizard without leaving Controllers tab
    console.navigate("back")
    QTest.qWait(20)
    assert not console.property("bindWizardOpen")
    assert console.property("currentTab") == 4

    # Cycle default profile, virtual pad type, gyro, rumble, and deadzones
    console.setProperty("focusId", "ctrlProfileRow")
    console.navigate("accept")
    assert engine.big_screen_view.activeProfileName == "Couch Action"

    # Pad type belongs to the controller profile; with no engine running nothing is faked
    console.setProperty("focusId", "ctrlVirtualPadRow")
    console.navigate("accept")
    assert engine.big_screen_view.virtualPadLabel.startswith("Default type")

    console.setProperty("focusId", "ctrlGyroRow")
    console.navigate("accept")
    assert engine.big_screen_view.gyroLabel == "On · gyro aim"

    console.setProperty("focusId", "ctrlRumbleRow")
    console.navigate("accept")
    assert engine.big_screen_view.rumbleLabel == "Off"

    # Settings tab -> test sound pack cycling, volume, and Start in Big Screen persistence
    console.switchTab(5)
    QTest.qWait(30)
    initial_pack = engine.ui_sounds.pack
    console.setProperty("focusId", "setSoundPackRow")
    console.navigate("accept")
    QTest.qWait(20)
    assert engine.ui_sounds.pack != initial_pack

    initial_vol = engine.ui_sounds.volume
    console.setProperty("focusId", "setSoundVolumeRow")
    console.navigate("left")
    QTest.qWait(20)
    assert engine.ui_sounds.volume == max(0, initial_vol - 10)

    assert not engine.big_screen_view.startInBigScreen
    console.setProperty("focusId", "setStartBigScreenRow")
    console.navigate("accept")
    QTest.qWait(20)
    assert engine.big_screen_view.startInBigScreen
    saved_data = json.loads(engine.big_screen_view._settings_path.read_text(encoding="utf-8"))
    assert saved_data["ui"]["start_in_big_screen"] is True
    assert not warnings


def test_big_screen_guide_running_game_resume_and_quit(console_session, monkeypatch):
    """Verify Guide drawer shows running game first when active, supports Resume and Quit, and quick-jumps tabs."""
    app, engine, model, root, control, warnings, _ = console_session
    warnings.clear()
    control.ShowConsole()
    QTest.qWait(40)
    console = root.findChild(QObject, "consoleLibrary")

    stopped_calls = []
    fake_sess = _FakeSession(session_id="sess-elden", state="running", target_id=model.idAt(0))
    monkeypatch.setattr(engine.launch_view, "_active_session", lambda: fake_sess)
    monkeypatch.setitem(engine.launch_view._session_info, "sess-elden", {"title": "Elden Ring", "gameId": model.idAt(0)})
    monkeypatch.setattr(engine.launch_view, "stopActiveGame", lambda: (stopped_calls.append("stopped"), True)[1])
    engine.launch_view.changed.emit()
    QTest.qWait(20)

    # Open Guide with Menu action
    console.navigate("menu")
    QTest.qWait(30)
    assert console.property("guideOpen")
    assert console.property("hasActiveGame")
    assert console.property("activeGameTitle") == "Elden Ring"
    # Running game Resume row is focused first
    assert console.property("focusId") == "guideResumeGameRow"

    # Move down to Quit running game row and press A
    console.setProperty("focusId", "guideQuitGameRow")
    console.navigate("accept")
    QTest.qWait(20)
    assert stopped_calls == ["stopped"]
    assert not console.property("guideOpen")
    # Verify heroUrl and logoUrl roles exist on libraryModel for ribbon + logo artwork
    role_names = {bytes(v).decode() for v in model.roleNames().values()}
    assert "heroUrl" in role_names
    assert "logoUrl" in role_names
    ov = model.overview(model.idAt(0))
    assert "heroUrl" in ov and "logoUrl" in ov
    assert not warnings
