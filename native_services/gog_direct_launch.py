"""Pure offline GOG direct-launch planner.

Builds a staged Waystone LaunchIntent-compatible payload for invoking the bundled
``gogdl`` CLI against a locally installed GOG title. No subprocesses, no
network/API/auth access, no downloads, no Heroic default config reads, and no
game/launcher execution.

Source-verified local behavior (bundled gogdl 1.3.0, inspected from
staging/components/provider_engines/gogdl): ``launch.launch`` resolves launch
from local install metadata (``goggame-<id>.info`` for windows/osx, ``start.sh``
for linux) and executes locally. ``cli.py`` eagerly initializes
AuthorizationManager/ApiHandler before dispatch for all commands; with an empty
app-owned ``--auth-config-path`` and no stored credentials this appears
non-networking by inspection, but runtime network/auth behavior is not
exhaustively proven by this planner (planner never executes).

Planner contract (explicit, app-owned): caller supplies an app-owned
``config_root`` (or explicit auth-config path under it), an exact ``product_id``,
an explicit ``install_path``, target ``platform``, and an explicit ``gogdl_path``
(provider engine). The planner validates containment and local metadata
presence, then emits argv for: ``gogdl --auth-config-path <app-owned> launch
<install_path> <product_id> --platform <platform>``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

__all__ = [
    "GogDirectLaunchError",
    "GogDirectLaunchPlan",
    "LaunchIntent",
    "plan_gog_direct_launch",
]

_SAFE_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_ENV = {"GOGDL_CONFIG_PATH"}


class GogDirectLaunchError(ValueError):
    """Raised when direct-launch planning is refused or evidence is missing."""


@dataclass(frozen=True)
class LaunchIntent:
    """Minimal LaunchIntent compatible with staged Waystone launch_profiles."""

    profile_id: str
    target_id: str
    target_path: str | None
    executable: str
    argv: list[str]
    cwd: str | None
    env: dict[str, str] = field(default_factory=dict)
    target_host: str | None = None
    verified_contract: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
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


@dataclass(frozen=True)
class GogDirectLaunchPlan:
    product_id: str
    platform: str
    install_path: str
    gogdl_path: str
    auth_config_path: str
    info_path: str | None
    launch_intent: LaunchIntent
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "platform": self.platform,
            "install_path": self.install_path,
            "gogdl_path": self.gogdl_path,
            "auth_config_path": self.auth_config_path,
            "info_path": self.info_path,
            "launch_intent": self.launch_intent.to_dict(),
            "limitations": list(self.limitations),
        }


def _clean_str(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GogDirectLaunchError(f"{name} must be a string")
    if "\x00" in value:
        raise GogDirectLaunchError(f"{name} contains NUL")
    if value != value.strip():
        raise GogDirectLaunchError(f"{name} has leading/trailing whitespace")
    if not allow_empty and value == "":
        raise GogDirectLaunchError(f"{name} is empty")
    return value


def _validate_id(value: Any, name: str) -> str:
    text = _clean_str(value, name)
    if not _SAFE_ID.fullmatch(text):
        raise GogDirectLaunchError(f"{name} format is invalid")
    return text


def _abs_dir(path: Any, name: str) -> Path:
    text = _clean_str(path, name)
    p = Path(text)
    if not p.is_absolute():
        raise GogDirectLaunchError(f"{name} must be an absolute path")
    resolved = p.resolve()
    if not resolved.is_dir():
        raise GogDirectLaunchError(f"{name} must be an existing directory")
    return resolved


def _abs_file(path: Any, name: str) -> Path:
    text = _clean_str(path, name)
    p = Path(text)
    if not p.is_absolute():
        raise GogDirectLaunchError(f"{name} must be an absolute path")
    resolved = p.resolve()
    if not resolved.is_file():
        raise GogDirectLaunchError(f"{name} must be an existing file")
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_relpath(value: Any, name: str) -> str:
    text = _clean_str(value, name)
    if text.startswith("/") or text.startswith("\\"):
        raise GogDirectLaunchError(f"{name} must be relative")
    # Reject drive letters / UNC-ish forms
    if re.match(r"^[A-Za-z]:", text):
        raise GogDirectLaunchError(f"{name} must be relative")
    p = PurePosixPath(text.replace("\\", "/"))
    if any(part in ("", ".", "..") for part in p.parts):
        raise GogDirectLaunchError(f"{name} is not a safe relative path")
    return str(PurePosixPath(*p.parts))


def _resolve_inside(root: Path, rel: str, name: str) -> Path:
    candidate = (root / Path(rel)).resolve()
    if not _is_within(candidate, root):
        raise GogDirectLaunchError(f"{name} escapes install_path")
    return candidate


def _load_info(info_path: Path, product_id: str) -> dict[str, Any]:
    if info_path.is_symlink():
        raise GogDirectLaunchError("goggame info must not be a symlink")
    try:
        if info_path.stat().st_size > 512 * 1024:
            raise GogDirectLaunchError("goggame info exceeds size limit")
        raw = info_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GogDirectLaunchError("cannot read goggame info") from exc
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise GogDirectLaunchError("goggame info is not valid JSON") from exc
    if not isinstance(data, dict):
        raise GogDirectLaunchError("goggame info root must be an object")
    # Exact identity is mandatory; bool is an int subclass but not a product ID.
    for key in ("rootGameId", "gameId"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise GogDirectLaunchError(f"goggame info missing valid {key}")
        if str(value) != product_id:
            raise GogDirectLaunchError("goggame info does not match exact product_id")
    return data


def _select_task(data: dict[str, Any], prefer_task: int | None) -> dict[str, Any]:
    tasks = data.get("playTasks")
    if not isinstance(tasks, list) or not tasks or not all(isinstance(t, dict) for t in tasks):
        raise GogDirectLaunchError("goggame info has no valid playTasks")
    if prefer_task is not None:
        if (not isinstance(prefer_task, int) or isinstance(prefer_task, bool)
                or prefer_task < 0 or prefer_task >= len(tasks)):
            raise GogDirectLaunchError("prefer_task is outside playTasks")
        selected = tasks[prefer_task]
    else:
        # Match gogdl's get_preferred_task: first isPrimary task, no fallback.
        selected = next((task for task in tasks if task.get("isPrimary") is True), None)
        if selected is None:
            raise GogDirectLaunchError("goggame info has no primary play task")
    if selected.get("type") != "FileTask":
        raise GogDirectLaunchError("selected play task is not a FileTask")
    return selected


def _validate_task(data: dict[str, Any], install_path: Path, *,
                   task_base: Path | None = None, prefer_task: int | None = None) -> str:
    selected = _select_task(data, prefer_task)
    rel_exe = _safe_relpath(selected.get("path"), "selected task path")
    exe = _resolve_inside(task_base or install_path, rel_exe.replace("/", os.sep), "selected executable")
    if not exe.is_file():
        raise GogDirectLaunchError("selected executable is missing inside install")
    working_dir = selected.get("workingDir")
    if working_dir not in (None, ""):
        rel_wd = _safe_relpath(working_dir, "task workingDir")
        wd = _resolve_inside(install_path, rel_wd.replace("/", os.sep), "task workingDir")
        if not wd.is_dir():
            raise GogDirectLaunchError("task workingDir is missing inside install_path")
    return rel_exe


def _info_path_for(install: Path, product_id: str, platform: str) -> Path:
    if platform == "osx":
        return install / "Contents" / "Resources" / f"goggame-{product_id}.info"
    if platform == "linux":
        return install / "game" / f"goggame-{product_id}.info"
    return install / f"goggame-{product_id}.info"


def _auth_config_path_for_root(config_root: Path) -> Path:
    # App-owned, deterministic location under config root. Do not read it.
    return (config_root / "gogdl-auth.json").resolve()


def plan_gog_direct_launch(
    config_root: str | os.PathLike[str],
    product_id: str,
    install_path: str | os.PathLike[str],
    *,
    platform: str,
    gogdl_path: str | os.PathLike[str],
    auth_config_path: str | os.PathLike[str] | None = None,
    profile_id: str = "gog",
    wrapper: Sequence[str] | str | None = None,
    prefer_task: int | None = None,
    wine_binary: str | os.PathLike[str] | None = None,
    wine_prefix: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> GogDirectLaunchPlan:
    """Plan an offline direct launch via gogdl for an exact installed product.

    The planner is pure/offline: it validates local paths and metadata and
    returns a LaunchIntent-compatible argv without spawning processes.
    """
    root = _abs_dir(os.fspath(config_root), "config_root")
    pid = _validate_id(product_id, "product_id")
    plat = _clean_str(platform, "platform").lower()
    if plat not in {"windows", "linux", "osx"}:
        raise GogDirectLaunchError("platform must be one of: windows, linux, osx")

    install = _abs_dir(os.fspath(install_path), "install_path")
    gogdl = _abs_file(os.fspath(gogdl_path), "gogdl_path")
    # Ensure gogdl binary is not outside an obviously unrelated root by accident?
    # Not required by contract; containment is enforced for install/config targets.

    if auth_config_path is None:
        auth_path = _auth_config_path_for_root(root)
    else:
        auth_text = _clean_str(os.fspath(auth_config_path), "auth_config_path")
        auth_p = Path(auth_text)
        if not auth_p.is_absolute():
            raise GogDirectLaunchError("auth_config_path must be absolute")
        auth_path = auth_p.resolve()
    # auth file must live under app-owned config root; never read it.
    if auth_path == root or not _is_within(auth_path, root):
        raise GogDirectLaunchError("auth_config_path must be within config_root and name a file")
    if auth_path.exists() and auth_path.is_dir():
        raise GogDirectLaunchError("auth_config_path must name a file, not a directory")
    if auth_path.is_symlink():
        raise GogDirectLaunchError("auth_config_path must not be a symlink")

    info_path = _info_path_for(install, pid, plat).resolve()
    if not _is_within(info_path, install):
        raise GogDirectLaunchError("goggame info escapes install_path")
    if not info_path.is_file():
        raise GogDirectLaunchError(f"matching goggame-{pid}.info is missing")
    data = _load_info(info_path, pid)
    limitations: list[str] = []
    if plat == "linux":
        start_sh = (install / "start.sh").resolve()
        if not _is_within(start_sh, install):
            raise GogDirectLaunchError("start.sh escapes install_path")
        if not start_sh.is_file() or not (start_sh.stat().st_mode & 0o111):
            raise GogDirectLaunchError("linux install_path missing executable start.sh")
        game_dir = (install / "game").resolve()
        if not _is_within(game_dir, install) or not game_dir.is_dir():
            raise GogDirectLaunchError("linux install missing contained game directory")
        _validate_task(data, install, task_base=game_dir, prefer_task=prefer_task)
        limitations.append(
            "gogdl launches Linux start.sh; Waystone verifies matching local GOG metadata and task paths, but cannot prove the script's eventual child command without executing it"
        )
    else:
        _validate_task(data, install, prefer_task=prefer_task)

    argv: list[str] = [str(gogdl), "--auth-config-path", str(auth_path),
                       "launch", str(install), pid, "--platform", plat]
    if prefer_task is not None:
        if not isinstance(prefer_task, int) or isinstance(prefer_task, bool):
            raise GogDirectLaunchError("prefer_task must be an integer")
        argv.extend(["--prefer-task", str(prefer_task)])
    if plat == "osx":
        raise GogDirectLaunchError("macOS GOG builds cannot be launched by this Linux-target planner")
    if plat == "windows":
        if wine_binary is None or wine_prefix is None:
            raise GogDirectLaunchError("Windows GOG launch requires an explicit Wine binary and prefix")
        wine = _abs_file(os.fspath(wine_binary), "wine_binary")
        prefix = _abs_dir(os.fspath(wine_prefix), "wine_prefix")
        if not os.access(wine, os.X_OK):
            raise GogDirectLaunchError("wine_binary must be executable")
        argv.extend(["--wine", str(wine), "--wine-prefix", str(prefix)])
    elif wine_binary is not None or wine_prefix is not None:
        raise GogDirectLaunchError("Wine settings are only valid for Windows GOG builds")
    if wrapper is not None:
        if isinstance(wrapper, str):
            wrapper_text = _clean_str(wrapper, "wrapper", allow_empty=False)
            try:
                shlex.split(wrapper_text)
            except ValueError as exc:
                raise GogDirectLaunchError("wrapper has invalid quoting") from exc
        else:
            if not isinstance(wrapper, Sequence):
                raise GogDirectLaunchError("wrapper must be a string or sequence")
            parts = [_clean_str(p, "wrapper argument") for p in wrapper]
            if not parts:
                raise GogDirectLaunchError("wrapper sequence is empty")
            wrapper_text = " ".join(shlex.quote(p) for p in parts)
        argv.extend(["--wrapper", wrapper_text])

    plan_env: dict[str, str] = {"GOGDL_CONFIG_PATH": str((root / "gogdl-config").resolve())}
    if env is not None:
        if not isinstance(env, Mapping):
            raise GogDirectLaunchError("env must be a mapping")
        for k, v in env.items():
            ks = _clean_str(k, "env key")
            if not _ENV_NAME.fullmatch(ks):
                raise GogDirectLaunchError("env key is not a valid environment variable")
            if ks in _RESERVED_ENV:
                raise GogDirectLaunchError(f"env may not override reserved variable {ks}")
            vs = _clean_str(v, "env value", allow_empty=True)
            plan_env[ks] = vs

    limitations.extend([
        "planner emits gogdl CLI argv; it does not execute or guarantee gogdl runtime auth/network behavior",
        "gogdl CLI initializes auth/api managers globally (source-inspected); use only an empty app-owned auth config path",
    ])

    intent = LaunchIntent(
        profile_id=_clean_str(profile_id, "profile_id"),
        target_id=pid,
        executable=str(gogdl),
        argv=argv,
        cwd=str(install),
        env=plan_env,
        target_path=str(install),
        verified_contract=True,
        error=None,
    )
    return GogDirectLaunchPlan(
        product_id=pid,
        platform=plat,
        install_path=str(install),
        gogdl_path=str(gogdl),
        auth_config_path=str(auth_path),
        info_path=str(info_path) if info_path else None,
        launch_intent=intent,
        limitations=tuple(dict.fromkeys(limitations)),
    )
