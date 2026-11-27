"""Discord phase 1 integration for Waystone: Local IPC Rich Presence, client hand-off, and Vesktop controller PTT (W20a).

Safety contract (from ``research/w20/discord.md``):
- Waystone NEVER reads, stores, or uses a Discord user token.
- Waystone NEVER calls Discord's user REST or Gateway API.
- All communication is strictly local over Discord's documented AF_UNIX IPC socket
  (``$XDG_RUNTIME_DIR/discord-ipc-0..9``, plus Flatpak and Snap socket paths) and
  Vesktop's local shortcut socket/CLI (``$XDG_RUNTIME_DIR/vesktop.sock`` / ``vesktop --run-shortcut``).

Discord Application ID registration:
- Local IPC Rich Presence (``SET_ACTIVITY``) requires a numeric Discord Application ID (``client_id``) in the
  handshake frame. An Application ID is registered at ``https://discord.com/developers/applications``
  ("New Application" -> Name: "Waystone" -> copy the public Application ID; no client secret, bot token, or OAuth
  redirect is needed for Rich Presence).
- Waystone has no registered Application ID yet, so ``DEFAULT_DISCORD_APP_ID`` is empty and Rich Presence stays off
  until an ID is set in ``Settings > Social`` or via the ``WAYSTONE_DISCORD_APP_ID`` environment variable. Never put an
  ID here that wasn't registered for Waystone: Discord would show that other application's name.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import time
from typing import Any, Callable
import uuid


# Default public Discord Application ID constant for Waystone (configurable in Settings > Social or WAYSTONE_DISCORD_APP_ID).
DEFAULT_DISCORD_APP_ID = ""  # no registered Waystone application yet (see module docstring)

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

MAX_IPC_FRAME_BYTES = 65536
PTT_BEHAVIORS = ("hold", "toggle")


@dataclass(frozen=True)
class DiscordClientInfo:
    client_id: str
    name: str
    kind: str  # "native" or "flatpak"
    command: tuple[str, ...]
    window_class: str
    supports_ptt: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.client_id,
            "name": self.name,
            "label": f"{self.name} ({'Flatpak' if self.kind == 'flatpak' else 'Native'})",
            "kind": self.kind,
            "command": list(self.command),
            "window_class": self.window_class,
            "supports_ptt": self.supports_ptt,
        }


_CLIENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "base_id": "vesktop",
        "name": "Vesktop",
        "binaries": ("vesktop",),
        "flatpak_id": "dev.vencord.Vesktop",
        "window_class": "vesktop",
        "supports_ptt": True,
    },
    {
        "base_id": "discord",
        "name": "Discord",
        "binaries": ("discord",),
        "flatpak_id": "com.discordapp.Discord",
        "window_class": "discord",
        "supports_ptt": False,
    },
    {
        "base_id": "legcord",
        "name": "Legcord",
        "binaries": ("legcord", "armcord"),
        "flatpak_id": "app.legcord.Legcord",
        "window_class": "legcord|armcord",
        "supports_ptt": False,
    },
    {
        "base_id": "webcord",
        "name": "WebCord",
        "binaries": ("webcord",),
        "flatpak_id": "io.github.spacingbat3.webcord",
        "window_class": "WebCord|webcord",
        "supports_ptt": False,
    },
)


def resolve_runtime_dir(
    runtime_dir: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> Path | None:
    if runtime_dir is not None:
        return Path(runtime_dir)
    env = environ if environ is not None else os.environ
    raw = env.get("XDG_RUNTIME_DIR", "").strip()
    if raw:
        return Path(raw)
    if hasattr(os, "getuid"):
        candidate = Path(f"/run/user/{os.getuid()}")
        if candidate.is_dir():
            return candidate
    return None


def discover_discord_ipc_sockets(
    runtime_dir: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> list[Path]:
    """Return existing Discord Local IPC socket paths (native, Flatpak, and Snap) in priority order."""
    base = resolve_runtime_dir(runtime_dir, environ=environ)
    if base is None or not base.is_dir():
        return []

    prefixes = (
        base,
        base / "app" / "com.discordapp.Discord",
        base / "app" / "dev.vencord.Vesktop",
        base / "app" / "app.legcord.Legcord",
        base / "app" / "io.github.spacingbat3.webcord",
        base / "snap.discord",
    )
    found: list[Path] = []
    seen: set[Path] = set()
    for i in range(10):
        name = f"discord-ipc-{i}"
        for prefix in prefixes:
            candidate = prefix / name
            try:
                st = candidate.stat()
            except OSError:
                continue
            if stat.S_ISSOCK(st.st_mode) and candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
    return found


def pack_ipc_frame(opcode: int, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack("<II", int(opcode), len(body)) + body


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise EOFError("Discord IPC socket closed during read")
        buf.extend(chunk)
    return bytes(buf)


def unpack_ipc_frame(sock: socket.socket) -> tuple[int, dict[str, Any]]:
    header = _recv_exact(sock, 8)
    opcode, length = struct.unpack("<II", header)
    if length > MAX_IPC_FRAME_BYTES:
        raise ValueError("Discord IPC frame exceeds size cap")
    raw = _recv_exact(sock, length) if length > 0 else b"{}"
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("Discord IPC payload must be a JSON object")
    return opcode, doc


class DiscordIpcClient:
    """Stdlib-only client for Discord's local IPC socket ($XDG_RUNTIME_DIR/discord-ipc-0..9)."""

    def __init__(
        self,
        *,
        app_id: str = DEFAULT_DISCORD_APP_ID,
        runtime_dir: str | Path | None = None,
        environ: dict[str, str] | None = None,
        timeout: float = 0.35,
    ) -> None:
        self.app_id = (str(app_id or "").strip() or DEFAULT_DISCORD_APP_ID)
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else None
        self.environ = environ
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._connected_path: Path | None = None

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        self._connected_path = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _connect(self) -> socket.socket | None:
        if self._sock is not None:
            return self._sock
        if not self.app_id:
            return None
        candidates = discover_discord_ipc_sockets(self.runtime_dir, environ=self.environ)
        for path in candidates:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(str(path))
                sock.sendall(pack_ipc_frame(OP_HANDSHAKE, {"v": 1, "client_id": self.app_id}))
                op, reply = unpack_ipc_frame(sock)
                if op == OP_CLOSE or reply.get("evt") == "ERROR":
                    sock.close()
                    continue
                self._sock = sock
                self._connected_path = path
                return sock
            except Exception:
                try:
                    sock.close()
                except OSError:
                    pass
                continue
        return None

    def _send_activity_payload(self, activity: dict[str, Any] | None, *, pid: int | None = None) -> bool:
        frame = {
            "cmd": "SET_ACTIVITY",
            "args": {
                "pid": int(pid if pid is not None else os.getpid()),
                "activity": activity,
            },
            "nonce": uuid.uuid4().hex,
        }
        for _attempt in range(2):
            sock = self._connect()
            if sock is None:
                return False
            try:
                sock.sendall(pack_ipc_frame(OP_FRAME, frame))
                op, reply = unpack_ipc_frame(sock)
                if op == OP_CLOSE or reply.get("evt") == "ERROR":
                    self.close()
                    continue
                return True
            except Exception:
                self.close()
                continue
        return False

    def set_activity(
        self,
        title: str,
        *,
        start_timestamp: int | float | None = None,
        pid: int | None = None,
    ) -> bool:
        """Broadcast 'Playing <title>' with start timestamp over local IPC. Silent if Discord is not running."""
        clean_title = str(title or "").strip()
        if not clean_title:
            return False
        start_ts = int(start_timestamp if start_timestamp and start_timestamp > 0 else time.time())
        activity = {
            "name": clean_title,
            "details": f"Playing {clean_title}",
            "timestamps": {"start": start_ts},
        }
        return self._send_activity_payload(activity, pid=pid)

    def clear_activity(self, *, pid: int | None = None) -> bool:
        """Clear Rich Presence activity when the game closes. Silent if Discord is not running."""
        ok = self._send_activity_payload(None, pid=pid)
        self.close()
        return ok


