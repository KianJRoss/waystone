"""Read-only game update detection and update job coordination for Waystone (W15).

Store update detection capabilities and citations:
1. Epic Games Store (`legendary`):
   - CLI command: `legendary list-installed --check-updates --json`
   - Runs against Waystone's managed config root (`~/.local/share/waystone/providers-v1/epic/legendary`)
     and Heroic's Legendary config root (`~/.config/heroic/legendaryConfig/legendary`).
   - Safety: Never runs two Legendary processes on the same config root concurrently
     (enforced via per-config-root locks), and skips Heroic's config root whenever
     Heroic or another Legendary process on that config root is running.
2. Amazon Games (`nile`):
   - Citation: `imLinguin/nile` (`nile/cli.py`, `CLI.list_updates` / `arguments.py` `list-updates --json`).
   - CLI command: `nile list-updates --json` (with `NILE_CONFIG_PATH`).
   - Compares installed game versions in `installed.json` against Amazon's
     `GetGameDownload` manifest versions and outputs a JSON list of updatable
     product IDs (`["<product_id>", ...]`) or product dicts.
3. GOG (`gogdl`):
   - Citation: `Heroic-Games-Launcher/heroic-gogdl` (`gogdl/args.py`, `init_parser`).
   - `gogdl` is a stateless download/update/repair helper (`download`, `repair`, `update`,
     `info`, `launch`, `save-sync`, `auth`) and does NOT maintain an `installed.json` or
     provide a standalone read-only `list-updates` / `--check-updates` CLI subcommand.
     (Heroic compares `buildId` in `~/.config/heroic/gog_store/installed.json` against
     GOG's `content-system.gog.com/products/<id>/os/<os>/builds` HTTP API directly.)
   - Waystone supports updating Waystone-installed GOG games via `gogdl update <id> ...`
     when an update record is present or explicitly requested.
4. Steam (`appmanifest_<appid>.acf`):
   - Read-only local VDF inspection of `appmanifest_*.acf` in every Steam library folder
     listed in `libraryfolders.vdf` (reusing `parse_vdf` and `steam_libraries` from
     `native_services.game_installs`).
   - Checks `AppState` fields `StateFlags` (bit 2 `UpdateRequired` or bits 256/512/1024),
     `UpdateResult` (non-zero), and `BytesToDownload` (`BytesToDownload > BytesDownloaded`).
   - Shows `"Update pending in Steam"` and offers `"Open Steam downloads"`
     (`steam://open/downloads`); Waystone never modifies Steam game files directly.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import logging
import subprocess
import threading
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from native_services.game_installs import parse_vdf, steam_libraries
from native_services.installation_library import ALIASES as INSTALL_ALIASES
from native_services import waystone_paths as _wpaths
from native_services import provider_engines as _provider_engines

_LOG = logging.getLogger("waystone.game_updates")

SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAYSTONE_EPIC_BINARY = _provider_engines.resolve("legendary")
DEFAULT_WAYSTONE_EPIC_CONFIG = (
    _wpaths.store("providers-v1", "epic", "legendary")
)
DEFAULT_HEROIC_EPIC_BINARY = Path(
    "/opt/Heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary"
)
DEFAULT_HEROIC_EPIC_CONFIG = Path.home() / ".config" / "heroic" / "legendaryConfig" / "legendary"

DEFAULT_WAYSTONE_NILE_BINARY = _provider_engines.resolve("nile")
DEFAULT_WAYSTONE_NILE_CONFIG = (
    _wpaths.store("providers-v1", "amazon")
)
DEFAULT_WAYSTONE_GOG_BINARY = _provider_engines.resolve("gogdl")
DEFAULT_WAYSTONE_GOG_CONFIG = (
    _wpaths.store("providers-v1", "gog")
)

UPDATE_CHECK_INTERVAL_SECONDS = 6 * 3600  # 6 hours
UPDATE_CHECK_INTERVAL_MS = UPDATE_CHECK_INTERVAL_SECONDS * 1000
MAX_MANIFEST_BYTES = 512 * 1024

# Per-config-root locks ensure two Legendary (or Nile) processes never run on one config root at once.
_META_LOCK = threading.Lock()
_CONFIG_ROOT_LOCKS: Dict[str, threading.Lock] = {}

_URL_SECRET_RE = re.compile(r"https?://\S+|Bearer\s+\S+|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]+", re.IGNORECASE)


def normalize_config_key(config_root: str | Path) -> str:
    """Canonical key for a provider config root directory."""
    try:
        return str(Path(config_root).expanduser().resolve(strict=False))
    except Exception:
        return str(config_root)


def config_root_lock(config_root: str | Path) -> threading.Lock:
    """Return the process-wide lock guarding a specific provider config root."""
    key = normalize_config_key(config_root)
    with _META_LOCK:
        lock = _CONFIG_ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CONFIG_ROOT_LOCKS[key] = lock
        return lock


def sanitize_store_error_text(raw_output: str, max_length: int = 280) -> str:
    """Extract a plain, human-readable store error message from CLI output without leaking URLs or tokens."""
    if not isinstance(raw_output, str) or not raw_output.strip():
        return ""
    lines = []
    for raw_line in raw_output.splitlines():
        cleaned = "".join(ch if ch.isprintable() else " " for ch in raw_line).strip()
        if not cleaned:
            continue
        cleaned = _URL_SECRET_RE.sub("[redacted]", cleaned)
        lines.append(cleaned)
    if not lines:
        return ""

    # Prefer explicit error/fatal/critical lines emitted by legendary, gogdl, or nile
    error_patterns = (
        re.compile(r"(?:\[[^\]]+\]\s*)?(?:ERROR|CRITICAL|FATAL)\s*:\s*(.+)", re.IGNORECASE),
        re.compile(r"^(?:error|fatal|failed)\s*:\s*(.+)", re.IGNORECASE),
    )
    for line in reversed(lines):
        for pat in error_patterns:
            match = pat.search(line)
            if match:
                extracted = line.strip()
                return extracted[:max_length]
    # Fallback to last non-progress line
    for line in reversed(lines):
        if not re.fullmatch(r"[\d.\s%]+", line):
            return line[:max_length]
    return lines[-1][:max_length]


@dataclass(frozen=True)
class GameUpdateInfo:
    """Normalized read-only observation that an installed game has a pending update."""

    provider: str  # 'epic' | 'amazon' | 'gog' | 'steam'
    product_id: str
    title: str
    scope: str  # 'waystone' | 'heroic' | 'steam'
    installed_version: str = ""
    available_version: str = ""
    install_path: str = ""
    config_root: str = ""
    binary_path: str = ""
    platform: str = ""
    status_label: str = "Update available"
    can_update_in_waystone: bool = True
    blocks_launch: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "productId": self.product_id,
            "product_id": self.product_id,
            "title": self.title,
            "scope": self.scope,
            "installedVersion": self.installed_version,
            "availableVersion": self.available_version,
            "installPath": self.install_path,
            "configRoot": self.config_root,
            "binaryPath": self.binary_path,
            "platform": self.platform,
            "statusLabel": self.status_label,
            "canUpdateInWaystone": self.can_update_in_waystone,
            "blocksLaunch": self.blocks_launch,
            "isSteamPending": self.provider == "steam",
        }


def is_heroic_running(
    proc_root: Path = Path("/proc"),
    heroic_config_root: Optional[Path] = None,
) -> bool:
    """Check `/proc` read-only to see if Heroic or a Legendary process on Heroic's config root is running."""
    try:
        if not proc_root.is_dir():
            return False
    except OSError:
        return False

    own_pid = os.getpid()
    cfg_norm = normalize_config_key(heroic_config_root or DEFAULT_HEROIC_EPIC_CONFIG)

    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return False

    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == own_pid:
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="ignore").strip().lower()
        except OSError:
            comm = ""
        if comm in {"heroic", "heroic-run"}:
            return True
        try:
            raw_cmd = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw_cmd:
            continue
        parts = [p.decode("utf-8", errors="ignore") for p in raw_cmd.split(b"\x00") if p]
        if not parts:
            continue
        exe_name = Path(parts[0]).name.lower()
        if exe_name in {"heroic", "heroic.appimage"} or any("/opt/heroic/" in p.lower() and "legendary" not in p.lower() for p in parts[:2]):
            return True
        if exe_name == "legendary" or comm == "legendary":
            # If any legendary process is running from Heroic's bundle or with Heroic's config root
            if any("/opt/heroic/" in p.lower() or cfg_norm in p for p in parts):
                return True
            try:
                raw_env = (entry / "environ").read_bytes().decode("utf-8", errors="ignore")
                if cfg_norm in raw_env:
                    return True
            except OSError:
                pass
    return False


