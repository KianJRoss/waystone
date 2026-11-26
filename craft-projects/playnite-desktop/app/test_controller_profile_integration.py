import json
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QCoreApplication
from PySide6.QtGui import QGuiApplication
from controller_profiles_view import ControllerProfilesView
from controller_profile_integration import ControllerProfileIntegration

_APP=None

def app():
    global _APP
    _APP=QCoreApplication.instance() or QGuiApplication([])
    return _APP


class Activation(QObject):
    changed=Signal();finished=Signal(str,bool,bool);observed=Signal(str)
    def __init__(self):
        super().__init__();self.calls=[];self.reads=0;self.uncertain=False;self.status='Not connected';self.pending=None;self.active='General'
    def request(self,name,token):
        if self.pending or self.uncertain:return False
        self.calls.append(name);self.pending=(name,token);return True
    def finish(self,success=True,uncertain=False):
        name,token=self.pending;self.pending=None;self.uncertain=uncertain
        if success:self.active=name
        self.changed.emit();self.finished.emit(token,success,uncertain)
    def refresh(self):
        self.reads+=1;self.uncertain=False;self.observed.emit(self.active);return True


def setup(tmp_path,canonicalize=None):
    app();path=tmp_path/'profiles.json'
    path.write_text(json.dumps({'version':1,'active_profile':'General','profiles':{'General':{},'Racing':{},'Flight':{}},'default_profile':'General','game_profiles':{'A':'Racing','B':'Flight'}}))
    view=ControllerProfilesView(path);adapter=Activation();wire=ControllerProfileIntegration(view,adapter,canonicalize)
    return view,adapter,wire


def test_construction_inert_launching_not_running_and_confirmed_switch(tmp_path):
    view,adapter,wire=setup(tmp_path)
    assert adapter.calls==[] and adapter.reads==0
    wire.observe('one','A','launching');assert adapter.calls==[]
    wire.observe('one','A','running');assert adapter.calls==['Racing'] and view.runtimeGameId=='A'
    assert 'awaiting native readback' in view.runtimeStatus
    adapter.finish();assert wire.runtime.active_profile=='Racing'
    wire.observe('one','A','unknown');wire.observe('one','A','running')
    assert adapter.calls==['Racing']
    wire.observe('one','A','closed');assert adapter.calls==['Racing','General']


def test_overlapping_sessions_restore_remaining_then_default(tmp_path):
    view,adapter,wire=setup(tmp_path)
    wire.observe('one','A','running');adapter.finish()
    wire.observe('two','B','running');adapter.finish()
    wire.observe('two','B','closed');assert adapter.calls==['Racing','Flight','Racing']
    adapter.finish();assert view.runtimeGameId=='A'
    wire.observe('one','A','closed');assert adapter.calls[-1]=='General' and view.runtimeGameId==''


def test_config_changes_preserve_active_game_then_apply_default_on_exit(tmp_path):
    view,adapter,wire=setup(tmp_path)
    wire.observe('one','A','running');adapter.finish()
    assert view.setDefaultProfile('Flight');assert adapter.calls==['Racing']
    assert json.loads(view._path.read_text())['active_profile']=='General'
    wire.observe('one','A','closed');assert adapter.calls[-1]=='Flight'


def test_exact_existing_id_link_resolution_not_title_match(tmp_path):
    view,adapter,wire=setup(tmp_path,lambda key:{'original':'A'}.get(key,key))
    wire.observe('one','original','running');assert adapter.calls==['Racing'] and view.runtimeGameId=='A'
    adapter.finish();wire.observe('two','unlinked-same-title','running')
    assert adapter.calls[-1]=='General'


def test_uncertainty_blocks_poll_and_config_retry_until_explicit_readback(tmp_path):
    view,adapter,wire=setup(tmp_path)
    wire.observe('one','A','running');adapter.finish(False,True)
    wire.observe('one','A','running');assert len(adapter.calls)==1
    snapshot=view._path.read_bytes()
    assert not view.assignGame('A','Flight') and view._path.read_bytes()==snapshot
    assert len(adapter.calls)==1
    assert 'uncertain' in view.runtimeStatus
    assert wire.refresh();assert adapter.reads==1 and adapter.calls[-1]=='Racing'
    adapter.finish();assert wire.runtime.active_profile=='Racing'
    assert view.assignGame('A','Flight');assert adapter.calls[-1]=='Flight'
    adapter.finish();assert wire.runtime.active_profile=='Flight'


def test_stale_result_and_exit_while_activation_pending_do_not_restore_early(tmp_path):
    view,adapter,wire=setup(tmp_path)
    wire.observe('one','A','running')
    adapter.finished.emit('999',True,False);assert wire.runtime.pending_profile=='Racing'
    wire.observe('one','A','closed');assert adapter.calls==['Racing']
    adapter.finish();assert adapter.calls==['Racing','General']


def test_storage_unreadable_disables_resolution_without_native_write(tmp_path):
    view,adapter,wire=setup(tmp_path);view._path.write_text('bad')
    assert not view.refresh()
    wire.observe('one','A','running');assert adapter.calls==[]
    assert not wire.refresh() and adapter.reads==0
