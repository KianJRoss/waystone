import os,json
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
from PySide6.QtCore import QObject,QUrl,QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from platform_social_view import PlatformSocialView
from app import create_session
_APP=None
OWNER='76561197968575517'
def app():
 global _APP
 _APP=QGuiApplication.instance() or QGuiApplication([]);return _APP

def settle(v):
 for _ in range(1000):
  QTest.qWait(5)
  if not v.busy:return
 raise AssertionError('Social thread leaked')
class Native:
 def __init__(self):self.calls=0
 def fetch_friends(self):
  self.calls+=1;return {'provider':'steam','account_id':OWNER,'status':'live','friends':[{'friend_id':'76561197968575518','display_name':'<b>Steam friend</b>','online':True,'presence':'Online'}]}
class Web(Native):
 def fetch_achievements(self,key,product):
  return {'game_key':key,'account_id':OWNER,'provider':'steam','product_id':product,'items':[{'label':'First achievement','unlocked':True}],'unlocked_count':1,'total_count':2}

def test_inert_connect_native_and_web_real_projection_no_login_reads():
 app();n=Native();v=PlatformSocialView([{'id':'g','variants':[{'source':'Steam','provider_game_id':'440','source_id':'not-the-product'}]}],native=n,web_factory=lambda owner,key:Web())
 try:
  assert n.calls==0 and not v.steamConnected
  v.connectSteam();settle(v);assert v.steamConnected and v.steamAccount==OWNER and n.calls==1
  assert v.friends[0]['provider']=='steam' and v.friends[0]['account_id']==OWNER
  v.selectGame('g');assert v.steamProducts==['440'] and not v.achievements
  v.syncAchievements('440');assert 'Web API key' in v.progressStatus
  v.connectSteamWeb(OWNER,'A'*32);settle(v);assert v.achievementAccess
  v.syncAchievements('440');settle(v);assert v.achievements[0]['unlocked'] is True and v.progress['product_id']=='440'
  v.selectGame('other');assert not v.achievements and not v.steamProducts
  v.disconnectSteam();assert not v.steamConnected and not v.achievementAccess
 finally:v.shutdown()

def test_actual_social_qml_and_account_controls_produce_qualified_steam_rows():
 app();native=Native();v=PlatformSocialView(native=native);engine,model,root,warnings=create_session(app(),headless=True,platform_social_view=v)
 try:
  root.show();QMetaObject.invokeMethod(root.findChild(QObject,'railFriends'),'click');QTest.qWait(40)
  panel=root.findChild(QObject,'socialPanel');assert panel
  panel.setProperty('selectedTab',3);QTest.qWait(30)
  connect=root.findChild(QObject,'connectNativeSteam');assert connect
  QMetaObject.invokeMethod(connect,'click');settle(v);QTest.qWait(40)
  assert v.steamConnected and len(v.friends)==1
  panel.setProperty('selectedTab',0);panel.setProperty('friendPlatform','steam');QTest.qWait(50)
  # Existing Xbox-only voice actions are never supplied Steam's qualified identity.
  rows=panel.property('shownFriends').toVariant();assert len(rows)==1 and rows[0]['provider']=='steam'
  assert rows[0]['personId']=='steam:'+OWNER+':76561197968575518'
  assert not engine.social_view.connected and not warnings
 finally:
  root.close();v.shutdown();engine.organizer_workspace_view.shutdown();engine.organizer_agent_view.shutdown();engine.social_view.shutdown();engine.voice_media.shutdown();engine.deleteLater();QTest.qWait(30)

def test_source_game_identity_required_never_title_or_guid_inferred():
 app();v=PlatformSocialView([{'id':'g','title':'Team Fortress 2','variants':[{'source':'Steam','source_id':'550e8400-e29b-41d4-a716-446655440000'}]}],native=Native())
 try:v.selectGame('g');assert not v.steamProducts
 finally:v.shutdown()

