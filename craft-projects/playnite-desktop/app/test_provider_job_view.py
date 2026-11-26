"""Download UI/lifecycle fixtures; never execute provider tools or contact stores."""
import asyncio,os,sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QObject,QMetaObject,QUrl,Qt,QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine,QQmlComponent
from PySide6.QtQuick import QQuickWindow
from provider_job_view import ProviderJobView
from native_services.provider_jobs import ProviderJobProgress
from test_xodus_social_view import wait_until
APP=Path(__file__).resolve().parent

class Manager:
    def __init__(self,block=False):self.executed=[];self.block=block;self.task=None
    def set_tool_config(self,provider,config):self.tool=config.binary_path
    def create_plan(self,recipe):return SimpleNamespace(recipe=recipe,tool_binary=self.tool,destination_dir=recipe.install_root/recipe.product_id)
    async def execute_job(self,plan,progress):
        self.task=asyncio.current_task();self.executed.append(plan)
        progress(ProviderJobProgress(phase='running',percent=30,message='Downloading…'))
        if self.block:await asyncio.sleep(100)
        return ProviderJobProgress(phase='completed',percent=100,message='Completed',exit_code=0)
    async def cancel_job(self,provider,product):
        if self.task and not self.task.done():
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
        return True

def app():return QGuiApplication.instance() or QGuiApplication([])
def prepare(view):return view.prepare('epic','actual-app-id','Fixture title','/tmp/waystone-install-fixture','install',sys.executable,'')

def test_prepare_does_not_start_worker_or_execute():
    application=app();manager=Manager();view=ProviderJobView(manager)
    assert prepare(view)
    assert manager.executed==[] and view._worker is None and not view.busy
    assert view.preview['product_id']=='actual-app-id'
    assert view.preview['destination'].endswith('/actual-app-id')
    view.clearPreview();assert not view.preview
    view.shutdown()

def test_explicit_start_and_terminal_state_delivery():
    application=app();manager=Manager();view=ProviderJobView(manager)
    try:
        assert prepare(view) and view.start()
        assert wait_until(lambda:not view.busy,2000)
        assert len(manager.executed)==1 and view.state['phase']=='completed'
        assert not view.preview and not view.start()
    finally:view.shutdown()
    assert view._worker is None

def test_cancel_and_shutdown_leave_no_worker():
    application=app();manager=Manager(block=True);view=ProviderJobView(manager)
    try:
        assert prepare(view) and view.start()
        assert wait_until(lambda:bool(manager.executed),2000)
        assert not prepare(view)
        view.cancel();assert wait_until(lambda:not view.busy,2000)
        assert view.state['phase']=='cancelled'
    finally:view.shutdown()
    assert view._worker is None

def test_dashboard_tracks_only_and_opening_never_prepares_or_executes():
    from game_install_controller import GameInstallController
    application=app();manager=Manager();view=ProviderJobView(manager)
    flow=GameInstallController(view)
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('providerJobs',view)
    engine.rootContext().setContextProperty('gameInstalls',flow)
    engine.addImportPath(str(APP));window=QQuickWindow();window.resize(1000,750)
    component=QQmlComponent(engine);component.setData(b'import QtQuick\nItem { width:1000;height:750; DownloadsDashboard {} }',QUrl.fromLocalFile(str(APP/'download_fixture.qml')))
    root=component.create();assert root is not None,[e.toString() for e in component.errors()]
    root.setParentItem(window.contentItem());dialog=root.findChild(QObject,'downloadsDashboard');dialog.open()
    try:
        application.processEvents()
        assert manager.executed==[] and view._worker is None and not view.preview
        assert not view.libraryLoaded and not view.loadingLibrary
        assert dialog.findChild(QObject,'startInstallButton') is None
        assert dialog.findChild(QObject,'installProductId') is None
        assert dialog.findChild(QObject,'downloadTrackingList').property('count')==0
    finally:
        dialog.close();flow.shutdown();view.shutdown();root.deleteLater();window.deleteLater();engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);application.processEvents()


def test_connected_store_refreshes_preserve_each_other():
    application=app();view=ProviderJobView(Manager())
    try:
        view.addManagedEpicLibrary([{'app_name':'epic-one','app_title':'Epic One'}])
        view.addManagedAmazonLibrary([{'product_id':'amazon-one','title':'Amazon One'}])
        keys={row['key'] for row in view._choices}
        assert {'epic:epic-one','amazon:amazon-one'} <= keys
    finally:view.shutdown()
