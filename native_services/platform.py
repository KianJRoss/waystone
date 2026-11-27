"""Small OS boundary for Waystone's user paths, process inspection and launch scopes.

Keep platform decisions here rather than spreading Linux path/launch assumptions through
the UI.  Every helper accepts an explicit platform/environment where that makes offline
tests possible.  Windows process inspection intentionally exposes only PID, parent PID,
image name/path and creation time; it never reads arbitrary process environments.
"""
from __future__ import annotations

import os
import subprocess
import sys
import stat
from pathlib import Path, PurePath, PureWindowsPath
from typing import Callable, Iterable


def system_name(system: str | None = None) -> str:
    return (system or sys.platform).lower()


def is_linux(system: str | None = None) -> bool:
    return system_name(system) in ("linux", "linux2")


def is_windows(system: str | None = None) -> bool:
    return system_name(system) in ("nt", "windows", "win32")


def set_private_file_mode(fd: int, mode: int = 0o600, *, path: str | os.PathLike[str] | None = None) -> None:
    """Apply private-file permissions using the native API available on this OS.

    POSIX has descriptor-based chmod; Windows' stdlib exposes path-based chmod
    with limited mode-bit semantics. Windows ACL inheritance remains the access
    control boundary there, so callers must keep files under the user's app-data
    directory and must not treat POSIX mode bits as an ACL.
    """
    chmod_fd = getattr(os, "fchmod", None)
    if chmod_fd is not None:
        chmod_fd(fd, mode)
    elif path is not None:
        os.chmod(path, mode)
    # If Windows provides no fd chmod and the caller has no path, leave the
    # inherited ACL intact; secure callers create files under private app data.


def is_current_user_file(info: os.stat_result) -> bool:
    """Check ownership where the OS exposes Unix UIDs; Windows has ACLs, not st_uid."""
    getuid = getattr(os, "getuid", None)
    return getuid is None or info.st_uid == getuid()


def app_roots(*, home: str | os.PathLike[str] | None = None, environ=None,
              system: str | None = None) -> dict[str, PurePath]:
    """Return Waystone's data/config/state/cache roots using native OS conventions."""
    env = os.environ if environ is None else environ
    windows = is_windows(system)
    # On a non-Windows test host, PureWindowsPath keeps fake Windows paths meaningful.
    path_type = PureWindowsPath if windows and os.name != "nt" else Path
    if windows:
        fallback_value = str(home) if home is not None else env.get("USERPROFILE") or str(Path.home())
        fallback_path = path_type(fallback_value)
        if not fallback_path.is_absolute():
            fallback_path = path_type(str(home) if home is not None else str(Path.home()))
        fallback = str(fallback_path)
        default_appdata = path_type(fallback) / "AppData" / "Roaming"
        default_local = path_type(fallback) / "AppData" / "Local"
        appdata_value = path_type(env.get("APPDATA", "")) if env.get("APPDATA") else default_appdata
        local_value = path_type(env.get("LOCALAPPDATA", "")) if env.get("LOCALAPPDATA") else default_local
        appdata = appdata_value if appdata_value.is_absolute() else default_appdata
        local = local_value if local_value.is_absolute() else default_local
        return {
            "data": local / "Waystone",
            "config": appdata / "Waystone",
            "state": local / "Waystone" / "State",
            "cache": local / "Waystone" / "Cache",
        }

    fallback_home = Path(home).expanduser() if home is not None else Path.home()

    def root(var: str, suffix: str) -> Path:
        candidate = env.get(var, "")
        return (Path(candidate).expanduser() if candidate and os.path.isabs(candidate)
                else fallback_home / suffix)

    return {
        "data": root("XDG_DATA_HOME", ".local/share") / "waystone",
        "config": root("XDG_CONFIG_HOME", ".config") / "waystone",
        "state": root("XDG_STATE_HOME", ".local/state") / "waystone",
        "cache": root("XDG_CACHE_HOME", ".cache") / "waystone",
    }


