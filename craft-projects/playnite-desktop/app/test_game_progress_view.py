import os,threading
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
from PySide6.QtCore import QObject,QUrl,QMetaObject,Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlApplicationEngine,QQmlExpression,QQmlEngine
from PySide6.QtQuick import QQuickWindow
from game_progress_view import GameProgressView

_APP=None
def app():
 global _APP
 _APP=QGuiApplication.instance() or QGuiApplication([]);return _APP

def data(key):return dict(game_key=key,achievements=[],completion_times=[],notices=['cached only'],live_sync=False)
def settle(v):
 for _ in range(600):
  QTest.qWait(5)
  if not v.busy:return
 raise AssertionError('progress read thread leaked')

class Repo:
 def __init__(self):self.calls=[]
 def get_game(self,key):self.calls.append(key);return data(key)

def test_constructor_inert_async_explicit_refresh():
 app();r=Repo();v=GameProgressView(r)
 try:
  assert r.calls==[] and not v.busy
  v.selectGame('exact:key');settle(v)
  assert r.calls==['exact:key'] and v.gameKey=='exact:key' and 'no live' in v.status
  v.refresh();settle(v);assert r.calls==['exact:key','exact:key']
 finally:v.shutdown()

def test_latest_selection_wins_without_old_game_flash():
 app();started=threading.Event();release=threading.Event()
 class Slow(Repo):
  def get_game(self,key):
   self.calls.append(key)
   if key=='first':started.set();assert release.wait(3)
   result=data(key);result['notices']=[key];return result
 r=Slow();v=GameProgressView(r)
 try:
  v.selectGame('first')
  for _ in range(100):
   QTest.qWait(5)
   if started.is_set():break
  assert started.is_set()
  v.selectGame('middle');v.selectGame('last')
  assert v.notices==[] and v.gameKey=='last'
  release.set();settle(v)
  assert r.calls==['first','last'] and v.notices==['last']
  v.selectGame('');settle(v);assert v.achievementSources==[] and v.notices==[]
 finally:release.set();v.shutdown()

def test_failure_and_bad_identity_never_expose_private_exception_or_old_data():
 app()
 class Bad:
  def get_game(self,key):raise OSError('/private/account/token-secret')
 v=GameProgressView(Bad())
 try:
  v.selectGame('g');settle(v)
  assert 'token-secret' not in v.status and 'unavailable' in v.status and not v.achievementSources
 finally:v.shutdown()
 class Wrong:
  def get_game(self,key):return data('wrong-game')
 v=GameProgressView(Wrong())
 try:v.selectGame('g');settle(v);assert 'unavailable' in v.status
 finally:v.shutdown()

def test_actual_qml_panel_and_refresh_handler_no_urls(tmp_path):
 app()
 from native_services.game_progress_repository import GameProgressRepository
 b=dict(game_key='g',provider='steam',product_id='10',account_id='account-a')
 obs={**b,'kind':'imported_achievements','records':[{'achievement_id':'one','label':'<img src="https://never.invalid"> literal','unlocked':None}], 'source':'fixture cache','observed_at':'2020-01-01T00:00:00Z'}
 times={'kind':'completion_time','game_key':'g','schema':'waystone-completion-seconds-v1','source':'explicit fixture','values':{'main_story':3600}}
 v=GameProgressView(GameProgressRepository(bindings=[b],observations=[obs,times]))
 engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('progressService',v)
 warnings=[];engine.warnings.connect(lambda items:warnings.extend(x.toString() for x in items))
 here=Path(__file__).parent
 qml=b'''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
ApplicationWindow { width: 900; height: 800; visible: true; color: Theme.background
 ScrollView { anchors.fill: parent
 GameProgressPanel { width: 870; service: progressService; gameKey: "g" }
 }
}'''
 engine.loadData(qml,QUrl.fromLocalFile(str(here/'isolated-game-progress-fixture.qml')))
 try:
  assert engine.rootObjects(),warnings
  window=engine.rootObjects()[0];settle(v);QTest.qWait(80)
  assert not warnings,warnings
  stack=[window.contentItem()];visual=[]
  while stack:
   item=stack.pop();visual.append(item);stack.extend(item.childItems())
  texts=[x for x in visual if x.metaObject().indexOfProperty('text')>=0]
  literal=[x for x in texts if 'never.invalid' in str(x.property('text'))]
  assert literal, (v.achievementSources,[x.property('text') for x in texts])
  for item in literal:
   expression=QQmlExpression(QQmlEngine.contextForObject(item),item,'textFormat === 0')
   value=expression.evaluate()
   assert not expression.hasError() and (value[0] if isinstance(value,tuple) else value) is True
  assert any('3600 seconds' in str(x.property('text')) for x in texts)
  refresh=window.findChild(QObject,'progressRefresh');assert refresh and refresh.property('enabled')
  QMetaObject.invokeMethod(refresh,'clicked');settle(v);QTest.qWait(30);assert not warnings,warnings
  target=Path(os.environ.get('WAYSTONE_GAME_PROGRESS_SCREENSHOT',tmp_path/'panel.png'))
  assert window.grabWindow().save(str(target)) and target.stat().st_size>1000
  source=(here/'GameProgressPanel.qml').read_text()
  assert 'openUrlExternally' not in source and 'Image {' not in source and 'RichText' not in source
 finally:
  v.shutdown()
  for w in engine.rootObjects():w.close()
  engine.deleteLater();QTest.qWait(20)

