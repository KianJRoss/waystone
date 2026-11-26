"""Sunshine & Apollo host integration for publishing a Waystone library to Moonlight (W11a).

Why Waystone writes ``apps.json`` by default and optionally syncs over the `:47990` REST API:
- Writing ``~/.config/sunshine/apps.json`` (or ``~/.config/apollo/apps.json``) directly requires no Web UI
  password setup and works whether the Sunshine daemon is running or stopped.
- If the user provides Sunshine Web UI credentials (or injects a REST client), Waystone also syncs its tagged
  entries over Sunshine's local REST API on ``https://localhost:47990/api/apps``.
- Every Waystone entry is tagged with ``"waystone_managed": true`` and ``"waystone_id": "<canonical-id>"`` so
  Waystone only ever updates or removes its own entries and never touches the user's other Sunshine apps.
- Before the first write to an existing ``apps.json``, Waystone saves an untouched backup at
  ``apps.json.waystone.bak``.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import ssl
import tempfile
from typing import Any, Callable, Iterable
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


SUNSHINE_COVER_WIDTH = 600
SUNSHINE_COVER_HEIGHT = 900
FILTER_MODES = ("installed", "favorites", "category")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class SunshineHostStatus:
    installed: bool
    running: bool
    client_connected: bool
    flavor: str
    binary_path: str
    config_dir: Path
    apps_json_path: Path
    backup_path: Path
    published_count: int
    summary: str
    big_screen_label: str


def is_waystone_entry(entry: Any) -> bool:
    """Return True if an apps.json entry is tagged as Waystone-owned."""
    return isinstance(entry, dict) and entry.get("waystone_managed") is True and isinstance(entry.get("waystone_id"), str)


def count_waystone_entries(apps_json_path: Path) -> int:
    """Count Waystone-tagged entries in an apps.json file without raising on missing/corrupt files."""
    try:
        if not apps_json_path.is_file():
            return 0
        doc = json.loads(apps_json_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return 0
        apps = doc.get("apps", [])
        if not isinstance(apps, list):
            return 0
        return sum(1 for item in apps if is_waystone_entry(item))
    except Exception:
        return 0


def probe_local_sunshine_serverinfo(
    *,
    host: str = "127.0.0.1",
    http_port: int = 47989,
    https_port: int = 47984,
    timeout: float = 0.35,
) -> dict[str, Any]:
    """Query Sunshine's local GameStream /serverinfo XML endpoint (no credentials required).

    Returns ``{"running": bool, "client_connected": bool}``.
    """
    urls = [
        f"http://{host}:{http_port}/serverinfo",
        f"https://{host}:{https_port}/serverinfo",
    ]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for url in urls:
        try:
            req = urllib.request.Request(url, method="GET")
            if url.startswith("https://"):
                resp_ctx = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            else:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout)
            with resp_ctx as resp:
                raw = resp.read(65536)
            root = ET.fromstring(raw.decode("utf-8", errors="replace"))
            state_text = (root.findtext("state") or "").strip().upper()
            current_game_text = (root.findtext("currentgame") or "0").strip()
            try:
                current_game = int(current_game_text)
            except ValueError:
                current_game = 0
            busy = state_text.endswith("_BUSY") or current_game > 0
            return {"running": True, "client_connected": busy}
        except Exception:
            continue
    return {"running": False, "client_connected": False}


def resolve_sunshine_config_dir(
    config_dir: Path | str | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    home_dir: Path | None = None,
    xdg_config_home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[Path, str, str]:
    """Resolve (config_dir, flavor, binary_path) for Sunshine or Apollo."""
    env = environ if environ is not None else os.environ
    home = Path(home_dir) if home_dir is not None else Path.home()
    if xdg_config_home is not None:
        xdg = Path(xdg_config_home)
    else:
        raw_xdg = env.get("XDG_CONFIG_HOME", "").strip()
        xdg = Path(raw_xdg).expanduser() if raw_xdg else (home / ".config")

    sunshine_bin = str(which("sunshine") or "")
    apollo_bin = str(which("apollo") or "")

    explicit = str(config_dir or "").strip() or env.get("WAYSTONE_SUNSHINE_CONFIG_DIR", "").strip()
    if explicit:
        resolved = Path(explicit).expanduser()
        is_apollo = resolved.name.casefold() == "apollo" or (bool(apollo_bin) and not sunshine_bin)
        flavor = "Apollo" if is_apollo else "Sunshine"
        binary = apollo_bin if is_apollo and apollo_bin else (sunshine_bin or apollo_bin)
        return resolved, flavor, binary

    candidates: list[tuple[Path, str, str]] = [
        (xdg / "sunshine", "Sunshine", sunshine_bin),
        (xdg / "apollo", "Apollo", apollo_bin or sunshine_bin),
        (home / ".var/app/dev.lizardbyte.app.Sunshine/config/sunshine", "Sunshine", sunshine_bin),
    ]
    for path, flavor, binary in candidates:
        if path.is_dir():
            return path, flavor, binary

    if apollo_bin and not sunshine_bin:
        return xdg / "apollo", "Apollo", apollo_bin
    return xdg / "sunshine", "Sunshine", sunshine_bin


def detect_sunshine_host(
    config_dir: Path | str | None = None,
    *,
    publish_enabled: bool = False,
    which: Callable[[str], str | None] = shutil.which,
    status_probe: Callable[[], dict[str, Any]] | None = None,
    home_dir: Path | None = None,
    xdg_config_home: Path | None = None,
    environ: dict[str, str] | None = None,
    offline: bool = False,
) -> SunshineHostStatus:
    """Detect Sunshine or Apollo installation, config path, running status, and connected client state."""
    resolved_dir, flavor, binary_path = resolve_sunshine_config_dir(
        config_dir,
        which=which,
        home_dir=home_dir,
        xdg_config_home=xdg_config_home,
        environ=environ,
    )
    apps_json_path = resolved_dir / "apps.json"
    backup_path = resolved_dir / "apps.json.waystone.bak"
    config_exists = resolved_dir.is_dir() and (
        apps_json_path.is_file() or (resolved_dir / "sunshine.conf").is_file() or (resolved_dir / "apollo.conf").is_file()
    )
    installed = bool(binary_path) or config_exists or resolved_dir.is_dir()

    running = False
    client_connected = False
    if status_probe is not None:
        try:
            probe_result = status_probe() or {}
            running = bool(probe_result.get("running", False))
            client_connected = bool(probe_result.get("client_connected", False))
        except Exception:
            running = False
            client_connected = False
    elif not offline and installed:
        probe_result = probe_local_sunshine_serverinfo()
        running = bool(probe_result.get("running", False))
        client_connected = bool(probe_result.get("client_connected", False))

    if running and not installed:
        installed = True

    published_count = count_waystone_entries(apps_json_path)
    host_label = "Apollo (Sunshine fork)" if flavor == "Apollo" else "Sunshine"

    if not installed:
        summary = f"{host_label} is not installed on this PC (looked in {resolved_dir})."
    else:
        run_part = "running" if running else "not currently running"
        client_part = " · Moonlight client connected" if (running and client_connected) else (
            " · no client connected" if running else ""
        )
        pub_part = (
            f" · {published_count} Waystone game{'s' if published_count != 1 else ''} published"
            if published_count > 0
            else " · no Waystone games published yet"
        )
        summary = f"{host_label} detected ({resolved_dir}) · {run_part}{client_part}{pub_part}"

    is_on = bool(publish_enabled or published_count > 0 or running)
    if not is_on:
        big_screen_label = "Off"
    elif running and client_connected:
        big_screen_label = "On · client connected"
    else:
        big_screen_label = "On"

    return SunshineHostStatus(
        installed=installed,
        running=running,
        client_connected=client_connected,
        flavor=flavor,
        binary_path=binary_path,
        config_dir=resolved_dir,
        apps_json_path=apps_json_path,
        backup_path=backup_path,
        published_count=published_count,
        summary=summary,
        big_screen_label=big_screen_label,
    )


def _safe_cover_filename(canonical_id: str) -> str:
    cleaned = _SAFE_ID_RE.sub("-", canonical_id.strip()).strip("-")[:80] or "game"
    digest = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:12]
    return f"waystone-{cleaned}-{digest}.png"


def export_sunshine_cover(
    canonical_id: str,
    source_image: bytes | str | Path | Any,
    covers_dir: Path,
) -> Path | None:
    """Convert a cover image from Waystone's cache/store into a 600x900 PNG in ``covers_dir``."""
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    if source_image is None or source_image == "":
        return None

    img: QImage | None = None
    if isinstance(source_image, QImage):
        img = source_image
    elif isinstance(source_image, (bytes, bytearray)):
        candidate = QImage()
        if candidate.loadFromData(bytes(source_image)):
            img = candidate
    elif isinstance(source_image, (str, Path)):
        text = str(source_image).strip()
        if text.startswith("file://"):
            from PySide6.QtCore import QUrl
            local_file = QUrl(text).toLocalFile()
            text = local_file or text[7:]
        path = Path(text)
        if path.is_file():
            candidate = QImage(str(path))
            if not candidate.isNull():
                img = candidate

    if img is None or img.isNull() or img.width() <= 0 or img.height() <= 0:
        return None

    canvas = QImage(SUNSHINE_COVER_WIDTH, SUNSHINE_COVER_HEIGHT, QImage.Format_ARGB32)
    canvas.fill(QColor("#121318"))
    scaled = img.scaled(
        SUNSHINE_COVER_WIDTH,
        SUNSHINE_COVER_HEIGHT,
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    painter = QPainter(canvas)
    try:
        x = (SUNSHINE_COVER_WIDTH - scaled.width()) // 2
        y = (SUNSHINE_COVER_HEIGHT - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
    finally:
        painter.end()

    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    if not canvas.save(buf, "PNG"):
        return None
    png_bytes = bytes(buf.data())
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return None

    covers_dir.mkdir(parents=True, exist_ok=True)
    target = covers_dir / _safe_cover_filename(canonical_id)
    fd, tmp_name = tempfile.mkstemp(prefix=".waystone-cover-", suffix=".png", dir=covers_dir)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(png_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return target


def resolve_game_cover_source(
    canonical_id: str,
    *,
    library_model: Any = None,
    artwork_view: Any = None,
    media_catalog: Any = None,
    ribbon_cache: Any = None,
    artwork_cache_dir: Path | None = None,
) -> Any:
    """Resolve the best available cover image (bytes, QImage, or Path) for ``canonical_id``."""
    game = None
    if library_model is not None:
        rep = library_model.representativeFor(canonical_id) or canonical_id
        game = next((g for g in getattr(library_model, "games", []) if isinstance(g, dict) and g.get("id") == rep), None)

    member_ids = [canonical_id]
    if isinstance(game, dict):
        linked = game.get("linked_record_ids", [])
        if isinstance(linked, list):
            for mid in linked:
                if isinstance(mid, str) and mid and mid not in member_ids:
                    member_ids.append(mid)

    # 1. Manual artwork override store
    av = artwork_view or getattr(library_model, "_artwork_view", None)
    if av is not None and hasattr(av, "_read_store_asset"):
        for mid in member_ids:
            try:
                raw = av._read_store_asset(mid, "cover")
                if raw:
                    return raw
            except Exception:
                continue

    # 2. RibbonCache / Waystone artwork cache
    rc = ribbon_cache or getattr(library_model, "_ribbon_cache", None)
    if rc is None and artwork_cache_dir is not None:
        from native_services.ribbon_artwork import RibbonCache
        rc = RibbonCache(artwork_cache_dir)
    if rc is not None and library_model is not None and game is not None and hasattr(library_model, "_ribbon_keys"):
        for provider, product_id in library_model._ribbon_keys(game):
            try:
                cached = rc.lookup(provider, product_id)
                cover_path = cached.get("cover") if isinstance(cached, dict) else None
                if cover_path and Path(cover_path).is_file():
                    return Path(cover_path)
            except Exception:
                continue

    # 3. Direct coverUrl or MediaCatalog handle from LibraryModel._urls
    if library_model is not None and game is not None and hasattr(library_model, "_urls"):
        try:
            urls = library_model._urls(game)
            cover_url = str(urls.get("coverUrl") or "")
            if cover_url.startswith("file://"):
                return cover_url
            mc = media_catalog or getattr(library_model, "_media", None)
            if cover_url.startswith("image://catalog-media/") and mc is not None:
                handle = cover_url.split("image://catalog-media/", 1)[1]
                from PySide6.QtCore import QSize
                img = mc.requestImage(handle, QSize(), QSize())
                if img is not None and not img.isNull():
                    return img
        except Exception:
            pass

    return None


def select_games_for_sunshine(
    library_model: Any,
    *,
    filter_mode: str = "installed",
    filter_category: str = "",
    local_library: Any = None,
    artwork_view: Any = None,
    media_catalog: Any = None,
    ribbon_cache: Any = None,
    artwork_cache_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Return filtered games ready to publish to Sunshine: ``[{"id", "title", "cover_source"}]``."""
    if library_model is None:
        return []
    mode = filter_mode if filter_mode in FILTER_MODES else "installed"
    target_category = (filter_category or "").strip()

    from library_model import collection_values_for_variant

    selected: list[dict[str, Any]] = []
    for game in getattr(library_model, "games", []):
        if not isinstance(game, dict):
            continue
        gid = str(game.get("id") or "").strip()
        if not gid:
            continue
        title = str(library_model._display_title(game) if hasattr(library_model, "_display_title") else game.get("title") or "").strip()
        if not title:
            continue

        variants = (
            library_model._effective_variants(game)
            if hasattr(library_model, "_effective_variants")
            else game.get("variants", [])
        )

        is_installed = any(
            isinstance(v, dict) and (v.get("status") == "installed" or bool(v.get("installation_basis")))
            for v in variants
        )
        if not is_installed and local_library is not None and hasattr(local_library, "recordsForGame"):
            member_ids = game.get("linked_record_ids", [gid])
            for mid in member_ids:
                try:
                    recs = local_library.recordsForGame(mid) or []
                    if recs:
                        is_installed = True
                        break
                except Exception:
                    continue

        is_favorite = any(
            "Favorite" in collection_values_for_variant(v, "favorite")
            for v in variants
            if isinstance(v, dict)
        )
        categories: set[str] = set()
        for v in variants:
            if isinstance(v, dict):
                categories.update(collection_values_for_variant(v, "categories"))

        if mode == "installed":
            if not is_installed:
                continue
        elif mode == "favorites":
            if not is_favorite:
                continue
        elif mode == "category":
            if target_category and target_category not in categories:
                continue
            if not target_category and not is_installed:
                continue

        cover_source = resolve_game_cover_source(
            gid,
            library_model=library_model,
            artwork_view=artwork_view,
            media_catalog=media_catalog,
            ribbon_cache=ribbon_cache,
            artwork_cache_dir=artwork_cache_dir,
        )
        selected.append({"id": gid, "title": title, "cover_source": cover_source})

    selected.sort(key=lambda item: (item["title"].casefold(), item["id"]))
    return selected


def resolve_waystone_command(which=shutil.which, home: Path | None = None) -> str:
    """Absolute path of the `waystone` command when it can be found: Sunshine runs apps with its own environment,
    which may not include the user's PATH (e.g. ~/.local/bin)."""
    found = which("waystone")
    if found:
        return str(Path(found).resolve()) if not Path(found).is_symlink() else str(found)
    local = (home or Path.home()) / ".local" / "bin" / "waystone"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return "waystone"


def build_waystone_command(canonical_id: str, *, waystone_command: str = "waystone") -> str:
    """Format the per-game Sunshine launch command ``waystone --play <canonical-id>``."""
    base = (waystone_command or "waystone").strip() or "waystone"
    return f"{shlex.quote(base)} --play {shlex.quote(canonical_id)}"


class SunshineRestClient:
    """Optional client for Sunshine's local REST API on ``https://localhost:47990/api/apps``."""

    def __init__(
        self,
        *,
        base_url: str = "https://127.0.0.1:47990",
        username: str = "",
        password: str = "",
        timeout: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.username or self.password:
            token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def list_apps(self) -> list[dict[str, Any]]:
        req = urllib.request.Request(f"{self.base_url}/api/apps", headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
            doc = json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))
        apps = doc.get("apps", []) if isinstance(doc, dict) else []
        return [dict(a) for a in apps if isinstance(a, dict)]

    def save_app(self, app_entry: dict[str, Any], index: int = -1) -> None:
        payload = dict(app_entry)
        payload["index"] = index
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/api/apps", data=data, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
            resp.read(65536)

    def delete_app(self, index: int) -> None:
        req = urllib.request.Request(
            f"{self.base_url}/api/apps/{index}",
            headers=self._headers(),
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
            resp.read(65536)


def sync_sunshine_rest(rest_client: Any, waystone_entries: list[dict[str, Any]]) -> None:
    """Synchronize Waystone-tagged entries with a Sunshine REST API endpoint without touching user apps."""
    existing = rest_client.list_apps()
    # Delete existing Waystone-managed entries in reverse index order so lower indices stay valid.
    for idx in range(len(existing) - 1, -1, -1):
        if is_waystone_entry(existing[idx]):
            rest_client.delete_app(idx)
    # Append current Waystone entries.
    for entry in waystone_entries:
        rest_client.save_app(entry, index=-1)


def _load_or_init_apps_doc(apps_json_path: Path) -> dict[str, Any]:
    if not apps_json_path.exists():
        return {
            "env": {"PATH": "$(PATH):$(HOME)/.local/bin"},
            "apps": [],
        }
    raw = apps_json_path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ValueError("Sunshine apps.json must be a JSON object")
    apps = doc.get("apps")
    if apps is None:
        doc["apps"] = []
    elif not isinstance(apps, list):
        raise ValueError("Sunshine apps.json 'apps' field must be a list")
    return doc


def _backup_before_first_write(apps_json_path: Path, backup_path: Path) -> bool:
    """Create ``apps.json.waystone.bak`` before the first modification of an existing ``apps.json``."""
    if not apps_json_path.is_file() or backup_path.exists():
        return False
    raw = apps_json_path.read_bytes()
    fd, tmp_name = tempfile.mkstemp(prefix=".apps.json.bak-", dir=apps_json_path.parent)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, backup_path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return True


def _atomic_write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def publish_to_sunshine(
    config_dir: Path | str,
    games: Iterable[dict[str, Any]],
    *,
    waystone_command: str = "waystone",
    rest_client: Any = None,
) -> dict[str, Any]:
    """Write Waystone-tagged entries into ``<config_dir>/apps.json`` (and optional REST client).

    Preserves all non-Waystone user entries and backs up ``apps.json`` before the first write.
    """
    cfg_dir = Path(config_dir).expanduser()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    apps_json_path = cfg_dir / "apps.json"
    backup_path = cfg_dir / "apps.json.waystone.bak"
    covers_dir = cfg_dir / "covers"

    doc = _load_or_init_apps_doc(apps_json_path)
    backed_up = _backup_before_first_write(apps_json_path, backup_path)

    user_apps = [dict(item) for item in doc.get("apps", []) if not is_waystone_entry(item)]

    waystone_entries: list[dict[str, Any]] = []
    active_cover_files: set[str] = set()
    covers_written = 0

    for item in games:
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not canonical_id or not title:
            continue

        cover_path = export_sunshine_cover(canonical_id, item.get("cover_source"), covers_dir)
        image_path_str = ""
        if cover_path is not None:
            image_path_str = str(cover_path)
            active_cover_files.add(cover_path.name)
            covers_written += 1

        entry = {
            "name": title,
            "cmd": build_waystone_command(canonical_id, waystone_command=waystone_command),
            "image-path": image_path_str,
            "output": "",
            "waystone_managed": True,
            "waystone_id": canonical_id,
        }
        waystone_entries.append(entry)

    # Prune stale Waystone-owned covers without touching user covers
    if covers_dir.is_dir():
        for existing_file in covers_dir.glob("waystone-*.png"):
            if existing_file.name not in active_cover_files:
                try:
                    existing_file.unlink()
                except OSError:
                    pass

    doc["apps"] = user_apps + waystone_entries
    _atomic_write_json(apps_json_path, doc)

    rest_synced = False
    if rest_client is not None:
        sync_sunshine_rest(rest_client, waystone_entries)
        rest_synced = True

    return {
        "published_count": len(waystone_entries),
        "user_count": len(user_apps),
        "covers_written": covers_written,
        "backed_up": backed_up,
        "backup_path": str(backup_path),
        "apps_json_path": str(apps_json_path),
        "rest_synced": rest_synced,
    }


def remove_from_sunshine(
    config_dir: Path | str,
    *,
    rest_client: Any = None,
) -> dict[str, Any]:
    """Remove all Waystone-tagged entries and covers from Sunshine while keeping user entries intact."""
    cfg_dir = Path(config_dir).expanduser()
    apps_json_path = cfg_dir / "apps.json"
    backup_path = cfg_dir / "apps.json.waystone.bak"
    covers_dir = cfg_dir / "covers"

    removed_count = 0
    user_count = 0
    if apps_json_path.is_file():
        doc = _load_or_init_apps_doc(apps_json_path)
        _backup_before_first_write(apps_json_path, backup_path)
        existing_apps = doc.get("apps", [])
        user_apps = [dict(item) for item in existing_apps if not is_waystone_entry(item)]
        removed_count = sum(1 for item in existing_apps if is_waystone_entry(item))
        user_count = len(user_apps)
        doc["apps"] = user_apps
        _atomic_write_json(apps_json_path, doc)

    removed_covers = 0
    if covers_dir.is_dir():
        for existing_file in covers_dir.glob("waystone-*.png"):
            try:
                existing_file.unlink()
                removed_covers += 1
            except OSError:
                pass

    rest_synced = False
    if rest_client is not None:
        sync_sunshine_rest(rest_client, [])
        rest_synced = True

    return {
        "removed_count": removed_count,
        "user_count": user_count,
        "removed_covers": removed_covers,
        "apps_json_path": str(apps_json_path),
        "rest_synced": rest_synced,
    }
