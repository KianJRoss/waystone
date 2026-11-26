"""Full native Waystone popup geometry probe, offscreen and inert backend."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];APP=ROOT/'craft-projects/playnite-desktop/app'
sys.path[:0]=[str(APP),str(ROOT)]
from PySide6.QtCore import QObject,Signal,Property,Slot,QRectF,QPointF,Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from app import create_session
class InertHost(QObject):
 ready=Signal();changed=Signal()
 @Property(bool,notify=changed)
 def running(self):return False
 @Property(bool,notify=changed)
 def available(self):return False
 @Property(str,notify=changed)
 def status(self):return 'Geometry test: no engine or devices started.'
 @Slot()
 def start(self):pass
 @Slot()
 def shutdown(self):pass
app=QGuiApplication([]);host=InertHost()
engine,model,root,warnings=create_session(app,headless=True,studio_host=host,native_controller_ui=True)
rows=[]
try:
 for width,height in [(960,720),(1080,960),(1440,900),(1920,1080)]:
  root.resize(width,height);root.setVisible(True);root.openControllers();QTest.qWait(80)
  native=root.findChild(QQuickItem,'nativeControllerStudio');assert native
  checks=[]
  for item in native.findChildren(QQuickItem):
   cls=item.metaObject().className()
   if not any(cls.startswith(x) for x in ('ControllerSurface_','KeyboardSurface_','MouseSurface_')):continue
   children=item.childItems()
   # Each diagram has one logical coordinate container; exclude other direct children.
   for child in children:
    if child.width()<100 or child.height()<100:continue
    bounds=child.mapRectToItem(item,QRectF(0,0,child.width(),child.height()))
    checks.append({'class':cls.split('_')[0],'viewport':[item.width(),item.height()], 'visual':[bounds.x(),bounds.y(),bounds.width(),bounds.height()], 'contained':bounds.x()>=-1 and bounds.y()>=-1 and bounds.right()<=item.width()+1 and bounds.bottom()<=item.height()+1})
  ctrl = next((x for x in native.findChildren(QQuickItem) if x.metaObject().className().startswith('ControllerSurface_')),None)
  hit_ok=False
  if ctrl:
   logical=next((x for x in ctrl.childItems() if abs(x.width()-520)<.1 and abs(x.height()-360)<.1),None)
   if logical:
    pos=logical.mapToItem(root.contentItem(),QPointF(380,188)).toPoint()
    native.setProperty('selectedSource','')
    QTest.mouseClick(root,Qt.LeftButton,Qt.NoModifier,pos);QTest.qWait(20)
    hit_ok=native.property('selectedSource')=='Controller:A'
  rows.append({'hit_alignment':hit_ok,'size' :[width,height],'checks':checks,'passed':hit_ok and bool(checks) and all(c['contained'] for c in checks)})
 print(json.dumps({'measurements':rows,'all_passed':all(r['passed'] for r in rows)},indent=2))
finally:
 root.setProperty('quitRequested',True);root.close();engine.voice_media.shutdown();engine.social_view.shutdown();engine.deleteLater();QTest.qWait(30)
