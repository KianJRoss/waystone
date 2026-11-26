import json
import os
import stat
import sys
from pathlib import Path

import pytest

# Isolated package import for owned artifact validation
HERE = Path(__file__).resolve().parent
ARTIFACT_ROOT = HERE.parent
if str(ARTIFACT_ROOT) not in sys.path:
    sys.path.insert(0, str(ARTIFACT_ROOT))

from native_services.epic_direct_launch import (  # noqa: E402
    DirectLaunchPlan,
    EpicDirectLaunchError,
    EpicDirectLaunchNeedsRuntimeProfile,
    PreservedRuntimeSettings,
    RuntimeSelection,
    plan_epic_direct_launch,
)


def _mk_exec(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _mk_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_installed(root: Path, data) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / "installed.json"
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def _base_installed(install_dir: Path, exe_rel: str = "Game.bin", **overrides):
    entry = {
        "app_name": "AppName123",
        "title": "Original Title",
        "install_path": str(install_dir),
        "executable": exe_rel,
        "platform": "Linux",
        "is_dlc": False,
        "version": "1.0",
    }
    entry.update(overrides)
    return {"AppName123": entry}


@pytest.fixture
def engine(tmp_path: Path) -> Path:
    return _mk_exec(tmp_path / "legendary")


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    return _mk_dir(tmp_path / "cfg")


@pytest.fixture
def native_game(config_root: Path, tmp_path: Path):
    install = _mk_dir(tmp_path / "games" / "native")
    exe = _mk_exec(install / "Game.bin")
    _write_installed(config_root, _base_installed(install, "Game.bin", platform="Linux"))
    return {"install": install, "exe": exe}


@pytest.fixture
def wine_game(config_root: Path, tmp_path: Path):
    install = _mk_dir(tmp_path / "games" / "wine")
    exe = _mk_exec(install / "Game.exe")
    entry = _base_installed(install, "Game.exe", platform="Windows", app_name="WinApp", title="Windows Game")["AppName123"]
    entry["app_name"] = "WinApp"
    _write_installed(config_root, {"WinApp": entry})
    return {"install": install, "exe": exe, "installed_key": "WinApp"}


def test_exact_selection_native_plan(engine, config_root, native_game):
    plan = plan_epic_direct_launch(
        engine_path=engine,
        config_root=config_root,
        app_name="AppName123",
        runtime=RuntimeSelection(kind="native"),
    )
    assert isinstance(plan, DirectLaunchPlan)
    assert plan.argv[:3] == (str(engine), "launch", "AppName123")
    assert plan.argv[-1] == "--no-wine"
    assert plan.env == {"LEGENDARY_CONFIG_PATH": str(config_root)}
    assert plan.cwd == str(native_game["install"])
    assert plan.identity.provider == "epic"
    assert plan.identity.product == "AppName123"
    assert plan.identity.app_name == "AppName123"
    assert plan.identity.executable == str(native_game["exe"])
    assert plan.launch_intent.verified_contract is True
    assert plan.launch_intent.executable == str(engine)
    assert plan.launch_intent.argv == plan.argv
    assert any("app_name=AppName123" in e for e in plan.identity.qualified_evidence)


def test_title_rename_does_not_affect_exact_selection(engine, config_root, native_game):
    # rewrite installed.json with different title/same app_name
    data = _base_installed(native_game["install"], "Game.bin", title="Renamed Completely")
    _write_installed(config_root, data)
    plan = plan_epic_direct_launch(
        engine_path=engine,
        config_root=config_root,
        app_name="AppName123",
        runtime=RuntimeSelection(kind="native"),
    )
    assert plan.identity.app_name == "AppName123"
    assert plan.identity.title == "Renamed Completely"


def test_duplicate_app_aliases_not_confused_and_duplicate_keys_rejected(engine, config_root, native_game):
    install2 = _mk_dir(config_root.parent / "games" / "other")
    _mk_exec(install2 / "Other.bin")
    # distinct keys are fine (no aliasing by title)
    data = _base_installed(native_game["install"], "Game.bin", title="Same Title")
    data["AliasLike"] = {
        "app_name": "AliasLike",
        "title": "Same Title",
        "install_path": str(install2),
        "executable": "Other.bin",
        "platform": "Linux",
        "is_dlc": False,
    }
    _write_installed(config_root, data)
    plan = plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))
    assert plan.identity.app_name == "AppName123"

    # duplicate JSON keys must be rejected (stable snapshot requirement)
    raw = '{"AppName123":{"app_name":"AppName123","install_path":"%s","executable":"Game.bin","platform":"Linux"},"AppName123":{"app_name":"AppName123","install_path":"%s","executable":"Game.bin","platform":"Linux"}}'
    raw = raw % (str(native_game["install"]).replace('\\', '/'), str(native_game["install"]).replace('\\', '/'))
    _write_installed(config_root, raw)
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))


