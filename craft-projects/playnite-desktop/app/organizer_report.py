"""Lazy historical baseline: pure catalog review off the GUI thread.

The selected import is immutable for the session. Organizer overrides remain a
separate store, so this report is intentionally the same historical baseline as
before, not a live editable metadata projection. No execution or I/O is added.
"""
from PySide6.QtCore import QObject, Property, QThread, Signal, Slot
from organizer_view import BANNER, organizer_preview


class _Review(QThread):
    def __init__(self, games, builder, parent):
        super().__init__(parent)
        self.games = games
        self.builder = builder
        self.text = ''

    def run(self):
        try:
            self.text = self.builder(self.games)
        except Exception:
            self.text = BANNER + '\nUNAVAILABLE — read-only baseline could not be prepared.'


class OrganizerReport(QObject):
    changed = Signal()

    def __init__(self, games, parent=None, builder=organizer_preview):
        super().__init__(parent)
        self._games = games
        self._builder = builder
        self._job = None
        self._closed = False
        self._started = False
        self._text = BANNER + '\nOpen Organizer to prepare the historical read-only baseline.'

    @Property(str, notify=changed)
    def text(self):
        return self._text

    @Slot()
    def start(self):
        if self._closed or self._started:
            return
        self._started = True
        self._text = BANNER + '\nPreparing historical baseline in the background…'
        self._job = _Review(self._games, self._builder, self)
        self._job.finished.connect(self._finished)
        self._job.start()
        self.changed.emit()

    @Slot()
    def _finished(self):
        job = self._job
        if job is None:
            return
        self._job = None
        if not self._closed:
            self._text = job.text
            self.changed.emit()
        job.deleteLater()

    @Slot()
    def shutdown(self):
        self._closed = True
        if self._job is not None:
            self._job.wait()
            self._job.deleteLater()
            self._job = None
