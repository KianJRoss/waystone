"""Game -> Install UX. All stores/launchers are injected fixture transports."""
import os,sys,time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtCore import QObject,QCoreApplication,QEvent,QMetaObject,Qt
from PySide6.QtGui import QGuiApplication
from provider_job_view import ProviderJobView
from game_install_controller import GameInstallController
from native_services.installation_library import catalog_install_choices
from test_provider_job_view import Manager
from test_xodus_install_view import setup as xbox_setup

def app():return QGuiApplication.instance() or QGuiApplication([])
def wait(predicate,seconds=3):
    end=time.monotonic()+seconds
    while not predicate() and time.monotonic()<end:QCoreApplication.processEvents();time.sleep(.005)
    QCoreApplication.processEvents();return predicate()
def catalog(multi=False):
    a={'id':'a','title':'Selected game','variants':[{'source':'Epic','provider_game_id':'epic-product'}]}
    b={'id':'b','title':'Other game','variants':[{'source':'GOG','provider_game_id':'123'}]}
    if multi:a['variants'].append({'source':'Steam','provider_game_id':'123'})
    return {'games':[a,b]}
def setup(tmp_path,block=False,multi=False,probe=None):
    application=app();manager=Manager(block);view=ProviderJobView(manager,catalog=catalog(multi),storage_probe=probe)
    view._root=str(tmp_path/'games')
    flow=GameInstallController(view,catalog(multi),tool_paths={p:sys.executable for p in ('epic','gog','amazon')})
    return view,flow,manager

def test_one_click_starts_exact_game_with_saved_drive_no_prompt(tmp_path):
    v,f,m=setup(tmp_path)
    try:
        assert not f.jobs and not m.executed
        assert f.install('a') and not f.prompt
        assert wait(lambda:not v.busy)
        assert len(m.executed)==1
        plan=m.executed[0];assert plan.recipe.product_id=='epic-product' and plan.recipe.install_root==tmp_path/'games'
        assert f.jobs[0]['game_id']=='a' and f.jobs[0]['phase']=='completed'
        assert not f.prompt
    finally:f.shutdown();v.shutdown()

def test_multiple_store_copies_require_exact_choice_not_first_match(tmp_path):
    v,f,m=setup(tmp_path,multi=True);uris=[];v._steam_dispatch=lambda uri:uris.append(uri) or True
    try:
        assert not f.install('a') and len(f.prompt['choices'])==2 and not m.executed
        assert not f.confirm('gog:123',str(tmp_path)) # Other game's product is not in this prompt.
        assert f.confirm('steam:123','')
        assert uris==['steam://install/123'] and not m.executed
        assert f.jobs[0]['phase']=='handed_off' and not f.prompt
    finally:f.shutdown();v.shutdown()

def test_missing_drive_asks_in_context_and_remembers_on_start(tmp_path):
    ready=[False]
    v,f,m=setup(tmp_path,probe=lambda p:{'ready':ready[0], 'message':'fixture'})
    try:
        assert not f.install('a') and f.prompt['kind']=='target' and not m.executed
        assert not f.confirm('epic:epic-product',str(tmp_path/'another'))
        ready[0]=True
        assert f.confirm('epic:epic-product',str(tmp_path/'another'))
        assert wait(lambda:not v.busy)
        assert m.executed[0].recipe.install_root==tmp_path/'another' and v.defaultRoot==str(tmp_path/'another')
    finally:f.shutdown();v.shutdown()

def test_unlinked_game_never_installs_a_different_library_record(tmp_path):
    v,f,m=setup(tmp_path)
    try:
        assert not f.install('unknown') and f.prompt['kind']=='error'
        assert not f.prompt['choices'] and not f.jobs and not m.executed and v._worker is None
        f.dismissPrompt();assert not f.prompt
    finally:f.shutdown();v.shutdown()

def test_unavailable_backend_is_local_error_not_fake_job(tmp_path):
    v,f,m=setup(tmp_path);f.tools={'epic':tmp_path/'missing'}
    try:
        assert not f.install('a') and f.prompt['kind']=='error' and not f.jobs and not m.executed
    finally:f.shutdown();v.shutdown()

