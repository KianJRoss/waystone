"""Nexus Mods v1 API with the user's personal API key (kept in the desktop keyring, never in files or logs).

Free (non-Premium) accounts may only download through an nxm:// link that the website hands out when the user
presses "Mod manager download": the link's key + expires authorize one download. Premium accounts can request
download links directly. Both paths end in `download_url`.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone

from native_services import mod_catalog

API = "https://api.nexusmods.com/v1"
KEYRING_ATTRS = ("service", "waystone", "username", "nexus")


class NexusError(Exception):
    pass


def api_key() -> str | None:
    try:
        out = subprocess.run(["secret-tool", "lookup", *KEYRING_ATTRS], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    key = out.stdout.strip()
    return key or None


def store_api_key(key: str) -> None:
    """Save the key in the desktop keyring (Secret Service). Passed on stdin, never on a command line."""
    key = (key or "").strip()
    if not key:
        raise NexusError("empty key")
    out = subprocess.run(["secret-tool", "store", "--label=Waystone Nexus API key", *KEYRING_ATTRS],
                         input=key, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise NexusError("could not save the key in the system keyring: " + (out.stderr.strip() or "secret-tool failed"))


def clear_api_key() -> None:
    subprocess.run(["secret-tool", "clear", *KEYRING_ATTRS], capture_output=True, timeout=15)


def _get(path: str, key: str, fetch=None) -> object:
    """GET with the apikey header. `fetch(url, headers)` may be injected by tests."""
    url = API + path
    headers = {"apikey": key, "User-Agent": mod_catalog.USER_AGENT, "Accept": "application/json"}
    if fetch is not None:
        return fetch(url, headers)
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get("message")
        except Exception:
            message = None
        raise NexusError(message or f"Nexus answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NexusError(f"Nexus is unreachable: {exc}") from None


_NXM = re.compile(r"^nxm://(?P<domain>[a-z0-9_-]+)/mods/(?P<mod>\d+)/files/(?P<file>\d+)/?(?:\?(?P<q>.*))?$", re.I)


def parse_nxm(url: str) -> dict:
    m = _NXM.match((url or "").strip())
    if not m:
        raise NexusError("not a Nexus mod-manager link")
    q = urllib.parse.parse_qs(m.group("q") or "")
    return {"domain": m.group("domain").lower(), "mod_id": int(m.group("mod")), "file_id": int(m.group("file")),
            "key": (q.get("key") or [""])[0], "expires": (q.get("expires") or [""])[0],
            "user_id": (q.get("user_id") or [""])[0]}


def mod_files(domain: str, mod_id: int | str, key: str, *, fetch=None) -> list[dict]:
    data = _get(f"/games/{domain}/mods/{int(mod_id)}/files.json", key, fetch)
    files = data.get("files", []) if isinstance(data, dict) else []
    out = []
    for f in files:
        if not isinstance(f, dict) or f.get("category_name") in ("ARCHIVED", "DELETED"):
            continue
        ts = f.get("uploaded_timestamp")
        out.append({
            "file_id": str(f.get("file_id") or ""), "name": str(f.get("file_name") or f.get("name") or ""),
            "size": int(f.get("size_in_bytes") or (f.get("size_kb") or 0) * 1024),
            "url": "", "md5": "", "sha1": "",
            "description": f"{f.get('name') or ''} {f.get('version') or ''} · {(f.get('category_name') or '').title()}".strip(),
            "date": datetime.fromtimestamp(ts, timezone.utc).isoformat() if isinstance(ts, (int, float)) else "",
            "game_versions": [], "loaders": [], "primary": bool(f.get("is_primary")),
            "category": str(f.get("category_name") or ""),
        })
    order = {"MAIN": 0, "UPDATE": 1, "OPTIONAL": 2, "MISCELLANEOUS": 3, "OLD_VERSION": 4}
    out.sort(key=lambda f: (not f["primary"], order.get(f["category"], 5), -(datetime.fromisoformat(f["date"]).timestamp() if f["date"] else 0)))
    return out


def download_url(domain: str, mod_id: int | str, file_id: int | str, key: str, *, nxm_key: str = "",
                 expires: str = "", fetch=None) -> str:
    path = f"/games/{domain}/mods/{int(mod_id)}/files/{int(file_id)}/download_link.json"
    if nxm_key:
        path += "?" + urllib.parse.urlencode({"key": nxm_key, "expires": expires})
    data = _get(path, key, fetch)
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("URI"):
        return str(data[0]["URI"])
    if isinstance(data, dict) and data.get("message"):
        raise NexusError(str(data["message"]))
    raise NexusError("Nexus returned no download link")


def validate(key: str, *, fetch=None) -> dict:
    data = _get("/users/validate.json", key, fetch)
    if not isinstance(data, dict) or "name" not in data:
        raise NexusError("Nexus did not accept the API key")
    return {"name": data.get("name"), "premium": bool(data.get("is_premium"))}