@pytest.mark.parametrize("bad_exe", ["../escape.bin", "/abs/Game.bin", "sub/../../escape.bin"])
def test_escaped_or_absolute_executable_rejected(engine, config_root, native_game, bad_exe):
    data = _base_installed(native_game["install"], bad_exe)
    _write_installed(config_root, data)
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))


def test_missing_executable_or_root_rejected(engine, config_root, native_game, tmp_path):
    # missing executable file
    data = _base_installed(native_game["install"], "Missing.bin")
    _write_installed(config_root, data)
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))

    # missing install root
    missing_root = tmp_path / "nope"
    data = _base_installed(missing_root, "Game.bin")
    _write_installed(config_root, data)
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))


def test_malformed_oversized_stale_symlink_metadata_rejected(engine, config_root, native_game, tmp_path):
    p = config_root / "installed.json"
    # malformed
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))

    # oversized
    huge = "{" + '"AppName123":' + json.dumps(_base_installed(native_game["install"])['AppName123']) + ',"pad":"' + ('a' * (2 * 1024 * 1024 + 10)) + '"}'
    p.write_text(huge, encoding="utf-8")
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))

    # symlink installed.json
    real = tmp_path / "real.json"
    real.write_text(json.dumps(_base_installed(native_game["install"])), encoding="utf-8")
    p.unlink()
    p.symlink_to(real)
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))

    # stale/wrong app_name in entry key/value mismatch
    p.unlink()
    _write_installed(config_root, {"Other": _base_installed(native_game["install"])})
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))


def test_windows_route_requires_explicit_wine_binary_and_prefix(engine, config_root, wine_game, tmp_path):
    # missing runtime fields -> needs-runtime-profile
    with pytest.raises(EpicDirectLaunchNeedsRuntimeProfile):
        plan_epic_direct_launch(
            engine_path=engine,
            config_root=config_root,
            app_name="WinApp",
            runtime=RuntimeSelection(kind="native"),  # windows game cannot use native --no-wine
        )

    wine_bin = _mk_exec(tmp_path / "wine" / "wine64")
    prefix = _mk_dir(tmp_path / "prefix")
    plan = plan_epic_direct_launch(
        engine_path=engine,
        config_root=config_root,
        app_name="WinApp",
        runtime=RuntimeSelection(kind="wine", wine_binary=str(wine_bin), wine_prefix=str(prefix)),
    )
    assert plan.argv[:3] == (str(engine), "launch", "WinApp")
    assert "--wine" in plan.argv and str(wine_bin) in plan.argv
    assert "--wine-prefix" in plan.argv and str(prefix) in plan.argv
    assert "--no-wine" not in plan.argv
    assert plan.env["LEGENDARY_CONFIG_PATH"] == str(config_root)
    assert plan.cwd == str(wine_game["install"])


