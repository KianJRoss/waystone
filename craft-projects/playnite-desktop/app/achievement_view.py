"""Qt presentation model and controller for cross-provider achievements.

Provides overall platform/account summary and per-game drilldown for QML.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Property, Signal, Slot

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from native_services.achievements import (
    AchievementStore,
    GameAchievementObservation,
    AchievementRecord,
    import_generic_observation,
    STATUS_UNAVAILABLE,
    STATUS_NEEDS_AUTH,
)


class AchievementView(QObject):
    """QObject wrapper exposing achievement data, platform summaries, and drilldown."""

    summaryChanged = Signal()
    accountsChanged = Signal()
    selectionChanged = Signal()
    errorChanged = Signal()

    def __init__(
        self,
        storage_path: Optional[Path | str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._store = AchievementStore(storage_path)
        self._selected_game_id = ''
        self._selected_provider = ''
        self._selected_account_id = ''
        self._provider_filter = ''
        self._error = ''

    # --- Properties ---

    @Property('QVariantMap', notify=summaryChanged)
    def summary(self) -> Dict[str, Any]:
        return self._store.get_summary(
            provider=self._provider_filter if self._provider_filter else None
        )

    @Property('QVariantList', notify=accountsChanged)
    def accounts(self) -> List[Dict[str, Any]]:
        return self._store.list_accounts()

    @Property(str, notify=selectionChanged)
    def selectedGameId(self) -> str:
        return self._selected_game_id

    @Property(str, notify=selectionChanged)
    def selectedProvider(self) -> str:
        return self._selected_provider

    @Property(str, notify=selectionChanged)
    def selectedAccountId(self) -> str:
        return self._selected_account_id

    @Property(str, notify=selectionChanged)
    def activeStatus(self) -> str:
        obs = self._get_current_observation()
        if obs:
            return obs.status
        return STATUS_UNAVAILABLE

    @Property('QVariantMap', notify=selectionChanged)
    def selectedGameSummary(self) -> Dict[str, Any]:
        obs = self._get_current_observation()
        if not obs:
            return {
                'game_id': self._selected_game_id,
                'total_count': 0,
                'unlocked_count': 0,
                'progress_percent': 0.0,
                'status': STATUS_UNAVAILABLE,
                'status_message': 'No achievement observation for game',
            }
        return {
            'game_id': obs.canonical_game_id,
            'provider': obs.provider,
            'account_id': obs.account_id,
            'total_count': obs.total_count,
            'unlocked_count': obs.unlocked_count,
            'progress_percent': obs.progress_percent,
            'status': obs.status,
            'status_message': obs.status_message,
            'last_synced_at': obs.last_synced_at or '',
        }

    @Property('QVariantList', notify=selectionChanged)
    def selectedGameObservations(self) -> List[Dict[str, Any]]:
        if not self._selected_game_id:
            return []
        obs_list = self._store.get_observations_for_game(self._selected_game_id)
        return [o.to_dict() for o in obs_list]

    @Property('QVariantList', notify=selectionChanged)
    def selectedGameAchievements(self) -> List[Dict[str, Any]]:
        obs = self._get_current_observation()
        if not obs:
            return []
        return [a.to_dict() for a in obs.achievements]

    @Property(str, notify=errorChanged)
    def error(self) -> str:
        return self._error

    # --- Helpers ---

    def _get_current_observation(self) -> Optional[GameAchievementObservation]:
        if not self._selected_game_id:
            return None
        if self._selected_provider:
            return self._store.get_observation(
                self._selected_game_id,
                self._selected_provider,
                self._selected_account_id,
            )
        # If no specific provider selected, default to first observation for game
        obs_list = self._store.get_observations_for_game(self._selected_game_id)
        return obs_list[0] if obs_list else None

    # --- Slots ---

    @Slot(str)
    def selectGame(self, game_id: str) -> None:
        self._selected_game_id = str(game_id)
        # Reset provider/account selection to match first available observation
        obs_list = self._store.get_observations_for_game(self._selected_game_id)
        if obs_list:
            self._selected_provider = obs_list[0].provider
            self._selected_account_id = obs_list[0].account_id
        else:
            self._selected_provider = ''
            self._selected_account_id = ''
        self.selectionChanged.emit()

    @Slot(str, str)
    def selectProviderAccount(self, provider: str, account_id: str) -> None:
        self._selected_provider = str(provider)
        self._selected_account_id = str(account_id)
        self.selectionChanged.emit()

    @Slot(str)
    def filterByProvider(self, provider: str) -> None:
        self._provider_filter = str(provider).strip()
        self.summaryChanged.emit()

    @Slot(str, result='QVariantMap')
    def gameSummary(self, game_id: str) -> Dict[str, Any]:
        obs_list = self._store.get_observations_for_game(str(game_id))
        total_ach = sum(o.total_count for o in obs_list)
        unlocked_ach = sum(o.unlocked_count for o in obs_list)
        percent = round((unlocked_ach / total_ach) * 100.0, 1) if total_ach > 0 else 0.0
        return {
            'game_id': game_id,
            'observation_count': len(obs_list),
            'total_achievements': total_ach,
            'unlocked_achievements': unlocked_ach,
            'completion_percent': percent,
        }

    @Slot(str, str, result='QVariantMap')
    def accountSummary(self, provider: str, account_id: str) -> Dict[str, Any]:
        return self._store.get_summary(provider=provider, account_id=account_id)

    @Slot('QVariantMap', result=bool)
    def recordObservation(self, observation_dict: Dict[str, Any]) -> bool:
        try:
            achievements = [
                AchievementRecord(**a) for a in observation_dict.get('achievements', [])
            ]
            obs_copy = dict(observation_dict)
            obs_copy['achievements'] = achievements
            obs = GameAchievementObservation(**obs_copy)
            self._store.record_observation(obs)
            self.summaryChanged.emit()
            self.accountsChanged.emit()
            if obs.canonical_game_id == self._selected_game_id:
                self.selectionChanged.emit()
            self._error = ''
            self.errorChanged.emit()
            return True
        except Exception as exc:
            self._error = str(exc)
            self.errorChanged.emit()
            return False
