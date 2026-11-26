"""ROM inventory scanner and emulator configuration backend.

Bounded, read-only scanning of explicit selected ROM roots.
Stable exact path and platform identities; multidisc and variants preserved.
Local presence exposed separately from launchability.
App-owned editable emulator profiles and roots with atomic persistence.
Zero archive extraction, zero whole-ROM hashing, zero subprocess execution.
Integrated with EmulatorRegistry for upstream CLI argument specifications.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from native_services.emulator_registry import (
    EmulatorRegistry,
    CATALOG_PLATFORMS,
    PLATFORM_ALIASES,
)

# Bounded traversal constraints
MAX_SCAN_DEPTH = 6
MAX_SCAN_FILES = 20_000
MAX_SCAN_DIRS = 2_000
MAX_CONFIG_BYTES = 4 * 1024 * 1024  # 4 MiB config cap

# Unambiguous extensions mapped directly to a single platform
EXTENSION_TO_PLATFORM = {
    '.nsp': 'switch',
    '.xci': 'switch',
    '.nro': 'switch',
    '.nup': 'switch2',
    '.gcm': 'gamecube',
    '.wbfs': 'wii',
    '.z64': 'n64',
    '.v64': 'n64',
    '.n64': 'n64',
    '.3ds': '3ds',
    '.cia': '3ds',
    '.nds': 'nds',
    '.gba': 'gba',
    '.gbc': 'gbc',
    '.gb': 'gb',
    '.nes': 'nes',
    '.sfc': 'snes',
    '.smc': 'snes',
    '.rpx': 'wiiu',
    '.wux': 'wiiu',
    '.wud': 'wiiu',
    '.gen': 'megadrive',
    '.smd': 'megadrive',
    '.xbe': 'xbox',
    '.xex': 'xbox360',
}

# Ambiguous extensions that CANNOT be defaulted to a single platform;
# they REQUIRE a platform directory context (e.g. gamecube/, wii/, ps1/, ps2/, ps3/, xbox/).
# Notice .chd, .pkg, .iso, .bin, .cue, .zip, .7z, .rvz are all ambiguous.
AMBIGUOUS_EXTENSIONS = {
    '.iso', '.bin', '.cue', '.zip', '.7z', '.rvz', '.chd', '.pkg', '.pbp', '.cso', '.elf', '.img'
}

SUPPORTED_ROM_EXTENSIONS = set(EXTENSION_TO_PLATFORM.keys()) | AMBIGUOUS_EXTENSIONS

DIRECTORY_TO_PLATFORM = {
    'switch': 'switch',
    'switch2': 'switch2',
    'gamecube': 'gamecube',
    'gc': 'gamecube',
    'wii': 'wii',
    'wiiu': 'wiiu',
    'n64': 'n64',
    '3ds': '3ds',
    'nds': 'nds',
    'gba': 'gba',
    'gbc': 'gbc',
    'gb': 'gb',
    'nes': 'nes',
    'snes': 'snes',
    'megadrive': 'megadrive',
    'genesis': 'megadrive',
    'pokemon': 'nintendo_multi',
    'ps1': 'ps1',
    'psx': 'psx',
    'playstation': 'ps1',
    'playstation1': 'ps1',
    'ps2': 'ps2',
    'playstation2': 'ps2',
    'ps3': 'ps3',
    'ps3_game': 'ps3',
    'playstation3': 'ps3',
    'ps4': 'ps4',
    'playstation4': 'ps4',
    'ps5': 'ps5',
    'playstation5': 'ps5',
    'xbox': 'xbox',
    'xboxog': 'xbox',
    'xbox_original': 'xbox',
    'xbox360': 'xbox360',
    'x360': 'xbox360',
    'xbox_360': 'xbox360',
}

# Directories and patterns strictly excluded from ROM scanning
EXCLUDED_DIR_NAMES = {
    'firmware', 'keys', 'key', 'nand', 'sysnand', 'usernand', 'sdmc',
    'save', 'saves', 'savedata', 'states', 'state', 'screenshots',
    'cheats', 'mods', 'cache', 'logs', 'shader_cache', '.git'
}

EXCLUDED_FILE_PATTERNS = re.compile(
    r'(?:prod\.keys|title\.keys|.*\.keys|.*\.sav|.*\.srm|.*\.state\d*|.*\.log|.*\.txt|.*\.ini|.*\.nfo)\Z',
    re.I
)

TITLE_ID_PATTERN = re.compile(r'\[([0-9A-Fa-f]{16})\]')
PS_TITLE_ID_PATTERN = re.compile(r'(?:\[([A-Za-z]{4}[-_]?\d{5})\]|([A-Za-z]{4}[-_]?\d{5}))')
VERSION_PATTERN = re.compile(r'\[v([0-9]+)\]', re.I)
DISC_PATTERN = re.compile(r'(?:\((?:disc|disk|part)\s*(\d+)\)|(?:disc|disk|part)\s*(\d+))', re.I)


@dataclass(frozen=True)
class EmulatorProfile:
    name: str
    display_name: str
    executable_path: str
    args_template: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    supported_platforms: tuple[str, ...] = field(default_factory=tuple)
    supported_extensions: tuple[str, ...] = field(default_factory=tuple)
    is_custom: bool = False
    is_enabled: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d['args_template'] = list(self.args_template)
        d['supported_platforms'] = list(self.supported_platforms)
        d['supported_extensions'] = list(self.supported_extensions)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> EmulatorProfile:
        return cls(
            name=str(data['name']),
            display_name=str(data.get('display_name', data['name'])),
            executable_path=str(data.get('executable_path', '')),
            args_template=tuple(str(a) for a in data.get('args_template', ['{rom}'])),
            env={str(k): str(v) for k, v in data.get('env', {}).items()},
            supported_platforms=tuple(str(p) for p in data.get('supported_platforms', ())),
            supported_extensions=tuple(str(e).lower() for e in data.get('supported_extensions', ())),
            is_custom=bool(data.get('is_custom', False)),
            is_enabled=bool(data.get('is_enabled', True)),
        )


# Default emulator profiles discovered on Linux
DEFAULT_PROFILES: dict[str, EmulatorProfile] = {
    'lichii': EmulatorProfile(
        name='lichii',
        display_name='Lichii (Switch & Switch 2)',
        executable_path=str(Path.home() / '.local/bin/lichii'),
        args_template=('{rom}',),
        env={'QT_QPA_PLATFORM': 'xcb'},
        supported_platforms=('switch2', 'switch'),
        supported_extensions=('.nup', '.nsp', '.xci', '.nro'),
        is_custom=False,
    ),
    'eden': EmulatorProfile(
        name='eden',
        display_name='Eden (Switch)',
        executable_path=str(Path.home() / '.local/bin/eden'),
        args_template=('-f', '-g', '{rom}'),
        supported_platforms=('switch',),
        supported_extensions=('.nsp', '.xci', '.nro'),
        is_custom=False,
    ),
    'dolphin': EmulatorProfile(
        name='dolphin',
        display_name='Dolphin (GameCube & Wii)',
        executable_path=str(Path.home() / '.local/bin/dolphin-emu'),
        args_template=('-b', '-e', '{rom}'),
        supported_platforms=('gamecube', 'wii'),
        supported_extensions=('.rvz', '.wbfs', '.iso', '.gcm'),
        is_custom=False,
    ),
    'cemu': EmulatorProfile(
        name='cemu',
        display_name='Cemu (Wii U)',
        executable_path='/usr/bin/cemu',
        args_template=('-f', '-g', '{rom}'),
        supported_platforms=('wiiu',),
        supported_extensions=('.rpx', '.wux', '.wud'),
        is_custom=False,
    ),
    'duckstation': EmulatorProfile(
        name='duckstation',
        display_name='DuckStation (PS1)',
        executable_path='/usr/bin/duckstation-qt',
        args_template=('-batch', '-fullscreen', '--', '{rom}'),
        supported_platforms=('ps1', 'psx'),
        supported_extensions=('.chd', '.cue', '.iso', '.pbp', '.bin', '.img'),
        is_custom=False,
    ),
    'pcsx2': EmulatorProfile(
        name='pcsx2',
        display_name='PCSX2 (PS2)',
        executable_path='/usr/bin/pcsx2-qt',
        args_template=('-batch', '-fullscreen', '--', '{rom}'),
        supported_platforms=('ps2',),
        supported_extensions=('.iso', '.chd', '.bin', '.cso', '.gz'),
        is_custom=False,
    ),
    'rpcs3': EmulatorProfile(
        name='rpcs3',
        display_name='RPCS3 (PS3)',
        executable_path=str(Path.home() / '.local/bin/rpcs3'),
        args_template=('--no-gui', '{rom}'),
        supported_platforms=('ps3',),
        supported_extensions=('.bin', '.iso'),
        is_custom=False,
    ),
    'shadps4': EmulatorProfile(
        name='shadps4',
        display_name='shadPS4 (PS4)',
        executable_path='/usr/bin/shadps4',
        args_template=('-g', '{rom}', '--fullscreen', 'true'),
        supported_platforms=('ps4',),
        supported_extensions=('.bin', '.elf'),
        is_custom=False,
    ),
    'xemu': EmulatorProfile(
        name='xemu',
        display_name='xemu (Xbox)',
        executable_path='/usr/bin/xemu',
        args_template=('-full-screen', '-dvd_path', '{rom}'),
        supported_platforms=('xbox',),
        supported_extensions=('.iso', '.xbe'),
        is_custom=False,
    ),
    'xenia': EmulatorProfile(
        name='xenia',
        display_name='Xenia (Xbox 360)',
        executable_path='/usr/bin/xenia',
        args_template=('{rom}',),
        supported_platforms=('xbox360',),
        supported_extensions=('.iso', '.xex'),
        is_custom=False,
    ),
    'retroarch': EmulatorProfile(
        name='retroarch',
        display_name='RetroArch (Multi-System)',
        executable_path='/usr/bin/retroarch',
        args_template=('{rom}',),
        supported_platforms=('n64', '3ds', 'nds', 'gba', 'gbc', 'gb', 'nes', 'snes', 'megadrive', 'ps1', 'psx', 'nintendo_multi'),
        supported_extensions=('.z64', '.v64', '.n64', '.3ds', '.cia', '.nds', '.gba', '.gbc', '.gb', '.nes', '.sfc', '.smc', '.chd', '.pbp', '.cue', '.bin', '.gen', '.smd', '.zip', '.7z'),
        is_custom=False,
    ),
}


@dataclass(frozen=True)
class RomRecord:
    rom_id: str
    platform: str
    file_path: str
    file_name: str
    file_size: int
    mtime_ns: int
    extension: str
    title_id: str | None = None
    version: int | None = None
    disc_number: int | None = None
    variant_label: str = 'Base'
    local_presence: bool = True
    launchable: bool = False
    emulator_name: str | None = None
    unlaunchable_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RomLaunchIntent:
    rom_id: str
    rom_path: str
    platform: str
    emulator_name: str | None
    executable: str | None
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    verified_contract: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['argv'] = list(self.argv)
        return d


def detect_platform(file_path: Path, extension: str, root_path: Path) -> str:
    """Determine platform identity from directory context and file extension.

    Ambiguous extensions (.iso, .bin, .cue, .zip, .7z, .rvz, .chd, .pkg, .pbp, .cso)
    REQUIRE a directory-level or root-level platform context. If no matching
    platform context exists, they return 'unknown'.
    """
    clean_ext = extension.lower()
    try:
        rel = file_path.relative_to(root_path)
        parts_to_check = list(rel.parts[:-1])
    except ValueError:
        parts_to_check = list(file_path.parent.parts)

    # Search directory parts from deepest to shallowest, then root name
    for part in reversed(parts_to_check + [root_path.name]):
        part_clean = part.lower().strip()
        candidate = DIRECTORY_TO_PLATFORM.get(part_clean)
        if not candidate and re.match(r'^cusa\d{5}', part_clean):
            candidate = 'ps4'
        if not candidate and part_clean == 'ps3_game':
            candidate = 'ps3'

        if candidate:
            # Verify extension compatibility with directory candidate
            if candidate == 'wii' and clean_ext in ('.wbfs', '.rvz', '.iso'):
                return 'wii'
            if candidate == 'gamecube' and clean_ext in ('.rvz', '.gcm', '.iso'):
                return 'gamecube'
            if candidate == 'switch2' and clean_ext == '.nup':
                return 'switch2'
            if candidate == 'switch' and clean_ext in ('.nsp', '.xci', '.nro'):
                return 'switch'
            if candidate in ('ps1', 'psx') and clean_ext in ('.chd', '.cue', '.iso', '.bin', '.pbp', '.img'):
                return candidate
            if candidate == 'ps2' and clean_ext in ('.iso', '.chd', '.bin', '.cso', '.gz'):
                return 'ps2'
            if candidate == 'ps3' and clean_ext in ('.bin', '.iso', '.pkg'):
                return 'ps3'
            if candidate == 'ps4' and clean_ext in ('.bin', '.elf', '.pkg', '.iso'):
                return 'ps4'
            if candidate == 'ps5' and clean_ext in ('.pkg', '.iso', '.bin', '.elf'):
                return 'ps5'
            if candidate == 'xbox' and clean_ext in ('.iso', '.xbe'):
                return 'xbox'
            if candidate == 'xbox360' and clean_ext in ('.iso', '.xex'):
                return 'xbox360'
            if candidate == 'wiiu' and clean_ext in ('.rpx', '.wux', '.wud', '.iso'):
                return 'wiiu'
            if candidate in ('n64', '3ds', 'nds', 'gba', 'gbc', 'gb', 'nes', 'snes', 'megadrive', 'nintendo_multi'):
                return candidate

    # Ambiguous extensions without platform directory cannot be defaulted
    if clean_ext in AMBIGUOUS_EXTENSIONS:
        return 'unknown'

    # Fall back to unambiguous extension allowlist
    return EXTENSION_TO_PLATFORM.get(clean_ext, 'unknown')


def extract_rom_metadata(file_path: Path, extension: str, platform: str) -> tuple[str | None, int | None, int | None, str]:
    """Extract Title ID, version, disc index, and variant label from filename."""
    name = file_path.stem

    # Extract 16-hex Title ID (Switch, Switch 2, Wii U)
    title_id = None
    t_match = TITLE_ID_PATTERN.search(name)
    if t_match:
        title_id = t_match.group(1).upper()
    else:
        # Check PlayStation / Xbox style Title ID
        ps_match = PS_TITLE_ID_PATTERN.search(name)
        if not ps_match:
            # Check parent directory name (e.g. CUSA00123/eboot.bin)
            ps_match = PS_TITLE_ID_PATTERN.search(file_path.parent.name)
        if ps_match:
            raw_id = ps_match.group(1) or ps_match.group(2)
            if raw_id:
                title_id = raw_id.replace('-', '').replace('_', '').upper()

    # Extract version
    version = None
    v_match = VERSION_PATTERN.search(name)
    if v_match:
        try:
            version = int(v_match.group(1))
        except ValueError:
            pass

    # Extract disc number for multi-disc games
    disc_number = None
    d_match = DISC_PATTERN.search(name)
    if d_match:
        for g in d_match.groups():
            if g is not None:
                try:
                    disc_number = int(g)
                    break
                except ValueError:
                    pass

    # Determine variant label
    variant_label = 'Base'
    name_lower = name.lower()
    if '[update]' in name_lower or '(update)' in name_lower or '[upd]' in name_lower or 'update' in name_lower:
        variant_label = 'Update'
    elif '[dlc]' in name_lower or '(dlc)' in name_lower or 'dlc' in name_lower:
        variant_label = 'DLC'
    elif '[patch]' in name_lower or '(patch)' in name_lower or 'patch' in name_lower:
        variant_label = 'Update'
    elif disc_number is not None:
        variant_label = f'Disc {disc_number}'
    elif version is not None and version > 0:
        variant_label = f'v{version}'

    return title_id, version, disc_number, variant_label


def generate_rom_id(platform: str, file_path: Path, root_path: Path) -> str:
    """Generate deterministic, stable identifier for a local ROM entry."""
    rel = str(file_path.relative_to(root_path)).replace(os.sep, '/')
    digest = hashlib.sha256(f'{platform}:{rel}'.encode('utf-8')).hexdigest()[:24]
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', file_path.stem.lower()).strip('-')[:32]
    return f'rom:{platform}:{slug}-{digest[:8]}'


class RomInventoryScanner:
    """Read-only bounded traversal of explicit local ROM roots."""

    def __init__(self, roots: list[str | Path]):
        self.roots: list[Path] = []
        for r in roots:
            p = Path(r).resolve()
            if p.is_dir() and p not in self.roots:
                self.roots.append(p)

    def scan(self, profiles: dict[str, EmulatorProfile] | None = None) -> list[RomRecord]:
        """Traverse configured roots and return verified ROM records."""
        active_profiles = profiles if profiles is not None else DEFAULT_PROFILES
        results: list[RomRecord] = []
        file_count = 0
        dir_count = 0

        for root in self.roots:
            for current_root, dirs, files in os.walk(str(root), followlinks=False):
                dir_count += 1
                if dir_count > MAX_SCAN_DIRS:
                    break

                current_path = Path(current_root)
                try:
                    rel_depth = len(current_path.relative_to(root).parts)
                except ValueError:
                    rel_depth = 0
                if rel_depth > MAX_SCAN_DEPTH:
                    dirs.clear()
                    continue

                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in EXCLUDED_DIR_NAMES
                    and not d.startswith('.')
                    and not os.path.islink(current_path / d)
                ]

                for file_name in files:
                    file_count += 1
                    if file_count > MAX_SCAN_FILES:
                        return results

                    if file_name.startswith('.'):
                        continue
                    if EXCLUDED_FILE_PATTERNS.match(file_name):
                        continue

                    ext = os.path.splitext(file_name)[1].lower()
                    if ext not in SUPPORTED_ROM_EXTENSIONS:
                        continue

                    file_path = current_path / file_name

                    if file_path.is_symlink():
                        try:
                            resolved = file_path.resolve()
                            if not str(resolved).startswith(str(root)):
                                continue
                        except (OSError, RuntimeError):
                            continue

                    try:
                        st = file_path.stat()
                        if not stat.S_ISREG(st.st_mode) or st.st_size <= 0:
                            continue
                    except OSError:
                        continue

                    platform = detect_platform(file_path, ext, root)
                    title_id, version, disc_num, variant = extract_rom_metadata(file_path, ext, platform)
                    rom_id = generate_rom_id(platform, file_path, root)

                    # Assess launchability and emulator association
                    if platform == 'unknown':
                        assigned_emu = None
                        launchable = False
                        unlaunch_reason = f'Ambiguous extension {ext} without platform directory; platform is unknown'
                    elif platform == 'ps5':
                        assigned_emu, _, _ = self._match_emulator(platform, ext, active_profiles)
                        launchable = False
                        unlaunch_reason = 'No verified Linux PS5 emulator or runtime available; platform is catalog-only'
                    elif ext == '.pkg' or variant in ('Update', 'DLC'):
                        assigned_emu, _, _ = self._match_emulator(platform, ext, active_profiles)
                        launchable = False
                        if ext == '.pkg':
                            unlaunch_reason = 'Package (.pkg) installer requires prior installation; not standalone launchable'
                        else:
                            unlaunch_reason = f'{variant} packages are not standalone launchable'
                    elif platform == 'xbox360':
                        assigned_emu, launchable, unlaunch_reason = self._match_emulator(
                            platform, ext, active_profiles
                        )
                        if not launchable and ('not found' in (unlaunch_reason or '') or 'No emulator' in (unlaunch_reason or '')):
                            unlaunch_reason = 'Xenia requires Windows or verified Wine/Proton runtime; no Linux native executable verified'
                    else:
                        assigned_emu, launchable, unlaunch_reason = self._match_emulator(
                            platform, ext, active_profiles
                        )

                    results.append(RomRecord(
                        rom_id=rom_id,
                        platform=platform,
                        file_path=str(file_path),
                        file_name=file_name,
                        file_size=st.st_size,
                        mtime_ns=st.st_mtime_ns,
                        extension=ext,
                        title_id=title_id,
                        version=version,
                        disc_number=disc_num,
                        variant_label=variant,
                        local_presence=True,
                        launchable=launchable,
                        emulator_name=assigned_emu,
                        unlaunchable_reason=unlaunch_reason,
                    ))

        results.sort(key=lambda r: (r.platform, r.file_name))
        return results

    @staticmethod
    def _match_emulator(platform: str, ext: str, profiles: dict[str, EmulatorProfile]) -> tuple[str | None, bool, str | None]:
        """Find best matching emulator profile and verify executable presence."""
        candidates = []
        for emu_name, prof in profiles.items():
            if not prof.is_enabled:
                continue
            platform_match = (
                platform in prof.supported_platforms
                or (platform == 'ps1' and 'psx' in prof.supported_platforms)
                or (platform == 'psx' and 'ps1' in prof.supported_platforms)
            )
            if prof.supported_platforms:
                if platform_match and (not prof.supported_extensions or ext in prof.supported_extensions):
                    candidates.append(prof)
            else:
                if not prof.supported_extensions or ext in prof.supported_extensions:
                    candidates.append(prof)

        if not candidates:
            return None, False, f'No emulator configured for platform {platform} ({ext})'

        chosen = candidates[0]
        exe = Path(chosen.executable_path)
        if not exe.is_file() or not os.access(exe, os.X_OK):
            return chosen.name, False, f"Emulator executable '{chosen.executable_path}' not found or not executable"

        return chosen.name, True, None


class RomInventoryStore:
    """Functional writable manager for app-owned ROM roots and emulator profiles."""

    def __init__(self, config_dir: str | Path):
        self.config_dir = Path(config_dir).resolve()
        self.config_path = self.config_dir / 'rom_config.json'
        self.roots: list[str] = []
        self.profiles: dict[str, EmulatorProfile] = dict(DEFAULT_PROFILES)
        self.preferred_emulators: dict[str, str] = {}
        self.load()

    def load(self):
        """Load app-owned configuration from disk if present."""
        if not self.config_path.is_file():
            return
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.roots = [str(r) for r in data.get('roots', []) if isinstance(r, str)]
            for p_dict in data.get('custom_profiles', []):
                try:
                    prof = EmulatorProfile.from_dict(p_dict)
                    self.profiles[prof.name] = prof
                except Exception:
                    pass
            self.preferred_emulators = {
                str(k): str(v) for k, v in data.get('preferred_emulators', {}).items()
            }
        except Exception:
            pass

    def save(self):
        """Persist app-owned configuration atomically with fsync."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            'kind': 'rom-inventory-config-v1',
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'roots': self.roots,
            'custom_profiles': [
                p.to_dict() for p in self.profiles.values() if p.is_custom
            ],
            'preferred_emulators': self.preferred_emulators,
        }
        raw = (json.dumps(payload, indent=2, sort_keys=True) + '\n').encode('utf-8')
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError('Configuration size exceeds maximum allowed byte cap')

        tmp_name = f'.txn-{uuid.uuid4().hex}'
        tmp_path = self.config_dir / tmp_name
        try:
            with open(tmp_path, 'wb') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.config_path)
            dfd = os.open(str(self.config_dir), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def add_root(self, root_path: str | Path) -> bool:
        p = str(Path(root_path).resolve())
        if p not in self.roots:
            self.roots.append(p)
            self.save()
            return True
        return False

    def remove_root(self, root_path: str | Path) -> bool:
        p = str(Path(root_path).resolve())
        if p in self.roots:
            self.roots.remove(p)
            self.save()
            return True
        return False

    def set_emulator_profile(self, profile: EmulatorProfile):
        """Update or register an emulator profile (built-in override or custom)."""
        self.profiles[profile.name] = profile
        self.save()

    def set_preferred_emulator(self, platform: str, emulator_name: str):
        if emulator_name not in self.profiles:
            raise ValueError(f"Unknown emulator profile '{emulator_name}'")
        self.preferred_emulators[platform] = emulator_name
        self.save()

    def scan_inventory(self) -> list[RomRecord]:
        """Perform scan across all configured roots using active profiles."""
        scanner = RomInventoryScanner(self.roots)
        return scanner.scan(self.profiles)

    def build_launch_intent(self, record: RomRecord) -> RomLaunchIntent:
        """Construct verified launch command vector without executing any process."""
        if not record.local_presence:
            return RomLaunchIntent(
                rom_id=record.rom_id,
                rom_path=record.file_path,
                platform=record.platform,
                emulator_name=None,
                executable=None,
                argv=(),
                verified_contract=False,
                error='ROM file does not exist locally on disk',
            )

        if record.platform == 'unknown':
            return RomLaunchIntent(
                rom_id=record.rom_id,
                rom_path=record.file_path,
                platform=record.platform,
                emulator_name=None,
                executable=None,
                argv=(),
                verified_contract=False,
                error='Platform is unknown; cannot launch ambiguous ROM',
            )

        if record.platform == 'ps5':
            return RomLaunchIntent(
                rom_id=record.rom_id,
                rom_path=record.file_path,
                platform=record.platform,
                emulator_name=None,
                executable=None,
                argv=(),
                verified_contract=False,
                error='No verified Linux PS5 emulator or runtime available; platform is catalog-only',
            )

        if record.extension == '.pkg' or record.variant_label in ('Update', 'DLC'):
            return RomLaunchIntent(
                rom_id=record.rom_id,
                rom_path=record.file_path,
                platform=record.platform,
                emulator_name=record.emulator_name,
                executable=None,
                argv=(),
                verified_contract=False,
                error=(
                    f'{record.variant_label} packages are not standalone launchable'
                    if record.variant_label in ('Update', 'DLC')
                    else 'Package (.pkg) installer requires prior installation; not standalone launchable'
                ),
            )

        # Check preferred emulator first, then record's assigned emulator
        emu_name = self.preferred_emulators.get(record.platform) or record.emulator_name
        if not emu_name or emu_name not in self.profiles:
            if record.platform == 'xbox360':
                err_msg = 'Xenia requires Windows or verified Wine/Proton runtime; no Linux native executable verified'
            else:
                err_msg = f"No emulator profile configured for platform '{record.platform}'"
            return RomLaunchIntent(
                rom_id=record.rom_id,
                rom_path=record.file_path,
                platform=record.platform,
                emulator_name=None,
                executable=None,
                argv=(),
                verified_contract=False,
                error=err_msg,
            )

        profile = self.profiles[emu_name]
        if not profile.is_enabled:
            return RomLaunchIntent(
                rom_id=record.rom_id,
                rom_path=record.file_path,
                platform=record.platform,
                emulator_name=emu_name,
                executable=profile.executable_path,
                argv=(),
                verified_contract=False,
                error=f"Configured emulator '{emu_name}' is disabled",
            )

        exe = Path(profile.executable_path)
        if not exe.is_file() or not os.access(exe, os.X_OK):
            if record.platform == 'xbox360' and 'xenia' in emu_name:
                err_msg = 'Xenia requires Windows or verified Wine/Proton runtime; no Linux native executable verified'
            else:
                err_msg = f"Configured emulator executable '{profile.executable_path}' is missing or not executable"
            return RomLaunchIntent(
                rom_id=record.rom_id,
                rom_path=record.file_path,
                platform=record.platform,
                emulator_name=emu_name,
                executable=profile.executable_path,
                argv=(),
                verified_contract=False,
                error=err_msg,
            )

        # Build argv vector using profile template
        argv_list = [profile.executable_path]
        for arg in profile.args_template:
            argv_list.append(arg.replace('{rom}', record.file_path))

        return RomLaunchIntent(
            rom_id=record.rom_id,
            rom_path=record.file_path,
            platform=record.platform,
            emulator_name=emu_name,
            executable=profile.executable_path,
            argv=tuple(argv_list),
            env=dict(profile.env),
            verified_contract=True,
            error=None,
        )
