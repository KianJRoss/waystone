"""App-owned native mod profiles and loader chains management.

Provides verified profile inventory, game installation discovery,
and integration with shared LaunchProfileStore for modded execution chains.
Supports:
- Satisfactory Mod Manager (SMM / SML v3) via profiles.json / installations.json / .uplugin
- r2modman / Thunderstore profiles via mods.yml (ebkr/r2modmanPlus schema)
- Skyrim Script Extender (SKSE) loader chains with explicit Proton / Wine runner kinds
- Honest Vortex / Mod Organizer 2 (MO2) interface gap reporting

Constraints:
- ZERO dependency solvers or downloaders
- ZERO unsafe deployment / mod file mutation
- ZERO live game launches or emulator launches
- Strict non-shell execution with validation
- Windows loaders (e.g. skse64_loader.exe) are never executed directly on Linux.
- Wine and Proton are distinct CLIs: ('run', loader) is Proton-only; Wine takes (loader,).
- STEAM_COMPAT_DATA_PATH is NEVER guessed from wine_prefix.parent.parent. An explicit
  compat_data_path (compatdata/APPID) is required for Proton runners.
- Target binding keys must match LaunchView resolution (product_id / variant_key),
  preserving unmodded associations. Steam URI commands require raw numeric product_ids.
- Profile inventory is NOT proof of filesystem deployment; activation without deployed
  binaries is rejected with explicit unsupported error.
- PyYAML safe_load or strict JSON fallback only; no heuristic/handmade YAML guessing.
- Vortex is open source; reported as no supported headless contract verified in this pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None

from native_services.launch_profiles import (
    LaunchProfile,
    LaunchProfileStore,
    LaunchProfileError,
)

SUPPORTED_MANAGERS = frozenset({'satisfactory', 'r2modman', 'skse', 'vortex', 'mo2', 'waystone', 'custom'})
SUPPORTED_RUNNER_KINDS = frozenset({'proton', 'wine'})


class ModManagementError(ValueError):
    """Raised for mod management validation, profile corruption, or unsafe launch configurations."""


def _safe_str(val: Any, max_len: int = 512) -> str:
    if not isinstance(val, str):
        return ''
    clean = val.strip().replace('\x00', '')
    return clean[:max_len]


def _safe_yaml_load(text: str) -> Any:
    """Parse YAML text using PyYAML safe_load or strict JSON fallback.
    
    Heuristic/guessed YAML parsers are prohibited.
    """
    clean = text.strip()
    if yaml is not None:
        try:
            return yaml.safe_load(clean)
        except Exception as exc:
            raise ModManagementError(f"Failed to parse YAML: {exc}") from exc

    # If PyYAML is not installed, accept strict JSON (valid YAML 1.2 subset)
    if clean.startswith('[') or clean.startswith('{'):
        try:
            return json.loads(clean)
        except Exception as exc:
            raise ModManagementError(f"Failed to parse JSON-formatted YAML: {exc}") from exc

    raise ModManagementError(
        "PyYAML is required to parse YAML-formatted mods.yml. "
        "Handmade heuristic fallback parsers are prohibited; please ensure 'pyyaml' is available."
    )


@dataclass(frozen=True)
class ModItem:
    mod_id: str
    name: str
    version: str
    enabled: bool
    author: str = ''
    description: str = ''
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    source_file: str = ''
    source_line: Optional[int] = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    category: str = ''
    source_origin: str = 'local'
    installed_files: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModProfile:
    profile_id: str
    display_name: str
    manager_type: str  # satisfactory, r2modman, skse, vortex, mo2
    game_id: str       # canonical game ID (e.g. 'satisfactory', 'ror2', 'skyrim-se')
    game_title: str
    install_directory: str
    profile_directory: Optional[str] = None      # distinct manager profile directory
    target_binding_key: Optional[str] = None     # exact product_id or variant_key matching LaunchView
    variant_key: Optional[str] = None            # e.g. 'steam:526870'
    mods: tuple[ModItem, ...] = field(default_factory=tuple)
    loader_executable: Optional[str] = None
    loader_argv_template: tuple[str, ...] = field(default_factory=tuple)
    runner_kind: Optional[str] = None            # 'proton' or 'wine' (explicit requirement)
    compat_data_path: Optional[str] = None       # explicit compatdata/APPID path for Proton
    wine_prefix: Optional[str] = None            # path to WINEPREFIX
    proton_path: Optional[str] = None            # path to runner binary (proton or wine)
    env: dict[str, str] = field(default_factory=dict)
    source_provenance: dict[str, Any] = field(default_factory=dict)
    isolate_saves: bool = False
    save_directory: Optional[str] = None
    notes: str = ''
    created_at: str = ''
    updated_at: str = ''


class ModManagerService:
    """Manages verified mod profiles, loader chains, and launch profile generation."""

    def __init__(
        self,
        config_path: Path | str,
        *,
        launch_profile_store: Optional[LaunchProfileStore] = None,
    ) -> None:
        self.config_path = Path(config_path).resolve()
        self.launch_profile_store = launch_profile_store
        self.profiles: Dict[str, ModProfile] = {}
        self.game_bindings: Dict[str, str] = {}           # target_key -> profile_id
        self.unmodded_bindings: Dict[str, str] = {}       # target_key -> original_profile_id
        self.load()

    # --- Persistence ---

    def load(self) -> None:
        """Atomically read configured mod profiles from storage."""
        if not self.config_path.exists():
            return

        try:
            raw = self.config_path.read_text(encoding='utf-8')
            if not raw.strip():
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ModManagementError("Corrupt mod profile store: root must be a JSON object")

            self.game_bindings = data.get('game_bindings', {})
            self.unmodded_bindings = data.get('unmodded_bindings', {})

            profiles_raw = data.get('profiles', {})
            loaded: Dict[str, ModProfile] = {}
            for pid, pdata in profiles_raw.items():
                mods = tuple(
                    ModItem(
                        mod_id=m['mod_id'],
                        name=m['name'],
                        version=m['version'],
                        enabled=bool(m['enabled']),
                        author=m.get('author', ''),
                        description=m.get('description', ''),
                        dependencies=tuple(m.get('dependencies', ())),
                        source_file=m.get('source_file', ''),
                        source_line=m.get('source_line'),
                        extra_metadata=m.get('extra_metadata', {}),
                        priority=m.get('priority', idx),
                        category=m.get('category', ''),
                        source_origin=m.get('source_origin', 'local'),
                        installed_files=tuple(m.get('installed_files', ())),
                    )
                    for idx, m in enumerate(pdata.get('mods', []))
                )
                loaded[pid] = ModProfile(
                    profile_id=pdata['profile_id'],
                    display_name=pdata['display_name'],
                    manager_type=pdata['manager_type'],
                    game_id=pdata['game_id'],
                    game_title=pdata.get('game_title', pdata['game_id']),
                    install_directory=pdata['install_directory'],
                    profile_directory=pdata.get('profile_directory'),
                    target_binding_key=pdata.get('target_binding_key'),
                    variant_key=pdata.get('variant_key'),
                    mods=mods,
                    loader_executable=pdata.get('loader_executable'),
                    loader_argv_template=tuple(pdata.get('loader_argv_template', ())),
                    runner_kind=pdata.get('runner_kind'),
                    compat_data_path=pdata.get('compat_data_path'),
                    wine_prefix=pdata.get('wine_prefix'),
                    proton_path=pdata.get('proton_path'),
                    env=pdata.get('env', {}),
                    source_provenance=pdata.get('source_provenance', {}),
                    isolate_saves=bool(pdata.get('isolate_saves', False)),
                    save_directory=pdata.get('save_directory'),
                    notes=pdata.get('notes', ''),
                    created_at=pdata.get('created_at', ''),
                    updated_at=pdata.get('updated_at', ''),
                )
            self.profiles = loaded
        except json.JSONDecodeError as exc:
            raise ModManagementError(f"Invalid JSON in mod store: {exc}") from exc

    def save(self) -> None:
        """Atomically persist mod profiles to disk via tempfile replace."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            'version': 2,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'game_bindings': self.game_bindings,
            'unmodded_bindings': self.unmodded_bindings,
            'profiles': {
                pid: {
                    'profile_id': p.profile_id,
                    'display_name': p.display_name,
                    'manager_type': p.manager_type,
                    'game_id': p.game_id,
                    'game_title': p.game_title,
                    'install_directory': p.install_directory,
                    'profile_directory': p.profile_directory,
                    'target_binding_key': p.target_binding_key,
                    'variant_key': p.variant_key,
                    'loader_executable': p.loader_executable,
                    'loader_argv_template': list(p.loader_argv_template),
                    'runner_kind': p.runner_kind,
                    'compat_data_path': p.compat_data_path,
                    'wine_prefix': p.wine_prefix,
                    'proton_path': p.proton_path,
                    'env': p.env,
                    'source_provenance': p.source_provenance,
                    'created_at': p.created_at,
                    'updated_at': p.updated_at,
                    'isolate_saves': p.isolate_saves,
                    'save_directory': p.save_directory,
                    'notes': p.notes,
                    'mods': [
                        {
                            'mod_id': m.mod_id,
                            'name': m.name,
                            'version': m.version,
                            'enabled': m.enabled,
                            'author': m.author,
                            'description': m.description,
                            'dependencies': list(m.dependencies),
                            'source_file': m.source_file,
                            'source_line': m.source_line,
                            'extra_metadata': m.extra_metadata,
                            'priority': m.priority,
                            'category': m.category,
                            'source_origin': m.source_origin,
                            'installed_files': list(m.installed_files),
                        }
                        for m in p.mods
                    ],
                }
                for pid, p in self.profiles.items()
            },
        }

        tmp_path = self.config_path.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:6]}")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.config_path)

    # --- External Manager Manifest Parsers ---

    def parse_satisfactory_manifest(
        self,
        *,
        canonical_game_id: str,
        install_directory: Path | str,
        profile_directory: Optional[Path | str] = None,
        profile_name: Optional[str] = None,
        game_title: str = 'Satisfactory',
        target_binding_key: Optional[str] = None,
        variant_key: Optional[str] = None,
    ) -> ModProfile:
        """Inspect Satisfactory Mod Manager (SMM) state and/or .uplugin manifests."""
        if not canonical_game_id:
            raise ModManagementError("canonical_game_id is required; never route on hardcoded IDs")

        inst_dir = Path(install_directory).resolve()
        if not inst_dir.exists() or not inst_dir.is_dir():
            raise ModManagementError(f"Satisfactory install directory does not exist: {inst_dir}")

        mods: List[ModItem] = []
        provenance: Dict[str, Any] = {'canonical_game_id': canonical_game_id, 'install_directory': str(inst_dir)}
        selected_prof_name = profile_name

        if profile_directory is not None:
            p_dir = Path(profile_directory).resolve()
            if not p_dir.exists() or not p_dir.is_dir():
                raise ModManagementError(f"Satisfactory profile directory not found: {p_dir}")

            prof_json_file = p_dir / 'profiles.json'
            inst_json_file = p_dir / 'installations.json'

            if not prof_json_file.exists():
                raise ModManagementError(f"Satisfactory profile configuration missing: {prof_json_file}")
            if not inst_json_file.exists():
                raise ModManagementError(f"Satisfactory installations configuration missing: {inst_json_file}")

            try:
                prof_data = json.loads(prof_json_file.read_text(encoding='utf-8'))
            except Exception as exc:
                raise ModManagementError(f"Failed to parse {prof_json_file}: {exc}") from exc

            try:
                inst_data = json.loads(inst_json_file.read_text(encoding='utf-8'))
            except Exception as exc:
                raise ModManagementError(f"Failed to parse {inst_json_file}: {exc}") from exc

            if not isinstance(prof_data, dict) or 'profiles' not in prof_data:
                raise ModManagementError(f"Invalid profiles.json schema in {prof_json_file}: missing 'profiles'")
            if not isinstance(inst_data, dict) or 'installations' not in inst_data:
                raise ModManagementError(f"Invalid installations.json schema in {inst_json_file}: missing 'installations'")

            if not selected_prof_name:
                matched_profile = None
                for inst_entry in inst_data['installations']:
                    if not isinstance(inst_entry, dict) or 'path' not in inst_entry:
                        continue
                    if Path(inst_entry['path']).resolve() == inst_dir:
                        matched_profile = inst_entry.get('profile')
                        break
                selected_prof_name = matched_profile or prof_data.get('selected_profile')

            if not selected_prof_name or selected_prof_name not in prof_data['profiles']:
                raise ModManagementError(
                    f"Selected profile '{selected_prof_name}' not found in {prof_json_file}"
                )

            target_prof = prof_data['profiles'][selected_prof_name]
            mods_dict = target_prof.get('mods', {})
            if not isinstance(mods_dict, dict):
                raise ModManagementError(f"Profile '{selected_prof_name}' mods field is not an object")

            for mod_ref, mod_entry in mods_dict.items():
                if not isinstance(mod_entry, dict):
                    raise ModManagementError(f"Mod entry '{mod_ref}' is not an object in {prof_json_file}")
                if 'version' not in mod_entry:
                    raise ModManagementError(f"Mod entry '{mod_ref}' missing 'version' in {prof_json_file}")
                if 'enabled' not in mod_entry or not isinstance(mod_entry['enabled'], bool):
                    raise ModManagementError(f"Mod entry '{mod_ref}' missing boolean 'enabled' in {prof_json_file}")

                mods.append(ModItem(
                    mod_id=mod_ref,
                    name=mod_ref,
                    version=str(mod_entry['version']),
                    enabled=bool(mod_entry['enabled']),
                    source_file=str(prof_json_file),
                    extra_metadata={'ficsit_profile': selected_prof_name},
                ))

            provenance['smm_profiles_json'] = str(prof_json_file)
            provenance['smm_installations_json'] = str(inst_json_file)
            provenance['selected_profile'] = selected_prof_name

        game_mods_dir = inst_dir / 'FactoryGame' / 'Mods'
        if game_mods_dir.exists() and game_mods_dir.is_dir():
            for item in sorted(game_mods_dir.iterdir()):
                if item.is_dir() and not item.name.startswith('.'):
                    uplugins = list(item.glob('*.uplugin'))
                    if not uplugins:
                        continue
                    up_file = uplugins[0]
                    try:
                        up_data = json.loads(up_file.read_text(encoding='utf-8'))
                    except Exception as exc:
                        raise ModManagementError(f"Failed to parse Unreal plugin descriptor {up_file}: {exc}") from exc

                    if not isinstance(up_data, dict):
                        raise ModManagementError(f"Plugin descriptor {up_file} is not a valid JSON object")

                    mod_id = item.name
                    version = up_data.get('SemVersion') or up_data.get('VersionName')
                    if not version:
                        raise ModManagementError(f"Plugin descriptor {up_file} missing 'SemVersion' or 'VersionName'")

                    if not any(m.mod_id == mod_id for m in mods):
                        mods.append(ModItem(
                            mod_id=mod_id,
                            name=up_data.get('FriendlyName', mod_id),
                            version=str(version),
                            enabled=True,
                            author=up_data.get('CreatedBy', ''),
                            description=up_data.get('Description', ''),
                            source_file=str(up_file),
                            extra_metadata={'uplugin_path': str(up_file)},
                        ))

        if not mods and profile_directory is None and not (game_mods_dir.exists()):
            raise ModManagementError(f"No valid SMM configuration or FactoryGame/Mods found for {inst_dir}")

        profile_id = f"smm-{uuid.uuid4().hex[:8]}"

        return ModProfile(
            profile_id=profile_id,
            display_name=selected_prof_name or f"Satisfactory Modded ({inst_dir.name})",
            manager_type='satisfactory',
            game_id=canonical_game_id,
            game_title=game_title,
            install_directory=str(inst_dir),
            profile_directory=str(profile_directory) if profile_directory else None,
            target_binding_key=target_binding_key,
            variant_key=variant_key,
            mods=tuple(mods),
            loader_executable=None,
            source_provenance=provenance,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def parse_r2modman_profile(
        self,
        *,
        canonical_game_id: str,
        install_directory: Path | str,
        profile_directory: Path | str,
        game_title: str,
        target_binding_key: Optional[str] = None,
        variant_key: Optional[str] = None,
    ) -> ModProfile:
        """Inspect an r2modman profile directory using upstream mods.yml schema (ebkr/r2modmanPlus)."""
        if not canonical_game_id:
            raise ModManagementError("canonical_game_id is required; never route on hardcoded IDs")

        inst_dir = Path(install_directory).resolve()
        if not inst_dir.exists() or not inst_dir.is_dir():
            raise ModManagementError(f"Game install directory not found: {inst_dir}")

        p_dir = Path(profile_directory).resolve()
        if not p_dir.exists() or not p_dir.is_dir():
            raise ModManagementError(f"r2modman profile directory not found: {p_dir}")

        mods_yml_file = p_dir / 'mods.yml'
        if not mods_yml_file.exists():
            raise ModManagementError(
                f"r2modman profile missing mandatory mods.yml: {mods_yml_file}. "
                "Upstream r2modman uses mods.yml as the local registry source of truth."
            )

        raw_yaml = mods_yml_file.read_text(encoding='utf-8')
        parsed = _safe_yaml_load(raw_yaml)

        if not isinstance(parsed, list):
            raise ModManagementError(f"Invalid mods.yml schema in {mods_yml_file}: root must be a YAML list")

        mods: List[ModItem] = []
        for idx, entry in enumerate(parsed):
            if not isinstance(entry, dict):
                raise ModManagementError(f"Item #{idx} in {mods_yml_file} is not a valid map")

            name = entry.get('name')
            if not name:
                raise ModManagementError(f"Item #{idx} in {mods_yml_file} missing required 'name'")

            if 'enabled' not in entry or not isinstance(entry['enabled'], bool):
                raise ModManagementError(
                    f"Mod '{name}' in {mods_yml_file} is missing mandatory boolean 'enabled' field"
                )

            raw_ver = entry.get('versionNumber')
            if raw_ver is None:
                raise ModManagementError(f"Mod '{name}' in {mods_yml_file} is missing 'versionNumber'")

            if isinstance(raw_ver, dict):
                if 'major' not in raw_ver or 'minor' not in raw_ver or 'patch' not in raw_ver:
                    raise ModManagementError(
                        f"Mod '{name}' in {mods_yml_file} has invalid versionNumber dict: {raw_ver}"
                    )
                version_str = f"{raw_ver['major']}.{raw_ver['minor']}.{raw_ver['patch']}"
            elif isinstance(raw_ver, str):
                version_str = raw_ver
            else:
                raise ModManagementError(
                    f"Mod '{name}' in {mods_yml_file} has unsupported versionNumber type: {type(raw_ver)}"
                )

            mods.append(ModItem(
                mod_id=name,
                name=entry.get('displayName', name),
                version=version_str,
                enabled=entry['enabled'],
                author=entry.get('authorName', ''),
                description=entry.get('description', ''),
                dependencies=tuple(entry.get('dependencies', ())),
                source_file=str(mods_yml_file),
                extra_metadata={
                    'manifestVersion': entry.get('manifestVersion', 2),
                    'websiteUrl': entry.get('websiteUrl', ''),
                },
            ))

        pid = f"r2-{uuid.uuid4().hex[:8]}"
        return ModProfile(
            profile_id=pid,
            display_name=f"r2modman: {p_dir.name}",
            manager_type='r2modman',
            game_id=canonical_game_id,
            game_title=game_title,
            install_directory=str(inst_dir),
            profile_directory=str(p_dir),
            target_binding_key=target_binding_key,
            variant_key=variant_key,
            mods=tuple(mods),
            source_provenance={'mods_yml': str(mods_yml_file), 'item_count': len(mods)},
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    # --- Profile Creation, Binding, and Launch Configuration ---

    def create_or_register_profile(self, profile: ModProfile) -> ModProfile:
        """Register or update a verified mod profile."""
        if not profile.profile_id or not profile.display_name:
            raise ModManagementError("Profile must have non-empty profile_id and display_name")
        if profile.manager_type not in SUPPORTED_MANAGERS:
            raise ModManagementError(
                f"Unsupported manager_type '{profile.manager_type}'. Must be one of {sorted(SUPPORTED_MANAGERS)}"
            )

        self.profiles[profile.profile_id] = profile
        self.save()
        return profile

    def bind_profile_to_game(
        self,
        target_key: str,
        profile_id: str,
        *,
        original_profile_id: Optional[str] = None,
    ) -> None:
        """Bind a verified mod profile to a target (product_id or variant_key matching LaunchView).
        
        Preserves original unmodded association so user can toggle back cleanly.
        """
        if not target_key:
            raise ModManagementError("target_key is required (product_id or variant_key)")
        if profile_id not in self.profiles:
            raise ModManagementError(f"Cannot bind unknown profile_id: '{profile_id}'")

        if original_profile_id:
            self.unmodded_bindings[target_key] = original_profile_id
        elif target_key in self.game_bindings and target_key not in self.unmodded_bindings:
            self.unmodded_bindings[target_key] = self.game_bindings[target_key]

        self.game_bindings[target_key] = profile_id
        self.save()

    def unbind_profile_from_game(self, target_key: str) -> Optional[str]:
        """Unbind mod profile and return previous unmodded profile_id if preserved."""
        if target_key in self.game_bindings:
            del self.game_bindings[target_key]
            restored = self.unmodded_bindings.pop(target_key, None)
            self.save()
            return restored
        return None

    def get_bound_profile_for_game(self, target_key: str) -> Optional[ModProfile]:
        """Return the bound mod profile for a target (product_id or variant_key), if any."""
        pid = self.game_bindings.get(target_key)
        if pid:
            return self.profiles.get(pid)
        return None

    def get_profile(self, profile_id: str) -> Optional[ModProfile]:
        """Retrieve a mod profile by ID."""
        return self.profiles.get(profile_id)

    def list_profiles(self, game_id: Optional[str] = None) -> List[ModProfile]:
        """List registered mod profiles, optionally filtered by canonical game_id."""
        if game_id:
            return [p for p in self.profiles.values() if p.game_id == game_id]
        return list(self.profiles.values())

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a registered mod profile and unbind from games."""
        if profile_id in self.profiles:
            del self.profiles[profile_id]
            to_remove = [k for k, pid in self.game_bindings.items() if pid == profile_id]
            for k in to_remove:
                del self.game_bindings[k]
                self.unmodded_bindings.pop(k, None)
            self.save()
            return True
        return False

    # --- Prism Launcher / Multi-Instance Modpack Engine ---

    def create_instance(
        self,
        game_id: str,
        display_name: str = "",
        install_directory: str = "",
        *,
        name: str = "",
        isolate_saves: bool = False,
        notes: str = "",
        game_title: str = "",
        template_profile_id: Optional[str] = None,
        copy_from_id: Optional[str] = None,
        manager_type: str = "waystone",
    ) -> ModProfile:
        """Create a new isolated modpack instance for a game."""
        eff_name = display_name or name or "Instance"
        eff_template = template_profile_id or copy_from_id
        instance_id = f"{game_id}-{uuid.uuid4().hex[:8]}"
        save_dir = None
        if isolate_saves:
            save_dir = str(Path.home() / ".local/share/waystone/mods/instances" / instance_id / "saves")
            Path(save_dir).mkdir(parents=True, exist_ok=True)

        mods: tuple[ModItem, ...] = ()
        loader = None
        loader_argv: tuple[str, ...] = ()
        runner = None
        compat_path = None
        p_env = {}

        if eff_template and eff_template in self.profiles:
            tmpl = self.profiles[eff_template]
            mods = tmpl.mods
            loader = tmpl.loader_executable
            loader_argv = tmpl.loader_argv_template
            runner = tmpl.runner_kind
            compat_path = tmpl.compat_data_path
            p_env = dict(tmpl.env)
            if not game_title:
                game_title = tmpl.game_title

        now = datetime.now(timezone.utc).isoformat()
        profile = ModProfile(
            profile_id=instance_id,
            display_name=eff_name,
            manager_type=manager_type,
            game_id=game_id,
            game_title=game_title or game_id,
            install_directory=str(install_directory),
            mods=mods,
            loader_executable=loader,
            loader_argv_template=loader_argv,
            runner_kind=runner,
            compat_data_path=compat_path,
            env=p_env,
            isolate_saves=isolate_saves,
            save_directory=save_dir,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        return self.create_or_register_profile(profile)

    def clone_instance(self, profile_id: str, new_display_name: str) -> ModProfile:
        """Clone an existing instance into a new independent modpack."""
        if profile_id not in self.profiles:
            raise ModManagementError(f"Instance '{profile_id}' not found")
        orig = self.profiles[profile_id]
        return self.create_instance(
            game_id=orig.game_id,
            display_name=new_display_name,
            install_directory=orig.install_directory,
            isolate_saves=orig.isolate_saves,
            notes=f"Cloned from {orig.display_name} ({profile_id})",
            game_title=orig.game_title,
            template_profile_id=profile_id,
            manager_type=orig.manager_type,
        )

    def toggle_mod(self, profile_id: str, mod_id: str, enabled: Optional[bool] = None) -> ModProfile:
        """Toggle active state of a mod inside an instance."""
        if profile_id not in self.profiles:
            raise ModManagementError(f"Instance '{profile_id}' not found")
        orig = self.profiles[profile_id]
        updated_mods = []
        found = False
        for m in orig.mods:
            if m.mod_id == mod_id:
                found = True
                new_state = (not m.enabled) if enabled is None else bool(enabled)
                updated_mods.append(ModItem(
                    mod_id=m.mod_id, name=m.name, version=m.version,
                    enabled=new_state, author=m.author, description=m.description,
                    dependencies=m.dependencies, source_file=m.source_file,
                    source_line=m.source_line, extra_metadata=m.extra_metadata,
                    priority=m.priority, category=m.category, source_origin=m.source_origin,
                    installed_files=m.installed_files,
                ))
            else:
                updated_mods.append(m)
        if not found:
            raise ModManagementError(f"Mod '{mod_id}' not found in instance '{profile_id}'")

        now = datetime.now(timezone.utc).isoformat()
        new_prof = ModProfile(
            profile_id=orig.profile_id, display_name=orig.display_name,
            manager_type=orig.manager_type, game_id=orig.game_id,
            game_title=orig.game_title, install_directory=orig.install_directory,
            profile_directory=orig.profile_directory, target_binding_key=orig.target_binding_key,
            variant_key=orig.variant_key, mods=tuple(updated_mods),
            loader_executable=orig.loader_executable, loader_argv_template=orig.loader_argv_template,
            runner_kind=orig.runner_kind, compat_data_path=orig.compat_data_path,
            wine_prefix=orig.wine_prefix, proton_path=orig.proton_path,
            env=orig.env, source_provenance=orig.source_provenance,
            isolate_saves=orig.isolate_saves, save_directory=orig.save_directory,
            notes=orig.notes, created_at=orig.created_at, updated_at=now,
        )
        return self.create_or_register_profile(new_prof)

    def reorder_mods(self, profile_id: str, mod_ids_in_order: list[str]) -> ModProfile:
        """Set load order priority of mods inside an instance."""
        if profile_id not in self.profiles:
            raise ModManagementError(f"Instance '{profile_id}' not found")
        orig = self.profiles[profile_id]
        by_id = {m.mod_id: m for m in orig.mods}
        reordered = []
        for prio, mid in enumerate(mod_ids_in_order):
            if mid in by_id:
                m = by_id.pop(mid)
                reordered.append(ModItem(
                    mod_id=m.mod_id, name=m.name, version=m.version,
                    enabled=m.enabled, author=m.author, description=m.description,
                    dependencies=m.dependencies, source_file=m.source_file,
                    source_line=m.source_line, extra_metadata=m.extra_metadata,
                    priority=prio, category=m.category, source_origin=m.source_origin,
                    installed_files=m.installed_files,
                ))
        base_prio = len(reordered)
        for idx, m in enumerate(by_id.values()):
            reordered.append(ModItem(
                mod_id=m.mod_id, name=m.name, version=m.version,
                enabled=m.enabled, author=m.author, description=m.description,
                dependencies=m.dependencies, source_file=m.source_file,
                source_line=m.source_line, extra_metadata=m.extra_metadata,
                priority=base_prio + idx, category=m.category, source_origin=m.source_origin,
                installed_files=m.installed_files,
            ))
        now = datetime.now(timezone.utc).isoformat()
        new_prof = ModProfile(
            profile_id=orig.profile_id, display_name=orig.display_name,
            manager_type=orig.manager_type, game_id=orig.game_id,
            game_title=orig.game_title, install_directory=orig.install_directory,
            profile_directory=orig.profile_directory, target_binding_key=orig.target_binding_key,
            variant_key=orig.variant_key, mods=tuple(reordered),
            loader_executable=orig.loader_executable, loader_argv_template=orig.loader_argv_template,
            runner_kind=orig.runner_kind, compat_data_path=orig.compat_data_path,
            wine_prefix=orig.wine_prefix, proton_path=orig.proton_path,
            env=orig.env, source_provenance=orig.source_provenance,
            isolate_saves=orig.isolate_saves, save_directory=orig.save_directory,
            notes=orig.notes, created_at=orig.created_at, updated_at=now,
        )
        return self.create_or_register_profile(new_prof)

    def detect_conflicts(self, profile_id: str) -> dict[str, Any]:
        """Calculate visual overwrite matrix and file collisions across mods (MO2 style)."""
        if profile_id not in self.profiles:
            raise ModManagementError(f"Instance '{profile_id}' not found")
        prof = self.profiles[profile_id]
        file_to_mods: dict[str, list[dict[str, Any]]] = {}
        for mod in prof.mods:
            if not mod.enabled:
                continue
            for f in mod.installed_files:
                f_norm = str(Path(f).as_posix()).lower()
                if f_norm not in file_to_mods:
                    file_to_mods[f_norm] = []
                file_to_mods[f_norm].append({
                    "mod_id": mod.mod_id,
                    "name": mod.name,
                    "priority": mod.priority,
                })

        conflicts_by_mod: dict[str, dict[str, list[str]]] = {
            m.mod_id: {"overwrites": [], "overwritten_by": []} for m in prof.mods
        }
        colliding_files: list[dict[str, Any]] = []

        for fpath, claimants in file_to_mods.items():
            if len(claimants) > 1:
                claimants_sorted = sorted(claimants, key=lambda c: c["priority"])
                winner = claimants_sorted[-1]
                loser_names = [c["name"] for c in claimants_sorted[:-1]]
                colliding_files.append({
                    "path": fpath,
                    "winner_mod_id": winner["mod_id"],
                    "winner_name": winner["name"],
                    "overwritten_mods": loser_names,
                })
                conflicts_by_mod[winner["mod_id"]]["overwrites"].extend(loser_names)
                for loser in claimants_sorted[:-1]:
                    conflicts_by_mod[loser["mod_id"]]["overwritten_by"].append(winner["name"])

        for m_id, data in conflicts_by_mod.items():
            data["overwrites"] = sorted(set(data["overwrites"]))
            data["overwritten_by"] = sorted(set(data["overwritten_by"]))

        return {
            "instance_id": profile_id,
            "has_conflicts": len(colliding_files) > 0,
            "collision_count": len(colliding_files),
            "colliding_files": colliding_files,
            "conflicts_by_mod": conflicts_by_mod,
        }

    def export_modpack(self, profile_id: str, output_path: Path | str) -> dict[str, Any]:
        """Export instance metadata as portable .waystonemodpack.json manifest."""
        if profile_id not in self.profiles:
            raise ModManagementError(f"Instance '{profile_id}' not found")
        prof = self.profiles[profile_id]
        manifest = {
            "format": "waystone.modpack.v1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "name": prof.display_name,
            "game_id": prof.game_id,
            "game_title": prof.game_title,
            "notes": prof.notes,
            "loader_executable": prof.loader_executable,
            "runner_kind": prof.runner_kind,
            "env": prof.env,
            "mods": [
                {
                    "mod_id": m.mod_id,
                    "name": m.name,
                    "version": m.version,
                    "enabled": m.enabled,
                    "priority": m.priority,
                    "category": m.category,
                    "source_origin": m.source_origin,
                    "author": m.author,
                    "dependencies": list(m.dependencies),
                }
                for m in prof.mods
            ]
        }
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return manifest

    def import_modpack(self, game_id: str, manifest_path: Path | str, install_directory: str) -> ModProfile:
        """Import a modpack manifest as a new isolated instance."""
        p = Path(manifest_path)
        if not p.is_file():
            raise ModManagementError(f"Modpack manifest '{manifest_path}' not found")
        data = json.loads(p.read_text(encoding="utf-8"))
        name = data.get("name") or p.stem
        mods = tuple(
            ModItem(
                mod_id=m["mod_id"],
                name=m["name"],
                version=m.get("version", "1.0.0"),
                enabled=bool(m.get("enabled", True)),
                priority=m.get("priority", idx),
                category=m.get("category", ""),
                source_origin=m.get("source_origin", "imported"),
                author=m.get("author", ""),
                dependencies=tuple(m.get("dependencies", ())),
            )
            for idx, m in enumerate(data.get("mods", []))
        )
        instance = self.create_instance(
            game_id=game_id,
            display_name=name,
            install_directory=install_directory,
            notes=data.get("notes", f"Imported from {p.name}"),
            game_title=data.get("game_title", game_id),
        )
        now = datetime.now(timezone.utc).isoformat()
        updated = ModProfile(
            profile_id=instance.profile_id, display_name=instance.display_name,
            manager_type=instance.manager_type, game_id=instance.game_id,
            game_title=instance.game_title, install_directory=instance.install_directory,
            mods=mods, loader_executable=data.get("loader_executable"),
            runner_kind=data.get("runner_kind"), env=data.get("env", {}),
            isolate_saves=instance.isolate_saves, save_directory=instance.save_directory,
            notes=instance.notes, created_at=instance.created_at,
            updated_at=now,
        )
        return self.create_or_register_profile(updated)

    # --- LaunchProfileStore Integration & Loader Chain ---

    def generate_launch_profile_for_mod_chain(
        self,
        profile_id: str,
        *,
        store: Optional[LaunchProfileStore] = None,
        bind_in_store: bool = False,
        bind_target_id: Optional[str] = None,
        base_profile: Optional[LaunchProfile] = None,
    ) -> LaunchProfile:
        """Generate an app-owned LaunchProfile from a mod profile.

        CRITICAL LOADER RULES:
        1. Native games (like Satisfactory with SML) launch via native game profile.
        2. Windows loaders (like SKSE skse64_loader.exe) are Windows binaries.
           Direct native execution on Linux is STRICTLY FORBIDDEN.
           They require an explicit runner_kind:
           - 'proton':
               * Requires explicit proton_path and wine_prefix.
               * Requires explicit compat_data_path (e.g. compatdata/APPID). NEVER derived from pfx.
               * Uses Proton CLI: argv = ('run', loader_executable, ...)
               * Sets STEAM_COMPAT_DATA_PATH = compat_data_path.
           - 'wine':
               * Requires explicit wine executable and wine_prefix.
               * Wine CLI does NOT take 'run' token: argv = (loader_executable, ...)
               * Does NOT set STEAM_COMPAT_DATA_PATH.
        3. r2modman / BepInEx activation:
           Profile inventory is NOT proof BepInEx was deployed to game.
           Activation without verified deployed preloader DLL is unsupported.
           Ordinary Steam launch does not activate mods without deployed runtime.
        4. Target binding keys must match LaunchView resolution (raw numeric product_id for Steam).
           Never use arbitrary non-numeric keys as Steam URI command targets.
        5. Do NOT attach to live store unless bind_in_store=True AND store is explicitly provided.
        """
        mod_profile = self.get_profile(profile_id)
        if not mod_profile:
            raise ModManagementError(f"Mod profile '{profile_id}' not found")

        p_id = f"mod-{mod_profile.profile_id}"
        display_name = f"{mod_profile.game_title} (Modded: {mod_profile.display_name})"

        # Handle SKSE / Windows Script Extender Loader Chain
        if mod_profile.manager_type == 'skse':
            if not mod_profile.loader_executable:
                raise ModManagementError("SKSE mod profile requires a loader_executable (e.g. skse64_loader.exe)")

            loader_ext = Path(mod_profile.loader_executable).suffix.lower()
            if loader_ext == '.exe':
                if not mod_profile.runner_kind:
                    raise ModManagementError(
                        f"Windows loader '{mod_profile.loader_executable}' requires an explicit runner_kind "
                        f"('proton' or 'wine'). Guessing or unconfigured runners are strictly unsupported."
                    )

                if mod_profile.runner_kind not in SUPPORTED_RUNNER_KINDS:
                    raise ModManagementError(
                        f"Unsupported runner_kind '{mod_profile.runner_kind}'. Must be one of {sorted(SUPPORTED_RUNNER_KINDS)}"
                    )

                if not mod_profile.proton_path:
                    raise ModManagementError(
                        f"Windows loader requires a configured runner executable (proton_path) for {mod_profile.runner_kind}."
                    )

                if not mod_profile.wine_prefix:
                    raise ModManagementError(
                        f"Windows loader requires an explicit 'wine_prefix' path."
                    )

                env = dict(mod_profile.env)
                env['WINEPREFIX'] = mod_profile.wine_prefix

                if mod_profile.runner_kind == 'proton':
                    if not mod_profile.compat_data_path:
                        raise ModManagementError(
                            "Proton runner requires an explicit 'compat_data_path' (e.g. compatdata/APPID). "
                            "Deriving STEAM_COMPAT_DATA_PATH from wine_prefix is strictly forbidden."
                        )
                    env['STEAM_COMPAT_DATA_PATH'] = mod_profile.compat_data_path
                    executable = mod_profile.proton_path
                    argv_template = ('run', mod_profile.loader_executable) + tuple(mod_profile.loader_argv_template)

                elif mod_profile.runner_kind == 'wine':
                    executable = mod_profile.proton_path
                    argv_template = (mod_profile.loader_executable,) + tuple(mod_profile.loader_argv_template)

                launch_prof = LaunchProfile(
                    profile_id=p_id,
                    display_name=eff_name,
                    target_type='native_game',
                    platform_or_provider='steam',
                    executable=executable,
                    argv_template=argv_template,
                    cwd=mod_profile.install_directory,
                    env=env,
                    is_app_owned=True,
                    extra_metadata={
                        'mod_profile_id': mod_profile.profile_id,
                        'loader_type': f"skse_{mod_profile.runner_kind}",
                        'runner_kind': mod_profile.runner_kind,
                    },
                )
            else:
                executable = mod_profile.loader_executable
                argv_template = tuple(mod_profile.loader_argv_template)
                launch_prof = LaunchProfile(
                    profile_id=p_id,
                    display_name=eff_name,
                    target_type='native_game',
                    platform_or_provider='steam',
                    executable=executable,
                    argv_template=argv_template,
                    cwd=mod_profile.install_directory,
                    env=dict(mod_profile.env),
                    is_app_owned=True,
                    extra_metadata={'mod_profile_id': mod_profile.profile_id},
                )

        elif mod_profile.manager_type == 'satisfactory':
            # Reusing base_profile or verified executable
            game_exe = Path(mod_profile.install_directory) / 'FactoryGameSteam.exe'
            if base_profile is not None:
                executable = base_profile.executable
                argv_template = tuple(base_profile.argv_template)
                env = dict(base_profile.env)
                env.update(mod_profile.env)
            elif mod_profile.proton_path:
                executable = mod_profile.proton_path
                argv_template = ('run', str(game_exe))
                env = dict(mod_profile.env)
            else:
                raw_product_id = mod_profile.target_binding_key or mod_profile.variant_key or mod_profile.game_id
                if not re.fullmatch(r'\d+', str(raw_product_id)):
                    raise ModManagementError(
                        f"Steam launch for Satisfactory requires a raw numeric product_id (e.g. '526870'); "
                        f"received non-numeric target '{raw_product_id}'."
                    )
                argv_template = ('-silent', f"steam://rungameid/{raw_product_id}")
                executable = shutil.which('steam') or '/usr/bin/steam'
                env = dict(mod_profile.env)

            launch_prof = LaunchProfile(
                profile_id=p_id,
                display_name=eff_name,
                target_type='native_game',
                platform_or_provider='steam',
                executable=executable,
                argv_template=argv_template,
                cwd=mod_profile.install_directory,
                env=env,
                is_app_owned=True,
                extra_metadata={'mod_profile_id': mod_profile.profile_id, 'manager': 'smm'},
            )

        elif mod_profile.manager_type == 'r2modman':
            # R2 profile inventory is NOT proof BepInEx was deployed to game.
            # Plain Steam URI does NOT activate mods without deployed runtime.
            preloader_dll = Path(mod_profile.install_directory) / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll'
            if not preloader_dll.exists():
                raise ModManagementError(
                    f"r2modman profile activation unsupported: BepInEx preloader DLL not found at {preloader_dll}. "
                    "Profile inventory is read-only inspection and does not deploy mods into game files. "
                    "Ordinary Steam launch does not activate mods without deployed runtime."
                )

            env = dict(mod_profile.env)
            env['DOORSTOP_ENABLE'] = 'TRUE'
            env['DOORSTOP_TARGET_ASSEMBLY'] = str(preloader_dll)

            raw_product_id = mod_profile.target_binding_key or mod_profile.variant_key or mod_profile.game_id
            if not re.fullmatch(r'\d+', str(raw_product_id)):
                raise ModManagementError(
                    f"Steam launch for r2modman requires a raw numeric product_id (e.g. '632360'); "
                    f"received non-numeric target '{raw_product_id}'."
                )

            launch_prof = LaunchProfile(
                profile_id=p_id,
                display_name=eff_name,
                target_type='native_game',
                platform_or_provider='steam',
                executable=shutil.which('steam') or '/usr/bin/steam',
                argv_template=('-silent', f"steam://rungameid/{raw_product_id}"),
                cwd=mod_profile.install_directory,
                env=env,
                is_app_owned=True,
                extra_metadata={'mod_profile_id': mod_profile.profile_id, 'manager': 'r2modman'},
            )

        else:
            raise ModManagementError(f"Cannot generate launch profile for manager '{mod_profile.manager_type}'")

        if bind_in_store and store is not None:
            if store.get_profile(launch_prof.profile_id):
                store.update_profile(launch_prof)
            else:
                store.create_profile(launch_prof)

            target_key = bind_target_id or mod_profile.target_binding_key or mod_profile.variant_key or mod_profile.game_id
            store.set_game_override(target_key, launch_prof.profile_id)

        return launch_prof

    # --- Truthful Interface Gap Reporting ---

    def report_manager_interface_gaps(self, manager_type: str) -> Dict[str, Any]:
        """Report honest architectural gaps for external managers."""
        m = manager_type.lower()
        if m == 'vortex':
            return {
                'manager': 'vortex',
                'source_available': True,
                'license': 'GPL-3.0 (Nexus-Mods/Vortex)',
                'headless_contract_verified': False,
                'gap_summary': (
                    "Vortex is open-source (Nexus-Mods/Vortex), but no supported headless CLI contract was "
                    "verified in this pass without running the Electron desktop application. Direct headless "
                    "profile activation without the runtime risks corrupting internal state. User permits "
                    "component porting/adapter work in future iterations."
                ),
                'recommended_approach': (
                    "Treat Vortex purely as an external launch target in this pass. An app-owned headless adapter "
                    "or component port remains planned for subsequent phases."
                ),
                'can_headless_activate': False,
            }
        elif m == 'mo2':
            return {
                'manager': 'mo2',
                'source_available': True,
                'license': 'GPL-3.0 (ModOrganizer2/modorganizer)',
                'headless_contract_verified': False,
                'gap_summary': (
                    "Mod Organizer 2 relies on USVFS (User Space Virtual File System), a Windows kernel hook DLL "
                    "that virtualizes the game's file hierarchy in memory. USVFS cannot hook native Linux filesystem calls. "
                    "Running MO2 on Linux requires a patched Wine environment with DLL overrides. Outside the MO2 GUI, "
                    "no official headless CLI contract was verified in this pass."
                ),
                'recommended_approach': (
                    "For games requiring MO2, use the app-owned LaunchProfile to invoke the game via ModOrganizer.exe "
                    "inside its configured Proton/Wine prefix (`proton run ModOrganizer.exe -p <profile> ...`), or "
                    "maintain native profiles directly via our app-owned ModProfile system."
                ),
                'can_headless_activate': False,
            }
        elif m in SUPPORTED_MANAGERS:
            return {
                'manager': m,
                'source_available': True,
                'headless_contract_verified': True,
                'gap_summary': 'Supported natively or via verified Proton/Wine loader chains.',
                'can_headless_activate': True,
            }
        else:
            raise ModManagementError(f"Unknown manager type: '{manager_type}'")

    # --- BepInEx/r2modman local deployment (preview/apply/undo) ---

    _DEPLOY_JOURNAL_VERSION = 1

    def _deployment_journal_dir(self) -> Path:
        base = getattr(self, 'config_path', None) or getattr(self, 'storage_path', None) or getattr(self, 'mods_file', None)
        state_dir = (Path(base).resolve().parent if base else Path.cwd()) / 'deployments'
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    @staticmethod
    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _safe_deploy_relpath(base: Path, rel: Path) -> Path:
        if rel.is_absolute() or '..' in rel.parts:
            raise ModDeploymentError(f"Unsafe path escapes deployment root: {rel}")
        resolved = (base / rel).resolve()
        base_resolved = base.resolve()
        try:
            if not resolved.is_relative_to(base_resolved):
                raise ModDeploymentError(f"Path/symlink escape refused: {rel} -> {resolved}")
        except AttributeError:
            if os.path.commonpath([str(base_resolved), str(resolved)]) != str(base_resolved):
                raise ModDeploymentError(f"Path/symlink escape refused: {rel} -> {resolved}")
        return resolved

    def _iter_deployable_source_files(self, profile: ModProfile) -> list[tuple[Path, Path]]:
        src_base = Path(profile.profile_directory).resolve()
        out: list[tuple[Path, Path]] = []

        def _assert_under_profile_root(path: Path, rel: Path) -> None:
            resolved = path.resolve()
            try:
                if not resolved.is_relative_to(src_base):
                    raise ModDeploymentError(
                        f"Source path/symlink escape refused: {rel.as_posix()} -> {resolved}"
                    )
            except AttributeError:
                if os.path.commonpath([str(src_base), str(resolved)]) != str(src_base):
                    raise ModDeploymentError(
                        f"Source path/symlink escape refused: {rel.as_posix()} -> {resolved}"
                    )

        bepinex = src_base / 'BepInEx'
        if bepinex.exists():
            _assert_under_profile_root(bepinex, Path('BepInEx'))
            if bepinex.is_dir():
                for p in sorted(bepinex.rglob('*')):
                    if p.is_file() or p.is_symlink():
                        rel = Path('BepInEx') / p.relative_to(bepinex)
                        _assert_under_profile_root(p, rel)
                        if p.is_file():
                            out.append((p, rel))
        for name in ('winhttp.dll', 'doorstop_config.ini', '.doorstop_version'):
            p = src_base / name
            if p.exists() or p.is_symlink():
                rel = Path(name)
                _assert_under_profile_root(p, rel)
                if p.is_file():
                    out.append((p, rel))
        libs = src_base / 'doorstop_libs'
        if libs.exists():
            _assert_under_profile_root(libs, Path('doorstop_libs'))
            if libs.is_dir():
                for p in sorted(libs.rglob('*')):
                    if p.is_file() or p.is_symlink():
                        rel = Path('doorstop_libs') / p.relative_to(libs)
                        _assert_under_profile_root(p, rel)
                        if p.is_file():
                            out.append((p, rel))
        return out

    def _source_fingerprint(self, profile: ModProfile, files: list[tuple[Path, Path]]) -> dict[str, Any]:
        mods_yml = Path(profile.profile_directory).resolve() / 'mods.yml'
        items = []
        for src, rel in files:
            items.append({'rel': rel.as_posix(), 'sha256': self._file_sha256(src), 'size': src.stat().st_size})
        payload = {
            'profile_id': profile.profile_id,
            'mods_yml_sha256': self._file_sha256(mods_yml) if mods_yml.exists() else None,
            'files': items,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        return {'digest': digest, 'aggregate': digest, 'payload': payload}

    def _journal_path(self, plan_id: str) -> Path:
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', plan_id)
        return self._deployment_journal_dir() / f"{safe}.json"

    def _write_json_atomic(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass

    def _load_journal(self, plan_id: str) -> dict[str, Any]:
        p = self._journal_path(plan_id)
        if not p.exists():
            raise ModDeploymentError(f"Deployment journal not found: {plan_id}")
        try:
            with p.open('r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            raise ModDeploymentError(f"Failed to read deployment journal: {e}") from e

    def _known_app_owned_deployment_state(self, install_directory: str, relpath: str, dest_sha256: str) -> Optional[dict[str, Any]]:
        """Return evidence if current dest bytes were produced by a prior verified app-owned journal."""
        try:
            inst_dir = str(Path(install_directory).resolve())
            jdir = self._deployment_journal_dir()
            if not jdir.exists():
                return None
            for jp in sorted(jdir.glob('*.json')):
                try:
                    with jp.open('r', encoding='utf-8') as f:
                        j = json.load(f)
                except Exception:
                    continue
                if j.get('status') != 'applied':
                    continue
                plan = j.get('plan') or {}
                jinst = str(Path(plan.get('target_install_directory') or j.get('target_install_directory') or '').resolve()) if (plan.get('target_install_directory') or j.get('target_install_directory')) else ''
                if jinst != inst_dir:
                    continue
                for rec in (j.get('applied') or []):
                    if rec.get('relpath') != relpath:
                        continue
                    # proof: this journal recorded replacing/writing to reach dest_sha_after and target still matches
                    if rec.get('dest_sha256_after') == dest_sha256:
                        return {'journal_path': str(jp), 'plan_id': j.get('plan_id'), 'dest_sha256_after': dest_sha256}
            return None
        except Exception:
            return None

    def preview_r2modman_bepinex_deployment(
        self,
        profile_id: str,
        install_directory: str,
        *,
        expected_target_binding_key: str | None = None,
        expected_variant_key: str | None = None,
        expected_provider: str | None = None,
    ) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ModDeploymentError(f"Profile not found: {profile_id}")
        if profile.manager_type != 'r2modman':
            raise ModDeploymentError(f"Unsupported manager for BepInEx deployment: {profile.manager_type}")
        inst_dir = Path(install_directory).resolve()
        prof_inst = Path(profile.install_directory).resolve()
        if inst_dir != prof_inst:
            raise ModDeploymentError(
                f"Install directory mismatch: selected '{inst_dir}' != profile install '{prof_inst}'"
            )
        if expected_target_binding_key is not None and (profile.target_binding_key or '') != expected_target_binding_key:
            raise ModDeploymentError(
                f"Target binding mismatch: expected '{expected_target_binding_key}' got '{profile.target_binding_key}'"
            )
        if expected_variant_key is not None and (profile.variant_key or '') != expected_variant_key:
            raise ModDeploymentError(
                f"Variant mismatch: expected '{expected_variant_key}' got '{profile.variant_key}'"
            )
        provider = expected_provider or 'steam'
        if expected_provider is not None and expected_provider != 'steam':
            raise ModDeploymentError(f"Unsupported provider for r2modman/BepInEx deployment: {expected_provider}")

        file_pairs = self._iter_deployable_source_files(profile)
        if not file_pairs:
            raise ModDeploymentError(
                "No deployable BepInEx/runtime files found in r2modman profile; no-deploy fallback (no silent ordinary launch)"
            )
        src_fp = self._source_fingerprint(profile, file_pairs)
        actions = []
        conflicts = []
        for src, rel in file_pairs:
            conflict_reason = None
            dest_str = str(inst_dir / rel)
            # detect symlink path components under install
            cur = inst_dir
            symlink_component = False
            for part in rel.parts:
                cur = cur / part
                if cur.exists() and cur.is_symlink() and not (cur == inst_dir / rel):
                    symlink_component = True
                    break
            try:
                dest = self._safe_deploy_relpath(inst_dir, rel)
                dest_str = str(dest)
            except ModDeploymentError as e:
                conflict_reason = 'path/symlink escape refused'
            if conflict_reason is None and symlink_component:
                conflict_reason = 'path/symlink escape refused'
            action = 'create'
            dest_exists = False
            dest_sha = None
            ownership = None
            if conflict_reason is None:
                if dest.is_symlink():
                    conflict_reason = 'path/symlink escape refused'
                elif dest.exists():
                    if dest.is_dir():
                        conflict_reason = 'destination is directory, expected file'
                    elif dest.is_file():
                        dest_sha = self._file_sha256(dest)
                        src_sha = self._file_sha256(src)
                        if dest_sha == src_sha:
                            action = 'noop'
                        else:
                            ownership = self._known_app_owned_deployment_state(str(inst_dir), rel.as_posix(), dest_sha)
                            if ownership:
                                action = 'replace'
                            else:
                                conflict_reason = 'unmanaged conflicting existing file (not app-owned)'
                    else:
                        conflict_reason = 'unsupported destination type'
                else:
                    action = 'create'
            if conflict_reason:
                action = 'conflict'
            item = {
                'action': action,
                'src': str(src),
                'relpath': rel.as_posix(),
                'rel_path': rel.as_posix(),
                'dest': dest_str,
                'src_sha256': self._file_sha256(src),
                'source_sha256': self._file_sha256(src),
                'src_size': src.stat().st_size,
            }
            if dest_sha is not None:
                item['dest_sha256'] = dest_sha
                item['dest_sha256_before'] = dest_sha
            if action == 'replace' and ownership:
                item['prior_app_owned'] = ownership
            if conflict_reason:
                item['reason'] = conflict_reason
                item['conflict'] = conflict_reason
                conflicts.append({'relpath': rel.as_posix(), 'rel_path': rel.as_posix(), 'dest': dest_str, 'reason': conflict_reason, 'action': action})
            actions.append(item)
        plan_id = str(uuid.uuid4())
        src_preloader = Path(profile.profile_directory).resolve() / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll'
        target_preloader = inst_dir / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll'
        preloader_target = str(src_preloader)
        # Explicit supported boundary for this Windows BepInEx slice: required runtime
        # loader components must exist in trusted source payload or already in target.
        if not src_preloader.exists() and not target_preloader.exists():
            conflicts.append({
                'relpath': 'BepInEx/core/BepInEx.Preloader.dll',
                'rel_path': 'BepInEx/core/BepInEx.Preloader.dll',
                'dest': str(target_preloader),
                'reason': 'required BepInEx.Preloader.dll missing from both trusted source payload and target installation',
                'action': 'refuse',
            })
        src_doorstop = Path(profile.profile_directory).resolve() / 'winhttp.dll'
        target_doorstop = inst_dir / 'winhttp.dll'
        if not src_doorstop.exists() and not target_doorstop.exists():
            conflicts.append({
                'relpath': 'winhttp.dll',
                'rel_path': 'winhttp.dll',
                'dest': str(target_doorstop),
                'reason': 'required Doorstop loader (winhttp.dll) missing from both trusted source payload and target installation',
                'action': 'refuse',
            })
        launch_intent = {
            'runner': 'steam-doorstop-bepinex',
            'provider': provider,
            'install_directory': str(inst_dir),
            'target_binding_key': profile.target_binding_key,
            'variant_key': profile.variant_key,
            'env': {
                'DOORSTOP_ENABLE': 'TRUE',
                'DOORSTOP_TARGET_ASSEMBLY': preloader_target,
            },
            'staged_runtime_required': True,
            'expected_staged_runtime': True,
            'refers_to_staged_runtime': True,
            'target_install_directory': str(inst_dir),
            'is_ordinary_unmodded_steam': False,
        }
        plan = {
            'plan_version': self._DEPLOY_JOURNAL_VERSION,
            'plan_id': plan_id,
            'operation_id': plan_id,
            'profile_id': profile.profile_id,
            'manager_type': profile.manager_type,
            'target_install_directory': str(inst_dir),
            'target_binding_key': profile.target_binding_key,
            'variant_key': profile.variant_key,
            'provider': provider,
            'runner': 'steam-doorstop-bepinex',
            'source_fingerprint': src_fp,
            'actions': actions,
            'operations': actions,
            'conflicts': conflicts,
            'can_apply': len(conflicts) == 0,
            'launch_intent': launch_intent,
            'expected_staged_runtime': {'install_directory': str(inst_dir), 'staged': True},
        }
        return plan

    def apply_r2modman_bepinex_deployment(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, dict) or plan.get('plan_version') != self._DEPLOY_JOURNAL_VERSION:
            raise ModDeploymentError('Invalid or unsupported deployment plan')
        if not plan.get('can_apply') or plan.get('conflicts'):
            raise ModDeploymentError(f"Deployment plan has conflicts/refusals: {plan.get('conflicts')}")
        profile_id = plan.get('profile_id')
        install_directory = plan.get('target_install_directory')
        if not profile_id or not install_directory:
            raise ModDeploymentError('Plan missing profile_id or target_install_directory')
        # Recompute trusted current state; never execute caller-supplied action paths blindly.
        current = self.preview_r2modman_bepinex_deployment(
            str(profile_id),
            str(install_directory),
            expected_target_binding_key=plan.get('target_binding_key'),
            expected_variant_key=plan.get('variant_key'),
            expected_provider=plan.get('provider'),
        )
        if current.get('source_fingerprint', {}).get('digest') != plan.get('source_fingerprint', {}).get('digest'):
            raise ModDeploymentError('Stale plan refused: source fingerprints changed')
        if current.get('conflicts'):
            raise ModDeploymentError(f"Stale/conflicting target state: {current.get('conflicts')}")
        cur_map = {a['relpath']: a for a in current.get('actions', [])}
        plan_map = {a['relpath']: a for a in (plan.get('actions') or [])}
        if set(cur_map) != set(plan_map):
            raise ModDeploymentError('Stale plan refused: deployable file set changed')
        for rel, ca in cur_map.items():
            pa = plan_map.get(rel)
            if pa is None:
                raise ModDeploymentError(f"Stale/forged plan refused: missing action for {rel}")
            # Compare all material fields; execute only trusted recomputed actions.
            for key in ('action', 'relpath', 'dest', 'src_sha256', 'dest_sha256'):
                if pa.get(key) != ca.get(key):
                    raise ModDeploymentError(f"Stale/forged plan refused: field {key} changed for {rel}")
            if str(Path(pa.get('src', '')).resolve()) != str(Path(ca.get('src', '')).resolve()):
                raise ModDeploymentError(f"Stale/forged plan refused: source path changed for {rel}")

        plan_id = str(plan.get('plan_id') or uuid.uuid4())
        inst_dir = Path(install_directory).resolve()
        journal_path = self._journal_path(plan_id)
        backup_dir = self._deployment_journal_dir() / plan_id / 'backup'
        trusted_actions = [a for a in current.get('actions', []) if a.get('action') in ('create', 'replace')]
        journal = {
            'journal_version': self._DEPLOY_JOURNAL_VERSION,
            'plan_id': plan_id,
            'operation_id': plan_id,
            'profile_id': str(profile_id),
            'status': 'applying',
            'target_install_directory': str(inst_dir),
            'plan': current,  # persist trusted recomputed plan snapshot
            'requested_plan_id': plan.get('plan_id'),
            'pending': [a['relpath'] for a in trusted_actions],
            'applied': [],
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        # Write-ahead intent before any mutation.
        self._write_json_atomic(journal_path, journal)
        try:
            for act in trusted_actions:
                src = Path(act['src']).resolve()
                rel = Path(act['relpath'])
                dest = self._safe_deploy_relpath(inst_dir, rel)
                # Re-validate immediate pre-state against trusted action to avoid forged/tampered execution window surprises.
                pre_sha = self._file_sha256(dest) if dest.exists() and dest.is_file() and not dest.is_symlink() else None
                if act.get('action') == 'create':
                    if dest.exists() or dest.is_symlink():
                        raise ModDeploymentError(f"Conflicting destination appeared before apply: {dest}")
                elif act.get('action') == 'replace':
                    if not dest.exists() or not dest.is_file() or dest.is_symlink():
                        raise ModDeploymentError(f"Replace target missing/invalid before apply: {dest}")
                    if pre_sha != act.get('dest_sha256'):
                        raise ModDeploymentError(f"Stale target before apply for {rel}")
                else:
                    continue

                rec = {
                    'relpath': act['relpath'],
                    'dest': str(dest),
                    'action': act['action'],
                    'src': str(src),
                    'src_sha256': act.get('src_sha256'),
                    'status': 'intent',
                }
                if act.get('action') == 'replace':
                    bpath = backup_dir / rel
                    bpath.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, bpath)
                    if self._file_sha256(bpath) != pre_sha:
                        raise ModDeploymentError(f"Backup verification failed for {rel}")
                    rec.update({'kind': 'replaced', 'backup': str(bpath), 'dest_sha256_before': pre_sha})
                else:
                    rec.update({'kind': 'created'})

                journal['applied'].append(rec)
                journal['current_intent'] = act['relpath']
                self._write_json_atomic(journal_path, journal)

                dest.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix='.tmpdeploy')
                try:
                    with os.fdopen(fd, 'wb') as out_f, src.open('rb') as in_f:
                        shutil.copyfileobj(in_f, out_f)
                        out_f.flush()
                        os.fsync(out_f.fileno())
                    try:
                        shutil.copystat(src, tmp, follow_symlinks=True)
                    except Exception:
                        pass
                    os.replace(tmp, dest)
                finally:
                    if os.path.exists(tmp):
                        try:
                            os.unlink(tmp)
                        except Exception:
                            pass
                post_sha = self._file_sha256(dest)
                if post_sha != act.get('src_sha256'):
                    raise ModDeploymentError(f"Post-write verification failed for {rel}")
                rec['dest_sha256_after'] = post_sha
                rec['status'] = 'applied'
                journal['current_intent'] = None
                if act['relpath'] in journal.get('pending', []):
                    journal['pending'].remove(act['relpath'])
                self._write_json_atomic(journal_path, journal)

            staged_preloader = inst_dir / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll'
            journal['status'] = 'applied'
            journal['applied_at'] = datetime.now(timezone.utc).isoformat()
            journal['operation_id'] = plan_id
            profile_obj = self.get_profile(str(profile_id))
            journal['resulting_launch_intent'] = {
                **(current.get('launch_intent') or plan.get('launch_intent') or {}),
                'profile_id': str(profile_id),
                'profile_directory': str(profile_obj.profile_directory) if profile_obj else None,
                'variant_key': plan.get('variant_key'),
                'target_binding_key': plan.get('target_binding_key'),
                'env': {
                    'DOORSTOP_ENABLE': 'TRUE',
                    'DOORSTOP_TARGET_ASSEMBLY': str(staged_preloader),
                },
                'staged_runtime_verified': staged_preloader.exists(),
                'refers_to_staged_runtime': True,
                'target_install_directory': str(inst_dir),
                'is_ordinary_unmodded_steam': False,
            }
            self._write_json_atomic(journal_path, journal)
            return journal
        except Exception:
            try:
                self._rollback_from_journal(journal, mark_status='apply_failed_rolled_back')
            except Exception:
                journal['status'] = 'apply_failed_rollback_error'
                try:
                    self._write_json_atomic(journal_path, journal)
                except Exception:
                    pass
            raise

    def _precheck_rollback_safety(self, journal: dict[str, Any]) -> None:
        inst_dir = Path((journal.get('plan') or {}).get('target_install_directory') or journal.get('target_install_directory') or '').resolve()
        if not str(inst_dir) or not inst_dir.exists():
            raise ModDeploymentError('Cannot rollback: target install directory missing')
        for rec in journal.get('applied', []):
            rel = Path(rec['relpath'])
            dest = self._safe_deploy_relpath(inst_dir, rel)
            kind = rec.get('kind')
            status = rec.get('status')
            # Only mutate entries that were actually written or intentionally created intent state we can reason about.
            if kind == 'created':
                if status != 'applied':
                    # intent-only created target: after a crash before post-hash, a file may exist.
                    # For ANY existing target require current regular non-symlink bytes match recorded
                    # src_sha256 (or verified posthash), otherwise refuse ALL undo before writes.
                    if dest.exists() or dest.is_symlink():
                        if dest.is_symlink() or not dest.is_file() or (dest.is_dir() and not dest.is_symlink()):
                            raise ModDeploymentError(f"Rollback refused: unexpected target type at created intent {rel}")
                        expected = rec.get('dest_sha256_after') or rec.get('src_sha256')
                        if not expected or self._file_sha256(dest) != expected:
                            raise ModDeploymentError(f"Rollback refused: modified/unexpected content at created intent {rel}")
                    continue
                expected = rec.get('dest_sha256_after') or rec.get('src_sha256')
                if dest.is_symlink():
                    raise ModDeploymentError(f"Rollback refused: symlink at created target {rel}")
                if dest.exists() and dest.is_dir() and not dest.is_symlink():
                    raise ModDeploymentError(f"Rollback refused: user directory at created target {rel}")
                if not dest.exists() or not dest.is_file():
                    raise ModDeploymentError(f"Rollback refused: missing/invalid created target {rel}")
                if expected and self._file_sha256(dest) != expected:
                    raise ModDeploymentError(f"Rollback refused: modified deployed file {rel}; refusing to delete user content")
            elif kind == 'replaced':
                backup = rec.get('backup')
                before = rec.get('dest_sha256_before')
                expected_after = rec.get('dest_sha256_after') or rec.get('src_sha256')
                if not backup or not Path(backup).exists():
                    raise ModDeploymentError(f"Missing backup for rollback: {rel}")
                if status == 'intent':
                    # mutation may or may not have happened; only allow restore if current matches before or expected_after
                    if dest.exists() and dest.is_dir() and not dest.is_symlink():
                        raise ModDeploymentError(f"Rollback refused: directory at replaced target {rel}")
                    if dest.exists() and dest.is_file() and not dest.is_symlink():
                        cur = self._file_sha256(dest)
                        if cur not in tuple(x for x in (before, expected_after) if x):
                            raise ModDeploymentError(f"Rollback refused: unexpected modified content at {rel}")
                    elif dest.exists() or dest.is_symlink():
                        raise ModDeploymentError(f"Rollback refused: unexpected target type at {rel}")
                    continue
                if dest.is_symlink():
                    raise ModDeploymentError(f"Rollback refused: symlink at replaced target {rel}")
                if dest.exists() and dest.is_dir() and not dest.is_symlink():
                    raise ModDeploymentError(f"Rollback refused: directory at replaced target {rel}")
                if not dest.exists() or not dest.is_file():
                    raise ModDeploymentError(f"Rollback refused: missing replaced target {rel}")
                if expected_after and self._file_sha256(dest) != expected_after:
                    raise ModDeploymentError(f"Rollback refused: modified deployed file {rel}; refusing to overwrite user content")
            else:
                raise ModDeploymentError(f"Rollback refused: unknown journal record kind for {rel}")

    def _rollback_from_journal(self, journal: dict[str, Any], mark_status: str = 'undone') -> dict[str, Any]:
        inst_dir = Path((journal.get('plan') or {}).get('target_install_directory') or journal.get('target_install_directory') or '').resolve()
        if not str(inst_dir) or not inst_dir.exists():
            raise ModDeploymentError('Cannot rollback: target install directory missing')
        # Check all targets before any undo mutation; preserve journal on conflict.
        self._precheck_rollback_safety(journal)
        try:
            for rec in reversed(journal.get('applied', [])):
                rel = Path(rec['relpath'])
                dest = self._safe_deploy_relpath(inst_dir, rel)
                kind = rec.get('kind')
                status = rec.get('status')
                if kind == 'created':
                    if status == 'applied' and (dest.exists() or dest.is_symlink()):
                        if dest.is_dir() and not dest.is_symlink():
                            raise ModDeploymentError(f"Rollback refused: directory at {rel}")
                        dest.unlink()
                        cur = dest.parent
                        while cur != inst_dir and cur.exists():
                            try:
                                cur.rmdir()
                            except OSError:
                                break
                            cur = cur.parent
                    elif status == 'intent' and dest.exists() and dest.is_file() and not dest.is_symlink():
                        # if intent recorded but file appeared matching expected deploy hash, treat as created artifact of this op
                        expected = rec.get('dest_sha256_after') or rec.get('src_sha256')
                        if expected and self._file_sha256(dest) == expected:
                            dest.unlink()
                elif kind == 'replaced':
                    backup = rec.get('backup')
                    if not backup or not Path(backup).exists():
                        raise ModDeploymentError(f"Missing backup for rollback: {rel}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix='.tmprollback')
                    try:
                        with os.fdopen(fd, 'wb') as out_f, open(backup, 'rb') as in_f:
                            shutil.copyfileobj(in_f, out_f)
                            out_f.flush(); os.fsync(out_f.fileno())
                        try:
                            shutil.copystat(backup, tmp, follow_symlinks=True)
                        except Exception:
                            pass
                        os.replace(tmp, dest)
                    finally:
                        if os.path.exists(tmp):
                            try:
                                os.unlink(tmp)
                            except Exception:
                                pass
                rec['rollback_done'] = True
            journal['status'] = mark_status
            journal['undone_at'] = datetime.now(timezone.utc).isoformat()
            jp = self._journal_path(str(journal.get('plan_id')))
            self._write_json_atomic(jp, journal)
            return journal
        except Exception:
            # Preserve journal on conflict/failure; do not mark undone.
            try:
                journal['last_rollback_error'] = 'rollback_conflict_or_failure'
                jp = self._journal_path(str(journal.get('plan_id')))
                self._write_json_atomic(jp, journal)
            except Exception:
                pass
            raise

    def undo_r2modman_bepinex_deployment(self, plan_or_id: Any) -> dict[str, Any]:
        if isinstance(plan_or_id, dict):
            plan_id = str(plan_or_id.get('plan_id') or '')
        else:
            plan_id = str(plan_or_id)
        if not plan_id:
            raise ModDeploymentError('Missing deployment plan_id for undo')
        journal = self._load_journal(plan_id)
        status = journal.get('status')
        if status in ('undone', 'apply_failed_rolled_back'):
            raise ModDeploymentError(f'Deployment already undone: {status}')
        if status not in ('applied', 'applying', 'apply_failed_rollback_error'):
            raise ModDeploymentError(f"Deployment not undoable in status: {status}")
        return self._rollback_from_journal(journal, mark_status='undone')



    def verify_staged_bepinex_runtime(self, install_directory: str, *, staged_only: bool = False) -> dict[str, Any]:
        """Report presence of staged/installed BepInEx runtime markers honestly.

        This is an explicit, bounded proof of staged runtime files only. It must not
        claim full mod stack compatibility, successful game startup, or end-to-end
        mod loading/progression.
        """
        inst_dir = Path(install_directory).resolve()
        required = [
            inst_dir / 'winhttp.dll',
            inst_dir / 'doorstop_config.ini',
            inst_dir / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll',
        ]
        present = {str(p.relative_to(inst_dir).as_posix()): p.exists() for p in required}
        bepinex_dir = inst_dir / 'BepInEx'
        plugin_count = 0
        if (bepinex_dir / 'plugins').is_dir():
            plugin_count = sum(1 for p in (bepinex_dir / 'plugins').rglob('*') if p.is_file())
        staged_runtime_present = all(present.values())
        return {
            'install_directory': str(inst_dir),
            'staged_only': bool(staged_only),
            'staged_runtime_present': staged_runtime_present,
            'required_files_present': present,
            'plugin_file_count': plugin_count,
            'proof_scope': 'staged_bepinex_runtime_files_only',
            'claims_complete_mod_loading': False,
            'claims_game_startup_or_progression': False,
            'note': 'Verified staged BepInEx runtime markers/files only; not a proof of complete mod loading or in-game behavior.',
        }


class ModDeploymentError(ModManagementError):
    """Raised for deployment preview/apply/undo failures."""
    pass
