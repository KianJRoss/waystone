"""Explicit selected-copy catalogs and local-only opaque image provider.

Catalog cap: 64 MiB actual bytes. Media: 16 MiB/file, 16M pixels, 64 MiB
decoder allocation, 64 KiB header admission (longer headers fail closed).
Only PNG/JPEG/WebP/BMP/ICO magic plus explicit Qt decoder.
No file URLs, network, directory discovery, media writes or decoded-image cache.
Selection is per role: first usable variant in (source, source_id,
installation_id) order, then explicit game-level artwork as fallback. Linked
groups retain each member's game-level fallbacks in canonical ID order, after
all explicit variants. Titles and
input order never select images. All original candidates remain in the model.
Header admission is lazy; full decode and descriptor/path revalidation occur on
every provider request. Corrupt admitted candidates fall through to the next.
"""
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtQuick import QQuickImageProvider

MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_MEDIA_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16_000_000
HEADER_BYTES = 64 * 1024
MEDIA_ROLES = ('cover', 'background', 'icon')
CATALOG_KIND = 'playnite-display-selected-copy-v1'


def _read_bounded(fd, limit):
    chunks = []
    count = 0
    while count <= limit:
        chunk = os.read(fd, min(65536, limit + 1 - count))
        if not chunk:
            break
        chunks.append(chunk)
        count += len(chunk)
    if count > limit:
        raise ValueError('Actual file byte limit exceeded')
    return b''.join(chunks)


def load_catalog(path, *, with_digest=False):
    """Read only an explicit regular JSON file; never use JSON-supplied roots."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK | os.O_NOFOLLOW
    fd = os.open(os.fspath(path), flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CATALOG_BYTES:
            raise ValueError('Catalog must be a regular file within the 64 MiB cap')
        raw = _read_bounded(fd, MAX_CATALOG_BYTES)
        after = os.fstat(fd)
        selected = os.stat(path, follow_symlinks=False)
        def fingerprint(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if len(raw) != after.st_size or fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(selected):
            raise ValueError('Catalog changed during read')
    finally:
        os.close(fd)
    try:
        def invalid_constant(value):
            raise ValueError('Nonfinite JSON number')
        data = json.loads(raw, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('Catalog must be bounded UTF-8 JSON') from exc
    if not isinstance(data, dict) or data.get('catalog_kind') != CATALOG_KIND:
        raise ValueError('Expected selected-copy display catalog kind')
    from library_model import validate_catalog
    validate_catalog(data)
    return (data, hashlib.sha256(raw).hexdigest()) if with_digest else data


def _reference(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(ord(c) < 32 for c in value) or ':' in value or '\\' in value or value.startswith('/'):
        return None
    parts = value.split('/')
    return parts if all(part and part not in ('.', '..') for part in parts) else None


def _format(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return b'png'
    if raw.startswith(b'\xff\xd8\xff'):
        return b'jpeg'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return b'webp'
    if raw.startswith(b'BM'):
        return b'bmp'
    if raw.startswith(b'\x00\x00\x01\x00'):
        return b'ico'
    return None


def _image(raw, decode=False):
    fmt = _format(raw)
    if not fmt:
        return QImage() if decode else False
    if not decode and fmt == b"png" and len(raw) >= 24:
        import struct
        w, h = struct.unpack(">II", raw[16:24])
        return 0 < w and 0 < h and w * h <= MAX_PIXELS
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.ReadOnly)
    QImageReader.setAllocationLimit(64)
    reader = QImageReader(buffer, QByteArray(fmt))
    reader.setAutoDetectImageFormat(False)
    reader.setDecideFormatFromContent(False)
    size = reader.size()
    # Some Qt ICO handlers consume their header during size(), making a later
    # canRead() false despite a valid explicit-format decoder. Validate dimensions
    # here, then require a successful bounded decode on the provider request.
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_PIXELS:
        return QImage() if decode else False
    if not decode:
        return True
    image = reader.read()
    if image.isNull() or image.width() * image.height() > MAX_PIXELS or image.sizeInBytes() > 64 * 1024 * 1024:
        return QImage()
    return image


class MediaCatalog(QQuickImageProvider):
    """Opaque handles only; root must come from the explicit CLI argument."""
    def __init__(self, media_root=None):
        super().__init__(QQuickImageProvider.Image)
        self.root = None
        self.root_identity = None
        self._handles = {}
        if media_root is not None:
            self.root = Path(media_root).resolve(strict=True)
            fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
            try:
                info = os.fstat(fd)
                self.root_identity = (info.st_dev, info.st_ino)
            finally:
                os.close(fd)

    @contextmanager
    def _open(self, reference):
        parts = _reference(reference)
        if self.root is None or parts is None:
            raise ValueError('No explicit safe media root/reference')
        descriptors = []
        try:
            parent = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
            descriptors.append(parent)
            info = os.fstat(parent)
            if (info.st_dev, info.st_ino) != self.root_identity:
                raise ValueError('Selected media root changed')
            for part in parts[:-1]:
                parent = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
                descriptors.append(parent)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_MEDIA_BYTES:
                raise ValueError('Media must be a nonempty bounded regular file')
            yield fd, info
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def _bytes(self, reference, header=False):
        with self._open(reference) as (fd, before):
            raw = os.read(fd, HEADER_BYTES) if header else _read_bounded(fd, MAX_MEDIA_BYTES)
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('Media changed while reading')
            return raw

    def urls_for(self, game):
        result = {role + 'Url': '' for role in MEDIA_ROLES}
        if self.root is None:
            return result
        variants = sorted(game['variants'], key=lambda v: (v['source'], v['source_id'], v.get('installation_id', 'fixture-default')))
        members = game.get('linked_member_artwork')
        # Fixed projection contract: raw artwork maps already sorted by member
        # canonical ID. Preserve that order independently for each artwork role.
        fallbacks = [{'artwork': artwork} for artwork in members
                     if isinstance(artwork, dict)] if isinstance(members, list) else []
        # The projection includes the representative in member fallbacks. Do not
        # promote these maps into variants or prefer the representative twice.
        owners = [*variants, *(fallbacks or [game])]
        for role in MEDIA_ROLES:
            references = []
            for owner in owners:
                artwork = owner.get('artwork')
                claim = artwork.get(role) if isinstance(artwork, dict) else None
                if not isinstance(claim, dict) or claim.get('status') != 'local_present':
                    continue
                reference = claim.get('relative_reference')
                if _reference(reference) is None:
                    continue
                try:
                    if _image(self._bytes(reference, header=True)):
                        references.append(reference)
                except (OSError, ValueError, RuntimeError):
                    continue
            if references:
                handle = hashlib.sha256(json.dumps([self.root_identity, game['id'], role, references], ensure_ascii=False).encode()).hexdigest()
                self._handles[handle] = tuple(references)
                result[role + 'Url'] = 'image://catalog-media/' + handle
        return result

    def requestImage(self, handle, size, requestedSize):
        # Never interpret the caller's ID as a filename. No decoded-image cache.
        for reference in self._handles.get(handle, ()):
            try:
                image = _image(self._bytes(reference), decode=True)
                if not image.isNull():
                    size.setWidth(image.width())
                    size.setHeight(image.height())
                    return image
            except (OSError, ValueError, RuntimeError):
                continue
        size.setWidth(0)
        size.setHeight(0)
        return QImage()
