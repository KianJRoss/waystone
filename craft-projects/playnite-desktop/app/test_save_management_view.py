"""Qt integration tests for SaveManagementView."""
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

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QSignalSpy, QTest

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from save_management_view import SaveManagementView


def make_mock_runner(version='ludusavi 0.24.0', delay: float = 0.0):
    executed = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess:
        if delay > 0:
            time.sleep(delay)
        executed.append(list(argv))
        if '--version' in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=version, stderr='')

        if 'backup' in argv or 'restore' in argv:
            names = argv[argv.index('--') + 1:]
            out = json.dumps({
                'overall': {'totalGames': 1, 'totalBytes': 2048, 'processedGames': 1, 'processedBytes': 2048},
                'games': {
                    name: {
                        'decision': 'Processed',
                        'files': {'/dummy/save': {'bytes': 2048}},
                    } for name in names
                }
            })
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr='')

        return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')

    runner.executed = executed
    return runner


class SaveManagementViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(['save-mgmt-view-tests'])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.config_path = self.base / 'save-view-config.json'
        (self.base / 'backups').mkdir()

    def test_default_view_missing_tool_reports_unavailable(self):
        view = SaveManagementView(self.config_path)
        view.setToolPath('/invalid/tool/path')

        self.assertFalse(view.toolAvailable)
        self.assertIn('not found', view.toolStatus)

        # Attempting backup with missing tool returns failure without running
        res = view.backupGame('game-id', 'Test Game', 'Steam')
        self.assertFalse(res['success'])
        self.assertIn('not found', res['error'])

    def test_unbound_target_refusal_across_all_operations(self):
        runner = make_mock_runner()
        view = SaveManagementView(self.config_path, runner=runner)
        view.setToolPath(sys.executable)
        self.assertTrue(view.toolAvailable)

        # 1. Single backup refused without manifest binding
        res_single = view.backupGame('unbound-1', 'Unbound Game', 'Steam')
        self.assertFalse(res_single['success'])
        self.assertIn('explicit manifest binding', res_single['error'])

        # 2. Bulk backup refused when any game lacks manifest binding
        bulk_targets = [
            {'id': 'unbound-2', 'title': 'Some Game', 'source': 'Heroic'},
        ]
        res_bulk = view.backupBulk(bulk_targets)
        self.assertFalse(res_bulk['success'])
        self.assertIn('explicit manifest binding', res_bulk['error'])

        # 3. Restore preview refused without manifest binding
        res_restore = view.previewRestore('unbound-3', 'Some Game', 'Steam')
        self.assertFalse(res_restore['success'])
        self.assertIn('explicit manifest binding', res_restore['error'])

        # 4. List backups refused without manifest binding when game_id specified
        res_list = view.listBackups('unbound-4', 'Some Game', 'Steam')
        self.assertEqual(res_list, [])
        self.assertIn('explicit manifest binding', view.errorMessage)

    def test_profiles_crud_and_signals(self):
        view = SaveManagementView(self.config_path)
        spy_profiles = QSignalSpy(view.profilesChanged)

        # Create profile
        prof = view.createProfile('cloud-profile', 'Cloud Saves', str(self.base / 'cloud'))
        self.assertEqual(prof['id'], 'cloud-profile')
        self.assertEqual(len(view.profiles), 1)
        self.assertEqual(spy_profiles.count(), 1)

        # List
        all_profiles = view.listProfiles()
        self.assertEqual(len(all_profiles), 1)
        self.assertEqual(all_profiles[0]['name'], 'Cloud Saves')

        # Delete
        deleted = view.deleteProfile('cloud-profile')
        self.assertTrue(deleted)
        self.assertEqual(len(view.profiles), 0)
        self.assertEqual(spy_profiles.count(), 2)

    def test_backup_and_bulk_backup_slots(self):
        runner = make_mock_runner()
        view = SaveManagementView(self.config_path, runner=runner)
        view.setToolPath(sys.executable)
        self.assertTrue(view.toolAvailable)

        spy_job = QSignalSpy(view.jobStateChanged)
        spy_completed = QSignalSpy(view.operationCompleted)

        # Explicit bindings
        view.setManifestBinding('g1', 'Elden Ring')
        view.setManifestBinding('g2', 'Hades')
        view.setManifestBinding('g3', 'Celeste')

        # Single backup
        res = view.backupGame('g1', 'Elden Ring', 'Steam')
        self.assertTrue(res['success'])
        self.assertEqual(res['targets'][0]['title'], 'Elden Ring')
        self.assertEqual(res['targets'][0]['id'], 'g1')
        self.assertGreater(spy_job.count(), 0)
        self.assertEqual(spy_completed.count(), 1)
        self.assertEqual(spy_completed.at(0)[0], 'backup')
        self.assertEqual(spy_completed.at(0)[1], True)

        # Bulk backup
        bulk_targets = [
            {'id': 'g2', 'title': 'Hades', 'source': 'Steam'},
            {'id': 'g3', 'title': 'Celeste', 'source': 'Heroic'},
        ]
        bulk_res = view.backupBulk(bulk_targets)
        self.assertTrue(bulk_res['success'])
        self.assertEqual(len(bulk_res['targets']), 2)
        self.assertEqual(spy_completed.count(), 2)

    def test_restore_preview_apply_and_cancel_lifecycle(self):
        runner = make_mock_runner()
        view = SaveManagementView(self.config_path, runner=runner)
        view.setToolPath(sys.executable)

        view.setManifestBinding('g1', 'Hades')

        spy_plan = QSignalSpy(view.restorePlanChanged)
        spy_completed = QSignalSpy(view.operationCompleted)

        # Preview restore
        plan = view.previewRestore('g1', 'Hades', 'Steam')
        self.assertIn('request_id', plan)
        req_id = plan['request_id']
        self.assertEqual(view.pendingRestorePlan['request_id'], req_id)
        self.assertGreater(spy_plan.count(), 0)
        self.assertEqual(spy_completed.count(), 0)  # Preview does not emit operationCompleted

        # Apply restore with valid request_id
        res = view.applyRestore(req_id)
        self.assertTrue(res['success'])
        self.assertEqual(view.pendingRestorePlan, {})
        self.assertEqual(spy_completed.count(), 1)
        self.assertEqual(spy_completed.at(0)[0], 'restore')
        self.assertEqual(spy_completed.at(0)[1], True)

        # Test cancel restore flow
        plan2 = view.previewRestore('g1', 'Hades', 'Steam')
        self.assertIn('request_id', plan2)
        self.assertTrue(view.cancelRestore())
        self.assertEqual(view.pendingRestorePlan, {})

    def test_restore_conflict_acknowledgement_in_view(self):
        existing_file = self.base / 'existing.dat'
        existing_file.write_text('old save')

        def runner(argv: list[str]) -> subprocess.CompletedProcess:
            if '--version' in argv:
                return subprocess.CompletedProcess(argv, 0, stdout='ludusavi 0.24.0', stderr='')
            out = json.dumps({
                'overall': {'totalGames': 1, 'totalBytes': 500, 'processedGames': 1, 'processedBytes': 500},
                'games': {
                    argv[-1]: {
                        'decision': 'Processed',
                        'files': {str(existing_file): {'bytes': 500}},
                    }
                }
            })
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr='')

        view = SaveManagementView(self.config_path, runner=runner)
        view.setToolPath(sys.executable)

        view.setManifestBinding('g1', 'Hades')

        plan = view.previewRestore('g1', 'Hades', 'Steam')
        self.assertTrue(plan.get('has_conflicts', False))
        req_id = plan['request_id']

        # Apply without acknowledging conflicts fails
        res_fail = view.applyRestore(req_id, False)
        self.assertFalse(res_fail['success'])
        self.assertIn('conflict', res_fail['error'].casefold())

        # Apply with acknowledge_conflicts=True succeeds (fresh plan since plan was consumed)
        plan_fresh = view.previewRestore('g1', 'Hades', 'Steam')
        req_id_fresh = plan_fresh['request_id']
        res_ok = view.applyRestore(req_id_fresh, True)
        self.assertTrue(res_ok['success'])

    def test_delayed_runner_and_safe_shutdown(self):
        """Verify shutdown cleanly waits and cancels active worker without leaking threads, crashes, or QThread.terminate."""
        runner = make_mock_runner(delay=0.2)
        view = SaveManagementView(self.config_path, runner=runner)
        view.setToolPath(sys.executable)
        view.setManifestBinding('g1', 'Hades')

        # 1. Test cancellation during startup / before dispatch
        view_shutting_down = SaveManagementView(self.config_path, runner=runner)
        view_shutting_down.shutdown()
        # Spawn after cancel/shutdown is refused
        refused = view_shutting_down.backupGameAsync('g1', 'Hades', 'Steam')
        self.assertFalse(refused)
        self.assertIn('shutting down', view_shutting_down.errorMessage)

        # 2. Test cancellation during in-flight job
        started = view.backupGameAsync('g1', 'Hades', 'Steam')
        self.assertTrue(started)
        self.assertTrue(view.isJobRunning)

        # Cooperative shutdown during in-flight execution joins worker safely
        clean = view.shutdown()
        self.assertTrue(clean)
        self.assertFalse(view.isJobRunning)
        self.assertIsNone(view._active_worker)


if __name__ == '__main__':
    unittest.main()


class RememberedToolCheckTests(unittest.TestCase):
    def test_successful_check_is_remembered_until_the_tool_changes(self):
        import json as _json, os as _os, tempfile as _tf
        from pathlib import Path as _P
        from save_management_view import SaveManagementView
        with _tf.TemporaryDirectory() as tmp:
            tool = _P(tmp) / "ludusavi"; tool.write_text("#!/bin/sh\n"); tool.chmod(0o755)
            cfg = _P(tmp) / "saves" / "config.json"
            view = SaveManagementView(cfg)
            view._manager.set_tool_path(str(tool))
            view._remember_capabilities({"available": True, "version": "0.31.0", "tool_path": str(tool)})
            again = SaveManagementView(cfg)
            self.assertTrue(again.toolAvailable)            # no subprocess ran: restored from the remembered check
            self.assertIn("0.31.0", again.toolStatus)
            tool.write_text("#!/bin/sh\necho updated\n")     # tool updated -> must check again
            self.assertFalse(SaveManagementView(cfg).toolAvailable)
