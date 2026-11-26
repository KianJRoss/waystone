"""Official emulator registry and catalog definitions.

Provides structured separation between:
1. Catalog platforms (Nintendo, PlayStation PS1-PS5, Xbox, Xbox 360, Sega),
2. Emulator upstream definitions and official CLI launcher capabilities,
3. Linux install detection across standard paths without starting processes,
4. Per-game compatibility tracking (defaulting to unknown/unverified),
5. Strict guardrails against unsupported runtimes (PS5, Linux native Xenia).

Official Upstream Repositories:
- DuckStation: https://github.com/stenzek/duckstation
- PCSX2:       https://github.com/PCSX2/pcsx2
- RPCS3:       https://github.com/RPCS3/rpcs3
- shadPS4:     https://github.com/shadps4-emu/shadPS4
- xemu:        https://github.com/xemu-project/xemu
- Xenia:       https://github.com/xenia-project/xenia
- Dolphin:     https://github.com/dolphin-emu/dolphin
- Cemu:        https://github.com/cemu-project/Cemu
- Eden:        https://github.com/eden-emulator/eden
- RetroArch:   https://github.com/libretro/RetroArch
- Ryujinx:     https://github.com/Ryubing/Ryujinx
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import os
from pathlib import Path
import shutil
from typing import Any

# Standard Linux search directories for emulator binaries
DEFAULT_SEARCH_PATHS = (
    Path.home() / '.local/bin',
    Path('/usr/bin'),
    Path('/usr/local/bin'),
    Path('/mnt/games/emulators/bin'),
    Path.home() / 'Applications',
    Path('/var/lib/flatpak/exports/bin'),
    Path.home() / '.local/share/flatpak/exports/bin',
)


@dataclass(frozen=True)
class CatalogPlatform:
    """Catalog definition for a supported gaming platform."""
    platform_id: str
    display_name: str
    manufacturer: str
    generation: int
    standard_extensions: tuple[str, ...]
    has_mature_emulation: bool
    known_emulators: tuple[str, ...]
    notes: str = ''

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['standard_extensions'] = list(self.standard_extensions)
        d['known_emulators'] = list(self.known_emulators)
        return d


@dataclass(frozen=True)
class EmulatorDefinition:
    """Official upstream emulator metadata, CLI launcher capabilities, and defaults."""
    name: str
    display_name: str
    upstream_url: str
    supported_platforms: tuple[str, ...]
    supported_extensions: tuple[str, ...]
    default_args: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    binary_candidates: tuple[str, ...] = field(default_factory=tuple)
    native_linux_support: bool = True
    requires_runtime_wrapper: bool = False
    notes: str = ''

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['supported_platforms'] = list(self.supported_platforms)
        d['supported_extensions'] = list(self.supported_extensions)
        d['default_args'] = list(self.default_args)
        return d


@dataclass(frozen=True)
class InstallStatus:
    """Detection state of an emulator on the local host."""
    emulator_name: str
    installed: bool
    executable_path: str | None
    source: str  # 'system_path', 'search_dir', 'custom', 'not_found'
    supported_platforms: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['supported_platforms'] = list(self.supported_platforms)
        return d


@dataclass(frozen=True)
class PerGameCompatibility:
    """Game compatibility report defaulting to unknown."""
    platform: str
    game_identifier: str
    status: str = 'unknown'  # 'unknown', 'playable', 'ingame', 'intro', 'untested', 'unsupported'
    notes: str = 'No upstream compatibility report; status is unverified'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =============================================================================
# Catalog Platform Definitions
# =============================================================================

CATALOG_PLATFORMS: dict[str, CatalogPlatform] = {
    # Nintendo
    'nes': CatalogPlatform(
        platform_id='nes',
        display_name='Nintendo Entertainment System',
        manufacturer='Nintendo',
        generation=3,
        standard_extensions=('.nes', '.zip', '.7z'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
    'snes': CatalogPlatform(
        platform_id='snes',
        display_name='Super Nintendo Entertainment System',
        manufacturer='Nintendo',
        generation=4,
        standard_extensions=('.sfc', '.smc', '.zip', '.7z'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
    'n64': CatalogPlatform(
        platform_id='n64',
        display_name='Nintendo 64',
        manufacturer='Nintendo',
        generation=5,
        standard_extensions=('.z64', '.v64', '.n64', '.zip'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
    'gamecube': CatalogPlatform(
        platform_id='gamecube',
        display_name='Nintendo GameCube',
        manufacturer='Nintendo',
        generation=6,
        standard_extensions=('.rvz', '.gcm', '.iso'),
        has_mature_emulation=True,
        known_emulators=('dolphin',),
    ),
    'wii': CatalogPlatform(
        platform_id='wii',
        display_name='Nintendo Wii',
        manufacturer='Nintendo',
        generation=7,
        standard_extensions=('.wbfs', '.rvz', '.iso'),
        has_mature_emulation=True,
        known_emulators=('dolphin',),
    ),
    'wiiu': CatalogPlatform(
        platform_id='wiiu',
        display_name='Nintendo Wii U',
        manufacturer='Nintendo',
        generation=8,
        standard_extensions=('.rpx', '.wux', '.wud'),
        has_mature_emulation=True,
        known_emulators=('cemu',),
    ),
    'switch': CatalogPlatform(
        platform_id='switch',
        display_name='Nintendo Switch',
        manufacturer='Nintendo',
        generation=8,
        standard_extensions=('.nsp', '.xci', '.nro'),
        has_mature_emulation=True,
        known_emulators=('lichii', 'eden', 'ryujinx'),
    ),
    'switch2': CatalogPlatform(
        platform_id='switch2',
        display_name='Nintendo Switch 2',
        manufacturer='Nintendo',
        generation=9,
        standard_extensions=('.nup',),
        has_mature_emulation=True,
        known_emulators=('lichii',),
        notes='Switch 2 preview profiles mapped via Lichii.',
    ),
    'gb': CatalogPlatform(
        platform_id='gb',
        display_name='Game Boy',
        manufacturer='Nintendo',
        generation=4,
        standard_extensions=('.gb', '.zip'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
    'gbc': CatalogPlatform(
        platform_id='gbc',
        display_name='Game Boy Color',
        manufacturer='Nintendo',
        generation=5,
        standard_extensions=('.gbc', '.zip'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
    'gba': CatalogPlatform(
        platform_id='gba',
        display_name='Game Boy Advance',
        manufacturer='Nintendo',
        generation=6,
        standard_extensions=('.gba', '.zip'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
    'nds': CatalogPlatform(
        platform_id='nds',
        display_name='Nintendo DS',
        manufacturer='Nintendo',
        generation=7,
        standard_extensions=('.nds', '.zip'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
    '3ds': CatalogPlatform(
        platform_id='3ds',
        display_name='Nintendo 3DS',
        manufacturer='Nintendo',
        generation=8,
        standard_extensions=('.3ds', '.cia'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),

    # Sony PlayStation
    'ps1': CatalogPlatform(
        platform_id='ps1',
        display_name='Sony PlayStation',
        manufacturer='Sony',
        generation=5,
        standard_extensions=('.chd', '.cue', '.iso', '.pbp', '.bin', '.img'),
        has_mature_emulation=True,
        known_emulators=('duckstation', 'retroarch'),
        notes='Also referred to as PSX. Upstream DuckStation is primary standalone.',
    ),
    'ps2': CatalogPlatform(
        platform_id='ps2',
        display_name='Sony PlayStation 2',
        manufacturer='Sony',
        generation=6,
        standard_extensions=('.iso', '.chd', '.bin', '.cso', '.gz'),
        has_mature_emulation=True,
        known_emulators=('pcsx2',),
        notes='Upstream PCSX2 Qt is official modern Linux standalone.',
    ),
    'ps3': CatalogPlatform(
        platform_id='ps3',
        display_name='Sony PlayStation 3',
        manufacturer='Sony',
        generation=7,
        standard_extensions=('.bin', '.iso', '.pkg'),
        has_mature_emulation=True,
        known_emulators=('rpcs3',),
        notes='Upstream RPCS3 launches EBOOT.BIN or disc ISO directly.',
    ),
    'ps4': CatalogPlatform(
        platform_id='ps4',
        display_name='Sony PlayStation 4',
        manufacturer='Sony',
        generation=8,
        standard_extensions=('.bin', '.elf', '.pkg'),
        has_mature_emulation=True,
        known_emulators=('shadps4',),
        notes='Upstream shadPS4 supports CUSA folders and eboot.bin/elf.',
    ),
    'ps5': CatalogPlatform(
        platform_id='ps5',
        display_name='Sony PlayStation 5',
        manufacturer='Sony',
        generation=9,
        standard_extensions=('.pkg', '.iso', '.bin', '.elf'),
        has_mature_emulation=False,
        known_emulators=(),
        notes='No mature or verified Linux PlayStation 5 emulator runtime exists. Catalog-only.',
    ),

    # Microsoft Xbox
    'xbox': CatalogPlatform(
        platform_id='xbox',
        display_name='Microsoft Xbox',
        manufacturer='Microsoft',
        generation=6,
        standard_extensions=('.iso', '.xbe'),
        has_mature_emulation=True,
        known_emulators=('xemu',),
        notes='Upstream xemu runs disc images with -dvd_path.',
    ),
    'xbox360': CatalogPlatform(
        platform_id='xbox360',
        display_name='Microsoft Xbox 360',
        manufacturer='Microsoft',
        generation=7,
        standard_extensions=('.iso', '.xex'),
        has_mature_emulation=False,
        known_emulators=('xenia',),
        notes='Xenia targets Windows natively; Linux execution requires verified Wine/Proton wrapper.',
    ),

    # Sega
    'megadrive': CatalogPlatform(
        platform_id='megadrive',
        display_name='Sega Genesis / Mega Drive',
        manufacturer='Sega',
        generation=4,
        standard_extensions=('.gen', '.smd', '.zip'),
        has_mature_emulation=True,
        known_emulators=('retroarch',),
    ),
}

# Platform aliases for flexible lookup
PLATFORM_ALIASES: dict[str, str] = {
    'psx': 'ps1',
    'playstation': 'ps1',
    'playstation1': 'ps1',
    'ps2': 'ps2',
    'playstation2': 'ps2',
    'ps3': 'ps3',
    'playstation3': 'ps3',
    'ps4': 'ps4',
    'playstation4': 'ps4',
    'ps5': 'ps5',
    'playstation5': 'ps5',
    'gc': 'gamecube',
    'genesis': 'megadrive',
    'x360': 'xbox360',
    'xbox_360': 'xbox360',
    'xboxog': 'xbox',
    'xbox_original': 'xbox',
}


# =============================================================================
# Upstream Emulator Definitions & CLI Arguments
# =============================================================================

EMULATOR_DEFINITIONS: dict[str, EmulatorDefinition] = {
    'duckstation': EmulatorDefinition(
        name='duckstation',
        display_name='DuckStation',
        upstream_url='https://github.com/stenzek/duckstation',
        supported_platforms=('ps1', 'psx'),
        supported_extensions=('.chd', '.cue', '.iso', '.pbp', '.bin', '.img'),
        default_args=('-batch', '-fullscreen', '--', '{rom}'),
        binary_candidates=('duckstation-qt', 'duckstation-nogui', 'duckstation'),
        native_linux_support=True,
        notes='Official PS1 emulator. -batch enables clean exit upon shutdown; -- prevents flag collision.',
    ),
    'pcsx2': EmulatorDefinition(
        name='pcsx2',
        display_name='PCSX2',
        upstream_url='https://github.com/PCSX2/pcsx2',
        supported_platforms=('ps2',),
        supported_extensions=('.iso', '.chd', '.bin', '.cso', '.gz'),
        default_args=('-batch', '-fullscreen', '--', '{rom}'),
        binary_candidates=('pcsx2-qt', 'pcsx2', 'net.pcsx2.PCSX2'),
        native_linux_support=True,
        notes='Official PS2 emulator. Modern Qt CLI supports -batch and -fullscreen with -- terminator.',
    ),
    'rpcs3': EmulatorDefinition(
        name='rpcs3',
        display_name='RPCS3',
        upstream_url='https://github.com/RPCS3/rpcs3',
        supported_platforms=('ps3',),
        supported_extensions=('.bin', '.iso'),
        default_args=('--no-gui', '{rom}'),
        binary_candidates=('rpcs3', 'RPCS3', 'net.rpcs3.RPCS3'),
        native_linux_support=True,
        notes='Official PS3 emulator. Directly boots EBOOT.BIN or disc ISO with --no-gui.',
    ),
    'shadps4': EmulatorDefinition(
        name='shadps4',
        display_name='shadPS4',
        upstream_url='https://github.com/shadps4-emu/shadPS4',
        supported_platforms=('ps4',),
        supported_extensions=('.bin', '.elf'),
        default_args=('-g', '{rom}', '--fullscreen', 'true'),
        binary_candidates=('shadps4', 'shadPS4', 'shadps4-qtlauncher'),
        native_linux_support=True,
        notes='Official PS4 emulator. Boots eboot.bin / raw ELF or Title ID folder with -g flag.',
    ),
    'xemu': EmulatorDefinition(
        name='xemu',
        display_name='xemu',
        upstream_url='https://github.com/xemu-project/xemu',
        supported_platforms=('xbox',),
        supported_extensions=('.iso', '.xbe'),
        default_args=('-full-screen', '-dvd_path', '{rom}'),
        binary_candidates=('xemu', 'app.xemu.xemu'),
        native_linux_support=True,
        notes='Official Original Xbox emulator. CLI accepts -full-screen and -dvd_path <path>.',
    ),
    'xenia': EmulatorDefinition(
        name='xenia',
        display_name='Xenia',
        upstream_url='https://github.com/xenia-project/xenia',
        supported_platforms=('xbox360',),
        supported_extensions=('.iso', '.xex'),
        default_args=('{rom}',),
        binary_candidates=('xenia', 'xenia.exe', 'xenia-canary'),
        native_linux_support=False,
        requires_runtime_wrapper=True,
        notes='Official Xbox 360 emulator. Native Windows build; Linux requires verified Wine/Proton runner.',
    ),
    'dolphin': EmulatorDefinition(
        name='dolphin',
        display_name='Dolphin',
        upstream_url='https://github.com/dolphin-emu/dolphin',
        supported_platforms=('gamecube', 'wii'),
        supported_extensions=('.rvz', '.wbfs', '.iso', '.gcm'),
        default_args=('-b', '-e', '{rom}'),
        binary_candidates=('dolphin-emu', 'dolphin-emu-nogui', 'org.DolphinEmu.dolphin-emu'),
        native_linux_support=True,
        notes='Official GameCube and Wii emulator. -b enables batch mode, -e specifies boot file.',
    ),
    'cemu': EmulatorDefinition(
        name='cemu',
        display_name='Cemu',
        upstream_url='https://github.com/cemu-project/Cemu',
        supported_platforms=('wiiu',),
        supported_extensions=('.rpx', '.wux', '.wud'),
        default_args=('-f', '-g', '{rom}'),
        binary_candidates=('cemu', 'Cemu', 'info.cemu.Cemu'),
        native_linux_support=True,
        notes='Official Wii U emulator. -f enables fullscreen, -g specifies game file.',
    ),
    'lichii': EmulatorDefinition(
        name='lichii',
        display_name='Lichii',
        upstream_url='https://github.com/shadps4-emu/shadPS4',
        supported_platforms=('switch2', 'switch'),
        supported_extensions=('.nup', '.nsp', '.xci', '.nro'),
        default_args=('{rom}',),
        env={'QT_QPA_PLATFORM': 'xcb'},
        binary_candidates=('lichii',),
        native_linux_support=True,
        notes='Preserved app-owned Switch and Switch 2 runner with QT_QPA_PLATFORM=xcb.',
    ),
    'eden': EmulatorDefinition(
        name='eden',
        display_name='Eden',
        upstream_url='https://github.com/eden-emulator/eden',
        supported_platforms=('switch',),
        supported_extensions=('.nsp', '.xci', '.nro'),
        default_args=('-f', '-g', '{rom}'),
        binary_candidates=('eden', 'Eden'),
        native_linux_support=True,
        notes='Preserved app-owned Switch emulator with -f -g CLI invocation.',
    ),
    'retroarch': EmulatorDefinition(
        name='retroarch',
        display_name='RetroArch',
        upstream_url='https://github.com/libretro/RetroArch',
        supported_platforms=('nes', 'snes', 'n64', 'gb', 'gbc', 'gba', 'nds', '3ds', 'megadrive', 'ps1', 'psx', 'nintendo_multi'),
        supported_extensions=('.z64', '.v64', '.n64', '.3ds', '.cia', '.nds', '.gba', '.gbc', '.gb', '.nes', '.sfc', '.smc', '.chd', '.pbp', '.cue', '.bin', '.gen', '.smd', '.zip', '.7z'),
        default_args=('{rom}',),
        binary_candidates=('retroarch', 'org.libretro.RetroArch'),
        native_linux_support=True,
        notes='Official libretro frontend. Multi-system core loader.',
    ),
    'ryujinx': EmulatorDefinition(
        name='ryujinx',
        display_name='Ryujinx',
        upstream_url='https://github.com/Ryubing/Ryujinx',
        supported_platforms=('switch',),
        supported_extensions=('.nsp', '.xci', '.nro'),
        default_args=('{rom}',),
        binary_candidates=('ryujinx', 'Ryujinx', 'org.ryujinx.Ryujinx'),
        native_linux_support=True,
        notes='Official Nintendo Switch emulator.',
    ),
}


# =============================================================================
# Registry & Discovery Engine
# =============================================================================

class EmulatorRegistry:
    """Central registry for catalog platforms, emulator definitions, and install detection."""

    def __init__(self, search_paths: tuple[Path, ...] | None = None):
        self.search_paths = search_paths if search_paths is not None else DEFAULT_SEARCH_PATHS

    @staticmethod
    def get_catalog_platform(platform_id: str) -> CatalogPlatform | None:
        """Resolve a catalog platform by canonical ID or alias."""
        clean = platform_id.lower().strip()
        canonical = PLATFORM_ALIASES.get(clean, clean)
        return CATALOG_PLATFORMS.get(canonical)

    @staticmethod
    def list_catalog_platforms() -> list[CatalogPlatform]:
        """Return all supported catalog platforms sorted by manufacturer and generation."""
        return sorted(
            CATALOG_PLATFORMS.values(),
            key=lambda p: (p.manufacturer, p.generation, p.display_name)
        )

    @staticmethod
    def get_emulator_definition(name: str) -> EmulatorDefinition | None:
        """Retrieve emulator definition by name."""
        return EMULATOR_DEFINITIONS.get(name.lower().strip())

    @staticmethod
    def list_emulator_definitions() -> list[EmulatorDefinition]:
        """Return all upstream emulator definitions."""
        return list(EMULATOR_DEFINITIONS.values())

    def detect_installed(
        self,
        emulator_name: str,
        custom_path: str | Path | None = None
    ) -> InstallStatus:
        """Detect whether an emulator executable is present and executable on disk.

        Does not start any process. Checks explicit custom_path first, then search paths,
        then system PATH.
        """
        definition = self.get_emulator_definition(emulator_name)
        if not definition:
            return InstallStatus(
                emulator_name=emulator_name,
                installed=False,
                executable_path=None,
                source='not_found',
                reason=f"Unknown emulator definition '{emulator_name}'",
            )

        # 1. Custom path check
        if custom_path:
            p = Path(custom_path).resolve()
            if p.is_file() and os.access(p, os.X_OK):
                return InstallStatus(
                    emulator_name=definition.name,
                    installed=True,
                    executable_path=str(p),
                    source='custom',
                    supported_platforms=definition.supported_platforms,
                )
            return InstallStatus(
                emulator_name=definition.name,
                installed=False,
                executable_path=str(p),
                source='custom',
                supported_platforms=definition.supported_platforms,
                reason=f"Custom path '{custom_path}' does not exist or is not executable",
            )

        # 2. Check for non-native requirements (e.g. Xenia on Linux)
        if not definition.native_linux_support:
            # Check if a custom wrapper script was placed in ~/.local/bin or search paths
            for candidate in definition.binary_candidates:
                found = self._find_candidate(candidate)
                if found:
                    return InstallStatus(
                        emulator_name=definition.name,
                        installed=True,
                        executable_path=str(found),
                        source='search_dir',
                        supported_platforms=definition.supported_platforms,
                    )
            return InstallStatus(
                emulator_name=definition.name,
                installed=False,
                executable_path=None,
                source='not_found',
                supported_platforms=definition.supported_platforms,
                reason=(
                    f"{definition.display_name} requires Windows or a verified Wine/Proton runtime; "
                    "no Linux native executable verified"
                ),
            )

        # 3. Standard search paths and candidate binary names
        for candidate in definition.binary_candidates:
            found = self._find_candidate(candidate)
            if found:
                return InstallStatus(
                    emulator_name=definition.name,
                    installed=True,
                    executable_path=str(found),
                    source='search_dir',
                    supported_platforms=definition.supported_platforms,
                )

        return InstallStatus(
            emulator_name=definition.name,
            installed=False,
            executable_path=None,
            source='not_found',
            supported_platforms=definition.supported_platforms,
            reason=f"Executable for {definition.display_name} not found in search paths",
        )

    def _find_candidate(self, candidate_name: str) -> Path | None:
        """Search for a binary candidate across directories and PATH."""
        # Check directories directly
        for base_dir in self.search_paths:
            candidate_path = base_dir / candidate_name
            if candidate_path.is_file() and os.access(candidate_path, os.X_OK):
                return candidate_path

        # Check system PATH via shutil.which
        which_path = shutil.which(candidate_name)
        if which_path:
            p = Path(which_path).resolve()
            if p.is_file() and os.access(p, os.X_OK):
                return p

        return None

    def detect_all(self) -> dict[str, InstallStatus]:
        """Detect installation status for all known upstream emulators."""
        return {
            name: self.detect_installed(name)
            for name in EMULATOR_DEFINITIONS
        }

    @staticmethod
    def get_game_compatibility(platform: str, game_identifier: str) -> PerGameCompatibility:
        """Query compatibility for a game.

        Defaults strictly to unknown/unverified. Zero false playable claims.
        """
        canonical_platform = PLATFORM_ALIASES.get(platform.lower().strip(), platform.lower().strip())
        return PerGameCompatibility(
            platform=canonical_platform,
            game_identifier=game_identifier,
            status='unknown',
            notes='No verified per-game compatibility report available; defaulting to unknown',
        )
