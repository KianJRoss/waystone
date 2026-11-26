"""Actual Main/create_session wiring with fixture file/native protocol, no real socket."""
import hashlib
import json
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Property, Slot, QEvent, QCoreApplication
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session

_APP=None

class Host(QObject):
    ready=Signal();changed=Signal()
    def __init__(self):super().__init__();self.starts=0
    @Property(bool,notify=changed)
    def available(self):return False
    @Property(bool,notify=changed)
    def running(self):return False
    @Property(str,notify=changed)
    def status(self):return 'Fixture engine host; no physical devices'
    @Slot()
    def start(self):self.starts+=1
    def shutdown(self):pass

class Client:
    def __init__(self,path):self.path=path;self.calls=[]
    def request(self,op,**fields):
        self.calls.append((op,fields))
        raw=self.path.read_bytes();doc=json.loads(raw);revision=hashlib.sha256(raw).hexdigest()
        if op=='profiles':return {'names':list(doc['profiles']),'active_profile':doc['active_profile'],'revision':revision}
        if op=='status':return {'running':True,'devices':[]}
        if op=='profile_get':return {'name':fields['name'],'profile':doc['profiles'][fields['name']],'revision':revision}
        if op=='profile_select':
            assert fields['expected_revision']==revision
            doc['active_profile']=fields['name'];self.path.write_text(json.dumps(doc))
            return {'name':fields['name'],'revision':hashlib.sha256(self.path.read_bytes()).hexdigest()}
        raise AssertionError(op)


def wait(check):
    for _ in range(200):
        if check():return
        QTest.qWait(10)
    raise AssertionError('profile fixture completion did not settle')


def test_real_session_switch_and_restore_preserves_editor_and_picker_contract(tmp_path):
    global _APP
    _APP=QGuiApplication.instance() or QGuiApplication([])
    path=tmp_path/'controller/profiles.json';path.parent.mkdir()
    path.write_text(json.dumps({'version':1,'active_profile':'General','profiles':{'General':{'opaque':1},'Racing':{'motion':{'passthrough':True}}},'unknown_root':{'keep':True}}))
    before=path.read_bytes();client=Client(path);host=Host()
    engine,model,root,warnings=create_session(_APP,headless=True,provider_jobs_enabled=False,controller_client=client,studio_host=host,native_controller_ui=True,controller_profiles_path=path)
    try:
        engine.live_updates.stop()
        assert host.starts==0 and client.calls==[] and path.read_bytes()==before
        view=engine.controller_profile_assignments;wire=engine.controller_profile_integration
        game_id=model.games[0]['id']
        assert view.assignGame(game_id,'Racing')
        # Explicit CRUD can resolve the general default, but does not start an engine.
        wait(lambda:not engine.controller_profile_activation.busy)
        engine.unified_controller.refresh();wait(lambda:not engine.unified_controller.busy)
        assert engine.unified_controller.editingProfile=='General'
        # Shared guard protects every legacy editor save entry point too, not
        # only the QML Use profile button. Reads remain available.
        wire.runtime._uncertain=True;wire._publish()
        before_calls=list(client.calls);draft=engine.unified_controller.profileJson
        engine.unified_controller.saveProfile(draft);engine.unified_controller.useProfile()
        assert client.calls==before_calls and engine.unified_controller.profileJson==draft
        wire.runtime._uncertain=False;wire._publish()
        engine.launch_view.controllerSessionObserved.emit('fake-session',game_id,'launching')
        assert view.runtimeGameId==''
        engine.launch_view.controllerSessionObserved.emit('fake-session',game_id,'running')
        wait(lambda:wire.runtime.active_profile=='Racing' and not engine.controller_profile_activation.busy)
        assert json.loads(path.read_text())['active_profile']=='Racing'
        assert json.loads(path.read_text())['unknown_root']=={'keep':True}
        assert engine.unified_controller.editingProfile=='General'  # automatic activation never picks another editor draft
        assert view.runtimeGameId==game_id
        root.setProperty('selectedId',game_id);QTest.qWait(20)
        picker=root.findChild(QObject,'detailsControllerProfilePicker')
        assert picker and picker.property('gameId')==game_id
        assert picker.property('choices').toVariant()[0]=='Use default (General)'
        engine.launch_view.setTarget('unrelated',{'provider':'steam','canonical_game_id':'other-selection'})
        engine.launch_view.controllerSessionObserved.emit('fake-session',game_id,'unknown')
        assert view.runtimeGameId==game_id
        engine.launch_view.controllerSessionObserved.emit('fake-session',game_id,'closed')
        wait(lambda:wire.runtime.active_profile=='General' and not engine.controller_profile_activation.busy)
        assert json.loads(path.read_text())['active_profile']=='General'
        assert host.starts==0
        assert [f['name'] for op,f in client.calls if op=='profile_select']==['Racing','General']
        assert not [w for w in warnings if any(s in w for s in ('ReferenceError','TypeError','Binding loop','Cannot assign'))]
    finally:
        engine.controller_profile_activation.shutdown();engine.unified_controller.shutdown()
        engine.controller_live.shutdown();engine.provider_jobs.shutdown() if engine.provider_jobs else None
        engine.social_view.shutdown();engine.voice_media.shutdown();engine.live_updates.stop()
        engine.organizer_agent_view.shutdown();engine.organizer_report.shutdown()
        root.close();root.deleteLater();engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);QTest.qWait(30)
