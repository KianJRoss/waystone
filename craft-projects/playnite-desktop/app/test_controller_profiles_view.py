import json
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from controller_profiles_view import ControllerProfilesView

_APP=None

def app():
    global _APP
    from PySide6.QtGui import QGuiApplication
    _APP=QCoreApplication.instance() or QGuiApplication([])
    return _APP


def document():
    return {'version':1,'active_profile':'General','profiles':{'General':{'routes':{'a':'b'},'motion':{'passthrough':True},'unknown':{'x':[1]}},'Racing':{'routes':{'x':'y'}}},'virtual_gamepad_driver':'existing','unknown_root':{'keep':True}}


def view(tmp_path):
    app();path=tmp_path/'profiles.json';path.write_text(json.dumps(document()));return path,ControllerProfilesView(path)


def test_read_and_selection_are_inert_and_existing_keys_survive(tmp_path):
    path,v=view(tmp_path);before=path.read_bytes()
    v.selectGame('id-one');v.setRuntimeGame('id-one');v.refresh()
    assert path.read_bytes()==before
    assert v.profiles==['General','Racing'] and v.defaultProfile=='General'
    assert v.resolveProfile('unknown')=='General'
    assert v.assignGame('id-one','Racing')
    saved=json.loads(path.read_text())
    assert saved['unknown_root']==document()['unknown_root']
    assert saved['profiles']==document()['profiles'] and saved['version']==1
    assert saved['virtual_gamepad_driver']=='existing'


def test_lifecycle_uses_assignment_model_and_updates_every_reference(tmp_path):
    path,v=view(tmp_path)
    assert v.assignGame('exact-id','Racing')
    assert v.duplicateProfile('Racing','Copy')
    assert v.createProfile('Fresh')
    assert json.loads(path.read_text())['profiles']['Fresh']==document()['profiles']['General']
    assert v.setDefaultProfile('Racing')
    assert v.renameProfile('Racing','Renamed')
    assert v.defaultProfile=='Renamed' and v.assignmentForGame('exact-id')=='Renamed'
    assert v.deleteProfile('Renamed')
    assert v.assignmentForGame('exact-id')=='' and v.defaultProfile in v.profiles
    assert v.deleteProfile('Copy') and v.deleteProfile('Fresh')
    before=path.read_bytes()
    assert not v.deleteProfile(v.profiles[0])
    assert path.read_bytes()==before


def test_same_title_unrelated_ids_and_default_reset(tmp_path):
    path,v=view(tmp_path)
    assert v.assignGame('a','Racing')
    assert v.assignmentForGame('a')=='Racing' and v.assignmentForGame('b')==''
    assert v.resolveProfile('b')=='General'
    assert v.assignGame('a','') and v.resolveProfile('a')=='General'
    assert not v.assignGame('','Racing')


def test_default_change_keeps_running_game_override(tmp_path):
    path,v=view(tmp_path)
    assert v.assignGame('running','Racing')
    v.setRuntimeGame('running')
    assert v.setDefaultProfile('General')
    assert json.loads(path.read_text())['active_profile']=='General'
    v.setRuntimeGame('')
    assert v.setDefaultProfile('General')
    assert json.loads(path.read_text())['active_profile']=='General'


def test_external_change_rejected_not_clobbered_then_explicit_retry(tmp_path):
    path,v=view(tmp_path);external=document();external['profiles']['Racing']['external']='preserve'
    path.write_text(json.dumps(external))
    assert not v.renameProfile('Racing','New')
    assert json.loads(path.read_text())==external
    assert v.renameProfile('Racing','New')
    assert json.loads(path.read_text())['profiles']['New']['external']=='preserve'


def test_bad_document_and_write_failure_do_not_announce_commit(tmp_path):
    path,v=view(tmp_path);commits=[];v.documentChanged.connect(lambda:commits.append(True));before=path.read_bytes()
    with patch('controller_profiles_view.model.save',side_effect=OSError('fixture failure')):
        assert not v.createProfile('No')
    assert not commits and path.read_bytes()==before
    path.write_text('invalid');assert not v.refresh() and not v.available
    assert not v.createProfile('No') and path.read_text()=='invalid'


def test_missing_explicit_file_only_created_by_user_action_and_invalid_names(tmp_path):
    app();path=tmp_path/'new'/'profiles.json';v=ControllerProfilesView(path)
    assert v.available and not v.profiles and not path.exists()
    assert not v.createProfile(' ') and not path.exists()
    assert v.createProfile('First') and v.defaultProfile=='First'
    assert not v.createProfile('First') and not v.createProfile('bad\nname')
    disabled=ControllerProfilesView();assert not disabled.available and not disabled.createProfile('No')


def test_intent_saves_preserve_active_profile_and_uncertainty_blocks_writes(tmp_path):
    path,v=view(tmp_path);before=document()['active_profile']
    assert v.assignGame('running','Racing')
    v.setRuntimeGame('running')
    assert v.setDefaultProfile('Racing')
    assert json.loads(path.read_text())['active_profile']==before
    v.setEngineMutationBlocked(True);snapshot=path.read_bytes()
    for fn in [lambda:v.assignGame('running','General'),lambda:v.setDefaultProfile('General'),lambda:v.renameProfile('Racing','No'),lambda:v.deleteProfile('Racing')]:
        assert not fn() and path.read_bytes()==snapshot
    v.setEngineMutationBlocked(False);assert v.assignGame('running','General')


def test_legacy_inferred_default_pinned_and_persisted_before_runtime_selection(tmp_path):
    path,v=view(tmp_path)
    # Simulate a partial additive import with an override but missing default.
    doc=document();doc['game_profiles']={'g':'Racing'};path.write_text(json.dumps(doc));assert v.refresh()
    assert v.defaultProfile=='General'
    assert v.ensureRuntimeMetadata()
    saved=json.loads(path.read_text());assert saved['default_profile']=='General'
    saved['active_profile']='Racing';path.write_text(json.dumps(saved))
    assert v.refresh() and v.defaultProfile=='General' and v.resolveProfile('')=='General'
    assert ControllerProfilesView(path).defaultProfile=='General'


def test_save_readback_failure_blocks_until_explicit_refresh(tmp_path):
    path,v=view(tmp_path);original=v._read_document;count=[0]
    def read():
        count[0]+=1
        if count[0]==2:raise OSError('post-save failure')
        return original()
    with patch.object(v,'_read_document',side_effect=read):
        assert not v.createProfile('Committed')
    assert not v.available and 'may already be committed' in v.error
    assert 'Committed' in json.loads(path.read_text())['profiles']
    assert not v.createProfile('Other')
    assert v.refresh() and v.available and 'Committed' in v.profiles