def test_real_qml_qr_render_and_session_login_without_api_key(tmp_path):
 class Qr:
  challenge_url='https://s.team/q/1/test-fixture'
  def begin(self):return {'challenge_url':self.challenge_url,'interval':5}
  def poll(self):
   adapter=Web();adapter.account_id=OWNER;return adapter
  def cancel(self):self.challenge_url=''
 app();v=PlatformSocialView([{'id':'g','title':'Game','variants':[{'source':'Steam','provider_game_id':'440'}]}],native=Native(),qr_session=Qr(),cache_path=tmp_path/'cache.sqlite')
 engine,model,root,warnings=create_session(app(),headless=True,platform_social_view=v)
 try:
  root.show();QMetaObject.invokeMethod(root,'openFriendsPlatforms');QTest.qWait(60)  # sign-ins live in Settings > Accounts
  QMetaObject.invokeMethod(root.findChild(QObject,'steamQrSignIn'),'click');settle(v);QTest.qWait(80)
  image=root.findChild(QObject,'steamSignInQrImage');assert v.qrActive and image.property('visible')
  assert 's.team' not in image.property('source').toString()  # no challenge in logged image URLs
  v.pollSteamSignIn();settle(v);assert v.steamConnected and v.achievementAccess and not v.qrActive
  v.selectGame('g');v.syncAchievements('440');settle(v)
  assert v.progress['unlocked_count']==1 and len(v.achievementAccounts)==1
  fresh=PlatformSocialView([{'id':'g','variants':[{'source':'Steam','provider_game_id':'440'}]}],cache_path=tmp_path/'cache.sqlite')
  try:
   fresh.loadProgressCache();settle(fresh);fresh.selectGame('g')
   assert fresh.achievements and 'Cached' in fresh.progressStatus and not fresh.steamConnected
  finally:fresh.shutdown()
  assert not warnings
 finally:
  root.close();v.shutdown();engine.organizer_workspace_view.shutdown();engine.organizer_agent_view.shutdown();engine.social_view.shutdown();engine.voice_media.shutdown();engine.deleteLater();QTest.qWait(25)

def test_cancel_before_qr_response_blocks_late_linking():
 import threading
 started=threading.Event();release=threading.Event()
 class Qr:
  def begin(self):started.set();assert release.wait(2);return {'challenge_url':'https://s.team/q/1/cancelled','interval':5}
  def cancel(self):pass
 app();v=PlatformSocialView(qr_session=Qr())
 try:
  v.beginSteamSignIn()
  for _ in range(100):
   QTest.qWait(2)
   if started.is_set():break
  assert started.is_set();v.cancelSteamSignIn();release.set();settle(v)
  assert not v.qrActive and v.qrUrl=='' and not v.steamConnected
 finally:release.set();v.shutdown()

def test_xbox_provider_view_keeps_account_and_product_separate(tmp_path):
 class Xbox:
  def discover(self,games):return {'account_id':'xbox-owner','games':[{'game_key':'g','title':'Game','provider':'xbox','products':['123']}]}
  def fetch_achievements(self,key,product):return {'game_key':key,'provider':'xbox','account_id':'xbox-owner','product_id':product,'source':'fixture','status':'live','items':[{'label':'Xbox trophy','unlocked':True}],'unlocked_count':1,'total_count':1}
 app();v=PlatformSocialView([{'id':'g','variants':[]}],xbox_adapter=Xbox(),cache_path=tmp_path/'cache.sqlite')
 try:
  v.connectXboxProgress();settle(v);v.setProgressProvider('xbox');v.selectGame('g')
  assert v.progressProducts==['123'] and not v.steamConnected
  v.syncAchievements('123');settle(v)
  assert v.progress['provider']=='xbox' and v.progress['account_id']=='xbox-owner' and v.achievements[0]['unlocked']
  assert v.achievementAccounts[0]['provider']=='xbox'
 finally:v.shutdown()
