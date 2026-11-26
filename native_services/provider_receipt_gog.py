"""Verified GOG installed-metadata receipt adapter (owned artifact).

Consumes exact selected provider/product/destination plus real engine installed
metadata/manifest evidence and returns a receipt accepted by the existing
InstallReceiptRegistry only when installation is corroborated on-disk.

No title/exit-code heuristics. No auth/network/download behavior.
"""
from __future__ import annotations

import copy
import os
import re
import socket
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

_PROVIDER = "gog"
_GOG_ID = re.compile(r"[1-9][0-9]{0,19}")
_INCOMPLETE_MARKERS = (
    ".nexus-update-incomplete-v1",
    ".gogdl-resume",
    ".xodus-streaming-tmp.msixvc",
)


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _record_product_id(record: Mapping[str, Any]) -> Optional[str]:
    # gog_store installed entries use appName/id; per-install goggame-*.info uses
    # rootGameId/gameId; gogdl import output uses appName (rootGameId). Prefer
    # stable product identifiers and root/base id over per-build gameId when both
    # are present.
    for key in ("appName", "app_name", "rootGameId", "root_game_id", "id", "product_id", "gameID", "game_id", "gameId"):
        value = _text(record.get(key))
        if value and _GOG_ID.fullmatch(value):
            return value
    return None


def _record_install_path(record: Mapping[str, Any]) -> Optional[str]:
    for key in ("install_path", "installPath", "install_dir", "installDir", "path", "folder_name"):
        value = _text(record.get(key))
        if value:
            return value
    return None


def _normalize_task_path(value: str) -> str:
    # heroic-gogdl normalizes Windows backslashes in task paths.
    return value.replace("\\", "/")


def _record_executable(record: Mapping[str, Any]) -> Optional[Any]:
    """Return executable proof.

    For direct executable fields returns text path. For task-based proof returns
    {"task_path": ..., "working_dir": ...|None} using direct GOGdl semantics:
    executable is install_root + task.path; working_dir is separate metadata.
    """
    for key in ("executable", "exe", "launch_executable", "launchExecutable"):
        if key in record:
            value = record.get(key)
            if isinstance(value, Mapping):
                return value
            return _text(value) if _text(value) is not None else value
    # GOG/Heroic metadata may provide playTasks; only accept an unambiguous one.
    for key in ("playTasks", "play_tasks", "playtasks", "tasks"):
        tasks = record.get(key)
        if tasks is None:
            continue
        if not isinstance(tasks, list):
            return {"__ambiguous__": "malformed_play_tasks"}
        candidates = []
        for task in tasks:
            if not isinstance(task, Mapping):
                return {"__ambiguous__": "malformed_play_tasks"}
            is_primary = task.get("isPrimary") if "isPrimary" in task else task.get("is_primary")
            task_type = _text(task.get("type")) or _text(task.get("category"))
            raw_path = task.get("path") or task.get("executable")
            if not _text(raw_path):
                continue
            # Only file-backed executable tasks are installation proof.
            # URLTask (or other non-file tasks) must not be treated as proof even
            # if they carry a path/link field.
            if task_type == "URLTask":
                continue
            if task_type is not None and task_type not in {"FileTask", "primary", "game"} and is_primary is not True and len(tasks) != 1:
                continue
            working_dir = task.get("workingDir") if "workingDir" in task else task.get("working_dir")
            candidate = (is_primary is True, _text(raw_path), _text(working_dir) if _text(working_dir) is not None else working_dir)
            if is_primary is True or task_type in {"FileTask", "primary", "game"} or len(tasks) == 1:
                candidates.append(candidate)
        if not candidates:
            return None
        primary = [c for c in candidates if c[0]]
        chosen = primary if primary else candidates
        if len(chosen) != 1:
            return {"__ambiguous__": "ambiguous_primary_tasks"}
        _, task_path, working_dir = chosen[0]
        return {"task_path": _normalize_task_path(task_path), "working_dir": _normalize_task_path(working_dir) if isinstance(working_dir, str) and working_dir else working_dir}
    return None


def _is_safe_absolute(path_str: str, label: str) -> Tuple[Optional[Path], Optional[str]]:
    if "\x00" in path_str:
        return None, f"{label} contains NUL"
    p = Path(path_str)
    if not p.is_absolute():
        return None, f"{label} must be absolute"
    try:
        resolved = p.resolve(strict=False)
    except Exception as exc:
        return None, f"{label} could not be resolved: {exc.__class__.__name__}"
    if not resolved.is_absolute():
        return None, f"{label} resolved to a non-absolute path"
    return resolved, None


