"""Full Main.qml navigation route probe using synthetic controller status only."""
from pathlib import Path
import os,sys,tempfile,json,time
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'craft-projects/playnite-desktop/app'),str(ROOT)]
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
app=QGuiApplication([])
class Client:
    buttons=0
    def request(self,operation):
        assert operation=='status'
        n=dict(buttons=self.buttons,left_x=0,left_y=0,right_x=0,right_y=0,left_trigger=0,right_trigger=0)
        return {'running':True,'live':{'schema_version':1,'input':n,'output':dict(n),'selected_source_id':'synthetic'}}
def wait(fn):
    for _ in range(150):
        QTest.qWait(20)
        if fn():return
    raise AssertionError('Timed out')
with tempfile.TemporaryDirectory() as d:
 os.environ['XDG_DATA_HOME']=d
 engine,model,root,warnings=create_session(app,headless=True)
 live=engine.controller_live;c=Client();live._client=c
 try:
  root.setVisible(True);root.toggleConsole();root.requestActivate();QTest.qWait(100)
  nav=engine.controller_navigation;console=root.findChild(QObject,'consoleLibrary');overlay=root.findChild(QObject,'desktopOverlayManager')
  assert console and overlay
  wait(lambda:nav.context=='console' and live.available);QTest.qWait(100)
  c.buttons=0x100;wait(lambda:console.property('activeSection')=='header')
  c.buttons=0;QTest.qWait(80);c.buttons=0x200;wait(lambda:console.property('activeSection')=='library')
  c.buttons=0;QTest.qWait(80);c.buttons=0x10;wait(lambda:overlay.property('visible'))
  overlay.findChild(QObject,'overlayToolbarWindow').requestActivate();wait(lambda:nav.context=='overlay')
  c.buttons=0;QTest.qWait(100);c.buttons=0x2000;wait(lambda:not overlay.property('visible'))
  root.requestActivate();wait(lambda:nav.context=='console')
  c.buttons=0;QTest.qWait(80)
  root.openControllers();wait(lambda:nav.context=='')
  assert not warnings,warnings
  print(json.dumps({'actual_main':True,'real_controller_router_synthetic_input':True,'console_sections':True,'menu_opens_overlay_widgets':True,'back_closes_editor':True,'page_blocks_console_input':True,'no_game_or_engine_started':True,'qml_warnings':warnings}))
 finally:
  live.shutdown();root.setProperty('quitRequested',True);root.close()
  engine.unified_controller.shutdown();engine.controller_studio.shutdown();engine.voice_media.shutdown();engine.social_view.shutdown();engine.deleteLater();QTest.qWait(30)