def test_queue_and_cancel_without_opening_tracker_or_replaying(tmp_path):
    v,f,m=setup(tmp_path,block=True)
    try:
        assert f.install('a') and wait(lambda:len(m.executed)==1)
        assert not f.install('a') # No duplicate current job.
        assert f.install('b') and len(m.executed)==1 and f.jobs[0]['phase']=='queued'
        f.cancel(f.jobs[0]['id']);assert f.jobs[0]['phase']=='cancelled'
        f.cancel(f.jobs[-1]['id']);assert wait(lambda:not v.busy)
        assert len(m.executed)==1 and f.activeCount==0
    finally:f.shutdown();v.shutdown()

def test_queue_advances_after_owned_job_cancellation(tmp_path):
    v,f,m=setup(tmp_path,block=True)
    try:
        assert f.install('a') and wait(lambda:len(m.executed)==1)
        assert f.install('b');m.block=False
        f.cancel(f.jobs[-1]['id'])
        assert wait(lambda:len(m.executed)==2 and not v.busy)
        assert [p.recipe.product_id for p in m.executed]==['epic-product','123']
        assert f.jobs[0]['phase']=='completed' and f.jobs[1]['phase']=='cancelled'
    finally:f.shutdown();v.shutdown()

def test_xbox_single_package_checks_then_downloads_from_same_user_click(tmp_path):
    application=app();v,calls=xbox_setup(tmp_path);v._root=str(tmp_path/'games')
    f=GameInstallController(v,{'games':[{'id':'game','title':'Game','variants':[{'source':'Xbox','provider_game_id':'9ABCDEF12345'}]}]})
    try:
        assert f.install('game')
        assert wait(lambda:f.jobs and f.jobs[0]['phase']=='completed')
        assert [c[1] for c in calls]==['resolve-package','streaming'] and not f.prompt
    finally:f.shutdown();v.shutdown()

def test_xbox_multiple_packages_only_download_after_explicit_choice(tmp_path):
    application=app();v,calls=xbox_setup(tmp_path,multiple=True);v._root=str(tmp_path/'games')
    f=GameInstallController(v,{'games':[{'id':'game','title':'Game','variants':[{'source':'Xbox','provider_game_id':'9ABCDEF12345'}]}]})
    try:
        assert f.install('game') and wait(lambda:f.prompt.get('kind')=='package')
        assert len(calls)==1 and not f.confirmPackage('unknown')
        assert f.confirmPackage(f.prompt['packages'][1]['content_id'])
        assert wait(lambda:f.jobs[0]['phase']=='completed') and len(calls)==2
    finally:f.shutdown();v.shutdown()

def test_cancel_package_choice_starts_no_download(tmp_path):
    application=app();v,calls=xbox_setup(tmp_path,multiple=True);v._root=str(tmp_path)
    f=GameInstallController(v,{'games':[{'id':'game','title':'Game','variants':[{'source':'Xbox','provider_game_id':'9ABCDEF12345'}]}]})
    try:
        assert f.install('game') and wait(lambda:bool(f.prompt))
        f.dismissPrompt();QCoreApplication.processEvents()
        assert len(calls)==1 and f.jobs[0]['phase']=='cancelled' and not v.preview
    finally:f.shutdown();v.shutdown()

def close_session(application,engine,root):
    if engine.game_installs:engine.game_installs.shutdown()
    engine.provider_jobs.shutdown();application.aboutToQuit.emit()
    root.close();root.deleteLater();engine.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);application.processEvents()

