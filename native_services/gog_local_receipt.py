"""Read selected GOGdl output only; no Heroic cache, subprocess, glob or auth.

Source semantics: heroic-gogdl/gogdl/launch.py (task path vs workingDir) and
HeroicGamesLauncher/src/common/types/gog.ts (GOGGameDotInfoFile/FileTask).
Windows root metadata and Linux game/ metadata are distinct bounded layouts.
"""
from __future__ import annotations
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
from .provider_receipt_gog import verify_gog_install

_MAX_BYTES = 512 * 1024
_PRODUCT = re.compile(r'[1-9][0-9]{0,19}\Z')


def _snapshot(path, root):
    if not path.resolve().is_relative_to(root):
        raise ValueError('metadata_escape')
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BYTES:
            raise ValueError('metadata_size_or_type')
        chunks, length = [], 0
        while length <= _MAX_BYTES:
            block = os.read(fd, min(65536, _MAX_BYTES + 1 - length))
            if not block:
                break
            chunks.append(block)
            length += len(block)
        after = os.fstat(fd)
        selected = os.stat(path, follow_symlinks=False)
        fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (length > _MAX_BYTES or length != after.st_size or
                fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(selected)):
            raise ValueError('metadata_changed')
    finally:
        os.close(fd)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_metadata_key')
            result[key] = value
        return result
    return json.loads(b''.join(chunks), object_pairs_hook=unique)


def _relative(raw):
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        raise ValueError('invalid_task_path')
    path = Path(raw.replace('\\', '/'))
    if path.is_absolute() or PureWindowsPath(raw).drive or '..' in path.parts:
        raise ValueError('task_path_escape')
    return path


def verify_selected_gog_directory(product, destination, *, platform=None, host=None):
    """Return (receipt|None, safe reason). Exact completed-plan destination only."""
    if not isinstance(product, str) or not _PRODUCT.fullmatch(product):
        return None, 'invalid_product'
    if not isinstance(destination, (str, Path)):
        return None, 'invalid_destination'
    try:
        if not Path(destination).is_absolute() or '\x00' in str(destination):
            return None, 'invalid_destination'
    except (OSError, ValueError, TypeError):
        return None, 'invalid_destination'
    if platform not in (None, 'windows', 'linux'):
        return None, 'unsupported_platform'
    try:
        root = Path(destination).resolve()
        if not root.is_dir():
            return None, 'destination_unavailable'
        if (root/'.gogdl-resume').exists():
            return None, 'incomplete_install'
        locations = []
        if platform in (None, 'windows'):
            locations.append((root/f'goggame-{product}.info', root))
        if platform in (None, 'linux'):
            locations.append((root/'game'/f'goggame-{product}.info', root/'game'))
        present = [(path, base) for path, base in locations if path.exists() or path.is_symlink()]
        if not present:
            return None, 'local_metadata_missing'
        if len(present) != 1:
            return None, 'local_metadata_ambiguous'
        path, executable_base = present[0]
        data = _snapshot(path, root)
        if (not isinstance(data, dict) or data.get('gameId') != product or
                data.get('rootGameId') != product):
            return None, 'local_product_mismatch'
        tasks = data.get('playTasks')
        if not isinstance(tasks, list) or not tasks or not all(isinstance(t, dict) for t in tasks):
            return None, 'invalid_local_tasks'
        if any('isPrimary' in t and type(t['isPrimary']) is not bool for t in tasks):
            return None, 'invalid_primary_marker'
        primary = [t for t in tasks if t.get('isPrimary') is True]
        if not primary and len(tasks) == 1:
            primary = tasks
        if len(primary) != 1:
            return None, 'ambiguous_local_tasks'
        task = primary[0]
        if task.get('type') != 'FileTask' or task.get('category') == 'document':
            return None, 'non_executable_task'
        executable = (executable_base/_relative(task.get('path'))).resolve()
        working_dir = (root/_relative(task.get('workingDir') or '.')).resolve()
        if not executable.is_relative_to(root) or not working_dir.is_relative_to(root):
            return None, 'task_path_escape'
        normalized = {'appName': product, 'install_path': str(root),
                      'executable': str(executable), 'is_dlc': False}
        receipt, reason, _ = verify_gog_install(
            normalized, selected_product_id=product, selected_destination=str(root), host=host)
        if receipt is None:
            return None, reason
        # Do not rely on a case-folded first match if a filesystem has two.
        declared = executable.relative_to(root)
        current = root
        for part in declared.parts:
            exact = current/part
            if exact.exists():
                current = exact
                continue
            matches = [p for p in current.iterdir() if p.name.casefold() == part.casefold()]
            if len(matches) != 1:
                return None, 'ambiguous_executable_case'
            current = matches[0]
        if not current.resolve().is_relative_to(root):
            return None, 'task_path_escape'
        receipt.update(verified_by='gog-game-local-info', working_dir=str(working_dir),
                       metadata_locator=str(path))
        return receipt, 'verified'
    except (OSError, ValueError, TypeError, RuntimeError):
        return None, 'local_metadata_unusable'
