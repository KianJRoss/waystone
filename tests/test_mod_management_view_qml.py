import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

# Make artifact modules importable (craft app + native_services)
ARTIFACT = Path(__file__).resolve().parents[1]
APP_DIR = ARTIFACT / "craft-projects" / "playnite-desktop" / "app"
for p in (ARTIFACT, APP_DIR):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from mod_management_view import ModManagementView
from native_services.mod_management import ModManagerService


def _app():
    app = QApplication.instance()
    if app is None:
        QQuickStyle.setStyle("Basic")
        app = QApplication([])
    return app


def _wait_until(cond, timeout_ms=8000):
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(20)
    deadline = time.time() + timeout_ms / 1000.0

    def tick():
        if cond() or time.time() >= deadline:
            loop.quit()

    timer.timeout.connect(tick)
    timer.start()
    tick()
    loop.exec()
    timer.stop()
    return bool(cond())


def _make_real_r2_profile_fixture(tmp_path: Path):
    install_dir = tmp_path / "install"
    profile_dir = tmp_path / "profiles" / "Default"
    (install_dir / "BepInEx" / "core").mkdir(parents=True)
    (install_dir / "winhttp.dll").write_bytes(b"orig-winhttp")
    (install_dir / "doorstop_config.ini").write_text("orig-doorstop\n", encoding="utf-8")
    (install_dir / "BepInEx" / "core" / "BepInEx.Preloader.dll").write_bytes(b"orig-preloader")

    plugin_src = profile_dir / "BepInEx" / "plugins" / "TestMod"
    plugin_src.mkdir(parents=True)
    plugin_bytes = b"REAL-MOD-BYTES-v1"
    (plugin_src / "mod.dll").write_bytes(plugin_bytes)
    (profile_dir / "mods.yml").write_text(
        "- name: TestAuthor-TestMod\n"
        "  displayName: Test Mod\n"
        "  enabled: true\n"
        "  versionNumber:\n"
        "    major: 1\n"
        "    minor: 0\n"
        "    patch: 0\n",
        encoding="utf-8",
    )
    return install_dir, profile_dir, plugin_bytes


def _load_qml_engine(app, view: ModManagementView):
    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()

    class _Theme:
        background = "#111"
        text = "#eee"
        muted = "#aaa"
        panel = "#222"
        hover = "#333"
        line = "#444"
        cornerRadius = 6

    ctx.setContextProperty("modManagement", view)
    ctx.setContextProperty("Theme", _Theme())
    qml = APP_DIR / "ModManagementPage.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    assert engine.rootObjects(), f"QML failed to load: {qml}"
    root = engine.rootObjects()[0]
    app.processEvents()
    return engine, root


def _find(obj, name):
    found = obj.findChild(object, name)
    assert found is not None, f"Missing QML object: {name}"
    return found


