"""Native, offline catalog readers. No helper execution, remote access or writes."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

MAX_EXPORT_BYTES = 32 * 1024 * 1024


class UnsupportedCatalog(ValueError):
    """Selected export cannot be safely interpreted; never silently report empty."""


@dataclass(frozen=True)
class CatalogItem:
    provider: str
    source_id: str
    source_key: str
    name: str
    platform: str | None
    provenance: str
    export_sha256: str
    record_number: int
    # A package listing alone is not proof of downloaded/playable state.
    downloaded: bool | None = None
    playable: bool | None = None


@dataclass(frozen=True)
class CatalogSnapshot:
    items: tuple[CatalogItem, ...]
    provider: str
    provenance: str
    export_sha256: str
    limitations: tuple[str, ...] = (
        'Offline snapshot; freshness and completeness are unknown.',
        'Download, install, launch and remote refresh are unsupported.',
    )


def _segment(value):
    if (not isinstance(value, str) or not value.strip() or value in ('.', '..')
            or '/' in value or '\\' in value or any(ord(c) < 32 for c in value)):
        raise UnsupportedCatalog('Invalid catalog path segment')
    return value


def _read(selected_path):
    path = Path(selected_path)
    if not path.is_absolute():
        raise UnsupportedCatalog('Select an absolute local export path explicitly')
    # Open once; never check a pathname and then reopen it. O_NONBLOCK prevents
    # a swapped FIFO from hanging before fstat rejects non-regular descriptors.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

    def fingerprint(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    try:
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise UnsupportedCatalog('Select a regular, non-symlink export file')
            if before.st_size > MAX_EXPORT_BYTES:
                raise UnsupportedCatalog('Export exceeds size limit')
            chunks, total = [], 0
            while total <= MAX_EXPORT_BYTES:
                chunk = os.read(fd, min(65536, MAX_EXPORT_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after = os.fstat(fd)
            selected = os.stat(path, follow_symlinks=False)
            if (fingerprint(before) != fingerprint(after)
                    or fingerprint(after) != fingerprint(selected)):
                raise UnsupportedCatalog('Selected export changed while being read')
            raw = b''.join(chunks)
            if len(raw) > MAX_EXPORT_BYTES:
                raise UnsupportedCatalog('Export exceeds size limit')
            if len(raw) != after.st_size:
                raise UnsupportedCatalog('Selected export read was incomplete')
        finally:
            os.close(fd)
    except OSError as exc:
        raise UnsupportedCatalog('Cannot safely read selected regular export file') from exc
    try:
        return raw.decode('utf-8-sig'), str(path), sha256(raw).hexdigest()
    except UnicodeError as exc:
        raise UnsupportedCatalog('Export must be UTF-8') from exc


def _item(provider, key, name, platform, provenance, digest, number):
    # Preserve source identity, NOT cleaned title identity or local cache filename.
    identity = sha256(json.dumps([provider, key], ensure_ascii=False).encode()).hexdigest()
    return CatalogItem(provider, provider + ':' + identity, key, name, platform,
                       provenance, digest, number)


def __getattr__(name: str):
    try:
        import private_addons.catalog_providers as _priv
        return getattr(_priv, name)
    except (ImportError, AttributeError) as exc:
        raise AttributeError(name) from exc
