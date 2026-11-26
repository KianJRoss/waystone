from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from PySide6.QtCore import QObject, Property, QThread, Signal, Slot

try:
    from native_services.mod_management import ModManagerService
except Exception:  # pragma: no cover
    ModManagerService = None  # type: ignore

# Compatibility alias (real class is ModManagerService)
ModManagementService = ModManagerService


def _to_qvariant(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_to_qvariant(v) for v in value]
    if isinstance(value, list):
        return [_to_qvariant(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_qvariant(v) for k, v in value.items()}
    return value


def _profile_to_ui(profile: Any) -> dict[str, Any]:
    data = _to_qvariant(profile) if not isinstance(profile, dict) else _to_qvariant(profile)
    pid = str(data.get('profile_id') or data.get('id') or '')
    return {
        **data,
        'profile_id': pid,
        'id': pid,
        'display_name': str(data.get('display_name') or data.get('name') or pid),
        'name': str(data.get('display_name') or data.get('name') or pid),
        'manager_type': str(data.get('manager_type') or data.get('manager') or ''),
        'manager': str(data.get('manager_type') or data.get('manager') or ''),
    }


def plan_profile_matches(plan: Any, profile_id: str) -> bool:
    if not isinstance(plan, dict):
        return False
    return str(plan.get('profile_id') or '') == str(profile_id or '')


class _Worker(QThread):
    # Emitted from worker thread when work finishes; result/error stored on worker.
    done = Signal(str)

    def __init__(self, op: str, fn, *args, **kwargs):
        super().__init__()
        self.op = op
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.result: Any = None
        self.error: Any = None

    def run(self) -> None:  # pragma: no cover - exercised via QThread in tests
        try:
            self.result = self._fn(*self._args, **self._kwargs)
        except Exception as e:
            self.error = e
        finally:
            # Signal completion; UI state is applied on main thread in finished handler.
            self.done.emit(self.op)


class ModManagementView(QObject):
    changed = Signal()

    def __init__(
        self,
        config_path: Optional[str] = None,
        launch_profile_store: Any = None,
        parent: Optional[QObject] = None,
        service: Any = None,
    ):
        super().__init__(parent)
        # Inert constructor: keep references only, no scans/deploys/refresh work.
        self._config_path = config_path
        self._launch_profile_store = launch_profile_store
        self._service = service
        self._configured = bool(service is not None or config_path)
        self._profiles: list[dict[str, Any]] = []
        self._selected_profile_id = ""
        self._selected_profile: dict[str, Any] = {}
        if self._configured:
            self._status = "Import a cached r2modman/BepInEx profile to get started."  # empty state not dead end
        else:
            # Startup guard: no implicit live profile fallback; fail inert and safe.
            self._status = "Mod profiles unavailable: no explicit config path or service supplied. Startup guard prevents reading live profile state."
        self._busy = False
        self._last_plan: Optional[dict[str, Any]] = None
        self._last_apply_result: Optional[dict[str, Any]] = None
        self._last_undo_result: Optional[dict[str, Any]] = None
        self._threads: list[_Worker] = []
        self._selected_game_id = ""
        self._conflict_matrix: dict[str, Any] = {}
        self._discovered_logs: list[dict[str, Any]] = []
        self._latest_log_report: dict[str, Any] = {}

    def _ensure_service(self):
        if self._service is not None:
            return self._service
        if not self._configured:
            raise RuntimeError("Mod profiles unavailable: no explicit config path or service supplied. Startup guard prevents reading live profile state.")
        if ModManagerService is None:
            raise RuntimeError("ModManagerService unavailable")
        if self._config_path is not None:
            self._service = ModManagerService(Path(self._config_path), launch_profile_store=self._launch_profile_store)
            return self._service
        raise RuntimeError("Mod profiles unavailable: no explicit config path or service supplied. Startup guard prevents reading live profile state.")

    def _set_busy(self, busy: bool) -> None:
        if self._busy != busy:
            self._busy = busy

    @Property('QVariantList', notify=changed)
    def profiles(self):
        return self._profiles

    @Property(str, notify=changed)
    def selectedProfileId(self):
        return self._selected_profile_id

    @Property('QVariantMap', notify=changed)
    def selectedProfile(self):
        return self._selected_profile

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property('QVariantMap', notify=changed)
    def lastPlan(self):
        return _to_qvariant(self._last_plan or {})

    @Property('QVariantMap', notify=changed)
    def lastApplyResult(self):
        return _to_qvariant(self._last_apply_result or {})

    @Property('QVariantMap', notify=changed)
    def lastUndoResult(self):
        return _to_qvariant(self._last_undo_result or {})

    @Property(bool, notify=changed)
    def canApply(self):
        plan = self._last_plan or {}
        # Real preview contract: can_apply/conflicts (no 'supported')
        return bool(
            not self._busy
            and plan
            and plan.get('plan_id')
            and plan.get('can_apply') is True
            and not plan.get('conflicts')
            and plan_profile_matches(plan, self._selected_profile_id)
        )

    @Property(str, notify=changed)
    def selectedGameId(self):
        return self._selected_game_id

    @Property('QVariantList', notify=changed)
    def instances(self):
        if not self._selected_game_id:
            return self._profiles
        return [
            p for p in self._profiles
            if str(p.get('game_id') or p.get('canonical_game_id') or '') == self._selected_game_id
        ]

    @Property(str, notify=changed)
    def activeInstanceId(self):
        return self._selected_profile_id

    @Property('QVariantMap', notify=changed)
    def activeInstance(self):
        return self._selected_profile

    @Property('QVariantMap', notify=changed)
    def conflictMatrix(self):
        return _to_qvariant(self._conflict_matrix or {})

    @Property('QVariantList', notify=changed)
    def discoveredLogs(self):
        return _to_qvariant(self._discovered_logs or [])

    @Property('QVariantMap', notify=changed)
    def latestLogReport(self):
        return _to_qvariant(self._latest_log_report or {})

    @Slot(str)
    def selectGame(self, game_id: str):
        self._selected_game_id = str(game_id or '')
        # Auto-select first matching instance if none or mismatched
        matching = [p for p in self._profiles if str(p.get('game_id') or p.get('canonical_game_id') or '') == self._selected_game_id]
        if matching:
            current_match = any(p['profile_id'] == self._selected_profile_id for p in matching)
            if not current_match:
                self.selectInstance(matching[0]['profile_id'])
        self.changed.emit()

    @Slot(str)
    def selectInstance(self, instance_id: str):
        self.selectProfile(instance_id)
        if instance_id:
            try:
                self.refreshConflicts(instance_id)
            except Exception:
                pass

    @Slot(str, str, bool, str, result='QVariantMap')
    def createInstance(self, game_id: str, name: str, isolate_saves: bool = False, copy_from_id: str = ""):
        try:
            svc = self._ensure_service()
            profile = svc.create_instance(game_id=game_id, name=name, isolate_saves=isolate_saves, copy_from_id=copy_from_id or None)
            self.refreshProfiles()
            self.selectInstance(profile.profile_id)
            self._status = f"Created instance '{name}' ({profile.profile_id})."
            self.changed.emit()
            return _profile_to_ui(profile)
        except Exception as e:
            self._status = f"Create instance failed: {e}"
            self.changed.emit()
            return {}

    @Slot(str, str, result='QVariantMap')
    def cloneInstance(self, instance_id: str, new_name: str):
        try:
            svc = self._ensure_service()
            profile = svc.clone_instance(instance_id=instance_id, new_name=new_name)
            self.refreshProfiles()
            self.selectInstance(profile.profile_id)
            self._status = f"Cloned instance '{new_name}' ({profile.profile_id})."
            self.changed.emit()
            return _profile_to_ui(profile)
        except Exception as e:
            self._status = f"Clone instance failed: {e}"
            self.changed.emit()
            return {}

    @Slot(str, result=bool)
    def deleteInstance(self, instance_id: str):
        try:
            svc = self._ensure_service()
            ok = svc.delete_profile(instance_id)
            if ok:
                self._status = f"Deleted instance {instance_id}."
                if self._selected_profile_id == instance_id:
                    self._selected_profile_id = ""
                    self._selected_profile = {}
                self.refreshProfiles()
            return ok
        except Exception as e:
            self._status = f"Delete instance failed: {e}"
            self.changed.emit()
            return False

    @Slot(str, str, result=bool)
    def toggleMod(self, instance_id: str, mod_id: str):
        try:
            svc = self._ensure_service()
            ok = svc.toggle_mod(instance_id, mod_id)
            self.refreshProfiles()
            if self._selected_profile_id == instance_id:
                self.selectProfile(instance_id)
                self.refreshConflicts(instance_id)
            return ok
        except Exception as e:
            self._status = f"Toggle mod failed: {e}"
            self.changed.emit()
            return False

    @Slot(str, 'QVariantList', result=bool)
    def reorderMods(self, instance_id: str, mod_ids: list):
        try:
            svc = self._ensure_service()
            ok = svc.reorder_mods(instance_id, [str(m) for m in mod_ids])
            self.refreshProfiles()
            if self._selected_profile_id == instance_id:
                self.selectProfile(instance_id)
                self.refreshConflicts(instance_id)
            return ok
        except Exception as e:
            self._status = f"Reorder mods failed: {e}"
            self.changed.emit()
            return False

    @Slot(str, result='QVariantMap')
    def refreshConflicts(self, instance_id: str):
        try:
            svc = self._ensure_service()
            conflicts = svc.detect_conflicts(instance_id)
            self._conflict_matrix = _to_qvariant(conflicts)
            self.changed.emit()
            return self._conflict_matrix
        except Exception as e:
            self._conflict_matrix = {}
            self.changed.emit()
            return {}

    @Slot(str, str, result=str)
    def exportModpack(self, instance_id: str, dest_path: str):
        try:
            svc = self._ensure_service()
            out = svc.export_modpack(instance_id, Path(dest_path))
            self._status = f"Exported modpack: {out}"
            self.changed.emit()
            return str(out)
        except Exception as e:
            self._status = f"Export modpack failed: {e}"
            self.changed.emit()
            return ""

    @Slot(str, str, result='QVariantMap')
    def importModpack(self, game_id: str, pack_path: str):
        try:
            svc = self._ensure_service()
            profile = svc.import_modpack(game_id, Path(pack_path))
            self.refreshProfiles()
            self.selectInstance(profile.profile_id)
            self._status = f"Imported modpack '{profile.display_name}'."
            self.changed.emit()
            return _profile_to_ui(profile)
        except Exception as e:
            self._status = f"Import modpack failed: {e}"
            self.changed.emit()
            return {}

    @Slot(str, result='QVariantList')
    def discoverLogs(self, search_root: str = ""):
        try:
            from native_services.mod_log_analyzer import ModLogAnalyzer
            analyzer = ModLogAnalyzer()
            extra = [Path(search_root)] if search_root and Path(search_root).exists() else []
            candidates = analyzer.discover_logs(extra_paths=extra)
            results = []
            for c in candidates:
                try:
                    st = c.stat()
                    results.append({
                        "name": c.name,
                        "path": str(c),
                        "size_bytes": st.st_size,
                        "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except Exception:
                    pass
            self._discovered_logs = results
            self.changed.emit()
            return results
        except Exception as e:
            self._status = f"Log discovery failed: {e}"
            self.changed.emit()
            return []

    @Slot(str, result='QVariantMap')
    def analyzeLogFile(self, log_path: str):
        try:
            from native_services.mod_log_analyzer import ModLogAnalyzer
            from dataclasses import asdict
            svc = self._ensure_service()
            known_mods = []
            if self._selected_profile_id and self._selected_profile_id in svc.profiles:
                known_mods = [_to_qvariant(m) for m in svc.profiles[self._selected_profile_id].mods]
            analyzer = ModLogAnalyzer()
            report = analyzer.analyze_file(Path(log_path), known_mods=known_mods)
            self._latest_log_report = _to_qvariant(asdict(report))
            self._status = f"Analyzed {Path(log_path).name}: {report.summary}"
            self.changed.emit()
            return self._latest_log_report
        except Exception as e:
            self._status = f"Log analysis failed: {e}"
            self.changed.emit()
            return {}

    @Slot('QVariantMap', result=bool)
    def applyDiagnosticFix(self, fix_dict: dict):
        try:
            action = fix_dict.get("action")
            if action == "disable_mod":
                mid = fix_dict.get("mod_id")
                if self._selected_profile_id and mid:
                    self.toggleMod(self._selected_profile_id, mid)
                    self._status = f"Applied AI fix: Disabled mod {mid}"
                    self.changed.emit()
                    return True
            self._status = f"Fix action '{action}' acknowledged."
            self.changed.emit()
            return False
        except Exception as e:
            self._status = f"Apply fix failed: {e}"
            self.changed.emit()
            return False

    @Property(bool, notify=changed)
    def canUndo(self):
        apply_res = self._last_apply_result or {}
        op = str(apply_res.get('operation_id') or apply_res.get('plan_id') or '')
        # Real apply contract: status == 'applied'
        return bool(
            not self._busy
            and op
            and apply_res.get('status') == 'applied'
            and str(apply_res.get('profile_id') or '') == self._selected_profile_id
        )

    @Slot(str, result=str)
    def localFolder(self, value):
        from PySide6.QtCore import QUrl
        url=QUrl(value)
        path=url.toLocalFile() if url.isLocalFile() else ''
        return path if Path(path).is_absolute() else ''

    @Slot()
    def refreshProfiles(self):
        """All-profile overview by default. Reads app-owned/config state; no game scan/deploy."""
        if not self._configured and self._service is None:
            self._profiles = []
            self._selected_profile_id = ""
            self._selected_profile = {}
            self._status = "Mod profiles unavailable: no explicit config path or service supplied. Startup guard prevents reading live profile state."
            self.changed.emit()
            return
        try:
            svc = self._ensure_service()
            load = getattr(svc, 'load', None)
            if callable(load):
                load()
            listed = []
            list_profiles = getattr(svc, 'list_profiles', None)
            if callable(list_profiles):
                listed = list_profiles() or []
            elif hasattr(svc, 'profiles'):
                profs = getattr(svc, 'profiles')
                listed = list(profs.values()) if isinstance(profs, dict) else list(profs)
            self._profiles = [_profile_to_ui(p) for p in listed]
            ids = {str(p.get('profile_id', '')) for p in self._profiles}
            if self._selected_profile_id and self._selected_profile_id not in ids:
                self._selected_profile_id = ""
                self._selected_profile = {}
            if not self._profiles:
                self._status = "No cached mod profiles yet. Import a cached r2modman/BepInEx profile to continue."
            else:
                self._status = f"Loaded {len(self._profiles)} mod profile(s)."
        except Exception as e:
            self._status = f"Failed to refresh profiles: {e}"
        self.changed.emit()

    @Slot(str)
    def selectProfile(self, profile_id: str):
        self._selected_profile_id = str(profile_id or "")
        self._selected_profile = {}
        self._last_plan = None
        self._last_apply_result = None
        self._last_undo_result = None
        for p in self._profiles:
            if str(p.get('profile_id', '')) == self._selected_profile_id:
                self._selected_profile = p
                break
        self._status = f"Selected profile {self._selected_profile_id}." if self._selected_profile_id else "No profile selected."
        self.changed.emit()

    def _start_op(self, op: str, fn, *args):
        if self._busy:
            return
        self._set_busy(True)
        self.changed.emit()
        worker = _Worker(op, fn, *args)
        # Keep strong reference until joined/finished; do not delete prematurely.
        self._threads.append(worker)
        worker.done.connect(self._on_worker_done)
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    @Slot(str)
    def previewSelected(self, install_directory: str = ""):
        if not self._selected_profile_id:
            self._status = "Select a profile before preview."
            self.changed.emit()
            return
        svc = self._ensure_service()
        inst = install_directory or str(self._selected_profile.get('install_directory') or '')
        self._status = "Previewing deployment plan..."
        self.changed.emit()
        self._start_op('preview', svc.preview_r2modman_bepinex_deployment, self._selected_profile_id, inst)

    @Slot()
    def applyReviewedPlan(self):
        plan = self._last_plan
        if not self.canApply or not isinstance(plan, dict):
            self._status = "Cannot apply: review an applicable plan first."
            self.changed.emit()
            return
        svc = self._ensure_service()
        self._status = "Applying reviewed deployment plan..."
        self.changed.emit()
        self._start_op('apply', svc.apply_r2modman_bepinex_deployment, plan)

    @Slot()
    def undoSelectedOperation(self):
        apply_res = self._last_apply_result or {}
        op_id = str(apply_res.get('operation_id') or apply_res.get('plan_id') or '')
        if not self.canUndo or not op_id:
            self._status = "No exact applied operation selected to undo."
            self.changed.emit()
            return
        svc = self._ensure_service()
        self._status = "Undoing selected deployment operation..."
        self.changed.emit()
        self._start_op('undo', svc.undo_r2modman_bepinex_deployment, op_id)

    @Slot(str, str, str, str)
    def importCachedR2Profile(self, profile_directory: str, install_directory: str, canonical_game_id: str, game_title: str):
        """Explicit import for cached r2modman profile so empty state is not a dead end."""
        profile_directory = (profile_directory or '').strip()
        install_directory = (install_directory or '').strip()
        canonical_game_id = (canonical_game_id or '').strip()
        game_title = (game_title or '').strip() or canonical_game_id or 'Game'
        if not profile_directory or not install_directory or not canonical_game_id:
            self._status = "Import requires profile directory, install directory, and canonical game id."
            self.changed.emit()
            return
        try:
            svc = self._ensure_service()
            profile = svc.parse_r2modman_profile(
                canonical_game_id=canonical_game_id,
                install_directory=install_directory,
                profile_directory=profile_directory,
                game_title=game_title,
            )
            svc.create_or_register_profile(profile)
            self._status = f"Imported cached profile {profile.profile_id}."
            self.refreshProfiles()
            self.selectProfile(profile.profile_id)
        except Exception as e:
            self._status = f"Import failed: {e}"
            self.changed.emit()

    @Slot(str)
    def _on_worker_done(self, op: str):
        sender = self.sender()
        if sender is None:
            return
        # Result/error are stored on worker; state update happens here on main thread.
        try:
            error = getattr(sender, 'error', None)
            result = getattr(sender, 'result', None)
            if error is not None:
                self._status = f"{op} failed: {error}"
            elif op == 'preview':
                self._last_plan = _to_qvariant(result) if isinstance(result, dict) else {}
                can_apply = bool(self._last_plan.get('can_apply')) and not self._last_plan.get('conflicts')
                self._status = "Preview ready (can apply)." if can_apply else "Preview ready (conflicts or cannot apply)."
            elif op == 'apply':
                self._last_apply_result = _to_qvariant(result) if isinstance(result, dict) else {}
                if self._last_apply_result.get('status') == 'applied':
                    self._status = "Apply completed (applied)."
                else:
                    self._status = f"Apply finished with status: {self._last_apply_result.get('status', 'unknown')}"
            elif op == 'undo':
                self._last_undo_result = _to_qvariant(result) if isinstance(result, dict) else {}
                if self._last_undo_result.get('status') == 'undone':
                    self._status = "Undo completed (undone)."
                else:
                    self._status = f"Undo finished with status: {self._last_undo_result.get('status', 'unknown')}"
        finally:
            self._set_busy(False)
            self.changed.emit()

    @Slot()
    def _on_worker_finished(self):
        # Thread is finished; keep strong Python/Qt refs alive (joined in shutdown).
        # Avoid premature deleteLater while result handling or test lifecycle may still reference it.
        return

    @Slot()
    def shutdown(self):
        # Keep references until joined; do not drop running threads.
        for t in list(self._threads):
            try:
                t.wait()
            except Exception:
                pass
        # All joined; safe to release refs.
        self._threads = [t for t in self._threads if t.isRunning()]
