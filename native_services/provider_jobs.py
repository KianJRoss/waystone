"""Native install, update, and repair job backend for Heroic CLI providers (Legendary, GOGdl, Nile).

Provides asynchronous, managed child-process execution for Epic Games (via Legendary),
GOG (via GOGdl), and Amazon Games (via Nile) without relying on Heroic GUI handoff.

Design Principles & Security Hardening:
- Explicit provider, product ID, install roots, and tool executable configuration.
- Strict validation: rejects outer whitespace, leading options (- or /), traversal,
  and enforces provider-specific ID formats (e.g. numeric IDs for GOG).
- Absolute path enforcement: binary, install_root, and auth_config_path must be absolute.
- Exact CLI verb mapping per operation:
    * Legendary: 'install' with '--update-only' or '--repair'
    * GOGdl: 'download', 'update', 'repair' (aliased in gogdl CLI)
    * Nile: 'install', 'update', 'verify' (aliased in nile CLI)
- Accurate path projection: Legendary base path vs explicit --game-folder destination.
  If game-folder is omitted, destination_dir is None (managed by CLI metadata).
- Bounded, sanitizing progress/status tracking: NEVER exposes raw stdout, URLs, tokens,
  or stack traces. Only fixed safe status strings and bounded percentage floats.
- Bounded stream consumption: stdin=DEVNULL, bounded buffer streaming (max lines / byte caps).
- Reliable process group reap: finally block guarantees process tree termination (SIGTERM -> SIGKILL)
  and process reaping on ALL exit paths (normal exit, error, or CancelledError).
- Immutable plan snapshot: frozen mappings and exact plan-to-execution verification.
- Honest error reporting: non-zero exit codes, timeouts, and cancellations are never reported as success.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import time
import os
import sys
from pathlib import Path
import re
import signal
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple
from types import MappingProxyType
from native_services import waystone_paths as _wpaths
from native_services import provider_engines as _provider_engines

try:
    from private_addons import (
        ADDON_SUPPORTED_PROVIDERS as _ADDON_SUPPORTED_PROVIDERS,
        build_addon_destination as _build_addon_destination,
        create_addon_plan as _create_addon_plan,
        finalize_addon_job as _finalize_addon_job,
        register_default_addon_tools as _register_default_addon_tools,
        validate_addon_recipe_product as _validate_addon_recipe_product,
    )
except ImportError:
    _ADDON_SUPPORTED_PROVIDERS = frozenset()
    _build_addon_destination = None
    _create_addon_plan = None
    _finalize_addon_job = None
    _register_default_addon_tools = None
    _validate_addon_recipe_product = None

logger = logging.getLogger("waystone.provider_jobs")

SAFE_ID_REGEX = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}$")
GOG_ID_REGEX = re.compile(r"^[1-9][0-9]{0,19}$")
SUPPORTED_PROVIDERS = frozenset({"epic", "gog", "amazon"}) | _ADDON_SUPPORTED_PROVIDERS
SUPPORTED_OPERATIONS = frozenset({"install", "update", "repair"})
SUPPORTED_PLATFORMS = frozenset({"windows", "linux", "osx"})

MANAGED_ENGINE_DIR = Path(__file__).resolve().parents[1] / 'components/provider_engines'

def _engine(name):
    # The provider CLI is part of Waystone. Never silently borrow Heroic binaries/config.
    return _provider_engines.resolve(name)

DEFAULT_TOOL_PATHS: Dict[str, Path] = {
    "epic": _engine("legendary"),
    "gog": _engine("gogdl"),
    "amazon": _engine("nile"),
}

DEFAULT_PROVIDER_CONFIG_ROOT = _wpaths.store("providers-v1")

def _default_provider_env_overrides() -> Dict[str, Dict[str, str]]:
    return {
        "epic": {"LEGENDARY_CONFIG_PATH": str(DEFAULT_PROVIDER_CONFIG_ROOT / "epic" / "legendary")},
    }

CHUNK_SIZE = 4096
MAX_PARSER_BUFFER = 8192


class ProviderJobError(ValueError):
    """Base error for provider job planning and execution."""


class ProviderValidationError(ProviderJobError):
    """Raised when provider parameters, product IDs, or paths violate safe bounds."""


class ProviderExecutionError(ProviderJobError):
    """Raised when a provider process terminates with failure, timeout, or invalid exit status."""

    def __init__(self, message: str, exit_code: Optional[int] = None, stage: Optional[str] = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.stage = stage


@dataclass(frozen=True)
class ProviderToolConfig:
    """Explicit binary paths and environment overrides for provider tools."""
    binary_path: Path
    extra_args: Tuple[str, ...] = ()
    env_overrides: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.binary_path, Path):
            object.__setattr__(self, "binary_path", Path(self.binary_path))
        if not self.binary_path.is_absolute():
            raise ProviderValidationError(f"Tool binary path '{self.binary_path}' must be absolute.")
        for arg in self.extra_args:
            if not isinstance(arg, str) or "\x00" in arg or "\n" in arg or "\r" in arg:
                raise ProviderValidationError("Tool extra_args must be clean strings.")
        # Ensure immutable env_overrides
        object.__setattr__(self, "env_overrides", MappingProxyType(dict(self.env_overrides)))


@dataclass(frozen=True)
class ProviderInstallRecipe:
    """Specification of an install, update, or repair job."""
    provider: str                      # 'epic', 'gog', or 'amazon'
    product_id: str                    # App/Game ID
    title: str                         # Display title
    install_root: Path                 # Base directory for installations
    operation: str = "install"         # 'install', 'update', 'repair'
    game_folder: Optional[str] = None  # Optional subfolder name override
    with_dlcs: bool = False            # Whether to automatically include DLCs
    platform: Optional[str] = None     # Optional platform override ('windows', 'linux', 'osx')
    auth_config_path: Optional[Path] = None  # Specific auth config file (e.g. gogdl)

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ProviderValidationError(f"Unsupported provider '{self.provider}'. Must be one of {sorted(SUPPORTED_PROVIDERS)}.")

        # Strict product ID validation: no outer whitespace, no leading dash/slash, safe chars
        if not isinstance(self.product_id, str):
            raise ProviderValidationError("Product ID must be a string.")
        if self.product_id != self.product_id.strip():
            raise ProviderValidationError("Product ID must not contain leading or trailing whitespace.")
        if self.product_id.startswith(("-", "/")):
            raise ProviderValidationError("Product ID must not start with an option/absolute prefix.")

        if self.provider in _ADDON_SUPPORTED_PROVIDERS and _validate_addon_recipe_product is not None:
            _validate_addon_recipe_product(self.provider, self.product_id, ProviderValidationError)
        else:
            if any(ch.isspace() or (ord(ch) < 32) or ch == "\x7f" for ch in self.product_id):
                raise ProviderValidationError(f"Invalid product_id '{self.product_id}'. Contains whitespace or control characters.")
            if not SAFE_ID_REGEX.fullmatch(self.product_id):
                raise ProviderValidationError(f"Invalid product_id '{self.product_id}'. Must be 1-128 safe characters.")

            # 4. GOG IDs must be numeric
            if self.provider == "gog" and not GOG_ID_REGEX.fullmatch(self.product_id):
                raise ProviderValidationError(f"GOG product_id '{self.product_id}' must be a positive integer ID.")
        if not isinstance(self.title, str) or not self.title.strip() or self.title != self.title.strip():
            raise ProviderValidationError("Title must be a clean non-empty string without outer whitespace.")

        if self.operation not in SUPPORTED_OPERATIONS:
            raise ProviderValidationError(f"Unsupported operation '{self.operation}'. Must be one of {sorted(SUPPORTED_OPERATIONS)}.")

        if not isinstance(self.install_root, Path):
            object.__setattr__(self, "install_root", Path(self.install_root))
        if not self.install_root.is_absolute():
            raise ProviderValidationError(f"install_root '{self.install_root}' must be an absolute path.")

        if self.game_folder is not None:
            if not isinstance(self.game_folder, str):
                raise ProviderValidationError("game_folder must be a string.")
            if self.game_folder != self.game_folder.strip():
                raise ProviderValidationError("game_folder must not contain leading or trailing whitespace.")
            if self.game_folder.startswith(("-", "/")):
                raise ProviderValidationError("game_folder must not start with an option prefix.")
            if not SAFE_ID_REGEX.fullmatch(self.game_folder):
                raise ProviderValidationError(f"Invalid game_folder '{self.game_folder}'. Must be safe relative path characters.")

        if self.platform is not None and self.platform not in SUPPORTED_PLATFORMS:
            raise ProviderValidationError(f"Unsupported platform '{self.platform}'. Must be one of {sorted(SUPPORTED_PLATFORMS)}.")

        if self.auth_config_path is not None:
            if not isinstance(self.auth_config_path, Path):
                object.__setattr__(self, "auth_config_path", Path(self.auth_config_path))
            if not self.auth_config_path.is_absolute():
                raise ProviderValidationError(f"auth_config_path '{self.auth_config_path}' must be an absolute path.")


@dataclass(frozen=True)
class ProviderInstallPlan:
    """Verifiable, immutable plan representing the argv array and targets to execute."""
    recipe: ProviderInstallRecipe
    tool_binary: Path
    argv: Tuple[str, ...]
    cwd: Path
    destination_dir: Optional[Path]     # Exact path if known/explicit; None if managed by tool metadata
    env: Mapping[str, str] = field(default_factory=dict)
    addon_request_path: Optional[Path] = None
    addon_result_path: Optional[Path] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True)
class ProviderJobProgress:
    """Sanitized progress snapshot (never contains raw stdout, credentials, or URLs)."""
    phase: str                         # 'planning', 'running', 'cancelling', 'completed', 'failed', 'cancelled'
    percent: Optional[float] = None    # 0.0 to 100.0 if parsed from tool output
    message: str = ""                  # Static safe status description
    exit_code: Optional[int] = None
    error: Optional[str] = None        # Typed uppercase error code (e.g. ERR_PROCESS_FAILED_1)


AsyncProcessRunner = Callable[
    [Tuple[str, ...], Path, Mapping[str, str], Optional[Callable[[float], None]]],
    Awaitable[int]
]


def build_provider_command(recipe: ProviderInstallRecipe, tool_config: ProviderToolConfig) -> Tuple[Tuple[str, ...], Optional[Path]]:
    """Builds the exact CLI argument vector for Legendary, GOGdl, or Nile based on the recipe."""
    binary = str(tool_config.binary_path)
    argv: List[str] = [binary]
    destination_dir: Optional[Path] = None

    if recipe.provider == "epic":
        # Legendary command line syntax:
        # legendary install <App Name> --base-path <path> [--game-folder <name>] [--repair] [--update-only] [--with-dlcs] -y
        argv.append("install")
        argv.append(recipe.product_id)
        argv.extend(["--base-path", str(recipe.install_root)])
        if recipe.game_folder:
            argv.extend(["--game-folder", recipe.game_folder])
            destination_dir = recipe.install_root / recipe.game_folder
        else:
            # When game-folder is omitted, Legendary determines folder from metadata
            destination_dir = None

        if recipe.operation == "repair":
            argv.append("--repair")
        elif recipe.operation == "update":
            argv.append("--update-only")
        if recipe.with_dlcs:
            argv.append("--with-dlcs")
        if recipe.platform:
            argv.extend(["--platform", recipe.platform])
        # Force non-interactive operation
        argv.append("-y")

    elif recipe.provider == "gog":
        # GOGdl CLI syntax:
        # gogdl {download,update,repair} <id> --path <destination_dir> [--with-dlcs] [--platform <os>] [--auth-config-path <path>]
        if recipe.auth_config_path:
            argv.extend(["--auth-config-path", str(recipe.auth_config_path)])

        verb = "download" if recipe.operation == "install" else recipe.operation
        argv.append(verb)
        argv.append(recipe.product_id)

        dest_name = recipe.game_folder or recipe.product_id
        destination_dir = recipe.install_root / dest_name
        argv.extend(["--path", str(destination_dir)])

        if recipe.with_dlcs:
            argv.append("--with-dlcs")
        if recipe.platform:
            argv.extend(["--platform", recipe.platform])

    elif recipe.provider == "amazon":
        # Nile CLI syntax:
        # nile {install,update,verify} <id> --path <destination_dir>
        verb_map = {"install": "install", "update": "update", "repair": "verify"}
        verb = verb_map[recipe.operation]
        argv.append(verb)
        argv.append(recipe.product_id)

        dest_name = recipe.game_folder or recipe.product_id
        destination_dir = recipe.install_root / dest_name
        argv.extend(["--path", str(destination_dir)])

    elif recipe.provider in _ADDON_SUPPORTED_PROVIDERS and _build_addon_destination is not None:
        destination_dir = _build_addon_destination(recipe.provider)

    argv.extend(tool_config.extra_args)
    return tuple(argv), destination_dir


_LAST_TASK_ERROR_TEXT: Dict[int, str] = {}


async def default_managed_process_runner(
    argv: Tuple[str, ...],
    cwd: Path,
    env: Mapping[str, str],
    progress_callback: Optional[Callable[[float], None]] = None,
) -> int:
    """Runs a managed async child process with stdin=DEVNULL, bounded streaming, and full group reap.

    Streams bounded chunks forever until stdout EOF, keeping a bounded parser buffer
    to extract percentage progress without risking stdout pipe deadlock or memory bloat.
    """
    run_env = os.environ.copy()
    run_env.update(env)

    percent_regex = re.compile(r"(\b\d{1,3}(?:\.\d+)?)\s*%")
    parser_buffer = ""
    last_reported_pct: Optional[float] = None

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=run_env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            parser_buffer += text
            if len(parser_buffer) > MAX_PARSER_BUFFER:
                parser_buffer = parser_buffer[-MAX_PARSER_BUFFER:]

            matches = percent_regex.findall(parser_buffer)
            if matches and progress_callback:
                try:
                    pct = float(matches[-1])
                    if 0.0 <= pct <= 100.0 and pct != last_reported_pct:
                        last_reported_pct = pct
                        progress_callback(pct)
                except Exception:
                    pass

        retcode = await proc.wait()
        if retcode != 0 and parser_buffer.strip():
            task = asyncio.current_task()
            if task is not None:
                from native_services.game_updates import sanitize_store_error_text
                cleaned = sanitize_store_error_text(parser_buffer)
                if cleaned:
                    _LAST_TASK_ERROR_TEXT[id(task)] = cleaned
        return retcode

    finally:
        # A second Cancel click or application shutdown must not interrupt reap.
        async def reap_owned_group():
            pgid = proc.pid  # fixed by start_new_session, even if leader exited
            try:
                os.killpg(pgid, signal.SIGTERM)
                for _ in range(10):
                    await asyncio.sleep(0.05)
                    try: os.killpg(pgid, 0)
                    except ProcessLookupError: break
                try: os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError: pass
            except ProcessLookupError:
                pass
            finally:
                await proc.wait()
        cleanup = asyncio.create_task(reap_owned_group())
        cancelled = False
        while not cleanup.done():
            try: await asyncio.shield(cleanup)
            except asyncio.CancelledError: cancelled = True
        await cleanup
        if cancelled: raise asyncio.CancelledError


class ProviderJobManager:
    """Orchestrates planning, verification, and execution of provider installation jobs."""

    def __init__(
        self,
        tool_configs: Optional[Dict[str, ProviderToolConfig]] = None,
        process_runner: Optional[AsyncProcessRunner] = None,
    ) -> None:
        self._tool_configs: Dict[str, ProviderToolConfig] = {}
        if tool_configs:
            for p, cfg in tool_configs.items():
                self.set_tool_config(p, cfg)
        else:
            default_env = _default_provider_env_overrides()
            for p, path in DEFAULT_TOOL_PATHS.items():
                self._tool_configs[p] = ProviderToolConfig(
                    binary_path=path,
                    env_overrides=dict(default_env.get(p, {})),
                )
            if _register_default_addon_tools is not None:
                _register_default_addon_tools(self._tool_configs, ProviderToolConfig)

        self._runner: AsyncProcessRunner = process_runner or default_managed_process_runner
        self._active_jobs: Dict[str, asyncio.Task[Any]] = {}
        self._provider_locks: Dict[str, asyncio.Lock] = {}

    def set_tool_config(self, provider: str, config: ProviderToolConfig) -> None:
        if provider not in SUPPORTED_PROVIDERS:
            raise ProviderValidationError(f"Unsupported provider '{provider}'.")
        self._tool_configs[provider] = config

    def create_plan(self, recipe: ProviderInstallRecipe) -> ProviderInstallPlan:
        """Generates an execution plan without starting any processes or network actions."""
        tool_cfg = self._tool_configs.get(recipe.provider)
        if not tool_cfg:
            raise ProviderValidationError(f"No tool configuration available for provider '{recipe.provider}'.")

        argv, destination_dir = build_provider_command(recipe, tool_cfg)

        cwd = recipe.install_root
        env = dict(tool_cfg.env_overrides)
        addon_request_path = None
        addon_result_path = None
        if recipe.provider in _ADDON_SUPPORTED_PROVIDERS and _create_addon_plan is not None:
            argv, cwd, destination_dir, addon_request_path, addon_result_path = _create_addon_plan(
                recipe,
                tool_cfg,
                cwd,
                ProviderValidationError,
                ProviderExecutionError,
            )

        return ProviderInstallPlan(
            recipe=recipe,
            tool_binary=tool_cfg.binary_path,
            argv=argv,
            cwd=cwd,
            destination_dir=destination_dir,
            env=env,
            addon_request_path=addon_request_path,
            addon_result_path=addon_result_path,
        )

    async def execute_job(
        self,
        plan: ProviderInstallPlan,
        progress_callback: Optional[Callable[[ProviderJobProgress], None]] = None,
    ) -> ProviderJobProgress:
        """Executes the given approved plan asynchronously and tracks progress."""
        # Integrity verification: verify plan matches current tool config and has not been forged
        expected_plan = self.create_plan(plan.recipe)
        if (
            plan.argv != expected_plan.argv
            or plan.tool_binary != expected_plan.tool_binary
            or plan.cwd != expected_plan.cwd
            or plan.destination_dir != expected_plan.destination_dir
            or dict(plan.env) != dict(expected_plan.env)
        ):
            raise ProviderValidationError(
                "Plan verification failed: argv, tool_binary, cwd, destination_dir, or env does not match approved provider configuration."
            )

        # Actual default install root ~/Games/Waystone may not exist; only EXPLICIT execute creates verified root
        plan.recipe.install_root.mkdir(parents=True, exist_ok=True)
        if plan.destination_dir is not None:
            plan.destination_dir.mkdir(parents=True, exist_ok=True)

        job_key = f"{plan.recipe.provider}:{plan.recipe.product_id}"
        if job_key in self._active_jobs:
            raise ProviderExecutionError(f"A job for '{job_key}' is already active.")

        def notify(prog: ProviderJobProgress) -> None:
            if progress_callback:
                try:
                    progress_callback(prog)
                except Exception:
                    pass

        notify(ProviderJobProgress(phase="planning", message="Plan verified and queued"))

        def handle_numeric_progress(pct: float) -> None:
            notify(ProviderJobProgress(phase="running", percent=pct, message="Operation in progress"))

        notify(ProviderJobProgress(phase="running", percent=0.0, message="Starting installer process"))

        provider_lock = self._provider_locks.setdefault(plan.recipe.provider, asyncio.Lock())

        async def _run() -> ProviderJobProgress:
            task_obj = asyncio.current_task()
            task_id = id(task_obj) if task_obj is not None else 0
            cfg_path = plan.env.get("LEGENDARY_CONFIG_PATH") or plan.env.get("NILE_CONFIG_PATH")
            cfg_lock = None
            if cfg_path:
                from native_services.game_updates import config_root_lock
                cfg_lock = config_root_lock(cfg_path)
            try:
                async with provider_lock:
                    if cfg_lock is not None:
                        # The update checker holds this lock on a worker thread for seconds;
                        # wait for it without stalling the job event loop.
                        await asyncio.to_thread(cfg_lock.acquire)
                    try:
                        raw_ret = await self._runner(
                            plan.argv,
                            plan.cwd,
                            plan.env,
                            handle_numeric_progress,
                        )
                    finally:
                        if cfg_lock is not None:
                            cfg_lock.release()

                store_error = ""
                if isinstance(raw_ret, tuple) and len(raw_ret) >= 2:
                    retcode = int(raw_ret[0])
                    store_error = str(raw_ret[1] or "")
                else:
                    retcode = int(raw_ret)
                if not store_error and task_id in _LAST_TASK_ERROR_TEXT:
                    store_error = _LAST_TASK_ERROR_TEXT.pop(task_id, "")
                if not store_error and getattr(self._runner, "last_error_text", ""):
                    store_error = str(getattr(self._runner, "last_error_text", ""))
                if store_error:
                    from native_services.game_updates import sanitize_store_error_text
                    store_error = sanitize_store_error_text(store_error)

                if retcode == 0:
                    if plan.recipe.provider in _ADDON_SUPPORTED_PROVIDERS and _finalize_addon_job is not None:
                        addon_res = _finalize_addon_job(plan, ProviderJobProgress)
                        if addon_res is not None:
                            notify(addon_res)
                            return addon_res
                    res = ProviderJobProgress(
                        phase="completed",
                        percent=100.0,
                        exit_code=0,
                        message="Operation completed successfully",
                    )
                    notify(res)
                    return res
                else:
                    safe_err = f"ERR_PROCESS_FAILED_{retcode}"
                    msg = store_error if store_error else f"Process exited with error code {retcode}"
                    res = ProviderJobProgress(
                        phase="failed",
                        exit_code=retcode,
                        error=safe_err,
                        message=msg,
                    )
                    notify(res)
                    return res

            except asyncio.CancelledError:
                _LAST_TASK_ERROR_TEXT.pop(task_id, None)
                notify(ProviderJobProgress(phase="cancelling", message="Cancelling provider operation"))
                res = ProviderJobProgress(phase="cancelled", message="Operation cancelled")
                notify(res)
                raise

        current_task = asyncio.create_task(_run())
        self._active_jobs[job_key] = current_task

        try:
            return await current_task
        finally:
            self._active_jobs.pop(job_key, None)

    async def cancel_job(self, provider: str, product_id: str) -> bool:
        """Cancels an active job if running."""
        job_key = f"{provider}:{product_id}"
        task = self._active_jobs.get(job_key)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True
        return False
