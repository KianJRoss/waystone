"""Where Waystone keeps its data (W22, public release): one place, XDG-aware, no machine-specific paths.

New installs use `$XDG_DATA_HOME/waystone` (default `~/.local/share/waystone`). Installs that predate the public
release keep using their pre-release data folder when it exists, so nothing has to be moved and nothing can be lost.
Qt-free; resolved at call time so tests can patch HOME.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from private_addons import LEGACY_DIR_NAME
except ImportError:
    LEGACY_DIR_NAME = os.environ.get("WAYSTONE_LEGACY_DATA_DIR", "waystone-legacy")


def data_home(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / ".local" / "share"
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".local" / "share"


def config_home() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".config"


def state_home() -> Path:
    value = os.environ.get("XDG_STATE_HOME", "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".local" / "state"


def xdg_paths(home: Path | None = None, environ=None) -> dict[str, Path]:
    """Resolve the two XDG roots used by local-library discovery.

    Explicit fixture homes and injected env mappings keep tests away from the
    real user's directories. Invalid relative XDG overrides are ignored.
    """
    env = os.environ if environ is None else environ
    fallback_home = Path(home).expanduser() if home is not None else Path.home()
    def root(variable, fallback):
        value = env.get(variable, "")
        candidate = Path(value).expanduser() if value and os.path.isabs(value) else fallback_home / fallback
        return candidate.absolute()
    return {"data": root("XDG_DATA_HOME", ".local/share") / "waystone",
            "config": root("XDG_CONFIG_HOME", ".config") / "waystone"}


def data_dir(home: Path | None = None) -> Path:
    return data_home(home) / "waystone"


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
