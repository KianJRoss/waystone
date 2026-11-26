"""Pure-Python, network-injectable mod catalog clients for Waystone phase 1.

No Qt, no import-time side effects and no real network in the search helpers:
every search accepts a ``fetch`` callable with the same signature as
:func:`http_json`, so callers and tests can supply their own transport.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode

USER_AGENT = "waystone/0.1 (github.com/KianJRoss)"

NEXUS_GRAPHQL = "https://api.nexusmods.com/v2/graphql"

_NEXUS_MODS_QUERY = (
    "query($filter: ModsFilter, $sort: [ModsSort!], $count: Int, $offset: Int)"
    "{ mods(filter: $filter, sort: $sort, count: $count, offset: $offset){ totalCount "
    "nodes { modId name summary author updatedAt downloads endorsements "
    "thumbnailUrl game { domainName name } } } }"
)

_NEXUS_GAMES_QUERY = (
    "query($f: GamesSearchFilter){ games(filter: $f, count: 10){ "
    "nodes { id domainName name } } }"
)

_EMULATOR_PLATFORM_TOKENS = (
    "Nintendo", "PlayStation", "Sony", "Sega", "Xbox 360", "Wii",
    "GameCube", "3DS", "Switch",
)

Fetch = Callable[..., dict]


class CatalogError(Exception):
    """Raised on HTTP, URL or JSON failures while talking to a catalog."""


# ---------------------------------------------------------------------------
# Small coercion helpers: every item is complete, and never carries None.
# ---------------------------------------------------------------------------

def _text(value) -> str:
    return "" if value is None else str(value)


def _https_image(value) -> str:
    """Thumbnails reach the UI only as plain https URLs (no file:, data:, http:, credentials or relative paths)."""
    if not isinstance(value, str):
        return ""
    url = value.strip()
    parts = urllib.parse.urlsplit(url)
    return url if parts.scheme == "https" and parts.netloc and not parts.username and not parts.password else ""


def _number(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _nested(value, key):
    return value.get(key) if isinstance(value, dict) else None


def _make_item(platform, mod_id, *, title="", summary="", author="",
               updated_at="", thumbnail_url="", downloads=0, likes=0,
               url="", game="") -> dict:
    return {
        "platform": _text(platform),
        "mod_id": _text(mod_id),
        "title": _text(title),
        "summary": _text(summary),
        "author": _text(author),
        "updated_at": _text(updated_at),
        "thumbnail_url": _https_image(thumbnail_url),
        "downloads": _number(downloads),
        "likes": _number(likes),
        "url": _text(url),
        "game": _text(game),
    }


def _result(total, items) -> dict:
    return {"total": _number(total), "items": list(items)}


def _unix_iso(value) -> str:
    if value is None or value == "":
        return ""
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _nexus_error(payload) -> None:
    if not isinstance(payload, dict) or not payload.get("errors"):
        return
    errors = payload["errors"]
    first = errors[0] if isinstance(errors, (list, tuple)) and errors else errors
    if isinstance(first, dict):
        raise CatalogError(_text(first.get("message")))
    raise CatalogError(_text(first))


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def http_json(url: str, *, body: dict | None = None, timeout: float = 15.0) -> dict:
    """Make one JSON request and return the decoded dict.

    GET is used when *body* is None; otherwise a JSON POST is sent. Any
    transport, HTTP or decoding failure is reported as :class:`CatalogError`.
    """
    try:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        response = urllib.request.urlopen(request, timeout=timeout)
        try:
            raw = response.read()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise CatalogError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Modrinth
# ---------------------------------------------------------------------------

def search_modrinth(query: str, *, project_type: str = "mod", limit: int = 20,
                    offset: int = 0, fetch: Fetch = http_json) -> dict:
    facets = json.dumps([["project_type:" + _text(project_type)]])
    url = "https://api.modrinth.com/v2/search?" + urlencode({
        "query": query,
        "limit": limit,
        "offset": offset,
        "facets": facets,
    })
    payload = fetch(url)
    if not isinstance(payload, dict):
        payload = {}
    items = []
    for hit in payload.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        items.append(_make_item(
            "modrinth",
            hit.get("project_id"),
            title=hit.get("title"),
            summary=hit.get("description"),
            author=hit.get("author"),
            updated_at=hit.get("date_modified"),
            thumbnail_url=hit.get("icon_url") or "",
            downloads=hit.get("downloads"),
            likes=hit.get("follows"),
            url="https://modrinth.com/" + _text(project_type) + "/" + _text(hit.get("slug")),
            game="Minecraft",
        ))
    return _result(payload.get("total_hits", 0), items)


# ---------------------------------------------------------------------------
# GameBanana
# ---------------------------------------------------------------------------

def _gamebanana_thumb(record: dict) -> str:
    media = record.get("_aPreviewMedia")
    images = _nested(media, "_aImages") or []
    if not isinstance(images, (list, tuple)) or not images:
        return ""
    first = images[0]
    if not isinstance(first, dict):
        return ""
    base = first.get("_sBaseUrl")
    filename = first.get("_sFile220")
    if not base or not filename:
        return ""
    return _text(base).rstrip("/") + "/" + _text(filename)


def search_gamebanana(query: str = "", *, game_id: int | None = None,
                      page: int = 1, fetch: Fetch = http_json) -> dict:
    if not query and game_id is None:
        return {"total": 0, "items": []}

    if query:
        params = {
            "_sModelName": "Mod",
            "_sOrder": "best_match",
            "_sSearchString": query,
            "_nPage": page,
        }
        if game_id is not None:
            params["_idGameRow"] = game_id
        url = ("https://gamebanana.com/apiv11/Util/Search/Results?"
               + urlencode(params))
    else:
        url = ("https://gamebanana.com/apiv11/Game/" + str(game_id)
               + "/Subfeed?" + urlencode({"_nPage": page, "_sSort": "default"}))

    payload = fetch(url)
    if not isinstance(payload, dict):
        payload = {}
    metadata = payload.get("_aMetadata")
    items = []
    for record in payload.get("_aRecords") or []:
        if not isinstance(record, dict):
            continue
        if "_sModelName" in record and record["_sModelName"] != "Mod":
            continue
        items.append(_make_item(
            "gamebanana",
            record.get("_idRow"),
            title=record.get("_sName"),
            summary="",
            author=_nested(record.get("_aSubmitter"), "_sName"),
            updated_at=_unix_iso(record.get("_tsDateModified")),
            thumbnail_url=_gamebanana_thumb(record),
            downloads=record.get("_nViewCount"),
            likes=record.get("_nLikeCount"),
            url=record.get("_sProfileUrl"),
            game=_nested(record.get("_aGame"), "_sName"),
        ))
    return _result(_nested(metadata, "_nRecordCount"), items)


def find_gamebanana_games(name: str, *, fetch: Fetch = http_json) -> list[dict]:
    url = "https://gamebanana.com/apiv11/Util/Search/Results?" + urlencode({
        "_sModelName": "Game",
        "_sOrder": "best_match",
        "_sSearchString": name,
        "_nPage": 1,
    })
    payload = fetch(url)
    if not isinstance(payload, dict):
        return []
    results = []
    for record in payload.get("_aRecords") or []:
        if not isinstance(record, dict):
            continue
        results.append({
            "id": _number(record.get("_idRow")),
            "name": _text(record.get("_sName")),
        })
    return results


# ---------------------------------------------------------------------------
# Nexus Mods
# ---------------------------------------------------------------------------

def search_nexus(query: str, *, game_domain: str | None = None, count: int = 20,
                 offset: int = 0, fetch: Fetch = http_json) -> dict:
    filt = {}
    if query:
        # Nexus WILDCARD on a plain value already means "name contains"; '*' makes it match nothing.
        filt["name"] = {"value": query, "op": "WILDCARD"}
    if game_domain:
        filt["gameDomainName"] = {"value": game_domain, "op": "EQUALS"}
    payload = fetch(NEXUS_GRAPHQL, body={
        "query": _NEXUS_MODS_QUERY,
        "variables": {"filter": filt, "sort": [{"endorsements": {"direction": "DESC"}}],
                      "count": count, "offset": offset},
    })
    _nexus_error(payload)
    mods = _nested(payload.get("data"), "mods") if isinstance(payload, dict) else None
    mods = mods if isinstance(mods, dict) else {}
    items = []
    for node in mods.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        game = node.get("game")
        domain = _nested(game, "domainName")
        mod_id = node.get("modId")
        items.append(_make_item(
            "nexus",
            mod_id,
            title=node.get("name"),
            summary=node.get("summary"),
            author=node.get("author"),
            updated_at=node.get("updatedAt"),
            thumbnail_url=node.get("thumbnailUrl") or "",
            downloads=node.get("downloads"),
            likes=node.get("endorsements"),
            url=("https://www.nexusmods.com/" + _text(domain)
                 + "/mods/" + _text(mod_id)),
            game=_nested(game, "name"),
        ))
    return _result(mods.get("totalCount", 0), items)


def find_nexus_games(name: str, *, fetch: Fetch = http_json) -> list[dict]:
    def lookup(op: str) -> list:
        payload = fetch(NEXUS_GRAPHQL, body={
            "query": _NEXUS_GAMES_QUERY,
            "variables": {"f": {"name": {"value": name, "op": op}}},
        })
        _nexus_error(payload)
        games = _nested(payload.get("data"), "games") if isinstance(payload, dict) else None
        games = games if isinstance(games, dict) else {}
        return games.get("nodes") or []

    exact = lookup("EQUALS")
    partial = lookup("MATCHES")
    results = []
    seen = set()
    for node in list(exact) + list(partial):
        if not isinstance(node, dict):
            continue
        domain = _text(node.get("domainName"))
        if domain in seen:
            continue
        seen.add(domain)
        results.append({
            "id": _number(node.get("id")),
            "domain": domain,
            "name": _text(node.get("name")),
        })
    return results


# ---------------------------------------------------------------------------
# Platform routing
# ---------------------------------------------------------------------------

def platforms_for(game: dict) -> list[str]:
    if not isinstance(game, dict):
        game = {}
    title = _text(game.get("title")).lower()
    if "minecraft" in title:
        return ["modrinth"]

    platforms = game.get("platforms") or []
    if not isinstance(platforms, (list, tuple)):
        platforms = [platforms]
    tokens = tuple(token.lower() for token in _EMULATOR_PLATFORM_TOKENS)
    for platform in platforms:
        lowered = _text(platform).lower()
        if any(token in lowered for token in tokens):
            return ["gamebanana"]

    try:
        from private_addons import EMULATION_SOURCE_NAMES as _ADDON_EMU_SOURCES
    except ImportError:
        _ADDON_EMU_SOURCES = set()
    if game.get("source") in ({"Emulation"} | set(_ADDON_EMU_SOURCES)):
        return ["gamebanana"]
    return ["nexus", "gamebanana"]
