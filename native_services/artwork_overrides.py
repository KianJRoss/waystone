"""Bounded manual raster overrides, independent of migration and source catalogs.

API: create(NEW selected directory, exact canonical IDs); preview(id, role,
selected absolute file or None to clear); apply(plan, request_id); snapshot();
audit(); undo(audit_id, request_id); read_asset(id, role). No constructor/read
creates directories. No title lookup, scans of source trees, or source writes.

Limits match media_catalog: 16 MiB encoded, 16M pixels, 64 MiB decoded,
64 KiB header admission; explicit PNG/JPEG/WebP/BMP/ICO Qt decoders only.
The private store additionally caps assets at 128 / 256 MiB, audit at 1000,
and metadata at 4 MiB. Retained assets allow exact undo; no automatic eviction.
Metadata and audit commit together by fsync + atomic replace + directory fsync.
A crash before metadata commit may leave an unreferenced, quota-counted copy.
Only latest unchanged apply can be undone. Request IDs are idempotent.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
from native_services import file_locking as fcntl

MAX_MEDIA_BYTES = 16 * 1024**2
MAX_PIXELS = 16_000_000
MAX_DECODED_BYTES = 64 * 1024**2
HEADER_BYTES = 64 * 1024
MAX_ASSETS = 128
MAX_TOTAL_BYTES = 256 * 1024**2
MAX_METADATA_BYTES = 4 * 1024**2
MAX_AUDIT = 1000
MAX_IDS = 100_000
MEDIA_ROLES = ('cover', 'background', 'icon', 'hero', 'logo')
KIND = 'native-manual-artwork-v1'
# Keep module importable on Windows even though the current secure store implementation
# still relies on POSIX dir_fd/openat operations and is not yet enabled there.
_FLAGS = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
          | getattr(os, 'O_NONBLOCK', 0))
_ASSET = re.compile(r'[0-9a-f]{64}\.(png|jpeg|webp|bmp|ico)\Z')


class Conflict(ValueError):
    """Stale/modified preview, request collision, or unsafe undo."""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False)


def _text(value, maximum=512):
    if (not isinstance(value, str) or not 0 < len(value) <= maximum
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError('Expected bounded nonempty exact text')
    return value


def _path(value):
    value = os.fspath(value)
    if (not isinstance(value, str) or len(value) > 4096 or not value.startswith('/')
            or str(Path(value)) != value or '..' in value.split('/')
            or any(ord(c) < 32 for c in value)):
        raise ValueError('Select a normalized absolute local path without traversal')
    return Path(value)


def _directory(path):
    """Walk every component without following symlinks (not Path.resolve)."""
    fd = os.open('/', _FLAGS | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in path.parts[1:]:
            child = os.open(part, _FLAGS | getattr(os, "O_DIRECTORY", 0), dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(s):
    return [s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def _private(s, directory=False):
    if (s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
            or (not directory and s.st_nlink != 1)):
        raise ValueError('Managed entries must be private, owned, and not hardlinked')


def _read(fd, limit):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError('Expected a nonempty bounded regular file')
    parts, count = [], 0
    while count <= limit:
        block = os.read(fd, min(65536, limit + 1 - count))
        if not block:
            break
        parts.append(block)
        count += len(block)
    if count != before.st_size or _identity(before) != _identity(os.fstat(fd)):
        raise ValueError('File changed or exceeded byte cap while reading')
    return b''.join(parts), before


def _source(path):
    parent = _directory(path.parent)
    try:
        fd = os.open(path.name, _FLAGS, dir_fd=parent)
        try:
            raw, info = _read(fd, MAX_MEDIA_BYTES)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    # Re-walk to detect a swapped file or ancestor while reading.
    parent = _directory(path.parent)
    try:
        selected = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if _identity(selected) != _identity(info):
            raise Conflict('Selected source changed during read')
    finally:
        os.close(parent)
    return raw, _identity(info)


def _raster(raw):
    # Lazy Qt imports: filesystem/state inspection needs no GUI or application.
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImageReader
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        fmt = 'png'
    elif raw.startswith(b'\xff\xd8\xff'):
        fmt = 'jpeg'
    elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        fmt = 'webp'
    elif raw.startswith(b'BM'):
        fmt = 'bmp'
    elif raw.startswith(b'\x00\x00\x01\x00'):
        fmt = 'ico'
    else:
        raise ValueError('Only PNG/JPEG/WebP/BMP/ICO raster content is supported')
    QImageReader.setAllocationLimit(64)
    def reader(data):
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.ReadOnly)
        decoder = QImageReader(buffer, QByteArray(fmt.encode('ascii')))
        decoder.setAutoDetectImageFormat(False)
        decoder.setDecideFormatFromContent(False)
        return buffer, decoder
    header, decoder = reader(raw[:HEADER_BYTES])
    size = decoder.size()
    width, height = size.width(), size.height()
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError('Raster dimensions missing or over pixel cap')
    buffer, decoder = reader(raw)
    image = decoder.read()
    if (image.isNull() or image.width() != width or image.height() != height
            or image.sizeInBytes() > MAX_DECODED_BYTES):
        raise ValueError('Invalid raster or decoded allocation exceeds cap')
    return {'format': fmt, 'width': width, 'height': height,
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


@dataclass(frozen=True)
class Plan:
    store_id: str
    revision: int
    canonical_id: str
    role: str
    source_path: str | None
    source_json: str
    before_json: str

    def as_dict(self):
        result = asdict(self)
        result['source'] = json.loads(result.pop('source_json'))
        result['before'] = json.loads(result.pop('before_json'))
        return result


class ArtworkOverrideStore:
    def __init__(self, selected_root):
        self.path = _path(selected_root)
        fd = _directory(self.path)
        try:
            _private(os.fstat(fd), directory=True)
            self._root_identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        finally:
            os.close(fd)
        with self._locked() as (fd, state):
            self.store_id = state['store_id']

    @classmethod
    def create(cls, selected_root, canonical_ids):
        """Exclusively create a NEW private directory; parent must already exist."""
        path = _path(selected_root)
        if not isinstance(canonical_ids, (list, tuple)) or not 1 <= len(canonical_ids) <= MAX_IDS:
            raise ValueError('Supply bounded exact canonical IDs')
        ids = [_text(i) for i in canonical_ids]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate canonical IDs')
        state = {'kind': KIND, 'store_id': uuid.uuid4().hex, 'canonical_ids': sorted(ids),
                 'revision': 0, 'overrides': {}, 'assets': {}, 'audit': []}
        cls._encoded(state)  # validate capacity before any filesystem changes
        parent = _directory(path.parent)
        fd = None
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
            fd = os.open(path.name, _FLAGS | getattr(os, "O_DIRECTORY", 0), dir_fd=parent)
            lock = os.open('lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600, dir_fd=fd)
            os.fsync(lock)
            os.close(lock)
            cls._commit(fd, state)
            os.fsync(parent)
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)
        return cls(path)

    @staticmethod
    def _encoded(state):
        raw = (_json(state) + '\n').encode('utf-8')
        if len(raw) > MAX_METADATA_BYTES:
            raise ValueError('Managed metadata capacity reached')
        return raw

    @staticmethod
    def _commit(fd, state):
        raw = ArtworkOverrideStore._encoded(state)
        name = '.txn-' + uuid.uuid4().hex
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, 'state.json', src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass

    @contextmanager
    def _locked(self, write=False):
        fd = _directory(self.path)
        lock = None
        try:
            info = os.fstat(fd)
            _private(info, directory=True)
            if (info.st_dev, info.st_ino) != self._root_identity:
                raise Conflict('Selected managed directory changed')
            lock = os.open('lock', _FLAGS, dir_fd=fd)
            _private(os.fstat(lock))
            fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            meta = os.open('state.json', _FLAGS, dir_fd=fd)
            try:
                _private(os.fstat(meta))
                raw, _ = _read(meta, MAX_METADATA_BYTES)
            finally:
                os.close(meta)
            state = json.loads(raw)
            self._validate(state)
            if hasattr(self, 'store_id') and state['store_id'] != self.store_id:
                raise Conflict('Managed store identity changed')
            yield fd, state
        finally:
            if lock is not None:
                os.close(lock)
            os.close(fd)

    @staticmethod
    def _validate(state):
        if (not isinstance(state, dict) or state.get('kind') != KIND
                or not re.fullmatch('[0-9a-f]{32}', state.get('store_id', ''))):
            raise ValueError('Not an artwork override store')
        ids = state.get('canonical_ids')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_IDS
                or any(_text(i) != i for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError('Invalid canonical ID registry')
        if type(state.get('revision')) is not int or state['revision'] < 0:
            raise ValueError('Invalid revision')
        assets, overrides, audit = state.get('assets'), state.get('overrides'), state.get('audit')
        if not isinstance(assets, dict) or len(assets) > MAX_ASSETS:
            raise ValueError('Invalid asset registry')
        total = 0
        for name, asset in assets.items():
            if (not _ASSET.fullmatch(name) or not isinstance(asset, dict)
                    or asset.get('relative_reference') != name
                    or name != str(asset.get('sha256')) + '.' + str(asset.get('format'))
                    or type(asset.get('bytes')) is not int or not 0 < asset['bytes'] <= MAX_MEDIA_BYTES
                    or type(asset.get('width')) is not int or type(asset.get('height')) is not int
                    or min(asset['width'], asset['height']) <= 0
                    or asset['width'] * asset['height'] > MAX_PIXELS):
                raise ValueError('Invalid bounded managed asset')
            total += asset['bytes']
        if total > MAX_TOTAL_BYTES or not isinstance(overrides, dict) or not set(overrides) <= set(ids):
            raise ValueError('Invalid override registry')
        for roles in overrides.values():
            if (not isinstance(roles, dict) or not set(roles) <= set(MEDIA_ROLES)
                    or any(not isinstance(name, str) or name not in assets for name in roles.values())):
                raise ValueError('Invalid role override')
        if not isinstance(audit, list) or len(audit) > MAX_AUDIT or len(audit) != state['revision']:
            raise ValueError('Invalid bounded audit')

    def canonical_ids(self):
        with self._locked() as (_, state):
            return tuple(state['canonical_ids'])

    def register_ids(self, canonical_ids):
        """Add newly seen exact canonical IDs. Never removes one, so saved overrides survive a game
        leaving and returning to the library. Revision is unchanged, so open previews stay valid."""
        ids = {_text(i) for i in canonical_ids}
        with self._locked(write=True) as (fd, state):
            known = set(state['canonical_ids'])
            new = ids - known
            if not new:
                return 0
            if len(known) + len(new) > MAX_IDS:
                raise ValueError('Supply bounded exact canonical IDs')
            state['canonical_ids'] = sorted(known | new)
            self._validate(state)
            self._commit(fd, state)
            return len(new)

    def snapshot(self):
        with self._locked() as (_, state):
            return {'revision': state['revision'], 'overrides': {
                game: {role: dict(state['assets'][name]) for role, name in roles.items()}
                for game, roles in state['overrides'].items()}}

    def _plan(self, state, canonical_id, role, selected_source):
        _text(canonical_id)
        if canonical_id not in state['canonical_ids']:
            raise ValueError('Unknown exact canonical ID')
        if role not in MEDIA_ROLES:
            raise ValueError('Unknown artwork role')
        source = None
        if selected_source is not None:
            path = _path(selected_source)
            if path == self.path or self.path in path.parents:
                raise ValueError('Select source media outside the managed store')
            raw, identity = _source(path)
            source = dict(_raster(raw), identity=identity)
            selected_source = str(path)
        before = state['overrides'].get(canonical_id, {}).get(role)
        return Plan(state['store_id'], state['revision'], canonical_id, role,
                    selected_source, _json(source), _json(before))

    def preview(self, canonical_id, role, selected_source):
        """Validate and fully decode selection, without creating any file/directory."""
        with self._locked() as (_, state):
            return self._plan(state, canonical_id, role, selected_source)

    @staticmethod
    def _existing(state, request_id, intent):
        for row in state['audit']:
            if row['request_id'] == request_id:
                if row['intent'] != intent:
                    raise Conflict('Request ID already used for a different operation')
                return json.loads(_json(row))

    @staticmethod
    def _record(state, request_id, intent, kind, canonical_id, role, before, after, target=None):
        if len(state['audit']) >= MAX_AUDIT:
            raise ValueError('Audit capacity reached; nothing evicted')
        roles = state['overrides'].setdefault(canonical_id, {})
        if after is None:
            roles.pop(role, None)
        else:
            roles[role] = after
        if not roles:
            state['overrides'].pop(canonical_id, None)
        state['revision'] += 1
        row = {'audit_id': state['revision'], 'request_id': request_id, 'intent': intent,
               'kind': kind, 'target': target, 'revision': state['revision'],
               'canonical_id': canonical_id, 'role': role, 'before': before, 'after': after,
               'created_at': datetime.now(timezone.utc).isoformat()}
        state['audit'].append(row)
        return json.loads(_json(row))

    @staticmethod
    def _asset_bytes(fd, name, asset):
        if not isinstance(name, str) or not _ASSET.fullmatch(name):
            raise ValueError('Unsafe managed asset reference')
        blob = os.open(name, _FLAGS, dir_fd=fd)
        try:
            _private(os.fstat(blob))
            raw, _ = _read(blob, MAX_MEDIA_BYTES)
        finally:
            os.close(blob)
        if len(raw) != asset['bytes'] or hashlib.sha256(raw).hexdigest() != asset['sha256']:
            raise Conflict('Managed asset was modified')
        return raw

    @staticmethod
    def _copy(fd, name, raw, asset):
        # Count even crash-orphan copies; never exceed the physical store cap.
        total, count = 0, 0
        for entry in os.listdir(fd):
            if _ASSET.fullmatch(entry):
                info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
                _private(info)
                total += info.st_size
                count += 1
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None:
            ArtworkOverrideStore._asset_bytes(fd, name, asset)
            return
        if count >= MAX_ASSETS or total + len(raw) > MAX_TOTAL_BYTES:
            raise ValueError('Managed copy capacity reached; retained undo assets not evicted')
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
        except BaseException:
            os.unlink(name, dir_fd=fd)
            raise

    def apply(self, plan: Plan, request_id: str):
        """Import an exact private byte copy and commit its role override atomically."""
        _text(request_id, 128)
        if not isinstance(plan, Plan):
            raise ValueError('Expected preview Plan')
        intent = _json({'apply': plan.as_dict()})
        with self._locked(write=True) as (fd, state):
            old = self._existing(state, request_id, intent)
            if old is not None:
                return old
            if plan.store_id != state['store_id'] or plan.revision != state['revision']:
                raise Conflict('Preview is stale or belongs to another store')
            current = self._plan(state, plan.canonical_id, plan.role, plan.source_path)
            if current != plan:
                raise Conflict('Preview/source modified; preview again')
            source = json.loads(plan.source_json)
            name, raw, asset = None, None, None
            if source is not None:
                raw, identity = _source(_path(plan.source_path))
                if identity != source['identity'] or hashlib.sha256(raw).hexdigest() != source['sha256']:
                    raise Conflict('Source changed after preview')
                name = source['sha256'] + '.' + source['format']
                asset = {k: v for k, v in source.items() if k != 'identity'}
                asset['relative_reference'] = name
                state['assets'][name] = asset
            result = self._record(state, request_id, intent, 'apply', plan.canonical_id,
                                  plan.role, json.loads(plan.before_json), name)
            self._validate(state)
            self._encoded(state)  # reject metadata capacity BEFORE copying
            if name is not None:
                self._copy(fd, name, raw, asset)
            self._commit(fd, state)
            return result

    import_artwork = apply

    def undo(self, audit_id: int, request_id: str):
        _text(request_id, 128)
        if type(audit_id) is not int or audit_id <= 0:
            raise ValueError('Expected positive audit ID')
        intent = _json({'undo': audit_id})
        with self._locked(write=True) as (fd, state):
            old = self._existing(state, request_id, intent)
            if old is not None:
                return old
            if not state['audit']:
                raise Conflict('No apply to undo')
            row = state['audit'][-1]
            if row['audit_id'] != audit_id or row['kind'] != 'apply':
                raise Conflict('Only latest unchanged apply can be undone')
            if row['before'] is not None:
                self._asset_bytes(fd, row['before'], state['assets'][row['before']])
            result = self._record(state, request_id, intent, 'undo', row['canonical_id'],
                                  row['role'], row['after'], row['before'], audit_id)
            self._commit(fd, state)
            return result

    def audit(self):
        with self._locked() as (_, state):
            return list(reversed(state['audit']))

    def read_asset(self, canonical_id, role):
        """Return verified original encoded bytes, or None when no override exists."""
        with self._locked() as (fd, state):
            if canonical_id not in state['canonical_ids'] or role not in MEDIA_ROLES:
                raise ValueError('Unknown exact canonical ID or role')
            name = state['overrides'].get(canonical_id, {}).get(role)
            return None if name is None else self._asset_bytes(fd, name, state['assets'][name])