def process_launch_options(system: str | None = None) -> dict[str, object]:
    """Detached child-process options shared by explicit game launch paths."""
    if is_windows(system):
        # CREATE_NEW_PROCESS_GROUP: lets the caller manage the game process without
        # inheriting a console control group. Avoid start_new_session (POSIX-only).
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200),
                "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def private_child_process_options(system: str | None = None) -> dict[str, object]:
    """Native subprocess options for private provider config creation.

    POSIX children can atomically inherit a restrictive umask. Windows does not
    support subprocess umask; provider configs are instead rooted in app-data ACLs.
    """
    return {} if is_windows(system) else {"umask": 0o077}


def is_executable_file(path: str | os.PathLike[str], *, system: str | None = None,
                       pathext: str | None = None) -> bool:
    """Check executable intent with native rules (Windows ignores POSIX X_OK)."""
    candidate = Path(path)
    if is_windows(system):
        extensions = pathext if pathext is not None else os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        allowed = {ext.casefold() for ext in extensions.split(";") if ext}
        return candidate.suffix.casefold() in allowed and os.access(candidate, os.R_OK)
    return os.access(candidate, os.X_OK)


def fsync_directory(path: str | os.PathLike[str], *, system: str | None = None) -> None:
    """Persist a directory entry on POSIX; Windows has no directory-fsync API."""
    if is_windows(system):
        return
    fd = os.open(Path(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class WindowsProcessReader:
    """Read-only Toolhelp process snapshot adapter implementing ProcReader's small API.

    `entries` and `details` injection is for deterministic tests. Windows cannot safely
    expose another process's environment using standard-library APIs, so targeted tags
    are unavailable there; callers can still track a launched executable and descendants.
    """

    def __init__(self, *, entries: Callable[[], Iterable[tuple[int, int, str]]] | None = None,
                 details: Callable[[int], tuple[int, str | None]] | None = None):
        self._entries = entries or self._toolhelp_entries
        self._details = details or self._process_details
        self._snapshot: dict[int, tuple[int, str]] = {}
        self._details_cache: dict[int, tuple[int, str | None]] = {}

    def list_pids(self) -> list[int]:
        try:
            self._snapshot = {int(pid): (int(parent), str(name))
                              for pid, parent, name in self._entries() if int(pid) > 0}
            self._details_cache = {}
        except (OSError, ValueError, TypeError):
            self._snapshot = {}
            self._details_cache = {}
        return sorted(self._snapshot)

    def read_stat(self, pid: int) -> tuple[int, str, int] | None:
        if pid not in self._snapshot:
            return None
        parent, name = self._snapshot[pid]
        try:
            created, exe = self._details(pid)
            self._details_cache[pid] = (created, exe)
            return parent, name, int(created)
        except (OSError, ValueError, TypeError):
            return None

    def read_exe(self, pid: int) -> str | None:
        try:
            if pid not in self._details_cache:
                self._details_cache[pid] = self._details(pid)
            return self._details_cache[pid][1]
        except (OSError, ValueError, TypeError):
            return None

    def read_cmdline(self, pid: int) -> tuple[str, ...]:
        # Image path is enough for wrapper/name filtering; do not run WMI or read
        # arbitrary command lines as part of a background tracking poll.
        exe = self.read_exe(pid)
        return (exe,) if exe else ()

    def read_cwd(self, _pid: int) -> str | None:
        return None

    def read_targeted_env(self, _pid: int) -> dict[str, str]:
        return {}

    @staticmethod
    def _toolhelp_entries() -> list[tuple[int, int, str]]:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        invalid = ctypes.c_void_p(-1).value
        handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if handle == invalid:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        rows: list[tuple[int, int, str]] = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
            while ok:
                rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
                ok = kernel.Process32NextW(handle, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(handle)
        return rows

    @staticmethod
    def _process_details(pid: int) -> tuple[int, str | None]:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                           ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                           ctypes.POINTER(wintypes.FILETIME)]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                      wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess denied")
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
            stamp = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            exe = kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            return stamp, buf.value if exe else None
        finally:
            kernel.CloseHandle(handle)
