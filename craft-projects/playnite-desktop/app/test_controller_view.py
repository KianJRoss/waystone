import os,json
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from controller_view import ControllerView
class Client:
 def __init__(self):self.calls=[];self.active='Default';self.profile={'routes':{},'unknown':{'kept':True}};self.revision='a'*64
 def request(self,operation,**fields):
  self.calls.append((operation,fields))
  if operation=='status':return {'running':True}
  if operation=='profiles':return {'names':['Default','Other'],'active_profile':self.active,'revision':self.revision}
  if operation=='profile_get':return {'name':fields['name'],'profile':self.profile,'revision':self.revision}
  if operation=='profile_update':self.profile=fields['profile'];self.revision='b'*64;return {'revision':self.revision}
  if operation=='profile_select':self.active=fields['name'];self.revision='c'*64;return {'revision':self.revision}
  if operation=='cancel_macros':return {'accepted':True}
  raise AssertionError(operation)
def settle(v):
 for _ in range(200):
  QTest.qWait(5)
  if not v.busy:return
 raise AssertionError('thread leaked')
def test_explicit_refresh_select_edit_and_readback_no_start_or_devices():
 app=QGuiApplication.instance() or QGuiApplication([]);client=Client();view=ControllerView(client)
 try:
  assert client.calls==[] and not view.available
  view.refresh();settle(view);assert view.available and view.activeProfile=='Default'
  view.loadProfile('Other');settle(view);assert view.editingProfile=='Other'
  view.useProfile();settle(view);assert view.activeProfile=='Other'
  payload=json.loads(view.profileJson);payload['routes']={'Controller:A':'Controller:B'}
  view.saveProfile(json.dumps(payload));settle(view)
  assert client.profile==payload and client.profile['unknown']=={'kept':True}
  assert [f['expected_revision'] for op,f in client.calls if op=='profile_update']==['c'*64]
  assert 'read back' in view.status
  view.cancelMacros();settle(view)
  assert all(op in {'status','profiles','profile_get','profile_select','profile_update','cancel_macros'} for op,_ in client.calls)
 finally:view.shutdown()
def test_bad_json_does_not_write():
 app=QGuiApplication.instance() or QGuiApplication([]);c=Client();v=ControllerView(c)
 try:
  v.refresh();settle(v);before=len(c.calls);v.saveProfile('[]');assert len(c.calls)==before and 'Nothing was saved' in v.status
 finally:v.shutdown()
def test_uncertain_write_is_not_replayed_and_blocks_further_mutations():
 app=QGuiApplication.instance() or QGuiApplication([])
 class Lost(Exception):code='ERR_REPLY_LOST';uncertain=True
 class Failing(Client):
  def request(self,operation,**fields):
   if operation=='profile_update':self.calls.append((operation,fields));raise Lost()
   return super().request(operation,**fields)
 c=Failing();v=ControllerView(c)
 try:
  v.refresh();settle(v);v.saveProfile('{"routes":[]}');settle(v)
  assert v.uncertain
  before=len(c.calls);v.saveProfile('{"routes":[]}');v.useProfile();assert len(c.calls)==before
  v.refresh();settle(v);assert v.uncertain
  assert len([op for op,_ in c.calls if op=='profile_update'])==1
 finally:v.shutdown()

def test_revision_conflict_reloads_profile_and_signals_without_replaying_write():
 class Conflict(Exception):code='ERR_REVISION_CONFLICT';uncertain=False
 class Conflicting(Client):
  def request(self,operation,**fields):
   if operation=='profile_update':
    self.calls.append((operation,fields))
    self.profile={'routes':{'fresh':'remote'},'stick_processing':{'left':{'inner_deadzone':0.21}}}
    self.revision='d'*64
    raise Conflict()
   return super().request(operation,**fields)
 app=QGuiApplication.instance() or QGuiApplication([]);c=Conflicting();v=ControllerView(c);finished=[];v.operationFinished.connect(finished.append)
 try:
  v.refresh();settle(v)
  draft={'routes':{'mine':'local'}}
  v.saveProfile(json.dumps(draft));settle(v)
  assert json.loads(v.profileJson)==c.profile
  assert v._revision=='d'*64 and 'Reloaded the latest profile' in v.status
  assert finished==[False]
  assert len([op for op,_ in c.calls if op=='profile_update'])==1
  assert len([op for op,_ in c.calls if op=='profile_get'])==2
 finally:v.shutdown()

