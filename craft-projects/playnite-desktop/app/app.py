"""Waystone Qt library with native storage, editable overlays and explicit Play."""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE_ROOT = HERE.parents[2]
sys.path[:] = [str(SERVICE_ROOT)] + [p for p in sys.path if p != str(SERVICE_ROOT)]
from native_services import waystone_paths as _wpaths  # noqa: E402 (needs SERVICE_ROOT on sys.path)
from native_services import provider_engines as _provider_engines  # noqa: E402
from native_services.platform import is_windows as _is_windows  # noqa: E402

try:
    from private_addons import (
        CLI_EMULATION_DB_DEST as _CLI_EMULATION_DB_DEST,
        CLI_EMULATION_DB_FLAG as _CLI_EMULATION_DB_FLAG,
        CLI_STORE_CATALOG_DEST as _CLI_STORE_CATALOG_DEST,
        CLI_STORE_CATALOG_FLAG as _CLI_STORE_CATALOG_FLAG,
        EXCLUDED_CONSOLE_CHIP_SOURCES as _EXCLUDED_CONSOLE_CHIP_SOURCES,
        SECONDARY_LIBRARY_NAME as _SECONDARY_LIBRARY_NAME,
    )
except ImportError:
    _CLI_EMULATION_DB_DEST = None
    _CLI_EMULATION_DB_FLAG = None
    _CLI_STORE_CATALOG_DEST = None
    _CLI_STORE_CATALOG_FLAG = None
    _EXCLUDED_CONSOLE_CHIP_SOURCES = set()
    _SECONDARY_LIBRARY_NAME = ''


