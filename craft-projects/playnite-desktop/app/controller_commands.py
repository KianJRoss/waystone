"""Typed async commands for native controller pages; queued is not completed."""
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread
from native_services.controller_command_protocol import OPERATIONS, validate

class _CommandJob(QThread):
    def __init__(self, client, op, fields, parent):
        super().__init__(parent);self.client=client;self.op=op;self.fields=fields;self.result=None
    def run(self):
        try:self.result=(True,self.client.request(self.op,**self.fields),False)
        except Exception as exc:self.result=(False,None,getattr(exc,'uncertain',False) is True)

class ControllerCommands(QObject):
    changed=Signal()
    operationFinished=Signal(str,bool)
    def __init__(self,client=None,parent=None):
        super().__init__(parent);self._client=client;self._job=None;self._caps=[]
        self._status='Refresh native command capabilities.';self._uncertain=False;self._closed=False
    @Property(bool,notify=changed)
    def busy(self):return self._job is not None
    @Property(bool,notify=changed)
    def available(self):return bool(self._caps)
    @Property('QStringList',notify=changed)
    def capabilities(self):return list(self._caps)
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(bool,notify=changed)
    def uncertain(self):return self._uncertain
    def _start(self,op,**fields):
        if self._closed or self.busy:return False
        if op != 'command_capabilities' and (op not in self._caps or (self._uncertain and op != 'rumble_stop')):
            self._status='Command unavailable or previous outcome uncertain; no action was sent.';self.changed.emit();return False
        try:validate(op,fields)
        except Exception:
            self._status='Invalid command parameters. No action was sent.';self.changed.emit();return False
        if self._client is None:
            from native_services.controller_command_protocol import ControllerCommandClient
            self._client=ControllerCommandClient()
        self._job=_CommandJob(self._client,op,fields,self)
        self._job.finished.connect(self._finished);self._job.start()
        self._status='Querying controller capabilities…' if op=='command_capabilities' else 'Sending native command…'
        self.changed.emit();return True
    @Slot()
    def refreshCapabilities(self):self._start('command_capabilities')
    @Slot()
    def recenterMotion(self):self._start('motion_recenter')
    @Slot()
    def calibrateMotion(self):self._start('motion_calibrate')
    @Slot(float,float,int)
    def testRumble(self,heavy,light,duration_ms):self._start('rumble_test',heavy=heavy,light=light,duration_ms=duration_ms)
    @Slot()
    def stopRumble(self):self._start('rumble_stop')
    @Slot()
    def acknowledgeUncertain(self):
        # Explicit USER acknowledgment of possible earlier effect, not proof it completed.
        if self.busy:return
        self._uncertain=False;self._status='Uncertain outcome acknowledged. No command was replayed.';self.changed.emit()
    @Slot()
    def _finished(self):
        job=self._job
        if job is None:return
        self._job=None;ok,data,uncertain=job.result or (False,None,False);op=job.op;job.deleteLater()
        if self._closed:return
        if ok and op=='command_capabilities':
            ok=isinstance(data,dict) and type(data.get('schema_version')) is int and data.get('schema_version')==1 and type(data.get('operations')) is list and all(type(x) is str and x in OPERATIONS for x in data['operations'])
            self._caps=sorted(set(data['operations'])) if ok else []
            self._status='Native command capabilities refreshed.' if ok else 'Invalid capabilities; commands unavailable.'
        elif ok:
            ok=isinstance(data,dict) and data.get('accepted') is True and data.get('operation')==op and data.get('completion')=='queued'
            if ok:self._status='Native command accepted and queued; physical completion is not verified.'
            else:uncertain=True
        if not ok:
            if op=='command_capabilities':self._caps=[]
            self._uncertain=self._uncertain or uncertain
            self._status='Native command outcome uncertain. No retry was sent.' if uncertain else 'Native command unavailable or rejected. No automatic retry.'
        self.changed.emit();self.operationFinished.emit(op,ok)
    def shutdown(self):
        self._closed=True
        if self._job:
            self._job.wait(31000)
            if self._job.isRunning():raise RuntimeError('Controller command is still settling')

class _RefreshProfiles(QThread):
    def __init__(self,client,parent):super().__init__(parent);self.client=client;self.result=None;self.op='profile_command_capabilities'
    def run(self):
        try:
            caps=self.client.request('profile_command_capabilities')
            profiles=self.client.request('profiles')
            self.result=(True,{'caps':caps,'profiles':profiles},False)
        except Exception:self.result=(False,None,False)

