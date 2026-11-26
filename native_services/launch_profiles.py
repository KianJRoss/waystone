"""Shared app-owned editable launch profile store, resolver, and executor.

Supports exact game/ROM association across all native launchers (Steam, Heroic, Xodus,
Lutris) and emulators (Lichii, Eden, Dolphin, Cemu, RPCS3, RetroArch, and custom).
Token substitution restricted to explicit supported whole arguments ({rom}, {game_id}).
Never shell. Atomic persistence with transactional rollback.
Executable/payload rechecks before launch, preventing validate-safe/execute-different bypass.
Subprocess Popen is the only execution method, with injectable spawn for offline testing.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict, field, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform as sys_platform
import re
import shutil
import stat
import subprocess
import uuid

try:
    from private_addons import DEFAULT_TARGET_HOST as _DEFAULT_TARGET_HOST
except ImportError:
    _DEFAULT_TARGET_HOST = None


def _default_executable(name: str, *, home: Path | None = None, which=None) -> str:
    """Prefer the executable available in this environment; only use local-bin if it exists.

    If the app is installed before its optional launcher, retain the command name
    so a later PATH change can make the reviewed default usable without rewriting
    the user's profile.
    """
    resolve = which or shutil.which
    found = resolve(name)
    if found:
        return str(Path(found).absolute())
    local = (Path(home).expanduser() if home is not None else Path.home()) / '.local' / 'bin' / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local.absolute())
    return name


_SEEDED_DEFAULT_COMMANDS = {
    'native-steam': 'steam',
    'native-xodus': 'xodus-nexus',
    'native-lutris-ea': 'lutris',
    'native-lutris-ubisoft': 'lutris',
    'native-lutris-battlenet-game': 'lutris',
    'emu-lichii': 'lichii',
    'emu-eden': 'eden',
    'emu-dolphin': 'dolphin-emu',
    'emu-cemu': 'cemu',
    'emu-rpcs3': 'rpcs3',
    'emu-retroarch': 'retroarch',
}

MAX_PROFILE_CONFIG_BYTES = 8 * 1024 * 1024  # 8 MiB cap
VALID_TARGET_TYPES = frozenset({'native_game', 'rom', 'custom'})


class LaunchProfileError(ValueError):
    """Raised for invalid profiles, token violations, or unverified launch intents."""


def _validate_clean_str(val: str, field_name: str, allow_empty: bool = False) -> None:
    if not isinstance(val, str):
        raise LaunchProfileError(f"Field '{field_name}' must be a string, got {type(val).__name__}")
    if not allow_empty and not val.strip():
        raise LaunchProfileError(f"Field '{field_name}' cannot be empty")
    if any(ord(c) < 32 for c in val):
        raise LaunchProfileError(f"Field '{field_name}' contains illegal control characters")


@dataclass(frozen=True)
class LaunchProfile:
    profile_id: str
    display_name: str
    target_type: str                     # 'native_game', 'rom', 'custom'
    platform_or_provider: str            # 'steam', 'heroic-epic', 'xodus', 'lutris-battlenet', 'switch', 'gamecube', etc.
    executable: str                      # absolute path or valid command
    argv_template: tuple[str, ...]       # tokenized arguments
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    target_host: str | None = None       # e.g. 'workstation', checked against platform.node()
    is_default: bool = False
    is_app_owned: bool = True
    supported_platforms: tuple[str, ...] = field(default_factory=tuple)
    supported_extensions: tuple[str, ...] = field(default_factory=tuple)
    extra_metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        _validate_clean_str(self.profile_id, 'profile_id')
        _validate_clean_str(self.display_name, 'display_name')
        if self.target_type not in VALID_TARGET_TYPES:
            raise LaunchProfileError(
                f"Invalid target_type '{self.target_type}'; must be one of {sorted(VALID_TARGET_TYPES)}"
            )
        _validate_clean_str(self.platform_or_provider, 'platform_or_provider')
        _validate_clean_str(self.executable, 'executable')

        if not isinstance(self.argv_template, (tuple, list)):
            raise LaunchProfileError("argv_template must be a tuple or list of strings")
        for i, a in enumerate(self.argv_template):
            _validate_clean_str(a, f"argv_template[{i}]", allow_empty=True)
        object.__setattr__(self, 'argv_template', tuple(self.argv_template))

        if self.cwd is not None:
            _validate_clean_str(self.cwd, 'cwd')

        if not isinstance(self.env, dict):
            raise LaunchProfileError("env must be a dict")
        for k, v in self.env.items():
            _validate_clean_str(k, 'env key')
            _validate_clean_str(v, 'env value', allow_empty=True)

        if self.target_host is not None:
            _validate_clean_str(self.target_host, 'target_host')

        if not isinstance(self.supported_platforms, (tuple, list)):
            raise LaunchProfileError("supported_platforms must be a tuple or list of strings")
        for i, p in enumerate(self.supported_platforms):
            _validate_clean_str(p, f"supported_platforms[{i}]")
        object.__setattr__(self, 'supported_platforms', tuple(self.supported_platforms))

        if not isinstance(self.supported_extensions, (tuple, list)):
            raise LaunchProfileError("supported_extensions must be a tuple or list of strings")
        for i, ext in enumerate(self.supported_extensions):
            _validate_clean_str(ext, f"supported_extensions[{i}]")
        object.__setattr__(self, 'supported_extensions', tuple(e.lower() for e in self.supported_extensions))

    def to_dict(self) -> dict:
        d = asdict(self)
        d['argv_template'] = list(self.argv_template)
        d['supported_platforms'] = list(self.supported_platforms)
        d['supported_extensions'] = list(self.supported_extensions)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> LaunchProfile:
        if not isinstance(data, dict):
            raise LaunchProfileError("Profile data must be a dictionary")
        if 'profile_id' not in data:
            raise LaunchProfileError("Profile data missing required 'profile_id'")
        if 'executable' not in data:
            raise LaunchProfileError("Profile data missing required 'executable'")

        raw_argv = data.get('argv_template', ())
        if not isinstance(raw_argv, (list, tuple)):
            raise LaunchProfileError("argv_template must be a list or tuple")

        raw_env = data.get('env', {})
        if not isinstance(raw_env, dict):
            raise LaunchProfileError("env must be a dictionary")

        raw_platforms = data.get('supported_platforms', ())
        if not isinstance(raw_platforms, (list, tuple)):
            raise LaunchProfileError("supported_platforms must be a list or tuple")

        raw_exts = data.get('supported_extensions', ())
        if not isinstance(raw_exts, (list, tuple)):
            raise LaunchProfileError("supported_extensions must be a list or tuple")

        return cls(
            profile_id=str(data['profile_id']),
            display_name=str(data.get('display_name', data['profile_id'])),
            target_type=str(data.get('target_type', 'custom')),
            platform_or_provider=str(data.get('platform_or_provider', 'custom')),
            executable=str(data['executable']),
            argv_template=tuple(raw_argv),
            cwd=str(data['cwd']) if data.get('cwd') else None,
            env=dict(raw_env),
            target_host=str(data['target_host']) if data.get('target_host') else None,
            is_default=bool(data.get('is_default', False)),
            is_app_owned=bool(data.get('is_app_owned', True)),
            supported_platforms=tuple(raw_platforms),
            supported_extensions=tuple(raw_exts),
            extra_metadata=dict(data.get('extra_metadata', {})),
        )


@dataclass(frozen=True)
class LaunchIntent:
    profile_id: str
    target_id: str
    target_path: str | None
    executable: str
    argv: tuple[str, ...]
    cwd: str | None
    env: dict[str, str]
    target_host: str | None
    verified_contract: bool
    error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['argv'] = list(self.argv)
        return d


@dataclass(frozen=True)
class LaunchResult:
    status: str                          # 'handed_off', 'failed'
    pid: int | None
    profile_id: str
    target_id: str
    argv: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['argv'] = list(self.argv)
        return d


def substitute_tokens(
    template: tuple[str, ...],
    *,
    target_id: str,
    target_path: str | None,
    executable: str,
) -> tuple[str, ...]:
    """Substitute only explicit supported whole argument tokens ({rom}, {game_id}, {executable}).

    Never uses shell evaluation. Preserves whitespaces and exact argument boundaries.
    """
    result: list[str] = []
    for arg in template:
        if not isinstance(arg, str):
            raise LaunchProfileError(f'Expected string argument token, got {type(arg).__name__}')

        # Direct whole-argument replacement
        if arg == '{rom}':
            if not target_path:
                raise LaunchProfileError('Profile requires {rom} payload path, but none was provided')
            result.append(target_path)
        elif arg == '{game_id}':
            result.append(target_id)
        elif arg == '{executable}':
            result.append(executable)
        else:
            # Embedded URI or compound token replacement (e.g. 'steam://rungameid/{game_id}')
            replaced = arg
            if '{game_id}' in replaced:
                replaced = replaced.replace('{game_id}', target_id)
            if '{rom}' in replaced:
                if not target_path:
                    raise LaunchProfileError('Profile requires {rom} payload path, but none was provided')
                replaced = replaced.replace('{rom}', target_path)
            if '{executable}' in replaced:
                replaced = replaced.replace('{executable}', executable)

            # Prevent unreplaced unknown curly-brace tokens
            unresolved = re.findall(r'\{[a-zA-Z0-9_]+\}', replaced)
            if unresolved:
                raise LaunchProfileError(f'Unsupported launch token(s): {", ".join(unresolved)}')

            result.append(replaced)

    return tuple(result)


def recheck_launch_intent(intent: LaunchIntent, current_host: str | None = None) -> tuple[bool, str | None]:
    """Validate host affinity, executable existence/permissions, argv[0] match, and payload presence."""
    host = current_host if current_host is not None else sys_platform.node()

    if intent.target_host and host != intent.target_host:
        return False, f"Target host mismatch: profile requires '{intent.target_host}', current host is '{host}'"

    if not intent.executable or not intent.executable.strip():
        return False, "Launch intent executable cannot be empty"

    if not intent.argv:
        return False, "Launch intent argv cannot be empty"

    # Executable bypass protection: argv[0] must match verified executable
    if intent.argv[0] != intent.executable:
        return False, (
            f"Executable mismatch: argv[0] '{intent.argv[0]}' does not match "
            f"verified executable '{intent.executable}'"
        )

    exe_path = Path(intent.executable)
    if exe_path.is_absolute():
        if not exe_path.is_file() or not os.access(exe_path, os.X_OK):
            return False, f"Configured executable '{intent.executable}' is missing or not executable"
    else:
        resolved = shutil.which(intent.executable)
        if not resolved or not os.access(resolved, os.X_OK):
            return False, f"Executable command '{intent.executable}' not found on PATH or not executable"

    # cwd validation: must be absolute, exist, and be an accessible directory
    if intent.cwd is not None:
        cwd_p = Path(intent.cwd)
        if not cwd_p.is_absolute():
            return False, f"Configured working directory '{intent.cwd}' must be an absolute path"
        if not cwd_p.is_dir():
            return False, f"Configured working directory '{intent.cwd}' does not exist or is not a directory"
        if not os.access(cwd_p, os.X_OK | os.R_OK):
            return False, f"Configured working directory '{intent.cwd}' is not accessible"

    if intent.target_path:
        p = Path(intent.target_path)
        if not p.exists():
            return False, f"Payload target '{intent.target_path}' does not exist on disk"

    return True, None


def launch(intent: LaunchIntent, *, current_host: str | None = None, spawn=None) -> LaunchResult:
    """Explicitly launch game or emulator using validated launch intent.

    Subprocess Popen is the only execution method. Spawn is injectable for offline tests.
    """
    if not intent.verified_contract:
        return LaunchResult(
            status='failed',
            pid=None,
            profile_id=intent.profile_id,
            target_id=intent.target_id,
            argv=intent.argv,
            error=intent.error or 'Unverified launch intent contract',
        )

    valid, reason = recheck_launch_intent(intent, current_host=current_host)
    if not valid:
        return LaunchResult(
            status='failed',
            pid=None,
            profile_id=intent.profile_id,
            target_id=intent.target_id,
            argv=intent.argv,
            error=reason,
        )

    env = dict(os.environ)
    if intent.env:
        env.update(intent.env)

    try:
        argv=list(intent.argv)
        # A detached Unix session still shares the viewer's systemd cgroup.
        # The packaged Linux frontend opts into independent game scopes so
        # closing/updating Waystone never terminates its games/emulators.
        if spawn is None and os.environ.get('WAYSTONE_GAME_SCOPES') == '1':
            runner=shutil.which('systemd-run')
            if not runner:
                raise OSError('Independent game scope is unavailable')
            argv=[runner,'--user','--scope','--collect','--quiet',
                  '--unit=waystone-game-'+uuid.uuid4().hex,'--',*argv]
        proc = (spawn or subprocess.Popen)(
            argv,
            cwd=intent.cwd,
            env=env,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        pid = getattr(proc, 'pid', None)
        return LaunchResult(
            status='handed_off',
            pid=pid,
            profile_id=intent.profile_id,
            target_id=intent.target_id,
            argv=intent.argv,
            error=None,
        )
    except OSError as exc:
        return LaunchResult(
            status='failed',
            pid=None,
            profile_id=intent.profile_id,
            target_id=intent.target_id,
            argv=intent.argv,
            error=f'Process handoff failed: {exc}',
        )


class LaunchProfileStore:
    """Manages app-owned launch profiles, defaults, and game-specific associations."""

    def __init__(self, config_dir: str | Path):
        self.config_dir = Path(config_dir).resolve()
        self.config_path = self.config_dir / 'launch_profiles.json'
        self.profiles: dict[str, LaunchProfile] = {}
        self.default_associations: dict[str, str] = {}    # platform_or_provider -> profile_id
        self.game_associations: dict[str, str] = {}       # target_id -> profile_id
        self.load()

    @contextmanager
    def _transaction(self):
        """Context manager providing transactional in-memory rollback if persistence fails."""
        backup_profiles = dict(self.profiles)
        backup_defaults = dict(self.default_associations)
        backup_games = dict(self.game_associations)
        try:
            yield
            self.save()
        except Exception:
            self.profiles = backup_profiles
            self.default_associations = backup_defaults
            self.game_associations = backup_games
            raise

    def load(self):
        """Load profiles and associations from app-owned JSON configuration.

        Enforces byte limit check and raises explicit LaunchProfileError on malformed config.
        """
        if not self.config_path.exists():
            return
        if not self.config_path.is_file():
            raise LaunchProfileError(f"Config path '{self.config_path}' is not a regular file")

        file_size = self.config_path.stat().st_size
        if file_size > MAX_PROFILE_CONFIG_BYTES:
            raise LaunchProfileError(
                f"Configuration file exceeds byte limit ({file_size} > {MAX_PROFILE_CONFIG_BYTES} bytes)"
            )

        with open(self.config_path, 'rb') as f:
            raw = f.read(MAX_PROFILE_CONFIG_BYTES + 1)
        if len(raw) > MAX_PROFILE_CONFIG_BYTES:
            raise LaunchProfileError(
                f"Configuration file exceeds byte limit ({len(raw)} > {MAX_PROFILE_CONFIG_BYTES} bytes)"
            )

        try:
            data = json.loads(raw.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LaunchProfileError(f"Malformed launch profile configuration JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise LaunchProfileError("Malformed launch profile configuration: root must be a JSON object")

        raw_profiles = data.get('profiles', [])
        if not isinstance(raw_profiles, list):
            raise LaunchProfileError("Malformed launch profile configuration: 'profiles' must be a list")

        profiles: dict[str, LaunchProfile] = {}
        for p_data in raw_profiles:
            if not isinstance(p_data, dict):
                raise LaunchProfileError("Malformed profile entry: expected JSON object")
            prof = LaunchProfile.from_dict(p_data)
            profiles[prof.profile_id] = prof

        raw_defaults = data.get('default_associations', {})
        if not isinstance(raw_defaults, dict):
            raise LaunchProfileError("Malformed launch profile configuration: 'default_associations' must be a dict")
        default_associations: dict[str, str] = {}
        for k, v in raw_defaults.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise LaunchProfileError("Malformed default_associations: keys and values must be strings")
            default_associations[k] = v

        raw_games = data.get('game_associations', {})
        if not isinstance(raw_games, dict):
            raise LaunchProfileError("Malformed launch profile configuration: 'game_associations' must be a dict")
        game_associations: dict[str, str] = {}
        for k, v in raw_games.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise LaunchProfileError("Malformed game_associations: keys and values must be strings")
            game_associations[k] = v

        self.profiles = profiles
        self.default_associations = default_associations
        self.game_associations = game_associations

    def save(self):
        """Atomically persist profiles and associations to disk with fsync."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            'kind': 'launch-profiles-v1',
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'profiles': [p.to_dict() for p in self.profiles.values()],
            'default_associations': self.default_associations,
            'game_associations': self.game_associations,
        }
        raw = (json.dumps(payload, indent=2, sort_keys=True) + '\n').encode('utf-8')
        if len(raw) > MAX_PROFILE_CONFIG_BYTES:
            raise LaunchProfileError('Profile configuration exceeds maximum byte cap')

        tmp_name = f'.txn-{uuid.uuid4().hex}'
        tmp_path = self.config_dir / tmp_name
        try:
            with open(tmp_path, 'wb') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.config_path)
            # Sync parent directory
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

    def create_profile(self, profile: LaunchProfile) -> bool:
        if profile.profile_id in self.profiles:
            raise LaunchProfileError(f"Profile with ID '{profile.profile_id}' already exists")
        with self._transaction():
            self.profiles[profile.profile_id] = profile
            if profile.is_default:
                self.default_associations[profile.platform_or_provider] = profile.profile_id
        return True

    def update_profile(self, profile: LaunchProfile) -> bool:
        if profile.profile_id not in self.profiles:
            raise LaunchProfileError(f"Profile with ID '{profile.profile_id}' does not exist")
        with self._transaction():
            self.profiles[profile.profile_id] = profile
            if profile.is_default:
                self.default_associations[profile.platform_or_provider] = profile.profile_id
        return True

    def delete_profile(self, profile_id: str) -> bool:
        if profile_id not in self.profiles:
            return False
        with self._transaction():
            prof = self.profiles.pop(profile_id)
            if self.default_associations.get(prof.platform_or_provider) == profile_id:
                self.default_associations.pop(prof.platform_or_provider, None)
            # Clear any specific game overrides mapped to this profile
            to_del = [gid for gid, pid in self.game_associations.items() if pid == profile_id]
            for gid in to_del:
                self.game_associations.pop(gid, None)
        return True

    def get_profile(self, profile_id: str) -> LaunchProfile | None:
        return self.profiles.get(profile_id)

    def list_profiles(
        self,
        *,
        target_type: str | None = None,
        platform_or_provider: str | None = None,
    ) -> list[LaunchProfile]:
        res = list(self.profiles.values())
        if target_type:
            res = [p for p in res if p.target_type == target_type]
        if platform_or_provider:
            res = [p for p in res if p.platform_or_provider == platform_or_provider or platform_or_provider in p.supported_platforms]
        return res

    def set_default_profile(self, platform_or_provider: str, profile_id: str):
        if profile_id not in self.profiles:
            raise LaunchProfileError(f"Cannot set unknown profile '{profile_id}' as default")
        with self._transaction():
            self.default_associations[platform_or_provider] = profile_id

    def set_game_override(self, target_id: str, profile_id: str):
        if profile_id not in self.profiles:
            raise LaunchProfileError(f"Cannot associate unknown profile '{profile_id}'")
        with self._transaction():
            self.game_associations[target_id] = profile_id

    def clear_game_override(self, target_id: str):
        if target_id in self.game_associations:
            with self._transaction():
                self.game_associations.pop(target_id, None)

    def resolve_profiles(
        self,
        target_id: str,
        platform_or_provider: str,
        target_type: str = 'rom',
    ) -> tuple[LaunchProfile | None, list[LaunchProfile]]:
        """Resolve the effective default profile and all matching alternative profiles.

        Precedence:
        1. Exact game-specific association (target_id) matching target_type
        2. Configured platform/provider default matching target_type
        3. Profile marked with is_default=True matching target_type
        4. First compatible candidate matching target_type
        """
        candidates = self.list_profiles(target_type=target_type, platform_or_provider=platform_or_provider)
        if not candidates:
            # Fall back to general match by platform_or_provider, strictly filtering by target_type
            candidates = [
                p for p in self.profiles.values()
                if p.target_type == target_type and (
                    p.platform_or_provider == platform_or_provider
                    or platform_or_provider in p.supported_platforms
                )
            ]

        default_prof: LaunchProfile | None = None

        # 1. Exact game override (must match target_type)
        if target_id in self.game_associations:
            override_prof = self.profiles.get(self.game_associations[target_id])
            if override_prof and override_prof.target_type == target_type:
                default_prof = override_prof

        # 2. Configured default association (must match target_type)
        if default_prof is None and platform_or_provider in self.default_associations:
            configured_default = self.profiles.get(self.default_associations[platform_or_provider])
            if configured_default and configured_default.target_type == target_type:
                default_prof = configured_default

        # 3. Profile flag is_default (within compatible candidates)
        if default_prof is None:
            default_prof = next((p for p in candidates if p.is_default and p.target_type == target_type), None)

        # 4. First available candidate
        if default_prof is None and candidates:
            default_prof = candidates[0]

        # If an override was selected and is not already in candidates, prepend it
        if default_prof is not None and default_prof not in candidates and default_prof.target_type == target_type:
            candidates.insert(0, default_prof)

        return default_prof, candidates

    def build_launch_intent(
        self,
        target_id: str = '',
        platform_or_provider: str = '',
        *,
        target_path: str | None = None,
        target_type: str = 'rom',
        profile_id: str | None = None,
        current_host: str | None = None,
    ) -> LaunchIntent:
        """Construct a validated LaunchIntent without starting any process."""
        if not target_id:
            raise LaunchProfileError("target_id is required to build launch intent")

        if profile_id:
            profile = self.get_profile(profile_id)
            if not profile:
                return LaunchIntent(
                    profile_id=profile_id,
                    target_id=target_id,
                    target_path=target_path,
                    executable='',
                    argv=(),
                    cwd=None,
                    env={},
                    target_host=None,
                    verified_contract=False,
                    error=f"Requested profile '{profile_id}' not found in store",
                )
            if profile.target_type != target_type:
                return LaunchIntent(
                    profile_id=profile_id,
                    target_id=target_id,
                    target_path=target_path,
                    executable=profile.executable,
                    argv=(),
                    cwd=profile.cwd,
                    env=dict(profile.env),
                    target_host=profile.target_host,
                    verified_contract=False,
                    error=f"Profile '{profile_id}' target_type '{profile.target_type}' is incompatible with requested '{target_type}'",
                )
        else:
            profile, _ = self.resolve_profiles(target_id, platform_or_provider, target_type)
            if not profile:
                return LaunchIntent(
                    profile_id='',
                    target_id=target_id,
                    target_path=target_path,
                    executable='',
                    argv=(),
                    cwd=None,
                    env={},
                    target_host=None,
                    verified_contract=False,
                    error=f"No launch profile available for target '{target_id}' ({platform_or_provider})",
                )

        # This built-in URI is Lutris' numeric games.id contract, not a
        # Battle.net product code (for example Pro/W3) or a Playnite GUID.
        # Fail closed instead of constructing a syntactically valid but wrong
        # rungameid URI for product.db-only observations.
        if (profile.profile_id == 'native-lutris-battlenet-game'
                and not re.fullmatch(r'[1-9][0-9]{0,11}', str(target_id))):
            return LaunchIntent(
                profile_id=profile.profile_id, target_id=target_id,
                target_path=target_path, executable=profile.executable,
                argv=(), cwd=profile.cwd, env=dict(profile.env),
                target_host=profile.target_host, verified_contract=False,
                error='Lutris Battle.net launch requires its exact numeric Lutris game row ID',
            )

        # Existing installs may have seeded a stale fixed location (for example
        # ~/.local/bin/steam) before the command was on PATH. Refresh only the
        # known app-owned seed records, and only while their executable basename
        # still matches the reviewed command; explicit user-edited paths survive.
        seeded_name = _SEEDED_DEFAULT_COMMANDS.get(profile.profile_id)
        executable_path = Path(profile.executable)
        known_seed_locations = {Path.home() / '.local' / 'bin', Path('/usr/bin'),
                                Path('/usr/local/bin'), Path('/bin')}
        if (profile.is_app_owned and seeded_name and executable_path.name == seeded_name
                and (not executable_path.is_absolute() or executable_path.parent in known_seed_locations)):
            resolved = _default_executable(seeded_name)
            if resolved != profile.executable:
                profile = replace(profile, executable=resolved)

        try:
            tokens = substitute_tokens(
                profile.argv_template,
                target_id=target_id,
                target_path=target_path,
                executable=profile.executable,
            )
            if tokens and tokens[0] == profile.executable:
                argv = tokens
            else:
                argv = (profile.executable,) + tokens
        except LaunchProfileError as exc:
            return LaunchIntent(
                profile_id=profile.profile_id,
                target_id=target_id,
                target_path=target_path,
                executable=profile.executable,
                argv=(),
                cwd=profile.cwd,
                env=dict(profile.env),
                target_host=profile.target_host,
                verified_contract=False,
                error=str(exc),
            )

        intent = LaunchIntent(
            profile_id=profile.profile_id,
            target_id=target_id,
            target_path=target_path,
            executable=profile.executable,
            argv=argv,
            cwd=profile.cwd,
            env=dict(profile.env),
            target_host=profile.target_host,
            verified_contract=True,
            error=None,
        )

        valid, reason = recheck_launch_intent(intent, current_host=current_host)
        if not valid:
            return LaunchIntent(
                profile_id=profile.profile_id,
                target_id=target_id,
                target_path=target_path,
                executable=profile.executable,
                argv=argv,
                cwd=profile.cwd,
                env=dict(profile.env),
                target_host=profile.target_host,
                verified_contract=False,
                error=reason,
            )

        return intent

    # Public alias to build_launch_intent
    build_intent = build_launch_intent

    def play(
        self,
        target_id: str = '',
        platform_or_provider: str = '',
        *,
        target_path: str | None = None,
        target_type: str = 'rom',
        profile_id: str | None = None,
        current_host: str | None = None,
        spawn=None,
    ) -> LaunchResult:
        """Minimal Play API: builds validated intent and executes explicit handoff.

        Never runs during scans. Injectable spawn for testing.
        """
        intent = self.build_launch_intent(
            target_id=target_id,
            platform_or_provider=platform_or_provider,
            target_path=target_path,
            target_type=target_type,
            profile_id=profile_id,
            current_host=current_host,
        )
        return launch(intent, current_host=current_host, spawn=spawn)

    def seed_reviewed_recipes(self) -> int:
        """Seed reviewed native launcher and discovered emulator recipes into store.

        Only populates store if profile_id does not already exist. Never writes to
        launcher configs or host OS configuration.
        """
        recipes = [
            # Native Launchers (from launcher_connections.py contracts)
            LaunchProfile(
                profile_id='native-steam',
                display_name='Steam Native Client',
                target_type='native_game',
                platform_or_provider='steam',
                executable=_default_executable('steam'),
                argv_template=('-silent', 'steam://rungameid/{game_id}'),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('steam',),
            ),
            LaunchProfile(
                profile_id='native-heroic-epic',
                display_name='Heroic Games Launcher (Legendary)',
                target_type='native_game',
                platform_or_provider='heroic-epic',
                executable='/opt/Heroic/heroic',
                argv_template=('heroic://launch?appName={game_id}&runner=legendary',),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('heroic-epic',),
            ),
            LaunchProfile(
                profile_id='native-heroic-sideload',
                display_name='Heroic Sideload',
                target_type='native_game',
                platform_or_provider='heroic-sideload',
                executable=_default_executable('heroic'),
                argv_template=('heroic://launch?appName={game_id}&runner=sideload',),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('heroic-sideload',),
            ),
            LaunchProfile(
                profile_id='native-xodus',
                display_name='Xodus Nexus Client',
                target_type='native_game',
                platform_or_provider='xodus',
                executable=_default_executable('xodus-nexus'),
                argv_template=('--launch-game', '{game_id}'),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('xodus',),
            ),
            LaunchProfile(
                profile_id='native-lutris-battlenet',
                display_name='Lutris Battle.net Client',
                target_type='native_game',
                platform_or_provider='lutris-battlenet',
                executable='/usr/bin/lutris',
                argv_template=('lutris:rungameid/2',),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('lutris-battlenet',),
            ),
            # Lutris writes these rows after importing the corresponding store
            # client. Its documented rungameid URI takes the numeric games.id;
            # the provider product/service ID is deliberately not substituted.
            LaunchProfile(
                profile_id='native-lutris-ea', display_name='Lutris (EA app)',
                target_type='native_game', platform_or_provider='ea app',
                executable=_default_executable('lutris'),
                argv_template=('lutris:rungameid/{game_id}',),
                target_host=_DEFAULT_TARGET_HOST, is_default=True,
                supported_platforms=('ea app',),
            ),
            LaunchProfile(
                profile_id='native-lutris-ubisoft', display_name='Lutris (Ubisoft Connect)',
                target_type='native_game', platform_or_provider='ubisoft connect',
                executable=_default_executable('lutris'),
                argv_template=('lutris:rungameid/{game_id}',),
                target_host=_DEFAULT_TARGET_HOST, is_default=True,
                supported_platforms=('ubisoft connect',),
            ),
            LaunchProfile(
                profile_id='native-lutris-battlenet-game', display_name='Lutris (Battle.net game)',
                target_type='native_game', platform_or_provider='battle.net',
                executable=_default_executable('lutris'),
                argv_template=('lutris:rungameid/{game_id}',),
                target_host=_DEFAULT_TARGET_HOST, is_default=True,
                supported_platforms=('battle.net',),
            ),
            # Discovered Linux Emulators
            LaunchProfile(
                profile_id='emu-lichii',
                display_name='Lichii (Switch / Switch 2)',
                target_type='rom',
                platform_or_provider='switch2',
                executable=_default_executable('lichii'),
                argv_template=('{rom}',),
                env={'QT_QPA_PLATFORM': 'xcb'},
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('switch2', 'switch'),
                supported_extensions=('.nup', '.nsp', '.xci', '.nro'),
            ),
            LaunchProfile(
                profile_id='emu-eden',
                display_name='Eden (Switch)',
                target_type='rom',
                platform_or_provider='switch',
                executable=_default_executable('eden'),
                argv_template=('-f', '-g', '{rom}'),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('switch',),
                supported_extensions=('.nsp', '.xci', '.nro'),
            ),
            LaunchProfile(
                profile_id='emu-dolphin',
                display_name='Dolphin Emulator (GameCube & Wii)',
                target_type='rom',
                platform_or_provider='gamecube',
                executable=_default_executable('dolphin-emu'),
                argv_template=('-b', '-e', '{rom}'),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('gamecube', 'wii'),
                supported_extensions=('.rvz', '.wbfs', '.iso', '.gcm'),
            ),
            LaunchProfile(
                profile_id='emu-cemu',
                display_name='Cemu Wii U Emulator',
                target_type='rom',
                platform_or_provider='wiiu',
                executable=_default_executable('cemu'),
                argv_template=('-f', '-g', '{rom}'),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('wiiu',),
                supported_extensions=('.rpx', '.wux', '.wud'),
            ),
            LaunchProfile(
                profile_id='emu-rpcs3',
                display_name='RPCS3 PlayStation 3 Emulator',
                target_type='rom',
                platform_or_provider='ps3',
                executable=_default_executable('rpcs3'),
                argv_template=('--no-gui', '{rom}'),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('ps3',),
                supported_extensions=('.bin', '.iso'),
            ),
            LaunchProfile(
                profile_id='emu-retroarch',
                display_name='RetroArch Multi-System Emulator',
                target_type='rom',
                platform_or_provider='n64',
                executable=_default_executable('retroarch'),
                argv_template=('{rom}',),
                target_host=_DEFAULT_TARGET_HOST,
                is_default=True,
                supported_platforms=('n64', '3ds', 'nds', 'gba', 'gbc', 'gb', 'nes', 'snes', 'megadrive', 'psx', 'nintendo_multi'),
                supported_extensions=('.z64', '.v64', '.n64', '.3ds', '.cia', '.nds', '.gba', '.gbc', '.gb', '.nes', '.sfc', '.smc', '.chd', '.pbp', '.cue', '.bin', '.gen', '.smd', '.zip', '.7z'),
            ),
        ]

        to_add = [rec for rec in recipes if rec.profile_id not in self.profiles]
        if not to_add:
            return 0

        with self._transaction():
            for rec in to_add:
                self.profiles[rec.profile_id] = rec
                if rec.is_default and rec.platform_or_provider not in self.default_associations:
                    self.default_associations[rec.platform_or_provider] = rec.profile_id
        return len(to_add)


def build_launch_intent(
    store: LaunchProfileStore,
    target_id: str = '',
    platform_or_provider: str = '',
    *,
    target_path: str | None = None,
    target_type: str = 'rom',
    profile_id: str | None = None,
    current_host: str | None = None,
) -> LaunchIntent:
    """Convenience module function to build launch intent via store."""
    return store.build_launch_intent(
        target_id=target_id,
        platform_or_provider=platform_or_provider,
        target_path=target_path,
        target_type=target_type,
        profile_id=profile_id,
        current_host=current_host,
    )


build_intent = build_launch_intent


def play(
    store: LaunchProfileStore,
    target_id: str = '',
    platform_or_provider: str = '',
    *,
    target_path: str | None = None,
    target_type: str = 'rom',
    profile_id: str | None = None,
    current_host: str | None = None,
    spawn=None,
) -> LaunchResult:
    """Minimal Play convenience function for UI/coordinator integration."""
    return store.play(
        target_id=target_id,
        platform_or_provider=platform_or_provider,
        target_path=target_path,
        target_type=target_type,
        profile_id=profile_id,
        current_host=current_host,
        spawn=spawn,
    )

