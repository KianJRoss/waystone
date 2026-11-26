"""App-owned save management service and job runner.

Provides save profiles/config CRUD, explicit tool capability discovery,
and verifiable backup / restore-preview / restore job contracts using
subprocesses with shell=False, option-separator enforcement (--),
atomic configuration writes, explicit manifest bindings, exact complete
preview verification, destination fingerprinting (both present and absent),
backup snapshot identity verification, and plan TTL expiration.
Zero game launches, zero shell interpolation, zero fabricated CLI options.
"""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional
import uuid
from native_services import waystone_paths as _wpaths

SCOPE_GAME = 'game'
SCOPE_BULK = 'bulk'
PLAN_TTL_SECONDS = 300  # 5 minutes validity for restore plans

BUNDLED_LUDUSAVI = _wpaths.store('tools', 'ludusavi', 'v0.31.0', 'ludusavi')


class Conflict(ValueError):
    """Raised when a restore plan is invalid, expired, stale, or has unacknowledged conflicts."""


class ManagedSubprocessRunner:
    """Subprocess runner tracking active processes to allow safe bounded timeouts and explicit killing."""

    def __init__(self, default_timeout: float = 30.0) -> None:
        self.default_timeout = default_timeout
        self._lock = threading.Lock()
        self._active_procs: Dict[int, subprocess.Popen] = {}
        self._closed = False

    def __call__(self, argv: List[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        eff_timeout = timeout if timeout is not None else self.default_timeout
        with self._lock:
            if self._closed:
                raise RuntimeError('Save subprocess runner is closed')
            proc = subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL,text=True,shell=False)
            self._active_procs[proc.pid] = proc

        try:
            stdout, stderr = proc.communicate(timeout=eff_timeout)
            return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            self._kill_process(proc)
            proc.communicate()
            raise TimeoutError(f"Process {' '.join(argv[:3])} timed out after {eff_timeout}s")
        except Exception:
            self._kill_process(proc)
            proc.communicate()
            raise
        finally:
            with self._lock:
                self._active_procs.pop(proc.pid, None)

    def _kill_process(self, proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except (subprocess.TimeoutExpired, Exception):
            try:
                proc.kill()
            except Exception:
                pass

    def kill_all_active(self) -> int:
        """Explicitly kill ONLY owned active Ludusavi subprocesses."""
        with self._lock:
            self._closed = True
            procs = list(self._active_procs.values())
        killed = 0
        for p in procs:
            self._kill_process(p)
            killed += 1
        return killed


default_runner = ManagedSubprocessRunner(default_timeout=60.0)


def _safe_str(val: Any, max_len: int = 512) -> str:
    if not isinstance(val, str):
        return ''
    clean = val.strip().replace('\x00', '')
    return clean[:max_len]


def _fingerprint_file(path: Path) -> Dict[str, Any]:
    """Fingerprint both present and absent destination files."""
    try:
        st = path.stat()
        return {
            'exists': True,
            'size': st.st_size,
            'mtime_ns': st.st_mtime_ns,
            'device': st.st_dev, 'inode': st.st_ino, 'ctime_ns': st.st_ctime_ns,
            'resolved_path': str(path.resolve()),
        }
    except FileNotFoundError:
        return {
            'exists': False,
            'size': 0,
            'mtime_ns': 0,
        }


def _snapshot_directory(path: Path) -> Dict[str, Any]:
    """Capture snapshot fingerprint of a backup directory."""
    if not path.exists() or not path.is_dir():
        return {}
    snap: Dict[str, Any] = {}
    for p in sorted(path.rglob('*')):
        if p.is_file():
            try:
                st = p.stat()
                snap[str(p)] = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
            except FileNotFoundError:
                pass
    return snap


def _checked_output(proc, targets=(), *, operation='operation'):
    """Validate real Ludusavi general-output; JSON alone does not mean success."""
    if proc.returncode != 0:
        raise RuntimeError(f'Ludusavi {operation} failed (exit {proc.returncode}); no success was recorded')
    if not isinstance(proc.stdout, str) or len(proc.stdout) > 16 * 1024 * 1024:
        raise RuntimeError('Ludusavi response exceeded the supported size')
    try:
        parsed=json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError('Ludusavi returned invalid JSON') from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get('games'), dict):
        raise RuntimeError('Ludusavi response is missing its game results')
    errors=parsed.get('errors') or {}
    if not isinstance(errors, dict) or any(errors.get(key) for key in
            ('someGamesFailed','unknownGames','cloudConflict','cloudSyncFailed')):
        raise RuntimeError('Ludusavi reported failed, unknown or conflicting game data')
    for target in targets:
        row=parsed['games'].get(target['manifest_title'])
        if not isinstance(row, dict):
            raise RuntimeError(f"Ludusavi response is missing exact requested game entry for '{target['manifest_title']}'")
        if operation!='backups':
            if row.get('decision')!='Processed' or not isinstance(row.get('files'), dict):
                raise RuntimeError('Ludusavi did not produce a Processed decision with valid file data')
            if row.get('registry'):
                raise RuntimeError('Registry restore/backup review is not yet supported by this view')
            for path, meta in row['files'].items():
                if (not isinstance(path,str) or not Path(path).is_absolute() or
                        not isinstance(meta,dict) or type(meta.get('bytes')) is not int or meta['bytes']<0 or
                        meta.get('failed') is True or meta.get('error')):
                    raise RuntimeError('Ludusavi reported invalid or failed file data')
    return parsed


def _validate_game_targets(targets: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Validate targets and enforce explicit manifest binding (ZERO title fallback)."""
    if not targets:
        raise ValueError('No target games provided for save operation')

    validated = []
    seen_ids = set()
    seen_manifest_titles = set()

    for item in targets:
        if not isinstance(item, dict):
            raise ValueError('Each target must be a dictionary')

        c_id = _safe_str(item.get('id', ''))
        title = _safe_str(item.get('title', ''))
        m_title = _safe_str(item.get('manifest_title', ''))
        source = _safe_str(item.get('source', ''))
        wine_prefix = _safe_str(item.get('wine_prefix', ''))
        variant_key = _safe_str(item.get('variant_key', ''))

        if not c_id:
            raise ValueError(f"Target missing required canonical game ID: {item}")

        # CRITICAL SAFETY: ZERO FALLBACK to display title
        if not m_title:
            raise ValueError(
                f"Target game '{c_id}' ({title or 'untitled'}) requires an explicit manifest_title binding. "
                "Inference or fallback to display title is strictly disallowed."
            )

        if title.startswith('--') or m_title.startswith('--'):
            raise ValueError(f"Game title and manifest title cannot start with '--': '{m_title}'")

        if c_id in seen_ids:
            raise ValueError(f"Duplicate canonical game ID in target list: '{c_id}'")
        seen_ids.add(c_id)

        # Ambiguity check: prevent accidental collision within same batch
        m_key = m_title.casefold()
        if m_key in seen_manifest_titles:
            raise ValueError(
                f"Ambiguous targets: manifest title '{m_title}' is mapped multiple times in one operation."
            )
        seen_manifest_titles.add(m_key)

        validated.append({
            'id': c_id,
            'title': title,
            'manifest_title': m_title,
            'source': source,
            'wine_prefix': wine_prefix,
            'variant_key': variant_key,
        })

    return validated


class SaveManager:
    """Core service for save management, profiles CRUD, and backup/restore jobs."""

    def __init__(
        self,
        config_path: Path | str,
        runner: Optional[Callable[[List[str]], subprocess.CompletedProcess]] = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser().absolute()
        self.runner = runner if runner is not None else ManagedSubprocessRunner(default_timeout=1800.0)
        self._pending_restore_plans: Dict[str, Dict[str, Any]] = {}
        self._config: Dict[str, Any] = {
            'tool_path': self._discover_initial_tool_path(),
            'default_backup_directory': str(self.config_path.parent / 'backups'),
            'profiles': {},
            'manifest_bindings': {},
        }
        self._load_config()

    def _cli_argv(self, executable, operation):
        # Separate app-owned Ludusavi config, no inherited cloud sync or implicit
        # manifest fetch. Definitions are installed/updated explicitly.
        return [executable, '--config', str(self.config_path.parent / 'ludusavi'),
                '--no-manifest-update', operation]

    def _discover_initial_tool_path(self) -> str:
        """Default discover bundled tool path if executable, else 'ludusavi'."""
        if BUNDLED_LUDUSAVI.is_file() and os.access(BUNDLED_LUDUSAVI, os.X_OK):
            return str(BUNDLED_LUDUSAVI)
        return 'ludusavi'

    def _load_config(self) -> None:
        if self.config_path.exists():
            text = self.config_path.read_text(encoding='utf-8')
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    self._config.update(data)
            except Exception as exc:
                raise ValueError(f"Invalid configuration file JSON at {self.config_path}: {exc}") from exc

    def save_config(self) -> None:
        """Atomically persist configuration."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix('.tmp.' + uuid.uuid4().hex[:8])
        tmp.write_text(json.dumps(self._config, indent=2, sort_keys=True), encoding='utf-8')
        tmp.replace(self.config_path)

    def cancel_active_processes(self) -> int:
        """Kill active child processes if using managed runner."""
        if hasattr(self.runner, 'kill_all_active'):
            return self.runner.kill_all_active()
        return 0

    # --- Tool path and directory configuration ---

    def get_tool_path(self) -> str:
        return str(self._config.get('tool_path', 'ludusavi'))

    def set_tool_path(self, tool_path: str) -> str:
        cleaned = _safe_str(tool_path, 1024)
        if not cleaned:
            raise ValueError('Tool path must be a non-empty valid path')
        self._config['tool_path'] = cleaned
        self.save_config()
        return cleaned

    def get_default_backup_directory(self) -> str:
        return str(self._config.get('default_backup_directory', ''))

    def set_default_backup_directory(self, path: str) -> str:
        cleaned = _safe_str(path, 1024)
        if not cleaned:
            raise ValueError('Backup directory must be a non-empty valid path')
        self._config['default_backup_directory'] = cleaned
        self.save_config()
        return cleaned

    def get_manifest_binding(self, game_id: str) -> str:
        c_id = _safe_str(game_id, 128)
        return str(self._config.get('manifest_bindings', {}).get(c_id, ''))

    def set_manifest_binding(self, game_id: str, manifest_title: str) -> str:
        c_id = _safe_str(game_id, 128)
        m_title = _safe_str(manifest_title, 256)
        if not c_id:
            raise ValueError('Valid game ID required for manifest binding')
        if not m_title:
            raise ValueError('Valid manifest title required for binding')
        if 'manifest_bindings' not in self._config:
            self._config['manifest_bindings'] = {}
        self._config['manifest_bindings'][c_id] = m_title
        self.save_config()
        return m_title

    # --- Profiles CRUD ---

    def list_profiles(self) -> List[Dict[str, Any]]:
        profs = self._config.get('profiles', {})
        res = []
        for pid, pdata in profs.items():
            entry = dict(pdata)
            entry['id'] = pid
            res.append(entry)
        return sorted(res, key=lambda x: x.get('name', '').casefold())

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        pid = _safe_str(profile_id, 128)
        prof = self._config.get('profiles', {}).get(pid)
        if prof:
            res = dict(prof)
            res['id'] = pid
            return res
        return None

    def create_profile(
        self,
        profile_id: str,
        name: str,
        backup_directory: str = '',
        wine_prefix: str = '',
        retention_limit: int = 0,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        pid = _safe_str(profile_id, 128)
        pname = _safe_str(name, 256)
        if not pid or not pname:
            raise ValueError('Profile ID and name cannot be empty')

        if 'profiles' not in self._config:
            self._config['profiles'] = {}

        if pid in self._config['profiles']:
            raise Conflict(f"Profile with ID '{pid}' already exists.")

        profile = {
            'name': pname,
            'backup_directory': _safe_str(backup_directory, 1024),
            'wine_prefix': _safe_str(wine_prefix, 1024),
            'retention_limit': int(retention_limit),
            'tags': list(tags or []),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._config['profiles'][pid] = profile
        self.save_config()

        res = dict(profile)
        res['id'] = pid
        return res

    def create_or_update_profile(
        self,
        profile_id: str,
        name: str,
        backup_directory: str = '',
        wine_prefix: str = '',
    ) -> Dict[str, Any]:
        pid = _safe_str(profile_id, 128)
        pname = _safe_str(name, 256)
        if not pid or not pname:
            raise ValueError('Profile ID and name cannot be empty')

        if 'profiles' not in self._config:
            self._config['profiles'] = {}

        profile = {
            'name': pname,
            'backup_directory': _safe_str(backup_directory, 1024),
            'wine_prefix': _safe_str(wine_prefix, 1024),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._config['profiles'][pid] = profile
        self.save_config()

        res = dict(profile)
        res['id'] = pid
        return res

    def update_profile(self, profile_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        pid = _safe_str(profile_id, 128)
        if pid not in self._config.get('profiles', {}):
            raise ValueError(f"Profile '{pid}' does not exist.")
        prof = self._config['profiles'][pid]
        for k, v in updates.items():
            if k == 'name':
                prof['name'] = _safe_str(v, 256)
            elif k == 'backup_directory':
                prof['backup_directory'] = _safe_str(v, 1024)
            elif k == 'wine_prefix':
                prof['wine_prefix'] = _safe_str(v, 1024)
            elif k == 'retention_limit':
                prof['retention_limit'] = int(v)
            elif k == 'tags':
                prof['tags'] = list(v)
        prof['updated_at'] = datetime.now(timezone.utc).isoformat()
        self.save_config()
        res = dict(prof)
        res['id'] = pid
        return res

    def delete_profile(self, profile_id: str) -> bool:
        pid = _safe_str(profile_id, 128)
        if pid in self._config.get('profiles', {}):
            del self._config['profiles'][pid]
            self.save_config()
            return True
        return False

    def _resolve_backup_dir(
        self,
        profile_id: Optional[str] = None,
        path: Optional[str | Path] = None,
        backup_path: Optional[str | Path] = None,
    ) -> Path:
        def selected(value):
            candidate=Path(value).expanduser()
            if not candidate.is_absolute():raise ValueError('Backup folder must be an absolute path')
            return candidate
        target_p = path or backup_path
        if target_p:
            return selected(target_p)
        if profile_id:
            prof = self.get_profile(profile_id)
            if prof and prof.get('backup_directory'):
                return selected(prof['backup_directory'])
        default_dir = self.get_default_backup_directory()
        if default_dir:
            return selected(default_dir)
        return _wpaths.store('backups')

    # --- Tool capability discovery ---

    def check_capabilities(self, tool_path: Optional[str] = None) -> Dict[str, Any]:
        """Check Ludusavi capabilities without launching games or touching real saves."""
        target_path = tool_path or self.get_tool_path()
        resolved = shutil.which(target_path)
        is_direct_exec = (
            Path(target_path).is_file() and os.access(target_path, os.X_OK)
        )
        executable = resolved if resolved else (target_path if is_direct_exec else None)

        if not executable:
            return {
                'available': False,
                'tool_path': target_path,
                'resolved_path': None,
                'version': None,
                'capabilities': [],
                'error': (
                    f"Ludusavi executable not found at '{target_path}'. "
                    "Save backup and restore require Ludusavi on PATH or an explicit tool path configured."
                ),
            }

        try:
            res = self.runner([executable, '--version'])
            if res.returncode == 0:
                version_str = res.stdout.strip()
                return {
                    'available': True,
                    'tool_path': target_path,
                    'resolved_path': executable,
                    'version': version_str,
                    'capabilities': [
                        'backup',
                        'restore',
                        'backup_single',
                        'backup_bulk',
                        'restore_preview',
                        'restore_apply',
                        'custom_path',
                        'wine_prefix',
                    ],
                    'error': '',
                }
            return {
                'available': False,
                'tool_path': target_path,
                'resolved_path': executable,
                'version': None,
                'capabilities': [],
                'error': f"Ludusavi execution failed with returncode {res.returncode}: {res.stderr.strip()}",
            }
        except Exception as exc:
            return {
                'available': False,
                'tool_path': target_path,
                'resolved_path': executable,
                'version': None,
                'capabilities': [],
                'error': f"Failed to execute Ludusavi capability check: {exc}",
            }

    # --- Backup Operations ---

    def find_manifest_titles(self, query):
        query=_safe_str(query,512)
        if not query:raise ValueError('Enter a game name to search save definitions')
        caps=self.check_capabilities()
        if not caps['available']:raise RuntimeError(caps['error'])
        argv=self._cli_argv(caps['resolved_path'],'find')+['--api','--multiple','--fuzzy','--',query]
        data=_checked_output(self.runner(argv),operation='find')
        return [{'title':name,'score':row.get('score')} for name,row in list(data['games'].items())[:50]
                if isinstance(name,str) and isinstance(row,dict)]

    def update_definitions(self):
        caps=self.check_capabilities()
        if not caps['available']:raise RuntimeError(caps['error'])
        result=self.runner(self._cli_argv(caps['resolved_path'],'manifest')+['update','--force'])
        if result.returncode:raise RuntimeError('Save definition update failed; existing definitions were kept')
        return {'success':True,'message':'Save-game definitions updated'}

    def backup(
        self,
        targets: List[Dict[str, Any]],
        *,
        scope: str = SCOPE_GAME,
        profile_id: Optional[str] = None,
        path: Optional[str | Path] = None,
        backup_path: Optional[str | Path] = None,
        preview: bool = False,
        wine_prefix: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Run backup or backup preview via Ludusavi."""
        caps = self.check_capabilities()
        if not caps['available']:
            raise RuntimeError(caps['error'])

        executable = caps['resolved_path']
        valid_targets = _validate_game_targets(targets)
        backup_dir = self._resolve_backup_dir(profile_id=profile_id, path=path, backup_path=backup_path)
        backup_dir.mkdir(parents=True, exist_ok=True)

        retention = (self.get_profile(profile_id) or {}).get('retention_limit', 0) if profile_id else 0
        retention = retention if type(retention) is int and 1 <= retention <= 255 else 5
        argv = self._cli_argv(executable, 'backup') + ['--path', str(backup_dir), '--api', '--no-cloud-sync', '--full-limit', str(retention)]

        wp = wine_prefix
        if not wp and profile_id:
            prof = self.get_profile(profile_id)
            if prof and prof.get('wine_prefix'):
                wp = prof['wine_prefix']
        if not wp and len(valid_targets) == 1 and valid_targets[0].get('wine_prefix'):
            wp = valid_targets[0]['wine_prefix']

        if wp:
            argv.extend(['--wine-prefix', str(wp)])

        if preview:
            argv.append('--preview')
        elif force:
            argv.append('--force')

        argv.append('--')
        for t in valid_targets:
            argv.append(t['manifest_title'])

        proc = self.runner(argv)
        parsed = _checked_output(proc, valid_targets, operation='backup')

        overall = parsed.get('overall', {})
        games = parsed.get('games', {})

        target_results = []
        for t in valid_targets:
            m_title = t['manifest_title']
            g_info = games.get(m_title, {})
            decision = g_info.get('decision', 'Unknown')
            files_dict = g_info.get('files', {})
            status_val = decision.casefold() if isinstance(decision, str) else 'unknown'
            target_results.append({
                'id': t['id'],
                'title': t['title'],
                'manifest_title': m_title,
                'source': t['source'],
                'decision': decision,
                'status': status_val,
                'files_count': len(files_dict),
                'bytes': sum(f.get('bytes', 0) for f in files_dict.values()),
            })

        return {
            'success': True,
            'scope': scope,
            'preview': preview,
            'backup_directory': str(backup_dir),
            'targets': target_results,
            'overall': overall,
            'raw_output': parsed,
            'argv': argv,
        }

    # --- Restore Operations ---

    def preview_restore(
        self,
        targets: List[Dict[str, Any]],
        *,
        scope: str = SCOPE_GAME,
        profile_id: Optional[str] = None,
        path: Optional[str | Path] = None,
        backup_path: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        """Generate a valid, single-use, TTL-enforced restore plan."""
        caps = self.check_capabilities()
        if not caps['available']:
            return {
                'success': False,
                'scope': scope,
                'error': caps['error'],
            }

        executable = caps['resolved_path']
        valid_targets = _validate_game_targets(targets)
        backup_dir = self._resolve_backup_dir(profile_id=profile_id, path=path, backup_path=backup_path)

        if not backup_dir.exists():
            raise RuntimeError(f"Backup directory does not exist: {backup_dir}")

        argv = self._cli_argv(executable, 'restore') + ['--path', str(backup_dir), '--preview', '--api', '--no-cloud-sync', '--']
        for t in valid_targets:
            argv.append(t['manifest_title'])

        proc = self.runner(argv)
        parsed = _checked_output(proc, valid_targets, operation='restore preview')

        games = parsed.get('games', {})

        # EXACT COMPLETE PREVIEW VERIFICATION:
        target_plans = []
        destination_fingerprint: Dict[str, Dict[str, Any]] = {}
        conflicts = []

        for t in valid_targets:
            m_title = t['manifest_title']
            if m_title not in games:
                raise RuntimeError(
                    f"Restore preview missing exact requested game entry for '{m_title}' in output"
                )

            g_info = games[m_title]
            decision = g_info.get('decision')
            if decision != 'Processed':
                raise RuntimeError(
                    f"Restore preview for '{m_title}' did not produce a Processed decision (got '{decision}')"
                )

            files = g_info.get('files', {})
            if not files:
                raise RuntimeError(
                    f"Restore preview for '{m_title}' returned empty files set"
                )

            game_conflicts = []
            for filepath, fmeta in files.items():
                p = Path(filepath)
                fp = _fingerprint_file(p)
                destination_fingerprint[str(p)] = fp
                if fp['exists']:
                    game_conflicts.append({
                        'path': str(p),
                        'existing_size': fp['size'],
                        'backup_size': fmeta.get('bytes', 0),
                    })

            if game_conflicts:
                conflicts.append({
                    'id': t['id'],
                    'title': t['title'],
                    'manifest_title': m_title,
                    'conflicting_files': game_conflicts,
                })

            target_plans.append({
                'id': t['id'],
                'title': t['title'],
                'manifest_title': m_title,
                'source': t['source'],
                'variant_key': t['variant_key'],
                'decision': decision,
                'files_count': len(files),
                'bytes': sum(f.get('bytes', 0) for f in files.values()),
                'files': list(files.keys()),
                'conflicts': game_conflicts,
            })

        # Archive snapshot fingerprint
        backup_snapshot = _snapshot_directory(backup_dir)

        req_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        plan = {
            'request_id': req_id,
            'plan_id': req_id,
            'created_at': now.isoformat(),
            'expires_at': now.timestamp() + PLAN_TTL_SECONDS,
            'scope': scope,
            'backup_directory': str(backup_dir),
            'targets': target_plans,
            'total_files': sum(t['files_count'] for t in target_plans),
            'total_bytes': sum(t['bytes'] for t in target_plans),
            'has_conflicts': len(conflicts) > 0,
            'conflict_count': len(conflicts),
            'conflicts': conflicts,
            'destination_fingerprint': destination_fingerprint,
            'backup_snapshot': backup_snapshot,
            'original_targets': valid_targets,
        }

        self._pending_restore_plans[req_id] = copy.deepcopy(plan)
        return copy.deepcopy(plan)

    def restore(
        self,
        request_id: str,
        *,
        acknowledge_conflicts: bool = False,
    ) -> Dict[str, Any]:
        """Apply restore for a previously generated, verified plan."""
        plan = self._pending_restore_plans.get(request_id)
        if not plan:
            raise Conflict(f"No restore plan found with request_id '{request_id}'")

        # Plan is strictly single-use
        del self._pending_restore_plans[request_id]

        # 1. TTL Check
        now_ts = datetime.now(timezone.utc).timestamp()
        if now_ts > plan['expires_at']:
            raise Conflict(
                f"Restore plan '{request_id}' has expired at {plan['expires_at']} (current time: {now_ts}). "
                "Re-run preview_restore."
            )

        # 2. Conflict acknowledgement check
        if plan['has_conflicts'] and not acknowledge_conflicts:
            raise Conflict(
                f"Restore plan '{request_id}' has {plan['conflict_count']} unacknowledged destination conflicts. "
                "Explicit acknowledgement is required to overwrite existing saves."
            )

        # 3. Verify destination state unchanged (including absent files not newly created)
        for path_str, orig_fp in plan['destination_fingerprint'].items():
            current_fp = _fingerprint_file(Path(path_str))
            if orig_fp['exists'] != current_fp['exists']:
                raise Conflict(
                    f"Destination existence changed for '{path_str}' between preview and restore: "
                    f"file was created after preview (was exists={orig_fp['exists']}, now exists={current_fp['exists']})"
                )
            if orig_fp['exists'] and current_fp['exists']:
                if orig_fp != current_fp:
                    raise Conflict(
                        f"Destination file '{path_str}' was modified externally between preview and restore "
                        f"(size {orig_fp['size']} -> {current_fp['size']})"
                    )

        # 4. Verify backup archive snapshot unchanged
        backup_dir = Path(plan['backup_directory'])
        current_snapshot = _snapshot_directory(backup_dir)
        if set(current_snapshot) != set(plan['backup_snapshot']):
            raise Conflict('Backup snapshot membership changed after preview; preview again')
        for snap_path, snap_meta in plan['backup_snapshot'].items():
            if snap_path not in current_snapshot:
                raise Conflict(f"backup file '{snap_path}' was removed after preview.")
            cur_meta = current_snapshot[snap_path]
            if cur_meta['size'] != snap_meta['size'] or cur_meta['mtime_ns'] != snap_meta['mtime_ns']:
                raise Conflict(f"backup file '{snap_path}' was modified after preview.")

        # Execute restore
        caps = self.check_capabilities()
        if not caps['available']:
            return {'success': False, 'error': caps['error']}

        executable = caps['resolved_path']
        argv = self._cli_argv(executable, 'restore') + ['--path', str(backup_dir), '--api', '--force', '--no-cloud-sync']
        argv.append('--')

        for t in plan['original_targets']:
            argv.append(t['manifest_title'])

        proc = self.runner(argv)
        parsed = _checked_output(proc, plan['original_targets'], operation='restore')

        return {
            'success': True,
            'request_id': request_id,
            'restored_targets': plan['targets'],
            'total_files': plan['total_files'],
            'total_bytes': plan['total_bytes'],
            'raw_output': parsed,
            'argv': argv,
        }

    def cancel_restore(self, request_id: str) -> bool:
        if request_id in self._pending_restore_plans:
            del self._pending_restore_plans[request_id]
            return True
        return False

    # --- Backups listing ---

    def list_backups(self, targets=None, *, profile_id=None, path=None):
        """Read real history from Ludusavi, including encoded game folders."""
        backup_dir = self._resolve_backup_dir(profile_id=profile_id, path=path)
        if not backup_dir.exists():return []
        valid_targets = _validate_game_targets(targets) if targets else []
        caps = self.check_capabilities()
        if not caps['available']:raise RuntimeError(caps['error'])
        argv = self._cli_argv(caps['resolved_path'], 'backups') + ['--path', str(backup_dir), '--api', '--']
        argv.extend(t['manifest_title'] for t in valid_targets)
        data = _checked_output(self.runner(argv), valid_targets, operation='backups')
        targets_by_name = {t['manifest_title']:t for t in valid_targets}
        results=[]
        for title, row in data['games'].items():
            if not isinstance(row,dict) or not isinstance(row.get('backups'),list):
                raise RuntimeError('Ludusavi history response was invalid')
            backups=[]
            for backup in row['backups']:
                if not isinstance(backup,dict) or not isinstance(backup.get('name'),str) or not isinstance(backup.get('when'),str):
                    raise RuntimeError('Ludusavi backup identity was invalid')
                backups.append({'name':backup['name'],'modified_at':backup['when'],
                                'locked':backup.get('locked') is True,'comment':backup.get('comment') or ''})
            target=targets_by_name.get(title,{})
            results.append({'id':target.get('id',''),'title':title,'manifest_title':title,
                            'source':target.get('source',''),'backup_count':len(backups),'backups':backups})
        return results
