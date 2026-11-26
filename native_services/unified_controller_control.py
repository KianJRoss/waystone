"""Native AF_UNIX control client for UnifiedController (Waystone integration)."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import socket
import stat
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 131072
DEFAULT_TIMEOUT = 5.0
MAX_TIMEOUT = 30.0
RUNTIME_SUBPATH = Path("waystone-unified-controller")
SOCKET_NAME = "control.sock"

_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_REMOTE_ERR_RE = re.compile(r"^ERR_[A-Z0-9_]{1,60}$")

READ_OPERATIONS = {"status", "profiles", "profile_get", "output_get"}
MUTATION_OPERATIONS = {"profile_select", "profile_update", "cancel_macros", "output_set", "output_game_state"}
ALLOWED_OPERATIONS = READ_OPERATIONS | MUTATION_OPERATIONS

OPERATION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "status": (),
    "profiles": (),
    "profile_get": ("name",),
    "profile_select": ("name", "expected_revision"),
    "profile_update": ("name", "profile", "expected_revision"),
    "cancel_macros": (),
    "output_get": (),
    "output_set": ("type",),
    "output_game_state": ("active_game_ids",),
}


# Canonical wire values; aliases belong at external presentation boundaries only.
VIRTUAL_PAD_TYPES = ("xbox360", "xboxone", "xboxseries", "dualshock4", "dualsense")
OUTPUT_OPTIONAL_FIELDS = {"output_set": {"force", "persist_default"},
                          "output_game_state": {"observation_known"}}


def validate_output_state(data: Any) -> dict[str, Any]:
    """Validate native readback without discarding forward-compatible metadata."""
    if not isinstance(data, dict):
        raise ControllerControlError("ERR_PROTOCOL_ERROR", "Invalid output state.")
    current, default, supported = data.get("current_type"), data.get("default_type"), data.get("supported_types")
    age = data.get("game_observation_age_seconds")
    slots = data.get("slots", [])
    owned_devices = data.get("owned_devices", [])
    if (current is not None and current not in VIRTUAL_PAD_TYPES or default not in VIRTUAL_PAD_TYPES
            or not isinstance(supported, list) or not supported
            or not all(isinstance(value, str) and value in VIRTUAL_PAD_TYPES for value in supported)
            or len(set(supported)) != len(supported)
            or data.get("game_state") not in ("unknown", "idle", "active")
            or data.get("game_observation_scope") != "waystone_tracked_games"
            or age is not None and (type(age) not in (int, float) or not math.isfinite(age) or age < 0)
            or not isinstance(data.get("revision"), str) or not _is_hex(data["revision"], 64)
            or not isinstance(slots, list) or len(slots) > 4
            or any(not isinstance(slot, dict) or type(slot.get("slot")) is not int or slot["slot"] not in range(1, 5)
                   or slot.get("type") not in VIRTUAL_PAD_TYPES or type(slot.get("connected")) is not bool
                   or slot.get("state") not in ("connected", "disconnected", "uncertain") for slot in slots)
            or not isinstance(owned_devices, list) or len(owned_devices) > 64
            or any(not isinstance(device, dict) or not isinstance(device.get("stable_id"), str)
                   or not device["stable_id"] or len(device["stable_id"]) > 512
                   or any(ord(char) < 32 for char in device["stable_id"])
                   or type(device.get("vendor_id")) is not int or not 0 <= device["vendor_id"] <= 0xffff
                   or type(device.get("product_id")) is not int or not 0 <= device["product_id"] <= 0xffff
                   or type(device.get("connected")) is not bool or type(device.get("virtual")) is not bool
                   for device in owned_devices)):
        raise ControllerControlError("ERR_PROTOCOL_ERROR", "Invalid output state.")
    return data


class ControllerControlError(Exception):
    """Sanitized control-client error."""

    def __init__(self, code: str, message: str | None = None, uncertain: bool = False):
        self.code = str(code)
        self.uncertain = bool(uncertain)
        super().__init__(message or self.code)


def _default_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / RUNTIME_SUBPATH / SOCKET_NAME
    return Path(f"/run/user/{os.getuid()}") / RUNTIME_SUBPATH / SOCKET_NAME


def _is_hex(value: str, length: int) -> bool:
    if not isinstance(value, str):
        return False
    if length == 64:
        return bool(_HEX64_RE.fullmatch(value))
    if length == 32:
        return bool(_REQUEST_ID_RE.fullmatch(value))
    return bool(re.fullmatch(r"[0-9a-fA-F]{" + str(length) + r"}", value))


def _validate_socket_path(path: Path) -> None:
    try:
        st = path.lstat()
    except FileNotFoundError as err:
        raise ControllerControlError("ERR_NOT_AVAILABLE", "Control socket not available.") from err
    except OSError as err:
        raise ControllerControlError("ERR_NOT_AVAILABLE", "Control socket not accessible.") from err

    if not stat.S_ISSOCK(st.st_mode):
        raise ControllerControlError("ERR_INVALID_SOCKET", "Control endpoint is not a socket.")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise ControllerControlError("ERR_PERMISSION_DENIED", "Control socket owner mismatch.")
    # Socket must not be group/other accessible (0600 or stricter).
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ControllerControlError("ERR_PERMISSION_DENIED", "Control socket permissions are unsafe.")

    parent = path.parent
    try:
        pst = parent.lstat()
    except OSError as err:
        raise ControllerControlError("ERR_NOT_AVAILABLE", "Control runtime directory not accessible.") from err
    if not stat.S_ISDIR(pst.st_mode):
        raise ControllerControlError("ERR_INVALID_SOCKET", "Control runtime path is not a directory.")
    if hasattr(os, "getuid") and pst.st_uid != os.getuid():
        raise ControllerControlError("ERR_PERMISSION_DENIED", "Control runtime directory owner mismatch.")
    # Runtime dir must be private (0700).
    if pst.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ControllerControlError("ERR_PERMISSION_DENIED", "Control runtime directory permissions are unsafe.")


def _validate_operation_args(operation: str, fields: dict[str, Any]) -> None:
    if operation not in ALLOWED_OPERATIONS:
        raise ControllerControlError("ERR_INVALID_OPERATION", "Unsupported operation.")
    if not isinstance(fields, dict):
        raise ControllerControlError("ERR_INVALID_REQUEST", "Request fields must be a mapping.")
    # Reject reserved/unknown fields.
    for key in fields:
        if key in ("version", "request_id"):
            raise ControllerControlError("ERR_INVALID_REQUEST", "Reserved request field.")
    required = OPERATION_REQUIRED_FIELDS[operation]
    unknown = set(fields) - set(required) - OUTPUT_OPTIONAL_FIELDS.get(operation, set())
    if unknown:
        raise ControllerControlError("ERR_INVALID_REQUEST", "Unknown request field.")
    for key in required:
        if key not in fields:
            raise ControllerControlError("ERR_INVALID_REQUEST", f"Missing required field: {key}.")
    if operation == "output_set":
        if not isinstance(fields.get("type"), str) or fields["type"] not in VIRTUAL_PAD_TYPES:
            raise ControllerControlError("ERR_INVALID_REQUEST", "Unsupported virtual pad type.")
        for key in ("force", "persist_default"):
            if key in fields and type(fields[key]) is not bool:
                raise ControllerControlError("ERR_INVALID_REQUEST", "Output flags must be booleans.")
    elif operation == "output_game_state":
        ids = fields.get("active_game_ids")
        if (not isinstance(ids, list) or len(ids) > 256 or
                not all(isinstance(value, str) and 0 < len(value) <= 1024
                        and not any(ord(c) < 32 for c in value) for value in ids)
                or len(set(ids)) != len(ids)):
            raise ControllerControlError("ERR_INVALID_REQUEST", "Invalid canonical game IDs.")
        if "observation_known" in fields and type(fields["observation_known"]) is not bool:
            raise ControllerControlError("ERR_INVALID_REQUEST", "Observation flag must be boolean.")
        if fields.get("observation_known", True) is False and ids:
            raise ControllerControlError("ERR_INVALID_REQUEST", "Unknown observation cannot contain game IDs.")
    # Basic type validation per contract
    if operation == "profile_get":
        if not isinstance(fields.get("name"), str) or not fields["name"]:
            raise ControllerControlError("ERR_INVALID_REQUEST", "profile_get requires non-empty name.")
    elif operation == "profile_select":
        if not isinstance(fields.get("name"), str) or not fields["name"]:
            raise ControllerControlError("ERR_INVALID_REQUEST", "profile_select requires non-empty name.")
        if not _is_hex(fields.get("expected_revision", ""), 64):
            raise ControllerControlError("ERR_INVALID_REQUEST", "profile_select requires 64-hex expected_revision.")
    elif operation == "profile_update":
        if not isinstance(fields.get("name"), str) or not fields["name"]:
            raise ControllerControlError("ERR_INVALID_REQUEST", "profile_update requires non-empty name.")
        if not isinstance(fields.get("profile"), dict):
            raise ControllerControlError("ERR_INVALID_REQUEST", "profile_update requires profile object.")
        if not _is_hex(fields.get("expected_revision", ""), 64):
            raise ControllerControlError("ERR_INVALID_REQUEST", "profile_update requires 64-hex expected_revision.")


class ControllerControlClient:
    """Pure client for the UnifiedController control socket. No I/O in constructor."""

    def __init__(self, socket_path: str | os.PathLike[str] | None = None, timeout: float = DEFAULT_TIMEOUT):
        self._socket_path = Path(socket_path) if socket_path is not None else None
        try:
            t = float(timeout)
        except (TypeError, ValueError):
            raise ControllerControlError("ERR_INVALID_REQUEST", "Timeout must be a finite number.")
        if not math.isfinite(t) or t <= 0 or t > MAX_TIMEOUT:
            raise ControllerControlError("ERR_INVALID_REQUEST", "Timeout must be finite and bounded.")
        self._timeout = t

    @property
    def socket_path(self) -> Path:
        return self._socket_path if self._socket_path is not None else _default_socket_path()

    def request(self, operation: str, **fields: Any) -> dict[str, Any]:
        """Send one validated request and return validated result or raise ControllerControlError."""
        if not isinstance(operation, str):
            raise ControllerControlError("ERR_INVALID_OPERATION", "Operation must be a string.")
        _validate_operation_args(operation, fields)

        request_id = secrets.token_hex(16)
        payload: dict[str, Any] = {"version": PROTOCOL_VERSION, "request_id": request_id, "operation": operation}
        payload.update(fields)

        try:
            raw_request = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        except Exception as err:
            raise ControllerControlError("ERR_INVALID_REQUEST", "Request is not serializable.") from err
        if len(raw_request) > MAX_MESSAGE_BYTES:
            raise ControllerControlError("ERR_INVALID_REQUEST", "Request exceeds maximum size.")

        path = self.socket_path
        _validate_socket_path(path)

        is_mutation = operation in MUTATION_OPERATIONS
        sent = False
        deadline = time.monotonic() + self._timeout
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._set_remaining_timeout(sock, deadline, is_mutation=is_mutation, phase="connect")
            try:
                sock.connect(os.fspath(path))
            except FileNotFoundError as err:
                raise ControllerControlError("ERR_NOT_AVAILABLE", "Control socket not available.") from err
            except ConnectionRefusedError as err:
                raise ControllerControlError("ERR_NOT_AVAILABLE", "Control socket refused connection.") from err
            except PermissionError as err:
                raise ControllerControlError("ERR_PERMISSION_DENIED", "Permission denied connecting to control socket.") from err
            except socket.timeout as err:
                raise ControllerControlError("ERR_TIMEOUT", "Timed out connecting to control socket.") from err
            except OSError as err:
                raise ControllerControlError("ERR_NOT_AVAILABLE", "Unable to connect to control socket.") from err

            try:
                self._set_remaining_timeout(sock, deadline, is_mutation=is_mutation, phase="send")
                sock.sendall(raw_request)
                sent = True
                sock.shutdown(socket.SHUT_WR)
            except socket.timeout as err:
                raise ControllerControlError("ERR_TIMEOUT", "Timed out sending control request.", uncertain=is_mutation) from err
            except OSError as err:
                # After connect succeeded, any send-attempt OSError on a mutation may mean
                # partial/unknown bytes reached the server; never report definite failure.
                raise ControllerControlError(
                    "ERR_SEND_FAILED", "Unable to send control request.", uncertain=is_mutation
                ) from err

            raw_response = self._read_response(sock, is_mutation=is_mutation, deadline=deadline)
        finally:
            sock.close()

        result = self._parse_response(raw_response, request_id=request_id, is_mutation=is_mutation)
        if operation in ("output_get", "output_set", "output_game_state"):
            try:
                validate_output_state(result)
            except ControllerControlError as exc:
                raise ControllerControlError(exc.code, str(exc), uncertain=is_mutation) from exc
        return result

    def _set_remaining_timeout(self, sock: socket.socket, deadline: float, *, is_mutation: bool, phase: str) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            uncertain = is_mutation and phase in ("send", "read")
            raise ControllerControlError("ERR_TIMEOUT", "Control request deadline exceeded.", uncertain=uncertain)
        sock.settimeout(remaining)

    def _read_response(self, sock: socket.socket, *, is_mutation: bool, deadline: float) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            self._set_remaining_timeout(sock, deadline, is_mutation=is_mutation, phase="read")
            try:
                chunk = sock.recv(4096)
            except socket.timeout as err:
                raise ControllerControlError("ERR_TIMEOUT", "Timed out waiting for control response.", uncertain=is_mutation) from err
            except OSError as err:
                raise ControllerControlError("ERR_READ_FAILED", "Unable to read control response.", uncertain=is_mutation) from err
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_MESSAGE_BYTES:
                raise ControllerControlError("ERR_PROTOCOL_ERROR", "Control response exceeds maximum size.", uncertain=is_mutation)
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        data = b"".join(chunks)
        if not data:
            raise ControllerControlError("ERR_PROTOCOL_ERROR", "Empty control response.", uncertain=is_mutation)
        line = data.split(b"\n", 1)[0]
        if len(line) > MAX_MESSAGE_BYTES:
            raise ControllerControlError("ERR_PROTOCOL_ERROR", "Control response exceeds maximum size.", uncertain=is_mutation)
        return line

    def _parse_response(self, raw: bytes, *, request_id: str, is_mutation: bool) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as err:
            raise ControllerControlError("ERR_PROTOCOL_ERROR", "Invalid control response.", uncertain=is_mutation) from err
        if not isinstance(payload, dict):
            raise ControllerControlError("ERR_PROTOCOL_ERROR", "Invalid control response.", uncertain=is_mutation)

        if payload.get("version") != PROTOCOL_VERSION:
            raise ControllerControlError("ERR_PROTOCOL_ERROR", "Unsupported control protocol version.", uncertain=is_mutation)
        resp_id = payload.get("request_id")
        if not (isinstance(resp_id, str) and _REQUEST_ID_RE.fullmatch(resp_id)) or resp_id != request_id:
            raise ControllerControlError("ERR_PROTOCOL_ERROR", "Mismatched control response id.", uncertain=is_mutation)

        ok = payload.get("ok")
        if ok is True:
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ControllerControlError("ERR_PROTOCOL_ERROR", "Control result missing or invalid.", uncertain=is_mutation)
            return result
        if ok is False:
            err_code = payload.get("error")
            if not isinstance(err_code, str) or not _REMOTE_ERR_RE.fullmatch(err_code):
                raise ControllerControlError("ERR_PROTOCOL_ERROR", "Control error invalid.", uncertain=is_mutation)
            # Some server codes may indicate accepted work still running for mutations.
            uncertain = bool(is_mutation and err_code in {"ERR_TIMEOUT", "ERR_OUTCOME_PENDING", "ERR_SHUTDOWN"})
            raise ControllerControlError(err_code, err_code, uncertain=uncertain)

        raise ControllerControlError("ERR_PROTOCOL_ERROR", "Control response missing ok flag.", uncertain=is_mutation)

