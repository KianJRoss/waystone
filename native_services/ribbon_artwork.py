"""Ribbon artwork (W18): a wide hero banner and a transparent logo per game, fetched on demand and cached.

Kian 2026-11-24: "Ideally we would pull a ribbon and a game cover for this UI though because just a game cover wont
cut it." Big Screen shows a ribbon (hero + logo) for the games it puts on screen; this module finds and caches them.

- Sources, in order: Steam's public library assets by app id (library_hero.jpg, logo.png). More stores plug in as
  more `Source` functions (Epic/Xbox store key images, SteamGridDB with a user key).
- Fetching happens only for games the UI actually shows (`request()`), in one background worker, never in bulk.
- Downloads are https-only from an allowlist of hosts, size-capped, and must decode as a real JPEG/PNG of sane size.
- The cache lives in `~/.local/share/waystone/artwork-cache/` and is pruned oldest-first above `max_bytes`.
  A manual artwork choice (artwork_overrides) always wins over this cache; the library model does that ordering.
- A game with no ribbon anywhere is remembered as a miss for a week so it is not re-requested on every view.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from typing import Callable, Iterable
from urllib.parse import quote, urlsplit

ROLES = ("hero", "logo", "cover")
MAX_DOWNLOAD = 8 * 1024 ** 2          # one image
MAX_PIXELS = 16_000_000
MISS_TTL = 7 * 86400                   # remember "no ribbon here" for a week
ALLOWED_HOSTS = frozenset({
    "cdn.cloudflare.steamstatic.com", "shared.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com", "shared.akamai.steamstatic.com",
    "steamcdn-a.akamaihd.net",
})
EPIC_IMAGE_HOSTS = frozenset({"cdn1.epicgames.com"})
GOG_IMAGE_HOSTS = frozenset({"images.gog.com", "images-1.gog.com", "images-2.gog.com",
                             "images-3.gog.com", "images-4.gog.com"})
SGDB_HOSTS = frozenset({"www.steamgriddb.com"})
SGDB_IMAGE_HOSTS = frozenset({"cdn2.steamgriddb.com", "cdn.steamgriddb.com"})
SGDB_API = "https://www.steamgriddb.com/api/v2"
_EPIC_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_GAME_ID = re.compile(r"^[0-9]{1,12}$")
_SGDB_DELAY = 1.0
_sgdb_lock = threading.Lock()
_sgdb_last_request = 0.0


def cache_root() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "")
    data_home = Path(raw).expanduser() if raw else Path.home() / ".local/share"
    if not data_home.is_absolute():
        data_home = Path.home() / data_home
    return data_home / "waystone/artwork-cache"


def steam_urls(app_id: str) -> dict[str, list[str]]:
    """Candidate URLs for Steam's library hero and logo (old and new CDN layouts)."""
    if not str(app_id).isdigit():
        return {}
    a = str(app_id)
    return {
        "cover": [f"https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/{a}/library_600x900.jpg",
                  f"https://cdn.akamai.steamstatic.com/steam/apps/{a}/library_600x900.jpg"],
        "hero": [f"https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/{a}/library_hero.jpg",
                 f"https://cdn.cloudflare.steamstatic.com/steam/apps/{a}/library_hero.jpg"],
        "logo": [f"https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/{a}/logo.png",
                 f"https://cdn.cloudflare.steamstatic.com/steam/apps/{a}/logo.png"],
    }


def _valid_https_url(url: object, hosts: frozenset[str]) -> str | None:
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in hosts or parts.username or parts.password:
            return None
    except ValueError:
        return None
    return url


def epic_metadata_roots() -> tuple[Path, ...]:
    """Use only the app-owned XDG root and Heroic's documented user config root."""
    raw = os.environ.get("XDG_DATA_HOME", "")
    data_home = Path(raw).expanduser() if raw else Path.home() / ".local/share"
    if not data_home.is_absolute():
        data_home = Path.home() / data_home
    waystone = data_home / "waystone/providers-v1/epic/legendary"
    heroic = Path.home() / ".config/heroic/legendaryConfig/legendary"
    return (waystone, heroic)