def create_session(application, snapshots=None, organizer=False, view='grid', headless=False, catalog=None, media_root=None, store_catalog=None, emulation_db=None, organizer_state=None, launcher_sync=False, connections=None, link_state=None, native_collector_root=None, native_refresh_root=None, library_db=None, plugin_metadata_db=None, reconciliation=None, launch_profile_root=None, artwork_override_root=None, rom_config=None, spawn_fn=None, save_config=None, save_runner=None, xodus_helper=None, social_transport=None, voice_available=False, provider_manager=None, provider_jobs_enabled=True, controller_client=None, studio_host=None, native_controller_ui=False, game_progress_repository=None, organizer_workspace=None, plugin_time_schema=None, organizer_agent_gateway=None, platform_social_view=None, session_store=None, mod_config=None, mod_service=None, receipt_registry=None, epic_account_service=None, nile_account_service=None, gog_account_service=None, controller_profiles_path=None, local_games=None, update_detector=None, first_run_screenshot=False, **addon_kwargs):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow  # Register the window wrapper before engine.load
    from library_model import LibraryModel, load_fixture, source_display_name
    from catalog_link_view import CatalogLinkView
    from organizer_report import OrganizerReport
    from artwork_view import ArtworkView
    from launch_view import LaunchView

    if sum(bool(x) for x in (catalog, snapshots, library_db)) > 1:
        raise ValueError('Library DB, catalog and snapshots are mutually exclusive')
    selected_library = catalog is not None or library_db is not None
    if media_root is not None and not selected_library:
        raise ValueError('An explicit media root requires --catalog or --library-db')
    media = None
    if library_db is not None:
        if launcher_sync:
            from native_services.library_repository import LibraryRepository
            if not Path(library_db).exists():
                LibraryRepository.create_empty(library_db)
        from native_services.native_catalog import load_native_catalog
        from media_catalog import MediaCatalog
        data, catalog_digest = load_native_catalog(library_db, with_digest=True)
        if local_games is not None:
            # App-owned local entries (modlist instances, non-store games) appended as their own works.
            from native_services import local_games as local_games_store
            local_games_store.merge_into_catalog(data, local_games_store.load(local_games))
        games = data['games']
        label = 'Waystone Library' if launcher_sync else 'Native SQLite library'
        notice = ('Waystone-owned SQLite library. Local launcher sync is on-demand/background, never contacts stores, '
                  'and preserves user edits and disappeared records.' if launcher_sync else
                  'Independent Linux SQLite library with immutable imported provenance. Organizer overrides use a separate app-owned database. No Windows, Wine, source scanning or imported action execution. Media uses only the explicit managed root. Installation claims are historical, not native verification.')
        media = MediaCatalog(media_root)
    elif catalog is not None:
        from media_catalog import load_catalog, MediaCatalog
        data, catalog_digest = load_catalog(catalog, with_digest=True)
        games = data['games']
        label = 'Selected-copy display catalog (unverified generation)'
        notice = label + '\n\nTop-level provenance claims (unverified):\n' + json.dumps(data.get('provenance', {}), ensure_ascii=False, sort_keys=True)
        notice += '\n\nImported catalog preserved. Editable metadata uses the separate native Organizer store when enabled. Media roles are historical claims, not same-generation verification or installation proof. No scanning, network fetch or launching from this import. Media comes only from the explicit --media-root, never JSON roots.'
        media = MediaCatalog(media_root)
    elif snapshots:
        from snapshot_bridge import load_exports
        games, contexts = load_exports(snapshots, with_context=True)
        label = 'Synthetic export' if all(c['fixture_mode'] for c in contexts) else 'Explicit snapshots'
        lines = [f"{c['provider']} {c['instance']} @ {c['host']}: availability={c['availability']}, complete={c['complete']}, observed={c['observed_at']}" for c in contexts]
        notice = label + f' • {len(contexts)} selected source snapshot(s)\n\n' + '\n\n'.join(lines)
        notice += '\n\nNo source scanning or game launching. Incomplete/offline results are observations, not uninstall deltas. Declared installed is not validated installed.'
    else:
        games = load_fixture(HERE / 'fixture-library.json')
        label = 'Synthetic fixtures'
        notice = 'Synthetic UI fixtures, not a live host scan.\n\nNo live source was opened. Select app-owned snapshots explicitly with --snapshot (repeatable).\n\nInstalled values in this fixture are synthetic, not validation of an installation on this computer. Artwork and activity metadata are not imported.\n\nAll recipes are display-only text. No launching, installing, metadata writes, AI or network calls are implemented.'
    from native_provider_view import NativeProviderView
    native = NativeProviderView(store_catalog, data if selected_library else None, emulation_db=emulation_db, **addon_kwargs)
    from connection_view import ConnectionView
    if connections is not None and not selected_library:
        raise ValueError('Native connections require an explicit generation-matched display catalog')
    connection_view = ConnectionView(connections, data if selected_library else None,
                                     catalog_digest if selected_library else None)
    if receipt_registry is None and not headless and launch_profile_root is not None:
        from native_services.install_receipts import InstallReceiptRegistry
        receipt_registry=InstallReceiptRegistry(Path(launch_profile_root).parent/'install-receipts-v1.json')
    if receipt_registry is not None:
        connection_view.configureReceipts(receipt_registry, {'games': games})
    from native_library_view import NativeLibraryView
    local_library = NativeLibraryView(connection_view)
    artwork_view = ArtworkView(catalog=data if selected_library else {'games': games},
                               override_root=artwork_override_root,
                               enable_override_state=bool(artwork_override_root))
    # Headless sessions (tests, screenshots) get a throwaway settings file: they must never change the user's
    # real Waystone settings or query the compositor (found 2026-11-24: tests had rewritten a developer machine's sound pack).
    headless_settings = None
    if headless:
        import tempfile
        headless_settings = Path(tempfile.mkdtemp(prefix='waystone-headless-')) / 'settings.json'
    monitor_kwargs = {}
    if headless:
        from native_services.game_monitor import GameMonitorSettings, HyprlandMonitors
        monitor_kwargs = {'monitor_settings': GameMonitorSettings(headless_settings),
                          'monitor_backend': HyprlandMonitors(environ={}, which=lambda _name: None)}
    launch_view = LaunchView(launch_profile_root=launch_profile_root,
                            rom_config_dir=rom_config, spawn_fn=spawn_fn, **monitor_kwargs)
    model = LibraryModel(games, media=media, catalog_mode=selected_library, connections=connection_view, artwork_view=artwork_view)
    from native_services.ui_sounds import audio_allowed as _live_session
    if not headless and _live_session() and os.environ.get('WAYSTONE_RIBBONS', '1') != '0':
        # W18 ribbon art: on-demand, cached; never in headless or offscreen (test/screenshot) sessions,
        # which must not fetch from the network or write the user's artwork cache.
        from native_services.ribbon_artwork import RibbonCache, RibbonFetcher, fetch_ribbon
        _ribbon_cache = RibbonCache()
        def _fetch_ribbon(provider, product_id):
            sgdb_key = ''
            if provider == 'steamgriddb':
                try:
                    from native_services.provider_sessions import ProviderSessionStore
                    store = session_store or ProviderSessionStore()
                    record = store.load('steamgriddb') if store is not None else None
                    sgdb_key = str(record.get('api_key') or '') if record else ''
                except Exception:
                    # An absent/locked keyring means skip this optional source silently.
                    return None
                if not sgdb_key:
                    # No configured key is not a failed lookup: do not create a week-long miss.
                    return None
            return fetch_ribbon(provider, product_id, sgdb_key=sgdb_key)
        _ribbon_fetcher = RibbonFetcher(_ribbon_cache, on_ready=model.ribbonReadyFromWorker,
                                       fetch=_fetch_ribbon)
        model.configureRibbons(_ribbon_cache, _ribbon_fetcher)
        model.destroyed.connect(lambda *_: _ribbon_fetcher.stop())
    if reconciliation is not None:
        model.configure_reconciliation(reconciliation)
    if organizer_state is not None:
        if not selected_library:
            raise ValueError('Organizer state requires an explicit display catalog')
        model.configure_organizer(organizer_state)
    link_view = CatalogLinkView({'games': games}, link_state=link_state,
                                enable_link_state=bool(link_state and Path(link_state).exists()))
    model.setIdentityLinks(link_view.resolution)
    link_view.changed.connect(lambda: model.setIdentityLinks(link_view.resolution))
    launcher_library = None
    if launcher_sync:
        from launcher_library_view import LauncherLibraryView
        launcher_library = LauncherLibraryView(library_db, model, link_view,
                                               organizer_state=organizer_state,
                                               scan_options={'rom_config_dir': rom_config,
                                                             'local_games_path': local_games},
                                               parent=None)
        application.aboutToQuit.connect(launcher_library.close)
    engine = QQmlApplicationEngine()
    if launcher_library is not None:
        launcher_library.setParent(engine)
    from native_services.assistant_animation_settings import AssistantAnimationSettings
    assistant_preferences = AssistantAnimationSettings(headless_settings, parent=engine)
    engine.assistant_preferences = assistant_preferences
    engine.addImageProvider('artwork-override', artwork_view.provider())
    refresh_view = None
    if native_collector_root is not None or native_refresh_root is not None:
        if not selected_library or native_collector_root is None or native_refresh_root is None:
            raise ValueError('Native refresh needs an explicit catalog, collector and output root')
        from native_refresh_view import NativeRefreshView
        refresh_view = NativeRefreshView(catalog, native_refresh_root, native_collector_root, library_db=library_db)
        def reload_native(path):
            try:
                fresh = ConnectionView(Path(path), data, catalog_digest)
            except (ValueError, OSError):
                if engine.rootObjects():
                    engine.rootObjects()[0].setProperty('nativeReloadError', 'New snapshot failed native identity validation. Previous library kept.')
                return
            connection_view.replaceFrom(fresh)
            local_library.refreshFrom(connection_view)
            model.setNativeFilter(model._native_filter)
            if engine.rootObjects():
                engine.rootObjects()[0].setProperty('nativeReloadError', '')
        refresh_view.refreshed.connect(reload_native)
        application.aboutToQuit.connect(refresh_view.close)
    if media is not None:
        engine.addImageProvider('catalog-media', media)
    warnings = []
    def record(items):
        for item in items:
            warnings.append(item.toString())
            print(item.toString(), file=sys.stderr)
    engine.warnings.connect(record)
    context = engine.rootContext()
    from plugin_features_view import PluginFeaturesView
    plugin_features = PluginFeaturesView(plugin_metadata_db, parent=engine)
    from organizer_workspace_view import OrganizerWorkspaceView
    if organizer_workspace is None and model._organizer is not None:
        from native_services.organizer_control import OrganizerControl
        from native_services.organizer_agent_workspace import OrganizerAgentWorkspace
        organizer_workspace = OrganizerAgentWorkspace(OrganizerControl(model._organizer, catalog_link_store=link_view._store, library_db_path=library_db, reconciliation_path=reconciliation))
    organizer_workspace_view = OrganizerWorkspaceView(organizer_workspace, parent=engine)
    application.aboutToQuit.connect(organizer_workspace_view.shutdown)
    engine.organizer_workspace = organizer_workspace
    engine.organizer_workspace_view = organizer_workspace_view
    from organizer_agent_view import OrganizerAgentView
    organizer_agent_view = OrganizerAgentView(organizer_workspace, gateway=organizer_agent_gateway, parent=engine)
    engine.organizer_agent_view = organizer_agent_view
    application.aboutToQuit.connect(organizer_agent_view.shutdown)
    organizer_agent_view.proposalReady.connect(organizer_workspace_view.refreshAgentActivity)
    from game_progress_view import GameProgressView
    if game_progress_repository is None and plugin_metadata_db is not None:
        from native_services.game_progress_repository import GameProgressRepository, HLTB_CLASSIC_SECONDS_SCHEMA
        game_progress_repository = GameProgressRepository(plugin_metadata_db, hltb_schema=plugin_time_schema)
    game_progress = GameProgressView(game_progress_repository, parent=engine)
    application.aboutToQuit.connect(game_progress.shutdown)
    from emulation_view import EmulationView
    emulation_manager = EmulationView(launch_profile_root, parent=engine)
    from mod_management_view import ModManagementView
    mod_config_path = (Path(mod_config) if mod_config is not None else
                       Path(launch_profile_root).parent/'mods-v1'/'config.json'
                       if launch_profile_root is not None else None)
    mod_management = ModManagementView(
        config_path=mod_config_path,
        launch_profile_store=getattr(launch_view, '_profile_store', None),
        service=mod_service, parent=engine)
    application.aboutToQuit.connect(mod_management.shutdown)
    save_management = None
    if save_config is not None:
        from save_management_view import SaveManagementView
        save_management = SaveManagementView(save_config, runner=save_runner, parent=engine)
        application.aboutToQuit.connect(save_management.shutdown)
    if session_store is None and platform_social_view is None and not headless:
        try:
            from native_services.provider_sessions import ProviderSessionStore
            session_store = ProviderSessionStore()
        except Exception:
            session_store = None
    elif session_store is not None and platform_social_view is None and headless:
        # Explicitly injected store is allowed in headless fixtures; restore hooks run below.
        pass
    elif session_store is not None and platform_social_view is not None:
        # caller owns both; explicit injection takes precedence
        pass
    from platform_social_view import PlatformSocialView
    platform_social = platform_social_view or PlatformSocialView(games, cache_path=Path(organizer_state).parent/'platform-progress-v1.sqlite3' if organizer_state else None, xodus_helper=xodus_helper, session_store=session_store, parent=engine)
    engine.platform_social = platform_social
    from steam_qr_image import SteamQrImage
    engine.addImageProvider('steamqr', SteamQrImage(platform_social))
    application.aboutToQuit.connect(platform_social.shutdown)
    from xodus_social_view import XodusSocialView
    social_view = XodusSocialView(binary_path=str(xodus_helper) if xodus_helper else None,
                                 transport=social_transport, session_store=session_store, parent=engine)
    if session_store is not None and platform_social_view is None:
        try:
            social_view.restoreSession()
        except Exception:
            pass
        try:
            platform_social.restoreSession()
        except Exception:
            pass
    from voice_media_view import VoiceMediaView
    voice_media = VoiceMediaView(social_view, available=voice_available, parent=engine)
    from voice_audio_routes_view import VoiceAudioRoutesView
    audio_routes = VoiceAudioRoutesView(voice_media, parent=engine)
    application.aboutToQuit.connect(audio_routes.shutdown)
    application.aboutToQuit.connect(voice_media.shutdown)
    application.aboutToQuit.connect(social_view.shutdown)
    download_jobs = None
    if provider_jobs_enabled:
        from provider_job_view import ProviderJobView
        from native_services.installation_library import InstallPreferences
        download_jobs = ProviderJobView(manager=provider_manager, parent=engine, receipt_registry=receipt_registry,
            catalog={'games': games}, preferences=InstallPreferences(
                Path.home()/'.local/share/waystone/install-settings.json' if not headless else None))
        if xodus_helper is not None:download_jobs.configureXodus(xodus_helper)
        application.aboutToQuit.connect(download_jobs.shutdown)
        if refresh_view is not None:
            download_jobs.finished.connect(lambda success: refresh_view.refresh() if success and not refresh_view.busy else None)
            download_jobs.refreshInstalledRequested.connect(lambda: refresh_view.refresh() if not refresh_view.busy else None)
    from store_accounts_view import StoreAccountsView
    xodus_auth_service=None
    if xodus_helper is not None and not headless:
        from xodus_auth_service import XodusAuthService
        xodus_auth_service=XodusAuthService(xodus_helper,parent=engine)
    if epic_account_service is None and not headless:
        from native_services.epic_account import EpicAccountService
        epic_account_service=EpicAccountService(
            _provider_engines.resolve('legendary'),
            _wpaths.store('providers-v1', 'epic', 'legendary'))
    if download_jobs is not None and epic_account_service is not None:
        download_jobs.configureManagedAccount('epic',str(epic_account_service.binary_path),str(epic_account_service.config_root))
    if nile_account_service is None and not headless:
        from native_services.nile_account import NileAccountService
        nile_account_service=NileAccountService(
            _provider_engines.resolve('nile'),
            _wpaths.store('providers-v1', 'amazon'))
    if download_jobs is not None and nile_account_service is not None:
        download_jobs.configureManagedAccount('amazon',str(nile_account_service.binary_path),
                                               str(nile_account_service.config_root))
    if gog_account_service is None and not headless:
        from native_services.gog_account import GOGAccountService
        gog_account_service=GOGAccountService(
            _provider_engines.resolve('gogdl'),
            _wpaths.store('providers-v1', 'gog'))
    if download_jobs is not None and gog_account_service is not None:
        download_jobs.configureManagedAccount('gog',str(gog_account_service.binary_path),str(gog_account_service.config_root))
    store_accounts=StoreAccountsView(epic_account_service,download_jobs,nile_account_service,gog_account_service,xodus_auth_service,parent=engine)
    if nile_account_service is not None:store_accounts.readAmazonSaved()
    if gog_account_service is not None:store_accounts.readGogSaved()
    application.aboutToQuit.connect(store_accounts.shutdown)
    from price_comparison_view import PriceComparisonView
    price_comparison=PriceComparisonView(session_store,parent=engine)
    from ribbon_settings import RibbonArtworkSettings
    ribbon_settings = RibbonArtworkSettings(session_store, parent=engine)
    from steam_library_settings import SteamLibrarySettings
    steam_library_settings = SteamLibrarySettings(session_store, parent=engine)
    first_run_guide = None
    if launcher_sync:
        from first_run_guide import FirstRunGuide
        first_run_guide = FirstRunGuide(parent=engine)
    ownership_revision = {"value": None}
    def _sync_store_ownership():
        # Organizer also signals draft/selection changes. Ownership depends only
        # on committed metadata and exact identity grouping, not editor state.
        revision = (model._organizer_revision, tuple(sorted(model._identity_resolution.items())))
        if revision == ownership_revision["value"]:
            return
        bought_set={'Steam','GOG','Epic','Xbox','Amazon','EA app','Battle.net','Ubisoft Connect'}
        b_titles=[]
        a_titles=[]
        for g in model.games:
            if not isinstance(g,dict):continue
            t=model._display_title(g) or g.get('title','')
            raw_t=g.get('title','')
            eff_srcs={source_display_name(v.get('source')) for v in model._effective_variants(g)}
            if eff_srcs & bought_set:
                if t:b_titles.append(t)
                if raw_t and raw_t!=t:b_titles.append(raw_t)
            elif _SECONDARY_LIBRARY_NAME and _SECONDARY_LIBRARY_NAME in eff_srcs:
                if t:a_titles.append(t)
                if raw_t and raw_t!=t:a_titles.append(raw_t)
        for item in getattr(native,'_items',()):
            if getattr(item,'name',None):a_titles.append(item.name)
        price_comparison.setLibraryOwnership(b_titles,a_titles)
        ownership_revision["value"] = revision
    _sync_store_ownership()
    model.organizerChanged.connect(_sync_store_ownership)
    link_view.changed.connect(_sync_store_ownership)
    application.aboutToQuit.connect(price_comparison.shutdown)
    if epic_account_service is not None:
        # New Epic operations always use Waystone's own scope, not Heroic's.
        store_accounts.readSaved()
        if download_jobs is not None:
            launch_view.configureEpicDirectLaunch(
                str(epic_account_service.binary_path), str(epic_account_service.config_root))
    def refresh_receipts():
        changed = connection_view.refreshReceipts()
        # An unchanged receipt snapshot must not reset models or trigger facet/QML rebuilds.
        if not changed:
            return True
        try:
            local_library.refreshFrom(connection_view)
        except Exception:
            pass
        model.setNativeFilter(model._native_filter)
        try:
            model.organizerOverlayChanged.emit()
        except Exception:
            pass
        return True
    if receipt_registry is not None:
        if download_jobs is not None:
            download_jobs.refreshInstalledRequested.connect(refresh_receipts)
        from PySide6.QtCore import QTimer
        receipt_timer=QTimer(engine)
        receipt_timer.setInterval(3000)
        receipt_timer.timeout.connect(refresh_receipts)
        if not headless:receipt_timer.start()
        application.aboutToQuit.connect(receipt_timer.stop)
        engine.receipt_timer=receipt_timer
    engine.refresh_receipts=refresh_receipts
    from game_install_controller import GameInstallController
    game_installs = GameInstallController(
        download_jobs,
        {'games': games},
        parent=engine,
        detector=update_detector,
        launch_view=launch_view,
        library_model=model,
        connection_view=connection_view,
        auto_check_updates=bool(update_detector is not None or (not headless and _live_session())),
    ) if download_jobs is not None else None
    if game_installs is not None:application.aboutToQuit.connect(game_installs.shutdown)
    engine.game_installs = game_installs
    from controller_view import ControllerView
    controller_view = ControllerView(client=controller_client, parent=engine)
    application.aboutToQuit.connect(controller_view.shutdown)
    engine.unified_controller = controller_view
    from controller_commands import ControllerCommands, ControllerProfileCommands, ControllerDeviceCommands
    controller_commands = ControllerCommands(parent=engine)
    application.aboutToQuit.connect(controller_commands.shutdown)
    engine.controller_commands = controller_commands
    controller_profiles = ControllerProfileCommands(parent=engine)
    controller_devices = ControllerDeviceCommands(parent=engine)
    application.aboutToQuit.connect(controller_profiles.shutdown)
    application.aboutToQuit.connect(controller_devices.shutdown)
    engine.controller_profile_commands = controller_profiles
    engine.controller_device_commands = controller_devices
    def controller_profile_committed(op, ok):
        if ok and op in {'profile_create','profile_copy','profile_rename','profile_delete','profile_import','profile_set_field'}:
            controller_view.refresh()
            controller_profiles.refreshProfiles()
    controller_profiles.operationFinished.connect(controller_profile_committed)
    from controller_live_view import ControllerLiveView
    controller_live = ControllerLiveView(parent=engine)
    application.aboutToQuit.connect(controller_live.shutdown)
    engine.controller_live = controller_live
    from controller_ui_navigation import ControllerUiNavigation
    controller_navigation = ControllerUiNavigation(controller_live, parent=engine)
    engine.controller_navigation = controller_navigation
    from controller_studio_host import ControllerStudioHost
    studio_root = SERVICE_ROOT / 'components' / 'controller_studio' / 'runtime' / 'publish'
    app_data = _wpaths.data_home()
    if native_controller_ui:
        from controller_engine_host import ControllerEngineHost
        studio = studio_host or ControllerEngineHost(
            SERVICE_ROOT / 'components/controller_studio/headless/publish/UnifiedController.Headless',
            app_data / 'waystone/controller', app_data / 'unified-controller', parent=engine)
        studio.ready.connect(controller_view.refresh)
        studio.ready.connect(controller_commands.refreshCapabilities)
        studio.ready.connect(controller_profiles.refreshProfiles)
        studio.ready.connect(controller_devices.refreshDevices)
        if not headless and studio_host is None:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, studio.start)
    else:
        studio = studio_host or ControllerStudioHost(studio_root / 'UnifiedController.Linux.App',
                                  app_data / 'waystone' / 'controller',
                                  app_data / 'unified-controller', parent=engine)
    from controller_profiles_view import ControllerProfilesView
    from controller_profile_activation import ControllerProfileActivation
    from controller_profile_integration import ControllerProfileIntegration
    profile_document = controller_profiles_path if controller_profiles_path is not None else (app_data / 'waystone/controller/profiles.json' if not headless else None)
    profile_assignments = ControllerProfilesView(profile_document, parent=engine)
    profile_activation = ControllerProfileActivation(client=controller_client, parent=engine)
    profile_integration = ControllerProfileIntegration(profile_assignments, profile_activation,
        canonicalize=lambda key: model.representativeFor(key) or key, parent=engine, native_writers=(controller_view, controller_profiles))
    launch_view.controllerSessionObserved.connect(profile_integration.observe)
    controller_view.activeProfileObserved.connect(profile_integration.observeEditorActive)
    controller_view.setMutationAdmission(lambda: profile_integration.mutationAllowed(controller_view))
    controller_profiles.setMutationAdmission(lambda: profile_integration.mutationAllowed(controller_profiles))
    if hasattr(studio, 'ready'): studio.ready.connect(profile_integration.refresh)
    application.aboutToQuit.connect(profile_activation.shutdown)
    engine.controller_profile_assignments = profile_assignments
    engine.controller_profile_activation = profile_activation
    engine.controller_profile_integration = profile_integration
    from virtual_pad_view import VirtualPadView
    virtual_pad = VirtualPadView(profile_document, client=controller_client, parent=engine)
    launch_view.controllerSessionObserved.connect(virtual_pad.observe)
    # Pad type follows the game's controller profile: re-read when a profile or a game's profile changes.
    controller_view.operationFinished.connect(lambda _ok: virtual_pad.reloadSettings())
    profile_assignments.documentChanged.connect(virtual_pad.reloadSettings)
    # Offscreen/fake app sessions remain inert unless a dedicated test explicitly
    # injects the guard. Production preparation finishes before any game spawn.
    if not headless:
        # Anti-cheat list for "real controller on competitive games": refreshed in the background at most weekly.
        import threading
        from native_services.anticheat_games import refresh_cache
        threading.Thread(target=refresh_cache, name='waystone-anticheat-list', daemon=True).start()
    if not headless and spawn_fn is None:
        launch_view.setPrelaunchGuard(virtual_pad.prepareLaunch)
        launch_view.setLaunchEnvironmentProvider(virtual_pad.launchEnvironment)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, virtual_pad.start)
    if hasattr(studio, 'ready') and not headless:
        studio.ready.connect(virtual_pad.refresh)
    application.aboutToQuit.connect(virtual_pad.shutdown)
    engine.virtual_pad_view = virtual_pad
    application.aboutToQuit.connect(studio.shutdown)
    engine.controller_studio = studio
    from menu_overview_view import MenuOverviewView
    menu_overviews = MenuOverviewView(connection_view, launch_view, native, emulation_manager, parent=engine)
    connection_view.changed.connect(menu_overviews.refresh)
    launch_view.romsChanged.connect(menu_overviews.refresh)
    native.changed.connect(menu_overviews.refresh)
    emulation_manager.changed.connect(menu_overviews.refresh)
    organizer_report = OrganizerReport(games, parent=engine)
    engine.organizer_report = organizer_report
    application.aboutToQuit.connect(organizer_report.shutdown)
    from mod_browser_view import rank_mod_games
    mod_game_choices = rank_mod_games(model.games, model._display_title)
    from mod_browser_view import ModBrowserView
    mod_browser = ModBrowserView(mod_game_choices, watch_inbox=not headless, offline=headless or os.environ.get('QT_QPA_PLATFORM') == 'offscreen', parent=engine)
    application.aboutToQuit.connect(mod_browser.shutdown)
    def _show_mods_for_nexus():
        from PySide6.QtCore import QMetaObject
        if engine.rootObjects():
            QMetaObject.invokeMethod(engine.rootObjects()[0], "openMods")
    mod_browser.nexusLinkReceived.connect(_show_mods_for_nexus)
    engine.mod_browser = mod_browser
    from pointer import Pointer
    screen_pointer = Pointer(parent=engine)
    from overlay_surface import OverlaySurfaceHost
    overlay_surface = OverlaySurfaceHost(parent=engine) if not headless else None
    engine.overlay_surface_host = overlay_surface
    from overlay_tools import OverlayToolsBridge
    overlay_tools = OverlayToolsBridge(
        game_installs=game_installs,
        controller_profiles=profile_assignments,
        audio_routes=audio_routes,
        save_management=save_management,
        launch_view=launch_view,
        parent=engine,
    )
    engine.overlay_tools = overlay_tools
    from overlay_library import OverlayLibraryBridge
    overlay_library = OverlayLibraryBridge(
        library_model=model,
        launch_view=launch_view,
        play_session_store=getattr(launch_view, '_playtime_store', None),
        parent=engine,
    )
    engine.overlay_library = overlay_library
    from native_services.ui_sounds import UiSounds
    # Menus stay quiet while a game runs; the overlay passes context "overlay" to still be heard.
    ui_sounds = UiSounds(quiet=lambda: bool(launch_view.activeGame), settings_path=headless_settings, parent=engine)
    launch_view.launched.connect(lambda *_: ui_sounds.play('launch'))
    last_launch_error = {'text': ''}
    def _launch_error_sound():
        text = launch_view.lastError or ''
        if text and text != last_launch_error['text']:
            ui_sounds.play('error')
        last_launch_error['text'] = text
    launch_view.changed.connect(_launch_error_sound)
    engine.ui_sounds = ui_sounds
    from big_screen_view import BigScreenView
    big_screen_view = BigScreenView(
        settings_path=headless_settings if headless else getattr(ui_sounds, '_path', None),
        persist_settings=True,
        launch_view=launch_view,
        emulation_view=emulation_manager,
        library_model=model,
        overlay_library=overlay_library,
        unified_controller=controller_view,
        controller_profiles=profile_assignments,
        controller_commands=controller_profiles,
        xodus_social=social_view,
        platform_social=platform_social,
        game_progress=game_progress,
        mod_management=mod_management,
        ui_sounds=ui_sounds,
        assistant_preferences=assistant_preferences,
        parent=engine,
    )
    engine.big_screen_view = big_screen_view
    # Proton versions (W19). Headless/test sessions look at an empty folder, never the user's Steam/Heroic files.
    from proton_view import ProtonView
    from native_services.proton_manager import Paths as _ProtonPaths
    def _reload_launch_profiles():
        launcher = getattr(engine, 'launch_view', None)
        if launcher is not None and launcher._profile_store is not None:
            launcher._profile_store.load()
            launcher._resolve_profiles_for_target()
            launcher.profilesChanged.emit()
    proton_paths = _ProtonPaths(headless_settings.parent) if headless else _ProtonPaths()
    store = getattr(launch_view, '_profile_store', None)
    if store is not None:
        proton_paths.launch_profiles = store.config_path      # the same file the app launches from
    proton_view = (ProtonView(proton_paths, get_latest=lambda: None, get_releases=lambda: [],
                              steam_running=lambda: False, close_steam=lambda: None,
                              on_launch_profiles_changed=_reload_launch_profiles, parent=engine)
                   if headless else ProtonView(proton_paths, on_launch_profiles_changed=_reload_launch_profiles,
                                               parent=engine))   # tests never go online or touch Steam
    engine.proton_view = proton_view
    properties = {
        'protonView': proton_view,
        'bigScreenView': big_screen_view,
        'uiSounds': ui_sounds,
        'overlaySurfaceHost': overlay_surface,
        'assistantPreferences': assistant_preferences,
        'overlayTools': overlay_tools,
        'overlayLibrary': overlay_library,
        'screenPointer': screen_pointer,
        'modBrowser': mod_browser,
        'virtualPad': virtual_pad, 'controllerProfiles': profile_assignments, 'controllerProfileIntegration': profile_integration, 'controllerProfileCommands': controller_profiles, 'controllerDeviceCommands': controller_devices, 'controllerCommands': controller_commands, 'controllerNavigation': controller_navigation, 'controllerLiveView': controller_live, 'nativeControllerEnabled': native_controller_ui, 'controllerStudioHost': studio, 'unifiedController': controller_view, 'menuOverviews': menu_overviews,
        'organizerWorkspace': organizer_workspace_view, 'organizerAgents': organizer_agent_view, 'providerJobs': download_jobs, 'gameInstalls': game_installs,
        'storeAccounts':store_accounts, 'priceComparison':price_comparison, 'ribbonArtworkSettings': ribbon_settings, 'platformSocial': platform_social, 'voiceAudioRoutes': audio_routes, 'saveManagement': save_management, 'xodusSocialView': social_view, 'xboxVoiceMedia': voice_media,
        'launcherLibrary': launcher_library,
        'steamLibrarySettings': steam_library_settings,
        'firstRunGuide': first_run_guide,
        'launchView': launch_view, 'artworkView': artwork_view,
        'gameProgressView': game_progress, 'pluginFeaturesView': plugin_features, 'emulationManager': emulation_manager, 'modManagement': mod_management,
        'modGameChoices': mod_game_choices,
        'nativeLocalLibrary': local_library,
        'nativeConnections': connection_view, 'nativeProviders': native, 'libraryModel': model, 'catalogNotice': notice, 'catalogLabel': label,
        'organizerNotice': organizer_report.text, 'initialOrganizerView': organizer,
        'catalogLinkView': link_view, 'linkStatePath': str(link_state) if link_state is not None else '',
        'initialView': view, 'headlessCheck': headless,
        'sourceOptions': model.library_sources(),
        'excludedConsoleChipSources': sorted(_EXCLUDED_CONSOLE_CHIP_SOURCES),
        'platformOptions': ['All platforms'] + sorted({v['platform'] for g in model._source_games for v in model._effective_variants(g)}),
    }
    for name, value in properties.items():
        context.setContextProperty(name, value)
    if refresh_view is not None:
        context.setContextProperty('nativeRefresh', refresh_view)
    engine.game_progress = game_progress
    engine.plugin_features = plugin_features
    engine.local_library = local_library
    engine.native_refresh = refresh_view
    engine.connection_view = connection_view
    engine.native_providers = native  # Keep Python-owned context object alive.
    engine.catalog_link_view = link_view  # Keep link overlay/controller alive.
    engine.launch_view = launch_view
    engine.artwork_view = artwork_view
    engine.launcher_library = launcher_library
    engine.steam_library_settings = steam_library_settings
    engine.first_run_guide = first_run_guide
    from live_library_updates import LiveLibraryUpdates
    live_updates = LiveLibraryUpdates(parent=engine)
    def refresh_organizer_state():
        if model._organizer is not None:
            if model._organizer.snapshot()['revision'] != model._organizer_revision:
                model._refresh_organizer()
                context.setContextProperty('platformOptions', ['All platforms'] + sorted({v['platform'] for g in model._source_games for v in model._effective_variants(g)}))
        return True
    organizer_workspace_view.metadataCommitted.connect(refresh_organizer_state)
    link_revision = {'value': link_view._store.snapshot()['revision'] if link_view._store else None}
    def refresh_link_state():
        if link_state is None or not Path(link_state).exists():
            return False
        if link_view._store is None:
            from native_services.catalog_link_store import CatalogLinkStore
            store = CatalogLinkStore(link_state)
            store.register_ids(link_view._catalog_ids())
            link_view._store = store
        revision = link_view._store.snapshot()['revision']
        if revision != link_revision['value']:
            selected = engine.rootObjects()[0].property('selectedId') if engine.rootObjects() else ''
            link_view._refresh_linked()
            link_view.changed.emit()
            if engine.rootObjects():
                engine.rootObjects()[0].setProperty('selectedId', link_view.resolution.get(selected, selected))
            link_revision['value'] = revision
        return True
    def refresh_reconciliation():
        model.configure_reconciliation(reconciliation)
        context.setContextProperty('sourceOptions', model.library_sources())
        return True
    live_updates.watch(organizer_state, refresh_organizer_state, poll_revision=True)
    live_updates.watch(link_state, refresh_link_state, poll_revision=True)
    live_updates.watch(reconciliation, refresh_reconciliation)
    context.setContextProperty('liveLibraryUpdates', live_updates)
    def refresh_emulator_profiles():
        emulation_manager.refresh()
        launcher = getattr(engine, 'launch_view', None)
        if launcher is not None and launcher._profile_store is not None:
            launcher._profile_store.load()
            launcher._resolve_profiles_for_target()
            launcher.changed.emit()
            launcher.profilesChanged.emit()
        return True
    emulation_manager.profilesCommitted.connect(refresh_emulator_profiles)
    if profile_document is not None:
        def refresh_controller_assignments():
            before = (profile_assignments.defaultProfile, profile_assignments.gameProfiles)
            if not profile_assignments.refresh(): return False
            after = (profile_assignments.defaultProfile, profile_assignments.gameProfiles)
            if before != after: profile_integration.reconcile()
            return True
        live_updates.watch(Path(profile_document), refresh_controller_assignments)
    if launch_profile_root is not None:
        live_updates.watch(Path(launch_profile_root) / 'launch_profiles.json', refresh_emulator_profiles)
    engine.save_management = save_management
    engine.menu_overviews = menu_overviews
    engine.store_accounts = store_accounts
    engine.provider_jobs = download_jobs
    engine.social_view = social_view
    engine.voice_media = voice_media
    engine.emulation_manager = emulation_manager
    engine.mod_management = mod_management
    engine.live_updates = live_updates
    engine.load(QUrl.fromLocalFile(str(HERE / 'Main.qml')))
    if not engine.rootObjects():
        raise RuntimeError('Qt Quick frontend failed to load')
    def load_organizer_report():
        if engine.rootObjects()[0].property('activePage') == 'organizer':
            organizer_report.start()
    organizer_report.changed.connect(lambda: context.setContextProperty('organizerNotice', organizer_report.text))
    engine.rootObjects()[0].activePageChanged.connect(load_organizer_report)
    load_organizer_report()
    if launcher_library is not None and (not headless or first_run_screenshot):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, launcher_library.refresh)
    if not headless:
        from ui_control import register_control
        engine.ui_control = register_control(engine.rootObjects()[0], engine)
    live_updates.start()
    return engine, model, engine.rootObjects()[0], warnings


