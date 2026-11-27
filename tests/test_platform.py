from pathlib import Path, PureWindowsPath
import errno
import tempfile

import pytest

from native_services.platform import (WindowsProcessReader, app_roots, is_linux, is_windows,
                                      process_launch_options, private_child_process_options,
                                      is_executable_file)
from native_services import file_locking
from native_services import proton_manager, xodus_install
from native_services import platform as platform_module
from native_services.assistant_animation_settings import AssistantAnimationSettings
from native_services.launcher_owner import Hyprland, atomic_status_write


def test_native_platform_flags_and_detached_launch_options():
    assert is_linux("linux") and not is_windows("linux")
    assert is_windows("nt") and not is_linux("nt")
    assert not is_linux("darwin")
    assert process_launch_options("linux") == {"start_new_session": True, "close_fds": True}
    options = process_launch_options("windows")
    assert options["creationflags"] == 0x00000200
    assert options["close_fds"] is True
    assert "start_new_session" not in options
    assert private_child_process_options("windows") == {}
    assert private_child_process_options("linux") == {"umask": 0o077}


def test_executable_check_uses_windows_PATHEXT_not_posix_X_OK(tmp_path):
    plain = tmp_path / "engine"
    exe = tmp_path / "engine.EXE"
    plain.write_text("fixture")
    exe.write_text("fixture")
    assert not is_executable_file(plain, system="windows")
    assert is_executable_file(exe, system="windows", pathext=".EXE;.BAT")


def test_windows_roots_use_appdata_without_touching_real_user_home():
    roots = app_roots(system="windows", home=r"C:\Users\Fixture", environ={
        "APPDATA": r"C:\Users\Fixture\Roaming",
        "LOCALAPPDATA": r"C:\Users\Fixture\Local",
    })
    assert isinstance(roots["data"], PureWindowsPath)
    assert str(roots["data"]) == r"C:\Users\Fixture\Local\Waystone"
    assert str(roots["config"]) == r"C:\Users\Fixture\Roaming\Waystone"
    assert str(roots["state"]) == r"C:\Users\Fixture\Local\Waystone\State"
    assert str(roots["cache"]) == r"C:\Users\Fixture\Local\Waystone\Cache"


def test_windows_roots_have_standard_userprofile_fallbacks():
    roots = app_roots(system="windows", environ={"USERPROFILE": r"D:\Home\Kian"})
    assert str(roots["data"]) == r"D:\Home\Kian\AppData\Local\Waystone"
    assert str(roots["config"]) == r"D:\Home\Kian\AppData\Roaming\Waystone"


def test_windows_relative_appdata_overrides_are_ignored():
    roots = app_roots(system="windows", environ={"USERPROFILE": r"D:\Home\Kian",
                                                   "APPDATA": "relative-roaming",
                                                   "LOCALAPPDATA": "relative-local"})
    assert str(roots["data"]) == r"D:\Home\Kian\AppData\Local\Waystone"
    assert str(roots["config"]) == r"D:\Home\Kian\AppData\Roaming\Waystone"


def test_windows_relative_userprofile_is_not_used_as_root():
    roots = app_roots(system="windows", home=r"D:\Fixture", environ={"USERPROFILE": "relative-home"})
    assert str(roots["data"]) == r"D:\Fixture\AppData\Local\Waystone"


def test_windows_process_reader_tracks_creation_identity_and_never_reads_environment():
    rows = [(40, 4, "game.exe"), (4, 1, "launcher.exe")]
    reader = WindowsProcessReader(entries=lambda: rows,
                                  details=lambda pid: (1000 + pid, fr"C:\Games\{pid}.exe"))
    assert reader.list_pids() == [4, 40]
    assert reader.read_stat(40) == (4, "game.exe", 1040)
    assert reader.read_exe(40) == r"C:\Games\40.exe"
    assert reader.read_cmdline(40) == (r"C:\Games\40.exe",)
    assert reader.read_targeted_env(40) == {}
    assert reader.read_cwd(40) is None
    assert reader.read_stat(999) is None


@pytest.mark.windows_api_probe
def test_windows_api_probe_exercises_missing_posix_attributes(monkeypatch, tmp_path):
    """Windows has no procfs; Linux-only inventory checks must degrade safely."""
    original = Path.iterdir
    def no_proc(self):
        if str(self) == "/proc":
            raise FileNotFoundError("no procfs")
        return original(self)
    monkeypatch.setattr(Path, "iterdir", no_proc)
    is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: False if str(self) == "/proc" else is_dir(self))
    assert list(proton_manager._procs()) == []
    installer = xodus_install.XodusInstaller("/not-used/xodus")
    assert installer._external_busy(Path("/tmp/game")) is False

    # This call must take the documented ACL-inheritance fallback, not assume
    # descriptor chmod or a Unix UID API exists in Windows' os module.
    target = tmp_path / 'private.json'
    target.write_text('{}')
    with target.open('r+b') as stream:
        platform_module.set_private_file_mode(stream.fileno(), 0o600, path=target)
    assert platform_module.is_current_user_file(target.stat())
    platform_module.fsync_directory(target.parent, system='windows')
    settings = AssistantAnimationSettings(target.parent / 'settings.json')
    assert settings.setPace(1.5)
    status = target.parent / 'status.json'
    atomic_status_write(status, {'state': 'running'})
    assert status.read_text() == '{"state":"running"}'
    hypr = Hyprland(enabled=True, environ={'XDG_RUNTIME_DIR': str(tmp_path),
                                           'HYPRLAND_INSTANCE_SIGNATURE': 'fixture'})
    assert hypr.watch_new_windows(lambda *_: None) is None
    from overlay_tools import OverlayToolsBridge
    overlay = OverlayToolsBridge(stats_reader=None)
    overlay.refreshPerformance()
    assert overlay.performanceAvailable is False


def test_portable_file_lock_maps_windows_shared_to_safe_exclusive_and_nonblocking():
    calls = []
    def adapter(_fd, action):
        calls.append(action)
        if action == "try_lock":
            raise OSError(errno.EACCES, "locked")
    with tempfile.TemporaryFile() as stream:
        fd = stream.fileno()
        file_locking.flock(fd, file_locking.LOCK_SH, system="windows", windows_adapter=adapter)
        try:
            file_locking.flock(fd, file_locking.LOCK_EX | file_locking.LOCK_NB,
                               system="windows", windows_adapter=adapter)
        except BlockingIOError:
            pass
        else:
            raise AssertionError("Windows nonblocking collision must be reported as BlockingIOError")
        file_locking.flock(fd, file_locking.LOCK_UN, system="windows", windows_adapter=adapter)
    assert calls == ["lock", "try_lock", "unlock"]


@pytest.mark.linux_only
def test_posix_file_lock_keeps_flock_contract():
    with tempfile.TemporaryFile() as stream:
        file_locking.flock(stream.fileno(), file_locking.LOCK_EX)
        file_locking.flock(stream.fileno(), file_locking.LOCK_UN)
