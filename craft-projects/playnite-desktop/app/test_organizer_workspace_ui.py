import json,os,threading
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
from PySide6.QtCore import QObject,QUrl,QMetaObject,Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlApplicationEngine,QQmlExpression,QQmlEngine
from PySide6.QtQuick import QQuickWindow
from organizer_workspace_view import OrganizerWorkspaceView
from organizer_agent_view import OrganizerAgentView
from native_services.organizer_agent_workspace import OrganizerAgentWorkspace
from native_services.organizer_control import OrganizerControl
from native_services.organizer_store import OrganizerStore
from app import create_session

_APP=None
def app():
    global _APP
    _APP=QGuiApplication.instance() or QGuiApplication([]);return _APP

def settle(view):
    for _ in range(1200):
        QTest.qWait(5)
        if not view.busy:return
    raise AssertionError('Organizer worker leaked')

def workspace(tmp_path):
    store=OrganizerStore.create(tmp_path/'organizer.sqlite',['g1','g2'])
    return OrganizerAgentWorkspace(OrganizerControl(store)),store

def test_real_edit_undo_conflict_preserves_selected_snapshot(tmp_path):
    app();w,s=workspace(tmp_path);v=OrganizerWorkspaceView(w)
    try:
        assert not v.rows and not v.busy
        v.selectGame('g1');settle(v);assert v.selectedGameId=='g1'
        signals=[];v.metadataCommitted.connect(lambda:signals.append(True))
        v.saveEdits('{"title":"Animal Crossing","platform":"GameCube","categories":["Nintendo"]}')
        settle(v);assert v.selectedGame['title']=='Animal Crossing' and v.revision==1 and len(signals)==1
        w.edit_metadata([{'game_id':'g2','changes':{'title':'Other'}}],1,'other-window')
        v.saveEdits('{"title":"stale draft"}');settle(v)
        assert 'Draft retained' in v.status and v.selectedGame['title']=='Animal Crossing' and not v.uncertain
        v.refresh();settle(v);assert v.canUndo
        v.undo();settle(v);assert v.revision==3 and len(signals)==2
    finally:v.shutdown()

def test_real_qml_save_apply_and_discard_prompt(tmp_path):
    app();w,s=workspace(tmp_path);v=OrganizerWorkspaceView(w);a=OrganizerAgentView(w)
    engine=QQmlApplicationEngine();warnings=[];engine.warnings.connect(lambda items:warnings.extend(x.toString() for x in items))
    engine.addImportPath(str(Path(__file__).parent));ctx=engine.rootContext()
    ctx.setContextProperty('organizerWorkspace',v);ctx.setContextProperty('organizerAgents',a)
    engine.loadData(b'import QtQuick; import QtQuick.Controls; ApplicationWindow { visible:true; width:1100; height:800; OrganizerWorkspace { anchors.fill:parent; initialGameId:"g1" } }',QUrl.fromLocalFile(str(Path(__file__).parent/'workspace-test.qml')))
    root=engine.rootObjects()[0]
    try:
        settle(v);title=root.findChild(QObject,'organizerTitleEdit');assert title
        component=root.findChild(QObject,'organizerWorkspace')
        title.setProperty('text','Animal Crossing');component.setProperty('dirty',True)
        assert QMetaObject.invokeMethod(root.findChild(QObject,'organizerSave'),'click')
        settle(v);assert s.snapshot()['overrides']['g1']['title']=='Animal Crossing'
        assert not component.property('dirty')
        title.setProperty('text','uncommitted');component.setProperty('dirty',True)
        scope=QQmlEngine.contextForObject(component)
        expression=QQmlExpression(scope,component,'chooseGame("g2")');expression.evaluate();QTest.qWait(20)
        assert root.findChild(QObject,'organizerDiscardDialog').property('visible')
        assert v.selectedGameId=='g1' and title.property('text')=='uncommitted'
        # No transport constructed/inference from loading the UI.
        assert a.gateway._observation is None and w.sessions()==[]
        assert not warnings
    finally:
        root.close();v.shutdown();a.shutdown();engine.deleteLater();QTest.qWait(20)

def test_rail_routes_exist_without_opening_hamburger_or_connecting(tmp_path):
    app();engine,model,root,warnings=create_session(app(),headless=True)
    try:
        root.show();QTest.qWait(30)
        for name in ['railLibrary','railStore','railDownloads','railMods','railSaves','railEmulation','railControllers','railSocial','railSettings','railConsole','railOverlay','railQuit','organizerButton']:
            item=root.findChild(QObject,name);assert item and item.property('visible'),name
        for gone in ['railInstalled','railDevice','railConnections','railFriends','railAchievements','railRoms']:
            assert root.findChild(QObject,gone) is None,gone  # one home per feature (Kian 2026-11-22)
        QMetaObject.invokeMethod(root.findChild(QObject,'organizerButton'),'click');QTest.qWait(20)
        assert root.property('organizerMode') and root.property('activePage')=='organizer'
        QMetaObject.invokeMethod(root.findChild(QObject,'organizerButton'),'click');QTest.qWait(20)
        assert not root.property('organizerMode') and root.property('activePage')=='library'
        QMetaObject.invokeMethod(root.findChild(QObject,'railSettings'),'click');QTest.qWait(20)
        QMetaObject.invokeMethod(root.findChild(QObject,'railLibrary'),'click');QTest.qWait(20)
        assert root.property('activePage')=='library'
        assert engine.organizer_agent_view.gateway is None and not warnings
    finally:
        root.close();engine.organizer_workspace_view.shutdown();engine.organizer_agent_view.shutdown();engine.voice_media.shutdown();engine.social_view.shutdown();engine.deleteLater();QTest.qWait(30)