def main():
    parser = argparse.ArgumentParser(description='Waystone desktop gaming library')
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--snapshot', type=Path, action='append')
    inputs.add_argument('--catalog', type=Path)
    inputs.add_argument('--library-db', type=Path, help='Independent read-only native SQLite library')
    parser.add_argument('--launcher-library', action='store_true', help='Open an app-owned launcher library and enable local sync')
    parser.add_argument('--launch-profile-root', type=Path, help='Shared app-owned game and emulator launch profiles')
    parser.add_argument('--artwork-override-root', type=Path, help='App-owned manual artwork overrides')
    parser.add_argument('--rom-config', type=Path, help='App-owned ROM roots and inventory configuration')
    parser.add_argument('--mod-config', type=Path, help='App-owned mod profile state; no startup deployment or scan')
    parser.add_argument('--save-config', type=Path, help='App-owned save manager configuration; no startup scan')
    parser.add_argument('--xodus-helper', type=Path, help='Existing Xodus native-social helper; only runs on Connect')
    parser.add_argument('--plugin-metadata-db', type=Path, help='Native migrated plugin history archive for per-game activity and completion data')
    parser.add_argument('--library-reconciliation', type=Path, help='App-owned exact-identity library attribution file')
    parser.add_argument('--local-games', type=Path, help='App-owned local library entries (modlist instances, non-store games)')
    parser.add_argument('--connections', type=Path, help='Explicit read-only generation-matched native observation inventory; no discovery or executor')
    parser.add_argument('--native-collector-root', type=Path, help='Explicit trusted native collector source root; refresh only on user click')
    parser.add_argument('--native-refresh-root', type=Path, help='App-owned root for new native observation snapshots')
    parser.add_argument('--organizer-state', type=Path, help='Explicit absolute native app-owned Organizer SQLite path (created only if NEW)')
    parser.add_argument('--link-state', type=Path, help='Explicit app-owned canonical-link overlay path; created only after clicking Enable')
    parser.add_argument('--emulation-db', type=Path, default=None, help='Explicit native read-only emulation SQLite snapshot')
    parser.add_argument('--store-catalog', type=Path, default=None, help='Explicit offline native store catalog; never merged into library')
    if _CLI_EMULATION_DB_FLAG and _CLI_EMULATION_DB_DEST:
        parser.add_argument(_CLI_EMULATION_DB_FLAG, dest=_CLI_EMULATION_DB_DEST, type=Path, default=None, help='Explicit native read-only add-on emulation SQLite snapshot')
    if _CLI_STORE_CATALOG_FLAG and _CLI_STORE_CATALOG_DEST:
        parser.add_argument(_CLI_STORE_CATALOG_FLAG, dest=_CLI_STORE_CATALOG_DEST, type=Path, default=None, help='Explicit offline native add-on store catalog; never merged into library')
    parser.add_argument('--media-root', type=Path)
    parser.add_argument('--organizer', action='store_true')
    parser.add_argument('--view', choices=['grid', 'details', 'list'], default='grid')
    parser.add_argument('--installed', action='store_true', help='Open with the installed filter; positive native observations are eligible')
    parser.add_argument('--native-controller-ui', action='store_true', help='Use native Waystone QML over the app-owned headless controller engine')
    parser.add_argument('--plugin-time-schema', choices=['playnite-hltb-3.11.2-classic-seconds-v1'], default=None, help='Explicit verified schema for selected HLTB import; absent keeps raw units')
    parser.add_argument('--open-controllers', action='store_true', help='Open the original controller studio inside Waystone')
    parser.add_argument('--big-screen', action='store_true', help='Open directly in Big Screen console mode')
    parser.add_argument('--open-overview', choices=['connections','roms','catalogs'], help='Open a grouped management overview without scanning or launching')
    parser.add_argument('--open-settings-accounts', action='store_true', help='Open Settings > Accounts')
    parser.add_argument('--first-run-guide', action='store_true', help='Open the library setup guide (including for screenshot/testing)')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--self-test', type=Path)
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--show-filters', action='store_true', default=True)
    parser.add_argument('--hide-filters', action='store_false', dest='show_filters')
    parser.add_argument('--show-details', action='store_true', default=None)
    parser.add_argument('--hide-details', action='store_false', dest='show_details')
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=800)
    args = parser.parse_args()
    if args.media_root is not None and args.catalog is None and args.library_db is None:
        parser.error('--media-root requires --catalog or --library-db')
    if args.show_details is None:
        args.show_details = args.view != 'list'
    headless = bool(args.smoke_test or args.self_test or args.screenshot)
    launcher_sync = bool(args.launcher_library)
    if not headless and args.snapshot is None and args.catalog is None and args.library_db is None:
        from native_services.launcher_library import default_library_path
        args.library_db = default_library_path()
        launcher_sync = True
    if launcher_sync:
        from native_services.waystone_paths import xdg_paths
        owned_data = xdg_paths()['data']
        # Edits and confirmed cross-store links belong to this user's new
        # library, never the old Kian/Playnite workspace or imported source DB.
        args.organizer_state = args.organizer_state or owned_data / 'organizer-state-v1.sqlite3'
        args.link_state = args.link_state or owned_data / 'catalog-links-v1.sqlite3'
        args.artwork_override_root = args.artwork_override_root or owned_data / 'artwork-overrides-v1'
        args.launch_profile_root = args.launch_profile_root or owned_data / 'launch-profiles-v1'
        args.rom_config = args.rom_config or owned_data / 'rom-inventory-v1'
        args.local_games = args.local_games or owned_data / 'local-games-v1.json'
    if headless:
        # All validation paths are incapable of creating a visible desktop window.
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        os.environ['QT_QUICK_BACKEND'] = 'software'
    elif args.save_config is None:
        args.save_config=_wpaths.store('saves-v1', 'config.json')
    os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Basic'
    from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent
    from PySide6.QtGui import QGuiApplication, QFont
    voice_available = False
    if not headless:
        try:
            from PySide6.QtWebEngineQuick import QtWebEngineQuick
            QtWebEngineQuick.initialize()
            voice_available = True
        except ImportError:
            pass  # Optional media runtime; the library remains usable.
    app = QGuiApplication(['waystone'])
    app.setApplicationName('Waystone')
    app.setApplicationDisplayName('Waystone')
    app.setDesktopFileName('waystone')
    # Waystone mark ("Tall three" cairn, assets/brand): window, taskbar and alt-tab icon.
    from PySide6.QtGui import QIcon
    _icon = QIcon()
    for _size in (16, 24, 32, 48, 64, 128, 256, 512):
        _png = SERVICE_ROOT / 'assets' / 'brand' / 'png' / f'waystone-{_size}.png'
        if _png.is_file():
            _icon.addFile(str(_png))
    if not _icon.isNull():
        app.setWindowIcon(_icon)
    app.setFont(QFont('Trebuchet MS', 10))
    try:
        resolved_store_catalog = args.store_catalog or (getattr(args, _CLI_STORE_CATALOG_DEST, None) if _CLI_STORE_CATALOG_DEST else None)
        resolved_emulation_db = args.emulation_db or (getattr(args, _CLI_EMULATION_DB_DEST, None) if _CLI_EMULATION_DB_DEST else None)
        engine, model, root, warnings = create_session(app, args.snapshot, args.organizer, args.view, headless,
                                                       catalog=args.catalog, library_db=args.library_db, launcher_sync=launcher_sync, media_root=args.media_root, store_catalog=resolved_store_catalog, emulation_db=resolved_emulation_db, organizer_state=args.organizer_state, connections=args.connections,
                                                       link_state=args.link_state or _wpaths.store('catalog-links-v1.sqlite3'),
                                                       native_collector_root=args.native_collector_root, native_refresh_root=args.native_refresh_root, plugin_metadata_db=args.plugin_metadata_db, plugin_time_schema=args.plugin_time_schema, reconciliation=args.library_reconciliation, launch_profile_root=args.launch_profile_root, local_games=args.local_games, artwork_override_root=args.artwork_override_root, rom_config=args.rom_config, save_config=args.save_config, mod_config=args.mod_config, xodus_helper=args.xodus_helper, voice_available=voice_available, native_controller_ui=args.native_controller_ui,
                                                       first_run_screenshot=bool(args.screenshot and args.first_run_guide))
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Unable to open catalog: {exc}', file=sys.stderr)
        return 1
    if not headless:
        # MCP live control is a thin local bridge into the running app. Requests are
        # dispatched by QLocalServer on this GUI thread and call Main.qml's exact
        # playGameById path; headless/smoke sessions never publish a control socket.
        if _is_windows():
            # The Linux AF_UNIX/peer-credential server is not a safe Windows transport;
            # a same-user Named Pipe is a separately planned controller/control slice.
            print('Waystone live control unavailable: Windows named-pipe transport is not implemented',
                  file=sys.stderr)
        else:
            try:
                from native_services.waystone_control import AppJournal, WaystoneControlServer
                state_root = _wpaths.state_home()
                control_server = WaystoneControlServer(
                    launch_view=engine.launch_view,
                    library_model=model,
                    qml_root=root,
                    journal=AppJournal(state_root / 'waystone' / 'app-journal.jsonl'),
                    parent=app,
                )
                control_server.start()
                engine.waystone_control_server = control_server
                app.aboutToQuit.connect(control_server.close)
            except (OSError, RuntimeError) as exc:
                # The library remains usable; MCP will return an explicit unavailable error.
                print(f'Waystone live control unavailable: {exc}', file=sys.stderr)
    root.resize(max(900, args.width), max(560, args.height))
    if args.installed:
        root.setProperty('status', 'installed')
    pass  # --show/--hide-filters kept for old launchers; the single Filters panel starts visible
    root.setProperty('inspectorVisible', args.show_details)
    if headless:
        root.setFlag(Qt.WindowDoesNotAcceptFocus, True)
        root.setVisible(True)  # Offscreen platform only; never requests desktop focus.
    if args.open_controllers:
        QTimer.singleShot(100, root.openControllers)
    elif args.open_overview:
        QTimer.singleShot(100, lambda: root.openManagementOverview(args.open_overview))
    elif args.open_settings_accounts:
        QTimer.singleShot(100, root.openSteamLibrarySettings if args.screenshot else root.openSettingsAccounts)
    elif args.first_run_guide and not (args.screenshot and launcher_sync):
        if engine.first_run_guide is not None:
            QTimer.singleShot(100, engine.first_run_guide.reopen)
    elif args.big_screen or (not headless and engine.big_screen_view.startInBigScreen):
        root.toggleConsole()
    result = {'code': 0}

    def finish():
        try:
            if args.smoke_test or args.self_test:
                from ui_checks import exercise_ui
                report = exercise_ui(app, root, model, warnings)
                if args.self_test:
                    args.self_test.parent.mkdir(parents=True, exist_ok=True)
                    args.self_test.write_text(json.dumps(report, indent=2) + '\n')
                for step in report['steps']:
                    print(('PASS' if step['passed'] else 'FAIL') + ' ' + step['step'] + ': ' + step['detail'])
                if not all(s['passed'] for s in report['steps']):
                    result['code'] = 2
                # Restore requested composition for a combined smoke + screenshot.
                root.clearFilters()
                root.setView(args.view)
                root.setProperty('organizerMode', args.organizer)
                pass  # see above
                root.setProperty('inspectorVisible', args.show_details)
                app.processEvents()
            if args.screenshot:
                shot = root.grabWindow()
                if shot.isNull():
                    raise RuntimeError('Window screenshot was blank')
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                if not shot.save(str(args.screenshot)):
                    raise RuntimeError('Could not save PNG')
                print(f'OFFSCREEN_PNG_OK {shot.width()}x{shot.height()}')
            if warnings:
                result['code'] = 3
        except Exception as exc:
            print(f'OFFSCREEN_CHECK_FAILED {type(exc).__name__}: {exc}', file=sys.stderr)
            result['code'] = 2
        finally:
            app.quit()

    if headless:
        # Request 120 paint/event cycles (~2 seconds) even with no focus/exposure.
        frames = {'count': 0}
        timer = QTimer(app)
        timer.setInterval(16)
        def frame():
            root.update()
            frames['count'] += 1
            if frames['count'] >= 120:
                timer.stop()
                QTimer.singleShot(50, finish)
        timer.timeout.connect(frame)
        timer.start()
    code = app.exec()
    # Destroy QML bindings before the Python-owned model/context is released.
    root.setVisible(False)
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    return result['code'] or code


if __name__ == '__main__':
    raise SystemExit(main())