def test_successful_write_but_failed_readback_remains_uncertain_without_replay():
 app=QGuiApplication.instance() or QGuiApplication([])
 class FailingRead(Client):
  wrote=False
  def request(self,operation,**fields):
   if self.wrote and operation=='status':raise RuntimeError('private error')
   result=super().request(operation,**fields)
   if operation=='profile_update':self.wrote=True
   return result
 c=FailingRead();v=ControllerView(c);finished=[];v.operationFinished.connect(finished.append)
 try:
  v.refresh();settle(v);v.saveProfile('{"routes":{}}');settle(v)
  assert v.uncertain and finished==[False] and 'private error' not in v.status
  count=len(c.calls);v.saveProfile('{"routes":{}}');assert len(c.calls)==count
 finally:v.shutdown()

def test_legacy_profile_editor_failed_save_retains_user_draft():
 from PySide6.QtCore import QObject,QMetaObject
 from app import create_session
 from ui_control import UiControl
 class Conflict(Exception):code='ERR_REVISION_CONFLICT';uncertain=False
 class Conflicting(Client):
  def request(self,operation,**fields):
   if operation=='profile_update':raise Conflict()
   return super().request(operation,**fields)
 app=QGuiApplication.instance() or QGuiApplication([]);c=Conflicting()
 engine,model,root,warnings=create_session(app,headless=True,controller_client=c)
 try:
  assert not engine.unified_controller.available and c.calls==[]
  # Retain regression coverage for the old advanced editor, but it is no longer
  # the user-facing Controllers destination (that hosts the ORIGINAL native UI).
  from PySide6.QtQml import QQmlComponent
  from PySide6.QtCore import QUrl
  from pathlib import Path
  component=QQmlComponent(engine,QUrl.fromLocalFile(str(Path(__file__).with_name('ControllerDashboard.qml'))))
  dialog=component.createWithInitialProperties({'parent':root.contentItem()})
  assert dialog is not None,component.errors()
  dialog.open();settle(engine.unified_controller);QTest.qWait(30)
  assert dialog.property('visible') and engine.unified_controller.available
  mapping=dialog.findChild(QObject,'controllerMappingEditor');assert mapping.addRoute('Controller:A','Controller:B')
  editor=dialog.findChild(QObject,'controllerProfileEditor');draft=editor.property('text')
  assert dialog.property('dirty')
  button=dialog.findChild(QObject,'controllerSaveProfile');assert button.property('enabled')
  QMetaObject.invokeMethod(button,'clicked');settle(engine.unified_controller);QTest.qWait(30)
  assert dialog.property('dirty') and editor.property('text')==draft
  assert 'changed elsewhere' in engine.unified_controller.status
  assert not warnings
 finally:
  engine.unified_controller.shutdown();engine.voice_media.shutdown();engine.social_view.shutdown()
  root.close();engine.deleteLater();QTest.qWait(30)

def test_profile_pad_type_is_saved_as_a_partial_update_and_read_back():
 app=QGuiApplication.instance() or QGuiApplication([])
 class Merging(Client):
  def request(self,operation,**fields):
   if operation=='profile_update':
    self.calls.append((operation,fields));self.profile={**self.profile,**fields['profile']};self.revision='d'*64
    return {'revision':self.revision}
   return super().request(operation,**fields)
 c=Merging();v=ControllerView(c)
 try:
  v.refresh();settle(v);assert v.padType==''
  v.setPadType('dualsense');settle(v)
  assert [f['profile'] for op,f in c.calls if op=='profile_update']==[{'virtual_gamepad_driver':'dualsense'}]
  assert v.padType=='dualsense' and c.profile['unknown']=={'kept':True} and 'read back' in v.status
  v.setPadType('');settle(v);assert v.padType=='' and c.profile['virtual_gamepad_driver'] is None
  before=len(c.calls);v.setPadType('n64');assert len(c.calls)==before
 finally:v.shutdown()