def _default_flatpak_installed(
    flatpak_id: str,
    *,
    home_dir: Path | None = None,
    system_flatpak_root: Path | None = None,
) -> bool:
    home = Path(home_dir) if home_dir is not None else Path.home()
    sys_root = Path(system_flatpak_root) if system_flatpak_root is not None else Path("/var/lib/flatpak")
    user_app = home / ".local/share/flatpak/app" / flatpak_id
    sys_app = sys_root / "app" / flatpak_id
    user_desktop = home / ".local/share/flatpak/exports/share/applications" / f"{flatpak_id}.desktop"
    sys_desktop = sys_root / "exports/share/applications" / f"{flatpak_id}.desktop"
    return any(p.exists() for p in (user_app, sys_app, user_desktop, sys_desktop))


def detect_discord_clients(
    *,
    which: Callable[[str], str | None] = shutil.which,
    flatpak_installed: Callable[[str], bool] | None = None,
    home_dir: Path | None = None,
    system_flatpak_root: Path | None = None,
) -> list[DiscordClientInfo]:
    """Detect installed native and Flatpak Discord clients (Vesktop, Official Discord, Legcord, WebCord)."""
    check_flatpak = flatpak_installed or (
        lambda app_id: _default_flatpak_installed(app_id, home_dir=home_dir, system_flatpak_root=system_flatpak_root)
    )
    has_flatpak_bin = bool(which("flatpak")) or flatpak_installed is not None
    clients: list[DiscordClientInfo] = []

    for spec in _CLIENT_SPECS:
        for binary_name in spec["binaries"]:
            resolved = which(binary_name)
            if resolved:
                clients.append(
                    DiscordClientInfo(
                        client_id=spec["base_id"],
                        name=spec["name"],
                        kind="native",
                        command=(str(resolved),),
                        window_class=spec["window_class"],
                        supports_ptt=bool(spec["supports_ptt"]),
                    )
                )
                break
        if has_flatpak_bin and check_flatpak(spec["flatpak_id"]):
            flatpak_cmd = str(which("flatpak") or "flatpak")
            clients.append(
                DiscordClientInfo(
                    client_id=f"{spec['base_id']}-flatpak",
                    name=spec["name"],
                    kind="flatpak",
                    command=(flatpak_cmd, "run", spec["flatpak_id"]),
                    window_class=spec["window_class"],
                    supports_ptt=bool(spec["supports_ptt"]),
                )
            )
    return clients


