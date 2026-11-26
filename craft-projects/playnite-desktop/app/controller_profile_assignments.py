"""Pure-Python controller profile assignment model.

Additive on-disk document shape over the existing profiles.json:
  {
    "version": 1,
    "active_profile": "...",
    "profiles": {"Name": {...opaque...}, ...},
    "virtual_gamepad_driver": "...",
    "default_profile": "...",          # added
    "game_profiles": {"game_id": "ProfileName", ...}  # added
  }

Profile bodies are opaque dicts: copied, never inspected.
Every function that returns a doc returns a NEW deep copy and never mutates its input.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any


def _empty_doc() -> dict[str, Any]:
    return {"version": 1, "profiles": {}, "default_profile": None, "game_profiles": {}}


def _profile_names(doc: dict[str, Any]) -> list[str]:
    profiles = doc.get("profiles")
    if not isinstance(profiles, dict):
        return []
    return list(profiles.keys())


def normalize(doc: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a profiles document to the additive assignment shape.

    None or {} gives the empty default document. Missing/invalid default_profile
    falls back to active_profile if present in profiles, else the first profile
    name in sorted order, else None. Game assignments pointing at missing profiles
    are dropped. Unknown top-level keys and version are preserved.
    """
    if doc is None or doc == {}:
        return _empty_doc()

    out = copy.deepcopy(doc)
    if not isinstance(out, dict):
        return _empty_doc()

    profiles = out.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        out["profiles"] = profiles

    # Preserve version as-is; only supply a default when absent.
    if "version" not in out:
        out["version"] = 1

    profile_names = set(profiles.keys())

    # Normalize default_profile.
    default = out.get("default_profile")
    if not isinstance(default, str) or default not in profile_names:
        active = out.get("active_profile")
        if isinstance(active, str) and active in profile_names:
            default = active
        elif profile_names:
            default = sorted(profile_names)[0]
        else:
            default = None
    out["default_profile"] = default

    # Normalize game_profiles, dropping stale entries.
    raw_gp = out.get("game_profiles")
    if not isinstance(raw_gp, dict):
        raw_gp = {}
    clean_gp: dict[str, Any] = {}
    for game_id, name in raw_gp.items():
        if isinstance(name, str) and name in profile_names:
            clean_gp[game_id] = name
    out["game_profiles"] = clean_gp

    return out


