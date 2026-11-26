"""Cross-provider achievement normalization, store, and aggregation service.

Reuses native_services.achievement_sources beneath service boundary.
Enforces explicit cross-provider identity:
    (canonical_game_id, provider, provider_product_id, account_id, achievement_id)
No same-title unlock merging across Steam, Xbox, GOG, or RetroAchievements.
Zero network calls, zero fabricated unlocks. Offline fixtures only.
Empty achievements never report 100% completion.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from native_services.achievement_sources import (
    normalize_steam as _source_normalize_steam,
    normalize_imported as _source_normalize_imported,
    account_overview as _source_account_overview,
)

VALID_PROVIDERS = frozenset({'steam', 'xbox', 'gog', 'retroachievements'})
STATUS_SYNCED = 'synced'
STATUS_AVAILABLE = 'available'
STATUS_IMPORTED = 'imported'
STATUS_NEEDS_AUTH = 'needs_auth'
STATUS_UNAVAILABLE = 'unavailable'


def _safe_str(val: Any, max_len: int = 512) -> str:
    if not isinstance(val, str):
        return ''
    cleaned = ''.join(c for c in val.strip() if c.isprintable())
    return cleaned[:max_len]


@dataclass
class AchievementRecord:
    canonical_game_id: str
    provider: str
    provider_product_id: str
    account_id: str
    achievement_id: str
    name: str
    description: str
    unlocked: bool
    unlocked_at: Optional[str]
    hidden: bool = False
    icon_url: Optional[str] = None
    icon_gray_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GameAchievementObservation:
    canonical_game_id: str
    provider: str
    provider_product_id: str
    account_id: str
    status: str
    status_message: str
    total_count: int
    unlocked_count: int
    progress_percent: float
    achievements: List[AchievementRecord]
    last_synced_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['achievements'] = [a.to_dict() for a in self.achievements]
        return d


def normalize_steam_payload(
    canonical_game_id: str,
    app_id: str,
    account_id: str,
    schema_json: Optional[Dict[str, Any]],
    player_stats_json: Optional[Dict[str, Any]],
    last_synced_at: Optional[str] = None,
) -> GameAchievementObservation:
    """Normalize Steam ISteamUserStats payload reusing native_services.achievement_sources."""
    feed = _source_normalize_steam(
        player_stats_json or {},
        game_id=canonical_game_id,
        app_id=app_id,
        account_id=account_id,
        schema=schema_json,
    )
    # Extract schema details if present
    schema_map = {}
    if schema_json:
        for s_item in schema_json.get('game', {}).get('availableGameStats', {}).get('achievements', []):
            if isinstance(s_item, dict) and 'name' in s_item:
                schema_map[str(s_item['name'])] = s_item

    records = []
    for item in feed.get('achievements', []):
        ach_id = item['achievement_id']
        s_item = schema_map.get(ach_id, {})
        records.append(AchievementRecord(
            canonical_game_id=feed['canonical_game_id'],
            provider=feed['provider'],
            provider_product_id=feed['provider_game_id'],
            account_id=feed['account_id'],
            achievement_id=ach_id,
            name=item.get('label', ach_id),
            description=s_item.get('description', ''),
            unlocked=bool(item.get('unlocked')),
            unlocked_at=item.get('unlocked_at'),
            hidden=bool(s_item.get('hidden', 0)),
            icon_url=s_item.get('icon'),
            icon_gray_url=s_item.get('icongray'),
        ))

    total = feed.get('total_count') or 0
    unlocked = feed.get('unlocked_count') or 0
    percent = float(feed.get('completion_percent') or 0.0)

    status = STATUS_SYNCED if feed['status'] == STATUS_AVAILABLE else feed['status']
    err_msg = (player_stats_json or {}).get('playerstats', {}).get('error', '')
    if status == STATUS_SYNCED:
        msg = 'Synchronized with Steam stats'
    else:
        msg = err_msg if err_msg else 'Steam user stats unavailable'

    return GameAchievementObservation(
        canonical_game_id=feed['canonical_game_id'],
        provider=feed['provider'],
        provider_product_id=feed['provider_game_id'],
        account_id=feed['account_id'],
        status=status,
        status_message=msg,
        total_count=total,
        unlocked_count=unlocked,
        progress_percent=percent,
        achievements=records,
        last_synced_at=last_synced_at,
    )


def import_generic_observation(
    canonical_game_id: str,
    provider: str,
    product_id: str,
    account_id: str,
    raw_items: List[Dict[str, Any]],
    status: str = STATUS_IMPORTED,
    status_message: str = '',
    last_synced_at: Optional[str] = None,
) -> GameAchievementObservation:
    """Normalize generic observations reusing native_services.achievement_sources."""
    p_name = _safe_str(provider, 32).casefold()
    if p_name not in VALID_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Must be one of {sorted(VALID_PROVIDERS)}")

    # Adapt raw_items to achievement_sources input contract
    adapted_records = []
    for item in raw_items:
        ach_id = item.get('achievement_id') or item.get('id') or item.get('apiname')
        label = item.get('label') or item.get('name') or item.get('title') or ach_id
        unlocked = item.get('unlocked')
        if unlocked is not None and not isinstance(unlocked, bool):
            unlocked = bool(unlocked)
        adapted_records.append({
            'achievement_id': ach_id,
            'label': label,
            'unlocked': unlocked,
            'unlocked_at': item.get('unlocked_at'),
            'provider': p_name,
            'account_id': account_id,
            'provider_game_id': product_id,
        })

    feed = _source_normalize_imported(
        p_name,
        adapted_records,
        game_id=canonical_game_id,
        product_id=product_id,
        account_id=account_id,
    )

    records = []
    for item in feed.get('achievements', []):
        records.append(AchievementRecord(
            canonical_game_id=feed['canonical_game_id'],
            provider=feed['provider'],
            provider_product_id=feed['provider_game_id'],
            account_id=feed['account_id'],
            achievement_id=item['achievement_id'],
            name=item.get('label', item['achievement_id']),
            description=item.get('description', ''),
            unlocked=bool(item.get('unlocked')),
            unlocked_at=item.get('unlocked_at'),
        ))

    total = feed.get('observed_count', 0)
    unlocked = feed.get('observed_unlocked_count', 0)
    percent = round((unlocked / total) * 100.0, 1) if total > 0 else 0.0

    return GameAchievementObservation(
        canonical_game_id=feed['canonical_game_id'],
        provider=feed['provider'],
        provider_product_id=feed['provider_game_id'],
        account_id=feed['account_id'],
        status=status,
        status_message=status_message or 'Imported observation recorded',
        total_count=total,
        unlocked_count=unlocked,
        progress_percent=percent,
        achievements=records,
        last_synced_at=last_synced_at,
    )


class AchievementStore:
    """Registry for cross-provider game achievement observations reusing account_overview."""

    def __init__(self, storage_path: Optional[Path | str] = None) -> None:
        self.storage_path = Path(storage_path) if storage_path else None
        self._observations: Dict[Tuple[str, str, str], GameAchievementObservation] = {}
        if self.storage_path and self.storage_path.exists():
            self.load()

    def record_observation(self, obs: GameAchievementObservation) -> None:
        key = (obs.canonical_game_id, obs.provider, obs.account_id)
        self._observations[key] = copy.deepcopy(obs)
        if self.storage_path:
            self.save()

    def get_observation(
        self,
        canonical_game_id: str,
        provider: str,
        account_id: str = '',
    ) -> Optional[GameAchievementObservation]:
        key = (canonical_game_id, provider.casefold(), account_id)
        if key in self._observations:
            return copy.deepcopy(self._observations[key])
        if not account_id:
            matches = [
                obs for (c_id, prov, _), obs in self._observations.items()
                if c_id == canonical_game_id and prov == provider.casefold()
            ]
            if matches:
                return copy.deepcopy(matches[0])
        return None

    def get_observations_for_game(self, canonical_game_id: str) -> List[GameAchievementObservation]:
        return [
            copy.deepcopy(obs)
            for (c_id, _, _), obs in self._observations.items()
            if c_id == canonical_game_id
        ]

    def list_accounts(self) -> List[Dict[str, Any]]:
        # Convert observations to feeds for coordinator account_overview reuse
        feeds = []
        status_by_account = {}
        for (_, provider, account_id), obs in self._observations.items():
            pair = (provider, account_id)
            if pair not in status_by_account or obs.status == STATUS_NEEDS_AUTH:
                status_by_account[pair] = obs.status
            feeds.append({
                'provider': provider,
                'account_id': account_id,
                'provider_game_id': obs.provider_product_id,
                'canonical_game_id': obs.canonical_game_id,
                'status': obs.status,
                'unlocked_count': obs.unlocked_count if obs.total_count > 0 else None,
                'total_count': obs.total_count if obs.total_count > 0 else None,
            })

        overviews = _source_account_overview(feeds) if feeds else []
        result = []
        for ov in overviews:
            pair = (ov['provider'], ov['account_id'])
            tot = sum(
                o.total_count for (_, p, a), o in self._observations.items()
                if p == ov['provider'] and a == ov['account_id']
            )
            unl = ov['known_unlocked']
            result.append({
                'provider': ov['provider'],
                'account_id': ov['account_id'],
                'game_count': ov['game_count'],
                'total_achievements': tot,
                'unlocked_achievements': unl,
                'completion_percent': round((unl / tot) * 100.0, 1) if tot > 0 else 0.0,
                'status': status_by_account.get(pair, STATUS_IMPORTED),
            })
        return sorted(result, key=lambda x: (x['provider'], x['account_id']))

    def get_summary(
        self,
        provider: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        matching = []
        for (_, p, acc), obs in self._observations.items():
            if provider and p != provider.casefold():
                continue
            if account_id is not None and acc != account_id:
                continue
            matching.append(obs)

        total_games = len(matching)
        total_ach = sum(o.total_count for o in matching)
        total_unl = sum(o.unlocked_count for o in matching)
        percent = round((total_unl / total_ach) * 100.0, 1) if total_ach > 0 else 0.0

        return {
            'total_games': total_games,
            'total_achievements': total_ach,
            'total_unlocked': total_unl,
            'completion_percent': percent,
            'provider_filter': provider or 'all',
            'account_filter': account_id if account_id is not None else 'all',
        }

    def save(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = [obs.to_dict() for obs in self._observations.values()]
        self.storage_path.write_text(json.dumps(serialized, indent=2, ensure_ascii=False), encoding='utf-8')

    def load(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding='utf-8'))
            if isinstance(data, list):
                for item in data:
                    achievements = [
                        AchievementRecord(**a) for a in item.get('achievements', [])
                    ]
                    item_copy = dict(item)
                    item_copy['achievements'] = achievements
                    obs = GameAchievementObservation(**item_copy)
                    self._observations[(obs.canonical_game_id, obs.provider, obs.account_id)] = obs
        except Exception:
            pass
