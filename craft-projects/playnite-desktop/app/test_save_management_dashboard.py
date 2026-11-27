"""Offscreen Qt Quick integration tests for SaveManagementDashboard."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QMetaObject, Qt, QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest, QSignalSpy

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from save_management_view import SaveManagementView


def make_dashboard_runner(version='ludusavi 0.24.0', destination_file=None, delay: float = 0.0):
    executed = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess:
        if delay > 0:
            time.sleep(delay)
        executed.append(list(argv))
        if '--version' in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=version, stderr='')

        if 'backup' in argv:
            out = json.dumps({
                'overall': {'totalGames': 1, 'totalBytes': 1024, 'processedGames': 1, 'processedBytes': 1024},
                'games': {
                    argv[-1]: {
                        'decision': 'Processed',
                        'files': {'/dummy/save.dat': {'bytes': 1024}},
                    }
                }
            })
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr='')

        if 'restore' in argv:
            target_file = str(destination_file) if destination_file else '/dummy/restore.dat'
            out = json.dumps({
                'overall': {'totalGames': 1, 'totalBytes': 500, 'processedGames': 1, 'processedBytes': 500},
                'games': {
                    argv[-1]: {
                        'decision': 'Processed',
                        'files': {target_file: {'bytes': 500}},
                    }
                }
            })
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr='')

        return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')

    runner.executed = executed
    return runner


class SaveManagementDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(['save-dashboard-tests'])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.config_path = self.base / 'save-config.json'
        (self.base / 'backups').mkdir()
        self.conflicting_file = self.base / 'existing_save.dat'
        self.conflicting_file.write_text('current save')

    def test_dashboard_ui_full_flow(self):
        runner = make_dashboard_runner(destination_file=self.conflicting_file)
        view = SaveManagementView(self.config_path, runner=runner)
        self.addCleanup(view.shutdown)

        # 1. Verify capability check is NOT run on startup
        self.assertFalse(view.toolAvailable)
        self.assertIn('checking ludusavi when saves opens', view.toolStatus.casefold())  # nothing ran at startup

        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('saveManagement', view)
        app_dir = Path(__file__).resolve().parent
        engine.addImportPath(str(app_dir))

        window = QQuickWindow()
        window.resize(1024, 768)
        component = QQmlComponent(engine)
        component.setData(b'''
import QtQuick
import QtQuick.Controls

Item {
    width: 1024; height: 768
    SaveManagementDashboard {
        id: dash
    }
}
''', QUrl.fromLocalFile(str(app_dir / 'test_host.qml')))

        root_item = component.create()
        self.assertIsNotNone(root_item, f"Failed to instantiate dashboard host: {component.errors()}")
        root_item.setParentItem(window.contentItem())

        dialog = root_item.findChild(QObject, 'saveManagementDashboard')
        self.assertIsNotNone(dialog)
        dialog.open()
        QTest.qWait(50)
        self.assertTrue(dialog.property('visible'))
        # Opening Saves starts one background Ludusavi check; let it finish before the manual check below.
        for _ in range(250):
            if not view.isJobRunning:
                break
            QTest.qWait(20)

        try:
            # 2. Check Ludusavi button (Async)
            check_btn = dialog.findChild(QObject, 'checkToolButton')
            self.assertIsNotNone(check_btn)
            view.setToolPath(sys.executable)

            spy_status = QSignalSpy(view.statusChanged)
            QMetaObject.invokeMethod(check_btn, 'clicked')
            # Wait for async completion
            for _ in range(50):
                if view.toolAvailable:
                    break
                QTest.qWait(20)

            self.assertTrue(view.toolAvailable)
            self.assertIn('Ready', view.toolStatus)

            # 3. Explicit Manifest Binding
            dialog.setProperty('selectedGameId', 'game-hades-1')
            dialog.setProperty('selectedGameTitle', 'Hades (Steam)')
            dialog.setProperty('selectedGameSource', 'Steam')

            binding_input = dialog.findChild(QObject, 'manifestBindingInput')
            binding_input.setProperty('text', 'Hades')

            save_binding_btn = dialog.findChild(QObject, 'saveBindingButton')
            QMetaObject.invokeMethod(save_binding_btn, 'clicked')
            QTest.qWait(50)

            self.assertEqual(view.getManifestBinding('game-hades-1'), 'Hades')

            # 4. Preview Backup (Async)
            preview_backup_btn = dialog.findChild(QObject, 'previewBackupButton')
            preview_completed = QSignalSpy(view.operationCompleted)
            QMetaObject.invokeMethod(preview_backup_btn, 'clicked')
            def preview_finished():
                return any(preview_completed.at(i)[0] == 'backup_preview'
                           for i in range(preview_completed.count()))
            for _ in range(250):
                if preview_finished():
                    break
                QTest.qWait(20)

            self.assertTrue(preview_finished(),
                            f"Backup preview did not complete; busy={view.isJobRunning}, result={view.lastJobResult!r}")
            self.assertTrue(view.lastJobResult.get('success'))
            self.assertTrue(view.lastJobResult.get('preview'))

            # 5. Preview Restore with Conflict Detection (Async)
            preview_restore_btn = dialog.findChild(QObject, 'previewRestoreButton')
            QMetaObject.invokeMethod(preview_restore_btn, 'clicked')
            for _ in range(50):
                if view.pendingRestorePlan.get('request_id'):
                    break
                QTest.qWait(20)

            self.assertTrue(view.hasConflicts)
            self.assertEqual(view.conflictCount, 1)

            # Check that plan box is visible
            plan_box = dialog.findChild(QObject, 'restorePlanBox')
            self.assertTrue(plan_box.property('visible'))

            # 6. Acknowledge Conflicts & Apply Restore (Async)
            ack_check = dialog.findChild(QObject, 'acknowledgeConflictsCheck')
            self.assertIsNotNone(ack_check)
            ack_check.setProperty('checked', True)

            apply_restore_btn = dialog.findChild(QObject, 'applyRestoreButton')
            QMetaObject.invokeMethod(apply_restore_btn, 'clicked')
            for _ in range(50):
                if view.lastJobResult.get('job_type') == 'restore':
                    break
                QTest.qWait(20)

            self.assertTrue(view.lastJobResult.get('success'))
            self.assertEqual(view.lastJobResult.get('job_type'), 'restore')
            self.assertEqual(view.pendingRestorePlan, {})
            self.assertFalse(dialog.findChild(QObject, 'acknowledgeConflictsCheck').property('checked'),
                             'Overwrite acknowledgement must not survive a different restore plan')

            # 7. Profiles UI Control Test
            profile_input = dialog.findChild(QObject, 'newProfileNameInput')
            self.assertIsNotNone(profile_input)
            profile_input.setProperty('text', 'Test Profile')
            add_prof_btn = dialog.findChild(QObject, 'addProfileButton')
            self.assertIsNotNone(add_prof_btn)
            QMetaObject.invokeMethod(add_prof_btn, 'clicked')
            QTest.qWait(50)
            self.assertEqual(len(view.profiles), 1)
            self.assertEqual(view.profiles[0]['name'], 'Test Profile')

            # 8. Leaving the embedded page never cancels an active save job
            view._active_job = True
            view.jobStateChanged.emit()
            QTest.qWait(20)
            dialog.close()
            QTest.qWait(20)
            self.assertFalse(dialog.property('visible'))
            self.assertTrue(view.isJobRunning, 'Navigation must not cancel an active save job')
            view._active_job = False
            view.jobStateChanged.emit()
            QTest.qWait(20)

        finally:
            view.shutdown()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
