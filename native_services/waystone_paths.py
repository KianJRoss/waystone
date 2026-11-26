"""Where Waystone keeps user data (XDG on Linux, AppData on Windows).

Linux installs use `$XDG_DATA_HOME/waystone` (default `~/.local/share/waystone`). Windows uses
`%LOCALAPPDATA%/Waystone` for data/state and `%APPDATA%/Waystone` for config. Installs that predate the public
release keep using their pre-release data folder when it exists, so nothing has to be moved and nothing can be lost.
Qt-free; resolved at call time so tests can patch HOME.
"""
from __future__ import annotations

import os
from pathlib import Path
from .platform import app_roots

try:
    from private_addons import LEGACY_DIR_NAME
except ImportError:
    LEGACY_DIR_NAME = os.environ.get("WAYSTONE_LEGACY_DATA_DIR", "waystone-legacy")


def data_home(home: Path | None = None) -> Path:
    # Keep the legacy helper's parent-root semantics; native app data is selected
    # from LOCALAPPDATA on Windows and XDG_DATA_HOME on Linux.
    return app_roots(home=home, environ={} if home is not None else None)["data"].parent


def config_home() -> Path:
    return app_roots()["config"].parent


def state_home() -> Path:
    return app_roots()["state"].parent


def xdg_paths(home: Path | None = None, environ=None, *, system: str | None = None) -> dict[str, Path]:
    """Resolve the two XDG roots used by local-library discovery.

    Explicit fixture homes and injected env mappings keep tests away from the
    real user's directories. Invalid relative XDG overrides are ignored.
    """
    roots = app_roots(home=home, environ=environ, system=system)
    return {"data": roots["data"], "config": roots["config"]}


def data_dir(home: Path | None = None) -> Path:
    return app_roots(home=home, environ={} if home is not None else None)["data"]


def legacy_dir(home: Path | None = None) -> Path:
    return data_home(home) / LEGACY_DIR_NAME


def store(*parts: str, home: Path | None = None) -> Path:
    """Path of an app-owned store, e.g. store('providers-v1', 'gog'). Uses the pre-release location when only that
    one exists. `home` is for code that is handed an explicit home folder (tests, fake homes)."""
    current = data_dir(home).joinpath(*parts)
    if current.exists():
        return current
    legacy = legacy_dir(home).joinpath(*parts)
    return legacy if legacy.exists() else current