def epic_urls(product_id: str, roots: Iterable[str | Path] | None = None) -> dict[str, list[str]]:
    """Read only exact Legendary metadata/<app>.json files; no account files or broad scans."""
    app_id = str(product_id or "")
    if not _EPIC_ID.fullmatch(app_id):
        return {}
    result = {"hero": [], "logo": [], "cover": []}
    for root in roots if roots is not None else epic_metadata_roots():
        path = Path(root) / "metadata" / f"{app_id}.json"
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(record, list):
                record = record[0] if record else {}
            metadata = record.get("metadata", record) if isinstance(record, dict) else {}
            images = metadata.get("keyImages", []) if isinstance(metadata, dict) else []
            by_type = {}
            for image in images if isinstance(images, list) else []:
                if not isinstance(image, dict):
                    continue
                kind = image.get("type")
                url = _valid_https_url(image.get("url"), EPIC_IMAGE_HOSTS)
                if url and kind not in by_type:
                    by_type[kind] = url
            hero = next((by_type[t] for t in ("DieselGameBox", "DieselGameBoxWide", "OfferImageWide")
                         if t in by_type), None)
            if hero and hero not in result["hero"]:
                result["hero"].append(hero)
            logo = by_type.get("DieselGameBoxLogo")
            if logo and logo not in result["logo"]:
                result["logo"].append(logo)
            cover = next((by_type[t] for t in ("DieselGameBoxTall", "OfferImageTall", "DieselGameBox")
                          if t in by_type), None)
            if cover and cover not in result["cover"]:
                result["cover"].append(cover)
        except (OSError, ValueError, TypeError):
            continue
    return {role: urls for role, urls in result.items() if urls}


def candidate_urls(provider: str, product_id: str) -> dict[str, list[str]]:
    provider = (provider or "").casefold()
    if provider == "steam":
        return steam_urls(product_id)
    if provider == "epic":
        return epic_urls(product_id)
    return {}


