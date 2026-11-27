"""Offline typed launch planner for direct Waystone-managed Legendary.

This module plans argv/env/cwd/identity for `legendary launch` without spawning
processes, scanning libraries, reading auth/user data, or constructing Heroic
frontend URIs. It reads only an explicit `config_root/installed.json` snapshot.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from native_services.platform import is_windows, is_executable_file

try:
    from native_services.launch_profiles import LaunchIntent
except Exception:  # pragma: no cover - isolated artifact execution fallback
    @dataclass(frozen=True)
    class LaunchIntent:  # type: ignore[no-redef]
        profile_id: str
        target_id: str
        target_path: str | None
        executable: str
        argv: tuple[str, ...]
        cwd: str | None
        env: dict[str, str]
        target_host: str | None
        verified_contract: bool
        error: str | None = None

        def to_dict(self) -> dict:
            return {
                "profile_id": self.profile_id,
                "target_id": self.target_id,
                "target_path": self.target_path,
                "executable": self.executable,
                "argv": list(self.argv),
                "cwd": self.cwd,
                "env": dict(self.env),
                "target_host": self.target_host,
                "verified_contract": self.verified_contract,
                "error": self.error,
            }

MAX_INSTALLED_JSON_BYTES = 2 * 1024 * 1024
MAX_STR_LEN = 4096
_APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class EpicDirectLaunchError(ValueError):
    """Safe planning/validation failure."""


class EpicDirectLaunchNeedsRuntimeProfile(EpicDirectLaunchError):
    """Explicit unsupported/unspecified runtime state; never pretend support."""


@dataclass(frozen=True)
class RuntimeSelection:
    kind: str  # "native" | "wine"
    wine_binary: str | None = None
    wine_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"native", "wine"}:
            raise EpicDirectLaunchError("runtime selection kind must be 'native' or 'wine'")
        if self.kind == "native":
            if self.wine_binary is not None or self.wine_prefix is not None:
                raise EpicDirectLaunchError("native runtime selection must not include wine fields")
            return
        # wine path requires explicit values (Proton/UMU equivalence is not invented)
        if self.wine_binary is None or self.wine_prefix is None:
            raise EpicDirectLaunchNeedsRuntimeProfile(
                "Windows runtime requires explicit wine_binary and wine_prefix"
            )
        _validate_abs_str(self.wine_binary, "wine_binary")
        _validate_abs_str(self.wine_prefix, "wine_prefix")


@dataclass(frozen=True)
class PreservedRuntimeSettings:
    """Explicit caller-preserved settings only; never guessed defaults."""

    wrapper: str | None = None
    language: str | None = None
    offline: bool = False
    skip_version_check: bool = False
    override_username: str | None = None
    extra_env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("wrapper", self.wrapper),
            ("language", self.language),
            ("override_username", self.override_username),
        ):
            if value is not None:
                _validate_clean_str(value, name)
                if len(value) > MAX_STR_LEN:
                    raise EpicDirectLaunchError(f"{name} is too large")
        if not isinstance(self.offline, bool) or not isinstance(self.skip_version_check, bool):
            raise EpicDirectLaunchError("boolean runtime settings must be bool")
        if not isinstance(self.extra_env, tuple):
            raise EpicDirectLaunchError("extra_env must be a tuple of key/value pairs")
        normalized: list[tuple[str, str]] = []
        for item in self.extra_env:
            if not isinstance(item, tuple) or len(item) != 2:
                raise EpicDirectLaunchError("extra_env items must be (key, value) tuples")
            k, v = item
            _validate_clean_str(k, "extra_env key")
            _validate_clean_str(v, "extra_env value", allow_empty=True)
            if k == "LEGENDARY_CONFIG_PATH":
                raise EpicDirectLaunchError("extra_env cannot override LEGENDARY_CONFIG_PATH")
            normalized.append((k, v))
        object.__setattr__(self, "extra_env", tuple(normalized))


@dataclass(frozen=True)
class GameIdentity:
    provider: str
    product: str
    app_name: str
    title: str | None
    install_path: str
    executable: str
    platform: str | None
    metadata_source: str
    qualified_evidence: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "product": self.product,
            "app_name": self.app_name,
            "title": self.title,
            "install_path": self.install_path,
            "executable": self.executable,
            "platform": self.platform,
            "metadata_source": self.metadata_source,
            "qualified_evidence": list(self.qualified_evidence),
        }


@dataclass(frozen=True)
class DirectLaunchPlan:
    identity: GameIdentity
    executable: str
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    launch_intent: LaunchIntent
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "identity": self.identity.to_dict(),
            "executable": self.executable,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "env": dict(self.env),
            "launch_intent": self.launch_intent.to_dict(),
            "limitations": list(self.limitations),
        }


def _validate_clean_str(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise EpicDirectLaunchError(f"{name} must be a string")
    if not allow_empty and value == "":
        raise EpicDirectLaunchError(f"{name} cannot be empty")
    if len(value) > MAX_STR_LEN:
        raise EpicDirectLaunchError(f"{name} is too large")
    if "\x00" in value or _CONTROL_RE.search(value):
        raise EpicDirectLaunchError(f"{name} contains control characters")
    if value != value.strip():
        # preserve content boundaries; avoid ambiguous whitespace
        raise EpicDirectLaunchError(f"{name} has leading/trailing whitespace")
    return value


def _validate_abs_str(value: Any, name: str) -> str:
    s = _validate_clean_str(value, name)
    p = Path(s)
    if not p.is_absolute():
        raise EpicDirectLaunchError(f"{name} must be an absolute path")
    return s


def _reject_duplicate_keys_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise EpicDirectLaunchError("installed.json contains duplicate keys")
        out[k] = v
    return out


def _lstat_regular_no_symlink(path: Path, label: str) -> os.stat_result:
    try:
        st = os.lstat(path)
    except FileNotFoundError as e:
        raise EpicDirectLaunchError(f"{label} does not exist") from e
    except OSError as e:
        raise EpicDirectLaunchError(f"cannot stat {label}") from e
    if stat.S_ISLNK(st.st_mode):
        raise EpicDirectLaunchError(f"{label} must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise EpicDirectLaunchError(f"{label} must be a regular file")
    return st


def _validate_config_root(config_root: str | Path) -> Path:
    root_str = _validate_abs_str(str(config_root) if isinstance(config_root, Path) else config_root, "config_root")
    root = Path(root_str)
    try:
        st = os.lstat(root)
    except FileNotFoundError as e:
        raise EpicDirectLaunchError("config_root does not exist") from e
    except OSError as e:
        raise EpicDirectLaunchError("cannot stat config_root") from e
    if stat.S_ISLNK(st.st_mode):
        raise EpicDirectLaunchError("config_root must not be a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise EpicDirectLaunchError("config_root must be a directory")
    return root


def _load_installed_json(config_root: Path) -> tuple[Path, dict[str, Any]]:
    installed_path = config_root / "installed.json"
    st = _lstat_regular_no_symlink(installed_path, "installed.json")
    if st.st_size <= 0 or st.st_size > MAX_INSTALLED_JSON_BYTES:
        raise EpicDirectLaunchError("installed.json is missing, empty, or oversized")
    try:
        fd = os.open(installed_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        raise EpicDirectLaunchError("cannot open installed.json") from e
    try:
        with os.fdopen(fd, "rb") as f:
            raw = f.read(MAX_INSTALLED_JSON_BYTES + 1)
    except OSError as e:
        raise EpicDirectLaunchError("cannot read installed.json") from e
    if len(raw) > MAX_INSTALLED_JSON_BYTES:
        raise EpicDirectLaunchError("installed.json is oversized")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys_hook)
    except (UnicodeDecodeError, json.JSONDecodeError, EpicDirectLaunchError) as e:
        raise EpicDirectLaunchError("installed.json is malformed") from e
    if not isinstance(data, dict):
        raise EpicDirectLaunchError("installed.json root must be an object")
    return installed_path, data


def _select_installed_entry(installed: Mapping[str, Any], app_name: str) -> dict[str, Any]:
    name = _validate_clean_str(app_name, "app_name")
    if not _APP_NAME_RE.match(name):
        raise EpicDirectLaunchError("app_name format is invalid")
    if name not in installed:
        raise EpicDirectLaunchError("requested app_name is not installed in selected config")
    entry = installed[name]
    if not isinstance(entry, dict):
        raise EpicDirectLaunchError("installed entry must be an object")
    entry_app = entry.get("app_name")
    if entry_app != name:
        raise EpicDirectLaunchError("installed entry app_name does not match selected key")
    if entry.get("is_dlc") is True:
        raise EpicDirectLaunchError("selected app_name is a DLC entry; DLC launch is not supported")
    return entry


def _validate_existing_executable(path: Path, label: str) -> str:
    try:
        st = path.lstat()
    except FileNotFoundError as e:
        raise EpicDirectLaunchError(f"{label} does not exist") from e
    except OSError as e:
        raise EpicDirectLaunchError(f"cannot stat {label}") from e
    if stat.S_ISLNK(st.st_mode):
        raise EpicDirectLaunchError(f"{label} must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise EpicDirectLaunchError(f"{label} must be a regular file")
    if not is_executable_file(path):
        raise EpicDirectLaunchError(f"{label} is not executable")
    return str(path)


def _validate_existing_dir(path: Path, label: str) -> str:
    try:
        st = path.lstat()
    except FileNotFoundError as e:
        raise EpicDirectLaunchError(f"{label} does not exist") from e
    except OSError as e:
        raise EpicDirectLaunchError(f"cannot stat {label}") from e
    if stat.S_ISLNK(st.st_mode):
        raise EpicDirectLaunchError(f"{label} must not be a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise EpicDirectLaunchError(f"{label} must be a directory")
    required_access = os.R_OK if is_windows() else os.X_OK | os.R_OK
    if not os.access(path, required_access):
        raise EpicDirectLaunchError(f"{label} is not accessible")
    return str(path)


def _resolve_install_and_executable(entry: Mapping[str, Any], app_name: str) -> tuple[Path, Path, str, str]:
    install_path_raw = entry.get("install_path")
    executable_raw = entry.get("executable")
    install_path_str = _validate_abs_str(install_path_raw, "install_path")
    install_path = Path(install_path_str).resolve(strict=False)
    if not install_path.is_absolute():
        raise EpicDirectLaunchError("install_path must resolve to an absolute path")
    install_root = _validate_existing_dir(install_path, "install_path")
    exe_str = _validate_clean_str(executable_raw, "executable")
    exe_path = Path(exe_str)
    if exe_path.is_absolute():
        raise EpicDirectLaunchError("entry executable must be relative to install_path")
    # no path traversal / escape: resolve and ensure containment
    resolved_exe = (install_path / exe_path).resolve(strict=False)
    try:
        resolved_exe.relative_to(install_path)
    except ValueError as e:
        raise EpicDirectLaunchError("executable must be contained within install_path") from e
    exe_abs = _validate_existing_executable(resolved_exe, "game executable")
    return Path(install_root), Path(exe_abs), install_root, exe_abs


def _sanitize_optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if value == "" or len(value) > MAX_STR_LEN or "\x00" in value or _CONTROL_RE.search(value):
        return None
    return value


def plan_epic_direct_launch(
    *,
    engine_path: str | Path,
    config_root: str | Path,
    app_name: str,
    runtime: RuntimeSelection,
    preserved: PreservedRuntimeSettings | None = None,
) -> DirectLaunchPlan:
    """Build an immutable direct-launch plan for one exact installed app.

    No process/network/auth/library scanning occurs. Installed metadata is read only
    from config_root/installed.json and corroborated against local filesystem state.
    """
    if preserved is None:
        preserved = PreservedRuntimeSettings()

    engine_abs = _validate_abs_str(str(engine_path) if isinstance(engine_path, Path) else engine_path, "engine_path")
    engine = Path(engine_abs)
    _validate_existing_executable(engine, "engine")

    root = _validate_config_root(config_root)
    installed_path, installed = _load_installed_json(root)
    entry = _select_installed_entry(installed, app_name)
    install_dir, exe_path, install_dir_str, exe_abs = _resolve_install_and_executable(entry, app_name)

    title = _sanitize_optional_str(entry.get("title"), "title")
    platform = _sanitize_optional_str(entry.get("platform"), "platform")
    # Windows-route inference only from explicit entry platform metadata; do not guess.
    is_windows_game = (platform or "").lower() in {"windows", "win32", "win64"}

    argv: list[str] = [str(engine), "launch", app_name]
    env: dict[str, str] = {"LEGENDARY_CONFIG_PATH": str(root)}
    limitations = [
        "Offline planner only: does not validate Epic auth, entitlements, cloud state, or runtime dependencies beyond explicit local checks.",
        "Reads only config_root/installed.json; no title matching, library scanning, or user.json reads.",
        "Does not import or execute arbitrary launch arguments from metadata.",
    ]

    if preserved.offline:
        argv.append("--offline")
    if preserved.skip_version_check:
        argv.append("--skip-version-check")
    if preserved.language is not None:
        argv.extend(["--language", preserved.language])
    if preserved.override_username is not None:
        argv.extend(["--override-username", preserved.override_username])
    if preserved.wrapper is not None:
        argv.extend(["--wrapper", preserved.wrapper])
    for k, v in preserved.extra_env:
        env[k] = v

    if runtime.kind == "native":
        # actual --no-wine contract for native route
        if is_windows_game:
            raise EpicDirectLaunchNeedsRuntimeProfile(
                "installed entry indicates a Windows title; native route is not applicable"
            )
        argv.append("--no-wine")
        qualified = (
            f"selected app_name={app_name} from {installed_path}",
            f"corroborated install_path={install_dir_str}",
            f"corroborated executable={exe_abs}",
            "runtime=native explicit --no-wine",
        )
    else:
        # explicit valid Wine binary and existing selected Wine prefix with source-verified flags
        _validate_existing_executable(Path(runtime.wine_binary or ""), "wine_binary")
        _validate_existing_dir(Path(runtime.wine_prefix or ""), "wine_prefix")
        argv.extend(["--wine", runtime.wine_binary or ""])
        argv.extend(["--wine-prefix", runtime.wine_prefix or ""])
        qualified = (
            f"selected app_name={app_name} from {installed_path}",
            f"corroborated install_path={install_dir_str}",
            f"corroborated executable={exe_abs}",
            "runtime=wine explicit --wine/--wine-prefix",
        )
        limitations.append(
            "Proton/UMU/generic compatibility layers are not treated as equivalent Wine runtimes; only explicit Wine binary+prefix is planned."
        )

    identity = GameIdentity(
        provider="epic",
        product=app_name,
        app_name=app_name,
        title=title,
        install_path=install_dir_str,
        executable=exe_abs,
        platform=platform,
        metadata_source=str(installed_path),
        qualified_evidence=tuple(qualified),
    )

    # Build a LaunchIntent for integration with existing launch_profiles consumers
    # without editing launch_profiles. Profile/host association remains external.
    launch_intent = LaunchIntent(
        profile_id=f"epic-direct:{app_name}",
        target_id=app_name,
        target_path=install_dir_str,
        executable=str(engine),
        argv=tuple(argv),
        cwd=install_dir_str,
        env=dict(env),
        target_host=None,
        verified_contract=True,
        error=None,
    )

    return DirectLaunchPlan(
        identity=identity,
        executable=str(engine),
        argv=tuple(argv),
        cwd=install_dir_str,
        env=dict(env),
        launch_intent=launch_intent,
        limitations=tuple(limitations),
    )
