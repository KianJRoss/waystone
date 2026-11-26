"""Actual Main.qml + native engine fixture + IPC; no hardware/user-data access."""
import json,os,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];APP=ROOT/'craft-projects/playnite-desktop/app'
sys.path[:0]=[str(APP),str(ROOT)]
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from app import create_session
from controller_engine_host import ControllerEngineHost
from native_services.unified_controller_control import ControllerControlClient

def wait(fn):
 for _ in range(300):
  QTest.qWait(20)
  if fn():return
 raise AssertionError('Timeout')
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);data=p/'data';data.mkdir();runtime=p/'rt';runtime.mkdir(mode=0o700)
 os.environ['XDG_RUNTIME_DIR']=str(runtime);os.environ['XDG_DATA_HOME']=str(p/'app-data')
 profile={'routes':{'Keyboard:W':'Controller:A'},'custom_kept':17}
 (data/'profiles.json').write_text(json.dumps({'version':1,'active_profile':'Default','profiles':{'Default':profile}}))
 app=QGuiApplication([])
 host=ControllerEngineHost(ROOT/'components/controller_studio/headless/publish/UnifiedController.Headless',data,fixture=True)
 client=ControllerControlClient(socket_path=runtime/'waystone-unified-controller/control.sock')
 engine,model,root,warnings=create_session(app,headless=True,controller_client=client,studio_host=host,native_controller_ui=True)
 try:
  root.setVisible(True);root.openControllers()
  view=engine.unified_controller
  live=engine.controller_live
  wait(lambda:host.available and view.available and not view.busy)
  wait(lambda:live.available)
  assert live.frame.get('schema_version')==1
  assert set(live.frame['input'])=={'buttons','left_x','left_y','right_x','right_y','left_trigger','right_trigger'}
  native=root.findChild(QObject,'nativeControllerStudio');assert native
  native.setProperty('selectedSource','Controller:X');native.setProperty('selectedDestination','Controller:B')
  native.saveRemap();wait(lambda:not view.busy and 'Controller:X' in json.loads(view.profileJson).get('routes',{}))
  saved=json.loads((data/'profiles.json').read_text())['profiles']['Default']
  assert saved['routes']['Controller:X']=='Controller:B' and saved['custom_kept']==17
  pid=host._process.processId();root.showLibraryView();QTest.qWait(20);assert host.running
  root.openControllers();QTest.qWait(30);assert host._process.processId()==pid
  assert not warnings,warnings
  live.stop();assert not live.available and not live.frame
  host.shutdown();wait(lambda:not host.running)
  assert not (runtime/'waystone-unified-controller/control.sock').exists()
  print(json.dumps({'live_polling_connected_to_actual_native_fixture':True,'actual_main_qml':True,'native_popup':True,'headless_engine_fixture':True,'real_ipc_save_and_readback':True,'unknown_fields_preserved':True,'same_runtime_reopened':True,'clean_shutdown':True,'warnings':warnings}))
 finally:
  live.shutdown();host.shutdown();view.shutdown();root.setProperty('quitRequested',True);root.close()
  engine.voice_media.shutdown();engine.social_view.shutdown()
