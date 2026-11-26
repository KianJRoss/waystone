"""Which games use anti-cheat (competitive games), from the public AreWeAntiCheatYet list.

Kian 2026-11-25: "ideally we have just the straight passthrough on competitive game and not even worry about the
controller unless they want to". Waystone uses this to give such games the real controller (no virtual pad) unless
the game's controller profile asks for a virtual pad.

Sources, first found wins: Waystone's own cached copy (~/.local/share/waystone/anticheat-games.json, refreshed at
most weekly from the project's GitHub), else Heroic's cached copy (~/.config/heroic/areweanticheatyet.json).
Qt-free; every path and the network fetch are injectable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
import unicodedata
from typing import Callable, Iterable

SOURCE_URL = "https://raw.githubusercontent.com/AreWeAntiCheatYet/AreWeAntiCheatYet/master/games.json"
MAX_BYTES = 8 * 1024 ** 2
REFRESH_AFTER = 7 * 86400


def waystone_cache(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local/share/waystone/anticheat-games.json"


def heroic_cache(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config/heroic/areweanticheatyet.json"


def normalize_title(title: str) -> str:
    """'Rocket League®' and 'rocket league' match; trademark signs, punctuation and case are ignored."""
    text = re.sub(r"[®™©℠]", "", str(title or ""))        # before NFKD, which turns ™ into "TM"
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^0-9a-z]+", " ", text).strip()


class AntiCheatIndex:
    def __init__(self, entries: Iterable[dict] = ()):
        self._names: dict[str, list[str]] = {}
        self._steam: dict[str, list[str]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            anticheats = [str(a) for a in entry.get("anticheats") or [] if a]
            if not anticheats:
                continue
            name = normalize_title(entry.get("name", ""))
            if name:
                self._names[name] = anticheats
            store_ids = entry.get("storeIds") if isinstance(entry.get("storeIds"), dict) else {}
            steam = str(store_ids.get("steam") or "").strip()
            if steam.isdigit():
                self._steam[steam] = anticheats

    def __len__(self) -> int:
        return len(self._names)

    def anticheats(self, title: str = "", steam_app_id: str = "") -> list[str]:
        """Anti-cheat names for a game, or [] when it has none we know of."""
        if str(steam_app_id or "").isdigit() and str(steam_app_id) in self._steam:
            return list(self._steam[str(steam_app_id)])
        return list(self._names.get(normalize_title(title), []))


def _read_entries(path: Path) -> list[dict] | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def load_index(home: Path | None = None) -> AntiCheatIndex:
    for path in (waystone_cache(home), heroic_cache(home)):
        entries = _read_entries(path)
        if entries:
            return AntiCheatIndex(entries)
    return AntiCheatIndex()


def _http_get(url: str, timeout: float = 20.0) -> bytes | None:
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Waystone"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_BYTES + 1)


def refresh_cache(home: Path | None = None, *, get: Callable[[str], bytes | None] = _http_get,
                  now: Callable[[], float] = time.time) -> bool:
    """Download the list when Waystone's copy is missing or older than a week. Returns True when updated."""
    path = waystone_cache(home)
    try:
        if now() - path.stat().st_mtime < REFRESH_AFTER:
            return False
    except OSError:
        pass
    try:
        raw = get(SOURCE_URL)
    except Exception:
        return False
    if not raw or len(raw) > MAX_BYTES:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, list) or not any(isinstance(e, dict) and e.get("anticheats") for e in data):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    return True
