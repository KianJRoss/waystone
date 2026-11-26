"""Unified installer fixtures; no native clients, real credentials or downloads."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
from PySide6.QtCore import QObject, QMetaObject, Qt, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from provider_job_view import ProviderJobView
from test_provider_job_view import Manager, prepare
from test_xodus_social_view import wait_until
from native_services.installation_library import InstallPreferences


def app(): return QGuiApplication.instance() or QGuiApplication([])
def catalog():
    return {'games':[{'id':'exact-game','title':'A game','variants':[
        {'source':'Steam','provider_game_id':'123'},
        {'source':'GOG','provider_game_id':'123'},
        {'source':'Steam','provider_game_id':'not-a-store-id'}]}]}


def test_select_from_library_is_inert_and_provider_qualified():
    application=app();manager=Manager();view=ProviderJobView(manager,catalog=catalog())
    try:
        assert view.forGame('exact-game')
        assert view.selection['key']=='gog:123' # Stable sorted choices, not title-based merging.
        assert view.chooseGame('steam:123')
        assert view.selection['product_id']=='123' and view.currentProvider=='steam'
        assert not view.chooseGame('steam:internal-guid')
        assert manager.executed==[] and view._worker is None
        view.setProvider('battlenet');assert not view.canInstall
        assert 'not implemented' in view.providerNotice and not view.selection
    finally:view.shutdown()


def test_refresh_reads_only_cache_and_worker_is_joined(tmp_path):
    application=app();calls=[]
    class Reader:
        def load_entitlements(self,provider,config_root):
            calls.append((provider,config_root))
            return [SimpleNamespace(provider=provider,product_id='777',title='Cached game',is_installed=False)]
    view=ProviderJobView(Manager(),cache_reader=Reader(),catalog=catalog())
    try:
        view.setAccountConfigRoot(str(tmp_path));assert view.refreshLibrary()
        assert wait_until(lambda:not view.loadingLibrary,2000)
        assert {p for p,r in calls}=={'epic','gog','amazon'}
        assert all(r==tmp_path for p,r in calls)
        assert view.chooseGame('gog:777') and view.selection['is_installed'] is False
        assert view._manager.executed==[]
    finally:
        assert view._worker is None
        view.shutdown()


def test_steam_confirmation_is_not_download_or_installed_result():
    application=app();uris=[];view=ProviderJobView(Manager(),steam_dispatch=lambda uri:uris.append(uri) or True)
    try:
        assert view.prepare('steam','123','Steam game','','install','','')
        assert uris==[] and view._worker is None
        assert view.start() and uris==['steam://install/123']
        assert view.state['phase']=='handed_off' and view.state['percent'] is None
        assert view._manager.executed==[] and not view.start()
        for bad in ('--help','steam://run/123','0','internal-guid'):
            assert not view.prepare('steam',bad,'','','install','','')
        assert not view.prepare('steam','123','', '', 'repair','','')
    finally:view.shutdown()


def test_refused_steam_handoff_is_failure_without_secret_output():
    application=app();view=ProviderJobView(steam_dispatch=lambda uri:False)
    try:
        assert view.prepare('steam','123','', '', 'install','','') and not view.start()
        assert view.state['phase']=='failed' and not view.preview
    finally:view.shutdown()


def test_drive_revalidated_before_start_and_preferences_not_written_early(tmp_path):
    application=app();manager=Manager();ready=[True]
    view=ProviderJobView(manager,preferences=InstallPreferences(tmp_path/'settings.json'),
        storage_probe=lambda path:{'ready':ready[0], 'free_bytes':100, 'message':'fixture'})
    try:
        assert prepare(view);assert not (tmp_path/'settings.json').exists()
        ready[0]=False
        assert not view.start() and not manager.executed and view._worker is None
        assert not (tmp_path/'settings.json').exists() and not view.preview
        ready[0]=True;assert prepare(view) and view.start()
        assert wait_until(lambda:not view.busy,2000)
        assert (tmp_path/'settings.json').exists()
    finally:view.shutdown()


def test_nonexistent_tool_never_starts_worker(tmp_path):
    application=app();view=ProviderJobView(Manager())
    try:
        assert view.prepare('gog','123','Game',str(tmp_path),'install',str(tmp_path/'not-installed'),'')
        assert not view.start() and view._worker is None
        assert view.state['phase']=='failed'
    finally:view.shutdown()


def test_unlinked_game_reports_in_context_without_opening_downloads(tmp_path):
    application=app()
    from app import create_session
    engine,model,root,warnings=create_session(application,headless=True)
    try:
        root.setProperty('viewMode','details')
        root.setProperty('inspectorVisible',True)
        application.processEvents()
        pane=root.findChild(QObject,'gameDetails');assert pane is not None
        QMetaObject.invokeMethod(pane,'installRequested',Qt.DirectConnection,
            __import__('PySide6.QtCore',fromlist=['Q_ARG']).Q_ARG(str,'unlinked-game'))
        application.processEvents()
        assert root.property('activePage')=='library'
        assert not root.findChild(QObject,'downloadsDashboard').property('visible')
        assert root.findChild(QObject,'gameInstallPrompt').property('visible')
        assert engine.game_installs.prompt['kind']=='error'
        assert wait_until(lambda:not engine.provider_jobs.loadingLibrary,2000)
        assert engine.provider_jobs._worker is None
        assert not [w for w in warnings if any(x in w for x in ('ReferenceError','TypeError','Cannot assign'))]
    finally:
        engine.provider_jobs.shutdown()
        application.aboutToQuit.emit()
        root.close();root.deleteLater();engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);application.processEvents()


def test_provider_job_view_defaults_to_waystone_owned_account_root(monkeypatch):
    application=app()
    monkeypatch.setenv('HOME','/tmp/waystone-home-fixture')
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    view=ProviderJobView(Manager())
    try:
        # a new install keeps provider accounts under Waystone's own data dir
        assert str(view.accountConfigRoot).startswith('/tmp/waystone-home-fixture/.local/share/waystone/providers-v1')
        assert '/.config/heroic' not in str(view.accountConfigRoot)
    finally:view.shutdown()