def _select_record(metadata: Any, selected_product_id: str) -> Tuple[Optional[Mapping[str, Any]], Optional[str], Optional[str]]:
    if isinstance(metadata, Mapping):
        # direct entry (installed store, goggame-*.info, or gogdl import output)
        if _record_product_id(metadata) == selected_product_id or any(
            k in metadata for k in (
                "install_path", "installPath", "executable", "appName", "app_name",
                "rootGameId", "gameId", "playTasks", "tasks",
            )
        ):
            pid = _record_product_id(metadata)
            if pid is None:
                return None, "metadata_missing_product_id", "installed metadata does not identify a GOG product id"
            if pid != selected_product_id:
                return None, "selected_product_mismatch", f"metadata product {pid} != selected {selected_product_id}"
            return metadata, None, None
        # container forms
        for key in ("installed", "games", "library", "items"):
            items = metadata.get(key)
            if isinstance(items, list):
                matches = [it for it in items if isinstance(it, Mapping) and _record_product_id(it) == selected_product_id]
                if len(matches) == 1:
                    return matches[0], None, None
                if len(matches) > 1:
                    return None, "ambiguous_product_records", f"multiple metadata entries match selected product {selected_product_id}"
        if isinstance(metadata.get("installed"), dict):
            items = list(metadata["installed"].values())
            matches = [it for it in items if isinstance(it, Mapping) and _record_product_id(it) == selected_product_id]
            if len(matches) == 1:
                return matches[0], None, None
            if len(matches) > 1:
                return None, "ambiguous_product_records", f"multiple metadata entries match selected product {selected_product_id}"
        # mapping keyed by product id
        direct = metadata.get(selected_product_id)
        if isinstance(direct, Mapping):
            rec = {"appName": selected_product_id, **copy.deepcopy(direct)}
            pid = _record_product_id(rec)
            if pid != selected_product_id:
                return None, "selected_product_mismatch", "keyed metadata record does not match selected product"
            return rec, None, None
        return None, "selected_product_not_found", f"no installed metadata entry for selected product {selected_product_id}"
    if isinstance(metadata, list):
        matches = [it for it in metadata if isinstance(it, Mapping) and _record_product_id(it) == selected_product_id]
        if len(matches) == 1:
            return matches[0], None, None
        if len(matches) > 1:
            return None, "ambiguous_product_records", f"multiple metadata entries match selected product {selected_product_id}"
        return None, "selected_product_not_found", f"no installed metadata entry for selected product {selected_product_id}"
    return None, "metadata_invalid", "installed metadata must be an object or list"


def _reject_markers(root: Path) -> Optional[str]:
    for marker in _INCOMPLETE_MARKERS:
        try:
            if (root / marker).exists():
                return marker
        except OSError:
            return marker
    return None


