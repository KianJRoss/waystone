"""Big Screen console mode state and service aggregation bridge."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot


def _default_settings_path() -> Path:
    env_path = os.environ.get("WAYSTONE_SETTINGS_PATH", "").strip()
    if env_path:
        return Path(env_path)
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "waystone" / "settings.json"


SYSTEM_CATALOG: dict[str, dict[str, Any]] = {
    "n64": {"name": "Nintendo 64", "maker": "Nintendo · 1996", "hue": 205, "default_emu": "simple64"},
    "gamecube": {"name": "GameCube", "maker": "Nintendo · 2001", "hue": 255, "default_emu": "Dolphin"},
    "3ds": {"name": "Nintendo 3DS", "maker": "Nintendo · 2011", "hue": 355, "default_emu": "Azahar"},
    "wii": {"name": "Wii", "maker": "Nintendo · 2006", "hue": 195, "default_emu": "Dolphin"},
    "gba": {"name": "Game Boy Advance", "maker": "Nintendo · 2001", "hue": 265, "default_emu": "mGBA"},
    "switch": {"name": "Switch", "maker": "Nintendo · 2017", "hue": 350, "default_emu": "Ryujinx"},
    "nds": {"name": "Nintendo DS", "maker": "Nintendo · 2004", "hue": 215, "default_emu": "melonDS"},
    "gb": {"name": "Game Boy", "maker": "Nintendo · 1989", "hue": 120, "default_emu": "mGBA"},
    "gbc": {"name": "Game Boy Color", "maker": "Nintendo · 1998", "hue": 280, "default_emu": "mGBA"},
    "wiiu": {"name": "Wii U", "maker": "Nintendo · 2012", "hue": 200, "default_emu": "Cemu"},
    "megadrive": {"name": "Mega Drive", "maker": "Sega · 1988", "hue": 220, "default_emu": "Genesis Plus GX"},
    "genesis": {"name": "Mega Drive", "maker": "Sega · 1988", "hue": 220, "default_emu": "Genesis Plus GX"},
    "nes": {"name": "NES", "maker": "Nintendo · 1983", "hue": 10, "default_emu": "Mesen"},
    "snes": {"name": "Super Nintendo", "maker": "Nintendo · 1990", "hue": 240, "default_emu": "snes9x"},
    "switch2": {"name": "Switch 2", "maker": "Nintendo · 2025", "hue": 5, "default_emu": "Ryujinx"},
    "ps1": {"name": "PlayStation", "maker": "Sony · 1994", "hue": 225, "default_emu": "DuckStation"},
    "psx": {"name": "PlayStation", "maker": "Sony · 1994", "hue": 225, "default_emu": "DuckStation"},
    "ps2": {"name": "PlayStation 2", "maker": "Sony · 2000", "hue": 230, "default_emu": "PCSX2"},
    "ps3": {"name": "PlayStation 3", "maker": "Sony · 2006", "hue": 235, "default_emu": "RPCS3"},
    "ps4": {"name": "PlayStation 4", "maker": "Sony · 2013", "hue": 220, "default_emu": "shadPS4"},
    "psp": {"name": "PSP", "maker": "Sony · 2004", "hue": 210, "default_emu": "PPSSPP"},
    "dreamcast": {"name": "Dreamcast", "maker": "Sega · 1998", "hue": 20, "default_emu": "Flycast"},
    "saturn": {"name": "Sega Saturn", "maker": "Sega · 1994", "hue": 215, "default_emu": "Mednafen"},
    "xbox": {"name": "Xbox", "maker": "Microsoft · 2001", "hue": 115, "default_emu": "xemu"},
    "xbox360": {"name": "Xbox 360", "maker": "Microsoft · 2005", "hue": 110, "default_emu": "Xenia"},
    "nintendo_multi": {"name": "Nintendo (Mixed Folder)", "maker": "Needs sorting", "hue": 35, "default_emu": "Multiple"},
    "unknown": {"name": "Unidentified ROMs", "maker": "Needs sorting", "hue": 30, "default_emu": "Unassigned"},
}

VIRTUAL_PAD_LABELS: dict[str, str] = {
    "": "Default type",
    "xbox360": "Xbox 360",
    "xboxone": "Xbox One",
    "xboxseries": "Xbox Series",
    "dualshock4": "DualShock 4",
    "dualsense": "DualSense",
    "passthrough": "Off · real controller",
}

# '' first: the profile follows the default pad type
VIRTUAL_PAD_ORDER: list[str] = ["", "passthrough", "xbox360", "xboxone", "xboxseries", "dualshock4", "dualsense"]

GYRO_LABELS: dict[str, str] = {
    "off": "Off · use right stick",
    "aim": "On · gyro aim",
    "flick_stick": "On · flick stick",
    "steering": "On · steering",
}

GYRO_ORDER: list[str] = ["off", "aim", "steering"]

def clean_rom_title(file_name: str) -> tuple[str, str]:
    """Return (clean_title, region_or_tags) from a ROM file name."""
    raw = str(file_name or "").strip()
    if not raw:
        return ("Unknown ROM", "")
    stem = Path(raw).stem
    tags: list[str] = []
    for match in re.finditer(r"\(([^)]+)\)|\[([^\]]+)\]", stem):
        tag = (match.group(1) or match.group(2) or "").strip()
        if tag and tag != "!":
            tags.append(tag)
    cleaned = re.sub(r"\s*(?:\([^)]*\)|\[[^\]]*\])\s*", " ", stem).strip()
    cleaned = re.sub(r"\s+-\s+", ": ", cleaned, count=1)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return (cleaned or stem, " · ".join(tags[:2]))


def _normalize_title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(title or "").casefold())


class BigScreenView(QObject):
    settingsChanged = Signal()
    pinnedChanged = Signal()
    systemsChanged = Signal()
    controllerChanged = Signal()
    socialChanged = Signal()
    gameMetaChanged = Signal()

    def __init__(
        self,
        settings_path: Path | str | None = None,
        persist_settings: bool = True,
        launch_view: Any = None,
        emulation_view: Any = None,
        library_model: Any = None,
        overlay_library: Any = None,
        unified_controller: Any = None,
        controller_profiles: Any = None,
        controller_commands: Any = None,
        xodus_social: Any = None,
        platform_social: Any = None,
        game_progress: Any = None,
        mod_management: Any = None,
        ui_sounds: Any = None,
        assistant_preferences: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._persist_settings = bool(persist_settings)
        self._settings_path = Path(settings_path) if settings_path is not None else _default_settings_path()
        self._launch_view = launch_view
        self._emulation_view = emulation_view
        self._library_model = library_model
        self._overlay_library = overlay_library
        self._unified_controller = unified_controller
        self._controller_profiles = controller_profiles
        self._controller_commands = controller_commands
        self._xodus_social = xodus_social
        self._platform_social = platform_social
        self._game_progress = game_progress
        self._mod_management = mod_management
        self._ui_sounds = ui_sounds

        self._start_in_big_screen = False
        # The assistant animation settings have ONE home (AssistantAnimationSettings, also behind desktop
        # Settings > General); Big Screen only reads and changes them through it.
        self._assistant = assistant_preferences
        if self._assistant is not None and hasattr(self._assistant, "changed"):
            self._assistant.changed.connect(self.settingsChanged)

        # Local fallback controller overrides when studio daemon is not running
        self._gyro_override: str = ""
        self._rumble_override: bool | None = None

        self._systems_cache: list[dict[str, Any]] | None = None
        self._roms_by_system: dict[str, list[dict[str, Any]]] = {}
        self._needs_nintendo = 0
        self._needs_unknown = 0
        self._total_roms = 0
        self._meta_revision = 0

        if self._persist_settings or settings_path is not None or os.environ.get("WAYSTONE_SETTINGS_PATH"):
            self._load_settings()

        self._connect_signals()
        self.gameMetaChanged.connect(self._bump_meta_revision)
        self.controllerChanged.connect(self._bump_meta_revision)

    def _bump_meta_revision(self) -> None:
        self._meta_revision += 1

    @Property(int, notify=gameMetaChanged)
    def metaRevision(self) -> int:
        return self._meta_revision

    def _connect_signals(self) -> None:
        if self._launch_view is not None:
            for sig_name in ("romsChanged", "changed", "profilesChanged", "monitorsChanged"):
                sig = getattr(self._launch_view, sig_name, None)
                if sig is not None:
                    try:
                        sig.connect(self._on_roms_or_launch_changed)
                    except Exception:
                        pass
        if self._emulation_view is not None:
            sig = getattr(self._emulation_view, "changed", None)
            if sig is not None:
                try:
                    sig.connect(self._on_roms_or_launch_changed)
                except Exception:
                    pass
        if self._unified_controller is not None:
            for sig_name in ("devicesChanged", "profileDataChanged", "stateChanged", "changed"):
                sig = getattr(self._unified_controller, sig_name, None)
                if sig is not None:
                    try:
                        sig.connect(self.controllerChanged.emit)
                    except Exception:
                        pass
        if self._controller_profiles is not None:
            sig = getattr(self._controller_profiles, "changed", None)
            if sig is not None:
                try:
                    sig.connect(self.controllerChanged.emit)
                    sig.connect(self.gameMetaChanged.emit)
                except Exception:
                    pass
        for social in (self._xodus_social, self._platform_social):
            if social is not None:
                sig = getattr(social, "changed", None)
                if sig is not None:
                    try:
                        sig.connect(self.socialChanged.emit)
                    except Exception:
                        pass
        for meta_obj in (self._game_progress, self._mod_management):
            if meta_obj is not None:
                sig = getattr(meta_obj, "changed", None)
                if sig is not None:
                    try:
                        sig.connect(self.gameMetaChanged.emit)
                    except Exception:
                        pass

    def _on_roms_or_launch_changed(self) -> None:
        self._systems_cache = None
        self._roms_by_system.clear()
        self.systemsChanged.emit()
        self.gameMetaChanged.emit()

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        try:
            if not self._settings_path.exists():
                return
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            ui = data.get("ui")
            if not isinstance(ui, dict):
                return
            if isinstance(ui.get("start_in_big_screen"), bool):
                self._start_in_big_screen = ui["start_in_big_screen"]
        except Exception:
            pass

    def _save_settings(self) -> None:
        if not (self._persist_settings or os.environ.get("WAYSTONE_SETTINGS_PATH")):
            return
        try:
            data: dict[str, Any] = {}
            if self._settings_path.exists():
                try:
                    loaded = json.loads(self._settings_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}
            ui = data.get("ui")
            if not isinstance(ui, dict):
                ui = {}
                data["ui"] = ui
            ui["start_in_big_screen"] = bool(self._start_in_big_screen)
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._settings_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self._settings_path)
        except Exception:
            pass

    @Property(bool, notify=settingsChanged)
    def startInBigScreen(self) -> bool:
        return self._start_in_big_screen

    @Slot(bool)
    def setStartInBigScreen(self, enabled: bool) -> None:
        target = bool(enabled)
        if self._start_in_big_screen != target:
            self._start_in_big_screen = target
            self._save_settings()
            self.settingsChanged.emit()

    @Slot(result=bool)
    def toggleStartInBigScreen(self) -> bool:
        self.setStartInBigScreen(not self._start_in_big_screen)
        return self._start_in_big_screen

    PACE_STEPS = (0.5, 1.0, 1.8, 3.0)

    @Property(bool, notify=settingsChanged)
    def assistantAnimation(self) -> bool:
        return bool(getattr(self._assistant, "showAnimation", True))

    @Property(str, notify=settingsChanged)
    def assistantSpeed(self) -> str:
        return f"{float(getattr(self._assistant, 'pace', 1.0)):.1f}×"

    @Property(str, notify=settingsChanged)
    def assistantSummary(self) -> str:
        return f"On · {self.assistantSpeed}" if self.assistantAnimation else "Off"

    @Slot(result=str)
    def cycleAssistantSetting(self) -> str:
        """Off -> 0.5x -> 1x -> 1.8x -> 3x -> Off, through the shared settings."""
        prefs = self._assistant
        if prefs is None:
            return self.assistantSummary
        if not prefs.showAnimation:
            prefs.setShowAnimation(True)
            prefs.setPace(self.PACE_STEPS[0])
        else:
            pace = float(prefs.pace)
            later = [p for p in self.PACE_STEPS if p > pace + 0.05]
            if later:
                prefs.setPace(later[0])
            else:
                prefs.setShowAnimation(False)
        self.settingsChanged.emit()
        return self.assistantSummary

    # ------------------------------------------------------------------
    # Pin / Favorite support
    # ------------------------------------------------------------------
    # Pinned = the library's Favourite flag (one home: the overlay's Pinned list and desktop favourites use it too).
    @Property("QStringList", notify=pinnedChanged)
    def pinnedIds(self) -> list[str]:
        bridge = self._overlay_library
        try:
            return [str(g.get("id")) for g in (getattr(bridge, "pinnedGames", None) or []) if g.get("id")]
        except Exception:
            return []

    def _favorite_values(self, gid: str):
        model = self._library_model
        if model is None or not getattr(model, "organizerEnabled", False):
            return None
        try:
            values = model.organizerEditValues(gid)
            return values if isinstance(values, dict) else None
        except Exception:
            return None

    @Slot(str, result=bool)
    def isPinned(self, game_id: str) -> bool:
        values = self._favorite_values(str(game_id or "").strip())
        return bool(values and values.get("favorite") is True)

    @Slot(str, result=bool)
    def togglePin(self, game_id: str) -> bool:
        """Toggle the library Favourite through the organizer (with its undo). Returns the new pinned state."""
        gid = str(game_id or "").strip()
        values = self._favorite_values(gid) if gid else None
        if values is None:
            return False
        pinned = not (values.get("favorite") is True)
        try:
            self._library_model.previewOrganizer(gid, values.get("tags", "[]"), pinned)
            self._library_model.applyOrganizer(gid)
        except Exception:
            return not pinned
        self.pinnedChanged.emit()
        return pinned

    # ------------------------------------------------------------------
    # Systems & ROMs
    # ------------------------------------------------------------------
    def _ensure_systems_cache(self) -> None:
        if self._systems_cache is not None:
            return
        records: list[dict[str, Any]] = []
        if self._launch_view is not None:
            try:
                raw = self._launch_view.romRecords
                if isinstance(raw, list):
                    records = [r for r in raw if isinstance(r, dict)]
            except Exception:
                records = []

        # Map emulator display names from emulation_view if available
        emu_by_platform: dict[str, str] = {}
        if self._emulation_view is not None:
            try:
                for sys_row in getattr(self._emulation_view, "systems", []) or []:
                    if isinstance(sys_row, dict):
                        plat = str(sys_row.get("platform") or "").strip().lower()
                        emulators = sys_row.get("emulators")
                        if plat and isinstance(emulators, list) and emulators:
                            first = emulators[0]
                            if isinstance(first, dict) and first.get("name"):
                                emu_by_platform[plat] = str(first["name"])
                            elif isinstance(first, str) and first:
                                emu_by_platform[plat] = first
            except Exception:
                pass

        # Title lookup in library_model for covers / playtime
        catalog_by_norm_title: dict[str, dict[str, Any]] = {}
        if self._library_model is not None:
            try:
                for g in getattr(self._library_model, "games", []) or []:
                    if isinstance(g, dict) and g.get("title"):
                        key = _normalize_title_key(g["title"])
                        if key and key not in catalog_by_norm_title:
                            catalog_by_norm_title[key] = g
            except Exception:
                pass

        grouped: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            plat = str(rec.get("platform") or "unknown").strip().lower() or "unknown"
            grouped.setdefault(plat, []).append(rec)

        self._needs_nintendo = len(grouped.get("nintendo_multi", []))
        self._needs_unknown = len(grouped.get("unknown", []))
        self._total_roms = len(records)
        self._roms_by_system = {}

        default_ctrl = "Default"
        if self._controller_profiles is not None:
            try:
                default_ctrl = str(self._controller_profiles.defaultProfile or "Default")
            except Exception:
                default_ctrl = "Default"

        systems_out: list[dict[str, Any]] = []
        for plat, plat_records in grouped.items():
            meta = SYSTEM_CATALOG.get(
                plat,
                {
                    "name": plat.upper() if len(plat) <= 4 else plat.replace("_", " ").title(),
                    "maker": "Emulated System",
                    "hue": 210,
                    "default_emu": "RetroArch",
                },
            )
            # Determine primary emulator for this platform
            emu_counts: dict[str, int] = {}
            launchable_count = 0
            for r in plat_records:
                if r.get("launchable"):
                    launchable_count += 1
                ename = str(r.get("emulator_name") or "").strip()
                if ename:
                    emu_counts[ename] = emu_counts.get(ename, 0) + 1
            if emu_counts:
                primary_emu = max(emu_counts.items(), key=lambda kv: kv[1])[0]
            else:
                primary_emu = emu_by_platform.get(plat, meta["default_emu"])

            rom_rows: list[dict[str, Any]] = []
            for r in plat_records:
                rom_id = str(r.get("rom_id") or "")
                fname = str(r.get("file_name") or "")
                clean_title, region = clean_rom_title(fname)
                norm = _normalize_title_key(clean_title)
                cat_match = catalog_by_norm_title.get(norm)
                cover_url = ""
                last_played = "Never"
                playtime = "0m"
                if cat_match is not None and self._library_model is not None:
                    try:
                        urls = self._library_model._urls(cat_match)
                        cover_url = str(urls.get("coverUrl") or "")
                    except Exception:
                        pass
                ctrl_prof = default_ctrl
                if self._controller_profiles is not None and rom_id:
                    try:
                        assigned = self._controller_profiles.assignmentForGame(rom_id)
                        if assigned:
                            ctrl_prof = str(assigned)
                    except Exception:
                        pass
                rom_emu = str(r.get("emulator_name") or "").strip() or primary_emu
                launchable = bool(r.get("launchable", False))
                reason = str(r.get("unlaunchable_reason") or "").strip()
                rom_rows.append(
                    {
                        "rom_id": rom_id,
                        "title": clean_title,
                        "region": region,
                        "file_name": fname,
                        "file_path": str(r.get("file_path") or ""),
                        "platform": plat,
                        "systemName": meta["name"],
                        "maker": meta["maker"],
                        "hue": meta["hue"],
                        "emu": rom_emu,
                        "launchable": launchable,
                        "status": "Ready to play" if launchable else (reason or "Emulator profile ready"),
                        "lastPlayed": last_played,
                        "playtime": playtime,
                        "coverUrl": cover_url,
                        "controllerProfile": ctrl_prof,
                    }
                )
            rom_rows.sort(key=lambda item: (item["title"].casefold(), item["file_name"].casefold()))
            self._roms_by_system[plat] = rom_rows

            if plat in ("nintendo_multi", "unknown"):
                continue

            systems_out.append(
                {
                    "id": plat,
                    "name": meta["name"],
                    "maker": meta["maker"],
                    "count": len(plat_records),
                    "launchableCount": launchable_count,
                    "hue": meta["hue"],
                    "emu": primary_emu,
                }
            )

        systems_out.sort(key=lambda s: (-int(s["count"]), str(s["name"]).casefold()))
        self._systems_cache = systems_out

    @Property("QVariantList", notify=systemsChanged)
    def systems(self) -> list[dict[str, Any]]:
        self._ensure_systems_cache()
        return list(self._systems_cache or [])

    @Property(int, notify=systemsChanged)
    def totalRomCount(self) -> int:
        self._ensure_systems_cache()
        return self._total_roms

    @Property(int, notify=systemsChanged)
    def needsSortingNintendoCount(self) -> int:
        self._ensure_systems_cache()
        return self._needs_nintendo

    @Property(int, notify=systemsChanged)
    def needsSortingUnknownCount(self) -> int:
        self._ensure_systems_cache()
        return self._needs_unknown

    @Property(str, notify=systemsChanged)
    def needsSortingNotice(self) -> str:
        self._ensure_systems_cache()
        parts: list[str] = []
        if self._needs_nintendo > 0:
            parts.append(f"{self._needs_nintendo} Nintendo files in a mixed folder")
        if self._needs_unknown > 0:
            parts.append(f"{self._needs_unknown} unidentified ROMs")
        if not parts:
            return ""
        return "Also found: " + " and ".join(parts) + " — needs sorting"

    @Slot(str, result="QVariantList")
    def romsForSystem(self, system_id: str) -> list[dict[str, Any]]:
        self._ensure_systems_cache()
        key = str(system_id or "").strip().lower()
        return list(self._roms_by_system.get(key, []))

    @Slot(str, result="QVariantMap")
    def systemInfo(self, system_id: str) -> dict[str, Any]:
        self._ensure_systems_cache()
        key = str(system_id or "").strip().lower()
        for sys_item in self._systems_cache or []:
            if sys_item["id"] == key:
                return dict(sys_item)
        meta = SYSTEM_CATALOG.get(
            key,
            {"name": key.title() or "System", "maker": "Emulated System", "hue": 210, "default_emu": "RetroArch"},
        )
        roms = self._roms_by_system.get(key, [])
        return {
            "id": key,
            "name": meta["name"],
            "maker": meta["maker"],
            "count": len(roms),
            "launchableCount": sum(1 for r in roms if r.get("launchable")),
            "hue": meta["hue"],
            "emu": roms[0]["emu"] if roms else meta["default_emu"],
        }

    @Slot(str, result=bool)
    def playRom(self, rom_id: str) -> bool:
        rid = str(rom_id or "").strip()
        if not rid or self._launch_view is None:
            return False
        try:
            self._launch_view.selectRom(rid)
            res = self._launch_view.play()
            if isinstance(res, dict):
                return res.get("status") not in ("error", "failed")
            return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Friends & Party aggregation
    # ------------------------------------------------------------------
    @Property("QVariantList", notify=socialChanged)
    def friendsList(self) -> list[dict[str, Any]]:
        combined: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _initials(name: str) -> str:
            parts = [p for p in re.split(r"[\s_\-]+", name.strip()) if p]
            if len(parts) >= 2:
                return (parts[0][0] + parts[1][0]).upper()
            return (name.strip()[:2] or "FR").upper()

        if self._xodus_social is not None:
            try:
                for f in getattr(self._xodus_social, "friends", []) or []:
                    if not isinstance(f, dict):
                        continue
                    name = str(f.get("display_name") or f.get("gamertag") or f.get("name") or f.get("id") or "").strip()
                    if not name:
                        continue
                    key = name.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    presence = str(f.get("presence") or f.get("status") or f.get("state") or "Online").strip()
                    game = str(f.get("title_name") or f.get("game") or f.get("activity") or "").strip()
                    online = presence.casefold() not in ("offline", "invisible", "disconnected", "")
                    status_line = f"Playing {game} · Xbox" if game else (presence or "Online")
                    combined.append(
                        {
                            "id": str(f.get("xuid") or f.get("id") or name),
                            "name": name,
                            "avatar": _initials(name),
                            "status": status_line,
                            "online": online,
                            "inGame": bool(game),
                            "joinable": bool(f.get("joinable") or game or "party" in status_line.casefold()),
                            "source": "Xbox",
                        }
                    )
            except Exception:
                pass

        if self._platform_social is not None:
            try:
                for f in getattr(self._platform_social, "friends", []) or []:
                    if not isinstance(f, dict):
                        continue
                    name = str(f.get("persona_name") or f.get("display_name") or f.get("name") or f.get("friend_id") or "").strip()
                    if not name:
                        continue
                    key = name.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    state = str(f.get("state") or f.get("presence_state") or f.get("status") or "").strip()
                    game = str(f.get("game_title") or f.get("current_game") or f.get("game") or "").strip()
                    provider = str(f.get("provider") or "Steam").strip().title()
                    online = state.casefold() not in ("offline", "0", "") or bool(game)
                    if game:
                        status_line = f"Playing {game} · {provider}"
                    elif online:
                        status_line = f"Online · {provider}"
                    else:
                        status_line = str(f.get("last_seen") or "Offline")
                    combined.append(
                        {
                            "id": str(f.get("friend_id") or f.get("id") or name),
                            "name": name,
                            "avatar": _initials(name),
                            "status": status_line,
                            "online": online,
                            "inGame": bool(game),
                            "joinable": bool(game),
                            "source": provider,
                        }
                    )
            except Exception:
                pass

        combined.sort(key=lambda item: (0 if item["online"] else 1, 0 if item["inGame"] else 1, item["name"].casefold()))
        return combined

    @Property(int, notify=socialChanged)
    def onlineFriendsCount(self) -> int:
        return sum(1 for f in self.friendsList if f.get("online"))

    # ------------------------------------------------------------------
    # Controller helpers
    # ------------------------------------------------------------------
    def _first_device(self) -> dict[str, Any]:
        if self._unified_controller is not None:
            try:
                devs = getattr(self._unified_controller, "devices", []) or []
                if isinstance(devs, list) and devs and isinstance(devs[0], dict):
                    return devs[0]
            except Exception:
                pass
        return {}

    def _profile_data(self) -> dict[str, Any]:
        if self._unified_controller is not None:
            try:
                data = getattr(self._unified_controller, "profileData", {}) or {}
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    @Property(str, notify=controllerChanged)
    def controllerName(self) -> str:
        dev = self._first_device()
        name = str(dev.get("name") or dev.get("display_name") or "").strip()
        return name or "Xbox Wireless Controller"

    @Property(str, notify=controllerChanged)
    def controllerBatteryText(self) -> str:
        dev = self._first_device()
        bat = dev.get("battery") or dev.get("battery_percent") or dev.get("battery_level")
        if isinstance(bat, (int, float)) and bat > 0:
            return f"{int(bat)}%"
        if isinstance(bat, str) and bat.strip():
            return bat.strip()
        return "78%" if dev else "Ready"

    @Property(str, notify=controllerChanged)
    def controllerStatusText(self) -> str:
        dev = self._first_device()
        if dev:
            transport = str(dev.get("transport") or dev.get("bus") or "Bluetooth").strip()
            bat = self.controllerBatteryText
            return f"Connected · {transport} · battery {bat}"
        return f"Active profile · {self.activeProfileName} · {self.virtualPadLabel}"

    @Property(str, notify=controllerChanged)
    def activeProfileName(self) -> str:
        if self._controller_profiles is not None:
            try:
                prof = str(self._controller_profiles.defaultProfile or "").strip()
                if prof:
                    return prof
            except Exception:
                pass
        if self._unified_controller is not None:
            try:
                prof = str(getattr(self._unified_controller, "activeProfileName", "") or "").strip()
                if prof:
                    return prof
            except Exception:
                pass
        return "Default"

    @Property(str, notify=controllerChanged)
    def virtualPadLabel(self) -> str:
        """Pad type of the controller profile being edited (the one the Sticks row edits too)."""
        uc = self._unified_controller
        driver = str(getattr(uc, "padType", "") or "") if uc is not None else ""
        profile = str(getattr(uc, "editingProfile", "") or "") if uc is not None else ""
        label = VIRTUAL_PAD_LABELS.get(driver, driver or "Default type")
        return f"{label} · {profile}" if profile else label

    @Property(str, notify=controllerChanged)
    def gyroLabel(self) -> str:
        preset = self._gyro_override
        if not preset:
            data = self._profile_data()
            motion = data.get("motion") if isinstance(data.get("motion"), dict) else {}
            preset = str(motion.get("preset") or "off").strip().lower()
        return GYRO_LABELS.get(preset, "Off · use right stick")

    @Property(str, notify=controllerChanged)
    def rumbleLabel(self) -> str:
        if self._rumble_override is not None:
            return "On" if self._rumble_override else "Off"
        data = self._profile_data()
        rumble = data.get("rumble") if isinstance(data.get("rumble"), dict) else {}
        enabled = rumble.get("enabled", True)
        return "On" if enabled is not False else "Off"

    @Slot(result=str)
    def cycleDefaultProfile(self) -> str:
        names: list[str] = []
        if self._controller_profiles is not None:
            try:
                for p in self._controller_profiles.profiles or []:
                    if isinstance(p, str) and p.strip():
                        names.append(p.strip())
                    elif isinstance(p, dict) and p.get("name"):
                        names.append(str(p["name"]))
            except Exception:
                pass
        if not names and self._unified_controller is not None:
            try:
                for n in getattr(self._unified_controller, "profileNames", []) or []:
                    if isinstance(n, str) and n.strip():
                        names.append(n.strip())
            except Exception:
                pass
        if not names:
            names = ["Default"]
        curr = self.activeProfileName
        try:
            idx = names.index(curr)
            nxt = names[(idx + 1) % len(names)]
        except ValueError:
            nxt = names[0]
        if self._controller_profiles is not None:
            try:
                if hasattr(self._controller_profiles, "setDefaultProfile"):
                    self._controller_profiles.setDefaultProfile(nxt)
                elif hasattr(self._controller_profiles, "setDefault"):
                    self._controller_profiles.setDefault(nxt)
            except Exception:
                pass
        self.controllerChanged.emit()
        return self.activeProfileName

    @Slot(result=str)
    def cycleVirtualPadType(self) -> str:
        """Next pad type for the profile being edited, saved through the engine (read back)."""
        uc = self._unified_controller
        if uc is None or not getattr(uc, "available", False) or getattr(uc, "busy", False) \
                or not getattr(uc, "editingProfile", ""):
            return "Controller engine unavailable"
        curr = str(getattr(uc, "padType", "") or "")
        idx = VIRTUAL_PAD_ORDER.index(curr) if curr in VIRTUAL_PAD_ORDER else 0
        nxt = VIRTUAL_PAD_ORDER[(idx + 1) % len(VIRTUAL_PAD_ORDER)]
        uc.setPadType(nxt)
        self.controllerChanged.emit()
        return f"{VIRTUAL_PAD_LABELS.get(nxt, nxt)} · {uc.editingProfile}"

    @Slot(result=str)
    def cycleGyroPreset(self) -> str:
        curr_key = self._gyro_override
        if not curr_key:
            data = self._profile_data()
            motion = data.get("motion") if isinstance(data.get("motion"), dict) else {}
            curr_key = str(motion.get("preset") or "off").strip().lower()
        try:
            idx = GYRO_ORDER.index(curr_key)
            nxt_key = GYRO_ORDER[(idx + 1) % len(GYRO_ORDER)]
        except ValueError:
            nxt_key = GYRO_ORDER[0]
        self._gyro_override = nxt_key
        self.controllerChanged.emit()
        return self.gyroLabel

    @Slot(result=str)
    def toggleRumble(self) -> str:
        curr = self.rumbleLabel == "On"
        self._rumble_override = not curr
        self.controllerChanged.emit()
        return self.rumbleLabel

    @Slot(str, result=str)
    def gameProfileLabel(self, game_id: str) -> str:
        gid = str(game_id or "").strip()
        if gid and self._controller_profiles is not None:
            try:
                assigned = str(self._controller_profiles.assignmentForGame(gid) or "").strip()
                if assigned:
                    return assigned
            except Exception:
                pass
        return "Use default"

    @Slot(str, result=str)
    def cycleGameProfile(self, game_id: str) -> str:
        gid = str(game_id or "").strip()
        if not gid or self._controller_profiles is None:
            return "Use default"
        choices: list[str] = [""]
        try:
            for p in self._controller_profiles.profiles or []:
                if isinstance(p, str) and p.strip():
                    choices.append(p.strip())
                elif isinstance(p, dict) and p.get("name"):
                    choices.append(str(p["name"]))
        except Exception:
            pass
        curr = ""
        try:
            curr = str(self._controller_profiles.assignmentForGame(gid) or "").strip()
        except Exception:
            curr = ""
        try:
            idx = choices.index(curr)
            nxt = choices[(idx + 1) % len(choices)]
        except ValueError:
            nxt = ""
        try:
            self._controller_profiles.assignGame(gid, nxt)
        except Exception:
            pass
        self.gameMetaChanged.emit()
        self.controllerChanged.emit()
        return self.gameProfileLabel(gid)

    # ------------------------------------------------------------------
    # Monitor & Per-Game Metadata helpers
    # ------------------------------------------------------------------
    @Slot(result=str)
    def defaultMonitorLabel(self) -> str:
        if self._launch_view is None:
            return "Wherever the game opens"
        try:
            lbl = getattr(self._launch_view, "defaultMonitorLabel", "")
            if isinstance(lbl, str) and lbl.strip():
                return lbl.strip()
            default_id = str(self._launch_view.defaultMonitor or "").strip()
            if not default_id:
                return "Wherever the game opens"
            for c in self._launch_view.monitorChoices or []:
                if isinstance(c, dict):
                    key = str(c.get("description") or c.get("id") or "").strip()
                    if key == default_id:
                        return str(c.get("label") or default_id)
            return default_id
        except Exception:
            return "Wherever the game opens"

    @Slot(result=str)
    def cycleDefaultMonitor(self) -> str:
        if self._launch_view is None:
            return "Wherever the game opens"
        try:
            choices: list[str] = [""]
            for c in self._launch_view.monitorChoices or []:
                if isinstance(c, dict):
                    cid = str(c.get("description") or c.get("id") or "").strip()
                    if cid and cid != "default" and cid not in choices:
                        choices.append(cid)
            curr = str(self._launch_view.defaultMonitor or "").strip()
            try:
                idx = choices.index(curr)
                nxt = choices[(idx + 1) % len(choices)]
            except ValueError:
                nxt = ""
            self._launch_view.setDefaultMonitor(nxt)
        except Exception:
            pass
        self.gameMetaChanged.emit()
        return self.defaultMonitorLabel()

    @Slot(str, result=str)
    def gameMonitorLabel(self, game_id: str) -> str:
        gid = str(game_id or "").strip()
        if not gid or self._launch_view is None:
            return "Use default"
        try:
            choice_id = str(self._launch_view.gameMonitorFor(gid) or "").strip()
            if choice_id in ("", "default"):
                return "Use default"
            if choice_id == "any":
                return "Wherever the game opens"
            for c in self._launch_view.monitorChoices or []:
                if isinstance(c, dict):
                    key = str(c.get("description") or c.get("id") or "").strip()
                    if key == choice_id:
                        return str(c.get("label") or choice_id)
            return choice_id
        except Exception:
            return "Use default"

    @Slot(str, result=str)
    def cycleGameMonitor(self, game_id: str) -> str:
        gid = str(game_id or "").strip()
        if not gid or self._launch_view is None:
            return "Use default"
        try:
            choices: list[str] = [""]
            for c in self._launch_view.monitorChoices or []:
                if isinstance(c, dict):
                    cid = str(c.get("description") or c.get("id") or "").strip()
                    if cid and cid != "default" and cid not in choices:
                        choices.append(cid)
            if "any" not in choices:
                choices.append("any")
            curr = str(self._launch_view.gameMonitorFor(gid) or "").strip()
            if curr == "default":
                curr = ""
            try:
                idx = choices.index(curr)
                nxt = choices[(idx + 1) % len(choices)]
            except ValueError:
                nxt = choices[0]
            self._launch_view.setGameMonitorFor(gid, nxt)
        except Exception:
            pass
        self.gameMetaChanged.emit()
        return self.gameMonitorLabel(gid)

    @Slot(str, result=str)
    def gameAchievementsLabel(self, game_id: str) -> str:
        gid = str(game_id or "").strip()
        if not gid:
            return "None tracked"
        if self._game_progress is not None:
            try:
                if getattr(self._game_progress, "gameKey", "") == gid:
                    sources = getattr(self._game_progress, "achievementSources", []) or []
                    if isinstance(sources, list) and sources:
                        first = sources[0]
                        if isinstance(first, dict):
                            unlocked = first.get("unlocked_count") or first.get("unlocked")
                            total = first.get("total_count") or first.get("total")
                            if unlocked is not None and total:
                                return f"{unlocked} / {total}"
                            items = first.get("items") or first.get("achievements")
                            if isinstance(items, list) and items:
                                u_cnt = sum(1 for x in items if isinstance(x, dict) and x.get("unlocked"))
                                return f"{u_cnt} / {len(items)}"
            except Exception:
                pass
        if self._platform_social is not None:
            try:
                achs = getattr(self._platform_social, "achievements", []) or []
                matching = [a for a in achs if isinstance(a, dict) and str(a.get("game_id") or "") == gid]
                if matching:
                    u_cnt = sum(1 for a in matching if a.get("unlocked"))
                    return f"{u_cnt} / {len(matching)}"
            except Exception:
                pass
        return "None tracked"

    @Slot(str, result=str)
    def gameModsLabel(self, game_id: str) -> str:
        gid = str(game_id or "").strip()
        if not gid or self._mod_management is None:
            return "None"
        try:
            profiles = getattr(self._mod_management, "profiles", []) or []
            matching = [
                p for p in profiles
                if isinstance(p, dict) and str(p.get("game_id") or p.get("canonical_game_id") or "") == gid
            ]
            if matching:
                first = matching[0]
                mods = first.get("mods")
                if isinstance(mods, list):
                    enabled = sum(1 for m in mods if isinstance(m, dict) and m.get("enabled", True))
                    return f"{enabled} enabled"
                return str(first.get("display_name") or first.get("name") or f"{len(matching)} profile(s)")
        except Exception:
            pass
        return "None"
