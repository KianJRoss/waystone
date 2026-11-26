"""No live accounts/provider processes; actual Qt task and managed install wiring."""
from pathlib import Path
import asyncio,json,sys
from unittest.mock import patch
import pytest
from store_accounts_view import StoreAccountsView,EPIC_REDIRECT
from test_game_install_controller import app,wait,close_session
from test_provider_job_view import Manager

class Account:
    def __init__(self,root):
        self.binary_path=Path(sys.executable);self.config_root=root;self.calls=[];self.block=False
    def read_saved_account(self):
        self.calls.append('read-saved');return {'status':'saved','account_id':'fixture-account','display_name':'Fixture'}
    async def authenticate(self,code):
        self.calls.append(('authenticate',code))
        if self.block:await asyncio.sleep(100)
        return {'status':'connected','account_id':'fixture-account','display_name':'Fixture'}
    async def refresh_library(self):
        self.calls.append('refresh');return [{'app_name':'actual-product','app_title':'Fixture Epic game'}]
    async def logout(self):
        self.calls.append('logout');return {'status':'disconnected'}

@pytest.mark.parametrize('url,body',[
    ('https://attacker.test/id/api/redirect?clientId=x','{}'),
    (EPIC_REDIRECT.replace('https://','http://'),'{"authorizationCode":"fixture"}'),
    (EPIC_REDIRECT.replace('www.epicgames.com','www.epicgames.com.attacker.test'),'{"authorizationCode":"fixture"}'),
    (EPIC_REDIRECT.replace('clientId=','wrong='),'{"authorizationCode":"fixture"}'),
    (EPIC_REDIRECT,'{"authorizationCode":[]}'),
    (EPIC_REDIRECT,'{"authorizationCode":"line\\nbreak"}'),
])
def test_wrong_callback_never_authenticates(tmp_path,url,body):
    application=app();service=Account(tmp_path/'scope');v=StoreAccountsView(service)
    try:
        assert service.calls==[] and v._worker is None
        assert v.beginEpicSignIn() and not service.calls
        assert not v.acceptEpicPage(url,body) and not service.calls and not v.busy
    finally:v.shutdown()

def test_auth_and_refresh_actual_qt_worker_no_secret_in_display(tmp_path):
    application=app();service=Account(tmp_path/'scope');v=StoreAccountsView(service)
    try:
        assert not v.acceptEpicPage(EPIC_REDIRECT,'{"authorizationCode":"fixture-code"}')
        assert v.beginEpicSignIn() and v.acceptEpicPage(EPIC_REDIRECT,'{"authorizationCode":"fixture-code"}')
        assert wait(lambda:not v.busy) and v.epicAccount['status']=='connected'
        assert 'fixture-code' not in v.message and not v.signingIn
        assert v.refreshEpic() and wait(lambda:not v.busy) and v.gameCount==1
        assert v.signOutEpic() and wait(lambda:not v.busy)
        assert v.epicAccount['status']=='disconnected' and v.gameCount==0
    finally:v.shutdown()
    assert v._worker is None

def test_shutdown_cancels_own_worker(tmp_path):
    application=app();service=Account(tmp_path/'scope');service.block=True;v=StoreAccountsView(service)
    assert v.beginEpicSignIn() and v.acceptEpicPage(EPIC_REDIRECT,'{"authorizationCode":"fixture-code"}')
    assert wait(lambda:bool(service.calls));v.shutdown()
    assert not v.busy and v._worker is None and not v.beginEpicSignIn()

class PlanManager(Manager):
    def set_tool_config(self,provider,config):self.config=config;super().set_tool_config(provider,config)
    def create_plan(self,recipe):self.plan=super().create_plan(recipe);return self.plan

