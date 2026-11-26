"""Qt boundary for on-demand/background local launcher library synchronization."""
from pathlib import Path

from PySide6.QtCore import QObject, Property, QThread, Qt, Signal, Slot


class _SyncWorker(QObject):
    finished = Signal(object)

    def __init__(self, database, options=None):
        super().__init__()
        self.database = Path(database)
        self.options = dict(options or {})

    @Slot()
    def run(self):
        try:
            from native_services.launcher_library import sync_library
            from native_services.native_catalog import load_native_catalog
            result = sync_library(self.database, **self.options)
            catalog = load_native_catalog(self.database)
            self.finished.emit({'ok': True, 'result': result, 'catalog': catalog})
        except Exception:
            # Never put source file paths, usernames, tokens, or arbitrary parser
            # errors into QML/diagnostics. The old catalog remains untouched.
            self.finished.emit({'ok': False})


class LauncherLibraryView(QObject):
    busyChanged = Signal()
    noticeChanged = Signal()
    refreshed = Signal()

    def __init__(self, database, model, links, *, organizer_state=None, scan_options=None, parent=None):
        super().__init__(parent)
        self.database = Path(database)
        self.model = model
        self.links = links
        self.organizer_state = Path(organizer_state) if organizer_state else None
        self.scan_options = dict(scan_options or {})
        self._busy = False
        self._notice = 'Launcher discovery runs locally; no store requests are made.'
        self._source_counts = []
        self._source_states = {}
        self._last_success = False
        self._thread = None
        self._worker = None

    @Property(bool, notify=busyChanged)
    def busy(self): return self._busy

    @Property(str, notify=noticeChanged)
    def notice(self): return self._notice

    @Property('QVariantList', notify=noticeChanged)
    def sourceCounts(self): return list(self._source_counts)

    @Property('QVariantMap', notify=noticeChanged)
    def sourceStates(self): return dict(self._source_states)

    @Property(bool, notify=noticeChanged)
    def lastRefreshSucceeded(self): return self._last_success

    def _set_busy(self, value):
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()

    def _set_notice(self, value):
        if self._notice != value:
            self._notice = value
            self.noticeChanged.emit()

    @Slot()
    def refresh(self):
        if self._busy:
            return
        self._set_busy(True)
        self._set_notice('Scanning local launchers and configured ROM folders…')
        thread = QThread(self)
        worker = _SyncWorker(self.database, self.scan_options)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._complete, Qt.QueuedConnection)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        thread.finished.connect(self._cleanup)
        thread.start()

    @Slot(object)
    def _complete(self, result):
        if not isinstance(result, dict) or result.get('ok') is not True:
            self._last_success = False
            self._set_notice('Refresh failed. Your existing library was kept; check the launcher data and try again.')
            self._set_busy(False)
            self.refreshed.emit()
            return
        catalog = result['catalog']
        self._last_success = True
        self.model.replaceCatalog(catalog['games'])
        self.links.replaceCatalog(catalog)
        self.model.setIdentityLinks(self.links.resolution)
        if self.model._organizer is not None:
            ids = [g['id'] for g in catalog['games']]
            self.model._organizer.register_ids(ids)
            self.model._refresh_organizer()
        count = int(result['result'].get('records', 0))
        source_counts = result['result'].get('source_counts', {})
        self._source_counts = [{'provider': name, 'count': int(amount)}
                               for name, amount in sorted(source_counts.items(), key=lambda item: item[0].casefold())]
        self._source_states = result['result'].get('source_states', {})
        self._set_notice(f'Library refreshed from local data · {count} source records')
        self._set_busy(False)
        self.refreshed.emit()

    @Slot()
    def _cleanup(self):
        self._thread = None
        self._worker = None

    def close(self):
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait()
