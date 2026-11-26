"""Menu overview aggregation controller for Waystone desktop.

Provides high-level aggregate summary cards for Linux Connections, ROM Library,
and Native Store/Emulation Catalogs to enable top-level category exploration
before drilling down into flat per-game lists.

Design Invariants:
- Pure QObject: Never performs filesystem walks, process spawns, network fetches,
  or live database writes.
- Full Dataset Inspection: Aggregates directly over complete underlying data stores
  (e.g. ConnectionView._records, LaunchView._rom_records, NativeProviderView._items/_db_summary/_retro)
  rather than currently paged or filtered view rows.
- Filter Independence: User filtering or pagination in detail views never alters
  overview aggregate counts.
- Privacy & Clean Summaries: Top-level card summaries NEVER contain individual game
  titles, ROM filenames, or raw product IDs.
- Truth in Status: Count None (null in QML) means unavailable, not fake 0.
  Distinguishes between cached presence, owned licenses, and local installations.
- Exact Key Contract:
  * connections.key: exact provider key (e.g. 'steam', 'heroic-epic', 'xodus', 'lutris-battlenet')
  * roms.key: exact platform string (e.g. 'Nintendo GameCube', 'Sony PlayStation 2')
  * catalogs.key: store catalog key, 'control', or 'emulation:<exact platform>'
"""
from __future__ import annotations

from collections import Counter, defaultdict
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from PySide6.QtCore import QObject, Property, Signal, Slot

try:
    from private_addons import build_addon_catalog_cards as _build_addon_catalog_cards
except ImportError:
    _build_addon_catalog_cards = None

logger = logging.getLogger("waystone.menu_overview")

# Recognized provider display labels matching ConnectionView conventions
PROVIDER_LABELS: Mapping[str, str] = {
    "steam": "Steam (Linux native)",
    "heroic-epic": "Heroic / Epic Games Store",
    "xodus": "Xodus / Xbox Games",
    "lutris-battlenet": "Lutris / Battle.net client",
}

PLATFORM_LABELS: Mapping[str, str] = {
    "snes": "Super Nintendo (SNES)",
    "nes": "Nintendo Entertainment System",
    "n64": "Nintendo 64",
    "gamecube": "Nintendo GameCube",
    "wii": "Nintendo Wii",
    "switch": "Nintendo Switch",
    "gb": "Game Boy",
    "gbc": "Game Boy Color",
    "gba": "Game Boy Advance",
    "nds": "Nintendo DS",
    "3ds": "Nintendo 3DS",
    "psx": "Sony PlayStation 1",
    "ps2": "Sony PlayStation 2",
    "ps3": "Sony PlayStation 3",
    "psp": "PlayStation Portable",
    "megadrive": "Sega Genesis / Mega Drive",
    "saturn": "Sega Saturn",
    "dreamcast": "Sega Dreamcast",
}


