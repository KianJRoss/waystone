"""Small cross-platform advisory byte/file lock surface.

Linux uses flock unchanged. Windows uses the CRT byte-range lock at offset zero; shared
locks are conservatively exclusive there. Lock files are coordination markers, not data
files. Callers still own path validation, descriptor lifetime and atomic data replacement.
"""
from __future__ import annotations

import errno
import os
from typing import Callable

from .platform import is_windows

# Stable fcntl-compatible operation bits used by existing call sites.
LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def _windows_lock(fd: int, action: str) -> None:
    import msvcrt

    # Every caller opens a dedicated lock file. Always lock its first byte; this avoids
    # depending on the caller's current offset and keeps the lock independent of payloads.
    os.lseek(fd, 0, os.SEEK_SET)
    mode = {"lock": msvcrt.LK_LOCK, "try_lock": msvcrt.LK_NBLCK,
            "unlock": msvcrt.LK_UNLCK}[action]
    msvcrt.locking(fd, mode, 1)


def flock(fd: int, operation: int, *, system: str | None = None,
          windows_adapter: Callable[[int, str], None] | None = None) -> None:
    """Acquire/release an advisory lock with the fcntl.flock call shape.

    On Windows, LOCK_SH maps to an exclusive byte lock because the CRT does not expose
    shared range locks. This may serialize readers, but cannot weaken mutual exclusion.
    """
    if is_windows(system):
        adapter = windows_adapter or _windows_lock
        if operation & LOCK_UN:
            adapter(fd, "unlock")
            return
        action = "try_lock" if operation & LOCK_NB else "lock"
        try:
            adapter(fd, action)
        except OSError as exc:
            if operation & LOCK_NB and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BlockingIOError(exc.errno, "lock is already held") from exc
            raise
        return

    import fcntl
    fcntl.flock(fd, operation)