def test_session_install_uses_own_account_scope_not_heroic(tmp_path):
    from app import create_session
    application=app();service=Account(tmp_path/'waystone-owned/epic/legendary');manager=PlanManager()
    games=[{'id':'epic-game','title':'Fixture Epic game','variants':[{
        'source':'Epic','provider_game_id':'actual-product','platform':'Windows',
        'status':'not-installed','recipe':[],'installation_id':'fixture-epic'}]}]
    with patch('library_model.load_fixture',return_value=games):
        engine,model,root,warnings=create_session(application,headless=True,provider_manager=manager,epic_account_service=service)
    try:
        view=engine.provider_jobs;view._root=str(tmp_path/'games')
        assert engine.game_installs.install('epic-game') and wait(lambda:not view.busy)
        assert manager.config.binary_path==Path(sys.executable)
        assert manager.config.env_overrides['LEGENDARY_CONFIG_PATH']==str(service.config_root)
        assert 'heroic' not in str(manager.config.env_overrides).casefold()
        assert root.property('activePage')=='library' and len(manager.executed)==1
        assert engine.store_accounts.refreshEpic() and wait(lambda:not engine.store_accounts.busy)
        choice=next(r for r in view._choices if r['key']=='epic:actual-product')
        assert choice['game_ids']==['epic-game']
        # Logout keeps routing to Waystone scope, never falls back to another client.
        assert engine.store_accounts.signOutEpic() and wait(lambda:not engine.store_accounts.busy)
        assert view.managedBinary('epic')==sys.executable
    finally:
        engine.store_accounts.shutdown();close_session(application,engine,root)


class NileAccount:
    def __init__(self,root):
        self.binary_path=Path(sys.executable);self.config_root=root;self.calls=[]
    def read_saved_account(self):
        return {"provider":"amazon","account_id":"","display_name":"","status":"disconnected"}
    async def begin_login(self):
        self.calls.append("begin")
        return {"url":"https://www.amazon.com/ap/signin?fixture=1"}
    async def finish_login(self,url):
        self.calls.append(("finish",url))
        return {"provider":"amazon","account_id":"amazon-fixture","display_name":"Kian","status":"connected"}
    async def refresh_library(self):
        self.calls.append("refresh")
        return [{"product_id":"amazon-product","title":"Fixture Amazon Game","is_installed":False}]
    async def logout(self):
        self.calls.append("logout")
        return {"provider":"amazon","account_id":"","display_name":"","status":"disconnected"}
    def cancel_login(self):pass

def test_amazon_signin_syncs_library_and_install_uses_waystone_nile(tmp_path):
    from app import create_session
    application=app();nile=NileAccount(tmp_path/"providers-v1/amazon");manager=PlanManager()
    with patch("library_model.load_fixture",return_value=[]):
        engine,model,root,warnings=create_session(application,headless=True,nile_account_service=nile,
                                                   provider_manager=manager)
    try:
        accounts=engine.store_accounts; jobs=engine.provider_jobs
        assert accounts.beginAmazonSignIn() and wait(lambda:not accounts.busy)
        assert accounts.amazonLoginUrl=="https://www.amazon.com/ap/signin?fixture=1"
        callback="https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=fixture-code"
        assert accounts.acceptAmazonCallback(callback) and wait(lambda:not accounts.busy)
        assert accounts.amazonAccount["status"]=="connected"
        assert jobs._choices and any(r["key"]=="amazon:amazon-product" for r in jobs._choices)
        assert "1 games synced" in accounts.amazonMessage
        assert "refresh" in nile.calls
        assert jobs.managedBinary("amazon")==sys.executable
        assert jobs.prepare("amazon","amazon-product","Fixture Amazon Game",str(tmp_path),
                            "install",sys.executable,"")
        assert jobs._plan is not None
        assert manager.config.env_overrides["NILE_CONFIG_PATH"]==str(nile.config_root)
        assert jobs._plan.recipe.provider=="amazon" and jobs._plan.recipe.product_id=="amazon-product"
        assert accounts.signOutAmazon() and wait(lambda:not accounts.busy)
        assert accounts.amazonAccount["status"]=="disconnected"
    finally:
        engine.store_accounts.shutdown();close_session(application,engine,root)