def default_readonly_runner(
    argv: Sequence[str],
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: float = 20.0,
) -> Tuple[int, str, str]:
    """Run a read-only CLI check (`legendary` or `nile`) with stdin=DEVNULL and bounded timeout."""
    if not argv:
        return 1, "", "Empty command"
    binary = Path(argv[0])
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return 127, "", f"Executable not available: {binary}"
    run_env = os.environ.copy()
    if env:
        for k, v in env.items():
            run_env[str(k)] = str(v)
    try:
        completed = subprocess.run(
            [str(a) for a in argv],
            stdin=subprocess.DEVNULL,
             capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=run_env,
            timeout=timeout_seconds,
            check=False,
        )
        return int(completed.returncode), completed.stdout or "", completed.stderr or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def _read_json_file(path: Path) -> Any:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, TypeError):
        return None


def _load_legendary_installed_map(config_root: Path) -> Dict[str, Dict[str, Any]]:
    """Read `<config_root>/installed.json` into `{app_name: entry_dict}`."""
    data = _read_json_file(config_root / "installed.json")
    result: Dict[str, Dict[str, Any]] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                app_name = str(value.get("app_name") or value.get("appName") or value.get("id") or key).strip()
                if app_name:
                    result[app_name] = value
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, dict):
                app_name = str(value.get("app_name") or value.get("appName") or value.get("id") or "").strip()
                if app_name:
                    result[app_name] = value
    return result