def load(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read JSON from path and return normalize(data).

    A missing file returns normalize(None). Invalid JSON raises ValueError.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return normalize(None)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {os.fspath(path)}: {e}") from e
    if not isinstance(data, dict):
        # Normalize handles non-dict by returning empty, but be explicit.
        return normalize(None)
    return normalize(data)


def save(path: str | os.PathLike[str], doc: dict[str, Any] | None) -> None:
    """Write normalize(doc) as indented JSON atomically.

    Writes a temp file in the same directory, then os.replace. Creates the
    parent directory if needed.
    """
    normalized = normalize(doc)
    target = os.fspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=parent or None, prefix=".tmp_profiles_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def resolve(doc: dict[str, Any] | None, game_id: str | None) -> str | None:
    """Resolve the effective profile for a game.

    Returns the game's assigned profile if assigned and existing; else
    default_profile if it exists in profiles; else None.
    """
    d = normalize(doc)
    profiles = d.get("profiles", {})
    if isinstance(game_id, str):
        gp = d.get("game_profiles", {})
        if isinstance(gp, dict):
            assigned = gp.get(game_id)
            if isinstance(assigned, str) and assigned in profiles:
                return assigned
    default = d.get("default_profile")
    if isinstance(default, str) and default in profiles:
        return default
    return None


def assign(doc: dict[str, Any] | None, game_id: str, name: str | None) -> dict[str, Any]:
    """Assign or remove a game profile assignment.

    name None removes the assignment; otherwise name must exist in profiles or
    KeyError is raised. Returns a new normalized doc.
    """
    d = normalize(doc)
    profiles = d.get("profiles", {})
    gp = dict(d.get("game_profiles", {}))
    if name is None:
        gp.pop(game_id, None)
    else:
        if name not in profiles:
            raise KeyError(name)
        gp[game_id] = name
    d["game_profiles"] = gp
    return normalize(d)


def set_default(doc: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """Set the default profile. KeyError if missing.

    Also sets active_profile to name. Returns a new normalized doc.
    """
    d = normalize(doc)
    profiles = d.get("profiles", {})
    if name not in profiles:
        raise KeyError(name)
    d["default_profile"] = name
    d["active_profile"] = name
    return normalize(d)


def create(doc: dict[str, Any] | None, name: str, base: str | None = None) -> dict[str, Any]:
    """Create a new profile.

    ValueError if name is empty/whitespace or already exists. Body is a deep copy
    of profiles[base] if base given (KeyError if base missing), else {}. If there
    was no default, the new profile becomes default.
    """
    d = normalize(doc)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("profile name must not be empty")
    profiles = d.get("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        d["profiles"] = profiles
    if name in profiles:
        raise ValueError(f"profile already exists: {name!r}")
    if base is None:
        body: dict[str, Any] = {}
    else:
        if base not in profiles:
            raise KeyError(base)
        body = copy.deepcopy(profiles[base])
        if not isinstance(body, dict):
            body = copy.deepcopy(body)
    profiles[name] = body
    if d.get("default_profile") is None:
        d["default_profile"] = name
        d["active_profile"] = name
    return normalize(d)


def duplicate(doc: dict[str, Any] | None, src: str, new: str) -> dict[str, Any]:
    """Duplicate src profile as new; same as create(doc, new, base=src)."""
    return create(doc, new, base=src)


def rename(doc: dict[str, Any] | None, old: str, new: str) -> dict[str, Any]:
    """Rename a profile.

    KeyError if old missing. ValueError if new exists or is empty. Moves the body
    and rewrites default_profile, active_profile and every game_profiles value
    that pointed at old.
    """
    d = normalize(doc)
    profiles = d.get("profiles", {})
    if old not in profiles:
        raise KeyError(old)
    if not isinstance(new, str) or not new.strip():
        raise ValueError("profile name must not be empty")
    if new in profiles:
        raise ValueError(f"profile already exists: {new!r}")

    # Move body, preserving insertion order by rebuilding dict.
    new_profiles: dict[str, Any] = {}
    for k, v in profiles.items():
        if k == old:
            new_profiles[new] = v
        else:
            new_profiles[k] = v
    d["profiles"] = new_profiles

    if d.get("default_profile") == old:
        d["default_profile"] = new
    if d.get("active_profile") == old:
        d["active_profile"] = new

    gp = d.get("game_profiles", {})
    if isinstance(gp, dict):
        for gid, pname in list(gp.items()):
            if pname == old:
                gp[gid] = new
    d["game_profiles"] = gp
    return normalize(d)


def delete(doc: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """Delete a profile.

    KeyError if missing; ValueError if it is the only profile. Removes game
    assignments that pointed at it. If it was the default, default and
    active_profile become the first remaining name in sorted order.
    """
    d = normalize(doc)
    profiles = d.get("profiles", {})
    if name not in profiles:
        raise KeyError(name)
    if len(profiles) <= 1:
        raise ValueError("cannot delete the only profile")

    # Remove profile.
    new_profiles = {k: v for k, v in profiles.items() if k != name}
    d["profiles"] = new_profiles

    # Remove assignments pointing at deleted profile.
    gp = d.get("game_profiles", {})
    if isinstance(gp, dict):
        d["game_profiles"] = {gid: pname for gid, pname in gp.items() if pname != name}

    # Fix default/active if needed.
    if d.get("default_profile") == name:
        remaining = sorted(new_profiles.keys())
        d["default_profile"] = remaining[0] if remaining else None
    if d.get("active_profile") == name or d.get("default_profile") is None:
        d["active_profile"] = d.get("default_profile")

    return normalize(d)
