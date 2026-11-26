"""Waystone-owned store sign-in; no launcher window or ambient network calls."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit, parse_qs, urlencode
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread

EPIC_CLIENT='34a02cf8f4414e29b15921876da36f9a'
EPIC_REDIRECT='https://www.epicgames.com/id/api/redirect?'+urlencode({'clientId':EPIC_CLIENT,'responseType':'code'})
EPIC_LOGIN='https://www.epicgames.com/id/login?'+urlencode({'redirectUrl':EPIC_REDIRECT})

class _AccountJob(QThread):
    result=Signal(dict)
    def __init__(self,factory,parent=None):
        super().__init__(parent);self._factory=factory;self._loop=None;self._task=None;self._cancel=threading.Event()
    def run(self):
        async def work():
            self._loop=asyncio.get_running_loop();self._task=asyncio.current_task()
            try:
                if self._cancel.is_set():return
                value=await self._factory()
                if not self._cancel.is_set():self.result.emit(value)
            except asyncio.CancelledError:pass
            except Exception:
                if not self._cancel.is_set():self.result.emit({'ok':False})
            finally:self._factory=None;self._task=None;self._loop=None
        asyncio.run(work())
    def cancel(self):
        self._cancel.set();loop=self._loop;task=self._task
        if loop is not None and task is not None:
            try:loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:pass

class StoreAccountsView(QObject):
    changed=Signal()
    def __init__(self,service=None,downloads=None,nile_service=None,gog_service=None,xodus_auth_service=None,parent=None):
        super().__init__(parent);self._service=service;self._downloads=downloads;self._nile_service=nile_service;self._gog_service=gog_service;self._xodus_auth_service=xodus_auth_service
        self._account={};self._games=[];self._busy=False;self._signin=False;self._amazon_signin=False;self._amazon_account={};self._amazon_login_url='';self._amazon_message='Amazon is not connected to Waystone.';self._gog_account={};self._gog_games=[];self._gog_signin=False;self._gog_login_url='';self._gog_message='GOG is not connected to Waystone.';self._xbox_message='Connect your Xbox account through Waystone and Xodus.'
        self._message='Sign in here. Waystone uses the store engine directly; Heroic does not need to run.'
        self._worker=None;self._closed=False
        if downloads is not None:downloads.finished.connect(self._bind_saved)
        if xodus_auth_service is not None:xodus_auth_service.finished.connect(self._completeXboxSignIn)
    @Property(bool,constant=True)
    def available(self):return self._service is not None
    @Property(bool,constant=True)
    def amazonAvailable(self):return self._nile_service is not None
    @Property(bool,constant=True)
    def gogAvailable(self):return self._gog_service is not None
    @Property(bool,constant=True)
    def xboxAvailable(self):return self._xodus_auth_service is not None and self._downloads is not None
    @Property(bool,notify=changed)
    def xboxSigningIn(self):return bool(self._xodus_auth_service and self._xodus_auth_service.running)
    @Property(str,notify=changed)
    def xboxMessage(self):return self._xbox_message
    @Property(QObject,constant=True)
    def xboxAuthService(self):return self._xodus_auth_service
    @Property(bool,notify=changed)
    def gogSigningIn(self):return self._gog_signin
    @Property(str,notify=changed)
    def gogLoginUrl(self):return self._gog_login_url
    @Property(str,notify=changed)
    def gogMessage(self):return self._gog_message
    @Property('QVariantMap',notify=changed)
    def gogAccount(self):return dict(self._gog_account)
    @Property(int,notify=changed)
    def gogGameCount(self):return len(self._gog_games)
    @Property(bool,notify=changed)
    def amazonSigningIn(self):return self._amazon_signin
    @Property(str,notify=changed)
    def amazonLoginUrl(self):return self._amazon_login_url
    @Property(str,notify=changed)
    def amazonMessage(self):return self._amazon_message
    @Property('QVariantMap',notify=changed)
    def amazonAccount(self):return dict(self._amazon_account)
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(bool,notify=changed)
    def signingIn(self):return self._signin
    @Property(str,constant=True)
    def loginUrl(self):return EPIC_LOGIN
    @Property(str,notify=changed)
    def message(self):return self._message
    @Property('QVariantMap',notify=changed)
    def epicAccount(self):return dict(self._account)
    @Property(int,notify=changed)
    def gameCount(self):return len(self._games)
    @Slot()
    def readSaved(self):
        if self._closed or self._busy or self._service is None:return
        self._account=self._service.read_saved_account()
        if self._account.get('status')=='saved':
            self._message='Saved Epic sign-in. Refresh to check this account with Epic.'
            self._bind_saved()
        self.changed.emit()
    @Slot(result=bool)
    def beginEpicSignIn(self):
        if self._closed or self._busy or self._service is None:return False
        self._signin=True;self._message='Sign into Epic below. Credentials go to Epic, not Heroic.';self.changed.emit();return True
    @Slot()
    def cancelSignIn(self):
        self._signin=False;self._amazon_signin=False;self._amazon_login_url='';self._gog_signin=False;self._gog_login_url=''
        if self._nile_service is not None:self._nile_service.cancel_login()
        if self._gog_service is not None:self._gog_service.cancel_login()
        if self._xodus_auth_service is not None:self._xodus_auth_service.cancel()
        self.changed.emit()
    @Slot(result=bool)
    def beginXboxSignIn(self):
        if self._closed or self._xodus_auth_service is None:return False
        if self._downloads is not None and (getattr(self._downloads,'busy',False) or getattr(self._downloads,'loadingLibrary',False)):
            self._xbox_message='Wait for the current provider operation to finish before signing into Xbox.'
            self.changed.emit();return False
        self._xbox_message='Starting the Xodus sign-in flow inside Waystone…';self.changed.emit()
        started=self._xodus_auth_service.beginLogin()
        if not started:self._xbox_message=self._xodus_auth_service.message
        self.changed.emit();return started
    @Slot(result=bool)
    def refreshXboxLibrary(self):
        if self._closed or self._downloads is None:return False
        started=self._downloads.refreshXboxLibrary()
        self._xbox_message='Refreshing your Xbox library through Xodus.' if started else 'Xbox library refresh could not start.'
        self.changed.emit();return started
    @Slot(bool)
    def _completeXboxSignIn(self,success):
        if self._closed:return
        self._xbox_message=self._xodus_auth_service.message
        self.changed.emit()
        if success and self._downloads is not None:self._downloads.refreshXboxLibrary()
    def _start(self,factory):
        if self._closed or self._busy:return False
        if self._worker is not None:
            if self._worker.isRunning():self._worker.wait()
            self._worker.deleteLater();self._worker=None
        self._busy=True;self._worker=_AccountJob(factory,self)
        self._worker.result.connect(self._complete);self._worker.start();self.changed.emit();return True
    @Slot(str,str,result=bool)
    def acceptEpicPage(self,url,body):
        if not self._signin or self._busy or self._closed or self._service is None:return False
        try:
            p=urlsplit(url);q=parse_qs(p.query)
            if (p.scheme!='https' or p.netloc!='www.epicgames.com' or p.path!='/id/api/redirect'
                    or q.get('clientId')!=[EPIC_CLIENT] or q.get('responseType')!=['code']):return False
            if len(body)>16384:return False
            data=json.loads(body);code=data.get('authorizationCode')
            if not isinstance(code,str) or not 1<=len(code)<=4096 or any(ord(c)<33 or ord(c)>126 for c in code):return False
        except (ValueError,TypeError,AttributeError):return False
        async def login():
            account=await self._service.authenticate(code)
            return {'ok':account.get('status')=='connected','account':account,'provider':'epic'}
        self._signin=False;self._message='Connecting your Epic account…'
        return self._start(login)
    @Slot()
    def readGogSaved(self):
        if self._closed or self._busy or self._gog_service is None:return
        self._gog_account=self._gog_service.read_saved_account()
        self._gog_games=self._gog_service.load_cached_library()
        if self._gog_account.get('status')=='connected':
            self._gog_message=f"GOG connected to Waystone · {len(self._gog_games)} cached games."
            self._bind_gog()
        self.changed.emit()
    @Slot(result=bool)
    def beginGogSignIn(self):
        if self._closed or self._busy or self._gog_service is None:return False
        try:self._gog_login_url=self._gog_service.begin_login()
        except Exception:
            self._gog_message='GOG sign-in could not be started.';self.changed.emit();return False
        self._gog_signin=True
        self._gog_message='Sign in to GOG here. Waystone stores its own GOG account; Heroic does not need to run.'
        self.changed.emit();return True
    @Slot(str,result=bool)
    def acceptGogCallback(self,url):
        if not self._gog_signin or self._busy or self._closed or self._gog_service is None:return False
        code=self._gog_service.consume_callback(url)
        if code is None:return False
        self._gog_signin=False;self._gog_login_url='';self._gog_message='Connecting GOG to Waystone…'
        async def login():
            try:account=await self._gog_service.authenticate(code)
            except Exception:return {'ok':False,'provider':'gog'}
            try:games=await self._gog_service.refresh_library();library_error=False
            except Exception:games=[];library_error=True
            return {'ok':account.get('status')=='connected','provider':'gog','gog_account':account,'gog_games':games,'library_error':library_error}
        return self._start(login)
    @Slot(result=bool)
    def refreshGogLibrary(self):
        if self._gog_service is None or self._closed or self._busy:return False
        if self._gog_account.get('status')!='connected':
            self._gog_message='Connect GOG to Waystone before refreshing its library.';self.changed.emit();return False
        async def refresh():
            try:return {'ok':True,'provider':'gog_library','gog_games':await self._gog_service.refresh_library()}
            except Exception:return {'ok':False,'provider':'gog_library'}
        self._gog_message='Refreshing GOG library directly in Waystone…';return self._start(refresh)
    @Slot(result=bool)
    def signOutGog(self):
        if self._gog_service is None or self._closed or self._busy:return False
        if self._downloads is not None and self._downloads.busy:
            self._gog_message='Wait for the current install before signing out of GOG.';self.changed.emit();return False
        async def logout():
            try:return {'ok':True,'provider':'gog','gog_account':self._gog_service.logout(),'gog_games':[]}
            except Exception:return {'ok':False,'provider':'gog'}
        return self._start(logout)

    @Slot()
    def readAmazonSaved(self):
        if self._closed or self._busy or self._nile_service is None:return
        self._amazon_account=self._nile_service.read_saved_account()
        if self._amazon_account.get('status')=='connected':self._amazon_message='Amazon connected to Waystone.'
        self.changed.emit()
    @Slot(result=bool)
    def beginAmazonSignIn(self):
        if self._closed or self._busy or self._nile_service is None:return False
        self._amazon_signin=True;self._amazon_message='Sign into Amazon in Waystone. Nile credentials stay in Waystone-owned storage.';self.changed.emit()
        async def begin():
            data=await self._nile_service.begin_login()
            return {'ok':True,'provider':'amazon','login_url':data['url']}
        return self._start(begin)
    @Slot(str,result=bool)
    def acceptAmazonCallback(self,url):
        if not self._amazon_signin or self._busy or self._closed or self._nile_service is None:return False
        async def register():
            account=await self._nile_service.finish_login(url)
            games=[]
            library_error=False
            if account.get('status')=='connected':
                try:games=await self._nile_service.refresh_library()
                except Exception:library_error=True
            return {'ok':account.get('status')=='connected','provider':'amazon',
                    'amazon_account':account,'amazon_games':games,'library_error':library_error}
        self._amazon_signin=False;self._amazon_login_url=''
        self._amazon_message='Connecting Amazon to Waystone…'
        return self._start(register)
    @Slot(result=bool)
    def refreshAmazonLibrary(self):
        if self._nile_service is None or self._closed or self._busy:return False
        if self._amazon_account.get('status')!='connected':
            self._amazon_message='Connect Amazon to Waystone before refreshing its library.'
            self.changed.emit();return False
        async def refresh():
            games=await self._nile_service.refresh_library()
            return {'ok':True,'provider':'amazon_library','amazon_games':games}
        self._amazon_message='Refreshing Amazon library directly in Waystone…'
        return self._start(refresh)
    @Slot(result=bool)
    def signOutAmazon(self):
        if self._nile_service is None or self._closed or self._busy:return False
        if self._downloads is not None and self._downloads.busy:
            self._message='Wait for the current install before signing out of Amazon.';self.changed.emit();return False
        async def logout():
            account=await self._nile_service.logout()
            return {'ok':account.get('status')=='disconnected','provider':'amazon','amazon_account':account}
        return self._start(logout)
    @Slot(result=bool)
    def refreshEpic(self):
        if self._service is None or self._closed or self._busy:return False
        async def refresh():
            games=await self._service.refresh_library()
            account=self._service.read_saved_account()
            if games is not None and account.get('account_id'):account=dict(account,status='connected')
            return {'ok':games is not None,'account':account,'games':games or []}
        self._message='Refreshing Epic directly…';return self._start(refresh)
    @Slot(result=bool)
    def signOutEpic(self):
        if self._service is None or self._closed or self._busy:return False
        if self._downloads is not None and self._downloads.busy:
            self._message='Wait for the current installation before signing out.';self.changed.emit();return False
        async def logout():
            account=await self._service.logout()
            return {'ok':account.get('status')=='disconnected','account':account,'games':[]}
        return self._start(logout)
    @Slot(dict)
    def _complete(self,frame):
        if self._closed:return
        self._busy=False
        if frame.get('ok') and frame.get('provider')=='gog_library':
            self._gog_games=list(frame.get('gog_games',[]))
            if self._downloads is not None:self._downloads.addManagedGogLibrary(self._gog_games)
            self._gog_message=f"GOG library refreshed · {len(self._gog_games)} games synced."
        elif frame.get('ok') and frame.get('provider')=='gog':
            self._gog_account=dict(frame.get('gog_account',{}));self._gog_games=list(frame.get('gog_games',[]))
            if self._gog_account.get('status')=='disconnected':
                self._gog_games=[];self._gog_message='GOG disconnected from Waystone. Other launcher sessions were not changed.'
                if self._downloads is not None:self._downloads.clearManagedAccount('gog')
            else:
                if self._downloads is not None:self._downloads.addManagedGogLibrary(self._gog_games)
                self._bind_gog()
                self._gog_message=(f"GOG connected to Waystone · {len(self._gog_games)} games synced." if not frame.get('library_error') else 'GOG connected, but its library could not refresh. Use Refresh GOG library to retry.')
        elif frame.get('ok') and frame.get('provider')=='amazon_library':
            games=frame.get('amazon_games',[])
            if self._downloads is not None:self._downloads.addManagedAmazonLibrary(games)
            self._amazon_message=f"Amazon library refreshed · {len(games)} games synced."
        elif frame.get('ok') and frame.get('provider')=='amazon':
            self._amazon_account=dict(frame.get('amazon_account',{}))
            self._amazon_login_url=frame.get('login_url','')
            self._amazon_signin=bool(self._amazon_login_url)
            if self._amazon_account.get('status')=='connected':
                games=frame.get('amazon_games',[])
                if self._downloads is not None:
                    self._downloads.addManagedAmazonLibrary(games)
                self._amazon_message=(f"Amazon connected to Waystone · {len(games)} games synced."
                    if not frame.get('library_error') else
                    "Amazon connected to Waystone, but its library could not refresh. Use Refresh in the install picker.")
            else:
                self._amazon_message='Amazon sign-in ready in Waystone.'
        elif frame.get('ok'):
            self._account=dict(frame.get('account',{}));self._games=list(frame.get('games',self._games))
            if self._account.get('status')=='disconnected':
                self._games=[];self._message='Signed out of Waystone. Other launcher sessions were not changed.'
                if self._downloads is not None:self._downloads.clearManagedAccount('epic')
            else:
                self._message='Epic connected directly to Waystone.'
                self._bind_saved()
        elif not frame.get('ok'):
            if frame.get('provider')=='gog_library':
                self._gog_message='GOG library refresh failed. No credentials were displayed.'
            elif frame.get('provider')=='gog':
                self._gog_signin=False;self._gog_login_url='';self._gog_message='GOG account action failed. No credentials were displayed.'
            elif frame.get('provider')=='amazon_library':
                self._amazon_message='Amazon library refresh failed. No credentials were displayed.'
            elif frame.get('provider')=='amazon':
                self._amazon_signin=False;self._amazon_login_url=''
                self._amazon_message='Amazon account action failed. No credentials were displayed.'
            else:self._message='Epic account action failed. No credentials were displayed.'
        self.changed.emit()
    @Slot()
    def _bind_saved(self,*ignored):
        if self._closed or self._service is None or self._downloads is None:return
        if self._account.get('status') not in ('connected','saved'):return
        if not self._downloads.configureManagedAccount('epic',str(self._service.binary_path),str(self._service.config_root)):
            self._message='Epic sign-in saved. The running download is unchanged; Waystone will switch after it finishes.'
        elif self._games:self._downloads.addManagedEpicLibrary(self._games)
    def _bind_gog(self):
        if self._closed or self._gog_service is None or self._downloads is None:return
        if self._gog_account.get('status')!='connected':return
        if not self._downloads.configureManagedAccount('gog',str(self._gog_service.binary_path),str(self._gog_service.config_root)):
            self._gog_message='GOG account saved. The running install is unchanged; Waystone will switch after it finishes.'
        elif self._gog_games:self._downloads.addManagedGogLibrary(self._gog_games)

    @Slot(result=bool)
    def shutdown(self):
        self._closed=True;self._signin=False;self._amazon_signin=False;self._gog_signin=False
        if self._nile_service is not None:self._nile_service.cancel_login()
        if self._gog_service is not None:self._gog_service.cancel_login()
        if self._xodus_auth_service is not None:self._xodus_auth_service.shutdown()
        if self._worker is not None:
            self._worker.cancel();self._worker.wait();self._worker.deleteLater();self._worker=None
        self._busy=False;return True