def test_editor_commit_updates_same_app_model_without_restart(tmp_path):
    from app import HERE
    application=app();data=json.loads((HERE/'fixture-library.json').read_text())
    data['catalog_kind']='playnite-display-selected-copy-v1'
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps(data))
    engine,model,root,warnings=create_session(application,headless=True,catalog=catalog,organizer_state=tmp_path/'state.sqlite')
    view=engine.organizer_workspace_view;key=data['games'][0]['id']
    try:
        view.selectGame(key);settle(view)
        view.saveEdits('{"title":"Immediate native edit","tags":["From Organizer"]}');settle(view)
        assert model.overview(key)['title']=='Immediate native edit'
        assert 'From Organizer' in model.overview(key)['tags']
        assert model._organizer_revision==1
        assert json.loads(catalog.read_text())==data  # imported catalog untouched
        view.undo();settle(view)
        assert model.overview(key)['title']!='Immediate native edit' and model._organizer_revision==2
        assert not warnings
    finally:
        root.close();view.shutdown();engine.organizer_agent_view.shutdown();engine.live_updates.stop();engine.voice_media.shutdown();engine.social_view.shutdown();engine.deleteLater();QTest.qWait(30)

def test_qml_explicit_connect_run_review_apply_real_store(tmp_path):
    from native_services.organizer_agent_gateway import OrganizerAgentGateway
    class Response:
        def __init__(self,data):self.data=data
        def json(self):return self.data
    class Transport:
        def __init__(self):self.token=None;self.client=self;self.calls=[]
        def __call__(self,*args):self.token='fixture-private';return {'events':[],'cursor':0}
        def relay_request(self,method,node,path,**kwargs):
            self.calls.append((method,path))
            if method=='GET':return Response({'agents':[{'name':'existing-builder'}]})
            body=kwargs['json']
            assert body['agent']=='existing-builder'
            return Response({'agent':body['agent'],'attempt_id':body['activity_id'],
                'reply':json.dumps({'edits':[{'game_id':'g1','changes':{'tags':['Suggested by agent']}}]})})
    app();w,s=workspace(tmp_path);transport=Transport()
    gateway=OrganizerAgentGateway(w,observation_factory=lambda:transport)
    v=OrganizerWorkspaceView(w);a=OrganizerAgentView(w,gateway)
    a.proposalReady.connect(v.refreshAgentActivity)
    engine=QQmlApplicationEngine();warnings=[];engine.warnings.connect(lambda items:warnings.extend(x.toString() for x in items))
    engine.addImportPath(str(Path(__file__).parent));ctx=engine.rootContext()
    ctx.setContextProperty('organizerWorkspace',v);ctx.setContextProperty('organizerAgents',a)
    engine.loadData(b'import QtQuick; import QtQuick.Controls; ApplicationWindow { visible:true; width:1100; height:800; OrganizerWorkspace { anchors.fill:parent; initialGameId:"g1"; section:1 } }',QUrl.fromLocalFile(str(Path(__file__).parent/'workspace-agent-test.qml')))
    root=engine.rootObjects()[0]
    try:
        settle(v);assert transport.calls==[]
        QMetaObject.invokeMethod(root.findChild(QObject,'organizerConnectAgents'),'click');settle(a)
        assert a.connected and transport.calls==[('GET','/api/agents')]
        # Preparing a useful task adjusts scope but never starts inference.
        component=root.findChild(QObject,'organizerWorkspace')
        component.prepareAssistantTask('organize')
        assert 'tags, categories and library collections' in root.findChild(QObject,'organizerAgentTask').property('text')
        assert transport.calls==[('GET','/api/agents')]
        root.findChild(QObject,'organizerAgentTask').setProperty('text','Add useful metadata tags')
        QTest.qWait(20)
        run=root.findChild(QObject,'organizerRunAgent');assert run.property('enabled')
        QMetaObject.invokeMethod(run,'click');settle(a);settle(v)
        assert len(v.proposals)==1 and v.proposals[0]['status']=='pending' and not s.audit()
        assert transport.calls==[('GET','/api/agents'),('POST','/api/agents/run')]
        p=v.proposals[0];v.acceptProposal(p['proposal_id'],p['preview']['revision']);settle(v)
        assert s.snapshot()['overrides']['g1']['tags']==['Suggested by agent']
        assert len(s.audit())==1 and not warnings
    finally:
        root.close();v.shutdown();a.shutdown();engine.deleteLater();QTest.qWait(20)

def test_ui_only_organizer_activation_never_starts_gateway():
    from ui_control import UiControl
    application=app();engine,model,root,warnings=create_session(application,headless=True)
    try:
        control=UiControl(root);control.ShowOrganizer();QTest.qWait(20)
        assert root.property('organizerMode') and not root.property('consoleMode')
        assert engine.organizer_agent_view.gateway is None and not warnings
    finally:
        root.close();engine.organizer_workspace_view.shutdown();engine.organizer_agent_view.shutdown();engine.voice_media.shutdown();engine.social_view.shutdown();engine.deleteLater();QTest.qWait(20)
