"""Small, reopenable first-run completion marker for the app-owned library."""
import json
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QObject, Property, Signal, Slot
from native_services.waystone_paths import state_home


class FirstRunGuide(QObject):
    changed = Signal()

    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        self.path = Path(path) if path else state_home() / 'waystone' / 'first-run-v1.json'
        self._visible = False
        self._complete = self._read_complete()
        self._message = ''

    def _read_complete(self):
        try:
            if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 4096:
                return False
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data == {'version': 1, 'complete': True}
        except (OSError, ValueError, UnicodeError):
            return False

    @Property(bool, notify=changed)
    def visible(self): return self._visible

    @Property(bool, notify=changed)
    def completed(self): return self._complete

    @Property(str, notify=changed)
    def status(self): return self._message

    @Slot()
    def openOnceAfterSync(self):
        if not self._complete:
            self._visible = True
            self.changed.emit()

    @Slot()
    def reopen(self):
        self._visible = True
        self.changed.emit()

    @Slot()
    def dismiss(self):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.first-run-', dir=self.path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'version': 1, 'complete': True}, stream, separators=(',', ':'))
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try: os.fsync(directory)
                finally: os.close(directory)
            except BaseException:
                try: os.close(fd)
                except OSError: pass
                Path(temporary).unlink(missing_ok=True)
                raise
            self._complete = True
            self._message = ''
        except OSError:
            self._message = 'Setup is dismissed for now, but Waystone could not save that preference. Reopen it from Settings.'
        self._visible = False
        self.changed.emit()
