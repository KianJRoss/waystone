"""Private local control socket for the running Waystone desktop.

The server deliberately accepts only library IDs and fixed actions; it is not a
generic RPC, command runner, or installer. Its callbacks execute on Qt's GUI
thread so app state and launch actions stay serialized with the visible UI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import time
import uuid
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from native_services.waystone_control_client import (  # noqa: F401 - re-exported
    MAX_QUERY_CHARS, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MAX_SEARCH_LIMIT, WaystoneSocketClient,
    _ALLOWED_FIELDS, _REQUEST_ID, runtime_socket_path,
)
from native_services import file_locking as fcntl


def _js_value(value: Any) -> Any:
    """QML functions called from Python return JS arrays/objects as QJSValue: convert them to Python."""
    to_variant = getattr(value, "toVariant", None)
    return to_variant() if callable(to_variant) else value


class AppJournal:
    """Append a compact, private JSONL audit event for every control request."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, *, request_id: str, action: str, outcome: str) -> None:
        record = {
            "at": time.time(), "component": "local-control", "request_id": request_id,
            "action": action, "outcome": outcome,
        }
        payload = (json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short app-journal write")
                view = view[written:]
        finally:
            os.close(fd)


def _peer_uid(socket_descriptor: int) -> int:
    """Read Linux SO_PEERCRED from a duplicate descriptor without taking ownership."""
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("peer credential verification is unavailable")
    duplicate = socket.fromfd(socket_descriptor, socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        raw = duplicate.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return uid
    finally:
        duplicate.close()


class WaystoneControlServer(QObject):
    """Newline-JSON app control server; all action handlers run on this object's thread."""

    def __init__(self, *, launch_view: Any, library_model: Any, qml_root: Any,
                 runtime_dir: str | Path | None = None,
                 journal: AppJournal | None = None,
                 peer_uid: Callable[[int], int] = _peer_uid,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._launch = launch_view
        self._model = library_model
        self._root = qml_root
        self._path = runtime_socket_path(runtime_dir)
        self._journal = journal or AppJournal(Path.home() / ".local/state/waystone/app-journal.jsonl")
        self._peer_uid = peer_uid
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept_connections)
        self._clients: dict[QLocalSocket, bytearray] = {}
        self._started = False
        self._lock_fd: int | None = None
        self._application = QCoreApplication.instance()
        self._socket_identity: tuple[int, int] | None = None

    @property
    def socket_path(self) -> Path:
        return self._path

    def start(self) -> None:
        if self._started:
            return
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_info = self._path.parent.lstat()
        if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid()
                or parent_info.st_mode & 0o077):
            raise RuntimeError("Waystone runtime socket directory is unsafe")
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self._lock_fd = os.open(lock_path, flags, 0o600)
            os.fchmod(self._lock_fd, 0o600)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._remove_stale_socket_only()
            if not self._server.listen(str(self._path)):
                raise RuntimeError("Waystone control socket failed to listen")
            self._secure_socket_mode()
            self._started = True
        except BlockingIOError as exc:
            self._release_lock()
            raise RuntimeError("another Waystone control server is already listening") from exc
        except Exception:
            self._release_lock()
            raise

    def _remove_stale_socket_only(self) -> None:
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError("refusing to remove a non-owned or non-socket control path")
        # Probe before removing. A successful connection always wins over any stale lock file.
        probe = QLocalSocket(self)
        probe.connectToServer(str(self._path))
        if probe.waitForConnected(150):
            probe.disconnectFromServer()
            raise RuntimeError("another Waystone control server is already listening")
        if probe.error() not in (QLocalSocket.LocalSocketError.ConnectionRefusedError,
                                 QLocalSocket.LocalSocketError.ServerNotFoundError):
            raise RuntimeError("could not establish whether the existing control socket is stale")
        if not QLocalServer.removeServer(str(self._path)):
            raise RuntimeError("could not remove stale Waystone control socket")

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def _secure_socket_mode(self) -> None:
        info = self._path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            self._server.close()
            raise RuntimeError("Waystone created an unsafe control socket")
        os.chmod(self._path, 0o600, follow_symlinks=False)
        if stat.S_IMODE(self._path.stat().st_mode) != 0o600:
            self._server.close()
            raise RuntimeError("Waystone control socket permissions could not be secured")
        info = self._path.lstat()
        self._socket_identity = (info.st_dev, info.st_ino)

    def close(self) -> None:
        self._watch_input(False)
        self._server.close()
        for client in tuple(self._clients):
            client.disconnectFromServer()
        self._clients.clear()
        if not self._started:
            self._release_lock()
            return
        self._started = False
        # Qt removes its own socket on close; do not unlink a path replaced by another process.
        try:
            info = self._path.lstat()
            if (stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                    and self._socket_identity == (info.st_dev, info.st_ino)):
                self._path.unlink()
        except FileNotFoundError:
            pass
        self._socket_identity = None
        self._release_lock()

    _INPUT_EVENTS = frozenset({
        QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
        QEvent.Type.TouchBegin, QEvent.Type.TabletPress,
    })

    def _watch_input(self, on: bool) -> None:
        """The app-wide input filter is a Python call per Qt event: install it only while an animation plays."""
        if self._application is None or on == getattr(self, "_watching_input", False):
            return
        if on:
            self._application.installEventFilter(self)
        else:
            self._application.removeEventFilter(self)
        self._watching_input = on

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """User input interrupts the narration and executes the requested launch now."""
        if not bool(self._root_property("assistantAnimating", False)):
            self._watch_input(False)        # animation over: stop filtering every event
            return False
        if event.type() in self._INPUT_EVENTS:
            try:
                if bool(self._root_property("assistantAnimating", False)):
                    self._root.cancelAssistantAnimationAndLaunch()
                    # Do not let the interrupting click/key also activate the UI now
                    # that cancellation has already launched the requested game.
                    return True
            except Exception:
                pass
        return False

    def _root_property(self, name: str, default: Any = None) -> Any:
        getter = getattr(self._root, "property", None)
        if callable(getter):
            value = getter(name)
            return default if value is None else value
        return getattr(self._root, name, default)

    @staticmethod
    def _focus_waystone_window() -> None:
        """Best-effort compositor focus; QML raise/requestActivate is the portable fallback."""
        try:
            if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
                executable = shutil.which("hyprctl")
                if executable:
                    # New (hl.dsp, Lua config) and classic dispatcher syntax: whichever this Hyprland speaks.
                    for args in (["dispatch", 'hl.dsp.focus({ window = "title:^(Waystone)$" })'],
                                 ["dispatch", "focuswindow", "title:^(Waystone)$"]):
                        subprocess.Popen([executable, *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
                    return
            executable = shutil.which("wmctrl")
            if executable:
                subprocess.Popen([executable, "-a", "Waystone"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except OSError:
            return

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            client = self._server.nextPendingConnection()
            if client is None:
                continue
            try:
                uid = self._peer_uid(int(client.socketDescriptor()))
            except Exception:
                client.abort()
                client.deleteLater()
                continue
            if uid != os.getuid():
                client.abort()
                client.deleteLater()
                continue
            self._clients[client] = bytearray()
            client.readyRead.connect(lambda c=client: self._read_client(c))
            client.disconnected.connect(lambda c=client: self._drop_client(c))

    def _drop_client(self, client: QLocalSocket) -> None:
        self._clients.pop(client, None)
        client.deleteLater()

    def _read_client(self, client: QLocalSocket) -> None:
        buffer = self._clients.get(client)
        if buffer is None:
            return
        buffer.extend(bytes(client.readAll()))
        if len(buffer) > MAX_REQUEST_BYTES and b"\n" not in buffer:
            self._write(client, self._response(None, False, error="request_too_large"))
            client.disconnectFromServer()
            return
        while b"\n" in buffer:
            line, _, remainder = buffer.partition(b"\n")
            buffer[:] = remainder
            if len(line) > MAX_REQUEST_BYTES:
                self._write(client, self._response(None, False, error="request_too_large"))
                client.disconnectFromServer()
                return
            self._handle_line(client, bytes(line))

    def _handle_line(self, client: QLocalSocket, line: bytes) -> None:
        request_id = uuid.uuid4().hex
        action = "invalid"
        outcome = "invalid_request"
        response_id: str | None = None
        response: dict[str, Any]
        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("object_required")
            raw_id = request.get("request_id")
            if isinstance(raw_id, str) and _REQUEST_ID.fullmatch(raw_id):
                request_id = response_id = raw_id
            action = request.get("action") if isinstance(request.get("action"), str) else "invalid"
            if action not in _ALLOWED_FIELDS:
                raise ValueError("unknown_action")
            if set(request) - _ALLOWED_FIELDS[action]:
                raise ValueError("unexpected_fields")
            if response_id is None:
                raise ValueError("invalid_request_id")
            result = self._dispatch(action, request)
            outcome = str(result.get("status", "ok")) if isinstance(result, dict) else "ok"
            response = {"request_id": response_id, "ok": True, "result": result}
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            response = {"request_id": response_id, "ok": False, "error": "invalid_request"}
        except Exception:
            outcome = "internal_error"
            response = {"request_id": response_id, "ok": False, "error": "control_action_failed"}
        try:
            self._journal.append(request_id=request_id, action=action[:32], outcome=outcome)
        except OSError:
            # Still reply; the failure is visible as a fixed field, not raw filesystem text.
            if response.get("ok"):
                response = {"request_id": response_id, "ok": False, "error": "journal_unavailable"}
        self._write(client, response)

    def _dispatch(self, action: str, request: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        if action == "play":
            game_id = request.get("game_id")
            if not isinstance(game_id, str) or not 1 <= len(game_id) <= 256:
                raise ValueError("invalid_game_id")
            # Treat IDs as selectors into the live catalog, never as caller-provided
            # launch targets. QML performs the authoritative installed-option check.
            if not any(isinstance(game, dict) and game.get("id") == game_id
                       for game in self._model.games):
                return {"status": "rejected", "detail": "Game is not in the Waystone library."}
            status = self._status_result()
            if status.get("activeGame") and status["activeGame"].get("state") in ("launching", "running", "unknown"):
                return {"status": "busy", "activeGame": status["activeGame"]}
            # Animation is presentation only; the final launch still enters the same
            # QML library path used by the visible Play button and overlay.
            request_assistant_play = getattr(self._root, "requestAssistantPlayById", None)
            if callable(request_assistant_play):
                assistant_result = _js_value(request_assistant_play(game_id))
                if isinstance(assistant_result, dict):
                    if assistant_result.get("status") != "started":
                        return assistant_result
                    self._focus_waystone_window()
                    if assistant_result.get("animating"):
                        self._watch_input(True)
                    return {"status": "started", "animating": bool(assistant_result.get("animating")),
                            **self._status_result()}
            handed_off = bool(_js_value(self._root.playGameById(game_id)))
            if not handed_off:
                return {"status": "rejected", "detail": self._safe_detail(self._launch.lastError)}
            return {"status": "started", **self._status_result()}
        if action == "status":
            return self._status_result()
        if action == "stop":
            active = self._launch.activeGame
            if not active:
                return {"status": "no_active_game"}
            stopped = bool(self._launch.stopActiveGame())
            return {"status": "stop_requested" if stopped else "stop_rejected", "activeGame": active}
        query = request.get("query", "")
        limit = request.get("limit", 20)
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
            raise ValueError("invalid_query")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError("invalid_limit")
        return self._search_installed(query, limit)

    def _status_result(self) -> dict[str, Any]:
        return {"activeGame": dict(self._launch.activeGame or {}),
                "launchDetail": str(self._launch.launchDetail or "")[:256],
                "playLabel": str(self._launch.playLabel or "")[:64],
                "assistantAnimation": {
                    "active": bool(self._root_property("assistantAnimating", False)),
                    "phase": str(self._root_property("assistantAnimationPhase", "idle") or "idle"),
                    "gameId": str(self._root_property("assistantAnimationTarget", "") or ""),
                }}

    @staticmethod
    def _safe_detail(value: Any) -> str:
        if not isinstance(value, str) or not value:
            return "No installed launch option or launch profile is available."
        # Never expose raw executable paths, exception text, or shell details to socket/MCP clients.
        return "Waystone could not start this game. Check its launch profile and app status."

    def _search_installed(self, query: str, limit: int) -> list[dict[str, Any]]:
        folded = query.casefold().strip()
        matches: list[dict[str, Any]] = []
        for game in self._model.games:
            game_id = game.get("id") if isinstance(game, dict) else None
            if not isinstance(game_id, str) or not game_id:
                continue
            overview = self._model.overview(game_id)
            options = _js_value(self._root.launchOptionsFor(overview, game_id))
            if not isinstance(options, list):
                options = []
            if not options:
                continue
            providers = sorted({str(option.get("provider", "")) for option in options if option.get("provider")})
            platforms = str(overview.get("platforms", ""))
            title = str(overview.get("title", ""))
            searchable = " ".join([title, *providers, platforms]).casefold()
            if folded and folded not in searchable:
                continue
            matches.append({"id": game_id, "title": title,
                            "provider": ", ".join(providers), "platform": platforms})
            if len(matches) >= limit:
                break
        return matches

    @staticmethod
    def _response(request_id: str | None, ok: bool, *, error: str | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {"request_id": request_id, "ok": ok}
        if error:
            value["error"] = error
        return value

    @staticmethod
    def _write(client: QLocalSocket, response: dict[str, Any]) -> None:
        payload = (json.dumps(response, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        if len(payload) > MAX_RESPONSE_BYTES:
            payload = b'{"request_id":null,"ok":false,"error":"response_too_large"}\n'
        client.write(payload)
        client.flush()
