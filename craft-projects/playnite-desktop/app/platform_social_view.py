"""Provider connections, remembered sign-ins and live platform progress."""
from copy import deepcopy
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication,QObject,Property,Signal,Slot,QThread
from native_services.platform_social import PlatformSocial
from native_services.steam_native import SteamNativeAdapter
from native_services.steam_web import SteamWebAdapter
from native_services.progress_observations import ProgressObservations
from native_services.steam_session import SteamQrSession
from native_services.xbox_progress import XboxProgress

class _Read(QThread):
    def __init__(self,fn,parent):super().__init__(parent);self.fn=fn;self.value=None;self.failed=False
    def run(self):
        try:self.value=self.fn()
        except Exception:self.failed=True

def social_catalog_snapshot(games):
    """Detached exact identity fields used by Xbox discovery and cache titles.

    Copying the entire immutable import (descriptions, artwork provenance and
    relationship graphs) on the GUI thread serves no social feature. Preserve
    all records/variants and only the fields this read-only adapter consumes.
    """
    return [{'id': g['id'], 'title': g.get('title'),
             'variants': [{'source': deepcopy(v.get('source')),
                           'provider_game_id': deepcopy(v.get('provider_game_id'))}
                          for v in g.get('variants', [])]} for g in games]


class PlatformSocialView(QObject):
    changed=Signal();progressChanged=Signal()
    def __init__(self,games=(),*,native=None,web_factory=None,cache_path=None,qr_session=None,xodus_helper=None,xbox_adapter=None,session_store=None,parent=None):
        super().__init__(parent);self._native=native or SteamNativeAdapter();self._web_factory=web_factory or SteamWebAdapter
        games=list(games)
        self.directory=PlatformSocial({'steam':self._native});self._web=None;self._steam_authenticated=False;self._job=None;self._closed=False
        self._session_store=session_store;self._forget_requested=False;self._pending_session_record=None
        self._session_status='Sign-in is remembered in your desktop keyring.' if session_store else 'Session storage is disabled in this isolated view.'
        self._qr=qr_session or SteamQrSession();self._qr_url='';self._qr_active=False;self._qr_generation=0
        self._cards=self.directory.providers();self._friends=[];self._status='Connect Steam to read its signed-in friends. Xbox party voice remains separate.'
        self._products={};self._games=[];self._observations={};self._cache=ProgressObservations(cache_path);self._native_identity=False
        for g in games:
            ids=set()
            for v in g.get('variants',[]):
                if str(v.get('source','')).lower()=='steam':
                    product=str(v.get('provider_game_id') or v.get('source_id') or '')
                    if product.isascii() and product.isdigit() and 0<int(product)<=4294967295:ids.add(product)
            if ids:
                self._products[g['id']]=sorted(ids)
                self._games.append({'game_key':g['id'],'title':g.get('title') or g['id'],'provider':'steam','products':sorted(ids)})
        self._source_games=social_catalog_snapshot(games);self._xbox=xbox_adapter or XboxProgress(xodus_helper);self._xbox_account='';self._xbox_products={};self._provider='steam'
        self._key='';self._items=[];self._progress={};self._progress_status='Select a Steam game, then Sync achievements.'
    @Property(bool,notify=changed)
    def busy(self):return self._job is not None
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property('QVariantList',notify=changed)
    def providers(self):return deepcopy(self._cards)
    @Property('QVariantList',notify=changed)
    def friends(self):return deepcopy(self._friends)
    @Property(str,notify=changed)
    def steamAccount(self):return next((r['account_id'] for r in self._cards if r['provider']=='steam'),'')
    @Property(bool,notify=changed)
    def steamConnected(self):return any(r['provider']=='steam' and r['status']=='live' for r in self._cards)
    @Property(bool,notify=changed)
    def achievementAccess(self):return self._web is not None and (self.steamConnected or self._steam_authenticated)
    @Property(str,notify=progressChanged)
    def gameKey(self):return self._key
    @Property('QStringList',notify=progressChanged)
    def steamProducts(self):return list(self._products.get(self._key,[]))
    @Property('QVariantList',notify=progressChanged)
    def achievements(self):return deepcopy(self._items)
    @Property('QVariantMap',notify=progressChanged)
    def progress(self):return deepcopy(self._progress)
    @Property(str,notify=progressChanged)
    def progressStatus(self):return self._progress_status
    @Property('QVariantList',notify=changed)
    def achievementGames(self):return deepcopy(self._games)
    @Property('QVariantList',notify=changed)
    def achievementAccounts(self):
        accounts={};latest={}
        for value in self._observations.values():
            key=(value['provider'],value['account_id'],value['product_id'])
            if key not in latest or value.get('observed_at','')>latest[key].get('observed_at',''):latest[key]=value
        for value in latest.values():
            key=(value['provider'],value['account_id'])
            row=accounts.setdefault(key,{'provider':key[0],'account_id':key[1],'observed_games':0,'unlocked_count':0,'total_count':0,'unknown_total':False})
            row['observed_games']+=1;row['unlocked_count']+=value.get('unlocked_count') or 0;row['total_count']+=value.get('total_count') or 0;row['unknown_total']=row['unknown_total'] or value.get('total_count') is None
        for row in accounts.values():
            if row['unknown_total']:row['total_count']=None
        return deepcopy(list(accounts.values()))
    @Slot()
    def loadProgressCache(self):self._start('cache',self._cache.list)
    @Property(str,notify=changed)
    def sessionStatus(self):return self._session_status
    @Slot()
    def restoreSession(self):
        if self.busy or self._closed or self._steam_authenticated or self._session_store is None:return
        if self._forget_requested:
            self._start('forget',lambda:self._session_store.clear('steam'));return
        def restore():
            record=self._session_store.load('steam')
            if record is None:return None
            if record['kind']=='steam_refresh':adapter=self._qr.restore(record['account_id'],record['refresh_token'])
            elif record['kind']=='steam_web':adapter=self._web_factory(record['account_id'],record['api_key'])
            elif record['kind']=='steam_native':return {'native':True}
            else:raise ValueError('Unsupported saved sign-in')
            fresh=adapter.session_record() if hasattr(adapter,'session_record') else record
            return {'adapter':adapter,'record':fresh or record,'authenticated':record['kind']=='steam_refresh'}
        self._session_status='Restoring saved Steam sign-in…'
        self._start('restore',restore)
    def _remember(self,record,refresh=True):
        self._pending_session_record=record;self._refresh_after_remember=refresh
        if self._session_store is None or record is None:
            if refresh:self._start('refresh',lambda:self.directory.refresh('steam'))
            return
        self._session_status='Saving sign-in to the desktop keyring…'
        self._start('remember',lambda:self._session_store.save('steam',record))
    @Slot()
    def rememberSteamSession(self):
        if self.busy or self._closed or self._pending_session_record is None or self._session_store is None:return
        self._remember(self._pending_session_record,refresh=False)
    @Property(str,notify=changed)
    def qrUrl(self):return self._qr_url
    @Property(bool,notify=changed)
    def qrActive(self):return self._qr_active
    @Slot()
    def beginSteamSignIn(self):
        if self.busy:return
        self._qr_generation+=1;self._qr_active=False;self._qr_url=''
        self._status='Starting Steam Guard QR sign-in. Approve only the Waystone request on your phone.'
        self._start('qr_begin',self._qr.begin)
    @Slot()
    def pollSteamSignIn(self):
        if self._qr_active:self._start('qr_poll',self._qr.poll)
    @Slot()
    def cancelSteamSignIn(self):
        # Stop observation immediately; never finish linking after user cancelled.
        self._qr_generation+=1;self._qr_active=False;self._qr_url=''
        if not self.busy:self._qr.cancel()
        self._status='Steam QR sign-in cancelled; existing saved sessions are unchanged.';self.changed.emit()
    @Property(str,notify=changed)
    def progressProvider(self):return self._provider
    @Slot(str)
    def setProgressProvider(self,provider):
        if provider not in ('steam','xbox') or provider==self._provider:return
        self._provider=provider;self._key='';self._items=[];self._progress={};self.changed.emit();self.progressChanged.emit()
    @Property('QStringList',notify=progressChanged)
    def progressProducts(self):return list((self._products if self._provider=='steam' else self._xbox_products).get(self._key,[]))
    @Slot()
    def connectXboxProgress(self):self._start('xbox',lambda:self._xbox.discover(self._source_games))
    def _start(self,kind,fn):
        if self.busy or self._closed:return
        self._kind=kind;self._job_generation=self._qr_generation
        job=_Read(fn,self)
        self._job=job
        # Preserve worker/thread refs through completion: finished handler waits
        # for full thread cleanup before dropping our ref; deleteLater remains
        # queued for the owner event loop after shutdown/finished handling.
        job.finished.connect(self._finished)
        job.start();self.changed.emit()
    @Slot()
    def connectSteam(self):
        def connect():
            value=self._native.fetch_friends()
            if value.get('account_id') and value.get('status') in ('live','offline','cached'):
                self.directory.bind('steam',value['account_id'])
                self.directory.register('steam',SimpleNamespace(fetch_friends=lambda:value));self.directory.refresh('steam')
                self.directory.register('steam',self._native)
                return True
            self.directory.register('steam',self._native);self.directory.refresh('steam');return False
        self._start('native',connect)
    @Slot(str,str)
    def connectSteamWeb(self,owner,key):
        if self.busy:return
        try:adapter=self._web_factory(owner,key)
        except Exception:self._status='Enter your SteamID64 and an existing32-character Web API key. Nothing was submitted.';self.changed.emit();return
        self._pending_session_record={'kind':'steam_web','account_id':owner,'api_key':key}
        def connect():
            self.directory.bind('steam',owner);self.directory.register('steam',adapter)
            self.directory.refresh('steam');return adapter
        self._start('web',connect)
    @Slot(str)
    def refreshProvider(self,provider):
        if provider=='steam':self._start('refresh',lambda:self.directory.refresh(provider))
    @Slot()
    def disconnectSteam(self):
        self._qr_generation+=1;self._qr_active=False;self._qr_url=''
        self._pending_session_record=None
        self.directory.disconnect('steam');self.directory.register('steam',self._native);self._web=None;self._steam_authenticated=False
        self._cards=self.directory.providers();self._friends=self.directory.friends();self._items=[];self._progress={}
        self._status='Steam signed out of Waystone.';self._progress_status='Steam disconnected.'
        if self._session_store is not None:
            self._forget_requested=True;self._session_status='Forgetting saved Steam sign-in…'
            if not self.busy:self._start('forget',lambda:self._session_store.clear('steam'))
        self.changed.emit();self.progressChanged.emit()
    @Slot(str)
    def selectGame(self,key):
        if key==self._key:return
        self._key=key;self._items=[];self._progress={};self._progress_status='Sync the selected Steam product. No unlocks are inferred from imported metadata.'
        owner=self.steamAccount if self._provider=='steam' else self._xbox_account
        cached=[v for v in self._observations.values() if v.get('provider')==self._provider and v.get('game_key')==key and (not owner or v.get('account_id')==owner)]
        if len(cached)==1:
            self._progress=deepcopy(cached[0]);self._items=deepcopy(cached[0]['items'])
            self._progress_status='Cached '+str(cached[0]['unlocked_count'])+' / '+str(cached[0]['total_count'])+' · account '+cached[0]['account_id']+' · '+cached[0].get('observed_at','date unknown')

        self.progressChanged.emit()
    @Slot(str)
    def syncAchievements(self,product):
        if self.busy:return
        if self._provider=='xbox' and not self._xbox_account:
            self._progress_status='Choose Read Xbox account titles to verify current account/product links before syncing.';self.progressChanged.emit();return
        if self._provider=='steam' and not self.achievementAccess:
            self._progress_status='Sign in with Steam Guard under Friends → Platforms to sync achievements (or use an existing Web API key).';self.progressChanged.emit();return
        if product not in (self._products if self._provider=='steam' else self._xbox_products).get(self._key,[]):
            self._progress_status='Choose an exact Steam product for this game.';self.progressChanged.emit();return
        key,account=self._key,(self.steamAccount if self._provider=='steam' else self._xbox_account);adapter=self._web if self._provider=='steam' else self._xbox
        self._progress_status='Reading Steam achievements…';self.progressChanged.emit()
        def fetch():
            value=adapter.fetch_achievements(key,product)
            self._cache.put(value)
            return key,account,value
        self._start('progress',fetch)
    @Slot()
    def _finished(self):
        job=self._job;kind=self._kind
        if job is None:return
        # finished may precede native thread-local cleanup. Join before allowing
        # Qt deferred deletion or dropping our strong Python reference.
        job.wait()
        self._job=None
        job.deleteLater()
        if self._closed:return
        # Sign-out invalidates every in-flight restore/QR/save, then clears the
        # keyring in the same serialized worker lane; stale results cannot rebind.
        if self._forget_requested and kind!='forget':
            self._start('forget',lambda:self._session_store.clear('steam'));return
        if kind=='forget':
            self.directory.disconnect('steam');self.directory.register('steam',self._native)
            self._cards=self.directory.providers();self._friends=self.directory.friends()
            self._forget_requested=job.failed
            self._session_status='Saved Steam sign-in forgotten.' if not job.failed else 'Could not forget saved sign-in. Unlock your desktop keyring and sign out again.'
            self.changed.emit();return
        if kind in ('native','web','refresh') and self._job_generation!=self._qr_generation:
            self.directory.disconnect('steam');self.directory.register('steam',self._native)
            self._cards=self.directory.providers();self._friends=self.directory.friends();self.changed.emit();return
        if kind=='restore':
            if self._job_generation!=self._qr_generation:self.changed.emit();return
            if job.failed:
                self._session_status='Saved Steam sign-in could not be restored. Unlock the keyring and retry, or sign in again.'
            elif job.value is None:self._session_status='No saved Steam sign-in yet. Sign in once to remember it.'
            elif job.value.get('native'):
                self.connectSteam();return
            else:
                adapter=job.value['adapter'];self._web=adapter;self._steam_authenticated=job.value['authenticated']
                self.directory.bind('steam',adapter.account_id);self.directory.register('steam',adapter)
                self._status='Steam sign-in restored. Reading friends…'
                self._remember(job.value['record'])
            self.changed.emit();return
        if kind=='remember':
            if self._job_generation!=self._qr_generation:self.changed.emit();return
            self._session_status='Steam sign-in saved securely; it will reconnect after restart.' if not job.failed else 'Signed in for this run only. Unlock your desktop keyring, then Save sign-in.'
            if self._refresh_after_remember:self._start('refresh',lambda:self.directory.refresh('steam'))
            self.changed.emit();return
        if kind in ('qr_begin','qr_poll'):
            if self._job_generation!=self._qr_generation:
                self._qr.cancel();self.changed.emit();return
            if job.failed:
                self._qr_active=False;self._qr_url='';self._status='Steam sign-in unavailable or expired. Start QR sign-in again; no login was replayed.'
            elif kind=='qr_begin':
                self._qr_url=job.value['challenge_url'];self._qr_active=True;self._status='Scan with Steam Guard, and approve Waystone on Linux. No API key needed.'
            elif job.value is None:
                self._qr_url=self._qr.challenge_url;self._status='Waiting for your Steam Guard approval…'
            else:
                adapter=job.value;self._web=adapter;self._steam_authenticated=True;self._qr_active=False;self._qr_url=''
                self.directory.bind('steam',adapter.account_id);self.directory.register('steam',adapter)
                self._status='Steam signed in. Reading friends…'
                self._remember(adapter.session_record() if hasattr(adapter,'session_record') else None)
            self.changed.emit();return
        if kind=='xbox':
            if job.failed:self._progress_status='Xbox title discovery unavailable. No sign-in or unlock was started.'
            else:
                self._xbox_account=job.value['account_id'];self._games=[g for g in self._games if g['provider']!='xbox']+job.value['games']
                self._xbox_products={g['game_key']:g['products'] for g in job.value['games']}
                self._progress_status=str(len(job.value['games']))+' Xbox library games linked by exact package identity and TitleId.'
            self.changed.emit();self.progressChanged.emit();return
        if kind=='cache':
            if job.failed:self._status='Progress cache unavailable; no counts inferred.'
            else:
                self._observations={(v['provider'],v['account_id'],v['game_key'],v['product_id']):v for v in job.value}
                # Cached observations retain exact native game identity but do not
                # authenticate a current account or populate the live helper grant.
                source={g['id']:g for g in self._source_games}
                existing={(g['provider'],g['game_key']) for g in self._games}
                for v in job.value:
                    if v['provider']=='xbox' and v['game_key'] in source:
                        if not self._xbox_account:self._xbox_products.setdefault(v['game_key'],[])
                        if not self._xbox_account and v['product_id'] not in self._xbox_products[v['game_key']]:self._xbox_products[v['game_key']].append(v['product_id'])
                        if ('xbox',v['game_key']) not in existing:
                            self._games.append({'provider':'xbox','game_key':v['game_key'],'title':source[v['game_key']].get('title') or v['game_key'],'products':self._xbox_products.get(v['game_key'],[])})
                            existing.add(('xbox',v['game_key']))
                key=self._key;self._key='';self.selectGame(key)
            self.changed.emit();return
        if kind=='progress':
            if job.failed:self._progress_status='Steam achievement read failed or is private. No unlocks or completion were invented.'
            else:
                key,account,value=job.value
                self._observations[(value['provider'],value['account_id'],key,value['product_id'])]=deepcopy(value)
                if key==self._key and value['provider']==self._provider and account==(self.steamAccount if self._provider=='steam' else self._xbox_account):
                    self._items=value['items'];self._progress=value
                    self._progress_status=str(value['unlocked_count'])+' / '+(str(value['total_count']) if value['total_count'] is not None else 'unknown total')+' unlocked · '+value['provider']+' · account '+account
            self.progressChanged.emit()
        else:
            if job.failed:self.directory.unavailable('steam')
            self._cards=self.directory.providers();self._friends=self.directory.friends()
            if self._progress and self._progress.get('account_id')!=self.steamAccount:
                self._items=[];self._progress={};self._progress_status='Steam account changed; sync this account separately.'
            if kind=='web' and not job.failed and self.steamConnected:self._web=job.value;self._steam_authenticated=False
            elif kind=='native' and not job.failed and job.value:self._web=None;self._steam_authenticated=False
            if self.steamConnected:self._status=('Steam session connected · ' if self._web is not None else 'Steam client connected · ')+str(len([f for f in self._friends if f['provider']=='steam']))+' friends · account '+self.steamAccount
            elif self._steam_authenticated:self._status='Steam signed in; friends are not shared or unavailable. Achievement sync can still be requested.'
            elif kind=='native':self._status='Steam is not running/signed in or its IPC reader is unavailable. Open Steam, then Connect; an existing Web API key is an alternative.'
            else:self._status='Steam read unavailable. Check key, profile privacy or Steam sign-in; cached rows are not current presence.'
            self.progressChanged.emit()
            if not job.failed and self.steamConnected and kind in ('web','native'):
                self._remember(self._pending_session_record if kind=='web' else {'kind':'steam_native'},refresh=False)
        self.changed.emit()
    def shutdown(self):
        self._closed=True
        job=self._job
        if job is not None:
            # API/keyring operations have their own deadlines. Never destroy a
            # still-running QThread after an arbitrary shorter shutdown timeout.
            job.wait()
            self._job=None
            job.deleteLater()
        if self._forget_requested and self._session_store is not None:
            try:self._session_store.clear('steam')
            except Exception:pass
