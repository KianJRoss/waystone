"""W15 UI & controller integration tests for game updates in Waystone (fakes only)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt
from PySide6.QtGui import QGuiApplication

from app import create_session
from game_install_controller import GameInstallController
from native_services.game_updates import GameUpdateDetector, GameUpdateInfo
from native_services.provider_jobs import ProviderJobProgress
from provider_job_view import ProviderJobView
from types import SimpleNamespace
from test_provider_job_view import Manager as BaseManager


class Manager(BaseManager):
    def __init__(self, block=False):
        super().__init__(block=block)
        self.env = {}

    def set_tool_config(self, provider, config):
        super().set_tool_config(provider, config)
        self.env = dict(config.env_overrides)

    def create_plan(self, recipe):
        return SimpleNamespace(
            recipe=recipe,
            tool_binary=self.tool,
            destination_dir=recipe.install_root / (recipe.game_folder or recipe.product_id),
            env=dict(self.env),
        )


def app():
    return QGuiApplication.instance() or QGuiApplication([])


def wait(predicate, seconds=3.0):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return predicate()


def close_session(application, engine, root):
    if getattr(engine, "game_installs", None):
        engine.game_installs.shutdown()
    if getattr(engine, "provider_jobs", None):
        engine.provider_jobs.shutdown()
    application.aboutToQuit.emit()
    root.close()
    root.deleteLater()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def test_update_badges_and_update_buttons_desktop_and_big_screen(tmp_path):
    """Verify 'Update available' badge and Update button appear on library cards, DetailsPane, and Big Screen Game Page."""
    application = app()
    m = Manager(block=True)
    engine, model, root, warnings = create_session(application, headless=True, provider_manager=m)
    try:
        first_id = model.idAt(0)
        second_id = model.idAt(1)
        assert first_id and second_id

        heroic_bin = tmp_path / "heroic_legendary"
        heroic_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        heroic_bin.chmod(0o755)
        heroic_cfg = tmp_path / "heroic_cfg"
        heroic_cfg.mkdir(parents=True)
        install_dir = tmp_path / "Games" / "DemoAdventure"
        install_dir.mkdir(parents=True)

        engine.game_installs.setDetectedUpdates(
            [
                GameUpdateInfo(
                    provider="epic",
                    product_id=first_id,
                    title=model.titleFor(first_id),
                    scope="heroic",
                    installed_version="1.0.0",
                    available_version="1.1.0",
                    install_path=str(install_dir),
                    config_root=str(heroic_cfg),
                    binary_path=str(heroic_bin),
                    status_label="Update available",
                    can_update_in_waystone=True,
                    blocks_launch=True,
                ),
                GameUpdateInfo(
                    provider="steam",
                    product_id=second_id,
                    title=model.titleFor(second_id),
                    scope="steam",
                    status_label="Update pending in Steam",
                    can_update_in_waystone=False,
                    blocks_launch=False,
                ),
            ]
        )
        application.processEvents()

        # 1. LibraryModel roles and overview reflect the updates
        assert model.hasUpdateFor(first_id) is True
        assert model.updateLabelFor(first_id) == "Update available"
        assert model.hasUpdateFor(second_id) is True
        assert model.updateLabelFor(second_id) == "Update pending in Steam"

        # 2. Desktop DetailsPane shows 'Update available' badge and 'Update' button
        root.setProperty("selectedId", first_id)
        root.setProperty("inspectorVisible", True)
        root.setProperty("viewMode", "details")
        application.processEvents()

        details_badge = root.findChild(QObject, "detailsUpdateBadge")
        details_badge_label = root.findChild(QObject, "detailsUpdateBadgeLabel")
        details_update_btn = root.findChild(QObject, "detailsUpdateButton")
        assert details_badge is not None and details_badge.property("visible") is True
        assert details_badge_label.property("text") == "Update available"
        assert details_update_btn is not None and details_update_btn.property("visible") is True
        assert details_update_btn.property("text") == "Update"

        # 3. Selecting the Steam game shows 'Update pending in Steam' and 'Open Steam downloads'
        root.setProperty("selectedId", second_id)
        application.processEvents()
        assert details_badge.property("visible") is True
        assert details_badge_label.property("text") == "Update pending in Steam"
        assert details_update_btn.property("text") == "Open Steam downloads"

        dispatched_uris = []
        engine.provider_jobs._steam_dispatch = lambda uri: dispatched_uris.append(uri) or True
        QMetaObject.invokeMethod(details_update_btn, "clicked", Qt.DirectConnection)
        application.processEvents()
        assert dispatched_uris == ["steam://open/downloads"]

        # 4. Big Screen Game Page shows 'Update available' badge and focusable 'Update' button
        root.setProperty("selectedId", first_id)
        root.toggleConsole()
        application.processEvents()
        console = root.findChild(QObject, "consoleLibrary")
        assert console is not None
        console.openGamePage(first_id)
        application.processEvents()

        gp_badge = root.findChild(QObject, "gamePageUpdateBadge")
        gp_badge_text = root.findChild(QObject, "gamePageUpdateBadgeText")
        gp_update_btn = root.findChild(QObject, "consoleUpdateButton")
        assert gp_badge is not None and gp_badge.property("visible") is True
        assert gp_badge_text.property("text") == "Update available"
        assert gp_update_btn is not None and gp_update_btn.property("visible") is True

        # Trigger Heroic-scoped Epic update via Big Screen controller focus on gameUpdateButton
        console.setProperty("focusId", "gameUpdateButton")
        console.navigate("accept")
        assert wait(lambda: len(m.executed) == 1)

        plan = m.executed[0]
        assert plan.recipe.provider == "epic"
        assert plan.recipe.operation == "update"
        assert plan.recipe.install_root == install_dir.parent
        assert str(plan.tool_binary) == str(heroic_bin)
        assert plan.env.get("LEGENDARY_CONFIG_PATH") == str(heroic_cfg)

        # Downloads lists the updating game with operation='update'
        assert len(engine.game_installs.jobs) == 1
        assert engine.game_installs.jobs[0]["operation"] == "update"

        # Finish the update job and verify the pending update badge is cleared
        m.block = False
        engine.game_installs.cancel(engine.game_installs.jobs[0]["id"])
        assert wait(lambda: not engine.provider_jobs.busy)
        assert not warnings
    finally:
        close_session(application, engine, root)


def test_update_completion_clears_badge_and_safety_blocks_running_game_or_heroic(tmp_path):
    """Verify completed update clears badge, and safety rules refuse updating a running game or when Heroic runs."""
    application = app()
    m = Manager(block=False)
    view = ProviderJobView(m, catalog={"games": [{"id": "g1", "title": "Rocket League", "variants": [{"source": "Epic", "provider_game_id": "Sugar"}]}]})
    view._root = str(tmp_path / "games")

    heroic_running = [False]
    detector = GameUpdateDetector(
        steam_library_dirs=[],
        heroic_running_fn=lambda: heroic_running[0],
    )

    class FakeLaunchView:
        def __init__(self):
            self.activeGame = {}

    fake_launch = FakeLaunchView()
    controller = GameInstallController(
        view,
        {"games": [{"id": "g1", "title": "Rocket League", "variants": [{"source": "Epic", "provider_game_id": "Sugar"}]}]},
        tool_paths={"epic": sys.executable},
        detector=detector,
        launch_view=fake_launch,
    )
    try:
        install_dir = tmp_path / "games" / "RocketLeague"
        install_dir.mkdir(parents=True)
        controller.setDetectedUpdates(
            [
                GameUpdateInfo(
                    provider="epic",
                    product_id="Sugar",
                    title="Rocket League",
                    scope="heroic",
                    installed_version="1.0",
                    available_version="2.0",
                    install_path=str(install_dir),
                    config_root=str(tmp_path / "heroic_cfg"),
                    binary_path=sys.executable,
                    blocks_launch=True,
                )
            ]
        )
        assert controller.hasUpdate("g1") is True

        # 1. Safety: Never update a game while it runs
        fake_launch.activeGame = {"state": "running", "gameId": "g1", "targetId": "Sugar", "title": "Rocket League"}
        assert controller.updateGame("g1") is False
        assert controller.prompt.get("kind") == "error"
        assert "running" in controller.prompt.get("message", "").lower()
        assert len(m.executed) == 0
        controller.dismissPrompt()

        # 2. Safety: Never update a Heroic-installed Epic game while Heroic is running
        fake_launch.activeGame = {}
        heroic_running[0] = True
        assert controller.updateGame("g1") is False
        assert controller.prompt.get("kind") == "error"
        assert "heroic" in controller.prompt.get("message", "").lower()
        assert len(m.executed) == 0
        controller.dismissPrompt()

        # 3. Once game and Heroic are closed, update succeeds and clears pending update
        heroic_running[0] = False
        assert controller.updateGame("g1") is True
        assert wait(lambda: not view.busy and controller.jobs and controller.jobs[0]["phase"] == "completed")
        assert controller.hasUpdate("g1") is False
    finally:
        controller.shutdown()
        view.shutdown()


def test_launch_prompt_when_update_pending_mouse_and_controller(tmp_path):
    """Verify clicking Play or pressing controller A when an Epic/Heroic update is pending shows the 3 choices
    ('Update first (recommended)', 'Play anyway', 'Cancel') and 'Play anyway' applies to this launch only."""
    application = app()
    m = Manager(block=True)
    engine, model, root, warnings = create_session(application, headless=True, provider_manager=m)
    try:
        first_id = model.idAt(0)
        install_dir = tmp_path / "Games" / "Game1"
        install_dir.mkdir(parents=True)

        engine.game_installs.setDetectedUpdates(
            [
                GameUpdateInfo(
                    provider="epic",
                    product_id=first_id,
                    title=model.titleFor(first_id),
                    scope="waystone",
                    installed_version="1.0",
                    available_version="2.0",
                    install_path=str(install_dir),
                    config_root=str(tmp_path / "ws_epic"),
                    binary_path=sys.executable,
                    blocks_launch=True,
                )
            ]
        )
        root.setProperty("selectedId", first_id)
        root.setProperty("inspectorVisible", True)
        root.setProperty("viewMode", "details")
        application.processEvents()

        play_calls = []
        engine.launch_view.setTarget = lambda tid, details=None: play_calls.append(("TARGET", tid))
        engine.launch_view.play = lambda: play_calls.append(("PLAY", first_id)) or {"status": "handed_off"}

        details_pane = root.findChild(QObject, "gameDetails")
        play_btn = root.findChild(QObject, "detailsPlayButton")
        prompt_box = root.findChild(QObject, "updateLaunchPrompt")
        btn_update_first = root.findChild(QObject, "updatePromptUpdateFirstButton")
        btn_play_anyway = root.findChild(QObject, "updatePromptPlayAnywayButton")
        btn_cancel = root.findChild(QObject, "updatePromptCancelButton")

        assert details_pane is not None and play_btn is not None and prompt_box is not None
        assert btn_update_first.property("text") == "Update first (recommended)"
        assert btn_play_anyway.property("text") == "Play anyway"
        assert btn_cancel.property("text") == "Cancel"
        assert prompt_box.property("visible") is False

        # 1. Click Play -> opens prompt without launching
        QMetaObject.invokeMethod(play_btn, "clicked", Qt.DirectConnection)
        application.processEvents()
        assert prompt_box.property("visible") is True
        assert len(play_calls) == 0

        # 2. Click Cancel -> closes prompt, no launch, no update
        QMetaObject.invokeMethod(btn_cancel, "clicked", Qt.DirectConnection)
        application.processEvents()
        assert prompt_box.property("visible") is False
        assert len(play_calls) == 0
        assert len(m.executed) == 0

        # 3. Click Play again -> choose 'Play anyway' by controller navigation (Down -> A)
        QMetaObject.invokeMethod(play_btn, "clicked", Qt.DirectConnection)
        application.processEvents()
        assert prompt_box.property("visible") is True
        root.desktopNavigate("down")  # moves focus index from 0 ('Update first') to 1 ('Play anyway')
        root.desktopNavigate("accept")
        application.processEvents()
        assert prompt_box.property("visible") is False
        assert any(c[0] == "PLAY" for c in play_calls)
        assert len(m.executed) == 0

        # 4. 'Play anyway' applied to THIS launch only: clicking Play a third time asks again!
        play_calls.clear()
        QMetaObject.invokeMethod(play_btn, "clicked", Qt.DirectConnection)
        application.processEvents()
        assert prompt_box.property("visible") is True
        assert len(play_calls) == 0

        # 5. Press controller A on default option 0 ('Update first (recommended)') -> starts update, does NOT launch
        root.desktopNavigate("accept")
        assert wait(lambda: len(m.executed) == 1)
        assert prompt_box.property("visible") is False
        assert len(play_calls) == 0
        assert m.executed[0].recipe.operation == "update"

        engine.game_installs.cancel(engine.game_installs.jobs[0]["id"])
        assert wait(lambda: not engine.provider_jobs.busy)
        assert not warnings
    finally:
        close_session(application, engine, root)


def test_big_screen_launch_prompt_by_controller(tmp_path):
    """Verify Big Screen Play button opens the controller-navigable 3-choice update prompt."""
    application = app()
    m = Manager(block=True)
    engine, model, root, warnings = create_session(application, headless=True, provider_manager=m)
    try:
        first_id = model.idAt(0)
        install_dir = tmp_path / "Games" / "BigScreenGame"
        install_dir.mkdir(parents=True)

        engine.game_installs.setDetectedUpdates(
            [
                GameUpdateInfo(
                    provider="epic",
                    product_id=first_id,
                    title=model.titleFor(first_id),
                    scope="waystone",
                    installed_version="1.0",
                    available_version="2.0",
                    install_path=str(install_dir),
                    config_root=str(tmp_path / "ws_epic"),
                    binary_path=sys.executable,
                    blocks_launch=True,
                )
            ]
        )

        play_calls = []
        engine.launch_view.setTarget = lambda tid, details=None: play_calls.append(("TARGET", tid))
        engine.launch_view.play = lambda: play_calls.append(("PLAY", first_id)) or {"status": "handed_off"}

        root.setProperty("selectedId", first_id)
        root.toggleConsole()
        application.processEvents()
        console = root.findChild(QObject, "consoleLibrary")
        assert console is not None
        console.openGamePage(first_id)
        application.processEvents()

        console_prompt = root.findChild(QObject, "consoleUpdateLaunchPrompt")
        assert console_prompt is not None and console_prompt.property("visible") is False

        # Press A on gamePlayButton -> opens consoleUpdateLaunchPrompt with focus on 'updatePromptUpdateFirst'
        console.setProperty("focusId", "gamePlayButton")
        console.navigate("accept")
        application.processEvents()
        assert console_prompt.property("visible") is True
        assert console.property("focusId") == "updatePromptUpdateFirst"
        assert len(play_calls) == 0

        # Navigate Down to 'updatePromptPlayAnyway' and press A -> launches for this launch only
        console.navigate("down")
        assert console.property("focusId") == "updatePromptPlayAnyway"
        console.navigate("accept")
        application.processEvents()
        assert console_prompt.property("visible") is False
        assert any(c[0] == "PLAY" for c in play_calls)

        # Pressing A on gamePlayButton again asks again!
        play_calls.clear()
        console.setProperty("focusId", "gamePlayButton")
        console.navigate("accept")
        application.processEvents()
        assert console_prompt.property("visible") is True
        assert console.property("focusId") == "updatePromptUpdateFirst"

        # Press A on 'updatePromptUpdateFirst' -> starts update job, does not launch
        console.navigate("accept")
        assert wait(lambda: len(m.executed) == 1)
        assert console_prompt.property("visible") is False
        assert len(play_calls) == 0

        engine.game_installs.cancel(engine.game_installs.jobs[0]["id"])
        assert wait(lambda: not engine.provider_jobs.busy)
        assert not warnings
    finally:
        close_session(application, engine, root)



def test_check_updates_runs_off_the_ui_thread():
    """A slow store check (Legendary over the network) must not freeze the UI thread."""
    import threading

    app()
    release = threading.Event()
    seen_threads = []

    class SlowDetector:
        def detect_all(self, *, active_config_roots=None):
            seen_threads.append(threading.current_thread())
            release.wait(5)
            return [GameUpdateInfo(provider="epic", product_id="Sugar", title="Rocket League",
                                   scope="heroic", status_label="Update available",
                                   can_update_in_waystone=True, blocks_launch=True)]

        def is_heroic_busy(self):
            return False

    catalog = {"games": [{"id": "g1", "title": "Rocket League", "variants": [{"source": "Epic", "provider_game_id": "Sugar"}]}]}
    view = ProviderJobView(Manager(block=False), catalog=catalog)
    controller = GameInstallController(view, catalog, tool_paths={"epic": sys.executable}, detector=SlowDetector())
    started = time.monotonic()
    controller.checkUpdates()
    assert time.monotonic() - started < 0.5
    assert controller.checkingUpdates is True
    # A second request while one runs does not start another check.
    controller.checkUpdates()
    release.set()
    assert wait(lambda: controller.checkingUpdates is False)
    assert len(seen_threads) == 1 and seen_threads[0] is not threading.main_thread()
    assert controller.pendingUpdateCount == 1
    controller.shutdown()
    view.shutdown()
