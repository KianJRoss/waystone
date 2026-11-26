"""App-owned local library entries: modlist instances (e.g. Skyrim + Nolvus) and games no store knows about.

The imported library stays immutable. Local entries live in one app-owned JSON file and are merged into the
catalog projection at startup as their own works, with ids starting `local:`. An entry may be `based_on` an
existing work (e.g. `steam:489830`): it then reuses that work's artwork and descriptive metadata, so a modlist
looks like the game it is built on. Launching goes through the normal launch profile store: the entry's id is
the launch target (provider `local`), associated with an app-owned `native_game` profile (see add_local_game).

File shape (schema 1):
    {"schema": 1, "games": [{"id": "local:nolvus-ascension", "title": "...", "based_on": "steam:489830",
                             "platform": "PC (Windows)", "tags": ["Modlist"], "notes": "..."}]}
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path

from native_services.native_catalog import FIELDS, NAME_FIELDS

SCHEMA = 1
SOURCE = "Local"
PROVIDER = "local"
MAX_BYTES = 1024 * 1024
_ID = re.compile(r"^local:[a-z0-9][a-z0-9._-]{0,80}$")


class LocalGamesError(ValueError):
    pass


def _validate(entry) -> dict:
    if not isinstance(entry, dict):
        raise LocalGamesError("each local game must be an object")
    gid, title = entry.get("id"), entry.get("title")
    if not isinstance(gid, str) or not _ID.match(gid):
        raise LocalGamesError(f"local game id must look like 'local:name' (lowercase), got {gid!r}")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise LocalGamesError(f"{gid}: title is required (max 200 characters)")
    for key in ("based_on", "platform", "notes"):
        if key in entry and not isinstance(entry[key], str):
            raise LocalGamesError(f"{gid}: {key} must be text")
    tags = entry.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise LocalGamesError(f"{gid}: tags must be a list of text")
    return entry


def load(path) -> list[dict]:
    """Validated entries from the app-owned file; a missing file means no entries."""
    path = Path(path)
    if not path.exists():
        return []
    if path.stat().st_size > MAX_BYTES:
        raise LocalGamesError("local games file is larger than 1 MiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("games"), list):
        raise LocalGamesError("local games file must be {'schema': 1, 'games': [...]}")
    seen, out = set(), []
    for entry in data["games"]:
        entry = _validate(entry)
        if entry["id"] in seen:
            raise LocalGamesError(f"duplicate local game id {entry['id']}")
        seen.add(entry["id"])
        out.append(entry)
    return out


def upsert(path, entry: dict) -> None:
    """Add or replace one entry (atomic write)."""
    entry = _validate(dict(entry))
    path = Path(path)
    games = [g for g in load(path) if g["id"] != entry["id"]] + [entry]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".local-games-", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"schema": SCHEMA, "games": games}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _empty_metadata() -> dict:
    meta = {key: [] for key in NAME_FIELDS}
    meta["completion"] = None
    meta.update({key: None for key in FIELDS})
    meta.update({"available_fields": [], "description": "", "developer_ids": [], "publisher_ids": [],
                 "completion_id": None, "relationships": []})
    return meta


def _best_variant(game: dict) -> dict | None:
    variants = [v for v in game.get("variants", []) if isinstance(v, dict)]
    return max(variants, key=lambda v: len((v.get("metadata") or {}).get("available_fields") or []), default=None)


def project(entry: dict, base: dict | None) -> dict:
    """The catalog work for one local entry (one installed variant, launch target = entry id)."""
    base_variant = _best_variant(base) if base else None
    meta = copy.deepcopy(base_variant["metadata"]) if base_variant and isinstance(base_variant.get("metadata"), dict) \
        else _empty_metadata()
    # Activity belongs to the instance, not the base game.
    for key in ("last_activity", "playtime_seconds", "play_count", "added", "modified", "favorite", "hidden",
                "install_size", "completion", "completion_id", "notes", "user_score", "user_score_rating"):
        meta[key] = [] if isinstance(meta.get(key), list) else None
    meta["relationships"] = []
    if entry.get("notes"):
        meta["notes"] = entry["notes"]
    meta["tags"] = sorted(set(meta.get("tags") or []) | set(entry.get("tags", [])))
    meta["provenance"] = {"kind": "app_owned_local_entry", "source_id": entry["id"],
                          "based_on": entry.get("based_on") or None}
    variant = {
        "source": SOURCE, "source_id": entry["id"], "provider_game_id": entry["id"],
        "installation_id": "local-entry:" + entry["id"],
        "platform": entry.get("platform") or (base_variant or {}).get("platform") or "PC (Windows)",
        "status": "installed", "recipe": [], "metadata": meta,
        "source_context": {"source": "waystone-local", "provider": SOURCE},
        "observation": {"kind": "app_owned_local_entry", "installation_verified": False},
        "evidence": {"kind": "app_owned_local_entry", "based_on": entry.get("based_on") or ""},
        "artwork": copy.deepcopy(base_variant.get("artwork", {})) if base_variant else {},
    }
    return {"id": entry["id"], "title": entry["title"].strip(), "variants": [variant]}


def merge_into_catalog(data: dict, entries: list[dict]) -> int:
    """Append local works to a catalog projection in place; returns how many were added."""
    games = data["games"]
    by_id = {g.get("id"): g for g in games if isinstance(g, dict)}
    added = 0
    for entry in entries:
        if entry["id"] in by_id:
            continue
        work = project(entry, by_id.get(entry.get("based_on")))
        games.append(work)
        by_id[work["id"]] = work
        added += 1
    return added
