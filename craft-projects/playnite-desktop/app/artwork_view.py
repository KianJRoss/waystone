"""Presentation and image provider boundary for manual artwork overrides.

Only explicit local file selections via QUrl or path are admitted.
Preview and apply/undo are strictly separate operations.
Images are served via QQuickImageProvider using opaque handles from verified raster bytes.
No file:// URLs or raw paths are exposed to QML.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Property, Signal, Slot, QUrl
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtQuick import QQuickImageProvider

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from native_services.artwork_overrides import (
    ArtworkOverrideStore,
    Conflict,
    MAX_MEDIA_BYTES,
    MAX_PIXELS,
    MAX_DECODED_BYTES,
    HEADER_BYTES,
    MEDIA_ROLES,
    Plan,
)

PROVIDER_ID = 'artwork-override'
IMAGE_SCHEME_PREFIX = f'image://{PROVIDER_ID}/'
MAX_PROJECTION = 65536


def _safe_message(exc: Exception, default: str) -> str:
    """Format safe display message without raw paths or sensitive internal data."""
    if isinstance(exc, (ValueError, Conflict)):
        msg = str(exc)
        # Check if message contains path-like or command-like tokens
        if not re.search(r'(?:[\\/]|\b(?:https?|file)://|\b(?:sudo|bash|sh|python)\b|\$\(|[;&|`])', msg, re.I):
            clean = ''.join(c for c in msg if c.isprintable()).strip()
            if clean and len(clean) <= 120:
                return clean
    return default


def _format(raw: bytes) -> bytes | None:
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


def _decode_raster_bytes(raw: bytes) -> QImage:
    """Decode raster bytes strictly matching media_catalog constraints."""
    fmt = _format(raw)
    if not fmt:
        return QImage()
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.ReadOnly)
    QImageReader.setAllocationLimit(64)
    reader = QImageReader(buffer, QByteArray(fmt))
    reader.setAutoDetectImageFormat(False)
    reader.setDecideFormatFromContent(False)
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_PIXELS:
        return QImage()
    image = reader.read()
    if image.isNull() or image.width() * image.height() > MAX_PIXELS or image.sizeInBytes() > MAX_DECODED_BYTES:
        return QImage()
    return image


def _extract_local_path(selected: str | QUrl | None) -> str | None:
    """Extract and validate normalized absolute path from local file URL or string."""
    if selected is None:
        return None
    if isinstance(selected, QUrl):
        if not selected.isLocalFile():
            raise ValueError('Selection must be a local file')
        path_str = selected.toLocalFile()
    elif isinstance(selected, str):
        selected = selected.strip()
        if not selected:
            return None
        if selected.startswith('file://'):
            parsed = urlparse(selected)
            if parsed.netloc and parsed.netloc != 'localhost':
                raise ValueError('Remote file URLs are not permitted')
            path_str = unquote(parsed.path)
        elif '://' in selected:
            raise ValueError('Selection must be a local file')
        else:
            path_str = selected
    else:
        raise ValueError('Expected local file URL or path string')

    # Normalize and validate absolute path without traversal
    path_obj = Path(path_str)
    if not path_obj.is_absolute():
        raise ValueError('Selected path must be an absolute path')
    raw_parts = path_str.replace('\\', '/').split('/')
    if '..' in raw_parts or any(ord(c) < 32 for c in path_str):
        raise ValueError('Path traversal and control characters are prohibited')
    norm = str(Path(os.path.normpath(str(path_obj))))
    # Symlink check
    if os.path.islink(norm):
        raise ValueError('Symlinks are not permitted')
    return norm


class ArtworkImageProvider(QQuickImageProvider):
    """Serves verified raster bytes for manual artwork overrides via opaque handles."""

    def __init__(self, artwork_view: ArtworkView):
        super().__init__(QQuickImageProvider.Image)
        self._view = artwork_view

    def requestImage(self, handle: str, size, requestedSize) -> QImage:
        """Resolve opaque handle to QImage from preview buffer or committed store."""
        # 1. Check ephemeral preview buffer
        preview_bytes = self._view._get_preview_bytes(handle)
        if preview_bytes is not None:
            image = _decode_raster_bytes(preview_bytes)
            if not image.isNull():
                size.setWidth(image.width())
                size.setHeight(image.height())
                return image

        # 2. Check committed store handle
        entry = self._view._resolve_committed_handle(handle)
        if entry is not None:
            canonical_id, role = entry
            try:
                raw = self._view._read_store_asset(canonical_id, role)
                if raw is not None:
                    image = _decode_raster_bytes(raw)
                    if not image.isNull():
                        size.setWidth(image.width())
                        size.setHeight(image.height())
                        return image
            except Exception:
                pass

        size.setWidth(0)
        size.setHeight(0)
        return QImage()


class ArtworkView(QObject):
    """QObject adapter for manual artwork override management."""

    changed = Signal()
    operationFinished = Signal(str)

    def __init__(self, catalog=None, override_root=None, enable_override_state=False, parent=None):
        super().__init__(parent)
        self._catalog = catalog if isinstance(catalog, dict) else {'games': []}
        self._enabled = bool(enable_override_state)
        self._override_root = Path(override_root) if override_root is not None else None
        self._store = None
        self._store_error = ''
        self._error = ''
        self._status = ''
        self._latest_action = ''
        self._plan: Plan | None = None
        self._pending_source_path: str | None = None
        self._preview_info: dict | None = None
        self._preview_bytes: bytes | None = None
        self._preview_handle: str | None = None

        # Opaque committed handles mapping: handle -> (canonical_id, role)
        self._committed_handles: dict[str, tuple[str, str]] = {}
        # Reverse mapping: (canonical_id, role) -> handle
        self._active_handles: dict[tuple[str, str], str] = {}

        # Connect to existing store if explicitly enabled
        if self._override_root is not None and self._enabled:
            if not self._override_root.is_absolute():
                raise ValueError('Override root must be an explicit absolute path')
            if self._override_root.is_dir():
                try:
                    store = ArtworkOverrideStore(self._override_root)
                    ids = list(self._catalog_ids())
                    if ids:
                        # The library changes (new games, imports). An exact-match check here made a
                        # single new game hide every saved pick, so add new IDs and never drop any.
                        store.register_ids(ids)
                    self._store = store
                    self._refresh_handles()
                except Exception as exc:
                    self._store_error = _safe_message(exc, 'Failed to open existing artwork override store')
                    self._error = self._store_error

        self._provider = ArtworkImageProvider(self)

    @Property(bool, notify=changed)
    def storeEnabled(self) -> bool:
        return self._store is not None or (self._enabled and self._override_root is not None)

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(str, notify=changed)
    def status(self) -> str:
        return self._status

    @Property(str, notify=changed)
    def latestAction(self) -> str:
        return self._latest_action

    @Property(int, notify=changed)
    def revision(self) -> int:
        if self._store is not None:
            try:
                return self._store.snapshot()['revision']
            except Exception:
                pass
        return 0

    @Property(int, notify=changed)
    def undoableAuditId(self) -> int:
        if self._store is not None:
            try:
                audit = self._store.audit()
                if audit:
                    latest = audit[0]
                    if latest.get('kind') == 'apply':
                        return latest.get('audit_id', 0)
            except Exception:
                pass
        return 0

    @Property('QVariantMap', notify=changed)
    def activePreview(self) -> dict:
        if self._preview_info is not None:
            return dict(self._preview_info)
        return {}

    @Property(str, notify=changed)
    def previewImageUrl(self) -> str:
        if self._preview_handle:
            return f'{IMAGE_SCHEME_PREFIX}{self._preview_handle}'
        return ''

    def provider(self) -> ArtworkImageProvider:
        return self._provider

    def _catalog_ids(self) -> tuple[str, ...]:
        return tuple(sorted(
            game['id'] for game in self._catalog.get('games', [])
            if isinstance(game, dict) and isinstance(game.get('id'), str) and game['id']
        ))

    def _publish(self):
        self.changed.emit()

    def _refresh_handles(self):
        """Refresh opaque handles for all committed overrides in the active store."""
        self._committed_handles.clear()
        self._active_handles.clear()
        if self._store is None:
            return
        try:
            snap = self._store.snapshot()
            store_id = self._store.store_id
            for canonical_id, roles in snap.get('overrides', {}).items():
                for role, asset in roles.items():
                    rel_ref = asset.get('relative_reference', '')
                    digest = hashlib.sha256(
                        f'{store_id}:{canonical_id}:{role}:{rel_ref}'.encode()
                    ).hexdigest()
                    handle = f'c_{digest[:32]}'
                    self._committed_handles[handle] = (canonical_id, role)
                    self._active_handles[(canonical_id, role)] = handle
        except Exception:
            pass

    def _get_preview_bytes(self, handle: str) -> bytes | None:
        if self._preview_handle and handle == self._preview_handle:
            return self._preview_bytes
        return None

    def _resolve_committed_handle(self, handle: str) -> tuple[str, str] | None:
        return self._committed_handles.get(handle)

    def _read_store_asset(self, canonical_id: str, role: str) -> bytes | None:
        if self._store is not None:
            return self._store.read_asset(canonical_id, role)
        return None

    @Slot(str, result=str)
    @Slot(str, str, result=str)
    def artworkUrl(self, canonical_id: str, role: str = 'cover') -> str:
        """Return opaque image:// URL for an active override, or empty string."""
        handle = self._active_handles.get((canonical_id, role))
        if handle:
            return f'{IMAGE_SCHEME_PREFIX}{handle}'
        return ''

    @Slot(str, bool, result=bool)
    def createStore(self, selected_root: str, enable: bool) -> bool:
        """Explicit opt-in creation of a new private artwork override store."""
        self._error = ''
        self._status = ''
        if not enable:
            self._error = 'Store creation requires explicit enablement'
            self._publish()
            return False
        try:
            path = Path(selected_root)
            if not path.is_absolute():
                raise ValueError('Store root must be an absolute path')
            if not path.parent.is_dir():
                raise ValueError('Parent directory must exist')
            canonical_ids = list(self._catalog_ids())
            if not canonical_ids:
                raise ValueError('Catalog must contain valid canonical IDs')
            store = ArtworkOverrideStore.create(path, canonical_ids)
            self._store = store
            self._override_root = path
            self._enabled = True
            self._refresh_handles()
            self._status = 'Artwork override store initialized'
            self._latest_action = 'Store created'
            self._publish()
            return True
        except Exception as exc:
            self._error = _safe_message(exc, 'Failed to create artwork override store')
            self._publish()
            return False

    @Slot()
    def discardPreview(self):
        """Clear active ephemeral preview."""
        if (self._plan is None and self._pending_source_path is None and
                self._preview_info is None and self._preview_bytes is None and
                self._preview_handle is None):
            return
        self._plan = None
        self._pending_source_path = None
        self._preview_info = None
        self._preview_bytes = None
        self._preview_handle = None
        self._publish()

    @Slot(str, str, str, result='QVariantMap')
    def preview(self, canonical_id: str, role: str, selected_url_or_path: str) -> dict:
        """Preview a manual override selection without creating or mutating store files."""
        self._error = ''
        self._status = ''
        self._plan = None
        self._pending_source_path = None
        self._preview_info = None
        self._preview_bytes = None
        self._preview_handle = None

        try:
            if not self.storeEnabled:
                raise ValueError('Artwork override store is not enabled')
            if role not in MEDIA_ROLES:
                raise ValueError('Invalid artwork role; must be cover, background, icon, hero, or logo')
            if canonical_id not in self._catalog_ids():
                raise ValueError('Unknown canonical game ID')

            local_path = _extract_local_path(selected_url_or_path) if selected_url_or_path else None
            if local_path is not None and self._override_root is not None:
                p = Path(local_path)
                if p == self._override_root or self._override_root in p.parents:
                    raise ValueError('Select source media outside the managed store')

            if self._store is not None:
                plan = self._store.preview(canonical_id, role, local_path)
                self._plan = plan
                self._pending_source_path = local_path
                info_dict = plan.as_dict()
                source = info_dict.get('source')
            else:
                self._pending_source_path = local_path
                if local_path is not None:
                    from native_services.artwork_overrides import _source, _raster
                    raw, identity = _source(Path(local_path))
                    source = dict(_raster(raw), identity=identity)
                else:
                    source = None

            if source is not None and local_path is not None:
                with open(local_path, 'rb') as f:
                    raw_bytes = f.read(MAX_MEDIA_BYTES)
                self._preview_bytes = raw_bytes
                self._preview_handle = f'p_{uuid.uuid4().hex}'
                preview_url = f'{IMAGE_SCHEME_PREFIX}{self._preview_handle}'
                self._preview_info = {
                    'valid': True,
                    'canonicalId': canonical_id,
                    'role': role,
                    'format': source.get('format', ''),
                    'width': source.get('width', 0),
                    'height': source.get('height', 0),
                    'bytes': source.get('bytes', 0),
                    'sha256': source.get('sha256', ''),
                    'isClear': False,
                    'previewUrl': preview_url,
                }
            else:
                self._preview_info = {
                    'valid': True,
                    'canonicalId': canonical_id,
                    'role': role,
                    'format': '',
                    'width': 0,
                    'height': 0,
                    'bytes': 0,
                    'sha256': '',
                    'isClear': True,
                    'previewUrl': '',
                }

            self._status = 'Preview ready'
            self._publish()
            return dict(self._preview_info)
        except Exception as exc:
            self._error = _safe_message(exc, 'Preview failed')
            self._publish()
            return {'valid': False, 'error': self._error}

    @Slot(str, result='QVariantMap')
    @Slot(result='QVariantMap')
    def apply(self, request_id: str = '') -> dict:
        """Apply the previously validated preview plan with idempotent request_id."""
        self._error = ''
        self._status = ''
        try:
            if not request_id or not isinstance(request_id, str):
                request_id = uuid.uuid4().hex
            request_id = request_id.strip()

            if not self.storeEnabled or self._preview_info is None or not self._preview_info.get('valid'):
                raise ValueError('Preview must succeed before apply')

            # Lazy store creation on first apply if override_root was provided
            if self._store is None:
                if self._override_root is None or not self._enabled:
                    raise ValueError('Artwork override store is not enabled')
                canonical_ids = list(self._catalog_ids())
                if not canonical_ids:
                    raise ValueError('Catalog must contain valid canonical IDs')
                self._store = ArtworkOverrideStore.create(self._override_root, canonical_ids)
                canonical_id = self._preview_info['canonicalId']
                role = self._preview_info['role']
                self._plan = self._store.preview(canonical_id, role, self._pending_source_path)

            if self._plan is None:
                raise ValueError('Preview must succeed before apply')

            result = self._store.apply(self._plan, request_id)
            audit_id = result.get('audit_id', 0)
            rev = result.get('revision', 0)

            # Clear active preview
            self._plan = None
            self._pending_source_path = None
            self._preview_info = None
            self._preview_bytes = None
            self._preview_handle = None

            self._refresh_handles()
            self._status = 'Artwork override applied successfully'
            self._latest_action = f'Applied override (audit #{audit_id})'
            self.operationFinished.emit('apply')
            self._publish()
            return {'success': True, 'auditId': audit_id, 'revision': rev}
        except Exception as exc:
            self._error = _safe_message(exc, 'Apply failed')
            self._publish()
            return {'success': False, 'error': self._error}

    @Slot(int, str, result='QVariantMap')
    @Slot(int, result='QVariantMap')
    @Slot(result='QVariantMap')
    def undo(self, audit_id: int = 0, request_id: str = '') -> dict:
        """Undo the latest applied artwork override."""
        self._error = ''
        self._status = ''
        try:
            if not self._store:
                raise ValueError('Artwork override store is not enabled')
            if not audit_id or audit_id <= 0:
                audit_id = self.undoableAuditId
            if audit_id <= 0:
                raise ValueError('Valid audit ID required')
            if not request_id or not isinstance(request_id, str):
                request_id = uuid.uuid4().hex
            request_id = request_id.strip()

            result = self._store.undo(audit_id, request_id)
            new_audit_id = result.get('audit_id', 0)
            rev = result.get('revision', 0)

            self.discardPreview()

            self._refresh_handles()
            self._status = 'Artwork override undone successfully'
            self._latest_action = f'Undone audit #{audit_id} (audit #{new_audit_id})'
            self.operationFinished.emit('undo')
            self._publish()
            return {'success': True, 'auditId': new_audit_id, 'revision': rev}
        except Exception as exc:
            self._error = _safe_message(exc, 'Undo failed')
            self._publish()
            return {'success': False, 'error': self._error}

    @Slot(result='QVariantList')
    def auditLog(self) -> list:
        """Return safe display audit log rows."""
        if not self._store:
            return []
        try:
            rows = []
            for item in self._store.audit():
                rows.append({
                    'auditId': item.get('audit_id'),
                    'intent': item.get('kind'),
                    'canonicalId': item.get('canonical_id'),
                    'role': item.get('role'),
                    'hasOverride': item.get('after') is not None,
                    'timestamp': item.get('created_at'),
                })
            return rows
        except Exception:
            return []