class MenuOverviewView(QObject):
    """Aggregate overview model providing structured summary cards for navigation."""

    changed = Signal()

    def __init__(
        self,
        connection_view: Optional[QObject] = None,
        launch_view: Optional[QObject] = None,
        native_view: Optional[QObject] = None,
        emulation_view: Optional[QObject] = None,
        control_adapter: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._connection_view = connection_view
        self._launch_view = launch_view
        self._native_view = native_view
        self._emulation_view = emulation_view
        self._control_adapter = control_adapter

        self._connections_cards: List[Dict[str, Any]] = []
        self._roms_cards: List[Dict[str, Any]] = []
        self._catalogs_cards: List[Dict[str, Any]] = []

        self.refresh()

    # --- QML Exposed Properties ---

    @Property("QVariantList", notify=changed)
    def connections(self) -> List[Dict[str, Any]]:
        """List of provider cards for native Linux connections."""
        return [dict(c) for c in self._connections_cards]

    @Property("QVariantList", notify=changed)
    def roms(self) -> List[Dict[str, Any]]:
        """List of platform cards for the ROM library."""
        return [dict(c) for c in self._roms_cards]

    @Property("QVariantList", notify=changed)
    def catalogs(self) -> List[Dict[str, Any]]:
        """List of catalog cards for store catalog, Emulation platforms, and Control."""
        return [dict(c) for c in self._catalogs_cards]

    @Property(int, notify=changed)
    def totalConnectionsCount(self) -> int:
        return sum(c["count"] for c in self._connections_cards if c.get("count") is not None)

    @Property(int, notify=changed)
    def totalRomsCount(self) -> int:
        return sum(c["count"] for c in self._roms_cards if c.get("count") is not None)

    @Property(int, notify=changed)
    def totalCatalogsCount(self) -> int:
        return sum(c["count"] for c in self._catalogs_cards if c.get("count") is not None)

    # --- View Injection / Updates ---

    def setConnectionView(self, view: Optional[QObject]) -> None:
        self._connection_view = view
        self.refresh()

    def setLaunchView(self, view: Optional[QObject]) -> None:
        self._launch_view = view
        self.refresh()

    def setNativeView(self, view: Optional[QObject]) -> None:
        self._native_view = view
        self.refresh()

    def setEmulationView(self, view: Optional[QObject]) -> None:
        self._emulation_view = view
        self.refresh()

    def setControlAdapter(self, adapter: Optional[Any]) -> None:
        self._control_adapter = adapter
        self.refresh()

    # --- Aggregation Logic ---

    @Slot()
    def refresh(self) -> None:
        """Re-aggregates all cards from injected views without mutating them."""
        self._build_connections_cards()
        self._build_roms_cards()
        self._build_catalogs_cards()
        self.changed.emit()

    def _build_connections_cards(self) -> None:
        cards: List[Dict[str, Any]] = []
        if not self._connection_view:
            self._connections_cards = []
            return

        # Read complete validated records, never paged or filtered UI rows
        records = getattr(self._connection_view, "_records", None)
        if records is None:
            records = getattr(self._connection_view, "records", None)

        is_avail = getattr(self._connection_view, "available", True)
        if records is None or not is_avail:
            cards.append({
                "key": "none",
                "label": "Native Connections",
                "count": None,  # None means unavailable, not fake 0
                "summary": "Observation snapshot not loaded. Supply an explicit inventory snapshot.",
            })
            self._connections_cards = cards
            return

        if not records:
            cards.append({
                "key": "none",
                "label": "Native Connections",
                "count": 0,
                "summary": "Snapshot loaded with 0 native records observed.",
            })
            self._connections_cards = cards
            return

        # Group records by provider key
        by_provider: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in records:
            p = r.get("provider", "unknown")
            by_provider[p].append(r)

        for provider_key in sorted(by_provider.keys()):
            group = by_provider[provider_key]
            count = len(group)
            label = PROVIDER_LABELS.get(provider_key, provider_key.replace("-", " ").title())

            # Breakdowns
            installed_count = sum(1 for r in group if r.get("state") == "installed")
            matched_count = sum(1 for r in group if r.get("join_state") == "matched")
            unmatched_count = sum(1 for r in group if r.get("join_state") == "unmatched")
            ambiguous_count = sum(1 for r in group if r.get("join_state") == "ambiguous")

            # Client-only presence detection (e.g. Battle.net)
            is_client_only = (provider_key == "lutris-battlenet") or all(
                r.get("launch_intent", {}).get("execution_supported") is False for r in group
            )

            parts = []
            if is_client_only and provider_key == "lutris-battlenet":
                parts.append("Client-only integration (no direct game installs joined)")
            else:
                parts.append(f"{installed_count} installed locally" if installed_count else "0 local installs detected")

            join_parts = []
            if matched_count:
                join_parts.append(f"{matched_count} matched")
            if unmatched_count:
                join_parts.append(f"{unmatched_count} unmatched")
            if ambiguous_count:
                join_parts.append(f"{ambiguous_count} ambiguous")

            if join_parts:
                parts.append("joins: " + ", ".join(join_parts))

            parts.append("declaration & directory check only; payload unverified")

            summary = " • ".join(parts)
            cards.append({
                "key": provider_key,  # Exact provider key e.g. 'steam'
                "label": label,
                "count": count,
                "summary": summary,
            })

        self._connections_cards = cards

    def _build_roms_cards(self) -> None:
        cards: List[Dict[str, Any]] = []
        if not self._launch_view:
            self._roms_cards = []
            return

        # Prioritize full in-memory dataclass collection to bypass the 20k display cap on romRecords
        rom_records = getattr(self._launch_view, "_rom_records", None)
        if rom_records is None:
            rom_records = getattr(self._launch_view, "romRecords", None)

        if rom_records is None:
            cards.append({
                "key": "none",
                "label": "ROM Library",
                "count": None,  # None means unavailable, not fake 0
                "summary": "ROM inventory not initialized.",
            })
            self._roms_cards = cards
            return

        if not rom_records:
            cards.append({
                "key": "none",
                "label": "ROM Library",
                "count": 0,
                "summary": "0 cached ROM records in memory. Add ROM roots and run an explicit scan.",
            })
            self._roms_cards = cards
            return

        # Group by exact platform string from record
        by_platform: Dict[str, List[Any]] = defaultdict(list)
        for r in rom_records:
            if isinstance(r, dict):
                plat = r.get("platform") or "Unknown Platform"
            else:
                plat = getattr(r, "platform", "Unknown Platform") or "Unknown Platform"
            by_platform[plat].append(r)

        for platform_key in sorted(by_platform.keys(), key=lambda k: str(k).lower()):
            group = by_platform[platform_key]
            count = len(group)
            # Display label: format recognized short codes or keep exact string
            label = PLATFORM_LABELS.get(str(platform_key).lower(), str(platform_key))

            launchable_count = 0
            configured_profiles: Set[str] = set()

            for r in group:
                if isinstance(r, dict):
                    if r.get("launchable"):
                        launchable_count += 1
                    emu = r.get("emulator_name")
                    if emu:
                        configured_profiles.add(str(emu).strip())
                else:
                    if getattr(r, "launchable", False):
                        launchable_count += 1
                    emu = getattr(r, "emulator_name", None)
                    if emu:
                        configured_profiles.add(str(emu).strip())

            parts = [f"{count} local ROM files"]
            if launchable_count:
                parts.append(f"{launchable_count} launchable")
            else:
                parts.append("0 configured to launch")

            # Do not falsely infer installed emulator on system; report configured profiles
            if configured_profiles:
                p_list = ", ".join(sorted(configured_profiles)[:3])
                if len(configured_profiles) > 3:
                    p_list += f" +{len(configured_profiles) - 3}"
                parts.append(f"configured profiles: {p_list}")
            else:
                parts.append("no profile assigned")

            summary = " • ".join(parts)
            cards.append({
                "key": platform_key,  # Exact platform string e.g. 'Nintendo GameCube'
                "label": label,
                "count": count,
                "summary": summary,
            })

        self._roms_cards = sorted(cards, key=lambda c: c["label"].casefold())

    def _build_catalogs_cards(self) -> None:
        cards: List[Dict[str, Any]] = []
        if not self._native_view and not self._control_adapter:
            self._catalogs_cards = []
            return

        # 1. Optional Add-on Store Catalog Card(s)
        if self._native_view and _build_addon_catalog_cards is not None:
            cards.extend(_build_addon_catalog_cards(self._native_view))

        # 2. Control Operations Adapter Card (key: 'control')
        if self._control_adapter is not None:
            is_active = getattr(self._control_adapter, "is_active", True)
            cards.append({
                "key": "control",
                "label": "Control & Operations API",
                "count": 1 if is_active else 0,
                "summary": "Remote control and operational lifecycle adapter connected",
            })

        # 3. Emulation Platforms Catalog Cards (key: 'emulation:<exact platform>')
        if self._native_view:
            db_summary = getattr(self._native_view, "_db_summary", None)
            retro_items = getattr(self._native_view, "_retro", None)

            # Prioritize platform_counts: {platform: int} from database summary if available
            platform_counts: Optional[Mapping[str, int]] = None
            if isinstance(db_summary, dict) and "platform_counts" in db_summary and isinstance(db_summary["platform_counts"], dict):
                platform_counts = db_summary["platform_counts"]

            if platform_counts:
                for plat in sorted(platform_counts.keys(), key=lambda k: str(k).lower()):
                    c_val = platform_counts[plat]
                    cards.append({
                        "key": f"emulation:{plat}",
                        "label": PLATFORM_LABELS.get(str(plat).lower(), str(plat)),
                        "count": c_val,
                        "summary": (
                            f"{c_val} catalog variants • database completeness and live service availability unknown"
                        ),
                    })
            elif retro_items is not None and len(retro_items) > 0:
                # Fallback: group whole _retro cache by platform
                by_retro_plat: Dict[str, int] = Counter()
                for item in retro_items:
                    if isinstance(item, dict):
                        p = item.get("platform") or "General Emulation"
                    else:
                        p = getattr(item, "platform", "General Emulation") or "General Emulation"
                    by_retro_plat[p] += 1

                for plat in sorted(by_retro_plat.keys(), key=lambda k: str(k).lower()):
                    c_val = by_retro_plat[plat]
                    cards.append({
                        "key": f"emulation:{plat}",
                        "label": PLATFORM_LABELS.get(str(plat).lower(), str(plat)),
                        "count": c_val,
                        "summary": (
                            f"{c_val} imported variants from library export • "
                            "completeness and live service availability unknown"
                        ),
                    })
            elif isinstance(db_summary, dict) and "platforms" in db_summary:
                # Fallback: list platforms from db_summary without fake 0 counts
                for plat in sorted(db_summary["platforms"], key=lambda k: str(k).lower()):
                    cards.append({
                        "key": f"emulation:{plat}",
                        "label": PLATFORM_LABELS.get(str(plat).lower(), str(plat)),
                        "count": None,  # None means variant count unavailable, not fake 0
                        "summary": "Platform supported in database schema • variant count unavailable",
                    })

        self._catalogs_cards = cards
