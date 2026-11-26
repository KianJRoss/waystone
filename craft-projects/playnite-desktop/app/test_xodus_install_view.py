"""Xbox install UI against the actual adapter and synthetic CLI responses only."""
import asyncio
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import time
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from provider_job_view import ProviderJobView
from native_services.xodus_install import XodusInstaller
from test_xodus_install import PRODUCT,CONTENT,response,frame,files

def app():return QGuiApplication.instance() or QGuiApplication([])

def wait_until(predicate,timeout_ms=2000):
    # Give Python's worker its GIL slice as well as delivering Qt events. Some
    # distro PySide builds hold the GIL through repeated QTest.qWait calls.
    deadline=time.monotonic()+timeout_ms/1000
    while not predicate() and time.monotonic()<deadline:
        QCoreApplication.processEvents();time.sleep(.005)
    QCoreApplication.processEvents()
    return predicate()

def setup(tmp_path,*,block=False,multiple=False,library=False):
    calls=[]
    async def process(argv,**kw):
        calls.append(argv)
        if argv[1]=='library':
            return 0,json.dumps({'schema':1,'market':'US','account':'not-exported@example.test',
                'entitlements':[{'productId':PRODUCT,'status':'active','isTrial':True}]})
        if argv[1]=='resolve-package':
            if block:await asyncio.sleep(999)
            data=json.loads(response())
            if multiple:
                extra=dict(data['choices'][0],contentId='aaaaaaaa-2222-3333-4444-555555555555',title='Other PC package')
                data['choices'].append(extra);data['status']='choice'
            return 0,json.dumps(data)
        if block:await asyncio.sleep(999)
        files(Path(argv[3]));kw['on_line'](frame('complete'));return 0,''
    async def titles(ids,market):return {}
    installer=XodusInstaller(sys.executable,process=process,busy_check=lambda p:False,title_reader=titles)
    view=ProviderJobView(catalog={'games':[{'id':'game','title':'Real catalog title','variants':[
        {'source':'Xbox','provider_game_id':PRODUCT}]}]})
    view.configureXodus(sys.executable,installer=installer)
    return view,calls


def test_lookup_is_not_download_and_explicit_start_verifies_files(tmp_path):
    application=app();view,calls=setup(tmp_path)
    try:
        view.forGame('game');assert not calls and view.currentProvider=='xbox'
        assert view.prepare('xbox',PRODUCT,'Fixture',str(tmp_path/'games'),'install',sys.executable,'')
        assert wait_until(lambda:not view.busy,2000)
        assert len(calls)==1 and calls[0][1]=='resolve-package'
        assert view.preview['content_id']==CONTENT and not (tmp_path/'games').exists()
        assert view.start()
        assert wait_until(lambda:not view.busy,2000), (view.state,calls,view._worker.isRunning(),repr(view._xodus._active))
        assert view.state['phase']=='completed' and view.xboxReceipt['state']=='files_verified'
        assert calls[-1][1]=='streaming' and not view.start()
    finally:assert view.shutdown()


def test_multiple_packages_need_explicit_selection_and_clear_invalidates_it(tmp_path):
    application=app();view,calls=setup(tmp_path,multiple=True)
    try:
        assert view.prepare('xbox',PRODUCT,'',str(tmp_path),'install',sys.executable,'')
        assert wait_until(lambda:not view.busy,2000)
        assert len(view.packageChoices)==2 and not view.preview and not view.start()
        assert not view.choosePackage('not-in-response') and view.choosePackage(CONTENT)
        view.clearPreview();assert not view.start() and not view.choosePackage(CONTENT)
        assert len(calls)==1
    finally:assert view.shutdown()


def test_switch_provider_never_keeps_an_xbox_plan(tmp_path):
    application=app();view,calls=setup(tmp_path)
    try:
        view.prepare('xbox',PRODUCT,'',str(tmp_path),'install',sys.executable,'')
        assert wait_until(lambda:not view.busy,2000)
        assert view.preview
        view.setProvider('epic');assert not view.preview and not view.packageChoices and not view.start()
    finally:assert view.shutdown()


def test_account_library_is_explicit_and_does_not_claim_trial_ownership(tmp_path):
    application=app();view,calls=setup(tmp_path)
    try:
        assert calls==[]
        assert view.refreshXboxLibrary() and wait_until(lambda:not view.loadingLibrary,2000)
        view.setProvider('xbox');rows=view.games
        assert len(rows)==1 and rows[0]['title']=='Real catalog title'
        assert 'entitlement record' in rows[0]['origin'] and 'owned' not in rows[0]['origin']
        assert 'not-exported' not in json.dumps(rows)
        assert calls[0][1]=='library' and view._worker is None
    finally:assert view.shutdown()


def test_shutdown_cancels_pending_public_lookup_without_leaking_thread(tmp_path):
    application=app();view,calls=setup(tmp_path,block=True)
    view.prepare('xbox',PRODUCT,'',str(tmp_path),'install',sys.executable,'')
    assert wait_until(lambda:len(calls)>0,2000)
    thread=view._worker
    assert view.shutdown() and view._worker is None and not thread.isRunning()


def test_cancel_pending_lookup_does_not_start_streaming(tmp_path):
    application=app();view,calls=setup(tmp_path,block=True)
    try:
        view.prepare('xbox',PRODUCT,'',str(tmp_path),'install',sys.executable,'')
        assert wait_until(lambda:len(calls)>0,2000)
        view.cancel();assert wait_until(lambda:not view.busy,2000)
        assert view.state['phase']=='cancelled' and len(calls)==1 and not view.preview
    finally:assert view.shutdown()


def test_package_combo_needs_explicit_choice_in_real_qml(tmp_path):
    from PySide6.QtCore import QObject,QUrl,QMetaObject,Qt,QEvent
    from PySide6.QtQml import QQmlApplicationEngine,QQmlComponent
    from PySide6.QtQuick import QQuickWindow
    from game_install_controller import GameInstallController
    application=app();view,calls=setup(tmp_path,multiple=True);view._root=str(tmp_path)
    flow=GameInstallController(view,{'games':[{'id':'game','title':'Game','variants':[{'source':'Xbox','provider_game_id':PRODUCT}]}]})
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('providerJobs',view)
    engine.rootContext().setContextProperty('gameInstalls',flow)
    here=Path(__file__).resolve().parent;engine.addImportPath(str(here))
    window=QQuickWindow();window.resize(1000,800)
    component=QQmlComponent(engine);component.setData(b'import QtQuick\nItem { width:1000;height:800; GameInstallPrompt {} }',QUrl.fromLocalFile(str(here/'xbox-download-fixture.qml')))
    root=component.create();assert root is not None,[e.toString() for e in component.errors()]
    root.setParentItem(window.contentItem())
    try:
        assert flow.install('game') and wait_until(lambda:bool(flow.prompt))
        application.processEvents()
        combo=root.findChild(QObject,'gameInstallPackageChoice');assert combo.property('currentIndex')==-1
        start=root.findChild(QObject,'confirmGameInstall');assert not start.property('enabled')
        assert len(calls)==1
        combo.setProperty('currentIndex',1);application.processEvents()
        assert start.property('enabled')
        QMetaObject.invokeMethod(start,'clicked',Qt.DirectConnection)
        assert wait_until(lambda:flow.jobs[0]['phase']=='completed')
        assert len(calls)==2 and 'aaaaaaaa-2222-3333-4444-555555555555' in calls[1]
    finally:
        flow.shutdown();view.shutdown();window.close();root.deleteLater();window.deleteLater();engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);application.processEvents()