def test_explicit_selected_copy_renders_without_live_backend(tmp_path):
 import pytest,json,hashlib
 configured=os.environ.get('WAYSTONE_PROGRESS_SELECTED_PLUGIN_DB')
 if not configured:pytest.skip('Only explicitly requested copied DB; no implicit discovery')
 from native_services.game_progress_repository import GameProgressRepository
 path=Path(configured).resolve();before=hashlib.sha256(path.read_bytes()).hexdigest()
 app();key='playnite:0558ce6d-33ac-4d6b-9038-d9cb5a83db0a'
 v=GameProgressView(GameProgressRepository(path));engine=QQmlApplicationEngine()
 engine.rootContext().setContextProperty('progressService',v)
 warnings=[];engine.warnings.connect(lambda items:warnings.extend(x.toString() for x in items))
 qml=('import QtQuick\nimport QtQuick.Controls\nimport "."\nApplicationWindow {width:900;height:900;visible:true;color:Theme.background; ScrollView {anchors.fill:parent;GameProgressPanel {width:870;service:progressService;gameKey:"'+key+'"}}}').encode()
 engine.loadData(qml,QUrl.fromLocalFile(str(Path(__file__).with_name('selected-copy-progress-fixture.qml'))))
 try:
  assert engine.rootObjects(),warnings
  settle(v);QTest.qWait(80)
  assert not warnings,warnings
  assert v.completionTimes and any(r['values'] for r in v.completionTimes)
  assert v.achievementSources and all(r['status']=='empty' and r['completion_percent'] is None for r in v.achievementSources)
  output=os.environ.get('WAYSTONE_PROGRESS_SELECTED_SCREENSHOT')
  if output:assert engine.rootObjects()[0].grabWindow().save(output)
  assert before==hashlib.sha256(path.read_bytes()).hexdigest()
 finally:
  v.shutdown()
  for w in engine.rootObjects():w.close()
  engine.deleteLater();QTest.qWait(20)

def test_verified_hltb_classic_schema_renders_hours_but_not_zero_or_malformed(tmp_path):
 import sqlite3,json,hashlib
 from native_services.game_progress_repository import GameProgressRepository,HLTB_CLASSIC_SECONDS_SCHEMA
 from native_services.plugin_metadata import APPLICATION_ID
 app();path=tmp_path/'verified-plugin.sqlite'
 db=sqlite3.connect(path);db.executescript(f'PRAGMA application_id={APPLICATION_ID}; CREATE TABLE records(source_path TEXT PRIMARY KEY,dataset TEXT,plugin_id TEXT,source_game_id TEXT,canonical_id TEXT,sha256 TEXT,source_bytes INTEGER,source_stat_json TEXT,raw BLOB);')
 raw=json.dumps({'Items':[{'Id':'42','IsVndb':False,'GameHltbData':{'MainStoryClassic':3600,'MainExtraClassic':0,'CompletionistClassic':'bad'}}]}).encode()
 db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)',('selected','HowLongToBeat','plugin','source','g',hashlib.sha256(raw).hexdigest(),len(raw),'{}',raw));db.commit();db.close()
 v=GameProgressView(GameProgressRepository(path,hltb_schema=HLTB_CLASSIC_SECONDS_SCHEMA));engine=QQmlApplicationEngine()
 engine.rootContext().setContextProperty('progressService',v);warnings=[]
 engine.warnings.connect(lambda items:warnings.extend(x.toString() for x in items))
 qml=b'import QtQuick\nimport QtQuick.Controls\nimport "."\nApplicationWindow {width:900;height:700;visible:true;color:Theme.background;GameProgressPanel {anchors.fill:parent;service:progressService;gameKey:"g"}}'
 engine.loadData(qml,QUrl.fromLocalFile(str(Path(__file__).with_name('verified-hltb-panel-fixture.qml'))))
 try:
  assert engine.rootObjects(),warnings
  settle(v);QTest.qWait(80)
  window=engine.rootObjects()[0]
  stack=[window.contentItem()];texts=[]
  while stack:
   item=stack.pop();stack.extend(item.childItems())
   if item.metaObject().indexOfProperty('text')>=0:texts.append(str(item.property('text')))
  joined='\n'.join(texts)
  assert 'Main story: 1.0 h (3600 seconds)' in joined
  assert 'Main + extras: unavailable (source value 0)' in joined and 'Completionist: invalid cached value' in joined
  assert '0.0 h' not in joined and not warnings,warnings
  assert v.completionTimes[0]['schema_provenance']['generation_equality_verified'] is False
  destination=os.environ.get('WAYSTONE_VERIFIED_HLTB_SCREENSHOT')
  if destination:assert engine.rootObjects()[0].grabWindow().save(destination)
 finally:
  v.shutdown()
  for w in engine.rootObjects():w.close()
  engine.deleteLater();QTest.qWait(20)
