import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from launcher_library_view import LauncherLibraryView


class _Model:
    def __init__(self): self.replaced = None; self.links = None; self._organizer = None
    def replaceCatalog(self, games): self.replaced = games
    def setIdentityLinks(self, value): self.links = value


class _Links:
    resolution = {}
    def __init__(self): self.catalog = None
    def replaceCatalog(self, catalog): self.catalog = catalog


class LauncherLibraryViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def pump(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.001)
        self.assertTrue(predicate(), 'background source sync completion did not arrive')

    def test_refresh_scans_and_projects_off_ui_thread(self):
        model, links = _Model(), _Links()
        view = LauncherLibraryView(Path('/unused/library.sqlite3'), model, links)
        self.addCleanup(view.close)
        gui_thread = threading.get_ident(); worker_threads = []
        games = [{'id': 'launcher:steam:7', 'title': 'Fixture game', 'variants': []}]
        with patch('native_services.launcher_library.sync_library',
                   side_effect=lambda *_a, **_k: (worker_threads.append(threading.get_ident()) or {'records': 1})), \
             patch('native_services.native_catalog.load_native_catalog', return_value={'games': games}):
            view.refresh()
            self.assertTrue(view.busy)
            self.pump(lambda: not view.busy)
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], gui_thread)
        self.assertEqual(model.replaced, games)
        self.assertEqual(links.catalog, {'games': games})
        self.assertEqual(view.notice, 'Library refreshed from local data · 1 source records')

    def test_failed_scan_keeps_catalog_and_never_leaks_exception_details(self):
        model, links = _Model(), _Links()
        view = LauncherLibraryView(Path('/unused/library.sqlite3'), model, links)
        self.addCleanup(view.close)
        with patch('native_services.launcher_library.sync_library', side_effect=RuntimeError('token=do-not-display')):
            view.refresh(); self.pump(lambda: not view.busy)
        self.assertIsNone(model.replaced)
        self.assertIn('existing library was kept', view.notice)
        self.assertNotIn('do-not-display', view.notice)