def test_missing_runtime_paths_rejected(engine, config_root, wine_game, tmp_path):
    wine_bin = tmp_path / "missing-wine"
    prefix = tmp_path / "missing-prefix"
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(
            engine_path=engine,
            config_root=config_root,
            app_name="WinApp",
            runtime=RuntimeSelection(kind="wine", wine_binary=str(wine_bin), wine_prefix=str(tmp_path)),
        )
    # create wine bin but missing prefix dir
    _mk_exec(tmp_path / "wine-ok")
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(
            engine_path=engine,
            config_root=config_root,
            app_name="WinApp",
            runtime=RuntimeSelection(kind="wine", wine_binary=str(tmp_path / "wine-ok"), wine_prefix=str(prefix)),
        )


def test_preserved_runtime_settings_explicit_only(engine, config_root, native_game):
    preserved = PreservedRuntimeSettings(
        offline=True,
        skip_version_check=True,
        language="en",
        override_username="User",
        wrapper="/usr/bin/gamemoderun",
        extra_env=(("SDL_VIDEODRIVER", "wayland"),),
    )
    plan = plan_epic_direct_launch(
        engine_path=engine,
        config_root=config_root,
        app_name="AppName123",
        runtime=RuntimeSelection(kind="native"),
        preserved=preserved,
    )
    argv = list(plan.argv)
    assert "--offline" in argv
    assert "--skip-version-check" in argv
    assert argv[argv.index("--language") + 1] == "en"
    assert argv[argv.index("--override-username") + 1] == "User"
    assert argv[argv.index("--wrapper") + 1] == "/usr/bin/gamemoderun"
    assert plan.env["SDL_VIDEODRIVER"] == "wayland"
    assert plan.env["LEGENDARY_CONFIG_PATH"] == str(config_root)


def test_no_arbitrary_launch_args_or_network_process_calls(engine, config_root, native_game, monkeypatch):
    # ensure arbitrary metadata fields are ignored and no subprocess/network used
    data = _base_installed(native_game["install"], launch_parameters="--evil", launch_args="rm -rf /")
    _write_installed(config_root, data)

    def _boom(*a, **k):
        raise AssertionError("process/network call attempted")

    monkeypatch.setattr(os, "system", _boom)
    monkeypatch.setattr("subprocess.Popen", _boom, raising=False)
    monkeypatch.setattr("subprocess.run", _boom, raising=False)
    import socket
    monkeypatch.setattr(socket, "socket", _boom)

    plan = plan_epic_direct_launch(engine_path=engine, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))
    assert "--evil" not in plan.argv
    assert "rm -rf /" not in " ".join(plan.argv)


def test_config_root_must_be_safe(engine, tmp_path, native_game):
    # config root itself symlink rejected
    real = _mk_dir(tmp_path / "realcfg")
    _write_installed(real, _base_installed(native_game["install"]))
    link = tmp_path / "linkcfg"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=engine, config_root=link, app_name="AppName123", runtime=RuntimeSelection(kind="native"))


def test_engine_must_exist_and_be_executable(config_root, native_game, tmp_path):
    bad = tmp_path / "legendary"
    bad.write_text("x", encoding="utf-8")
    bad.chmod(0o644)
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=bad, config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))
    with pytest.raises(EpicDirectLaunchError):
        plan_epic_direct_launch(engine_path=tmp_path / "missing", config_root=config_root, app_name="AppName123", runtime=RuntimeSelection(kind="native"))


def test_proton_umu_not_invented(engine, config_root, wine_game, tmp_path):
    # unsupported/unspecified compatibility runtime cannot be passed as "proton"/umu pseudo-wine
    with pytest.raises((EpicDirectLaunchError, EpicDirectLaunchNeedsRuntimeProfile)):
        RuntimeSelection(kind="proton")  # type: ignore[arg-type]
    # wine kind without explicit valid binary+prefix must be explicit needs-runtime-profile
    with pytest.raises(EpicDirectLaunchNeedsRuntimeProfile):
        RuntimeSelection(kind="wine")
