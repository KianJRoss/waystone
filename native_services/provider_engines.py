"""The store helpers Waystone drives: legendary (Epic), gogdl (GOG), nile (Amazon). All GPL-3.0, not bundled.

W22 (public release): the public app does not ship these programs. For each helper Waystone uses, in order:
  1. a copy shipped inside the app folder (`components/provider_engines/<name>`; private builds),
  2. a copy Waystone downloaded earlier (`~/.local/share/waystone/provider-engines/<name>-<version>/<name>`),
and otherwise downloads the pinned official release from GitHub the first time it is needed, verified by SHA-256
before it is made executable. Never Heroic's copies: Heroic replaces them on its own schedule, and Waystone keeps
its own accounts and installs. Qt-free; network and file access are injectable for tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Callable

from native_services import waystone_paths

SERVICE_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PREFIXES = ("https://github.com/Heroic-Games-Launcher/", "https://github.com/imLinguin/")


@dataclass(frozen=True)
class Pin:
    version: str
    url: str
    sha256: str
    size: int
    source: str          # where the code and license live (GPL-3.0 source offer)


PINS = {
    "legendary": Pin("0.20.43",
                     "https://github.com/Heroic-Games-Launcher/legendary/releases/download/0.20.43/legendary_linux_x86_64",
                     "2b82497051afd95670994146e6038d6e1c98a1c60c21949def668b52aef7d3f7", 4696954,
                     "https://github.com/Heroic-Games-Launcher/legendary"),
    "gogdl": Pin("1.3.0",
                 "https://github.com/Heroic-Games-Launcher/heroic-gogdl/releases/download/v1.3.0/gogdl_linux_x86_64",
                 "cba013d42767c808237c437335ab1d56f58405d07e8f37b3324d264ea5c49655", 1563756,
                 "https://github.com/Heroic-Games-Launcher/heroic-gogdl"),
    "nile": Pin("1.2.0",
                "https://github.com/imLinguin/nile/releases/download/v1.2.0/nile_linux_x86_64",
                "d73519514df9d52c50c6feec629373cc3a0d9504e087b6c7372a8ca8a95e7bfe", 11113264,
                "https://github.com/imLinguin/nile"),
}


class EngineError(RuntimeError):
    pass


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def bundled_path(name: str, root: Path = SERVICE_ROOT) -> Path:
    return root / "components" / "provider_engines" / name


def download_path(name: str, home: Path | None = None) -> Path:
    pin = PINS[name]
    return waystone_paths.data_dir(home) / "provider-engines" / f"{name}-{pin.version}" / name


def resolve(name: str, *, root: Path = SERVICE_ROOT, home: Path | None = None) -> Path:
    """The helper to use. When none is installed yet this is where `ensure` will download it."""
    if name not in PINS:
        raise KeyError(name)
    bundled = bundled_path(name, root)
    return bundled if _executable(bundled) else download_path(name, home)


def _http_download(url: str, dest: Path, limit: int) -> None:
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Waystone"})
    with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
        final = response.geturl()
        if not final.startswith("https://"):
            raise EngineError("Download was redirected away from HTTPS.")
        total = 0
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise EngineError("Download is larger than expected.")
            out.write(chunk)


def ensure(path: str | os.PathLike, *, home: Path | None = None,
           download: Callable[[str, Path, int], None] = _http_download) -> Path:
    """Make sure the helper at `path` exists. Only Waystone's own download location is ever filled; any other path
    is returned unchanged (Waystone never writes into the app folder or Heroic's install; callers keep their own
    "engine unavailable" checks)."""
    path = Path(path)
    if _executable(path):
        return path
    name = path.name
    if name not in PINS or path != download_path(name, home):
        return path
    pin = PINS[name]
    if not pin.url.startswith(ALLOWED_PREFIXES):
        raise EngineError("Unexpected download source.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{name}-", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        download(pin.url, tmp, pin.size + 1024)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest != pin.sha256:
            raise EngineError(f"{name} download does not match the official release (checksum). Nothing was installed.")
        tmp.chmod(0o755)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path