def parse_legendary_installed_csv(
    raw_csv: str,
    *,
    scope: str,
    config_root: str,
    binary_path: str,
    installed_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[GameUpdateInfo]:
    """Parse `legendary list-installed --check-updates --csv` (Legendary 0.21 columns:
    App name, App title, Installed version, Available version, Update available, Install size, Install path, Platform)."""
    import csv
    import io

    if not isinstance(raw_csv, str) or not raw_csv.strip():
        return []
    rows = csv.DictReader(io.StringIO(raw_csv.strip()))
    fallback_map = installed_map or {}
    updates: List[GameUpdateInfo] = []
    seen: Set[str] = set()
    for row in rows:
        app_name = str(row.get("App name") or "").strip()
        if not app_name or app_name in seen:
            continue
        if str(row.get("Update available") or "").strip().lower() != "true":
            continue
        seen.add(app_name)
        fallback = fallback_map.get(app_name, {})
        updates.append(
            GameUpdateInfo(
                provider="epic",
                product_id=app_name,
                title=str(row.get("App title") or fallback.get("title") or app_name).strip(),
                scope=scope,
                installed_version=str(row.get("Installed version") or fallback.get("version") or "").strip(),
                available_version=str(row.get("Available version") or "").strip(),
                install_path=str(row.get("Install path") or fallback.get("install_path") or "").strip(),
                config_root=config_root,
                binary_path=binary_path,
                platform=str(row.get("Platform") or fallback.get("platform") or "").strip(),
                status_label="Update available",
                can_update_in_waystone=True,
                blocks_launch=True,
            )
        )
    return updates


def parse_legendary_installed_updates(
    raw_json: str,
    *,
    scope: str,
    config_root: str,
    binary_path: str,
    installed_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[GameUpdateInfo]:
    """Parse `legendary list-installed --check-updates --json` stdout into `GameUpdateInfo` records."""
    if not isinstance(raw_json, str) or not raw_json.strip():
        return []
    text = raw_json.strip()
    # Legendary may print log lines before/after JSON if stderr was merged; locate JSON array or object
    if not (text.startswith("[") or text.startswith("{")):
        arr_start = text.find("[")
        obj_start = text.find("{")
        candidates = [idx for idx in (arr_start, obj_start) if idx >= 0]
        if not candidates:
            return []
        text = text[min(candidates):]
    try:
        payload = json.loads(text)
    except ValueError:
        return []

    entries: List[Tuple[str, Mapping[str, Any]]] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, Mapping):
                app_name = str(item.get("app_name") or item.get("appName") or item.get("id") or "").strip()
                if app_name:
                    entries.append((app_name, item))
    elif isinstance(payload, Mapping):
        for key, item in payload.items():
            if isinstance(item, Mapping):
                app_name = str(item.get("app_name") or item.get("appName") or item.get("id") or key).strip()
                if app_name:
                    entries.append((app_name, item))

    fallback_map = installed_map or {}
    updates: List[GameUpdateInfo] = []
    seen: Set[str] = set()

    for app_name, item in entries:
        if app_name in seen:
            continue
        fallback = fallback_map.get(app_name, {})
        installed_ver = str(
            item.get("version")
            or item.get("installed_version")
            or fallback.get("version")
            or ""
        ).strip()
        remote_ver = str(
            item.get("remote_version")
            or item.get("latest_version")
            or item.get("asset_version")
            or item.get("update_version")
            or item.get("available_version")
            or ""
        ).strip()
        if not remote_ver and isinstance(item.get("asset_infos"), Mapping):
            for asset in item["asset_infos"].values():
                if isinstance(asset, Mapping) and asset.get("build_version"):
                    remote_ver = str(asset["build_version"]).strip()
                    break

        if "update_available" in item:
            has_update = bool(item["update_available"])
        elif "has_update" in item:
            has_update = bool(item["has_update"])
        elif "needs_update" in item:
            has_update = bool(item["needs_update"])
        else:
            has_update = bool(installed_ver and remote_ver and installed_ver != remote_ver)

        if not has_update:
            continue

        seen.add(app_name)
        title = str(item.get("title") or fallback.get("title") or app_name).strip()
        install_path = str(item.get("install_path") or fallback.get("install_path") or "").strip()
        platform = str(item.get("platform") or fallback.get("platform") or "").strip()
        updates.append(
            GameUpdateInfo(
                provider="epic",
                product_id=app_name,
                title=title,
                scope=scope,
                installed_version=installed_ver,
                available_version=remote_ver,
                install_path=install_path,
                config_root=str(config_root),
                binary_path=str(binary_path),
                platform=platform,
                status_label="Update available",
                can_update_in_waystone=True,
                blocks_launch=True,
            )
        )
    return updates


def check_legendary_config_updates(
    config_root: Path,
    binary_path: Path,
    *,
    scope: str,
    runner: Callable[[Sequence[str], Optional[Mapping[str, str]]], Tuple[int, str, str]] = default_readonly_runner,
    heroic_running_fn: Optional[Callable[[], bool]] = None,
    active_config_roots: Optional[Set[str]] = None,
) -> List[GameUpdateInfo]:
    """Run `legendary list-installed --check-updates --json` safely for one config root.

    Enforces:
    - Never runs on Heroic's config root while Heroic is running/updating.
    - Never runs two Legendary processes on the same config root at once.
    - Only runs when `installed.json` exists and has at least one installed game.
    """
    cfg_key = normalize_config_key(config_root)
    if active_config_roots and cfg_key in active_config_roots:
        return []
    if scope == "heroic" and heroic_running_fn is not None and heroic_running_fn():
        return []

    installed_map = _load_legendary_installed_map(config_root)
    if not installed_map:
        return []

    lock = config_root_lock(config_root)
    if not lock.acquire(blocking=False):
        return []
    try:
        if scope == "heroic" and heroic_running_fn is not None and heroic_running_fn():
            return []
        # --csv, not --json: Legendary 0.21's JSON is only the installed records (no latest version);
        # the CSV adds "Available version" and "Update available" (legendary/cli.py list_installed).
        argv = [str(binary_path), "list-installed", "--check-updates", "--csv"]
        env = {"LEGENDARY_CONFIG_PATH": str(config_root)}
        retcode, stdout, _stderr = runner(argv, env)
        if retcode != 0 or not stdout:
            return []
        parse = parse_legendary_installed_csv if stdout.lstrip().startswith("App name,") else parse_legendary_installed_updates
        return parse(
            stdout,
            scope=scope,
            config_root=str(config_root),
            binary_path=str(binary_path),
            installed_map=installed_map,
        )
    finally:
        lock.release()


def _load_nile_installed_map(config_root: Path) -> Tuple[Path, Dict[str, Dict[str, Any]]]:
    """Locate Nile's `installed.json` under `config_root` and return `(installed_path, {id: entry})`."""
    candidates = [
        config_root / "nile" / "installed.json",
        config_root / "installed.json",
    ]
    for path in candidates:
        data = _read_json_file(path)
        if data is None:
            continue
        result: Dict[str, Dict[str, Any]] = {}
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    pid = str(entry.get("id") or entry.get("product_id") or entry.get("app_name") or "").strip()
                    if pid:
                        result[pid] = entry
        elif isinstance(data, dict):
            for key, entry in data.items():
                if isinstance(entry, dict):
                    pid = str(entry.get("id") or entry.get("product_id") or key).strip()
                    if pid:
                        result[pid] = entry
                elif isinstance(entry, list):
                    for sub in entry:
                        if isinstance(sub, dict):
                            pid = str(sub.get("id") or sub.get("product_id") or "").strip()
                            if pid:
                                result[pid] = sub
        if result:
            return path, result
    return config_root / "nile" / "installed.json", {}


def check_nile_config_updates(
    config_root: Path,
    binary_path: Path,
    *,
    scope: str = "waystone",
    runner: Callable[[Sequence[str], Optional[Mapping[str, str]]], Tuple[int, str, str]] = default_readonly_runner,
    active_config_roots: Optional[Set[str]] = None,
) -> List[GameUpdateInfo]:
    """Run `nile list-updates --json` when installed Amazon games exist.

    Citation: `imLinguin/nile` (`nile/cli.py`, `CLI.list_updates`).
    """
    cfg_key = normalize_config_key(config_root)
    if active_config_roots and cfg_key in active_config_roots:
        return []

    _installed_path, installed_map = _load_nile_installed_map(config_root)
    if not installed_map:
        return []

    lock = config_root_lock(config_root)
    if not lock.acquire(blocking=False):
        return []
    try:
        argv = [str(binary_path), "list-updates", "--json"]
        env = {"NILE_CONFIG_PATH": str(config_root)}
        retcode, stdout, _stderr = runner(argv, env)
        if retcode != 0 or not stdout or not stdout.strip():
            return []
        try:
            payload = json.loads(stdout.strip())
        except ValueError:
            return []

        updates: List[GameUpdateInfo] = []
        seen: Set[str] = set()
        items = payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) else [])
        for item in items:
            if isinstance(item, str):
                pid = item.strip()
                remote_ver = ""
            elif isinstance(item, Mapping):
                pid = str(item.get("id") or item.get("product_id") or item.get("app_name") or "").strip()
                remote_ver = str(item.get("version") or item.get("remote_version") or item.get("available_version") or "").strip()
            else:
                continue
            if not pid or pid in seen or pid not in installed_map:
                continue
            seen.add(pid)
            local = installed_map[pid]
            updates.append(
                GameUpdateInfo(
                    provider="amazon",
                    product_id=pid,
                    title=str(local.get("title") or pid).strip(),
                    scope=scope,
                    installed_version=str(local.get("version") or "").strip(),
                    available_version=remote_ver,
                    install_path=str(local.get("path") or local.get("install_path") or "").strip(),
                    config_root=str(config_root),
                    binary_path=str(binary_path),
                    status_label="Update available",
                    can_update_in_waystone=True,
                    blocks_launch=False,
                )
            )
        return updates
    finally:
        lock.release()