def _path_contained(child: Path, root: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except Exception:
        return False


def verify_gog_install(
    metadata: Any,
    *,
    selected_product_id: Any,
    selected_destination: Any,
    host: Optional[str] = None,
    account_id: Optional[str] = None,
    expected_title: Optional[str] = None,
) -> Tuple[Optional[dict], str, str]:
    """Verify a GOG install from real installed metadata and return a registry-ready receipt.

    Returns (receipt_dict_or_none, reason_code, detail).
    """
    product = _text(selected_product_id)
    if not product or not _GOG_ID.fullmatch(product):
        return None, "invalid_selected_product", "selected GOG product id must be a positive numeric string"

    dest_text = _text(selected_destination)
    if not dest_text:
        return None, "invalid_selected_destination", "selected destination is required"
    selected_root, err = _is_safe_absolute(dest_text, "selected destination")
    if err or selected_root is None:
        return None, "unsafe_selected_destination", err or "invalid selected destination"

    record, reason, detail = _select_record(metadata, product)
    if reason or record is None:
        return None, reason or "metadata_unusable", detail or "metadata unusable"

    # reject obvious DLC-only records when no install/executable proof is present
    is_dlc = record.get("is_dlc") if "is_dlc" in record else record.get("isDlc")
    exe_raw = _record_executable(record)
    if is_dlc is True and not _text(exe_raw):
        return None, "dlc_without_executable_proof", "DLC metadata does not prove an installed playable executable"

    meta_install_text = _record_install_path(record)
    if meta_install_text:
        meta_root, err = _is_safe_absolute(meta_install_text, "metadata install path")
        if err or meta_root is None:
            return None, "unsafe_metadata_install_path", err or "invalid metadata install path"
        # selected destination and metadata must agree on the same install root
        if meta_root != selected_root:
            return None, "destination_mismatch", "metadata install path does not match selected destination"
    else:
        # Producer-shaped per-install goggame-*.info and gogdl import output
        # generally lack install_path; Waystone supplies the exact selected
        # destination/CLI plan provenance separately. Accept only when the record
        # is a corroborated game-info/import record for the selected product with
        # unambiguous executable/playTask proof (no arbitrary scan/defaulting).
        has_game_info_identity = (
            any(k in record for k in ("rootGameId", "gameId", "gameID"))
            or (record.get("appName") and _GOG_ID.fullmatch(record["appName"]))
        )
        has_tasks_schema = any(k in record for k in ("playTasks", "play_tasks", "playtasks", "tasks"))
        if not (has_game_info_identity and has_tasks_schema):
            return None, "missing_install_path", "installed metadata missing install path"

    try:
        if not selected_root.exists() or not selected_root.is_dir():
            return None, "install_path_missing", "install path is missing or not a directory"
    except OSError:
        return None, "install_path_unavailable", "install path could not be inspected"

    marker = _reject_markers(selected_root)
    if marker:
        return None, "incomplete_install", f"incomplete marker present: {marker}"

    if isinstance(exe_raw, Mapping) and "__ambiguous__" in exe_raw:
        code = exe_raw["__ambiguous__"]
        if code == "malformed_play_tasks":
            return None, "malformed_primary_tasks", "installed metadata play tasks are malformed"
        return None, "ambiguous_primary_tasks", "installed metadata does not identify an unambiguous primary executable"

    task_working_dir = None
    if isinstance(exe_raw, Mapping) and "task_path" in exe_raw:
        exe_text = _text(exe_raw.get("task_path"))
        wd_raw = exe_raw.get("working_dir")
        if wd_raw is not None and wd_raw != "":
            if not isinstance(wd_raw, str):
                return None, "unsafe_working_dir", "workingDir must be a string"
            wd_text = _normalize_task_path(wd_raw).strip()
            if "\x00" in wd_text:
                return None, "unsafe_working_dir", "workingDir contains NUL"
            wd_path = Path(wd_text)
            if wd_path.is_absolute():
                wd_candidate = wd_path
            else:
                wd_candidate = selected_root / wd_path
            wd_resolved, err = _is_safe_absolute(str(wd_candidate), "workingDir")
            if err or wd_resolved is None:
                return None, "unsafe_working_dir", err or "invalid workingDir"
            if not _path_contained(wd_resolved, selected_root):
                return None, "working_dir_escape", "workingDir resolves outside the selected install root"
            task_working_dir = str(wd_resolved)
    else:
        exe_text = _text(exe_raw)

    if not exe_text:
        return None, "missing_executable", "installed metadata missing executable"
    if "\x00" in exe_text:
        return None, "unsafe_executable", "executable contains NUL"
    # Direct GOGdl path semantics: executable = install/path root + primary task path.
    # workingDir is separate and must not be joined into the executable path.
    exe_text = _normalize_task_path(exe_text) if not isinstance(exe_raw, Mapping) or "task_path" not in exe_raw else exe_text
    exe_path = Path(exe_text)
    if not exe_path.is_absolute():
        exe_path = selected_root / exe_path
    exe_resolved, err = _is_safe_absolute(str(exe_path), "executable")
    if err or exe_resolved is None:
        return None, "unsafe_executable", err or "invalid executable path"
    if not _path_contained(exe_resolved, selected_root):
        return None, "executable_escape", "executable resolves outside the selected install root"
    try:
        if not exe_resolved.exists() or not exe_resolved.is_file():
            # Case-insensitive fallback (gogdl launch semantics) within install root.
            rel = None
            try:
                rel = exe_resolved.relative_to(selected_root)
            except Exception:
                rel = None
            found = None
            if rel is not None:
                cur = selected_root
                ok = True
                for part in rel.parts:
                    try:
                        entries = list(cur.iterdir())
                    except OSError:
                        ok = False
                        break
                    match = None
                    lower = part.lower()
                    for ent in entries:
                        if ent.name == part:
                            match = ent
                            break
                    if match is None:
                        for ent in entries:
                            if ent.name.lower() == lower:
                                match = ent
                                break
                    if match is None:
                        ok = False
                        break
                    cur = match
                if ok and cur.exists() and cur.is_file() and _path_contained(cur.resolve(strict=False), selected_root):
                    found = cur.resolve(strict=False)
            if found is not None:
                exe_resolved = found
            else:
                return None, "executable_missing", "executable is missing or not a regular file"
    except OSError:
        return None, "executable_unavailable", "executable could not be inspected"

    # Title is display metadata, not installation authority. A user rename or
    # differing expected title must not override exact SKU/file identity.

    resolved_host = _text(host) or socket.gethostname()
    fs_device = None
    try:
        fs_device = selected_root.stat().st_dev
    except OSError:
        fs_device = None

    receipt = {
        "provider": _PROVIDER,
        "product_id": product,
        "host": resolved_host,
        "install_path": str(selected_root),
        "executable": str(exe_resolved),
        "state": "files_verified",
        "playability": "unknown",
        "selected_product_id": product,
        "verified_by": "gog-installed-metadata",
        "install_root": str(selected_root),
    }
    acct = _text(account_id)
    if acct:
        receipt["account_id"] = acct
    if task_working_dir is not None:
        receipt["working_dir"] = task_working_dir
    if fs_device is not None:
        receipt["filesystem_device"] = fs_device
    # Harmless non-secret corroboration from metadata may be included; baseline
    # registry normalizes to its accepted field set.
    for key in ("buildId", "build_id", "version", "versionName", "platform", "installedLanguage", "language"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            receipt[key] = value.strip()[:128]
    return receipt, "verified", "verified installed files from GOG metadata" 
