"""Read-only Waystone boundary for the native Unified Controller status socket.

Source audit: research/unified-controller-integration-20261116.md. The existing
Linux app now exposes an optional same-UID private runtime AF_UNIX status
endpoint outside the realtime thread. This module reads only that sanitized
status contract. It does NOT start the controller/CLI, read private profiles or
config, infer health from lock files, mutate runtime/profile state, or invent
unsupported APIs.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import stat as statmod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CONTROLLER_ID = "unified-controller"
PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1024
MAX_RESPONSE_BYTES = 16384
CONNECT_TIMEOUT = 0.25
IO_TIMEOUT = 0.5

BLOCKER_UNAVAILABLE = (
    "No supported read-only controller status endpoint is available. "
    "Absence cannot distinguish an older running build from a stopped/new build."
)
BLOCKER_NOT_RUNNING = "Verified native status endpoint is absent or refused; controller is not running."
BLOCKER_TIMEOUT = "Timed out while reading the native status endpoint."
BLOCKER_PERMISSION = "Permission denied while accessing the native status endpoint."
BLOCKER_PROTOCOL = "Native status endpoint returned an unsupported or invalid protocol response."
BLOCKER_IDENTITY = "Native status endpoint identity did not match the expected controller."


@dataclass(frozen=True)
class ControllerStatus:
    """Unknown runtime facts stay unknown, never false or synthetic ready state."""

    controller_id: str = CONTROLLER_ID
    state: str = "unavailable"
    available: bool = False
    runtime_running: bool | None = None
    active_profile: str | None = None
    profiles: tuple[str, ...] | None = None
    status_supported: bool = False
    profile_selection_supported: bool = False
    action_supported: bool = False
    read_only: bool = True
    source_audit: str = "research/unified-controller-integration-20261116.md"
    reason_code: str = "native_status_profile_api_missing"
    blocker: str = BLOCKER_UNAVAILABLE

    def __post_init__(self) -> None:
        if self.runtime_running is False:
            raise ValueError("runtime_running must be None or True while unavailable")
        if self.profiles is not None and any(
            not isinstance(p, str) or not p for p in self.profiles
        ):
            raise ValueError("profiles may be None or a tuple of non-empty strings")
        if self.available and self.profiles is not None and len(self.profiles) == 0:
            raise ValueError("empty profile inventory requires an explicit supported response")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.profiles is not None:
            data["profiles"] = list(self.profiles)
        return data


class ControllerBridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnifiedControllerBridge:
    def __init__(
        self,
        controller_id: str = CONTROLLER_ID,
        socket_path: str | Path | None = None,
    ) -> None:
        self._controller_id = _validated_controller_id(controller_id)
        self._socket_path = Path(socket_path) if socket_path is not None else _default_socket_path()

    def status(self) -> ControllerStatus:
        try:
            payload = self._read_status_payload()
        except ControllerBridgeError as err:
            return _status_from_error(err)
        return _status_from_payload(payload)

    def list_profiles(self) -> tuple[str, ...]:
        payload = self._require_payload("list_profiles")
        profiles = payload.get("profiles")
        if not isinstance(profiles, list) or not profiles or not all(
            isinstance(p, str) and p for p in profiles
        ):
            raise ControllerBridgeError("unsupported", "Profile inventory is not supported by this endpoint.")
        return tuple(profiles)

    def select_profile(self, profile: str, *, explicit_user_action: bool = False) -> str:
        _validated_profile_label(profile)
        if not explicit_user_action:
            raise ControllerBridgeError("explicit_intent_required", "Profile selection requires explicit user intent.")
        self._require_payload("select_profile")
        raise ControllerBridgeError("unsupported", "Profile selection is read-only unsupported.")

    def invoke_action(self, action: str, *, explicit_user_action: bool = False) -> str:
        _validated_profile_label(action)
        if not explicit_user_action:
            raise ControllerBridgeError("explicit_intent_required", "Controller actions require explicit user intent.")
        self._require_payload("invoke_action")
        raise ControllerBridgeError("unsupported", "Controller actions are unsupported by this read-only bridge.")

    def _require_payload(self, operation: str) -> dict[str, Any]:
        try:
            payload = self._read_status_payload()
        except ControllerBridgeError as err:
            if err.code == "not_running":
                raise ControllerBridgeError("not_running", err.message) from err
            if err.code == "timeout":
                raise ControllerBridgeError("timeout", err.message) from err
            if err.code == "permission_denied":
                raise ControllerBridgeError("permission_denied", err.message) from err
            if err.code in {"unsupported_protocol", "identity_mismatch"}:
                raise ControllerBridgeError(err.code, err.message) from err
            raise ControllerBridgeError("unsupported", err.message) from err
        return payload

    def _read_status_payload(self) -> dict[str, Any]:
        path = self._socket_path
        try:
            st = os.lstat(path)
        except FileNotFoundError as err:
            raise ControllerBridgeError("not_running", BLOCKER_NOT_RUNNING) from err
        except PermissionError as err:
            raise ControllerBridgeError("permission_denied", BLOCKER_PERMISSION) from err
        except OSError as err:
            raise ControllerBridgeError("unavailable", f"Unable to inspect status endpoint: {err}") from err

        if not statmod.S_ISSOCK(st.st_mode):
            raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise ControllerBridgeError("permission_denied", BLOCKER_PERMISSION)

        request_id = secrets.token_hex(8)
        request = json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "operation": "status",
                "controller_id": self._controller_id,
                "request_id": request_id,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(request) > MAX_REQUEST_BYTES:
            raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(CONNECT_TIMEOUT)
            try:
                sock.connect(os.fspath(path))
            except FileNotFoundError as err:
                raise ControllerBridgeError("not_running", BLOCKER_NOT_RUNNING) from err
            except ConnectionRefusedError as err:
                raise ControllerBridgeError("not_running", BLOCKER_NOT_RUNNING) from err
            except PermissionError as err:
                raise ControllerBridgeError("permission_denied", BLOCKER_PERMISSION) from err
            except socket.timeout as err:
                raise ControllerBridgeError("timeout", BLOCKER_TIMEOUT) from err
            except OSError as err:
                raise ControllerBridgeError("unavailable", f"Unable to connect to status endpoint: {err}") from err

            sock.settimeout(IO_TIMEOUT)
            try:
                sock.sendall(request)
                sock.shutdown(socket.SHUT_WR)
            except socket.timeout as err:
                raise ControllerBridgeError("timeout", BLOCKER_TIMEOUT) from err
            except OSError as err:
                raise ControllerBridgeError("unavailable", f"Unable to send status request: {err}") from err

            chunks: list[bytes] = []
            total = 0
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout as err:
                    raise ControllerBridgeError("timeout", BLOCKER_TIMEOUT) from err
                except OSError as err:
                    raise ControllerBridgeError("unavailable", f"Unable to read status response: {err}") from err
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
                chunks.append(chunk)
        finally:
            sock.close()

        raw = b"".join(chunks)
        if not raw:
            raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as err:
            raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL) from err
        if not isinstance(payload, dict):
            raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)

        _validate_response(payload, self._controller_id, request_id)
        return payload


def _default_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "waystone-unified-controller" / "status.sock"
    return Path(f"/run/user/{os.getuid()}") / "waystone-unified-controller" / "status.sock"


def _validated_controller_id(controller_id: str) -> str:
    if controller_id != CONTROLLER_ID:
        raise ControllerBridgeError("identity_mismatch", BLOCKER_IDENTITY)
    return controller_id


def _validated_profile_label(label: str) -> str:
    if not isinstance(label, str) or not label.strip():
        raise ControllerBridgeError("invalid_profile", "A non-empty label is required.")
    if len(label) > 128 or any((c <= "\x1f") or ("\x7f" <= c <= "\x9f") for c in label):
        raise ControllerBridgeError("invalid_profile", "Label contains unsupported characters.")
    return label


def _validate_response(payload: dict[str, Any], controller_id: str, request_id: str) -> None:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
    if payload.get("operation") != "status":
        raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
    if payload.get("controller_id") != controller_id:
        raise ControllerBridgeError("identity_mismatch", BLOCKER_IDENTITY)
    if payload.get("request_id") != request_id:
        raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
    running = payload.get("runtime_running")
    if running is not None and running is not True and running is not False:
        raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
    profiles = payload.get("profiles")
    if profiles is not None:
        if not isinstance(profiles, list) or not all(isinstance(p, str) and p for p in profiles):
            raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)
    active = payload.get("active_profile")
    if active is not None and not isinstance(active, str):
        raise ControllerBridgeError("unsupported_protocol", BLOCKER_PROTOCOL)


def _status_from_payload(payload: dict[str, Any]) -> ControllerStatus:
    profiles = payload.get("profiles")
    prof_tuple = tuple(profiles) if isinstance(profiles, list) else None
    running = payload.get("runtime_running")
    state = payload.get("state")
    if state not in {"available", "running", "stopped"}:
        state = "running" if running is True else "available"
    return ControllerStatus(
        controller_id=CONTROLLER_ID,
        state=state,
        available=True,
        runtime_running=running if running is True or running is False else None,
        active_profile=payload.get("active_profile") if isinstance(payload.get("active_profile"), str) else None,
        profiles=prof_tuple,
        status_supported=True,
        profile_selection_supported=False,
        action_supported=False,
        read_only=True,
        reason_code="ok",
        blocker="",
    )


def _status_from_error(err: ControllerBridgeError) -> ControllerStatus:
    if err.code == "not_running":
        return ControllerStatus(state="not_running", reason_code="endpoint_not_running", blocker=err.message)
    if err.code == "timeout":
        return ControllerStatus(state="timeout", reason_code="timeout", blocker=err.message)
    if err.code == "permission_denied":
        return ControllerStatus(state="permission_denied", reason_code="permission_denied", blocker=err.message)
    if err.code in {"unsupported_protocol", "identity_mismatch"}:
        return ControllerStatus(state="unsupported_protocol" if err.code == "unsupported_protocol" else "identity_mismatch", reason_code=err.code, blocker=err.message)
    return ControllerStatus(state="unavailable", reason_code="native_status_profile_api_missing", blocker=err.message)
