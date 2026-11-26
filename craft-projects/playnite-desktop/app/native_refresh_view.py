"""Explicit, detached native refresh. No discovery or writes until refresh().

runner has subprocess.run's keyword contract. Only a successful, bounded JSON
snapshot for the unchanged selected catalog is signalled; the owning application
still validates/replaces its ConnectionView. close() joins (at most the collector
timeout for the default runner); injected runners must also honor timeout.
"""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading

from PySide6.QtCore import QObject, Property, QThread, Qt, Signal, Slot


TIMEOUT_SECONDS = 60
CATALOG_LIMIT = 64 * 1024 * 1024
DATABASE_LIMIT = 512 * 1024 * 1024
INVENTORY_LIMIT = 8 * 1024 * 1024
_ERRORS = {
    'failed': 'Native refresh failed. Previous snapshot unchanged.',
    'timeout': 'Native refresh timed out. Previous snapshot unchanged.',
    'invalid': 'Native refresh returned an invalid snapshot. Previous snapshot unchanged.',
    'stale': 'Selected catalog changed. Refresh again; previous snapshot unchanged.',
}


class _Rejected(Exception):
    pass


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read(path, limit):
    """Bounded stable regular-file read, rejecting links, FIFOs and replacement."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise _Rejected('invalid')
        chunks, count = [], 0
        while count <= limit:
            block = os.read(fd, min(65536, limit + 1 - count))
            if not block:
                break
            chunks.append(block)
            count += len(block)
        after = os.fstat(fd)
        selected = path.stat(follow_symlinks=False)
        if (count > limit or count != after.st_size
                or _fingerprint(before) != _fingerprint(after)
                or _fingerprint(after) != _fingerprint(selected)):
            raise _Rejected('invalid')
        return b''.join(chunks), _fingerprint(after)
    finally:
        os.close(fd)


def _source_digest(path, native_mode=False):
    if not native_mode:
        raw, fingerprint = _read(path, CATALOG_LIMIT)
        return sha256(raw).hexdigest(), fingerprint
    # Stream SQLite bytes; never parse the database as JSON or allocate its size.
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > DATABASE_LIMIT:
            raise _Rejected('invalid')
        digest, count = sha256(), 0
        while count <= DATABASE_LIMIT:
            block = os.read(fd, min(65536, DATABASE_LIMIT + 1 - count))
            if not block: break
            count += len(block); digest.update(block)
        after = os.fstat(fd)
        selected = path.stat(follow_symlinks=False)
        if (count > DATABASE_LIMIT or count != after.st_size
                or _fingerprint(before) != _fingerprint(after)
                or _fingerprint(after) != _fingerprint(selected)):
            raise _Rejected('invalid')
        return digest.hexdigest(), _fingerprint(after)
    finally:
        os.close(fd)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Rejected('invalid')
        result[key] = value
    return result


def _nonfinite(_value):
    raise _Rejected('invalid')


def _metadata(path, digest, native_mode=False):
    raw, fingerprint = _read(path, INVENTORY_LIMIT)
    try:
        data = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
        if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
                or data['schema_version'] != 1
                or data.get('inventory_kind') != 'native-connections-readonly'
                or data.get('execution_supported') is not False):
            raise _Rejected('invalid')
        if data.get('catalog_sha256') != digest:
            raise _Rejected('stale')
        if native_mode:
            identity = data.get('source_identity', {})
            if (identity.get('kind') != 'native-library-sqlite-v1'
                    or identity.get('database_sha256') != digest):
                raise _Rejected('stale')
        observed = data.get('observed_at')
        if not isinstance(observed, str) or len(observed) > 128:
            raise _Rejected('invalid')
        timestamp = datetime.fromisoformat(observed)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise _Rejected('invalid')
        return timestamp.isoformat(), fingerprint
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _Rejected('invalid') from exc


@dataclass
class _Job:
    thread: object = None
    path: object = None
    observed: str = ''
    catalog_fingerprint: object = None
    snapshot_fingerprint: object = None
    error: str = ''


class _Completion(QObject):
    finished = Signal(object)


def _collect(job, completion, catalog, output_root, collector_root, runner, native_mode=False):
    """Worker owns all reads, hashing, subprocess work and publication staging."""
    try:
        digest, _ = _source_digest(catalog, native_mode)
        output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A fresh private directory and the collector's O_EXCL publication keep
        # every earlier snapshot and all selected source files untouched.
        directory = Path(tempfile.mkdtemp(prefix='refresh-', dir=output_root))
        path = directory / 'native-connection-inventory.json'
        env = {'PYTHONPATH': str(collector_root), 'PYTHONNOUSERSITE': '1',
               'PYTHONDONTWRITEBYTECODE': '1'}
        # Do not inherit arbitrary PYTHON*, loader hooks, or credentials.
        for key in ('PATH', 'LANG', 'LC_ALL', 'TZ'):
            if key in os.environ:
                env[key] = os.environ[key]
        result = runner(
            [sys.executable, '-B', '-m', 'native_services.connection_inventory',
             '--library-db' if native_mode else '--catalog', str(catalog), '--output', str(path)],
            cwd=str(collector_root), env=env, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, check=False)
        if result.returncode != 0:
            raise _Rejected('failed')
        observed, snapshot_fingerprint = _metadata(path, digest, native_mode)
        current, catalog_fingerprint = _source_digest(catalog, native_mode)
        if current != digest:
            raise _Rejected('stale')
        job.path, job.observed = path, observed
        job.catalog_fingerprint = catalog_fingerprint
        job.snapshot_fingerprint = snapshot_fingerprint
    except subprocess.TimeoutExpired:
        job.error = 'timeout'
    except _Rejected as exc:
        job.error = str(exc) if str(exc) in _ERRORS else 'invalid'
    except Exception:
        # Neither source bytes, stdout/stderr, paths nor exception text cross
        # into Qt properties/signals. The collector is never run through a shell.
        job.error = 'failed'
    completion.finished.emit(job)


class NativeRefreshView(QObject):
    changed = Signal()
    refreshed = Signal(str)

    def __init__(self, catalog: Path, output_root: Path, collector_root: Path,
                 runner=None, parent=None, *, library_db=None):
        super().__init__(parent)
        # Absolute but do not resolve symlinks in the selected catalog pathname:
        # the bounded reader must reject them rather than follow them.
        self._native_mode = library_db is not None
        selected = library_db if self._native_mode else catalog
        if selected is None:
            raise ValueError('Explicit catalog or native database required')
        self._catalog = Path(os.path.abspath(selected))
        self._output_root = Path(os.path.abspath(output_root))
        self._collector_root = Path(os.path.abspath(collector_root))
        self._runner = subprocess.run if runner is None else runner
        self._job = None
        self._closed = False
        self._busy = False
        self._status = 'Ready to refresh native connections.'
        self._error = ''
        self._observed_at = ''
        self._snapshot_path = None
        # Not parented: a worker retains this emitter even if its receiver is
        # destroyed. Qt disconnects deleted receivers; no worker touches a view.
        self._completion = _Completion()
        self._completion.finished.connect(self._complete, Qt.QueuedConnection)

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(str, notify=changed)
    def observedAt(self):
        return self._observed_at

    def _assert_owner(self):
        if QThread.currentThread() != self.thread():
            raise RuntimeError('NativeRefreshView must be used on its Qt thread')

    @Slot(result=bool)
    def refresh(self):
        self._assert_owner()
        if self._closed or self._busy:
            return False
        job = _Job()
        job.thread = threading.Thread(
            target=_collect, name='native-refresh', daemon=False,
            args=(job, self._completion, self._catalog, self._output_root,
                  self._collector_root, self._runner, self._native_mode))
        self._job = job
        self._busy, self._error = True, ''
        self._status = 'Refreshing native connections…'
        try:
            job.thread.start()
        except Exception:
            self._job = None
            self._busy = False
            self._error = _ERRORS['failed']
            self._status = 'Native refresh failed.'
        self.changed.emit()
        return self._busy

    @Slot(object)
    def _complete(self, job):
        if self._job is not job or self._closed:
            return
        job.thread.join()
        self._job = None
        self._busy = False
        if not job.error:
            try:
                # Cheap final identity checks on the Qt thread also reject a
                # catalog/output replacement while completion was queued.
                if _fingerprint(self._catalog.stat(follow_symlinks=False)) != job.catalog_fingerprint:
                    job.error = 'stale'
                elif _fingerprint(job.path.stat(follow_symlinks=False)) != job.snapshot_fingerprint:
                    job.error = 'invalid'
            except OSError:
                job.error = 'stale'
        if job.error:
            self._error = _ERRORS[job.error]
            self._status = 'Native refresh failed.'
        else:
            self._snapshot_path = job.path
            self._observed_at = job.observed
            self._error = ''
            self._status = 'Native connections refreshed.'
        self.changed.emit()
        if not job.error and not self._closed:
            self.refreshed.emit(str(job.path))

    def wait(self, timeout_ms=None):
        """Join work; deliver completion without requiring a Qt event loop.

        Return False on a bounded wait timeout. None waits until runner timeout
        and validation complete. Must be called on the object's owning Qt thread.
        """
        self._assert_owner()
        job = self._job
        if job is None:
            return True
        job.thread.join(None if timeout_ms is None else max(0, timeout_ms) / 1000)
        if job.thread.is_alive():
            return False
        self._complete(job)
        return True

    @Slot()
    def close(self):
        """Idempotent shutdown for aboutToQuit; discard late results and join."""
        self._assert_owner()
        self._closed = True
        job, self._job = self._job, None
        if job is not None:
            job.thread.join()
        self._busy = False
        self._status = 'Native refresh closed.'
        self.changed.emit()
