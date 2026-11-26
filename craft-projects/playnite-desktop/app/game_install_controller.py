"""Game-scoped install and update actions and tracking; never navigates to Downloads.

Constructing/opening the tracker is inert unless auto_check_updates is explicitly enabled.
Only install/update/confirm are user actions.
One backend job at a time; exact queued identities and destinations are retained.
"""
import logging
import os
import threading
from pathlib import Path
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer
from native_services.installation_library import catalog_install_choices, PROVIDERS
from native_services.provider_jobs import DEFAULT_TOOL_PATHS
from native_services.game_updates import (
    GameUpdateDetector,
    GameUpdateInfo,
    UPDATE_CHECK_INTERVAL_MS,
    build_catalog_product_index,
    normalize_config_key,
)

TERMINAL = {'completed', 'failed', 'cancelled', 'handed_off', 'invalid', 'unsupported'}


class GameInstallController(QObject):
    changed = Signal()
    updatesChanged = Signal()
    # Detection runs Legendary/Nile network checks (seconds each); it never runs on the UI thread.
    _detectionFinished = Signal(object)

    def __init__(
        self,
        manager,
        catalog=None,
        parent=None,
        *,
        tool_paths=None,
        detector=None,
        launch_view=None,
        library_model=None,
        connection_view=None,
        auto_check_updates=False,
    ):
        super().__init__(parent)
        self.manager = manager
        self._catalog = catalog if isinstance(catalog, dict) else {'games': []}
        self.choices = catalog_install_choices(self._catalog)
        self.tools = dict(DEFAULT_TOOL_PATHS if tool_paths is None else tool_paths)
        self._detector = detector
        self._launch_view = launch_view
        self._library_model = library_model
        self._connection_view = connection_view
        self._jobs = []
        self._queue = []
        self._current = None
        self._prompt = {}
        self._serial = 0
        self._driving = False
        self._closed = False
        self._auto = False
        self._update_revision = 0
        self._updates_by_game = {}
        self._updates_by_product = {}
        self._checking = False
        self._detectionFinished.connect(self._on_detection_finished)

        self._update_timer = QTimer(self)
        self._update_timer.setInterval(UPDATE_CHECK_INTERVAL_MS)
        self._update_timer.timeout.connect(self.checkUpdates)

        manager.changed.connect(self._sync)
        if self._launch_view is not None and hasattr(self._launch_view, 'changed'):
            self._launch_view.changed.connect(self._on_launch_state_changed)
        if auto_check_updates:
            self._update_timer.start()
            QTimer.singleShot(50, self.checkUpdates)

    def _on_launch_state_changed(self):
        self.changed.emit()

    @Property('QVariantList', notify=changed)
    def jobs(self):
        return [dict(r) for r in reversed(self._jobs)]

    @Property('QVariantMap', notify=changed)
    def prompt(self):
        return dict(self._prompt)

    @Property(int, notify=changed)
    def activeCount(self):
        return sum(r['phase'] not in TERMINAL for r in self._jobs)

    @Property(int, notify=updatesChanged)
    def updateRevision(self):
        return self._update_revision

    @Property(int, notify=updatesChanged)
    def pendingUpdateCount(self):
        return len(self._updates_by_game)

    @Property('QVariantList', notify=updatesChanged)
    def updates(self):
        rows = []
        for gid, info in self._updates_by_game.items():
            d = info.to_dict()
            d['gameId'] = gid
            rows.append(d)
        return rows

    def setDetector(self, detector):
        self._detector = detector

    def setLaunchView(self, launch_view):
        self._launch_view = launch_view

    def setLibraryModel(self, library_model):
        self._library_model = library_model
        self._sync_library_model_updates()

    def setConnectionView(self, connection_view):
        self._connection_view = connection_view

    def _canonical_id(self, game_id):
        if not isinstance(game_id, str) or not game_id:
            return ''
        if self._library_model is not None and hasattr(self._library_model, 'representativeFor'):
            rep = self._library_model.representativeFor(game_id)
            if rep:
                return rep
        return game_id

    def _candidate_game_ids(self, game_id):
        if not isinstance(game_id, str) or not game_id:
            return []
        ids = [game_id]
        rep = self._canonical_id(game_id)
        if rep and rep not in ids:
            ids.insert(0, rep)
        if self._library_model is not None:
            for g in getattr(self._library_model, 'games', ()):
                if g.get('id') in ids:
                    for linked in g.get('linked_record_ids', ()):
                        if linked not in ids:
                            ids.append(linked)
        return ids

    def _active_config_roots(self):
        roots = set()
        for r in self._jobs:
            if r.get('phase') not in TERMINAL and r.get('config_root'):
                roots.add(normalize_config_key(r['config_root']))
        return roots

    def _ensure_detector(self):
        if self._detector is not None:
            return self._detector
        managed = getattr(self.manager, '_managed_accounts', {})
        epic_bin, epic_cfg = managed.get('epic', (None, None))
        nile_bin, nile_cfg = managed.get('amazon', (None, None))
        self._detector = GameUpdateDetector(
            waystone_epic_binary=Path(epic_bin) if epic_bin else None,
            waystone_epic_config=Path(epic_cfg) if epic_cfg else None,
            waystone_nile_binary=Path(nile_bin) if nile_bin else None,
            waystone_nile_config=Path(nile_cfg) if nile_cfg else None,
        )
        return self._detector

    @Property(bool, notify=updatesChanged)
    def checkingUpdates(self):
        return self._checking

    @Slot(result=int)
    def checkUpdates(self):
        """Start a background update check; results arrive on the UI thread. Returns the current count."""
        if self._closed or self._checking:
            return len(self._updates_by_game)
        detector = self._ensure_detector()
        active = self._active_config_roots()
        self._checking = True
        self.updatesChanged.emit()

        def work():
            try:
                detected = detector.detect_all(active_config_roots=active)
            except Exception:
                logging.getLogger('waystone.game_updates').exception('Update check failed')
                detected = []
            try:
                self._detectionFinished.emit(detected)
            except RuntimeError:
                pass  # controller deleted while the check ran

        threading.Thread(target=work, name='waystone-update-check', daemon=True).start()
        return len(self._updates_by_game)

    def _on_detection_finished(self, detected):
        self._checking = False
        if self._closed:
            return
        self.setDetectedUpdates(detected)

    def setDetectedUpdates(self, updates):
        conn_records = getattr(self._connection_view, '_records', None) if self._connection_view is not None else None
        product_index = build_catalog_product_index(self._catalog, conn_records)
        catalog_ids = {str(g.get('id')) for g in self._catalog.get('games', ()) if isinstance(g, dict) and g.get('id')}

        by_game = {}
        by_product = {}

        for raw in updates or ():
            if isinstance(raw, GameUpdateInfo):
                info = raw
                explicit_game_id = ''
            elif isinstance(raw, dict):
                explicit_game_id = str(raw.get('game_id') or raw.get('gameId') or '').strip()
                prov = str(raw.get('provider') or 'epic').strip().lower()
                info = GameUpdateInfo(
                    provider=prov,
                    product_id=str(raw.get('product_id') or raw.get('productId') or explicit_game_id).strip(),
                    title=str(raw.get('title') or raw.get('product_id') or 'Game').strip(),
                    scope=str(raw.get('scope') or ('steam' if prov == 'steam' else 'waystone')).strip(),
                    installed_version=str(raw.get('installed_version') or raw.get('installedVersion') or '').strip(),
                    available_version=str(raw.get('available_version') or raw.get('availableVersion') or '').strip(),
                    install_path=str(raw.get('install_path') or raw.get('installPath') or '').strip(),
                    config_root=str(raw.get('config_root') or raw.get('configRoot') or '').strip(),
                    binary_path=str(raw.get('binary_path') or raw.get('binaryPath') or '').strip(),
                    platform=str(raw.get('platform') or '').strip(),
                    status_label=str(
                        raw.get('status_label')
                        or raw.get('statusLabel')
                        or ('Update pending in Steam' if prov == 'steam' else 'Update available')
                    ),
                    can_update_in_waystone=bool(
                        raw.get('can_update_in_waystone', raw.get('canUpdateInWaystone', prov != 'steam'))
                    ),
                    blocks_launch=bool(raw.get('blocks_launch', raw.get('blocksLaunch', prov == 'epic'))),
                )
            else:
                continue

            if not info.product_id:
                continue
            by_product[(info.provider, info.product_id)] = info
            matched_ids = set(product_index.get((info.provider, info.product_id), set()))
            if explicit_game_id:
                matched_ids.add(explicit_game_id)
            if info.product_id in catalog_ids:
                matched_ids.add(info.product_id)

            for gid in matched_ids:
                rep = self._canonical_id(gid)
                for target_gid in {gid, rep}:
                    if not target_gid:
                        continue
                    # Prefer Waystone/Heroic actionable updates over Steam if a game is linked to multiple stores
                    existing = by_game.get(target_gid)
                    if existing is None or (not existing.can_update_in_waystone and info.can_update_in_waystone):
                        by_game[target_gid] = info

        self._updates_by_game = by_game
        self._updates_by_product = by_product
        self._sync_library_model_updates()
        self._update_revision += 1
        self.updatesChanged.emit()
        self.changed.emit()

    def _sync_library_model_updates(self):
        if self._library_model is not None and hasattr(self._library_model, 'setGameUpdates'):
            payload = {gid: info.to_dict() for gid, info in self._updates_by_game.items()}
            self._library_model.setGameUpdates(payload)

    def _find_update_info(self, game_id):
        for candidate in self._candidate_game_ids(game_id):
            if candidate in self._updates_by_game:
                return self._updates_by_game[candidate]
        return None

    @Slot(str, result=bool)
    def hasUpdate(self, game_id):
        return self._find_update_info(game_id) is not None

    @Slot(str, result=str)
    def updateLabel(self, game_id):
        info = self._find_update_info(game_id)
        return info.status_label if info is not None else ''

    @Slot(str, result='QVariantMap')
    def updateInfoFor(self, game_id):
        info = self._find_update_info(game_id)
        if info is None:
            return {}
        candidates = set(self._candidate_game_ids(game_id))
        is_updating = any(
            (r['game_id'] in candidates or (r['provider'], r['product_id']) == (info.provider, info.product_id))
            and r.get('operation') == 'update'
            and r['phase'] not in TERMINAL
            for r in self._jobs
        )
        data = info.to_dict()
        data['gameId'] = self._canonical_id(game_id) or game_id
        data['isUpdating'] = is_updating
        if info.provider == 'steam':
            data['buttonLabel'] = 'Open Steam downloads'
        elif is_updating:
            data['buttonLabel'] = 'Updating…'
        else:
            data['buttonLabel'] = 'Update'
        return data

    @Slot(str, result=bool)
    def requiresUpdatePrompt(self, game_id):
        info = self._find_update_info(game_id)
        if info is None or not info.blocks_launch:
            return False
        return True

    @Slot(str, result=bool)
    def isGameRunning(self, game_id):
        if self._launch_view is None:
            return False
        active = getattr(self._launch_view, 'activeGame', None)
        if not isinstance(active, dict) or active.get('state') not in ('running', 'launching'):
            return False
        candidates = set(self._candidate_game_ids(game_id))
        info = self._find_update_info(game_id)
        if info is not None:
            candidates.add(info.product_id)
        for row in self.choices:
            if any(cid in row.get('game_ids', ()) for cid in candidates):
                candidates.add(row['product_id'])
        active_gid = str(active.get('gameId') or '').strip()
        active_tid = str(active.get('targetId') or '').strip()
        return bool((active_gid and active_gid in candidates) or (active_tid and active_tid in candidates))

    @Slot(result=bool)
    def openSteamDownloads(self):
        dispatch = getattr(self.manager, '_steam_dispatch', None)
        if dispatch is None:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            dispatch = lambda uri: QDesktopServices.openUrl(QUrl(uri))
        try:
            return bool(dispatch('steam://open/downloads'))
        except Exception:
            return False

    @Slot(str, result=bool)
    def updateGame(self, game_id):
        if self._closed or self._prompt.get('kind') == 'package':
            return False
        info = self._find_update_info(game_id)
        if info is None:
            self._ask(
                game_id,
                'Update game',
                'No pending update is available for this game.',
                kind='error',
            )
            return False
        if info.provider == 'steam':
            return self.openSteamDownloads()

        # Safety Rule 1: Never update a game while it runs.
        if self.isGameRunning(game_id):
            self._ask(
                game_id,
                info.title,
                'Cannot update while the game is running. Close the game before updating.',
                kind='error',
            )
            return False

        # Safety Rule 2: Never update a Heroic-installed Epic game while Heroic is running.
        if info.scope == 'heroic':
            detector = self._ensure_detector()
            if detector.is_heroic_busy():
                self._ask(
                    game_id,
                    info.title,
                    'Cannot update a Heroic-installed Epic game while Heroic is running. Close Heroic first.',
                    kind='error',
                )
                return False

        candidates = set(self._candidate_game_ids(game_id))
        if any(
            (r['game_id'] in candidates or (r['provider'], r['product_id']) == (info.provider, info.product_id))
            and r['phase'] not in TERMINAL
            for r in self._jobs
        ):
            return False

        root = self.manager.defaultRoot
        game_folder = info.product_id
        if info.install_path:
            p = Path(info.install_path)
            if p.is_absolute() and p.name:
                root = str(p.parent)
                game_folder = p.name

        if not self.manager._storage_probe(root).get('ready'):
            self._ask(
                game_id,
                info.title,
                'The game installation folder is not writable or has no free space.',
                kind='error',
            )
            return False

        tool_path = info.binary_path or self._tool({'provider': info.provider})
        row = {
            'provider': info.provider,
            'product_id': info.product_id,
            'title': info.title,
            'scope': info.scope,
            'config_root': info.config_root,
            'binary_path': tool_path,
            'game_folder': game_folder,
            'platform': info.platform,
        }
        self._prompt = {}
        return self._enqueue(self._canonical_id(game_id) or game_id, row, root, operation='update')

    def _ask(self, game_id, title, message, choices=(), kind='target', root=''):
        self._prompt = {
            'game_id': game_id,
            'title': title,
            'message': message,
            'kind': kind,
            'choices': [dict(r, label=PROVIDERS[r['provider']][0] + ' · ' + r['product_id']) for r in choices],
            'root': root,
            'open': True,
        }
        self.changed.emit()

    def _tool(self, row):
        if row.get('binary_path'):
            return str(row['binary_path'])
        managed = getattr(self.manager, 'managedBinary', lambda p: '')(row['provider'])
        return self.manager.xboxBinary if row['provider'] == 'xbox' else managed or str(self.tools.get(row['provider'], ''))

    @Slot(str, result=bool)
    def install(self, game_id):
        if self._closed or self._prompt.get('kind') == 'package':
            return False
        if any(r['game_id'] == game_id and r['phase'] not in TERMINAL for r in self._jobs):
            return False
        rows = [r for r in self.choices if game_id in r['game_ids']]
        if not rows:
            self._ask(
                game_id,
                'Install game',
                'This game has no supported, exact store product linked yet. No installation was started.',
                kind='error',
            )
            return False
        root = self.manager.defaultRoot
        if len(rows) > 1:
            self._ask(game_id, rows[0]['title'], 'Choose which store copy to install.', rows, root=root)
            return False
        row = rows[0]
        if row['provider'] != 'steam' and not self.manager._storage_probe(root).get('ready'):
            self._ask(game_id, row['title'], 'Choose a writable installation drive.', rows, root=root)
            return False
        self._prompt = {}
        return self._enqueue(game_id, row, root, operation='install')

    @Slot(str, result=bool)
    def options(self, game_id):
        if self._closed or self._prompt.get('kind') == 'package':
            return False
        if any(r['game_id'] == game_id and r['phase'] not in TERMINAL for r in self._jobs):
            return False
        rows = [r for r in self.choices if game_id in r['game_ids']]
        if not rows:
            return self.install(game_id)
        self._ask(game_id, rows[0]['title'], 'Choose your store copy and installation drive.', rows, root=self.manager.defaultRoot)
        return True

    @Slot(str, str, result=bool)
    def confirm(self, key, root):
        if self._closed or self._prompt.get('kind') != 'target':
            return False
        row = next((r for r in self._prompt.get('choices', []) if r['key'] == key), None)
        if row is None:
            return False
        if row['provider'] != 'steam' and not self.manager._storage_probe(root).get('ready'):
            self._prompt = dict(
                self._prompt,
                message='That drive is unavailable, full or not writable. Choose another folder.',
                root=root,
            )
            self.changed.emit()
            return False
        game_id = self._prompt['game_id']
        self._prompt = {}
        return self._enqueue(game_id, row, root, operation='install')

    def _enqueue(self, game_id, row, root, *, operation='install'):
        if any(
            r['provider'] == row['provider']
            and r['product_id'] == row['product_id']
            and r['phase'] not in TERMINAL
            for r in self._jobs
        ):
            self.changed.emit()
            return False
        if row['provider'] != 'steam':
            tool = Path(self._tool(row))
            if not tool.is_absolute() or not tool.is_file() or not os.access(tool, os.X_OK):
                verb_noun = 'update' if operation == 'update' else 'installation'
                self._ask(
                    game_id,
                    row['title'],
                    'The configured ' + PROVIDERS[row['provider']][0] + ' ' + verb_noun + ' backend is unavailable. Nothing was started.',
                    kind='error',
                )
                return False
        self._serial += 1
        wait_msg = 'Waiting for the current update.' if operation == 'update' else 'Waiting for the current installation.'
        record = {
            'id': str(self._serial),
            'game_id': game_id,
            'title': row['title'],
            'provider': row['provider'],
            'provider_label': PROVIDERS[row['provider']][0],
            'product_id': row['product_id'],
            'operation': operation,
            'scope': row.get('scope', 'waystone'),
            'config_root': row.get('config_root', ''),
            'game_folder': row.get('game_folder', row['product_id']),
            'root': root,
            'phase': 'queued',
            'percent': None,
            'message': wait_msg,
        }
        self._jobs.append(record)
        self._queue.append((record, dict(row)))
        self._update_revision += 1
        self.updatesChanged.emit()
        self.changed.emit()
        self._advance()
        return True

    def _advance(self):
        if self._closed or self._current or self.manager.busy or self.manager.loadingLibrary or not self._queue:
            return
        record, row = self._queue.pop(0)
        self._current = record
        operation = record.get('operation', 'install')

        if operation == 'update':
            if self.isGameRunning(record['game_id']):
                record.update(
                    phase='failed',
                    percent=0,
                    message='Cannot update while the game is running. Close the game before updating.',
                )
                self._finish()
                return
            if record.get('scope') == 'heroic' and self._detector is not None and self._detector.is_heroic_busy():
                record.update(
                    phase='failed',
                    percent=0,
                    message='Cannot update a Heroic-installed Epic game while Heroic is running. Close Heroic first.',
                )
                self._finish()
                return

        self._auto = True
        self._driving = True
        try:
            if operation == 'update' and hasattr(self.manager, 'prepare_with_options'):
                ok = self.manager.prepare_with_options(
                    row['provider'],
                    row['product_id'],
                    row['title'],
                    record['root'],
                    'update',
                    self._tool(row),
                    '',
                    config_path_override=row.get('config_root', ''),
                    game_folder_override=row.get('game_folder', ''),
                    platform_override=row.get('platform', ''),
                )
            else:
                ok = self.manager.prepare(
                    row['provider'],
                    row['product_id'],
                    row['title'],
                    record['root'],
                    operation,
                    self._tool(row),
                    '',
                )
        finally:
            self._driving = False
        if not ok:
            fallback_msg = 'Update could not be prepared.' if operation == 'update' else 'Installation could not be prepared.'
            record.update(phase='failed', percent=0, message=self.manager.state.get('message', fallback_msg))
            self._finish()
            return
        self._sync()

    @Slot()
    def _sync(self):
        if self._closed or self._driving:
            return
        if self._current is None:
            if self._queue and not self.manager.busy and not self.manager.loadingLibrary:
                QTimer.singleShot(0, self._advance)
            return
        state = self.manager.state
        phase = state.get('phase', 'idle')
        self._current.update(
            phase=phase,
            percent=state.get('percent'),
            message=state.get('message') or state.get('error') or phase,
        )
        if self.manager.preview.get('destination'):
            self._current['destination'] = self.manager.preview['destination']
        if phase == 'prepared' and self._auto:
            self._auto = False
            self._driving = True
            try:
                ok = self.manager.start()
            finally:
                self._driving = False
            if not ok:
                fallback_msg = (
                    'Update could not start.'
                    if self._current.get('operation') == 'update'
                    else 'Installation could not start.'
                )
                self._current.update(phase='failed', percent=0, message=self.manager.state.get('message', fallback_msg))
                self._finish()
                return
            self._sync()
            return
        if phase == 'choose-package':
            self._auto = False
            r = self._current
            self._prompt = {
                'game_id': r['game_id'],
                'title': r['title'],
                'kind': 'package',
                'open': True,
                'message': 'Choose the PC package for this game.',
                'root': r['root'],
                'packages': self.manager.packageChoices,
            }
        if phase in TERMINAL and not self.manager.busy:
            self._finish()
            return
        self.changed.emit()

    def _finish(self):
        finished_record = self._current
        if (
            self._prompt.get('kind') == 'package'
            and finished_record
            and self._prompt.get('game_id') == finished_record['game_id']
        ):
            self._prompt = {}
        self._current = None
        self._auto = False

        if (
            finished_record
            and finished_record.get('operation') == 'update'
            and finished_record.get('phase') == 'completed'
        ):
            # Clear pending update state for the updated product/game
            prov = finished_record.get('provider')
            pid = finished_record.get('product_id')
            self._updates_by_product.pop((prov, pid), None)
            to_remove = [
                gid
                for gid, info in self._updates_by_game.items()
                if (info.provider == prov and info.product_id == pid) or gid == finished_record.get('game_id')
            ]
            for gid in to_remove:
                self._updates_by_game.pop(gid, None)
            self._sync_library_model_updates()

        # Keep pending/active jobs; bound only finished session history.
        terminal = [r for r in self._jobs if r['phase'] in TERMINAL]
        if len(terminal) > 100:
            remove = {r['id'] for r in terminal[:-100]}
            self._jobs = [r for r in self._jobs if r['id'] not in remove]
        self._update_revision += 1
        self.updatesChanged.emit()
        self.changed.emit()
        QTimer.singleShot(0, self._advance)

    @Slot(str, result=bool)
    def confirmPackage(self, content_id):
        if not self._current or self._prompt.get('kind') != 'package':
            return False
        if content_id not in {p['content_id'] for p in self._prompt.get('packages', [])}:
            return False
        self._prompt = {}
        self._driving = True
        try:
            ok = self.manager.choosePackage(content_id)
        finally:
            self._driving = False
        self._auto = True
        self._sync()
        return ok

    @Slot()
    def dismissPrompt(self):
        package = self._prompt.get('kind') == 'package'
        self._prompt = {}
        if package and self._current:
            self._current.update(
                phase='cancelled',
                percent=0,
                message='Package selection cancelled. Nothing was downloaded.',
            )
            self._driving = True
            try:
                self.manager.clearPreview()
            except Exception:
                pass
            finally:
                self._driving = False
            self._finish()
        else:
            self.changed.emit()

    @Slot(str)
    def cancel(self, job_id):
        if self._current and self._current['id'] == job_id:
            if self._prompt.get('kind') == 'package':
                self.dismissPrompt()
            elif self.manager.busy:
                self.manager.cancel()
            return
        for record, row in list(self._queue):
            if record['id'] == job_id:
                self._queue.remove((record, row))
                cancel_msg = (
                    'Queued update cancelled.'
                    if record.get('operation') == 'update'
                    else 'Queued installation cancelled.'
                )
                record.update(phase='cancelled', message=cancel_msg, percent=0)
                self._update_revision += 1
                self.updatesChanged.emit()
                self.changed.emit()
                return

    @Slot()
    def shutdown(self):
        self._closed = True
        self._update_timer.stop()
        self._prompt = {}
        for record, row in self._queue:
            record.update(phase='cancelled', message='Application closed before this download started.')
        self._queue = []
