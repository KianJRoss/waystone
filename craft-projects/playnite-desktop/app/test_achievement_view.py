"""Qt integration tests for AchievementView."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QSignalSpy

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from achievement_view import AchievementView
from native_services.achievements import (
    import_generic_observation,
    STATUS_SYNCED,
    STATUS_IMPORTED,
    STATUS_NEEDS_AUTH,
)


class AchievementViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(['achievement-view-tests'])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.storage_path = self.base / 'achievements.json'

    def test_default_empty_view(self):
        view = AchievementView(self.storage_path)
        self.assertEqual(view.accounts, [])
        self.assertEqual(view.summary['total_games'], 0)
        self.assertEqual(view.summary['completion_percent'], 0.0)
        self.assertEqual(view.selectedGameObservations, [])
        self.assertEqual(view.selectedGameAchievements, [])

    def test_record_observation_and_summary_signals(self):
        view = AchievementView(self.storage_path)
        spy_summary = QSignalSpy(view.summaryChanged)
        spy_accounts = QSignalSpy(view.accountsChanged)

        obs1 = import_generic_observation(
            canonical_game_id='game-1',
            provider='steam',
            product_id='100',
            account_id='user-steam',
            raw_items=[
                {'id': 'a1', 'name': 'First Step', 'unlocked': True, 'unlocked_at': '2026-01-01T00:00:00Z'},
                {'id': 'a2', 'name': 'Master', 'unlocked': False},
            ],
            status=STATUS_SYNCED,
        )

        ok = view.recordObservation(obs1.to_dict())
        self.assertTrue(ok)
        self.assertEqual(spy_summary.count(), 1)
        self.assertEqual(spy_accounts.count(), 1)

        summary = view.summary
        self.assertEqual(summary['total_games'], 1)
        self.assertEqual(summary['total_achievements'], 2)
        self.assertEqual(summary['total_unlocked'], 1)
        self.assertEqual(summary['completion_percent'], 50.0)

        accounts = view.accounts
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]['provider'], 'steam')
        self.assertEqual(accounts[0]['account_id'], 'user-steam')

    def test_game_selection_drilldown_and_cross_provider_isolation(self):
        view = AchievementView(self.storage_path)

        # Same game on Steam and Xbox
        obs_steam = import_generic_observation(
            canonical_game_id='game-portal',
            provider='steam',
            product_id='620',
            account_id='steam-user',
            raw_items=[
                {'id': 'steam_1', 'name': 'Portal Gun', 'unlocked': True},
            ],
            status=STATUS_SYNCED,
        )
        obs_xbox = import_generic_observation(
            canonical_game_id='game-portal',
            provider='xbox',
            product_id='xbox-portal',
            account_id='xbox-user',
            raw_items=[
                {'id': 'xbox_1', 'name': 'Portal Gun', 'unlocked': False},
                {'id': 'xbox_2', 'name': 'Cake', 'unlocked': False},
            ],
            status=STATUS_IMPORTED,
        )

        view.recordObservation(obs_steam.to_dict())
        view.recordObservation(obs_xbox.to_dict())

        # Select game
        spy_sel = QSignalSpy(view.selectionChanged)
        view.selectGame('game-portal')
        self.assertGreater(spy_sel.count(), 0)
        self.assertEqual(view.selectedGameId, 'game-portal')

        # Check observations list for game (should have 2 distinct observations)
        game_obs = view.selectedGameObservations
        self.assertEqual(len(game_obs), 2)

        # Check default selected observation (Steam)
        self.assertEqual(view.selectedProvider, 'steam')
        self.assertEqual(view.selectedAccountId, 'steam-user')
        self.assertEqual(len(view.selectedGameAchievements), 1)
        self.assertTrue(view.selectedGameAchievements[0]['unlocked'])
        self.assertEqual(view.selectedGameSummary['progress_percent'], 100.0)

        # Switch to Xbox observation for same game
        view.selectProviderAccount('xbox', 'xbox-user')
        self.assertEqual(view.selectedProvider, 'xbox')
        self.assertEqual(view.selectedAccountId, 'xbox-user')
        self.assertEqual(len(view.selectedGameAchievements), 2)
        self.assertFalse(view.selectedGameAchievements[0]['unlocked'])
        self.assertFalse(view.selectedGameAchievements[1]['unlocked'])
        self.assertEqual(view.selectedGameSummary['progress_percent'], 0.0)

    def test_empty_stats_and_status_reporting(self):
        view = AchievementView(self.storage_path)

        obs_empty = import_generic_observation(
            canonical_game_id='game-empty',
            provider='retroachievements',
            product_id='ra-999',
            account_id='ra-user',
            raw_items=[],
            status=STATUS_NEEDS_AUTH,
            status_message='Account authentication required',
        )
        view.recordObservation(obs_empty.to_dict())
        view.selectGame('game-empty')

        self.assertEqual(view.activeStatus, STATUS_NEEDS_AUTH)
        summary = view.selectedGameSummary
        self.assertEqual(summary['total_count'], 0)
        self.assertEqual(summary['unlocked_count'], 0)
        self.assertEqual(summary['progress_percent'], 0.0)
        self.assertNotEqual(summary['progress_percent'], 100.0)


if __name__ == '__main__':
    unittest.main()
