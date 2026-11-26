"""Offscreen Qt + real SQLite + fake tracker/spawn; zero live process reads."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from PySide6.QtGui import QGuiApplication
from launch_view import LaunchView
from native_services.game_process_tracker import TrackedGameSession, ProcessIdentity
from native_services.launch_profiles import LaunchProfile
from native_services.play_session_store import PlaySessionStore


class FixtureTracker:
    def __init__(self):
        self.sessions = {}
        self.fail = False
    def capture_prelaunch_pids(self):
        return frozenset({1})
    def start_tracking(self, target_id, provider, **kwargs):
        sid = f'session-{len(self.sessions)}'
        record = TrackedGameSession(sid, target_id, provider, kwargs['target_type'], 'launching', launcher_pid=42)
        self.sessions[sid] = record
        return record
    def get_session(self, sid):
        return self.sessions.get(sid)
    def poll_session(self, sid):
        if self.fail:
            raise OSError('fixture uncertainty')
        return self.sessions[sid]


class PlaytimeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 0
        self.store = PlaySessionStore(self.root / 'play.sqlite3', monotonic=lambda: self.now, wall_clock=lambda: 100)
        self.addCleanup(self.store.close)
        self.tracker = FixtureTracker()
        self.spawns = []
        self.views = []
        self.confident = True
        self.view = self.make_view(play_session_store=self.store, playtime_verifier=lambda _: self.confident)
        self.details = {'provider': 'steam', 'product_id': '123', 'canonical_game_id': 'canonical-1'}
        self.view.setTarget('variant-1', self.details)

    def make_view(self, **kwargs):
        exe = self.root / 'fixture-exe'
        exe.write_text('not executable code')
        exe.chmod(0o700)
        def spawn(argv, **kw):
            self.spawns.append(argv)
            return SimpleNamespace(pid=42)
        view = LaunchView(self.root / ('profiles-' + str(len(self.views))), spawn_fn=spawn,
                          process_tracker=self.tracker, **kwargs)
        self.views.append(view)
        self.addCleanup(lambda view=view: view.closePlaytimeStore())
        view._profile_store.create_profile(LaunchProfile('test', 'Steam fixture', 'native_game', 'steam', str(exe), ('{game_id}',)))
        view._profile_store.default_associations['steam'] = 'test'
        view._profile_store.save()
        return view

    def start(self):
        self.assertEqual(self.view.play()['status'], 'handed_off')
        sid = self.view._tracking_sessions[self.view._tracking_key()]
        return self.tracker.sessions[sid]

    def poll(self, record, now, state='running', pid=7, starttime=100):
        self.now = now
        record.state = state
        record.tracked_processes = {pid: ProcessIdentity(pid, starttime, 'game')} if state == 'running' else {}
        self.view.pollGameState()

    def test_full_lifecycle_and_terminal_observation_once(self):
        changes = []
        self.view.playtimeChanged.connect(lambda: changes.append(True))
        record = self.start()
        self.assertEqual(self.view.selectedPlaytimeTotals['native_seconds'], 0)
        self.poll(record, 10)
        self.poll(record, 20)
        self.assertEqual(self.view.selectedPlaytimeTotals['live_seconds'], 10)
        self.assertEqual(self.view.playtimeStatus, 'tracking')
        self.poll(record, 25, 'closed')
        self.view.pollGameState()
        totals = self.view.selectedPlaytimeTotals
        self.assertEqual((totals['native_seconds'], totals['completed_sessions'], totals['live_sessions']), (10, 1, 0))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM play_events').fetchone()[0], 4)
        self.assertGreaterEqual(len(changes), 4)
        self.assertFalse(self.view._tracking_timer.isActive())
        self.assertEqual(len(self.spawns), 1)

    def test_selection_change_keeps_frozen_session_identity(self):
        record = self.start()
        self.poll(record, 10)
        self.view.setTarget('other', {'provider': 'steam', 'canonical_game_id': 'canonical-2'})
        self.poll(record, 20)
        self.assertEqual(self.view.selectedPlaytimeTotals['native_seconds'], 0)
        self.assertEqual(self.store.totals('canonical-1')['native_seconds'], 10)
        self.view.setTarget('variant-1', self.details)
        self.assertEqual(self.view.selectedPlaytimeTotals['native_seconds'], 10)

    def test_unknown_poll_exception_breaks_interval_and_keeps_timer(self):
        record = self.start()
        self.poll(record, 10)
        self.tracker.fail = True
        self.now = 15
        self.view.pollGameState()
        self.assertEqual(self.view.playtimeStatus, 'blocked')
        self.assertEqual(self.view.gameState, 'unknown')
        self.assertTrue(self.view._tracking_timer.isActive())
        self.tracker.fail = False
        self.poll(record, 20)
        self.poll(record, 30)
        self.assertEqual(self.store.totals('canonical-1')['native_seconds'], 10)

    def test_no_verifier_exposes_blocker_and_credits_nothing(self):
        self.view._playtime_verifier = None
        record = self.start()
        self.poll(record, 10)
        self.poll(record, 20)
        self.assertEqual(self.view.playtimeStatus, 'blocked')
        self.assertIn('confidence', self.view.playtimeError)
        self.assertEqual(self.view.selectedPlaytimeTotals['native_seconds'], 0)
        self.assertEqual(self.view.selectedPlaytimeTotals['live_sessions'], 0)

    def test_explicit_confidence_failure_breaks_continuity(self):
        record = self.start()
        self.poll(record, 10)
        self.confident = False
        self.poll(record, 15)
        self.confident = True
        self.poll(record, 20)
        self.poll(record, 30)
        self.assertEqual(self.view.selectedPlaytimeTotals['native_seconds'], 10)

    def test_launcher_only_pid_not_credited(self):
        record = self.start()
        self.poll(record, 10, pid=42)
        self.poll(record, 20, pid=42)
        self.poll(record, 30, 'closed')
        self.assertEqual(self.view.selectedPlaytimeTotals['native_seconds'], 0)
        self.assertEqual(self.view.selectedPlaytimeTotals['completed_sessions'], 0)

    def test_failed_start_no_completed_session(self):
        record = self.start()
        self.poll(record, 30, 'failed')
        self.assertEqual(self.view.selectedPlaytimeTotals['completed_sessions'], 0)
        self.assertEqual(self.view.selectedPlaytimeTotals['native_seconds'], 0)

    def test_imported_statistics_remain_separate(self):
        self.store.set_imported_totals('canonical-1', source='playnite', seconds=500)
        record = self.start()
        self.poll(record, 10)
        self.poll(record, 20)
        totals = self.view.selectedPlaytimeTotals
        self.assertEqual(totals['native_seconds'], 10)
        self.assertEqual(totals['imported_totals'][0]['seconds'], 500)

    def test_disabled_default_does_not_open_database(self):
        view = self.make_view()
        self.assertIsNone(view._playtime_store)
        self.assertEqual(view.playtimeStatus, 'disabled')
        self.assertEqual(view.selectedPlaytimeTotals, {})

    def test_explicit_path_owned_store_shutdown_preserves_unfinished_session(self):
        path = self.root / 'explicit.sqlite3'
        view = self.make_view(play_session_path=path, playtime_verifier=lambda _: True)
        view.setTarget('123', self.details)
        view.play()
        view.closePlaytimeStore()
        self.assertTrue(path.exists())
        reopened = PlaySessionStore(path)
        try:
            self.assertEqual(reopened.session('session-0')['state'], 'interrupted')
        finally:
            reopened.close()

    def test_injected_store_not_closed_by_view(self):
        self.view.closePlaytimeStore()
        self.assertEqual(self.store.totals('canonical-1')['native_seconds'], 0)

    def test_store_failure_is_not_game_state_failure(self):
        record = self.start()
        original = self.store.poll
        self.store.poll = lambda *a, **kw: (_ for _ in ()).throw(OSError('fixture db error'))
        self.poll(record, 10)
        self.assertEqual(self.view.gameState, 'running')
        self.assertEqual(self.view.playtimeStatus, 'error')
        self.store.poll = original


if __name__ == '__main__':
    unittest.main()
