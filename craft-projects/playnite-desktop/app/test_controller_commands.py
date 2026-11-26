import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from controller_commands import ControllerCommands
from native_services.controller_command_protocol import OPERATIONS
from native_services.unified_controller_control import ControllerControlError

_APP=None
class Client:
 def __init__(self):self.calls=[];self.fail=False
 def request(self,op,**fields):
  self.calls.append((op,fields))
  if op=='command_capabilities':return dict(schema_version=1,operations=sorted(OPERATIONS))
  if self.fail:raise ControllerControlError('ERR_TIMEOUT',uncertain=True)
  return dict(accepted=True,operation=op,completion='queued')
def make(client=None):
 global _APP
 _APP=QGuiApplication.instance() or QGuiApplication([])
 return ControllerCommands(client or Client())
def settle(v):
 for _ in range(300):
  QTest.qWait(5)
  if not v.busy:return
 raise AssertionError('command thread leaked')
def test_inert_and_capability_gate():
 c=Client();v=make(c)
 try:
  assert not c.calls and not v.available
  v.recenterMotion();assert not c.calls
  v.refreshCapabilities();settle(v);assert v.available and not v.uncertain
  v.calibrateMotion();settle(v);assert 'queued' in v.status and c.calls[-1][0]=='motion_calibrate'
 finally:v.shutdown()
def test_invalid_rumble_never_sent():
 c=Client();v=make(c)
 try:
  v.refreshCapabilities();settle(v);n=len(c.calls)
  v.testRumble(2,0,1);assert len(c.calls)==n
 finally:v.shutdown()
def test_uncertain_blocks_no_replay_refresh_does_not_clear():
 c=Client();v=make(c)
 try:
  v.refreshCapabilities();settle(v);c.fail=True
  v.recenterMotion();settle(v);assert v.uncertain
  n=len(c.calls);v.recenterMotion();assert len(c.calls)==n
  v.refreshCapabilities();settle(v);assert v.uncertain
  v.stopRumble();settle(v);assert c.calls[-1][0]=='rumble_stop'
  v.acknowledgeUncertain();assert not v.uncertain
 finally:v.shutdown()
def test_missing_capabilities_disables_not_fake_success():
 class Empty(Client):
  def request(self,op,**fields):self.calls.append((op,fields));return dict(schema_version=1,operations=['unsupported invented'])
 c=Empty();v=make(c)
 try:
  v.refreshCapabilities();settle(v);assert not v.available and not v.capabilities
  n=len(c.calls);v.calibrateMotion();assert len(c.calls)==n
 finally:v.shutdown()
def test_rejected_reply_never_claims_completion():
 class Invalid(Client):
  def request(self,op,**fields):
   return super().request(op,**fields) if op=='command_capabilities' else {'accepted':False}
 v=make(Invalid())
 try:
  v.refreshCapabilities();settle(v);v.testRumble(0.5,0.1,100);settle(v)
  assert v.uncertain and 'uncertain' in v.status
 finally:v.shutdown()

def test_profile_uncertainty_and_revision_conflict_no_replay():
 from controller_commands import ControllerProfileCommands
 from native_services.controller_command_protocol import PROFILE_OPERATIONS
 class Profiles:
  def __init__(self):self.calls=[]
  def request(self,op,**fields):
   self.calls.append((op,fields))
   if op=='profile_command_capabilities':return dict(schema_version=1,operations=list(PROFILE_OPERATIONS))
   if op=='profiles':return dict(revision='a'*64,names=['Default'],active_profile='Default')
   raise ControllerControlError('ERR_TIMEOUT',uncertain=True)
 c=Profiles();make() # initialize real Qt application
 v=ControllerProfileCommands(c)
 try:
  assert c.calls==[];v.refreshProfiles();settle(v);assert v.available
  v.createProfile('New');settle(v);assert v.uncertain
  n=len(c.calls);v.copyProfile('Default','Other');assert len(c.calls)==n
  v.refreshProfiles();settle(v);assert v.uncertain
  v.acknowledgeUncertain();assert not v.uncertain
 finally:v.shutdown()

def test_device_caps_and_uncertainty_are_explicit():
 from controller_commands import ControllerDeviceCommands
 from native_services.controller_command_protocol import DEVICE_OPERATIONS
 class Device:
  def __init__(self):self.calls=[]
  def request(self,op,**fields):
   self.calls.append((op,fields))
   if op=='device_command_capabilities':return dict(schema_version=1,operations=list(DEVICE_OPERATIONS),pairs=[],exclusive_capture=False)
   raise ControllerControlError('ERR_REPLY_LOST',uncertain=True)
 c=Device();make();v=ControllerDeviceCommands(c)
 try:
  v.pairJoyCons('left','right');assert not c.calls
  v.refreshDevices();settle(v);assert 'joycon_pair' in v.capabilities
  v.pairJoyCons('left','right');settle(v);assert v.uncertain
  n=len(c.calls);v.setExclusiveCapture(True);assert len(c.calls)==n
  v.refreshDevices();settle(v);assert v.uncertain
 finally:v.shutdown()

def test_profile_export_failure_clears_previous_document():
 from controller_commands import ControllerProfileCommands
 class Exporter:
  def __init__(self):self.fail=False
  def request(self,op,**fields):
   if op=='profile_command_capabilities':return dict(schema_version=1,operations=['profile_export'])
   if op=='profiles':return dict(revision='a'*64,names=['Default'],active_profile='Default')
   if self.fail:raise ControllerControlError('ERR_PROFILE_NOT_FOUND')
   return dict(name='Default',revision='a'*64,document={'version':1,'active_profile':'Default','profiles':{'Default':{}}})
 make();client=Exporter();v=ControllerProfileCommands(client)
 try:
  v.refreshProfiles();settle(v);v.exportProfile('Default');settle(v);assert v.exportDocument
  client.fail=True;v.exportProfile('Default');settle(v);assert v.exportDocument==''
 finally:v.shutdown()
