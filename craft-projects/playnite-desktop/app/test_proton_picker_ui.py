"""W19b: per-game Proton version picker on the game page (fakes only: tmp HOME, no Steam, no network)."""
from __future__ import annotations

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Q_ARG, Qt
from PySide6.QtGui import QGuiApplication

from app import create_session
from native_services import proton_manager as pm
from proton_view import ProtonView


def app():
    return QGuiApplication.instance() or QGuiApplication([])


def wait(predicate, seconds=3.0):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return predicate()


def add_runtimes(paths):
    for name in ("GE-Proton10-14", "GE-Proton11-11-x86_64"):
        (paths.compat / name).mkdir(parents=True, exist_ok=True)
        (paths.compat / name / "proton").write_text("")


def test_game_page_picks_a_proton_for_a_waystone_entry_and_reloads_launch_settings(tmp_path):
    application = app()
    engine, model, root, _warnings = create_session(application, headless=True,
                                                    launch_profile_root=str(tmp_path / "launch-profiles"))
    try:
        view = engine.proton_view
        add_runtimes(view._paths)
        store = engine.launch_view._profile_store
        data = {"kind": "launch-profiles-v1", "default_associations": {}, "game_associations": {
            "demo-adventure": "demo-modlist"}, "profiles": [{
                "profile_id": "demo-modlist", "display_name": "Demo modlist", "executable": "/usr/bin/python3",
                "argv_template": ["launch.py", "--proton", str(view._paths.compat / "GE-Proton10-14/proton")],
                "env": {}, "platform_or_provider": "local", "target_type": "native_game"}]}
        store.config_path.parent.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text(json.dumps(data))
        store.load()
        view.refresh()

        root.setProperty("inspectorVisible", True)
        root.setProperty("viewMode", "details")
        root.setProperty("selectedId", "demo-adventure")
        application.processEvents()
        row = root.findChild(QObject, "detailsProtonRow")
        combo = root.findChild(QObject, "detailsProtonVersion")
        assert row is not None and row.property("visible") is True
        labels = [c["label"] for c in row.property("choices").toVariant()] if hasattr(row.property("choices"), "toVariant") \
            else [c["label"] for c in row.property("choices")]
        assert labels == ["GE-Proton10-14", "GE-Proton11-11-x86_64", "Install another version…"]
        assert combo.property("currentIndex") == 0

        QMetaObject.invokeMethod(combo, "activated", Qt.DirectConnection, Q_ARG(int, 1))
        assert wait(lambda: view.gameStatus.startswith("Saved"))
        assert view.gameStatus == "Saved: the next launch uses GE-Proton11-11-x86_64."
        status = root.findChild(QObject, "detailsProtonStatus")
        assert status.property("visible") is True
        assert store.profiles["demo-modlist"].argv_template[2].endswith("GE-Proton11-11-x86_64/proton")
        assert wait(lambda: combo.property("currentIndex") == 1)

        # Install another version opens the versions dialog and keeps the current choice
        QMetaObject.invokeMethod(combo, "activated", Qt.DirectConnection, Q_ARG(int, 2))
        application.processEvents()
        assert root.findChild(QObject, "protonVersionsPanel").property("visible") is True
        assert combo.property("currentIndex") == 1

        # Other games (an emulator ROM) have no Proton row
        root.setProperty("selectedId", "demo-racing")
        application.processEvents()
        assert row.property("visible") is False
    finally:
        application.aboutToQuit.emit()
        root.close()
        root.deleteLater()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        application.processEvents()


def test_steam_change_asks_to_close_steam_then_applies(tmp_path):
    app()
    paths = pm.Paths(tmp_path)
    add_runtimes(paths)
    paths.steam_config.parent.mkdir(parents=True)
    paths.steam_config.write_text('"x" { "CompatToolMapping" {\n} }\n')
    steam = {"running": True}
    closed = []
    view = ProtonView(paths, get_latest=lambda: None, steam_running=lambda: steam["running"],
                      close_steam=lambda: closed.append(1) or steam.update(running=False))
    info = view.gameRuntime("Steam", "322330", "")
    assert info["supported"] and info["store"] == "steam" and info["current"] == ""
    view.setGameRuntime("steam", "322330", "GE-Proton10-14")
    assert wait(lambda: not view.busy and view.gameStatus != "Saving…")
    assert view.gameNeedsSteamClose and "Close Steam first" in view.gameStatus
    assert pm.steam_mapping(paths) == {}
    view.closeSteamAndApply("steam", "322330", "GE-Proton10-14")
    assert wait(lambda: view.gameStatus.startswith("Saved"))
    assert closed == [1] and pm.steam_mapping(paths) == {"322330": "GE-Proton10-14"}
    assert not view.gameNeedsSteamClose
    assert view.gameRuntime("Steam", "322330", "")["current"] == "GE-Proton10-14"


def test_versions_list_marks_installed_and_installs_the_chosen_one(tmp_path):
    app()
    paths = pm.Paths(tmp_path)
    add_runtimes(paths)
    releases = [{"tag": "GE-Proton11-14", "tarball": "t", "size": 600 * 1024**2, "sha512_url": "s"},
                {"tag": "GE-Proton11-11", "tarball": "t", "size": 1, "sha512_url": "s"}]
    installed = []
    view = ProtonView(paths, get_latest=lambda: None, get_releases=lambda: releases,
                      install=lambda release, p, **kw: installed.append(release["tag"]) or release["tag"])
    view.refresh()
    view.checkReleases()
    assert wait(lambda: len(view.releases) == 2)
    assert [(r["tag"], r["installed"], r["sizeMb"]) for r in view.releases] == [
        ("GE-Proton11-14", False, 600), ("GE-Proton11-11", True, 0)]
    view.installRelease("GE-Proton11-14")
    assert wait(lambda: installed == ["GE-Proton11-14"] and not view.busy)