def _image_kind(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def validate_image(raw: bytes, decode: Callable[[bytes], tuple[int, int] | None] | None = None) -> str | None:
    """Return 'png'/'jpg' when raw is a real, sanely sized image, else None."""
    if not raw or len(raw) > MAX_DOWNLOAD:
        return None
    kind = _image_kind(raw)
    if kind is None:
        return None
    size = (decode or _qt_decode)(raw)
    if not size:
        return None
    w, h = size
    if w < 32 or h < 16 or w * h > MAX_PIXELS:
        return None
    return kind


def _qt_decode(raw: bytes) -> tuple[int, int] | None:
    from PySide6.QtGui import QImage
    image = QImage.fromData(raw)
    return None if image.isNull() else (image.width(), image.height())


def _http_get(url: str, timeout: float = 10.0) -> bytes | None:
    """https GET from an allowed host, at most MAX_DOWNLOAD bytes; None on any failure or a non-200."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALL_IMAGE_HOSTS:
        return None
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Waystone/1.0 (game library)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname not in ALL_IMAGE_HOSTS:
                return None
            raw = response.read(MAX_DOWNLOAD + 1)
            return raw if len(raw) <= MAX_DOWNLOAD else None
    except Exception:
        return None


ALL_IMAGE_HOSTS = ALLOWED_HOSTS | EPIC_IMAGE_HOSTS | GOG_IMAGE_HOSTS | SGDB_IMAGE_HOSTS


def _bounded_json_get(url: str, *, hosts: frozenset[str], headers: dict[str, str] | None = None,
                      limit: int = 2 * 1024 * 1024, timeout: float = 8.0) -> dict | None:
    if not _valid_https_url(url, hosts):
        return None
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Waystone/1.0 (game library)", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200 or not _valid_https_url(response.geturl(), hosts):
                return None
            raw = response.read(limit + 1)
            if len(raw) > limit:
                return None
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def gog_urls(product_id: str, *, get_json: Callable[[str], dict | None] = _bounded_json_get) -> dict[str, list[str]]:
    """Key-free GOG public product metadata (numeric GOG product ID only)."""
    product_id = str(product_id or "")
    if not _GAME_ID.fullmatch(product_id):
        return {}
    data = get_json(f"https://api.gog.com/products/{product_id}", hosts=frozenset({"api.gog.com"}))
    images = data.get("images") if isinstance(data, dict) else None
    if not isinstance(images, dict):
        return {}
    background = images.get("background")
    if isinstance(background, str) and background.startswith("//"):
        background = "https:" + background
    hero = _valid_https_url(background, GOG_IMAGE_HOSTS)
    logo_value = images.get("logo2x") or images.get("logo")
    if isinstance(logo_value, str) and logo_value.startswith("//"):
        logo_value = "https:" + logo_value
    logo = _valid_https_url(logo_value, GOG_IMAGE_HOSTS)
    cover_value = images.get("boxart") or images.get("cover") or images.get("coverimage")
    if isinstance(cover_value, str) and cover_value.startswith("//"):
        cover_value = "https:" + cover_value
    cover = _valid_https_url(cover_value, GOG_IMAGE_HOSTS)
    return {role: [url] for role, url in (("hero", hero), ("logo", logo), ("cover", cover)) if url}


def _sgdb_request(url: str, api_key: str, *, get_json=_bounded_json_get) -> dict | None:
    global _sgdb_last_request
    # One shared worker and >=1s between API calls keeps title fallbacks polite.
    with _sgdb_lock:
        delay = _SGDB_DELAY - (time.monotonic() - _sgdb_last_request)
        if delay > 0:
            time.sleep(delay)
        _sgdb_last_request = time.monotonic()
    return get_json(url, hosts=SGDB_HOSTS, headers={"Authorization": "Bearer " + api_key}, limit=512 * 1024)


def _sgdb_exact_game(query: str, api_key: str, *, steam_app_id: str = "", get_json=_bounded_json_get) -> int | None:
    if steam_app_id and _GAME_ID.fullmatch(steam_app_id):
        data = _sgdb_request(f"{SGDB_API}/games/steam/{steam_app_id}", api_key, get_json=get_json)
        rows = data.get("data") if isinstance(data, dict) else None
        if isinstance(rows, dict):
            rows = [rows]
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and type(rows[0].get("id")) is int:
            return rows[0]["id"]
    title = " ".join(query.split())
    if not title or len(title) > 180:
        return None
    data = _sgdb_request(f"{SGDB_API}/search/autocomplete/{quote(title, safe='')}", api_key, get_json=get_json)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    norm = lambda s: " ".join(re.sub(r"[^\w]+", " ", s.casefold()).split())
    exact = [row for row in rows if isinstance(row, dict)
             and norm(str(row.get("name", ""))) == norm(title) and type(row.get("id")) is int]
    if exact:
        exact.sort(key=lambda row: (row.get("verified") is True, row.get("id", 0)), reverse=True)
        return exact[0]["id"]
    return None


def steamgriddb_urls(query: str, *, api_key: str, steam_app_id: str = "",
                     get_json: Callable[..., dict | None] = _bounded_json_get) -> dict[str, list[str]]:
    """Exact-name SteamGridDB fallback; scores select the best hero/logo candidate."""
    if not isinstance(api_key, str) or not 8 <= len(api_key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
        return {}
    game_id = _sgdb_exact_game(query, api_key, steam_app_id=steam_app_id, get_json=get_json)
    if game_id is None:
        return {}
    found = {}
    for role in ("heroes", "logos", "grids"):
        data = _sgdb_request(f"{SGDB_API}/{role}/game/{game_id}", api_key, get_json=get_json)
        rows = data.get("data") if isinstance(data, dict) else None
        candidates = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = _valid_https_url(row.get("url"), SGDB_IMAGE_HOSTS)
            score = row.get("score", 0)
            if url and type(score) in (int, float):
                candidates.append((float(score), url))
        if candidates:
            found[{"heroes": "hero", "logos": "logo", "grids": "cover"}[role]] = [url for _, url in sorted(candidates, reverse=True)]
    return found


class RibbonCache:
    """Files: <root>/<key>/<role>.<ext>, plus index.json {key: {role: file, ..., "miss_at": t, "used_at": t}}."""

    def __init__(self, root: str | Path | None = None, *, max_bytes: int = 2 * 1024 ** 3,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root) if root else cache_root()
        self.max_bytes = max_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._index = self._load()

    @staticmethod
    def key(provider: str, product_id: str) -> str:
        return hashlib.sha256(f"{provider.casefold()}:{product_id}".encode()).hexdigest()[:32]

    def _load(self) -> dict:
        try:
            data = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "index.json.tmp"
        tmp.write_text(json.dumps(self._index), encoding="utf-8")
        os.replace(tmp, self.root / "index.json")

    def lookup(self, provider: str, product_id: str) -> dict[str, str]:
        """{'hero': path, 'logo': path} for whatever is cached (files that still exist)."""
        entry = self._index.get(self.key(provider, product_id)) or {}
        out = {}
        for role in ROLES:
            name = entry.get(role)
            if isinstance(name, str):
                path = self.root / self.key(provider, product_id) / name
                if path.is_file():
                    out[role] = str(path)
        return out

    def needs_fetch(self, provider: str, product_id: str) -> bool:
        entry = self._index.get(self.key(provider, product_id)) or {}
        if all(isinstance(entry.get(r), str) for r in ROLES):
            return False
        miss_at = entry.get("miss_at")
        return not (isinstance(miss_at, (int, float)) and self._clock() - miss_at < MISS_TTL)

    def store(self, provider: str, product_id: str, found: dict[str, tuple[bytes, str]]) -> dict[str, str]:
        """Save validated images {role: (raw, ext)}; record a miss when nothing was found."""
        key = self.key(provider, product_id)
        with self._lock:
            entry = dict(self._index.get(key) or {})
            now = self._clock()
            if found:
                folder = self.root / key
                folder.mkdir(parents=True, exist_ok=True)
                for role, (raw, ext) in found.items():
                    name = f"{role}.{ext}"
                    tmp = folder / f".{name}.tmp"
                    tmp.write_bytes(raw)
                    os.replace(tmp, folder / name)
                    entry[role] = name
            # Anything still missing (nothing found, or a hero without a logo) waits MISS_TTL before a retry.
            if all(isinstance(entry.get(r), str) for r in ROLES):
                entry.pop("miss_at", None)
            else:
                entry["miss_at"] = now
            entry["used_at"] = now
            self._index[key] = entry
            self._prune()
            self._save()
        return self.lookup(provider, product_id)

    def _prune(self) -> None:
        sizes = []
        total = 0
        for key, entry in self._index.items():
            folder = self.root / key
            size = sum(p.stat().st_size for p in folder.glob("*") if p.is_file()) if folder.is_dir() else 0
            total += size
            sizes.append((entry.get("used_at", 0), key, size))
        for _used, key, size in sorted(sizes):
            if total <= self.max_bytes:
                break
            folder = self.root / key
            for p in folder.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass
            self._index.pop(key, None)
            total -= size


def fetch_ribbon(provider: str, product_id: str, *, get: Callable[[str], bytes | None] = _http_get,
                 decode: Callable[[bytes], tuple[int, int] | None] | None = None,
                 sgdb_key: str = "", get_json: Callable[..., dict | None] = _bounded_json_get) -> dict[str, tuple[bytes, str]]:
    """Try each candidate URL per role; return the validated images found."""
    found: dict[str, tuple[bytes, str]] = {}
    provider = (provider or "").casefold()
    if provider == "gog":
        candidates = gog_urls(product_id, get_json=get_json)
    elif provider == "steamgriddb":
        try:
            request = json.loads(product_id)
            title = request.get("title", "") if isinstance(request, dict) else ""
            app_id = request.get("steam_app_id", "") if isinstance(request, dict) else ""
        except (ValueError, TypeError):
            title, app_id = product_id, ""
        candidates = steamgriddb_urls(title, api_key=sgdb_key, steam_app_id=app_id, get_json=get_json) if sgdb_key else {}
    else:
        candidates = candidate_urls(provider, product_id)
    for role, urls in candidates.items():
        for url in urls:
            raw = get(url)
            kind = validate_image(raw, decode) if raw else None
            if kind:
                found[role] = (raw, kind)
                break
    return found


class RibbonFetcher:
    """One background worker. `request()` from the UI thread; `on_ready(provider, product_id, paths)` is called
    from the worker thread, so a Qt caller should forward it through a queued signal."""

    def __init__(self, cache: RibbonCache, *, on_ready: Callable[[str, str, dict], None] | None = None,
                 fetch: Callable[..., dict] = fetch_ribbon, start: bool = True):
        self.cache = cache
        self.on_ready = on_ready or (lambda *_: None)
        self._fetch = fetch
        self._queue: "queue.Queue[tuple[str, str] | None]" = queue.Queue()
        self._pending: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="waystone-ribbons", daemon=True)
        if start:
            self._thread.start()

    def request(self, provider: str, product_id: str) -> bool:
        """Queue a fetch if this game has no cached ribbon and no recent miss. Returns True when queued."""
        provider = provider.casefold()
        if (provider not in {"steam", "epic", "gog", "steamgriddb"}
                or (provider == "gog" and not _GAME_ID.fullmatch(str(product_id)))
                or (provider == "epic" and not _EPIC_ID.fullmatch(str(product_id)))
                or not self.cache.needs_fetch(provider, product_id)):
            return False
        item = (provider, str(product_id))
        with self._lock:
            if item in self._pending:
                return False
            self._pending.add(item)
        self._queue.put(item)
        return True

    def request_many(self, items: Iterable[tuple[str, str]]) -> int:
        return sum(1 for provider, product_id in items if self.request(provider, product_id))

    def run_once(self) -> bool:
        """Process one queued item (tests); False when the queue is empty."""
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            return False
        self._handle(item)
        return True

    def stop(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            self._handle(item)

    def _handle(self, item: tuple[str, str]) -> None:
        provider, product_id = item
        try:
            found = self._fetch(provider, product_id)
            if found is not None:
                paths = self.cache.store(provider, product_id, found)
                if paths:
                    self.on_ready(provider, product_id, paths)
        except Exception:
            pass
        finally:
            with self._lock:
                self._pending.discard(item)