def choose_discord_client(
    clients: list[DiscordClientInfo],
    preferred_id: str = "auto",
) -> DiscordClientInfo | None:
    if not clients:
        return None
    pref = (preferred_id or "auto").strip()
    if pref and pref != "auto":
        for client in clients:
            if client.client_id == pref:
                return client
        for client in clients:
            if client.client_id.split("-", 1)[0] == pref.split("-", 1)[0]:
                return client
    return clients[0]


def open_or_focus_discord_client(
    client: DiscordClientInfo,
    *,
    which: Callable[[str], str | None] = shutil.which,
    spawn_fn: Callable[[list[str]], Any] | None = None,
    environ: dict[str, str] | None = None,
) -> bool:
    """Focus the chosen Discord client window if open, and start/hand off to its executable."""
    if client is None or not client.command:
        return False
    env = environ if environ is not None else os.environ
    spawner = spawn_fn or (
        lambda argv: subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    )
    # Best-effort compositor focus on Hyprland or X11 wmctrl
    try:
        if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
            hyprctl = which("hyprctl")
            if hyprctl:
                spawner([hyprctl, "dispatch", "focuswindow", f"class:^({client.window_class})$"])
        else:
            wmctrl = which("wmctrl")
            if wmctrl:
                spawner([wmctrl, "-x", "-a", client.window_class.split("|", 1)[0]])
    except Exception:
        pass

    try:
        spawner(list(client.command))
        return True
    except Exception:
        return False