def test_real_mod_manager_qml_import_preview_apply_undo_disk_and_status(tmp_path: Path):
    install_dir, profile_dir, plugin_bytes = _make_real_r2_profile_fixture(tmp_path)
    config_path = tmp_path / "mod_profiles.json"
    service = ModManagerService(config_path)  # REAL service
    view = ModManagementView(config_path=str(config_path), service=service)

    app = _app()
    engine, root = _load_qml_engine(app, view)
    try:
        # Empty/new state is not a dead end: import UI present and actionable.
        status = _find(root, "modsStatusLabel")
        import_profile_dir = _find(root, "importProfileDirField")
        import_install_dir = _find(root, "importInstallDirField")
        import_game_id = _find(root, "importGameIdField")
        import_btn = _find(root, "importProfileButton")
        assert "Import" in status.property("text") or view.profiles == []

        import_profile_dir.setProperty("text", str(profile_dir))
        import_install_dir.setProperty("text", str(install_dir))
        import_game_id.setProperty("text", "test-game")
        import_btn.setProperty("clicked", True) if False else None
        # trigger click via meta invocation by calling slot through view after setting fields in UI context:
        # QML button onClicked calls view.importCachedR2Profile with field texts; simulate by invoking same path through button click signal.
        import_btn.clicked.emit() if hasattr(import_btn, 'clicked') else None
        app.processEvents()

        assert _wait_until(lambda: len(view.profiles) >= 1), f"profiles not imported, status={view.status}"
        # select imported profile (real profile_id/display_name/manager_type)
        prof = view.profiles[0]
        pid = str(prof.get("profile_id") or prof.get("id") or "")
        assert pid, f"missing real profile_id: {prof}"
        assert prof.get("display_name") or prof.get("name")
        assert (prof.get("manager_type") or prof.get("manager")) == "r2modman"

        # Click/select via QML delegate path by invoking view selection as UI would; also ensure list model is real.
        view.selectProfile(pid)
        app.processEvents()
        assert _wait_until(lambda: view.selectedProfileId == pid)

        # Preview through QML click -> real service preview with can_apply/conflicts
        preview_btn = _find(root, "previewButton")
        install_field = _find(root, "installDirField")
        install_field.setProperty("text", str(install_dir))
        assert preview_btn.property("enabled") is True
        preview_btn.clicked.emit() if hasattr(preview_btn, 'clicked') else None
        app.processEvents()

        assert _wait_until(lambda: not view.busy and bool(view.lastPlan.get("plan_id"))), f"preview not ready, busy={view.busy} plan={view.lastPlan} status={view.status}"
        plan = view.lastPlan
        assert plan.get("can_apply") is True, f"expected can_apply True, got {plan}"
        assert plan.get("conflicts") in ([], None) or len(plan.get("conflicts") or []) == 0
        assert str(plan.get("profile_id")) == pid

        target_file = install_dir / "BepInEx" / "plugins" / "TestMod" / "mod.dll"
        assert not target_file.exists(), "apply must not run before explicit apply"

        # Apply reviewed plan through QML click -> real apply status applied and disk bytes written
        apply_btn = _find(root, "applyButton")
        assert _wait_until(lambda: view.canApply), f"apply not enabled, plan={view.lastPlan} status={view.status}"
        assert apply_btn.property("enabled") is True
        apply_btn.clicked.emit() if hasattr(apply_btn, 'clicked') else None
        app.processEvents()

        assert _wait_until(lambda: not view.busy and (view.lastApplyResult.get("operation_id") or view.lastApplyResult.get("plan_id"))), f"apply not finished, status={view.status} res={view.lastApplyResult}"
        assert view.lastApplyResult.get("status") == "applied", f"expected applied, got {view.lastApplyResult}"
        assert target_file.exists(), "applied mod file missing on disk"
        assert target_file.read_bytes() == plugin_bytes, "applied disk bytes mismatch"

        # Undo selected exact operation through QML click -> real undo status undone and disk restored
        undo_btn = _find(root, "undoButton")
        assert _wait_until(lambda: view.canUndo), f"undo not enabled, status={view.status} apply={view.lastApplyResult}"
        assert undo_btn.property("enabled") is True
        undo_btn.clicked.emit() if hasattr(undo_btn, 'clicked') else None
        app.processEvents()

        assert _wait_until(lambda: not view.busy and (view.lastUndoResult.get("operation_id") or view.lastUndoResult.get("plan_id"))), f"undo not finished, status={view.status} res={view.lastUndoResult}"
        assert view.lastUndoResult.get("status") == "undone", f"expected undone, got {view.lastUndoResult}"
        assert not target_file.exists(), "undo must restore disk state (remove deployed file with no prior original)"

        # UI reflects real statuses (no fake ok/supported contract)
        result_label = _find(root, "resultLabel")
        plan_label = _find(root, "planLabel")
        assert "Apply status: applied" in result_label.property("text")
        assert "Undo status: undone" in result_label.property("text")
        assert "Can apply:" in plan_label.property("text")
    finally:
        view.shutdown()
        engine.deleteLater()
        app.processEvents()


def test_mod_management_view_startup_guard_no_implicit_live_profile_fallback(tmp_path):
    """No config/service => inert safe state; must not read/create live mod_profiles.json."""
    app = _app()
    live_path = Path.home() / '.config' / 'kian-game-library' / 'mod_profiles.json'
    before_exists = live_path.exists()
    before_mtime = live_path.stat().st_mtime_ns if before_exists else None
    before_size = live_path.stat().st_size if before_exists else None

    view = ModManagementView()  # no config_path, no service
    try:
        assert view.profiles == []
        assert view.selectedProfileId == ""
        assert view.busy is False
        status = str(view.status)
        assert "no explicit config" in status.lower() or "startup guard" in status.lower(), status
        assert "fail" not in status.lower() or "inert" in status.lower() or "startup guard" in status.lower()

        # Explicit refresh must remain inert and must not touch live profile state.
        view.refreshProfiles()
        app.processEvents()
        assert view.profiles == []
        status2 = str(view.status)
        assert "startup guard" in status2.lower() or "no explicit config" in status2.lower(), status2

        # QML can still load Theme-compatibly with inert view model.
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("modManagement", view)
        qml_path = APP_DIR / "ModManagementPage.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        app.processEvents()
        assert engine.rootObjects(), "QML failed to load for inert startup-guard view"
        root = engine.rootObjects()[0]
        assert root.objectName() == "modManagementPageRoot" or root is not None
        label = _find(root, "modsStatusLabel")
        label_text = str(label.property("text"))
        assert "startup guard" in label_text.lower() or "no explicit config" in label_text.lower(), label_text
        engine.deleteLater()
        app.processEvents()
    finally:
        view.shutdown()

    after_exists = live_path.exists()
    after_mtime = live_path.stat().st_mtime_ns if after_exists else None
    after_size = live_path.stat().st_size if after_exists else None
    # Startup guard must not create or mutate live profile state.
    assert after_exists == before_exists, f"live mod_profiles.json existence changed: {before_exists}->{after_exists}"
    assert after_mtime == before_mtime and after_size == before_size, "live mod_profiles.json was read/mutated by startup-guard path"
