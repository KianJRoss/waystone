"""Shared, injectable primitives for keeping external launcher UI under Waystone control.

This module deliberately contains no launch policy and never starts, stops, or kills a
process.  Callers supply process/window snapshots; the only live adapter is Hyprland,
which can be disabled or replaced by a fake in tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Iterable
from urllib.parse import parse_qs, urlsplit

HIDDEN_WORKSPACE = "special:waystone-hidden"


def build_owned_launch_intent(intent, *, provider: str, game_id: str, status_file: str | Path,
                              game_executable: str | None = None, game_folder: str | None = None):
    """Wrap only reviewed Steam/Heroic URI handoffs; direct Waystone backend paths stay untouched.

    Returns a dataclass-compatible replacement or None when the profile is not a supported URI.
    The URI remains a JSON argument vector; no shell parsing is involved.
    """
    from dataclasses import replace
    import json

    provider = str(provider).casefold()
    expected = "steam" if provider == "steam" else "heroic" if provider.startswith("heroic-") else ""
    if not expected or not getattr(intent, "argv", ()):
        return None
    uri = next((arg for arg in intent.argv if isinstance(arg, str)
                and arg.startswith(("steam://", "heroic://"))), None)
    if not uri:
        return None
    parsed = urlsplit(uri)
    if expected == "steam":
        match = re.fullmatch(r"[0-9]+", parsed.path.lstrip("/"))
        if (parsed.scheme != "steam" or parsed.netloc not in {"rungameid", "run"}
                or not match or match.group(0) != str(game_id)):
            return None
    else:
        query = parse_qs(parsed.query)
        app_name = query.get("appName", [""])[0]
        runner = query.get("runner", [""])[0].casefold()
        if (parsed.scheme != "heroic" or parsed.netloc != "launch" or app_name != str(game_id)
                or runner not in {"legendary", "gog", "nile", "sideload"}):
            return None

    args = [arg for arg in intent.argv[1:]]
    runtime = Path(__file__).resolve().with_name("launcher_owner_runtime.py")
    argv = [sys.executable, str(runtime),
            "--provider", expected, "--launcher", str(intent.executable),
            "--launch-args-json", json.dumps(args, ensure_ascii=False, separators=(",", ":")),
            "--game-id", str(game_id), "--status-file", str(status_file)]
    if game_executable:
        argv.extend(("--game-executable", str(game_executable)))
    if game_folder:
        argv.extend(("--game-folder", str(game_folder)))
    return replace(intent, executable=sys.executable, argv=tuple(argv),
                   error=None, verified_contract=bool(intent.verified_contract))


def heroic_install_info(app_name: str, *, home: str | Path | None = None) -> dict:
    """Install folder (and Epic executable) of a game Heroic installed, from Heroic's own install lists:
    Legendary (Epic) `legendaryConfig/legendary/installed.json`, GOG `gog_store/installed.json`, Amazon
    `nile_config/nile/installed.json`; native and Flatpak Heroic. {} when Heroic does not know the game."""
    base = Path(home).expanduser() if home else Path.home()
    roots = [base / ".config/heroic", base / ".var/app/com.heroicgameslauncher.hgl/config/heroic"]
    for root in roots:
        if re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', str(app_name)):
            try:
                from native_services.game_installs import _read_json_bounded
                sideload = _read_json_bounded(root / 'sideload_apps/library.json')
                games = sideload.get('games', []) if isinstance(sideload, dict) else []
                for game in games if isinstance(games, list) else []:
                    if (not isinstance(game, dict) or game.get('runner') != 'sideload'
                            or game.get('app_name') != str(app_name) or game.get('is_installed') is not True):
                        continue
                    folder = game.get('folder_name')
                    install = game.get('install') if isinstance(game.get('install'), dict) else {}
                    folder_path = Path(folder) if isinstance(folder, str) else Path()
                    if not folder_path.is_absolute() or not folder_path.is_dir():
                        continue
                    executable = install.get('executable')
                    exe_path = Path(executable) if isinstance(executable, str) and executable else Path()
                    if executable and not exe_path.is_absolute():
                        exe_path = folder_path / exe_path
                    return {'install_path': str(folder_path),
                            'executable': str(exe_path) if executable and exe_path.is_absolute() and exe_path.is_file() else ''}
            except (OSError, ValueError, TypeError):
                pass
        try:
            epic = json.loads((root / "legendaryConfig/legendary/installed.json").read_text(encoding="utf-8"))
            entry = epic.get(app_name) if isinstance(epic, dict) else None
            if isinstance(entry, dict) and entry.get("install_path"):
                exe = entry.get("executable") or ""
                return {"install_path": str(entry["install_path"]),
                        "executable": str(Path(entry["install_path"]) / exe) if exe else ""}
        except (OSError, ValueError):
            pass
        try:
            gog = json.loads((root / "gog_store/installed.json").read_text(encoding="utf-8"))
            for entry in (gog.get("installed") or []) if isinstance(gog, dict) else []:
                if isinstance(entry, dict) and entry.get("appName") == app_name and entry.get("install_path"):
                    return {"install_path": str(entry["install_path"]), "executable": ""}
        except (OSError, ValueError):
            pass
        try:
            nile = json.loads((root / "nile_config/nile/installed.json").read_text(encoding="utf-8"))
            for entry in nile if isinstance(nile, list) else []:
                if isinstance(entry, dict) and entry.get("id") == app_name and entry.get("path"):
                    return {"install_path": str(entry["path"]), "executable": ""}
        except (OSError, ValueError):
            pass
    return {}


def process_snapshot(proc_root: str | Path = "/proc") -> dict[int, tuple[int, str]]:
    """Return pid -> (ppid, decoded cmdline), ignoring processes that disappear mid-scan."""
    root = Path(proc_root)
    result: dict[int, tuple[int, str]] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return result
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            ppid = int(stat[stat.rindex(")") + 2 :].split()[1])
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except (OSError, ValueError, IndexError):
            continue
        result[int(entry.name)] = (ppid, cmd)
    return result


def process_tree(root_pid: int, table: dict[int, tuple[int, str]]) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in table:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, ()))
    return seen


def pids_with_env(name: str, value: str, pids: Iterable[int], proc_root: str | Path = "/proc") -> set[int]:
    """Match one explicitly named environment variable; never read/store arbitrary env data."""
    if not name or "=" in name or "\0" in name or "\0" in value:
        return set()
    root = Path(proc_root)
    prefix = f"{name}=".encode()
    wanted = value.rstrip("/")
    found: set[int] = set()
    for pid in pids:
        try:
            for item in (root / str(pid) / "environ").read_bytes().split(b"\0"):
                if item.startswith(prefix) and item[len(prefix):].decode(errors="replace").rstrip("/") == wanted:
                    found.add(pid)
                    break
        except OSError:
            continue
    return found


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    comm: str
    starttime: int
    cmdline: tuple[str, ...]
    exe: str | None
    cwd: str | None
    env: dict[str, str]


_OBSERVED_ENV = ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID")


def process_records(proc_root: str | Path = "/proc") -> dict[int, ProcessRecord]:
    """Read process facts needed by ownership and tracking; environment is allowlisted."""
    root = Path(proc_root)
    result: dict[int, ProcessRecord] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return result
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            end = stat.rindex(")")
            comm = stat[stat.index("(") + 1 : end]
            fields = stat[end + 2 :].split()
            ppid, starttime = int(fields[1]), int(fields[19])
            raw_cmdline = (entry / "cmdline").read_bytes()
        except (OSError, ValueError, IndexError):
            continue
        env: dict[str, str] = {}
        try:
            for part in (entry / "environ").read_bytes().split(b"\0"):
                key, sep, value = part.partition(b"=")
                if sep and key.decode(errors="replace") in _OBSERVED_ENV:
                    env[key.decode(errors="replace")] = value.decode(errors="replace")
        except OSError:
            pass
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            exe = None
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            cwd = None
        result[pid] = ProcessRecord(pid, ppid, comm, starttime,
                                    tuple(p.decode(errors="replace") for p in raw_cmdline.split(b"\0") if p),
                                    exe, cwd, env)
    return result


_GAME_HELPERS = frozenset({
    "steam", "steamwebhelper", "steamservice", "reaper", "pressure-vessel",
    "pressure-vessel-wrap", "pressure-vessel-adverb", "bwrap", "srt-bwrap",
    "srt-logger", "steam-runtime", "heroic", "heroicgameslauncher",
    "heroicgameslauncher-linux", "legendary", "gogdl", "nile",
    # Proton/umu helpers that run from the game folder or show their own small windows (ProtonFixes progress).
    "zenity", "umu-run", "umu", "pv-adverb", "steam-runtime-launch-client", "steam-runtime-supervisor",
})


def ancestors(pid: int, records: dict[int, ProcessRecord]) -> set[int]:
    """pid and every parent above it (the monitor, Waystone, its shell): never the game."""
    seen: set[int] = set()
    while pid and pid not in seen:
        seen.add(pid)
        record = records.get(pid)
        if record is None:
            break
        pid = record.ppid
    return seen


def matching_game_processes(records: dict[int, ProcessRecord], *, provider: str, game_id: str,
                            game_executable: str | None = None, game_folder: str | None = None,
                            exclude_pids: set[int] | frozenset[int] = frozenset()) -> set[int]:
    """Conservative process matcher: Steam's app env tag or exact exe/folder evidence for other stores.
    exclude_pids: the monitor and its ancestors, whose argv carries the game folder."""
    provider = provider.casefold()
    wanted_exe = Path(game_executable).name.casefold() if game_executable else ""
    folder = str(Path(game_folder).expanduser()) if game_folder else ""
    matches: set[int] = set()
    for pid, record in records.items():
        if pid in exclude_pids:
            continue
        comm = record.comm.casefold()
        exe_name = Path(record.exe).name.casefold() if record.exe else ""
        if comm in _GAME_HELPERS or exe_name in _GAME_HELPERS:
            continue
        if provider == "steam" and any(record.env.get(key) == str(game_id)
                                       for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID")):
            matches.add(pid)
            continue
        if wanted_exe and (comm == wanted_exe or exe_name == wanted_exe):
            matches.add(pid)
            continue
        if folder:
            try:
                root = Path(folder).resolve()
                candidates = [*(record.cmdline or ()), record.cwd or "", record.exe or ""]
                if any(Path(value).resolve().is_relative_to(root) for value in candidates
                       if value and value.startswith("/")):
                    matches.add(pid)
            except (OSError, ValueError, RuntimeError):
                continue
    return matches


def atomic_status_write(path: str | Path, payload: dict) -> None:
    """Atomically replace a private status document without exposing partial JSON."""
    target = Path(path).expanduser()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


class Hyprland:
    """Small command adapter; tests inject a callable and never contact a compositor."""

    def __init__(self, *, enabled: bool | None = None, run: Callable | None = None,
                 environ: dict[str, str] | None = None, which: Callable = shutil.which):
        env = os.environ if environ is None else environ
        self.enabled = (bool(which("hyprctl")) and bool(env.get("HYPRLAND_INSTANCE_SIGNATURE"))) if enabled is None else enabled
        self._run = run or subprocess.run
        self._event_socket = (Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "hypr"
                              / str(env.get("HYPRLAND_INSTANCE_SIGNATURE") or "") / ".socket2.sock")

    def watch_new_windows(self, callback: Callable[[str, str, str], None]) -> Callable[[], None] | None:
        """Call callback(address, window_class, workspace) the moment Hyprland opens a window (its event socket), so a
        launcher window can be hidden before it is drawn instead of up to one poll later. Returns a stop function,
        or None when the event socket is unavailable (polling still hides the window)."""
        if not self.enabled:
            return None
        import socket
        import threading
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(self._event_socket))
        except OSError:
            return None
        stopped = threading.Event()

        def reader():
            buffer = b""
            while not stopped.is_set():
                try:
                    chunk = sock.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for raw in lines:
                    event, _, data = raw.decode(errors="replace").partition(">>")
                    if event == "openwindow":
                        parts = data.split(",", 3)   # address,workspace,class,title
                        if len(parts) >= 3:
                            try:
                                callback("0x" + parts[0].removeprefix("0x"), parts[2], parts[1])
                            except Exception:
                                pass

        threading.Thread(target=reader, name="waystone-hypr-events", daemon=True).start()

        def stop():
            stopped.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        return stop

    def _call(self, args: list[str], *, json_output: bool = False):
        if not self.enabled:
            return [] if json_output else None
        try:
            result = self._run(args, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return [] if json_output else None
        if json_output:
            try:
                return json.loads(result.stdout or "[]")
            except (ValueError, TypeError):
                return []
        return result

    def clients(self) -> list[dict]:
        value = self._call(["hyprctl", "-j", "clients"], json_output=True)
        return value if isinstance(value, list) else []

    def move(self, address: str, workspace: str) -> None:
        self._call(["hyprctl", "dispatch", f'hl.dsp.window.move({{ workspace = "{workspace}", follow = false, window = "address:{address}" }})'])

    def place(self, address: str, monitor: str, fullscreen: bool, *, follow: bool = True) -> None:
        if monitor:
            follow_text = "true" if follow else "false"
            self._call(["hyprctl", "dispatch", f'hl.dsp.window.move({{ monitor = "{monitor}", follow = {follow_text}, window = "address:{address}" }})'])
        if fullscreen:
            self._call(["hyprctl", "dispatch", f'hl.dsp.window.fullscreen({{ window = "address:{address}" }})'])

    def toggle_fullscreen(self, address: str) -> None:
        self._call(["hyprctl", "dispatch", f'hl.dsp.window.fullscreen({{ window = "address:{address}" }})'])

    def active_workspace(self) -> str:
        value = self._call(["hyprctl", "-j", "activeworkspace"], json_output=True)
        return str(value.get("name") or "1") if isinstance(value, dict) else "1"


def _locked(method):
    """Run a WindowOwnership method under its lock (the compositor event thread hides windows too)."""
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


@dataclass(frozen=True)
class WindowPlacement:
    workspace: str
    monitor: str
    fullscreen: bool


class WindowOwnership:
    """Hide only windows owned by a supplied PID set and restore their original placement."""

    def __init__(self, backend: Hyprland, *, hidden_workspace: str = HIDDEN_WORKSPACE):
        import threading
        self.backend = backend
        self.hidden_workspace = hidden_workspace
        self._placements: dict[str, WindowPlacement] = {}
        self._lock = threading.RLock()

    def hide_new(self, address: str, workspace: str = "", monitor: str = "") -> bool:
        """Hide a window the moment it opens (from the compositor's event), remembering where it opened."""
        with self._lock:
            if not address or address in self._placements:
                return False
            self._placements[address] = WindowPlacement(workspace or "1", monitor, False)
            self.backend.move(address, self.hidden_workspace)
            return True

    @property
    def addresses(self) -> frozenset[str]:
        return frozenset(self._placements)

    @_locked
    def hide(self, clients: Iterable[dict], owner_pids: set[int], *,
             exclude_pids: set[int] | None = None,
             exclude_window: Callable[[dict], bool] | None = None) -> set[str]:
        excluded = exclude_pids or set()
        moved: set[str] = set()
        for client in clients:
            address = str(client.get("address") or "")
            if (not address or client.get("pid") not in owner_pids or client.get("pid") in excluded
                    or (exclude_window and exclude_window(client))):
                continue
            workspace_data = client.get("workspace") or {}
            workspace = str(workspace_data.get("name") or "1")
            if address not in self._placements:
                self._placements[address] = WindowPlacement(
                    workspace, str(client.get("monitor") or ""),
                    bool(client.get("fullscreen") or client.get("fullscreenClient")),
                )
            if workspace != self.hidden_workspace:
                self.backend.move(address, self.hidden_workspace)
                moved.add(address)
        return moved

    @_locked
    def restore(self, clients: Iterable[dict] | None = None, *, destination: str | None = None) -> set[str]:
        current = {str(c.get("address")): c for c in (clients if clients is not None else self.backend.clients())
                   if c.get("address")}
        restored: set[str] = set()
        for address, placement in list(self._placements.items()):
            if address not in current:
                continue
            self.backend.move(address, destination or placement.workspace)
            if destination is None:
                current_client = current[address]
                current_fullscreen = bool(current_client.get("fullscreen") or current_client.get("fullscreenClient"))
                self.backend.place(address, placement.monitor, False, follow=False)
                if current_fullscreen != placement.fullscreen:
                    toggle = getattr(self.backend, "toggle_fullscreen", None)
                    if toggle:
                        toggle(address)
            restored.add(address)
            del self._placements[address]
        return restored

    @_locked
    def reveal(self, clients: Iterable[dict] | None = None, *, destination: str) -> set[str]:
        """Show hidden owned windows for user attention but retain their restore snapshot."""
        current = {str(c.get("address")): c for c in (clients if clients is not None else self.backend.clients())
                   if c.get("address")}
        revealed: set[str] = set()
        for address in self._placements:
            client = current.get(address)
            if client and str((client.get("workspace") or {}).get("name") or "") == self.hidden_workspace:
                self.backend.move(address, destination)
                revealed.add(address)
        return revealed