class GogAccount:
    def __init__(self,root):self.binary_path=Path(sys.executable);self.config_root=root;self.calls=[];self.pending=False
    def read_saved_account(self):return {"provider":"gog","account_id":"gog-user","display_name":"GOG account","status":"connected"}
    def load_cached_library(self):return []
    def begin_login(self):self.pending=True;return "https://auth.gog.com/auth?state=fixture"
    def consume_callback(self,url):
        self.calls.append(("callback",url))
        if self.pending and url=="https://embed.gog.com/on_login_success?origin=client&state=fixture&code=one-time":
            self.pending=False;return "one-time"
        return None
    async def authenticate(self,code):self.calls.append(("authenticate",code));return self.read_saved_account()
    async def refresh_library(self):self.calls.append("refresh");return [{"product_id":"12345","title":"Fixture GOG game"}]
    def cancel_login(self):self.pending=False
    def logout(self):self.calls.append("logout");return {"provider":"gog","account_id":"","display_name":"","status":"disconnected"}

class GogDownloads:
    def __init__(self):self.accounts={};self.libraries={};self.busy=False;self.finished=type("SignalStub",(),{"connect":lambda self,*a:None})()
    def configureManagedAccount(self,provider,binary,root):self.accounts[provider]=(binary,root);return True
    def addManagedGogLibrary(self,rows):self.libraries["gog"]=rows
    def clearManagedAccount(self,provider):return True

def test_store_account_qml_compiles_amazon_and_gog_signin(tmp_path):
    from PySide6.QtQml import QQmlEngine,QQmlComponent
    from PySide6.QtCore import QUrl
    application=app();service=NileAccount(tmp_path/"amazon");view=StoreAccountsView(None,None,service)
    engine=QQmlEngine();engine.addImportPath(str(Path(__file__).parent))
    engine.rootContext().setContextProperty("storeAccounts",view)
    gog=GogAccount(tmp_path/"gog");gog_view=StoreAccountsView(None,None,None,gog)
    engine.rootContext().setContextProperty("storeAccounts",gog_view)
    for name in ("NileSignInPane.qml","GOGSignInPane.qml","StoreAccountsPanel.qml"):
        component=QQmlComponent(engine,QUrl.fromLocalFile(str(Path(__file__).with_name(name))))
        assert component.status()==QQmlComponent.Status.Ready, [e.toString() for e in component.errors()]
    view.shutdown();gog_view.shutdown()


def test_gog_install_plan_uses_waystone_auth_and_engine_scope(tmp_path):
    from provider_job_view import ProviderJobView
    application=app();manager=PlanManager();view=ProviderJobView(manager)
    binary=str(tmp_path/"bin/gogdl");root=str(tmp_path/"providers-v1/gog")
    try:
        assert view.configureManagedAccount("gog",binary,root)
        assert view.prepare("gog","12345","Fixture GOG game",str(tmp_path/"games"),"install",binary,"")
        assert manager.config.binary_path==Path(binary)
        assert manager.config.env_overrides["GOGDL_CONFIG_PATH"]==root
        assert manager.plan.recipe.auth_config_path==Path(root)/"auth.json"
        assert manager.plan.recipe.product_id=="12345"
        assert manager.executed==[] and view._worker is None
    finally:view.shutdown()


def test_gog_signin_sync_and_logout_use_waystone_download_scope(tmp_path):
    from store_accounts_view import StoreAccountsView
    application=app();service=GogAccount(tmp_path/"providers-v1/gog");downloads=GogDownloads()
    view=StoreAccountsView(None,downloads,None,service)
    try:
        view.readGogSaved();assert view.gogAccount["status"]=="connected"
        assert view.beginGogSignIn() and view.gogSigningIn
        assert not view.acceptGogCallback("https://attacker.test/callback?code=x")
        assert view.acceptGogCallback("https://embed.gog.com/on_login_success?origin=client&state=fixture&code=one-time")
        assert wait(lambda:not view.busy)
        assert view.gogGameCount==1 and downloads.accounts["gog"]==(str(Path(sys.executable)),str(service.config_root))
        assert downloads.libraries["gog"][0]["product_id"]=="12345"
        assert "one-time" not in view.gogMessage and "secret" not in repr(view.gogAccount).casefold()
        assert view.signOutGog() and wait(lambda:not view.busy)
        assert view.gogAccount["status"]=="disconnected" and downloads.accounts["gog"]
    finally:view.shutdown()