class ControllerProfileCommands(QObject):
    """Additive lifecycle/settings commands with CAS, never full-profile replacement."""
    changed=Signal()
    operationFinished=Signal(str,bool)
    def __init__(self,client=None,parent=None):
        super().__init__(parent);self._client=client;self._job=None;self._closed=False
        self._caps=[];self._names=[];self._revision='';self._active='';self._uncertain=False
        self._export='';self._status='Refresh native profile command capabilities.'
        self._mutation_admission=None
    @Property(bool,notify=changed)
    def busy(self):return self._job is not None
    @Property(bool,notify=changed)
    def available(self):return bool(self._caps)
    @Property('QStringList',notify=changed)
    def capabilities(self):return list(self._caps)
    @Property('QStringList',notify=changed)
    def profiles(self):return list(self._names)
    @Property(str,notify=changed)
    def activeProfile(self):return self._active
    @Property(str,notify=changed)
    def revision(self):return self._revision
    @Property(str,notify=changed)
    def exportDocument(self):return self._export
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(bool,notify=changed)
    def uncertain(self):return self._uncertain
    def _ensure_client(self):
        if self._client is None:
            from native_services.controller_command_protocol import ControllerProfileCommandClient
            self._client=ControllerProfileCommandClient()
    @Slot()
    def refreshProfiles(self):
        if self.busy or self._closed:return
        self._ensure_client();self._job=_RefreshProfiles(self._client,self)
        self._job.finished.connect(self._finished);self._job.start();self.changed.emit()
    def setMutationAdmission(self, allowed):
        if allowed is not None and not callable(allowed): raise TypeError('mutation admission must be callable')
        self._mutation_admission=allowed
    def _send(self,op,**fields):
        if self.busy or self._closed:return
        if op!='profile_export' and self._mutation_admission is not None:
            try: allowed=self._mutation_admission() is True
            except Exception: allowed=False
            if not allowed:
                self._status='Native profile operation pending or uncertain. No mutation sent.'
                self.changed.emit();self.operationFinished.emit(op,False);return
        if op not in self._caps or (self._uncertain and op!='profile_export'):
            self._status='Profile command unavailable or previous outcome uncertain. No mutation sent.';self.changed.emit();return
        fields['expected_revision']=self._revision
        try:validate(op,fields)
        except Exception:
            self._status='Invalid profile command. Nothing was written.';self.changed.emit();return
        if op=='profile_export':self._export=''
        self._ensure_client();self._job=_CommandJob(self._client,op,fields,self)
        self._job.finished.connect(self._finished);self._job.start();self.changed.emit()
    @Slot(str)
    def createProfile(self,name):self._send('profile_create',name=name)
    @Slot(str,str)
    def copyProfile(self,source,name):self._send('profile_copy',source=source,name=name)
    @Slot(str,str)
    def renameProfile(self,source,name):self._send('profile_rename',source=source,name=name)
    @Slot(str)
    def deleteProfile(self,name):self._send('profile_delete',name=name)
    @Slot(str,str)
    def importProfile(self,name,raw):
        import json
        try:
            if len(raw.encode())>100000:raise ValueError()
            document=json.loads(raw)
        except Exception:self._status='Invalid import JSON; nothing was written.';self.changed.emit();return
        self._send('profile_import',name=name,document=document)
    @Slot(str)
    def exportProfile(self,name):
        self._export='';self.changed.emit();self._send('profile_export',name=name)
    @Slot(str,str,'QVariant')
    def setField(self,name,field,value):self._send('profile_set_field',name=name,field=field,value=value)
    @Slot()
    def acknowledgeUncertain(self):
        if not self.busy:self._uncertain=False;self._status='Possible earlier effect acknowledged; nothing replayed.';self.changed.emit()
    @Slot()
    def _finished(self):
        import json,re
        from native_services.controller_command_protocol import PROFILE_OPERATIONS
        job=self._job
        if job is None:return
        self._job=None;ok,data,uncertain=job.result or (False,None,False);op=job.op;job.deleteLater()
        if self._closed:return
        if ok and op=='profile_command_capabilities':
            try:
                caps=data['caps'];profiles=data['profiles']
                assert type(caps['schema_version']) is int and caps['schema_version']==1
                assert type(caps['operations']) is list and all(type(x) is str and x in PROFILE_OPERATIONS for x in caps['operations'])
                assert re.fullmatch('[0-9a-f]{64}',profiles['revision'])
                assert type(profiles['names']) is list and all(type(n) is str for n in profiles['names'])
                assert profiles['active_profile'] in profiles['names']
                self._caps=sorted(set(caps['operations']));self._names=list(profiles['names'])
                self._revision=profiles['revision'];self._active=profiles['active_profile']
                self._status='Native profile capabilities and current revision refreshed.'
            except Exception:ok=False;self._caps=[]
        elif ok:
            try:
                assert type(data) is dict and re.fullmatch('[0-9a-f]{64}',data['revision'])
                assert data.get('name')==job.fields['name']
                if op=='profile_export':
                    assert type(data['document']) is dict
                    self._export=json.dumps(data['document'],ensure_ascii=False,indent=2)
                    self._status='Profile exported as a document; choose a destination in Waystone to save it.'
                else:
                    assert data.get('committed') is True
                    self._revision=data['revision'];self._active=data['active_profile']
                    self._status='Profile change durably committed; refresh to update profile list.'
            except Exception:ok=False;uncertain=op!='profile_export'
        if not ok:
            if op=='profile_command_capabilities':self._caps=[]
            self._uncertain=self._uncertain or uncertain
            self._status='Profile outcome uncertain; no retry was sent.' if uncertain else 'Profile command unavailable or rejected; nothing retried.'
        self.changed.emit();self.operationFinished.emit(op,ok)
    def shutdown(self):
        self._closed=True
        if self._job:
            self._job.wait(31000)
            if self._job.isRunning():raise RuntimeError('Profile operation is still settling')

