"""System-wide emulator management using the SAME launch profiles as Play."""
import json,os,platform
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import QObject,Property,Signal,Slot
from native_services.emulator_registry import EmulatorRegistry
from native_services.launch_profiles import LaunchProfile,LaunchProfileStore,substitute_tokens


class EmulationView(QObject):
    changed=Signal()
    profilesCommitted=Signal()
    def __init__(self,profile_root=None,parent=None):
        super().__init__(parent)
        self._error=''
        try:self._store=LaunchProfileStore(profile_root) if profile_root else None
        except (ValueError,OSError):
            self._store=None;self._error='Emulator profiles could not be loaded; existing configuration was preserved'
        self._registry=EmulatorRegistry()
    @Property(bool,notify=changed)
    def enabled(self):return self._store is not None
    @Property(str,notify=changed)
    def error(self):return self._error
    @Property('QVariantList',notify=changed)
    def systems(self):return [p.to_dict() for p in self._registry.list_catalog_platforms()]
    @Property('QVariantList',notify=changed)
    def profiles(self):
        if not self._store:return []
        result=[]
        for p in self._store.list_profiles(target_type='rom'):
            exe=Path(p.executable)
            result.append({'id':p.profile_id,'name':p.display_name,'platform':p.platform_or_provider,
                'platforms':list(p.supported_platforms),'executable':p.executable,
                'argsJson':json.dumps(list(p.argv_template)),'cwd':p.cwd or '',
                'installed':exe.is_file() and os.access(exe,os.X_OK),
                'default':self._store.default_associations.get(p.platform_or_provider)==p.profile_id,
                'compatibility':'Per-game compatibility not verified'})
        return result
    @Slot()
    def refresh(self):
        try:
            if self._store:self._store.load()
            self._error=''
        except Exception:self._error='Emulator profiles could not be loaded'
        self.changed.emit()
    @Slot(str,str,str,str,str,str,result=bool)
    def saveProfile(self,profile_id,name,system,executable,args_json,cwd=''):
        if not self._store:return False
        try:
            # A different controller may have committed before the watcher fires.
            # Never persist this view's older full-store snapshot over that edit.
            self._store.load()
            if not all(isinstance(v,str) and v and len(v)<=512 for v in (profile_id,name,system)):
                raise ValueError('Profile ID, name and system are required')
            exe=Path(executable)
            if not exe.is_absolute() or not exe.is_file() or not os.access(exe,os.X_OK):
                raise ValueError('Select an existing executable emulator path')
            args=json.loads(args_json)
            if not isinstance(args,list) or len(args)>64 or any(not isinstance(a,str) or len(a)>4096 or '\0' in a for a in args):
                raise ValueError('Arguments must be a JSON array of strings')
            if cwd and (not Path(cwd).is_absolute() or not Path(cwd).is_dir()):
                raise ValueError('Working directory must exist')
            substitute_tokens(tuple(args),target_id='probe',target_path='/tmp/probe',executable=executable)
            existing=self._store.get_profile(profile_id)
            if existing and existing.target_type!='rom':raise ValueError('This profile belongs to a native game launcher')
            if existing and system!=existing.platform_or_provider:
                raise ValueError('Create a new profile to change systems without breaking existing ROM associations')
            p=replace(existing,display_name=name,executable=executable,
                argv_template=tuple(args),cwd=cwd or None) if existing else LaunchProfile(profile_id=profile_id,display_name=name,target_type='rom',
                platform_or_provider=system,executable=executable,argv_template=tuple(args),cwd=cwd or None,
                target_host=existing.target_host if existing else platform.node(),
                supported_platforms=existing.supported_platforms if existing else (('ps1','psx') if system in ('ps1','psx') else (system,)),
                supported_extensions=existing.supported_extensions if existing else (),
                env=existing.env if existing else {})
            if existing:self._store.update_profile(p)
            else:self._store.create_profile(p)
            self._error='';self.profilesCommitted.emit();self.changed.emit();return True
        except (ValueError,OSError,TypeError):
            self._error='Check the emulator path, system, arguments and working directory';self.changed.emit();return False
    @Slot(str,str,result=bool)
    def setDefault(self,system,profile_id):
        if not self._store:return False
        try:self._store.load()
        except (OSError,ValueError):
            self._error='Emulator profiles could not be loaded';self.changed.emit();return False
        p=self._store.get_profile(profile_id)
        if not p or p.target_type!='rom' or (system!=p.platform_or_provider and system not in p.supported_platforms):
            self._error='Select a compatible emulator profile';self.changed.emit();return False
        before=dict(self._store.default_associations)
        try:
            for key in (('ps1','psx') if system in ('ps1','psx') else (system,)):
                self._store.default_associations[key]=profile_id
            self._store.save()
        except (OSError,ValueError):
            self._store.default_associations=before
            self._error='Emulator default could not be saved';self.changed.emit();return False
        self._error='';self.profilesCommitted.emit();self.changed.emit();return True
