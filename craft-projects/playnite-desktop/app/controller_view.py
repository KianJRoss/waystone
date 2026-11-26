"""Waystone's embedded UnifiedController controls, attached to the one native engine."""
import json
import re
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread

class _MutationUnconfirmed(Exception):
    code="ERR_CONTROLLER_VERIFY_FAILED"
    uncertain=True

class _Job(QThread):
    def __init__(self,fn,parent):
        super().__init__(parent);self.fn=fn;self.result=None
    def run(self):
        try:self.result={'ok':True,'data':self.fn()}
        except Exception as e:
            code=getattr(e,'code','ERR_CONTROLLER_UNAVAILABLE')
            self.result={'ok':False,'code':code if isinstance(code,str) and re.fullmatch(r'ERR_[A-Z0-9_]{1,60}',code) else 'ERR_CONTROLLER_UNAVAILABLE',
                         'uncertain':getattr(e,'uncertain',False) is True}

class ControllerView(QObject):
    changed=Signal()
    operationFinished=Signal(bool)
    activeProfileObserved=Signal(str)
    def __init__(self,client=None,parent=None):
        super().__init__(parent);self._client=client;self._job=None;self._closed=False
        self._available=False;self._devices=[];self._names=[];self._active=self._editing=self._revision=''
        self._profile={};self._status='Refresh to connect to the existing UnifiedController engine.'
        self._uncertain=False;self._expectation=None;self._operation='';self._conflict_reload=False
        self._mutation_admission=None
    @Property(bool,notify=changed)
    def busy(self):return self._job is not None
    @Property(bool,notify=changed)
    def available(self):return self._available
    @Property(bool,notify=changed)
    def uncertain(self):return self._uncertain
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property('QStringList',notify=changed)
    def profiles(self):return list(self._names)
    @Property('QVariantList',notify=changed)
    def devices(self):return [dict(d) for d in self._devices]
    @Property(str,notify=changed)
    def activeProfile(self):return self._active
    @Property(str,notify=changed)
    def editingProfile(self):return self._editing
    @Property(str,notify=changed)
    def padType(self):
        """Virtual pad type saved in the profile being edited ('' = follows the default type)."""
        value=self._profile.get('virtual_gamepad_driver') if self._editing else None
        return value if isinstance(value,str) else ''
    @Slot(str)
    def setPadType(self,pad_type):
        """Save this profile's virtual pad type ('' = use the default) through the engine, read back."""
        from native_services.unified_controller_control import VIRTUAL_PAD_TYPES
        if pad_type and pad_type not in VIRTUAL_PAD_TYPES and pad_type!='passthrough':return
        self.saveProfile(json.dumps({'virtual_gamepad_driver':pad_type or None}))
    @Property(str,notify=changed)
    def profileJson(self):return json.dumps(self._profile,ensure_ascii=False,indent=2) if self._editing else ''
    def _request(self,operation,**fields):
        if self._client is None:
            from native_services.unified_controller_control import ControllerControlClient
            self._client=ControllerControlClient()
        return self._client.request(operation,**fields)
    def _read(self,name=None):
        status=self._request('status');profiles=self._request('profiles')
        selected=name or profiles.get('active_profile','')
        profile=self._request('profile_get',name=selected) if selected else {}
        return {'status':status,'profiles':profiles,'profile':profile}
    def _start(self,operation,fn):
        if self.busy or self._closed:return False
        self._operation=operation;self._job=_Job(fn,self)
        self._job.finished.connect(self._finished)
        self._job.start();self.changed.emit();return True
    @Slot()
    def refresh(self):
        if self._start('read',lambda:self._read()):
            self._status='Reading the existing controller engine…';self.changed.emit()
    @Slot(str)
    def loadProfile(self,name):
        if name not in self._names:return
        self._start('read',lambda:self._read(name))
    @Slot(str)
    def reloadProfile(self, name):
        # Explicit local lifecycle changes may add/rename a profile after our
        # last inventory. Read it via the same backend without activating it or
        # changing an unrelated editor draft; caller guards its dirty state.
        if not isinstance(name, str) or not name or len(name) > 256 or any(ord(c) < 32 for c in name): return
        self._start('read', lambda: self._read(name))

    def setMutationAdmission(self, allowed):
        # Job-scoped shared runtime guard. Read-only editor refreshes stay usable
        # to reconcile a lost native reply; rejected writes retain the UI draft.
        if allowed is not None and not callable(allowed): raise TypeError('mutation admission must be callable')
        self._mutation_admission = allowed

    def _admitMutation(self):
        try: allowed = self._mutation_admission is None or self._mutation_admission() is True
        except Exception: allowed = False
        if not allowed:
            self._status='Native profile operation pending or uncertain. Refresh before saving or selecting another profile.'
            self.changed.emit();self.operationFinished.emit(False)
        return allowed

    @Slot()
    def useProfile(self):
        if not self._admitMutation(): return
        if self.busy or not self._available or self._uncertain or not self._editing:return
        name,revision=self._editing,self._revision
        self._expectation=('select',name,None)
        def work():
            self._request('profile_select',name=name,expected_revision=revision)
            try:return self._read(name)
            except Exception:raise _MutationUnconfirmed() from None
        self._start('mutation',work)
    @Slot(str)
    def saveProfile(self,raw):
        if not self._admitMutation(): return
        if self.busy or not self._available or self._uncertain or not self._editing:return
        try:
            if len(raw.encode('utf-8'))>100000:raise ValueError()
            profile=json.loads(raw)
            if not isinstance(profile,dict):raise ValueError()
        except (ValueError,TypeError,UnicodeError):
            self._status='Profile must be a JSON object within 100 KB. Nothing was saved.'
            self.changed.emit();return
        name,revision=self._editing,self._revision
        # Native profile_update is a shallow top-level merge, preserving opaque
        # fields omitted by an explicit partial editor object.
        self._expectation=('update',name,{**self._profile,**profile})
        def work():
            self._request('profile_update',name=name,profile=profile,expected_revision=revision)
            try:return self._read(name)
            except Exception:raise _MutationUnconfirmed() from None
        self._start('mutation',work)
    @Slot()
    def cancelMacros(self):
        if self._available:self._start('stop',lambda:self._request('cancel_macros'))
    @Slot()
    def _finished(self):
        job=self._job
        if job is None:return
        result=job.result or {'ok':False,'code':'ERR_CONTROLLER_UNAVAILABLE'}
        completed_operation=self._operation
        conflict_reloaded=False
        self._job=None;job.deleteLater()
        if self._closed:return
        if not result['ok']:
            if self._operation=='mutation' and result.get('uncertain'):
                self._uncertain=True
                self._status='Controller reply was lost. No retry was sent. Refresh to reconcile the actual profile.'
            elif result.get('code')=='ERR_REVISION_CONFLICT':
                self._conflict_reload=True
                name=self._editing
                if self._start('read',lambda:self._read(name)):
                    self._status='Profile changed elsewhere. Reloading the latest profile; nothing was overwritten.'
                    self.changed.emit()
                    return
                self._conflict_reload=False
                self._status='Profile changed elsewhere. Refresh before editing again; nothing was overwritten.'
            else:
                code=result.get('code','ERR_CONTROLLER_UNAVAILABLE')
                self._status=('Unified Controller engine is unavailable. Start it to edit controller settings.'
                              if code in ('ERR_NOT_AVAILABLE','ERR_CONTROLLER_UNAVAILABLE')
                              else 'Controller request failed ('+code+').')
                if self._operation=='read':self._available=False
        elif self._operation=='stop':
            self._status='Stop-all-macros command accepted by the controller engine.'
        else:
            try:
                data=result['data'];status=data['status'];profiles=data['profiles'];profile=data['profile']
                names=profiles['names'];active=profiles['active_profile'];revision=profile.get('revision',profiles['revision'])
                name=profile['name'];payload=profile['profile']
                if not isinstance(names,list) or not names or len(names)>256 or not all(isinstance(n,str) and 0<len(n)<=256 for n in names):raise ValueError()
                if active not in names or name not in names or not isinstance(payload,dict) or not isinstance(revision,str) or not re.fullmatch(r'[a-fA-F0-9]{64}',revision):raise ValueError()
                self._names=list(names);self._active=active;self._editing=name;self._profile=payload;self._revision=revision
                self._available=status.get('running') is True
                devices=status.get('devices',[])
                self._devices=[{'id':d.get('id',''),'name':d['name'],'connected':d.get('connected') is True,'virtual':d.get('virtual') is True}
                    for d in devices[:64] if isinstance(d,dict) and isinstance(d.get('name'),str) and len(d['name'])<=512] if isinstance(devices,list) else []
                if self._expectation and (self._uncertain or self._operation=='mutation'):
                    kind,target,expected=self._expectation
                    if (kind=='select' and active==target) or (kind=='update' and name==target and payload==expected):
                        self._uncertain=False;self._expectation=None
                    elif self._operation=='mutation': self._uncertain=True
                self._status=('Controller connected · active profile: '+active) if self._available else 'Controller engine is stopped.'
                if self._uncertain:self._status='Outcome still unconfirmed. Mutations remain blocked; no operation was replayed.'
                elif self._operation=='mutation':self._expectation=None;self._status='Controller change saved and read back from the native engine.'
                conflict_reloaded=self._conflict_reload and self._operation=='read'
                if conflict_reloaded:
                    self._conflict_reload=False
                    self._expectation=None
                    self._status='Profile changed elsewhere. Reloaded the latest profile; review it before saving.'
                self.activeProfileObserved.emit(active)
            except (KeyError,ValueError,TypeError):
                if self._operation=='mutation': self._uncertain=True
                self._available=False;self._status='Controller returned an invalid profile response. No fallback engine was started.'
        self.changed.emit()
        if completed_operation=='read' and not result['ok'] and self._conflict_reload:
            self._status='Profile changed elsewhere, but reloading failed. The staged draft was kept; refresh before saving.'
            self.changed.emit()
        elif conflict_reloaded:
            self.operationFinished.emit(False)
        elif completed_operation=='mutation' and self._operation=='mutation':
            self.operationFinished.emit(result['ok'] and self._available and not self._uncertain and self._expectation is None)
    @Slot()
    def shutdown(self):
        self._closed=True
        if self._job is not None:self._job.wait();self._job.deleteLater();self._job=None