def test_actual_details_install_click_stays_in_library_and_tracks_in_background(tmp_path):
    from app import create_session
    application=app();m=Manager(block=True)
    engine,model,root,warnings=create_session(application,headless=True,provider_manager=m)
    try:
        engine.provider_jobs._root=str(tmp_path)
        engine.game_installs.tools={'epic':sys.executable}
        engine.game_installs.choices=catalog_install_choices({'games':[{'id':'demo-adventure','title':'Demo Adventure','variants':[{'source':'Epic','provider_game_id':'fixture-game'}]}]})
        root.setProperty('selectedId','demo-adventure');root.setProperty('inspectorVisible',True);root.setProperty('viewMode','details')
        application.processEvents();button=root.findChild(QObject,'detailsInstallButton');assert button
        QMetaObject.invokeMethod(button,'clicked',Qt.DirectConnection)
        assert wait(lambda:len(m.executed)==1)
        assert root.property('activePage')=='library'
        assert not root.findChild(QObject,'downloadsDashboard').property('visible')
        assert not root.findChild(QObject,'gameInstallPrompt').property('visible')
        assert root.findChild(QObject,'detailsInstallButton').property('text')=='Installing…'
        assert not root.findChild(QObject,'detailsInstallButton').property('enabled')
        root.openDownloads();application.processEvents()
        assert root.findChild(QObject,'downloadTrackingList').property('count')==1
        assert len(m.executed)==1
        engine.game_installs.cancel(engine.game_installs.jobs[0]['id']);assert wait(lambda:not engine.provider_jobs.busy)
        assert not [w for w in warnings if any(x in w for x in ('ReferenceError','TypeError','Cannot assign','Binding loop'))],warnings
    finally:close_session(application,engine,root)

def test_open_tracker_is_inert_and_contains_no_installer_form(tmp_path):
    from app import create_session
    application=app();m=Manager();engine,model,root,warnings=create_session(application,headless=True,provider_manager=m)
    try:
        root.openDownloads();application.processEvents()
        assert not m.executed and engine.provider_jobs._worker is None
        assert root.findChild(QObject,'downloadTrackingList').property('count')==0
        assert root.findChild(QObject,'installProvider') is None and root.findChild(QObject,'startInstallButton') is None
        assert not engine.provider_jobs.libraryLoaded
    finally:close_session(application,engine,root)


def test_optional_options_changes_drive_without_a_tracking_screen(tmp_path):
    v,f,m=setup(tmp_path)
    try:
        assert f.options('a') and not m.executed and not f.jobs
        assert len(f.prompt['choices'])==1
        assert f.confirm('epic:epic-product',str(tmp_path/'different-drive'))
        assert wait(lambda:not v.busy)
        assert m.executed[0].recipe.install_root==tmp_path/'different-drive'
    finally:f.shutdown();v.shutdown()


def test_actual_copy_choice_prompt_never_defaults_to_an_arbitrary_store(tmp_path):
    from app import create_session
    application=app();m=Manager();uris=[]
    engine,model,root,warnings=create_session(application,headless=True,provider_manager=m)
    try:
        engine.provider_jobs._root=str(tmp_path)
        engine.provider_jobs._steam_dispatch=lambda uri:uris.append(uri) or True
        engine.game_installs.tools={'epic':sys.executable}
        engine.game_installs.choices=catalog_install_choices({'games':[{'id':'demo-adventure','title':'Demo Adventure','variants':[{'source':'Epic','provider_game_id':'fixture-game'},{'source':'Steam','provider_game_id':'123'}]}]})
        root.setProperty('selectedId','demo-adventure');root.setProperty('inspectorVisible',True);application.processEvents()
        QMetaObject.invokeMethod(root.findChild(QObject,'detailsInstallButton'),'clicked',Qt.DirectConnection)
        application.processEvents()
        prompt=root.findChild(QObject,'gameInstallPrompt');assert prompt.property('visible')
        combo=root.findChild(QObject,'gameInstallStoreChoice');button=root.findChild(QObject,'confirmGameInstall')
        assert combo.property('currentIndex')==-1 and not button.property('enabled')
        assert not m.executed and not uris and root.property('activePage')=='library'
        choices=engine.game_installs.prompt['choices'];index=next(i for i,r in enumerate(choices) if r['provider']=='steam')
        combo.setProperty('currentIndex',index);application.processEvents()
        QMetaObject.invokeMethod(button,'clicked',Qt.DirectConnection);application.processEvents()
        assert uris==['steam://install/123'] and root.property('activePage')=='library' and not m.executed
        assert not prompt.property('visible')
        assert not [w for w in warnings if any(x in w for x in ('ReferenceError','TypeError','Cannot assign','Binding loop'))],warnings
    finally:close_session(application,engine,root)
