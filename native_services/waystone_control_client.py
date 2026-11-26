"""Qt-free client side of the Waystone control socket (used by the MCP server, which has no PySide6)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import stat
import uuid
from typing import Any


MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 65536
MAX_QUERY_CHARS = 256
MAX_SEARCH_LIMIT = 100
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ALLOWED_FIELDS = {
    "play": {"request_id", "action", "game_id"},
    "status": {"request_id", "action"},
    "stop": {"request_id", "action"},
    "search": {"request_id", "action", "query", "limit"},
}


def runtime_socket_path(runtime_dir: str | Path | None = None) -> Path:
    """Return the private control socket path below the selected XDG runtime dir."""
    raw = runtime_dir if runtime_dir is not None else os.environ.get("XDG_RUNTIME_DIR")
    if not raw:
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    root = Path(raw)
    try:
        info = root.stat()
    except OSError as exc:
        raise RuntimeError("XDG runtime directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError("XDG runtime directory has unsafe ownership or permissions")
    return root / "waystone" / "control.sock"


class WaystoneSocketClient:
    """Small bounded AF_UNIX client used by the separate MCP stdio process."""

    def __init__(self, *, path: str | Path | None = None, timeout: float = 1.0):
        # Resolve the environment lazily so the independent stdio MCP process can
        # still start in a stripped environment and report a clean not-running error.
        self.path = Path(path) if path is not None else None
        self.timeout = timeout

    def request(self, action: str, **fields: Any) -> dict[str, Any]:
        if action not in _ALLOWED_FIELDS:
            raise ValueError("Unsupported Waystone control action")
        request_id = uuid.uuid4().hex
        payload = json.dumps({"request_id": request_id, "action": action, **fields}, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Waystone control request is too large")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            path = self.path or runtime_socket_path()
            sock.connect(str(path))
            sock.sendall(payload)
            received = bytearray()
            while b"\n" not in received:
                chunk = sock.recv(4096)
                if not chunk:
                    raise RuntimeError("Waystone closed the control connection")
                received.extend(chunk)
                if len(received) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Waystone control response is too large")
            response = json.loads(bytes(received).split(b"\n", 1)[0].decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as exc:
            raise ConnectionError("Waystone is not running or its local control socket is unavailable") from exc
        finally:
            sock.close()
        if not isinstance(response, dict) or response.get("request_id") != request_id:
            raise ConnectionError("Waystone returned an invalid control response")
        if response.get("ok") is not True:
            raise ValueError(str(response.get("error") or "Waystone rejected the control request"))
        result = response.get("result")
        if not isinstance(result, (dict, list)):
            raise ConnectionError("Waystone returned an invalid control result")
        return result
