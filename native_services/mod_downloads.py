"""Pure-Python mod file listing and safe downloads for Waystone phase 2a.

No Qt and no network in the listing helpers: both accept a ``fetch`` callable
with the same signature as :func:`native_services.mod_catalog.http_json`, so
callers and tests can inject their own transport.  :func:`download` accepts an
``opener`` for the same reason and never follows a non-HTTPS URL.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import mod_catalog

GB_MOD_URL = "https://gamebanana.com/apiv11/Mod/{mod_id}"
MR_VERSION_URL = "https://api.modrinth.com/v2/project/{project_id}/version"

_CHUNK_SIZE = 1024 * 1024
_SAFE_RE = re.compile(r"[^A-Za-z0-9._ ()+-]")


class DownloadError(Exception):
    """Raised when a download cannot be performed or fails verification."""


# ---------------------------------------------------------------------------
# Small coercion helpers: entries are always complete and never carry None.
# ---------------------------------------------------------------------------

def _text(value) -> str:
    return "" if value is None else str(value)


def _integer(value) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _text_list(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item) for item in value if item is not None]


def _unix_iso(value) -> str:
    if value is None or value == "":
        return ""
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _nested(value, key):
    return value.get(key) if isinstance(value, dict) else None


def _normalise_hash(value) -> str:
    return _text(value).strip().lower()


# ---------------------------------------------------------------------------
# GameBanana
# ---------------------------------------------------------------------------

def gamebanana_files(mod_id: str | int, *,
                     fetch: Callable[..., object] = mod_catalog.http_json
                     ) -> list[dict]:
    """List the downloadable files attached to one GameBanana mod."""
    safe_id = urllib.parse.quote(_text(mod_id), safe="")
    url = GB_MOD_URL.format(mod_id=safe_id) + "?_csvProperties=_aFiles,_sName"
    payload = fetch(url)
    if not isinstance(payload, dict):
        return []

    entries = []
    for record in payload.get("_aFiles") or []:
        if not isinstance(record, dict):
            continue
        added = record.get("_tsDateAdded")
        try:
            sort_key = float(added)
        except (TypeError, ValueError, OverflowError):
            sort_key = 0.0
        entries.append((sort_key, {
            "file_id": _text(record.get("_idRow")),
            "name": _text(record.get("_sFile")),
            "size": _integer(record.get("_nFilesize")),
            "url": _text(record.get("_sDownloadUrl")),
            "md5": _text(record.get("_sMd5Checksum")),
            "sha1": "",
            "description": _text(record.get("_sDescription")),
            "date": _unix_iso(added),
            "game_versions": [],
            "loaders": [],
        }))

    entries.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _key, item in entries]


# ---------------------------------------------------------------------------
# Modrinth
# ---------------------------------------------------------------------------

def _primary_file(files):
    if not isinstance(files, (list, tuple)):
        return None
    fallback = None
    for candidate in files:
        if not isinstance(candidate, dict):
            continue
        if fallback is None:
            fallback = candidate
        if candidate.get("primary"):
            return candidate
    return fallback


def modrinth_files(project_id: str, *,
                   loaders: list[str] | None = None,
                   game_versions: list[str] | None = None,
                   fetch: Callable[..., object] = mod_catalog.http_json
                   ) -> list[dict]:
    """List the versions (with their primary file) of a Modrinth project."""
    safe_id = urllib.parse.quote(_text(project_id), safe="")
    url = MR_VERSION_URL.format(project_id=safe_id)
    params = []
    if loaders is not None:
        params.append(("loaders", json.dumps([_text(item) for item in loaders])))
    if game_versions is not None:
        params.append(("game_versions",
                       json.dumps([_text(item) for item in game_versions])))
    if params:
        url += "?" + urllib.parse.urlencode(params)

    payload = fetch(url)
    if not isinstance(payload, list):
        return []

    results = []
    for version in payload:
        if not isinstance(version, dict):
            continue
        chosen = _primary_file(version.get("files"))
        if chosen is None:
            continue
        results.append({
            "file_id": _text(version.get("id")),
            "name": _text(chosen.get("filename")),
            "size": _integer(chosen.get("size")),
            "url": _text(chosen.get("url")),
            "md5": "",
            "sha1": _text(_nested(chosen.get("hashes"), "sha1")),
            "description": _text(version.get("name")),
            "date": _text(version.get("date_published")),
            "game_versions": _text_list(version.get("game_versions")),
            "loaders": _text_list(version.get("loaders")),
        })
    return results


# ---------------------------------------------------------------------------
# Safe filenames
# ---------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    """Reduce *name* to a single harmless basename inside a download folder."""
    text = _text(name).replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(ch for ch in text if ord(ch) >= 32 and ord(ch) != 127)
    text = text.lstrip(".")
    text = _SAFE_RE.sub("_", text)
    return text or "download"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _file_digests(path: Path) -> dict:
    digests = {"md5": hashlib.md5(), "sha1": hashlib.sha1()}
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            for digest in digests.values():
                digest.update(chunk)
    return {key: digest.hexdigest() for key, digest in digests.items()}


def _matches_existing(path: Path, md5: str, sha1: str) -> bool:
    if not (md5 or sha1):
        return False
    try:
        actual = _file_digests(path)
    except OSError:
        return False
    if md5 and actual["md5"] != _normalise_hash(md5):
        return False
    if sha1 and actual["sha1"] != _normalise_hash(sha1):
        return False
    return True


def _content_length(response) -> int:
    headers = getattr(response, "headers", None)
    if headers is None:
        return 0
    try:
        value = headers.get("Content-Length")
        if value is None:
            value = headers.get("content-length")
        return max(0, int(value or 0))
    except (TypeError, ValueError, AttributeError):
        return 0


def _close(response) -> None:
    closer = getattr(response, "close", None)
    if callable(closer):
        closer()


def download(url: str, dest_dir: str | Path, *, filename: str,
             md5: str = "", sha1: str = "",
             max_bytes: int = 4 * 1024 ** 3,
             opener=None, progress=None) -> Path:
    """Download *url* into *dest_dir* and return the verified final path.

    The body is streamed into a ``.part`` file and only moved into place with
    :func:`os.replace` after the size and any supplied checksum checks pass.
    """
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise DownloadError("only https:// URLs are supported")

    destination = Path(dest_dir)
    final_path = destination / safe_filename(filename)

    if final_path.is_file() and _matches_existing(final_path, md5, sha1):
        return final_path

    destination.mkdir(parents=True, exist_ok=True)
    part_path = final_path.with_name(final_path.name + ".part")

    request = urllib.request.Request(
        url, headers={"User-Agent": mod_catalog.USER_AGENT})
    open_url = opener if opener is not None else urllib.request.urlopen

    response = open_url(request, timeout=60)
    md5_hash = hashlib.md5()
    sha1_hash = hashlib.sha1()
    written = 0
    total = _content_length(response)

    try:
        with open(part_path, "wb") as handle:
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                md5_hash.update(chunk)
                sha1_hash.update(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(written, total)
                if written > max_bytes:
                    raise DownloadError(
                        "download exceeds max_bytes of %d" % max_bytes)
    except BaseException:
        _close(response)
        try:
            part_path.unlink()
        except OSError:
            pass
        raise
    else:
        _close(response)

    if md5 and md5_hash.hexdigest() != _normalise_hash(md5):
        part_path.unlink(missing_ok=True)
        raise DownloadError("checksum mismatch")
    if sha1 and sha1_hash.hexdigest() != _normalise_hash(sha1):
        part_path.unlink(missing_ok=True)
        raise DownloadError("checksum mismatch")

    os.replace(part_path, final_path)
    return final_path
