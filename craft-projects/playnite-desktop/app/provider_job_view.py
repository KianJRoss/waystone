"""Native download/install controller. Planning and page opening never execute tools."""
from __future__ import annotations
import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path
from PySide6.QtCore import QObject, Property, Signal, Slot
from xodus_social_view import SocialWorkerThread as AsyncJobThread
from native_services.provider_jobs import ProviderJobManager, ProviderInstallRecipe, ProviderToolConfig
from native_services.provider_entitlements import ProviderEntitlementsReader
from native_services.installation_library import (PROVIDERS, catalog_install_choices,
    merge_cached_choices, InstallPreferences, inspect_destination, valid_product)
from native_services.install_receipts import PROVIDERS as RECEIPT_PROVIDERS
from native_services.xodus_install import XodusInstaller, XodusInstallPlan, XodusInstallError
from native_services import waystone_paths as _wpaths

class ProviderJobView(QObject):
    changed=Signal()
    finished=Signal(bool)
    _progress=Signal(dict)
    _result=Signal(dict)
    _libraryResult=Signal(dict)
    selectionChanged=Signal()
    refreshInstalledRequested=Signal()
    _packageResult=Signal(dict)

    def __init__(self,manager=None,parent=None,*,catalog=None,cache_reader=None,
                 preferences=None,steam_dispatch=None,storage_probe=None,receipt_registry=None):
        super().__init__(parent)
        self._receipt_registry=receipt_registry
        self._managed_accounts={};self._managed_library={}
        self._manager=manager or ProviderJobManager()
        self._worker=None
        self._busy=False
        self._plan=None
        self._target=None
        self._generation=0
        self._state={'phase':'idle','percent':0,'message':'Choose a provider product and preview its install destination.'}
        self._preview={}
        self._account_config_root=_wpaths.store('providers-v1')
        self._imported=catalog_install_choices(catalog)
        self._choices=merge_cached_choices(self._imported,[])
        self._reader=cache_reader or ProviderEntitlementsReader()
        self._preferences=preferences or InstallPreferences()
        self._root=None
        self._steam_dispatch=steam_dispatch
        self._storage_probe=storage_probe or inspect_destination
        self._storage={}
        self._xodus=None;self._packages=[];self._package_scope=None;self._xbox_receipt={};self._install_receipt={};self._xbox_library=[]
        self._provider='epic';self._query='';self._game_filter=''
        self._selection={};self._loading=False;self._library_loaded=False;self._library_epoch=0
        self._library_status='Choose a game from the library, or refresh connected provider libraries.'
        self._progress.connect(self._on_progress)
        self._result.connect(self._on_result)
        self._libraryResult.connect(self._on_library_result)
        self._packageResult.connect(self._on_packages)

    def configureManagedAccount(self,provider,binary,config_root):
        """Route future jobs only; never change the active plan/credentials."""
        if self._busy or self._loading or provider not in ('epic','amazon','gog'):return False
        if any(not isinstance(v,str) or '\x00' in v or not Path(v).is_absolute() for v in (binary,config_root)):return False
        self._managed_accounts[provider]=(binary,config_root)
        return True

    def clearManagedAccount(self,provider):
        # Keep routing to the app-owned engine scope after logout. Never silently
        # borrow another client's still-signed-in account as a fallback.
        return provider in self._managed_accounts

    def managedBinary(self,provider):
        return self._managed_accounts.get(provider,('', ''))[0]

    def _merge_managed_library(self,provider,items,origin):
        self._managed_library[provider]=items
        all_items=[item for rows in self._managed_library.values() for item in rows]
        self._choices=merge_cached_choices(self._imported,all_items)
        ids={item.product_id for item in items}
        for row in self._choices:
            if row['provider']==provider and row['product_id'] in ids:row['origin']=origin
        self.changed.emit()

    def addManagedEpicLibrary(self,games):
        items=[]
        for row in games[:10000]:
            if not isinstance(row,dict) or not valid_product('epic',row.get('app_name')):continue
            title=row.get('app_title')
            if not isinstance(title,str) or not title or any(ord(c)<32 for c in title):continue
            items.append(SimpleNamespace(provider='epic',product_id=row['app_name'],title=title[:512],is_installed=None))
        self._merge_managed_library('epic',items,'Your connected Waystone Epic account')

    def addManagedGogLibrary(self,games):
        items=[]
        for row in games[:10000]:
            if not isinstance(row,dict) or not valid_product('gog',row.get('product_id')):continue
            title=row.get('title')
            if not isinstance(title,str) or not title or any(ord(c)<32 for c in title):continue
            items.append(SimpleNamespace(provider='gog',product_id=row['product_id'],title=title[:512],is_installed=None))
        self._merge_managed_library('gog',items,'Your connected Waystone GOG account')

    def addManagedAmazonLibrary(self,games):
        items=[]
        for row in games[:10000]:
            if not isinstance(row,dict) or not valid_product('amazon',row.get('product_id')):continue
            title=row.get('title')
            if not isinstance(title,str) or not title or any(ord(c)<32 for c in title):continue
            items.append(SimpleNamespace(provider='amazon',product_id=row['product_id'],
                title=title[:512],is_installed=row.get('is_installed')))
        self._merge_managed_library('amazon',items,'Your connected Waystone Amazon account')

    def configureXodus(self,binary,*,installer=None):
        """Explicit trusted backend; constructor/selection never starts it."""
        if self._busy or self._loading:return False
        self._xodus=installer or XodusInstaller(binary)
        return True

    @Property('QVariantList',notify=changed)
    def packageChoices(self):return [dict(x) for x in self._packages]
    @Property('QVariantMap',notify=changed)
    def xboxReceipt(self):return dict(self._xbox_receipt)
    @Property('QVariantMap',notify=changed)
    def installReceipt(self):return dict(self._install_receipt)
    @Property(str,notify=changed)
    def xboxBinary(self):return str(self._xodus.binary) if self._xodus else ''

    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property('QVariantMap',notify=changed)
    def state(self):return dict(self._state)
    @Property('QVariantMap',notify=changed)
    def preview(self):return dict(self._preview)
    @Property(str,notify=changed)
    def defaultRoot(self):
        if self._root is None:self._root=self._preferences.load()
        return self._root
    @Property('QVariantMap',notify=changed)
    def storage(self):return dict(self._storage)
    @Property('QStringList',constant=True)
    def providerKeys(self):return list(PROVIDERS)
    @Property('QStringList',constant=True)
    def providerLabels(self):return [p[0] for p in PROVIDERS.values()]
    @Property(str,notify=changed)
    def currentProvider(self):return self._provider
    @Property(str,notify=changed)
    def providerNotice(self):return PROVIDERS.get(self._provider,('', 'Unknown provider'))[1]
    @Property(bool,notify=changed)
    def canInstall(self):return self._provider in ('steam','epic','gog','amazon','xbox')
    @Property(bool,notify=changed)
    def loadingLibrary(self):return self._loading
    @Property(bool,notify=changed)
    def libraryLoaded(self):return self._library_loaded
    @Property(str,notify=changed)
    def libraryStatus(self):return self._library_status
    @Property('QVariantMap',notify=selectionChanged)
    def selection(self):return dict(self._selection)
    @Property('QVariantList',notify=changed)
    def games(self):
        query=self._query.casefold()
        return [dict(row) for row in self._choices if row['provider']==self._provider
                and (not self._game_filter or self._game_filter in row['game_ids'])
                and (not query or query in (row['title']+' '+row['product_id']).casefold())][:100]

    @Slot(str)
    def setProvider(self,provider):
        if self._busy or provider not in PROVIDERS:return
        self._provider=provider;self._selection={};self.clearPreview();self.selectionChanged.emit()

    @Slot(str)
    def searchGames(self,query):
        self._query=query[:512];self.changed.emit()

    @Slot()
    def showAllGames(self):
        self._game_filter='';self._query='';self.changed.emit()

    @Slot(str,result=bool)
    def chooseGame(self,key):
        if self._busy:return False
        row=next((row for row in self._choices if row['key']==key),None)
        if not row:return False
        self._provider=row['provider'];self._selection=dict(row);self.clearPreview()
        self.selectionChanged.emit();return True

    @Slot(str,result=bool)
    def forGame(self,game_id):
        if self._busy:return False
        matches=[row for row in self._choices if game_id in row['game_ids']]
        self._game_filter=game_id if matches else '';self._query=''
        if matches:self.chooseGame(matches[0]['key'])
        else:
            self._library_status='No exact installable store ID is linked to this game. Choose a provider library below.'
            self._selection={};self.clearPreview();self.selectionChanged.emit()
        self.changed.emit();return bool(matches)

    @Slot(str,result=bool)
    def checkDestination(self,path):
        if self._busy:return False
        self._storage=self._storage_probe(path)
        self.clearPreview();return bool(self._storage.get('ready'))

    @Slot(str,result=str)
    def localFolder(self,url):
        from PySide6.QtCore import QUrl
        value=QUrl(url)
        return value.toLocalFile() if value.isLocalFile() else ''

    def _ensure_worker(self):
        if self._worker is None:
            self._worker=AsyncJobThread(self);self._worker.start()
            if not self._worker.wait_ready():
                self._worker.stop();self._worker=None;return False
        return True

    @Slot(result=bool)
    def refreshLibrary(self):
        if self._loading or self._busy or not self._ensure_worker():return False
        self._loading=True;self._library_epoch+=1
        epoch=self._library_epoch;root=self._account_config_root
        self._library_status='Reading cached provider libraries…';self.changed.emit()
        async def read():
            def load():
                items=[];failed=0
                for provider in ('epic','gog','amazon'):
                    try:items.extend(self._reader.load_entitlements(provider,config_root=root))
                    except Exception:failed+=1
                return {'epoch':epoch,'rows':self._with_xbox(merge_cached_choices(self._imported,items)),
                        'cached':len(items),'failed':failed}
            frame=load()  # Already runs on the dedicated job worker, not the GUI thread.
            self._libraryResult.emit(frame)
        self._worker.schedule(read());return True

    def _with_xbox(self,rows):
        titles={r['product_id']:r['title'] for r in self._imported if r['provider']=='xbox'}
        ids={r['product_id'] for r in self._xbox_library}
        items=[SimpleNamespace(provider='xbox',product_id=row['product_id'],
            title=titles.get(row['product_id'],row['title']),is_installed=None) for row in self._xbox_library]
        merged=merge_cached_choices(rows,items)
        for row in merged:
            if row['provider']=='xbox' and row['product_id'] in ids:
                row['origin']='Xbox account entitlement record; subscriptions/trials/access are checked at download.'
        return merged

    @Slot(result=bool)
    def refreshXboxLibrary(self):
        if self._loading or self._busy or not self._xodus or not self._ensure_worker():return False
        self._loading=True;self._library_epoch+=1;epoch=self._library_epoch
        self._library_status='Reading the existing Xodus account library. No sign-in or download is started.'
        self.changed.emit()
        async def read():
            try:
                records=await self._xodus.library()
                self._xbox_library=records
                frame={'epoch':epoch,'rows':self._with_xbox([r for r in self._choices if r['provider']!='xbox']+[
                    r for r in self._imported if r['provider']=='xbox']), 'cached':len(records),'failed':0}
            except Exception:
                frame={'epoch':epoch,'rows':self._choices,'cached':0,'failed':1}
            self._libraryResult.emit(frame)
        self._worker.schedule(read());return True

    @Slot(dict)
    def _on_library_result(self,frame):
        if frame.get('epoch')!=self._library_epoch:return
        self._choices=frame['rows'];self._loading=False
        self._library_loaded=True
        self._library_status=f"{len(self._choices)} provider-qualified games · {frame['cached']} cached account entries."
        if frame['failed']:self._library_status+=' Some account caches could not be read; refresh/sign in with the provider.'
        if self._worker is not None and not self._busy:
            self._worker.stop();self._worker=None
        self.changed.emit()

    @Property(str,notify=changed)
    def accountConfigRoot(self):return str(self._account_config_root)

    @Slot(str,result=bool)
    def setAccountConfigRoot(self,path):
        if self._busy or self._loading:return False
        if not Path(path).is_absolute():
            self._plan=None;self._preview={}
            self._state={'phase':'invalid','percent':0,'message':'Choose an absolute provider account-data directory.'}
            self.changed.emit();return False
        if self._account_config_root!=Path(path):self._library_loaded=False
        self._account_config_root=Path(path);self.clearPreview();return True

    @Slot()
    def clearPreview(self):
        if self._busy:return
        self._plan=None;self._preview={};self._packages=[];self._package_scope=None;self.changed.emit()

    @Slot(str,str,str,str,str,str,str,result=bool)
    def prepare(self,provider,product_id,title,root,operation,tool,auth_path):
        return self.prepare_with_options(provider,product_id,title,root,operation,tool,auth_path)

    def prepare_with_options(self,provider,product_id,title,root,operation,tool,auth_path,
                             *,config_path_override='',game_folder_override='',platform_override=''):
        if self._busy or self._loading:return False
        self._plan=None;self._preview={};self._packages=[];self._package_scope=None;self._xbox_receipt={};self._install_receipt={}
        if provider in PROVIDERS:self._provider=provider
        try:
            if provider=='xbox':
                if operation!='install' or not valid_product('xbox',product_id):
                    raise ValueError('Invalid Store install target')
                if self._xodus is None or str(self._xodus.binary)!=tool:
                    self.configureXodus(tool)
                self._storage=self._storage_probe(root)
                if not self._storage.get('ready'):raise ValueError('Invalid destination')
                if not self._ensure_worker():return False
                self._busy=True;self._generation+=1
                generation=self._generation
                self._target=('xbox',product_id)
                self._package_scope=(product_id,title,root)
                self._packages=[];self._xbox_receipt={};self._install_receipt={}
                self._state={'phase':'checking','percent':None,'message':'Checking public Store packages. No download has started.'}
                self.changed.emit()
                async def resolve():
                    try:
                        choices=await self._xodus.resolve(product_id)
                        frame={'generation':generation,'choices':choices}
                    except asyncio.CancelledError:
                        frame={'generation':generation,'cancelled':True,'choices':[]}
                    except XodusInstallError as error:
                        frame={'generation':generation,'error':str(error),'choices':[]}
                    except Exception:
                        frame={'generation':generation,'error':'Store packages could not be checked. Nothing was downloaded.','choices':[]}
                    self._packageResult.emit(frame)
                self._worker.schedule(resolve());return True
            if provider=='steam':
                if operation!='install' or not valid_product('steam',product_id):raise ValueError('Invalid Steam product')
                self._plan=('steam',product_id)
                self._preview={'provider':'steam','product_id':product_id,'title':title,
                    'operation':'install','destination':'Choose an existing Steam library in Steam’s install confirmation.'}
                self._state={'phase':'prepared','percent':0,'message':'Steam must manage its own download. Start opens only the selected game’s install confirmation.'}
                self.changed.emit();return True
            self._storage=self._storage_probe(root)
            if not self._storage.get('ready'):
                self._state={'phase':'invalid','percent':0,'message':self._storage.get('message','Destination unavailable.')}
                self.changed.emit();return False
            env_keys={'epic':('LEGENDARY_CONFIG_PATH','epic/legendary'),
                      'gog':('GOGDL_CONFIG_PATH','gog/gogdlConfig'),
                      'amazon':('NILE_CONFIG_PATH','amazon')}
            key,suffix=env_keys[provider]
            config_path=str(self._account_config_root/suffix)
            tool_path=str(tool or '').strip()
            if config_path_override and Path(config_path_override).is_absolute():
                config_path=str(config_path_override)
            elif provider in self._managed_accounts:
                managed_tool,managed_cfg=self._managed_accounts[provider]
                if not tool_path:tool_path=managed_tool
                config_path=managed_cfg
                if provider=='gog' and not auth_path:auth_path=str(Path(config_path)/'auth.json')
            if not tool_path or not Path(tool_path).is_absolute():
                existing_cfg=getattr(self._manager,'_tool_configs',{}).get(provider)
                if existing_cfg is not None:
                    tool_path=str(existing_cfg.binary_path)
            self._manager.set_tool_config(provider,ProviderToolConfig(binary_path=Path(tool_path),
                env_overrides={key:config_path}))
            if provider=='gog' and not auth_path:
                auth_path=str(self._account_config_root/'gog/auth.json')
            folder_name=str(game_folder_override or product_id).strip()
            recipe=ProviderInstallRecipe(provider=provider,product_id=product_id,title=title,
                install_root=Path(root),operation=operation,game_folder=folder_name,
                platform=platform_override or None,
                auth_config_path=Path(auth_path) if auth_path else None)
            self._plan=self._manager.create_plan(recipe)
            self._preview={'provider':provider,'product_id':product_id,'title':title,'operation':operation,
                'destination':str(self._plan.destination_dir),'tool':str(self._plan.tool_binary)}
            self._state={'phase':'prepared','percent':0,'message':'No download started. Review the exact product and destination.'}
            ok=True
        except Exception:
            self._state={'phase':'invalid','percent':0,'message':'Invalid provider, product ID, tool or destination. Nothing was started.'}
            ok=False
        self.changed.emit();return ok

    @Slot(dict)
    def _on_packages(self,frame):
        if frame.get('generation')!=self._generation:return
        self._busy=False;self._target=None;self._packages=frame.get('choices',[])
        if frame.get('cancelled'):
            self._package_scope=None;self._state={'phase':'cancelled','percent':0,'message':'Package check cancelled.'}
        elif frame.get('error'):
            self._package_scope=None;self._state={'phase':'failed','percent':0,'message':frame['error']}
        elif len(self._packages)==1:
            self.choosePackage(self._packages[0]['content_id']);return
        elif not self._packages:
            self._package_scope=None;self._state={'phase':'unsupported','percent':0,'message':'No supported standalone PC game package was found. Console-only games and parent-bound DLC are not installed as base games.'}
        else:self._state={'phase':'choose-package','percent':0,'message':'Choose the PC game package to install. No download has started.'}
        self.changed.emit()

    @Slot(str,result=bool)
    def choosePackage(self,content_id):
        if self._busy or self._package_scope is None or self._xodus is None:return False
        choice=next((c for c in self._packages if c['content_id']==content_id),None)
        if choice is None:return False
        product,title,root=self._package_scope
        self._plan=None;self._preview={}
        try:
            self._plan=self._xodus.plan(product,choice,root,title)
            self._preview={'provider':'xbox','product_id':product,'title':title or choice['title'],
                'operation':'install','destination':str(self._plan.destination),'tool':str(self._plan.binary),
                'content_id':content_id,'required_bytes':self._plan.required_bytes}
            self._state={'phase':'prepared','percent':0,'message':'PC Store package checked. Review the selected package and drive, then Start installation.'}
            ok=True
        except XodusInstallError as error:
            self._state={'phase':'invalid','percent':0,'message':str(error)};ok=False
        self.changed.emit();return ok

    @Slot(result=bool)
    def start(self):
        if self._busy or self._plan is None:return False
        if isinstance(self._plan,XodusInstallPlan):
            plan=self._plan
            if not self._ensure_worker():return False
            try:self._preferences.save(str(plan.root));self._root=str(plan.root)
            except (OSError,ValueError):
                self._state={'phase':'failed','percent':0,'message':'Could not save the install destination. Nothing was started.'};self.changed.emit();return False
            self._busy=True;self._generation+=1;generation=self._generation
            self._target=('xbox',plan.product_id)
            self._state={'phase':'starting','percent':0,'message':'Starting Xodus download in Waystone…'};self.changed.emit()
            async def run_xbox():
                try:
                    receipt=await self._xodus.execute(plan,lambda p:self._progress.emit({'generation':generation,'progress':asdict(p)}))
                    state={'phase':'completed','percent':100,'message':'Xbox package files verified. The configured Xodus runtime is required to play.','receipt':receipt}
                except asyncio.CancelledError:state={'phase':'cancelled','percent':0,'message':'Xbox download cancelled. Partial files were retained for resume.'}
                except XodusInstallError as error:state={'phase':'failed','percent':0,'message':str(error)}
                except Exception:state={'phase':'failed','percent':0,'message':'Xbox installation failed. Partial files were retained; check the existing Xodus account and destination.'}
                self._result.emit({'generation':generation,'progress':state})
            self._worker.schedule(run_xbox());return True
        if isinstance(self._plan,tuple) and self._plan[0]=='steam':
            product=self._plan[1];self._plan=None;self._preview={}
            try:
                dispatch=self._steam_dispatch
                if dispatch is None:
                    from PySide6.QtCore import QUrl
                    from PySide6.QtGui import QDesktopServices
                    dispatch=lambda uri: QDesktopServices.openUrl(QUrl(uri))
                if not valid_product('steam',product) or not dispatch('steam://install/'+product):raise ValueError('Handoff refused')
                self._state={'phase':'handed_off','percent':None,'message':'Steam install confirmation requested. This is not an installed-game confirmation. Refresh installed games after Steam finishes.'}
                ok=True
            except Exception:
                self._state={'phase':'failed','percent':0,'message':'Steam could not receive the install request. Check that the Steam client is installed.'};ok=False
            self.changed.emit();return ok
        self._storage=self._storage_probe(str(self._plan.recipe.install_root))
        if not self._storage.get('ready'):
            self._plan=None;self._preview={}
            self._state={'phase':'invalid','percent':0,'message':'The selected drive is no longer writable or has no free space. Nothing was started.'}
            self.changed.emit();return False
        import os
        if not self._plan.tool_binary.is_file() or not os.access(self._plan.tool_binary,os.X_OK):
            self._state={'phase':'failed','percent':0,'message':'The native provider download tool is not installed or executable.'}
            self._plan=None;self._preview={};self.changed.emit();return False
        if not self._ensure_worker():return False
        if self._plan.recipe.operation=='install':
            try:self._preferences.save(str(self._plan.recipe.install_root));self._root=str(self._plan.recipe.install_root)
            except (OSError,ValueError):
                self._state={'phase':'failed','percent':0,'message':'Could not save the install destination. Nothing was started.'};self.changed.emit();return False
        self._busy=True;self._generation+=1
        generation=self._generation;plan=self._plan
        expected_device=self._storage.get('filesystem_device')
        self._target=(plan.recipe.provider,plan.recipe.product_id)
        self._state={'phase':'starting','percent':0,'message':'Starting the selected native provider job…'}
        self.changed.emit()
        async def run():
            try:
                result=await self._manager.execute_job(plan,lambda p:self._progress.emit({'generation':generation,'progress':asdict(p)}))
                state=asdict(result)
                if (self._receipt_registry is not None and plan.recipe.provider=='gog'
                        and state.get('phase')=='completed' and type(state.get('exit_code')) is int
                        and state['exit_code']==0):
                    try:
                        from native_services.gog_local_receipt import verify_selected_gog_directory
                        receipt,reason=verify_selected_gog_directory(
                            plan.recipe.product_id,plan.destination_dir,platform=plan.recipe.platform,
                            host=self._receipt_registry.host)
                        if (receipt is not None and type(expected_device) is int
                                and receipt.get('filesystem_device')!=expected_device):
                            receipt=None;reason='destination_device_changed'
                    except Exception:
                        receipt=None;reason='local_metadata_unusable'
                    if receipt is not None:
                        state['receipt']=receipt
                        state['message']='GOG files verified on this device. Runtime/launch configuration remains separate.'
                    elif plan.recipe.operation=='update':
                        state['message']='Update completed.'
                    else:
                        state['verification_reason']=reason
                        state['message']='Download completed, but installed files could not yet be verified ('+reason+').'
                elif (plan.recipe.provider in RECEIPT_PROVIDERS and plan.recipe.provider not in ('xbox','gog')
                        and state.get('phase')=='completed' and state.get('exit_code')==0):
                    try:
                        dest=Path(plan.destination_dir).resolve()
                        candidates=[]
                        if dest.is_dir():
                            for pat in ('*.exe','*.bin','*.x86_64'):
                                candidates.extend(p for p in dest.rglob(pat) if p.is_file())
                            if not candidates:
                                candidates=[p for p in dest.rglob('*') if p.is_file()]
                        executable=candidates[0].resolve() if candidates else None
                        if executable is not None and executable.is_relative_to(dest) and executable.exists():
                            host=getattr(self._receipt_registry,'host',None) if self._receipt_registry is not None else None
                            if not host:
                                import socket
                                host=socket.gethostname()
                            state['receipt']={
                                'provider': plan.recipe.provider,
                                'product_id': str(plan.recipe.product_id),
                                'host': str(host),
                                'install_path': str(dest),
                                'executable': str(executable),
                                'state': 'files_verified',
                            }
                        elif plan.recipe.operation=='update':
                            state['message']='Update completed.'
                        else:
                            state['verification_reason']='no_executable_found'
                            state['message']='Download completed, but no executable could be located for receipt generation.'
                    except Exception:
                        if plan.recipe.operation=='update':
                            state['message']='Update completed.'
                        else:
                            state['verification_reason']='receipt_generation_failed'
                            state['message']='Download completed, but install receipt generation failed.'
            except asyncio.CancelledError:
                state={'phase':'cancelled','percent':0,'message':'Download cancelled.'}
            except Exception:
                state={'phase':'failed','percent':0,'message':'Provider job failed. Check its account connection and destination.'}
            self._result.emit({'generation':generation,'progress':state})
        self._worker.schedule(run());return True

    @Slot()
    def cancel(self):
        if not self._busy or self._worker is None or self._target is None:return
        if self._state.get('phase')=='cancelling':return
        provider,product=self._target
        self._state={'phase':'cancelling','percent':self._state.get('percent',0),'message':'Stopping this download only…'}
        self.changed.emit()
        self._worker.schedule(self._xodus.cancel() if provider=='xbox' else self._manager.cancel_job(provider,product))

    @Slot(dict)
    def _on_progress(self,frame):
        if frame.get('generation')!=self._generation or not self._busy:return
        self._state=dict(frame['progress']);self.changed.emit()

    @Slot(dict)
    def _on_result(self,frame):
        if frame.get('generation')!=self._generation:return
        target=self._target
        self._state=dict(frame['progress']);self._busy=False;self._target=None
        receipt=self._state.pop('receipt',{})
        self._install_receipt=receipt if isinstance(receipt,dict) else {}
        self._xbox_receipt=self._install_receipt if self._install_receipt.get('provider')=='xbox' else {}
        if (self._receipt_registry is not None and self._state.get('phase')=='completed'
                and target and target[0] in RECEIPT_PROVIDERS and isinstance(receipt,dict)):
            if receipt.get('provider')==target[0] and receipt.get('product_id')==target[1]:
                try:
                    if target[0]=='xbox':self._receipt_registry.register_xodus_verified(receipt)
                    else:self._receipt_registry.register(receipt)
                except (ValueError,OSError,RuntimeError):
                    self._state['registration_warning']='Files downloaded, but installation history could not be saved. Retry local verification before relying on installed status.'
                    self._state['message']=(self._state.get('message','Files downloaded.')+' '+self._state['registration_warning'])
                else:
                    self.refreshInstalledRequested.emit()
        self._plan=None;self._preview={};self.changed.emit()
        self.finished.emit(self._state.get('phase')=='completed')

    @Slot(result=bool)
    def shutdown(self):
        self._library_epoch+=1;self._loading=False
        if self._worker is not None:
            if self._worker.loop:
                target=self._target
                async def clean():
                    current=asyncio.current_task()
                    controls=[t for t in asyncio.all_tasks() if t is not current and
                        getattr(t.get_coro(),'__qualname__','') in ('XodusInstaller.cancel','ProviderJobManager.cancel_job')]
                    if controls:await asyncio.gather(*controls,return_exceptions=True)
                    if target:
                        if target[0]=='xbox' and self._xodus:await self._xodus.cancel()
                        else:await self._manager.cancel_job(*target)
                    jobs=[t for t in asyncio.all_tasks() if t is not current and
                        getattr(t.get_coro(),'__qualname__','').startswith('ProviderJobView.')]
                    for task in jobs:task.cancel()
                    if jobs:await asyncio.gather(*jobs,return_exceptions=True)
                future=asyncio.run_coroutine_threadsafe(clean(),self._worker.loop)
                try:future.result(timeout=15.0)
                except Exception:return False  # Do not destroy a loop while its owned child is still reaping.
            self._worker.stop();self._worker=None
        self._generation+=1;self._busy=False;self._target=None
        self._plan=None;self._preview={};return True