def _safe_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return 0


def parse_steam_appmanifest_update(text: str, library_dir: Optional[Path] = None) -> Optional[GameUpdateInfo]:
    """Parse a single Steam `appmanifest_<appid>.acf` file and return a `GameUpdateInfo` if an update is pending.

    Checks `AppState`:
    - `StateFlags`: bit 2 (`0x2`, `StateUpdateRequired`) or bits `256 | 512 | 1024` (`UpdateRunning | UpdatePaused | UpdateStarted`).
    - `UpdateResult`: non-zero value indicates an update is pending/incomplete.
    - `BytesToDownload`: positive and greater than `BytesDownloaded`.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    parsed = parse_vdf(text)
    app_state = parsed.get("AppState")
    if not isinstance(app_state, dict):
        return None

    appid = str(app_state.get("appid") or "").strip()
    if not appid.isdigit():
        return None

    state_flags = _safe_int(app_state.get("StateFlags", 0))
    update_result = _safe_int(app_state.get("UpdateResult", 0))
    bytes_to_download = _safe_int(app_state.get("BytesToDownload", 0))
    bytes_downloaded = _safe_int(app_state.get("BytesDownloaded", 0))
    installdir = str(app_state.get("installdir") or "").strip()

    # Must represent an installed app, not an empty/uninstalled manifest stub
    if state_flags == 0 and not installdir:
        return None

    update_flag_bits = 2 | 256 | 512 | 1024  # UpdateRequired | UpdateRunning | UpdatePaused | UpdateStarted
    needs_update = (
        (state_flags & update_flag_bits) != 0
        or update_result != 0
        or (bytes_to_download > 0 and bytes_to_download > bytes_downloaded)
    )
    if not needs_update:
        return None

    title = str(app_state.get("name") or f"Steam App {appid}").strip()
    install_path = ""
    if library_dir is not None and installdir:
        install_path = str(library_dir / "steamapps" / "common" / installdir)

    return GameUpdateInfo(
        provider="steam",
        product_id=appid,
        title=title,
        scope="steam",
        installed_version=str(app_state.get("buildid") or "").strip(),
        available_version=str(app_state.get("TargetBuildID") or "").strip(),
        install_path=install_path,
        status_label="Update pending in Steam",
        can_update_in_waystone=False,
        blocks_launch=False,
    )


def scan_steam_pending_updates(
    home: Optional[Path] = None,
    *,
    library_dirs: Optional[Sequence[Path]] = None,
) -> List[GameUpdateInfo]:
    """Scan `appmanifest_*.acf` in all Steam library folders for games with pending updates."""
    libs = list(library_dirs) if library_dirs is not None else steam_libraries(home if home is not None else Path.home())
    updates: List[GameUpdateInfo] = []
    seen: Set[str] = set()

    for lib in libs:
        steamapps = Path(lib) / "steamapps"
        try:
            if not steamapps.is_dir():
                continue
            manifests = sorted(steamapps.glob("appmanifest_*.acf"))
        except OSError:
            continue
        for manifest in manifests:
            try:
                if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
                    continue
                content = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            info = parse_steam_appmanifest_update(content, library_dir=Path(lib))
            if info is not None and info.product_id not in seen:
                seen.add(info.product_id)
                updates.append(info)
    return updates


class GameUpdateDetector:
    """Read-only detector for pending game updates across Epic (Waystone + Heroic), Amazon (Nile), and Steam."""

    def __init__(
        self,
        *,
        waystone_epic_config: Optional[Path] = None,
        waystone_epic_binary: Optional[Path] = None,
        heroic_epic_config: Optional[Path] = None,
        heroic_epic_binary: Optional[Path] = None,
        waystone_nile_config: Optional[Path] = None,
        waystone_nile_binary: Optional[Path] = None,
        steam_home: Optional[Path] = None,
        steam_library_dirs: Optional[Sequence[Path]] = None,
        runner: Optional[Callable[[Sequence[str], Optional[Mapping[str, str]]], Tuple[int, str, str]]] = None,
        heroic_running_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.waystone_epic_config = Path(waystone_epic_config) if waystone_epic_config else DEFAULT_WAYSTONE_EPIC_CONFIG
        self.waystone_epic_binary = Path(waystone_epic_binary) if waystone_epic_binary else DEFAULT_WAYSTONE_EPIC_BINARY
        self.heroic_epic_config = Path(heroic_epic_config) if heroic_epic_config else DEFAULT_HEROIC_EPIC_CONFIG
        if heroic_epic_binary is not None:
            self.heroic_epic_binary = Path(heroic_epic_binary)
        elif DEFAULT_HEROIC_EPIC_BINARY.is_file():
            self.heroic_epic_binary = DEFAULT_HEROIC_EPIC_BINARY
        else:
            self.heroic_epic_binary = self.waystone_epic_binary
        self.waystone_nile_config = Path(waystone_nile_config) if waystone_nile_config else DEFAULT_WAYSTONE_NILE_CONFIG
        self.waystone_nile_binary = Path(waystone_nile_binary) if waystone_nile_binary else DEFAULT_WAYSTONE_NILE_BINARY
        self.steam_home = Path(steam_home) if steam_home else None
        self.steam_library_dirs = [Path(p) for p in steam_library_dirs] if steam_library_dirs is not None else None
        self.runner = runner or default_readonly_runner
        self.heroic_running_fn = heroic_running_fn or (
            lambda: is_heroic_running(heroic_config_root=self.heroic_epic_config)
        )

    def is_heroic_busy(self) -> bool:
        try:
            return bool(self.heroic_running_fn())
        except Exception:
            return False

    def detect_all(self, *, active_config_roots: Optional[Set[str]] = None) -> List[GameUpdateInfo]:
        """Run read-only update checks for all configured installed game stores.

        Each store is checked on its own: one failing source (missing tool, odd manifest) is logged and
        skipped, never hides the updates found by the others.
        """
        active = {normalize_config_key(p) for p in (active_config_roots or set())}
        heroic_differs = normalize_config_key(self.heroic_epic_config) != normalize_config_key(self.waystone_epic_config)
        sources: List[Tuple[str, Callable[[], List[GameUpdateInfo]]]] = [
            ("Epic (Waystone)", lambda: check_legendary_config_updates(
                self.waystone_epic_config, self.waystone_epic_binary, scope="waystone", runner=self.runner,
                heroic_running_fn=self.heroic_running_fn, active_config_roots=active)),
        ]
        if heroic_differs:
            sources.append(("Epic (Heroic)", lambda: check_legendary_config_updates(
                self.heroic_epic_config, self.heroic_epic_binary, scope="heroic", runner=self.runner,
                heroic_running_fn=self.heroic_running_fn, active_config_roots=active)))
        sources.append(("Amazon", lambda: check_nile_config_updates(
            self.waystone_nile_config, self.waystone_nile_binary, scope="waystone", runner=self.runner,
            active_config_roots=active)))
        sources.append(("Steam", lambda: scan_steam_pending_updates(
            self.steam_home, library_dirs=self.steam_library_dirs)))

        results: List[GameUpdateInfo] = []
        seen_provider_product: Set[Tuple[str, str]] = set()
        for label, check in sources:
            try:
                found = check()
            except Exception:
                _LOG.exception("Update check for %s failed", label)
                continue
            for info in found:
                key = (info.provider, info.product_id)
                if key not in seen_provider_product:
                    seen_provider_product.add(key)
                    results.append(info)
        return results


def build_catalog_product_index(
    catalog: Mapping[str, Any],
    connection_records: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[Tuple[str, str], Set[str]]:
    """Map `(provider, product_id)` to canonical `game_id`s in the library catalog."""
    index: Dict[Tuple[str, str], Set[str]] = {}
    games = catalog.get("games", []) if isinstance(catalog, Mapping) else []
    for game in games:
        if not isinstance(game, Mapping):
            continue
        gid = str(game.get("id") or "").strip()
        if not gid:
            continue
        for variant in game.get("variants", ()):
            if not isinstance(variant, Mapping):
                continue
            raw_source = str(variant.get("source") or "").strip().casefold()
            provider = INSTALL_ALIASES.get(raw_source, raw_source)
            if provider in {"heroic-epic", "heroic", "epic games"}:
                provider = "epic"
            elif provider in {"steam"}:
                provider = "steam"
            elif provider in {"gog"}:
                provider = "gog"
            elif provider in {"amazon", "amazon games", "nile"}:
                provider = "amazon"
            if provider not in {"epic", "steam", "gog", "amazon"}:
                continue

            for field in ("provider_game_id", "external_id", "source_id"):
                val = str(variant.get(field) or "").strip()
                if not val:
                    continue
                index.setdefault((provider, val), set()).add(gid)
                # Handle prefixed source_id like "epic:Sugar" or "steam:620"
                if ":" in val:
                    tail = val.rsplit(":", 1)[-1].strip()
                    if tail:
                        index.setdefault((provider, tail), set()).add(gid)

    if connection_records:
        for rec in connection_records:
            if not isinstance(rec, Mapping):
                continue
            raw_prov = str(rec.get("provider") or "").strip().casefold()
            provider = "epic" if raw_prov in {"heroic-epic", "epic"} else raw_prov
            product_id = str(rec.get("product_id") or "").strip()
            if provider in {"epic", "steam", "gog", "amazon"} and product_id:
                for gid in rec.get("canonical_game_ids", ()):
                    if isinstance(gid, str) and gid:
                        index.setdefault((provider, product_id), set()).add(gid)

    return index
