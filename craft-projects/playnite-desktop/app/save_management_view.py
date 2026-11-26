"""Qt Presentation adapter and view model for Save Management.

Exposes thread-safe, non-blocking asynchronous save operations, explicit manifest bindings,
profiles CRUD, capability status reporting, and restore confirmation flow.
Zero game execution, zero implicit fallback from manifest to display title.
"""
from __future__ import annotations

from pathlib import Path
import json
import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
    QThread,
)

from native_services.save_management import (
    SaveManager,
    SCOPE_GAME,
    SCOPE_BULK,
    Conflict,
    default_runner,
)


class SaveJobWorker(QThread):
    """Bounded, cancellable background worker for save operations to keep UI non-blocking."""
    jobFinished = Signal(dict)
    jobFailed = Signal(str)

    def __init__(self, task_fn: Callable[[], Dict[str, Any]], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._task_fn = task_fn
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            if self._is_cancelled:
                return
            res = self._task_fn()
            if not self._is_cancelled:
                self.jobFinished.emit(res)
        except Exception as exc:
            if not self._is_cancelled:
                self.jobFailed.emit(str(exc))


class SaveManagementView(QObject):
    """QObject wrapper exposing SaveManager capabilities and operations."""

    statusChanged = Signal()
    profilesChanged = Signal()
    bindingsChanged = Signal()
    jobStateChanged = Signal()
    restorePlanChanged = Signal()
    backupsChanged = Signal()
    errorChanged = Signal()
    operationCompleted = Signal(str, bool, str)  # job_type, success, message

    def __init__(
        self,
        config_path: Path | str,
        runner: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = SaveManager(config_path, runner=runner)
        self._active_job = False
        self._active_worker: Optional[SaveJobWorker] = None
        self._worker_lock = threading.Lock()
        self._is_shutting_down = False
        self._selected_profile = ''
        self._manifest_candidates = []
        self._last_job_result: Dict[str, Any] = {}
        self._pending_plan: Dict[str, Any] = {}
        self._current_backups: List[Dict[str, Any]] = []
        self._error = ''
        # Do NOT run the tool at startup. Instead restore the last successful check when the executable is unchanged
        # (Kian 2026-11-23: Ludusavi had to be re-checked by hand after every restart).
        self._capabilities: Dict[str, Any] = self._cached_capabilities() or {
            'available': False,
            'version': None,
            'tool_path': self._manager.get_tool_path(),
            'error': 'Checking Ludusavi when Saves opens…',
        }

    # --- Remembered tool check ---

    def _cache_file(self) -> Path:
        return Path(self._manager.config_path).parent / 'ludusavi-check.json'

    def _tool_fingerprint(self) -> Dict[str, Any] | None:
        try:
            path = Path(self._manager.get_tool_path()).expanduser()
            st = path.stat()
            return {'tool_path': str(path), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
        except (OSError, TypeError, ValueError):
            return None

    def _cached_capabilities(self) -> Dict[str, Any] | None:
        try:
            data = json.loads(self._cache_file().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get('fingerprint') != self._tool_fingerprint():
            return None  # tool moved or updated: check again
        caps = data.get('capabilities')
        return caps if isinstance(caps, dict) and caps.get('available') else None

    def _remember_capabilities(self, caps: Dict[str, Any]) -> None:
        fingerprint = self._tool_fingerprint()
        try:
            if caps.get('available') and fingerprint:
                self._cache_file().write_text(json.dumps({'fingerprint': fingerprint, 'capabilities': caps}), encoding='utf-8')
            else:
                self._cache_file().unlink(missing_ok=True)
        except OSError:
            pass

    @Slot()
    def ensureToolChecked(self) -> None:
        """Called when Saves opens: check once in the background if we do not already know the tool works."""
        if not self.toolAvailable and not self._active_job:
            self.checkCapabilitiesAsync()

    # --- Teardown & Lifecycle ---

    @Slot(result=bool)
    def shutdown(self) -> bool:
        """Safely cancel, stop owned subprocesses, and cooperatively wait for active worker without QThread.terminate().
        Returns True if cleanly shut down, or False if worker could not finish in bounded time."""
        self._is_shutting_down = True
        self._manager.cancel_active_processes()
        with self._worker_lock:
            worker = self._active_worker
            if worker:
                worker.cancel()
                if worker.isRunning():
                    worker.quit()
                    if not worker.wait(3000):
                        return False
                self._active_worker = None
        self._active_job = False
        self.jobStateChanged.emit()
        return True

    # --- Properties ---

    @Property(bool, notify=statusChanged)
    def toolAvailable(self) -> bool:
        return bool(self._capabilities.get('available', False))

    @Property(str, notify=statusChanged)
    def toolStatus(self) -> str:
        if self.toolAvailable:
            ver = self._capabilities.get('version', '')
            return f"Ready ({ver})" if ver else "Ready"
        return self._capabilities.get('error', 'Tool not available')

    @Property(str, notify=statusChanged)
    def toolPath(self) -> str:
        return self._manager.get_tool_path()

    @Property(str, notify=statusChanged)
    def defaultBackupDirectory(self) -> str:
        return self._manager.get_default_backup_directory()

    @Property(bool, notify=jobStateChanged)
    def isJobRunning(self) -> bool:
        return self._active_job

    @Property('QVariantList', notify=profilesChanged)
    def profiles(self) -> List[Dict[str, Any]]:
        return self._manager.list_profiles()

    @Property('QVariantMap', notify=restorePlanChanged)
    def pendingRestorePlan(self) -> Dict[str, Any]:
        return self._pending_plan

    @Property(bool, notify=restorePlanChanged)
    def hasConflicts(self) -> bool:
        return bool(self._pending_plan.get('has_conflicts', False))

    @Property(int, notify=restorePlanChanged)
    def conflictCount(self) -> int:
        return int(self._pending_plan.get('conflict_count', 0))

    @Property('QVariantList', notify=restorePlanChanged)
    def conflicts(self) -> List[Dict[str, Any]]:
        return list(self._pending_plan.get('conflicts', []))

    @Property('QVariantList', notify=backupsChanged)
    def currentBackups(self) -> List[Dict[str, Any]]:
        return self._current_backups

    @Property('QVariantMap', notify=jobStateChanged)
    def lastJobResult(self) -> Dict[str, Any]:
        return self._last_job_result

    @Property(str, notify=errorChanged)
    def errorMessage(self) -> str:
        return self._error

    # --- Async Worker Dispatcher ---

    def _dispatch_async_job(
        self,
        job_type: str,
        task_fn: Callable[[], Dict[str, Any]],
        on_success: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        with self._worker_lock:
            if self._is_shutting_down:
                self._error = 'Save manager is shutting down (cannot spawn new operations)'
                self.errorChanged.emit()
                return False

            if self._active_job or (self._active_worker and self._active_worker.isRunning()):
                self._error = f"Another operation is already running (cannot start {job_type})"
                self.errorChanged.emit()
                return False

            self._active_job = True
            self.jobStateChanged.emit()

            worker = SaveJobWorker(task_fn, parent=self)
            self._active_worker = worker

            self._pending_async_result = None
            self._job_type = job_type
            self._job_success_callback = on_success
            worker.jobFinished.connect(self._on_async_result)
            worker.jobFailed.connect(self._on_async_error)
            worker.finished.connect(self._on_async_thread_finished)
            worker.finished.connect(worker.deleteLater)
            worker.start()
            return True

    @Slot(dict)
    def _on_async_result(self, res):
        # Publish terminal UI state only after QThread.finished, not while run()
        # is still returning. This prevents success->next-job overlap races.
        self._pending_async_result=res

    @Slot(str)
    def _on_async_error(self, message):
        self._pending_async_result={'success':False,'error':message}

    @Slot()
    def _on_async_thread_finished(self):
        result=getattr(self,'_pending_async_result',None)
        callback=getattr(self,'_job_success_callback',None)
        job_type=getattr(self,'_job_type','save')
        self._pending_async_result=None
        self._job_success_callback=None
        with self._worker_lock:
            self._active_worker=None
            self._active_job=False
        if result is not None:
            self._last_job_result=result
            if callback:callback(result)
            self._error=result.get('error','')
            self.errorChanged.emit()
            self.operationCompleted.emit(job_type,bool(result.get('success',False)),self._error or 'Success')
        self.jobStateChanged.emit()
        if getattr(self, '_recheck_capabilities_pending', False) and not self._is_shutting_down:
            self._recheck_capabilities_pending = False
            self.checkCapabilitiesAsync()

    # --- Capability & Config Slots ---

    @Slot(result='QVariantMap')
    def checkCapabilities(self) -> Dict[str, Any]:
        """Synchronously check capabilities or return cached."""
        self._capabilities = self._manager.check_capabilities()
        self._remember_capabilities(self._capabilities)
        self.statusChanged.emit()
        return self._capabilities

    @Slot()
    def checkCapabilitiesAsync(self) -> bool:
        """Route capability discovery asynchronously to keep UI responsive."""
        with self._worker_lock:
            if (self._active_job or (self._active_worker and self._active_worker.isRunning())) and getattr(self, '_job_type', '') == 'check_capabilities':
                self._recheck_capabilities_pending = True
                return True
        def run_check() -> Dict[str, Any]:
            caps = self._manager.check_capabilities()
            return {'success': caps.get('available', False), 'capabilities': caps}

        def on_ok(res: Dict[str, Any]) -> None:
            self._capabilities = res.get('capabilities', {})
            self._remember_capabilities(self._capabilities)
            self.statusChanged.emit()

        return self._dispatch_async_job('check_capabilities', run_check, on_ok)

    @Slot(str, bool, result=bool)
    @Slot(str, result=bool)
    def setToolPath(self, path: str, check_now: bool = True) -> bool:
        """Save tool path. Can optionally skip immediate synchronous check."""
        try:
            self.cancelRestore()
            self._manager.set_tool_path(path)
            if check_now:
                self.checkCapabilities()
            else:
                self.statusChanged.emit()
            self._error = ''
            self.errorChanged.emit()
            return True
        except Exception as exc:
            self._error = str(exc)
            self.errorChanged.emit()
            return False

    @Slot(str, result=bool)
    def setDefaultBackupDirectory(self, path: str) -> bool:
        try:
            self.cancelRestore()
            self._manager.set_default_backup_directory(path)
            self.statusChanged.emit()
            self._error = ''
            self.errorChanged.emit()
            return True
        except Exception as exc:
            self._error = str(exc)
            self.errorChanged.emit()
            return False

    @Property(str, notify=profilesChanged)
    def selectedProfileId(self):return self._selected_profile

    @Property('QVariantList', notify=bindingsChanged)
    def manifestCandidates(self):return self._manifest_candidates

    @Slot(str, result=bool)
    def findManifestAsync(self, query):
        def found(res):
            self._manifest_candidates=res.get('candidates',[])
            self.bindingsChanged.emit()
        return self._dispatch_async_job('find_manifest',
            lambda:{'success':True,'candidates':self._manager.find_manifest_titles(query)},found)

    @Slot(result=bool)
    def updateDefinitionsAsync(self):
        return self._dispatch_async_job('update_definitions',self._manager.update_definitions)

    @Slot(str, result=bool)
    def selectProfile(self, profile_id):
        if self._active_job:return False
        if profile_id and not self._manager.get_profile(profile_id):return False
        self.cancelRestore()
        self._selected_profile=profile_id
        self.profilesChanged.emit()
        return True

    # --- Profile CRUD Slots ---

    @Slot(str, str, str, str, result='QVariantMap')
    @Slot(str, str, str, result='QVariantMap')
    @Slot(str, str, result='QVariantMap')
    def createProfile(
        self,
        profile_id: str,
        name: str,
        backup_directory: str = '',
        wine_prefix: str = '',
    ) -> Dict[str, Any]:
        try:
            prof = self._manager.create_or_update_profile(
                profile_id=profile_id,
                name=name,
                backup_directory=backup_directory,
                wine_prefix=wine_prefix,
            )
            self.profilesChanged.emit()
            self._error = ''
            self.errorChanged.emit()
            return prof
        except Exception as exc:
            self._error = str(exc)
            self.errorChanged.emit()
            return {}

    @Slot(str, result=bool)
    def deleteProfile(self, profile_id: str) -> bool:
        ok = self._manager.delete_profile(profile_id)
        if ok:
            self.profilesChanged.emit()
        return ok

    @Slot(result='QVariantList')
    def listProfiles(self) -> List[Dict[str, Any]]:
        return self._manager.list_profiles()

    # --- Explicit Manifest Binding Slots ---

    @Slot(str, str, result=str)
    def setManifestBinding(self, game_id: str, manifest_title: str) -> str:
        """Store explicit manifest title binding for canonical game ID."""
        try:
            m_title = self._manager.set_manifest_binding(game_id, manifest_title)
            self.bindingsChanged.emit()
            return m_title
        except Exception as exc:
            self._error = str(exc)
            self.errorChanged.emit()
            return ''

    @Slot(str, result=str)
    def getManifestBinding(self, game_id: str) -> str:
        return self._manager.get_manifest_binding(game_id)

    # --- Backups listing Slots ---

    @Slot(str, str, str, str, result='QVariantList')
    @Slot(str, str, str, result='QVariantList')
    def listBackups(
        self,
        game_id: str = '',
        title: str = '',
        source: str = '',
        manifest_title: str = '',
    ) -> List[Dict[str, Any]]:
        try:
            m_title = (manifest_title or (self.getManifestBinding(game_id) if game_id else '')).strip()
            targets = None
            if title or game_id:
                if not m_title:
                    raise ValueError(f"Game '{game_id or title}' has no explicit manifest binding.")
                targets = [{'id': game_id, 'title': title, 'manifest_title': m_title, 'source': source}]
            self._current_backups = self._manager.list_backups(targets, profile_id=self._selected_profile or None)
            self.backupsChanged.emit()
            return self._current_backups
        except Exception as exc:
            self._error = str(exc)
            self.errorChanged.emit()
            return []

    @Slot(str, str, str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def listBackupsAsync(
        self,
        game_id: str = '',
        title: str = '',
        source: str = '',
        manifest_title: str = '',
    ) -> bool:
        """Non-blocking asynchronous backups inventory fetch."""
        m_title = (manifest_title or (self.getManifestBinding(game_id) if game_id else '')).strip()
        targets = None
        if title or game_id:
            if not m_title:
                self._error = f"Game '{game_id or title}' has no explicit manifest binding."
                self.errorChanged.emit()
                return False
            targets = [{'id': game_id, 'title': title, 'manifest_title': m_title, 'source': source}]

        def do_list() -> Dict[str, Any]:
            items = self._manager.list_backups(targets, profile_id=self._selected_profile or None)
            return {'success': True, 'backups': items}

        def on_list_ok(res: Dict[str, Any]) -> None:
            self._current_backups = res.get('backups', [])
            self.backupsChanged.emit()

        return self._dispatch_async_job('list_backups', do_list, on_list_ok)

    # --- Backup Slots ---

    @Slot(str, str, str, bool, str, result='QVariantMap')
    @Slot(str, str, str, bool, result='QVariantMap')
    def backupGame(
        self,
        game_id: str,
        title: str,
        source: str = '',
        preview: bool = False,
        manifest_title: str = '',
    ) -> Dict[str, Any]:
        """Backup single game synchronously. Requires explicit manifest binding (NO fallback to title)."""
        with self._worker_lock:
            if self._is_shutting_down:
                self._error = 'Save manager is shutting down (cannot spawn new operations)'
                self.errorChanged.emit()
                return {'success': False, 'error': self._error}

        if not self.toolAvailable:
            self._error = self.toolStatus
            self.errorChanged.emit()
            return {'success': False, 'error': self._error}

        m_title = (manifest_title or self.getManifestBinding(game_id)).strip()
        if not m_title:
            self._error = (
                f"Game '{game_id}' ('{title}') has no explicit manifest binding. "
                "Specify manifest title in the dashboard."
            )
            self.errorChanged.emit()
            return {'success': False, 'error': self._error}

        self._active_job = True
        self.jobStateChanged.emit()
        try:
            targets = [{'id': game_id, 'title': title, 'manifest_title': m_title, 'source': source}]
            res = self._manager.backup(targets, scope=SCOPE_GAME, preview=preview, force=not preview, profile_id=self._selected_profile or None)
            self._last_job_result = res
            self._error = res.get('error', '')
            self.errorChanged.emit()
            self.jobStateChanged.emit()
            self.operationCompleted.emit(
                'backup_preview' if preview else 'backup',
                res['success'],
                self._error or 'Success',
            )
            return res
        except Exception as exc:
            self._error = str(exc)
            self._last_job_result = {'success': False, 'error': self._error}
            self.errorChanged.emit()
            self.jobStateChanged.emit()
            self.operationCompleted.emit('backup_preview' if preview else 'backup', False, self._error)
            return self._last_job_result
        finally:
            self._active_job = False
            self.jobStateChanged.emit()

    @Slot(str, str, str, bool, str, result=bool)
    @Slot(str, str, str, bool, result=bool)
    def backupGameAsync(
        self,
        game_id: str,
        title: str,
        source: str = '',
        preview: bool = False,
        manifest_title: str = '',
    ) -> bool:
        """Asynchronous non-blocking backup execution."""
        with self._worker_lock:
            if self._is_shutting_down:
                self._error = 'Save manager is shutting down (cannot spawn new operations)'
                self.errorChanged.emit()
                return False

        if not self.toolAvailable:
            self._error = self.toolStatus
            self.errorChanged.emit()
            return False

        m_title = (manifest_title or self.getManifestBinding(game_id)).strip()
        if not m_title:
            self._error = (
                f"Game '{game_id}' ('{title}') has no explicit manifest binding. "
                "Specify manifest title in the dashboard."
            )
            self.errorChanged.emit()
            return False

        targets = [{'id': game_id, 'title': title, 'manifest_title': m_title, 'source': source}]
        job_type = 'backup_preview' if preview else 'backup'

        return self._dispatch_async_job(
            job_type,
            lambda: self._manager.backup(targets, scope=SCOPE_GAME, preview=preview, force=not preview, profile_id=self._selected_profile or None),
        )

    @Slot('QVariantList', bool, result='QVariantMap')
    def backupBulk(self, raw_targets: List[Dict[str, Any]], preview: bool = False) -> Dict[str, Any]:
        """Bulk backup. Every item must have explicit manifest_title or saved manifest binding."""
        if not self.toolAvailable:
            self._error = self.toolStatus
            self.errorChanged.emit()
            return {'success': False, 'error': self._error}

        enriched_targets = []
        for item in raw_targets:
            t = dict(item)
            c_id = t.get('id', '')
            if not t.get('manifest_title'):
                t['manifest_title'] = self.getManifestBinding(c_id)
            if not t.get('manifest_title'):
                self._error = (
                    f"Game '{c_id}' ({t.get('title', 'untitled')}) has no explicit manifest binding. "
                    "Inference or fallback to display title is strictly disallowed."
                )
                self.errorChanged.emit()
                return {'success': False, 'error': self._error}
            enriched_targets.append(t)

        self._active_job = True
        self.jobStateChanged.emit()
        try:
            res = self._manager.backup(enriched_targets, scope=SCOPE_BULK, preview=preview, force=not preview, profile_id=self._selected_profile or None)
            self._last_job_result = res
            self._error = res.get('error', '')
            self.errorChanged.emit()
            self.jobStateChanged.emit()
            self.operationCompleted.emit(
                'backup_bulk_preview' if preview else 'backup_bulk',
                res['success'],
                self._error or 'Success',
            )
            return res
        except Exception as exc:
            self._error = str(exc)
            self._last_job_result = {'success': False, 'error': self._error}
            self.errorChanged.emit()
            self.jobStateChanged.emit()
            self.operationCompleted.emit(
                'backup_bulk_preview' if preview else 'backup_bulk',
                False,
                self._error,
            )
            return self._last_job_result
        finally:
            self._active_job = False
            self.jobStateChanged.emit()

    # --- Restore Preview & Apply Slots ---

    @Slot(str, str, str, str, result='QVariantMap')
    @Slot(str, str, str, result='QVariantMap')
    def previewRestore(
        self,
        game_id: str,
        title: str,
        source: str = '',
        manifest_title: str = '',
    ) -> Dict[str, Any]:
        """Generate verified restore preview plan."""
        if not self.toolAvailable:
            self._error = self.toolStatus
            self.errorChanged.emit()
            return {'success': False, 'error': self._error}

        m_title = (manifest_title or self.getManifestBinding(game_id)).strip()
        if not m_title:
            self._error = (
                f"Game '{game_id}' ('{title}') has no explicit manifest binding. "
                "Specify manifest title in the dashboard."
            )
            self.errorChanged.emit()
            return {'success': False, 'error': self._error}

        self._active_job = True
        self.jobStateChanged.emit()
        try:
            targets = [{'id': game_id, 'title': title, 'manifest_title': m_title, 'source': source}]
            plan = self._manager.preview_restore(targets, scope=SCOPE_GAME, profile_id=self._selected_profile or None)
            self._pending_plan = plan
            self.restorePlanChanged.emit()
            self._error = plan.get('error', '')
            self.errorChanged.emit()
            return plan
        except Exception as exc:
            self._error = str(exc)
            self._pending_plan = {}
            self.restorePlanChanged.emit()
            self.errorChanged.emit()
            return {'success': False, 'error': self._error}
        finally:
            self._active_job = False
            self.jobStateChanged.emit()

    @Slot(str, str, str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def previewRestoreAsync(
        self,
        game_id: str,
        title: str,
        source: str = '',
        manifest_title: str = '',
    ) -> bool:
        """Asynchronous non-blocking restore preview."""
        if not self.toolAvailable:
            self._error = self.toolStatus
            self.errorChanged.emit()
            return False

        m_title = (manifest_title or self.getManifestBinding(game_id)).strip()
        if not m_title:
            self._error = (
                f"Game '{game_id}' ('{title}') has no explicit manifest binding. "
                "Specify manifest title in the dashboard."
            )
            self.errorChanged.emit()
            return False

        targets = [{'id': game_id, 'title': title, 'manifest_title': m_title, 'source': source}]

        def do_preview() -> Dict[str, Any]:
            plan = self._manager.preview_restore(targets, scope=SCOPE_GAME, profile_id=self._selected_profile or None)
            plan['success'] = True
            return plan

        def on_preview_ok(plan: Dict[str, Any]) -> None:
            self._pending_plan = plan
            self.restorePlanChanged.emit()

        return self._dispatch_async_job('restore_preview', do_preview, on_preview_ok)

    @Slot(str, bool, result='QVariantMap')
    @Slot(str, result='QVariantMap')
    def applyRestore(self, request_id: str, acknowledge_conflicts: bool = False) -> Dict[str, Any]:
        """Apply restore plan with conflict validation."""
        if not self.toolAvailable:
            self._error = self.toolStatus
            self.errorChanged.emit()
            return {'success': False, 'error': self._error}

        self._active_job = True
        self.jobStateChanged.emit()
        try:
            res = self._manager.restore(request_id, acknowledge_conflicts=acknowledge_conflicts)
            res['job_type'] = 'restore'
            self._pending_plan = {}
            self.restorePlanChanged.emit()
            self._last_job_result = res
            self._error = res.get('error', '')
            self.errorChanged.emit()
            self.jobStateChanged.emit()
            self.operationCompleted.emit('restore', res['success'], self._error or 'Success')
            return res
        except Exception as exc:
            self._error = str(exc)
            self._last_job_result = {'success': False, 'error': self._error}
            self.errorChanged.emit()
            self.jobStateChanged.emit()
            self.operationCompleted.emit('restore', False, self._error)
            return self._last_job_result
        finally:
            self._active_job = False
            self.jobStateChanged.emit()

    @Slot(str, bool, result=bool)
    @Slot(str, result=bool)
    def applyRestoreAsync(self, request_id: str, acknowledge_conflicts: bool = False) -> bool:
        """Asynchronous non-blocking restore apply."""
        if not self.toolAvailable:
            self._error = self.toolStatus
            self.errorChanged.emit()
            return False

        def do_apply() -> Dict[str, Any]:
            res = self._manager.restore(request_id, acknowledge_conflicts=acknowledge_conflicts)
            res['job_type'] = 'restore'
            return res

        def on_apply_ok(res: Dict[str, Any]) -> None:
            self._pending_plan = {}
            self.restorePlanChanged.emit()

        return self._dispatch_async_job('restore', do_apply, on_apply_ok)

    @Slot(result=bool)
    def cancelRestore(self) -> bool:
        req_id = self._pending_plan.get('request_id', '')
        if req_id:
            self._manager.cancel_restore(req_id)
        self._pending_plan = {}
        self.restorePlanChanged.emit()
        return True