class ControllerDeviceCommands(QObject):
    changed=Signal()
    operationFinished=Signal(str,bool)
    def __init__(self,client=None,parent=None):
        super().__init__(parent);self._client=client;self._job=None;self._closed=False
        self._caps=[];self._pairs=[];self._capture=False;self._uncertain=False
        self._status='Refresh native device capabilities. Source selection is automatic.'
    @Property(bool,notify=changed)
    def busy(self):return self._job is not None
    @Property('QStringList',notify=changed)
    def capabilities(self):return list(self._caps)
    @Property('QVariantList',notify=changed)
    def pairs(self):return [dict(p) for p in self._pairs]
    @Property(bool,notify=changed)
    def exclusiveCapture(self):return self._capture
    @Property(bool,notify=changed)
    def uncertain(self):return self._uncertain
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(bool,constant=True)
    def deadbandAvailable(self):
        import shutil,os
        return bool(shutil.which('deadband') or os.access(os.path.expanduser('~/.local/bin/deadband'),os.X_OK))
    @Slot()
    def openDeadband(self):
        import shutil,os,subprocess
        exe=shutil.which('deadband') or os.path.expanduser('~/.local/bin/deadband')
        try:
            subprocess.Popen([exe],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            self._status='Deadband opened.'
        except Exception:
            self._status='Could not open Deadband.'
        self.changed.emit()
    def _send(self,op,**fields):
        if self._closed or self.busy:return
        if op!='device_command_capabilities' and (op not in self._caps or self._uncertain):
            self._status='Device command unavailable or previous result uncertain. Nothing sent.';self.changed.emit();return
        try:validate(op,fields)
        except Exception:self._status='Invalid device command; nothing sent.';self.changed.emit();return
        if self._client is None:
            from native_services.controller_command_protocol import ControllerCommandClient
            self._client=ControllerCommandClient()
        self._job=_CommandJob(self._client,op,fields,self)
        self._job.finished.connect(self._finished);self._job.start();self.changed.emit()
    @Slot()
    def refreshDevices(self):self._send('device_command_capabilities')
    @Slot(str,str)
    def pairJoyCons(self,left,right):self._send('joycon_pair',left_id=left,right_id=right)
    @Slot(str)
    def unpairJoyCons(self,pair):self._send('joycon_unpair',pair_id=pair)
    @Slot(bool)
    def setExclusiveCapture(self,value):self._send('device_capture',enabled=value)
    @Slot()
    def acknowledgeUncertain(self):
        if not self.busy:self._uncertain=False;self.changed.emit()
    @Slot()
    def _finished(self):
        from native_services.controller_command_protocol import DEVICE_OPERATIONS
        job=self._job
        if job is None:return
        self._job=None;ok,data,uncertain=job.result or (False,None,False);op=job.op;job.deleteLater()
        if self._closed:return
        if ok:
            try:
                assert type(data) is dict and type(data.get('pairs')) is list
                assert all(type(p) is dict and all(type(p.get(k)) is str for k in ('id','left_id','right_id','name')) for p in data['pairs'])
                if op=='device_command_capabilities':
                    assert type(data['schema_version']) is int and data['schema_version']==1
                    assert type(data['operations']) is list and all(type(x) is str and x in DEVICE_OPERATIONS for x in data['operations'])
                    self._caps=sorted(set(data['operations']))
                    assert type(data.get('exclusive_capture')) is bool or data.get('exclusive_capture') is None
                else:
                    assert data.get('accepted') is True and data.get('operation')==op and data.get('completion')=='provider_state_verified'
                self._pairs=[dict(p) for p in data['pairs']];self._capture=data.get('exclusive_capture') is True
                self._status='Device provider state read back. Physical input behavior is not independently verified.'
            except Exception:ok=False;uncertain=op!='device_command_capabilities'
        if not ok:
            if op=='device_command_capabilities':self._caps=[]
            self._uncertain=self._uncertain or uncertain
            self._status='Device result uncertain; no retry.' if uncertain else 'Device command unavailable or rejected.'
        self.changed.emit();self.operationFinished.emit(op,ok)
    def shutdown(self):
        self._closed=True
        if self._job:
            self._job.wait(31000)
            if self._job.isRunning():raise RuntimeError('Device command still settling')