def test_xodus_hosted_signin_component_compiles_and_missing_backend_fails_closed(tmp_path):
    from xodus_auth_service import XodusAuthService
    from PySide6.QtQml import QQmlEngine, QQmlComponent
    from PySide6.QtCore import QUrl
    application=app()
    auth=XodusAuthService(tmp_path/"missing-xodus", parent=application)
    downloads=GogDownloads()
    view=StoreAccountsView(None,downloads,None,None,auth)
    engine=QQmlEngine();engine.addImportPath(str(Path(__file__).parent))
    engine.rootContext().setContextProperty("storeAccounts",view)
    try:
        assert view.xboxAvailable and not view.xboxSigningIn
        downloads.busy=True
        assert not view.beginXboxSignIn() and not view.xboxSigningIn
        assert "Wait for the current provider operation" in view.xboxMessage
        downloads.busy=False
        assert not view.beginXboxSignIn() and not view.xboxSigningIn
        assert "backend is unavailable" in view.xboxMessage
        for name in ("XodusSignInPane.qml", "StoreAccountsPanel.qml"):
            component=QQmlComponent(engine,QUrl.fromLocalFile(str(Path(__file__).with_name(name))))
            assert component.status()==QQmlComponent.Status.Ready, [e.toString() for e in component.errors()]
        pane=QQmlComponent(engine,QUrl.fromLocalFile(str(Path(__file__).with_name("XodusSignInPane.qml"))))
        browser=pane.create()
        assert browser is not None, [e.toString() for e in pane.errors()]
        browser.deleteLater()
    finally:
        view.shutdown();auth.shutdown();engine.deleteLater()


def test_xodus_stdio_bridge_handshake_and_payload_without_network(tmp_path):
    from xodus_auth_service import XodusAuthService
    from PySide6.QtWebEngineQuick import QQuickWebEngineProfile
    application=app()
    script=tmp_path/"xodus-fixture"
    script.write_text("""#!/usr/bin/env python3
import json,sys
p="NEXUS_AUTH_V1 "
def emit(x): print(p+json.dumps(x,separators=(",",":")),flush=True)
emit({"type":"ready","version":1})
hello=json.loads(sys.stdin.readline())
if hello!={"type":"hello","version":1}: raise SystemExit(10)
emit({"type":"open","session":1,"url":"https://login.live.com/","headers":{"HostApp":"CloudExperienceHost"}})
event=json.loads(sys.stdin.readline())
if event.get("type")!="ipc" or event.get("session")!=1 or event.get("payload")!=json.dumps({"fixture":True},separators=(",",":")): raise SystemExit(11)
emit({"type":"close","session":1})
""")
    script.chmod(0o700)
    service=XodusAuthService(script)
    profile=QQuickWebEngineProfile()
    received=[]
    service.pageRequested.connect(lambda sid,url: received.append((sid,url)) or service.sendPayload(sid,'{"fixture":true}'))
    try:
        assert service.attachProfile(profile)
        assert service.beginLogin()
        assert wait(lambda:not service.running)
        assert received==[(1,"https://login.live.com/")], (service.message, received)
        assert service.message=="Signed in with Xbox through Waystone and Xodus."
        service.detachProfile(profile)
        second_profile=QQuickWebEngineProfile()
        assert service.attachProfile(second_profile)
        service.detachProfile(second_profile)
        second_profile.deleteLater()
    finally:
        service.shutdown();profile.deleteLater()