def send_vesktop_ptt(
    down: bool,
    *,
    client: DiscordClientInfo | None = None,
    runtime_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    spawn_fn: Callable[[list[str]], Any] | None = None,
    timeout: float = 0.15,
) -> tuple[bool, str]:
    """Drive Vesktop Push-to-Talk via ``$XDG_RUNTIME_DIR/vesktop.sock`` or ``vesktop --run-shortcut``.

    Returns ``(ok, message)``. When the selected client does not support PTT, returns a plain message.
    """
    if client is not None and not client.supports_ptt:
        return (
            False,
            f"{client.name} does not support external Wayland Push-to-Talk. Switch to Vesktop to use controller PTT.",
        )

    action = "pushToTalk:down" if down else "pushToTalk:up"
    base = resolve_runtime_dir(runtime_dir, environ=environ)
    if base is not None:
        sock_candidates = (
            base / "vesktop.sock",
            base / "app" / "dev.vencord.Vesktop" / "vesktop.sock",
        )
        for sock_path in sock_candidates:
            try:
                st = sock_path.stat()
            except OSError:
                continue
            if not stat.S_ISSOCK(st.st_mode):
                continue
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(str(sock_path))
                sock.sendall(f"run-shortcut {action}\n".encode("utf-8"))
                return True, f"Sent {action} to Vesktop socket."
            except OSError:
                continue
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

    # Fallback to Vesktop CLI (--run-shortcut)
    spawner = spawn_fn or (
        lambda argv: subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    )
    if client is not None and client.supports_ptt and client.command:
        try:
            spawner([*client.command, "--run-shortcut", action])
            return True, f"Sent {action} via {client.name} CLI."
        except Exception:
            pass

    vesktop_bin = which("vesktop")
    if vesktop_bin:
        try:
            spawner([str(vesktop_bin), "--run-shortcut", action])
            return True, f"Sent {action} via Vesktop CLI."
        except Exception:
            pass

    return (
        False,
        "Vesktop is not running or installed. Controller Push-to-Talk requires Vesktop.",
    )


_BUTTON_MASKS: dict[str, int] = {
    "Controller:D-pad Up": 0x0001,
    "Controller:D-pad Down": 0x0002,
    "Controller:D-pad Left": 0x0004,
    "Controller:D-pad Right": 0x0008,
    "Controller:Menu": 0x0010,
    "Controller:Start": 0x0010,
    "Controller:View": 0x0020,
    "Controller:Back": 0x0020,
    "Controller:Left Stick": 0x0040,
    "Controller:L3": 0x0040,
    "Controller:Right Stick": 0x0080,
    "Controller:R3": 0x0080,
    "Controller:LB": 0x0100,
    "Controller:RB": 0x0200,
    "Controller:Home": 0x0400,
    "Controller:Guide": 0x0400,
    "Controller:A": 0x1000,
    "Controller:B": 0x2000,
    "Controller:X": 0x4000,
    "Controller:Y": 0x8000,
}


def is_controller_source_pressed(frame: dict[str, Any] | None, source: str) -> bool:
    """Return True if ``source`` (e.g. ``Controller:Share`` or ``Controller:Right Stick``) is pressed in ``frame``."""
    src = str(source or "").strip()
    if not src or not isinstance(frame, dict):
        return False

    ext = frame.get("extended_buttons")
    if isinstance(ext, list):
        short_name = src.split(":", 1)[1].strip() if ":" in src else src
        if src == "Controller:Share" and any(x in ("Share", "CompanionShare") for x in ext):
            return True
        if short_name in ext or src in ext:
            return True

    inp = frame.get("input")
    if not isinstance(inp, dict):
        return False

    if src == "Controller:LT":
        return int(inp.get("left_trigger") or 0) > 128
    if src == "Controller:RT":
        return int(inp.get("right_trigger") or 0) > 128

    mask = _BUTTON_MASKS.get(src)
    if mask is not None:
        return (int(inp.get("buttons") or 0) & mask) != 0
    return False
