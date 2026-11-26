"""Durable app-owned registry for exact, verified provider installations.

Receipts attest installed files only; they do not attest launch/playability.
All app-owned state updates are atomic and protected by an advisory lock so
concurrent workers cannot clobber each other.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from native_services import file_locking as fcntl

try:
    from private_addons import (
        ADDON_RECEIPT_PROVIDERS as _ADDON_RECEIPT_PROVIDERS,
        validate_addon_receipt_product as _validate_addon_receipt,
    )
except ImportError:
    _ADDON_RECEIPT_PROVIDERS = frozenset()
    _validate_addon_receipt = None

PROVIDERS = frozenset({'xbox', 'gog', 'amazon', 'epic'}) | _ADDON_RECEIPT_PROVIDERS
_SAFE_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}\Z')
_PRODUCT = _SAFE_ID
_ACCOUNT = re.compile(r'[A-Za-z0-9_.@+-]{1,256}\Z')
_HOST = re.compile(r'[A-Za-z0-9_.-]{1,255}\Z')
_INCOMPLETE_MARKERS = (
    '.nexus-update-incomplete-v1',
    '.gogdl-resume',
    '.xodus-streaming-tmp.msixvc',
    '.install-incomplete',
    '.partial',
    '.download-incomplete',
)
_VERIFIED_STATE = 'files_verified'
_MAX_REGISTRY_BYTES = 4 * 1024 * 1024
_SECRET_KEYS = ('token', 'password', 'secret', 'credential', 'credentials', 'prompt', 'cookie', 'authorization')


class ReceiptError(ValueError):
    """Invalid receipt, identity mismatch, or unsafe/corrupt state file."""


def _reject_secrets(obj, path='receipt'):
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            if any(secret in lowered for secret in _SECRET_KEYS):
                raise ReceiptError(f'Credential or prompt data is not allowed in {path}.')
            _reject_secrets(value, f'{path}.{key}')
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _reject_secrets(value, f'{path}[{i}]')


def _text(value, pattern, label, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or not pattern.fullmatch(value) or '..' in value:
        raise ReceiptError(f'Invalid {label}.')
    return value


def _abs_path(value, label):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ReceiptError(f'{label} must be an absolute path.')
    path = Path(value)
    if not path.is_absolute():
        raise ReceiptError(f'{label} must be an absolute path.')
    # Resolve for a stable identity/storage form, but callers must still verify
    # presence under the recorded constraints.
    return str(path.resolve())


def _normalize_receipt(raw):
    if not isinstance(raw, dict):
        raise ReceiptError('Receipt must be an object.')
    _reject_secrets(raw)

    provider = raw.get('provider')
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ReceiptError('Unknown provider.')

    product_raw = raw.get('product_id')
    if provider == 'xbox':
        product = _text(product_raw, re.compile(r'[A-Za-z0-9]{12,20}\Z'), 'Xbox product ID')
    elif provider == 'gog':
        product = _text(product_raw, re.compile(r'[0-9]{1,20}\Z'), 'GOG product ID')
    elif provider in _ADDON_RECEIPT_PROVIDERS and _validate_addon_receipt is not None:
        _validate_addon_receipt(provider, product_raw, ReceiptError)
        product = product_raw
    elif provider == 'epic':
        product = _text(product_raw, _SAFE_ID, 'Epic product ID')
    elif provider == 'amazon':
        product = _text(product_raw, _SAFE_ID, 'Amazon product ID')
    else:
        product = _text(product_raw, _SAFE_ID, 'product ID')
    if provider == 'xbox' and not re.fullmatch(r'[A-Z0-9]{12}', product):
        raise ReceiptError('Invalid Xbox product ID.')
    if provider == 'gog' and not re.fullmatch(r'[1-9][0-9]{0,19}', product):
        raise ReceiptError('Invalid GOG product ID.')
    if provider == 'amazon' and not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', product):
        raise ReceiptError('Invalid Amazon product ID.')

    account = _text(raw.get('account_id'), _ACCOUNT, 'account ID', optional=True)
    host = _text(raw.get('host'), _HOST, 'host')
    install_path = _abs_path(raw.get('install_path') or raw.get('destination'), 'Install path')
    executable = _abs_path(raw.get('executable'), 'Executable')

    if not Path(executable).resolve().is_relative_to(Path(install_path).resolve()):
        raise ReceiptError('Executable must be inside the installation path.')

    state = raw.get('state')
    if state != _VERIFIED_STATE:
        # Non-completed/cancelled/failed/download-only results are not install
        # receipts and must not mutate prior verified state.
        raise ReceiptError('Only verified installation receipts may be registered.')

    fs_device = raw.get('filesystem_device')
    if fs_device is not None and (type(fs_device) is not int or fs_device < 0):
        raise ReceiptError('Invalid filesystem device.')

    playability = raw.get('playability', 'unknown')
    if not isinstance(playability, str) or len(playability) > 500 or any(ord(c) < 32 for c in playability):
        raise ReceiptError('Invalid playability metadata.')

    record = {
        'provider': provider,
        'product_id': product,
        'account_id': account,
        'host': host,
        'install_path': install_path,
        'executable': executable,
        'state': _VERIFIED_STATE,
        # Kept separate from installed-file verification on purpose.
        'playability': playability,
    }
    if fs_device is not None:
        record['filesystem_device'] = fs_device

    # Preserve harmless provider-specific corroboration fields when they are
    # simple scalar strings/ints from a verified source (e.g. xbox content id),
    # but never invent required fields.
    for key in ('selected_product_id', 'content_id', 'verified_by', 'install_root'):
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            if len(value) > 256 or any(ord(c) < 32 for c in value):
                raise ReceiptError(f'Invalid {key}.')
            record[key] = value
        elif type(value) is int and 0 <= value <= 2**53 - 1:
            record[key] = value
        else:
            raise ReceiptError(f'Invalid {key}.')

    for key in ('working_dir','metadata_locator'):
        if key in raw:
            value=_abs_path(raw[key], key)
            if not Path(value).is_relative_to(Path(install_path)):
                raise ReceiptError('Receipt provenance path escapes installation.')
            record[key]=value

    return record


def _identity_key(record):
    return json.dumps([
        record.get('provider'),
        record.get('product_id'),
        record.get('account_id'),
        record.get('host'),
        record.get('install_path'),
    ], separators=(',', ':'))


def _is_present(record, host):
    """Current host presence only. Missing/unavailable state keeps history."""
    try:
        if not isinstance(record, dict):
            return False
        if record.get('state') != _VERIFIED_STATE:
            return False
        if record.get('host') != host:
            return False
        root_raw = record.get('install_path')
        exe_raw = record.get('executable')
        if not isinstance(root_raw, str) or not isinstance(exe_raw, str):
            return False
        root = Path(root_raw)
        exe = Path(exe_raw)
        if not root.is_absolute() or not exe.is_absolute():
            return False
        if not root.exists() or not root.is_dir():
            return False
        # If the recorded device cannot be confirmed (unavailable mount / changed
        # device) the install is not currently present; history is retained.
        if 'filesystem_device' in record:
            try:
                if root.stat().st_dev != record.get('filesystem_device'):
                    return False
            except OSError:
                return False
        for marker in _INCOMPLETE_MARKERS:
            try:
                if (root / marker).exists():
                    return False
            except OSError:
                return False
        try:
            if not exe.is_file():
                return False
            resolved_exe = exe.resolve()
            resolved_root = root.resolve()
            if not resolved_exe.is_relative_to(resolved_root):
                return False
        except (OSError, RuntimeError):
            return False
        return True
    except Exception:
        # Persisted/corrupt records must never make a query crash or claim presence.
        return False


def _sanitize_persisted_record(record):
    if not isinstance(record, dict):
        return None
    try:
        clean = _normalize_receipt(record)
    except (ValueError, TypeError, OSError, RuntimeError):
        return None
    if 'received_at' in record:
        clean['received_at'] = copy.deepcopy(record['received_at'])
    history = record.get('history')
    if isinstance(history, list):
        safe_history = []
        for item in history:
            if not isinstance(item, dict):
                continue
            entry = {}
            if 'received_at' in item:
                entry['received_at'] = copy.deepcopy(item['received_at'])
            if isinstance(item.get('state'), str) and len(item['state']) <= 64:
                entry['state'] = item['state']
            if entry:
                safe_history.append(entry)
        clean['history'] = safe_history
    else:
        clean['history'] = []
    return clean


def adapt_xodus_verified_receipt(receipt, *, host=None, account_id=None, clock=time.time):
    """Adapt a real xodus verify_installation/execute receipt for registration.

    The source receipt shape is preserved logically: destination/executable and
    verified file state come from xodus; this adapter only adds host/device
    context required by the app-owned registry. No extra required product fields
    are invented.
    """
    if not isinstance(receipt, dict):
        raise ReceiptError('Xodus receipt must be an object.')
    _reject_secrets(receipt)
    provider = receipt.get('provider')
    if provider != 'xbox':
        raise ReceiptError('Xodus adapter requires an Xbox receipt.')
    if receipt.get('state') != _VERIFIED_STATE:
        raise ReceiptError('Xodus receipt is not verified installed files.')
    destination = receipt.get('destination') or receipt.get('install_path')
    executable = receipt.get('executable')
    product_id = receipt.get('product_id')
    if not isinstance(destination, str) or not isinstance(executable, str) or not isinstance(product_id, str):
        raise ReceiptError('Incomplete Xodus receipt metadata.')
    resolved_host = _text(host or socket.gethostname(), _HOST, 'host')
    fs_device = None
    try:
        fs_device = Path(destination).stat().st_dev
    except OSError:
        fs_device = None
    adapted = {
        'provider': 'xbox',
        'product_id': product_id,
        'account_id': account_id,
        'host': resolved_host,
        'install_path': destination,
        'executable': executable,
        'state': _VERIFIED_STATE,
        'playability': receipt.get('playability', 'unknown'),
    }
    if fs_device is not None:
        adapted['filesystem_device'] = fs_device
    for key in ('selected_product_id', 'content_id'):
        if key in receipt:
            adapted[key] = receipt[key]
    return _normalize_receipt(adapted)


class InstallReceiptRegistry:
    """Atomic, locked registry retaining receipt history including absent installs."""

    def __init__(self, path, *, host=None, clock=time.time):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + '.lock')
        self.host = _text(host or socket.gethostname(), _HOST, 'host')
        self.clock = clock

    def _load_unlocked(self):
        try:
            stat = self.path.stat()
            if stat.st_size > _MAX_REGISTRY_BYTES:
                raise ReceiptError('Registry exceeds size limit.')
            with self.path.open('r', encoding='utf-8') as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('records'), dict):
                raise ReceiptError('Invalid registry schema.')
            # Do not blindly trust persisted records as proof of current presence;
            # sanitize shape for safe querying and re-evaluate presence dynamically.
            records = {}
            for key, value in data['records'].items():
                if not isinstance(key, str):
                    continue
                clean = _sanitize_persisted_record(value)
                if clean is not None:
                    records[key] = clean
            return {'schema': 1, 'records': records}
        except FileNotFoundError:
            return {'schema': 1, 'records': {}}
        except json.JSONDecodeError:
            raise ReceiptError('Cannot read installation registry.') from None
        except OSError:
            raise ReceiptError('Cannot read installation registry.') from None

    def _save_unlocked(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(prefix='.install-receipts-', dir=str(self.path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, sort_keys=True, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            try:
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        finally:
            try:
                if os.path.exists(temp):
                    os.unlink(temp)
            except OSError:
                pass

    def _locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    @staticmethod
    def _unlock(fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def register(self, backend_receipt):
        """Register a verified, currently present installation receipt.

        Accepts either a normalized registry receipt or a provider receipt that
        already contains verified file metadata and host/path/executable fields.
        For real Xodus receipts, use adapt_xodus_verified_receipt first or pass
        an already adapted receipt.
        """
        record = _normalize_receipt(copy.deepcopy(backend_receipt))
        if record.get('host') != self.host:
            raise ReceiptError('Receipt host does not match this registry host.')

        lock_fd = self._locked()
        try:
            # Presence is evaluated under lock against current filesystem state.
            if not _is_present(record, self.host):
                raise ReceiptError('Verified installation is not currently complete and present.')
            data = self._load_unlocked()
            key = _identity_key(record)
            previous = data['records'].get(key, {})
            history = previous.get('history', []) if isinstance(previous, dict) else []
            if not isinstance(history, list):
                history = []
            ts = self.clock()
            entry = {'received_at': ts, 'state': _VERIFIED_STATE}
            record['received_at'] = ts
            record['history'] = history + [entry]
            # files_verified remains distinct from playability/launch config.
            if 'playability' not in record:
                record['playability'] = 'unknown'
            data['records'][key] = record
            self._save_unlocked(data)
            return copy.deepcopy(record)
        finally:
            self._unlock(lock_fd)

    def register_xodus_verified(self, receipt, *, account_id=None):
        adapted = adapt_xodus_verified_receipt(receipt, host=self.host, account_id=account_id, clock=self.clock)
        return self.register(adapted)

    def snapshot(self):
        """Detached, dynamically checked host presence; one locked read per refresh."""
        if not self.path.exists():
            return []  # Unused registry does not create directories/locks on read.
        lock_fd = self._locked()
        try:
            data = self._load_unlocked()
        finally:
            self._unlock(lock_fd)
        return [dict(copy.deepcopy(r), is_present=_is_present(r, self.host))
                for r in data['records'].values()]

    def query(self, game_id, links):
        """Project exact provider/product receipts for one canonical game.

        links: iterable of mappings containing at least game_id, provider,
        product_id. Matching is exact identity only; titles are never used.
        Returns detached records with dynamic is_present state; history is kept.
        """
        if not isinstance(game_id, str) or not game_id:
            raise ReceiptError('Canonical game ID is required.')

        exact = set()
        for link in links or []:
            if not isinstance(link, dict):
                continue
            if link.get('game_id') != game_id:
                continue
            provider = link.get('provider')
            product_id = link.get('product_id')
            if isinstance(provider, str) and isinstance(product_id, str) and provider and product_id:
                exact.add((provider, product_id))

        lock_fd = self._locked()
        try:
            data = self._load_unlocked()
        finally:
            self._unlock(lock_fd)

        results = []
        for record in data.get('records', {}).values():
            if not isinstance(record, dict):
                continue
            identity = (record.get('provider'), record.get('product_id'))
            if identity not in exact:
                continue
            item = copy.deepcopy(record)
            # Dynamic presence: missing exe/device/mount/incomplete marker loses
            # present state but does not delete retained receipt history.
            item['is_present'] = _is_present(record, self.host)
            # files_verified != launch-configured/playability.
            item['files_verified'] = record.get('state') == _VERIFIED_STATE
            item.setdefault('playability', 'unknown')
            results.append(item)
        return results


def project_canonical_installed_state(registry, game_id, links):
    """Concrete adapter to existing exact canonical-game projection shape.

    Returns a detached, query-only projection: canonical game id plus exact
    provider/product receipt rows (with dynamic presence) derived from explicit
    canonical links.
    """
    return {
        'game_id': game_id,
        'install_receipts': registry.query(game_id, links),
    }


def adapt_verified_provider_receipt(receipt, *, host=None):
    """Normalize non-Xbox provider receipts that already carry verified metadata.

    GOG/Amazon completions lacking verified installation metadata must not be
    passed here and will be rejected (no executable/path/verified state).
    """
    normalized = copy.deepcopy(receipt) if isinstance(receipt, dict) else receipt
    if isinstance(normalized, dict) and host and 'host' not in normalized:
        normalized['host'] = host
    if isinstance(normalized, dict) and 'install_path' not in normalized and 'destination' in normalized:
        normalized['install_path'] = normalized['destination']
    # Best-effort device capture for present-state checks when available.
    if isinstance(normalized, dict) and 'filesystem_device' not in normalized:
        try:
            normalized['filesystem_device'] = Path(str(normalized.get('install_path'))).stat().st_dev
        except Exception:
            pass
    return _normalize_receipt(normalized)
