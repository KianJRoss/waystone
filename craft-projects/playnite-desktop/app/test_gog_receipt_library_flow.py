"""Actual UI completion/model path with synthetic local files, no provider calls."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from app import create_session
from native_services.install_receipts import InstallReceiptRegistry
from test_game_install_controller import app, wait, close_session
from test_provider_job_view import Manager

PRODUCT='1234567890'
def game(key,source,product):
    return {'id':key,'title':'Renamed same-title game','variants':[{
        'source':source,'provider_game_id':product,'source_id':key+'-guid',
        'platform':'Windows','status':'not-installed','recipe':[],
        'installation_id':'fixture:'+key}]}
GAMES=[game('gog-game','GOG.com',PRODUCT),game('other-sku','GOG','1234567891'),
       game('other-store','Amazon',PRODUCT)]

class GOGManager(Manager):
    def __init__(self,mode='valid'):
        super().__init__();self.mode=mode
    async def execute_job(self,plan,progress):
        root=plan.destination_dir;root.mkdir(parents=True)
        (root/'bin').mkdir();(root/'bin/Game.exe').write_bytes(b'fixture game - never executed')
        data={'gameId':PRODUCT,'rootGameId':PRODUCT,'name':'Old store title',
              'playTasks':[{'type':'FileTask','path':r'bin\Game.exe','workingDir':'bin','isPrimary':True}]}
        if self.mode=='url':data['playTasks'][0]['type']='URLTask'
        if self.mode=='wrong-product':data['gameId']='1234567891'
        if self.mode=='incomplete':(root/'.gogdl-resume').write_text('fixture')
        if self.mode!='missing':(root/f'goggame-{PRODUCT}.info').write_text(json.dumps(data))
        return await super().execute_job(plan,progress)

def session(application,registry,manager):
    with patch('library_model.load_fixture',return_value=GAMES):
        result=create_session(application,headless=True,receipt_registry=registry,provider_manager=manager)
    result[0].game_installs.tools['gog']=sys.executable
    return result

def installed(model):model.setFilters('', 'All sources', 'All platforms', 'installed')

def test_one_click_gog_completion_updates_same_model_and_restarts(tmp_path):
    application=app();reg=InstallReceiptRegistry(tmp_path/'state/receipts.json')
    manager=GOGManager();engine,model,root,warnings=session(application,reg,manager)
    try:
        view=engine.provider_jobs;view._root=str(tmp_path/'games')
        installed(model);assert model.rowCount()==0 and not reg.path.exists()
        assert engine.game_installs.install('gog-game')
        assert wait(lambda:not view.busy and model.rowCount()==1)
        assert len(manager.executed)==1 and root.property('activePage')=='library'
        assert not engine.game_installs.prompt
        assert [r['id'] for r in model.visible]==['gog-game']
        assert view.installReceipt['provider']=='gog' and view.xboxReceipt=={}
        receipt=view.installReceipt
        saved=reg.snapshot()[0]
        assert saved['is_present'] and saved['metadata_locator'].endswith(f'goggame-{PRODUCT}.info')
        assert saved['working_dir']==str(tmp_path/'games'/PRODUCT/'bin')
        assert 'Verified provider files' in model._effective_variants(model.visible[0])[0]['installation_basis']
        # Duplicate completion cannot append another history entry.
        view._on_result({'generation':view._generation,'progress':{'phase':'completed','receipt':receipt}})
        assert len(reg.snapshot()[0]['history'])==1
    finally:close_session(application,engine,root)
    engine,model,root,warnings=session(application,InstallReceiptRegistry(reg.path),GOGManager())
    try:
        installed(model);assert model.rowCount()==1 and engine.provider_jobs._worker is None
        marker=Path(receipt['install_path'])/'.gogdl-resume';marker.write_text('fixture update in progress')
        engine.refresh_receipts();assert model.rowCount()==0
        marker.unlink();engine.refresh_receipts();assert model.rowCount()==1
        Path(receipt['executable']).unlink();engine.refresh_receipts();assert model.rowCount()==0
        assert reg.snapshot() and not reg.snapshot()[0]['is_present']
        assert not [w for w in warnings if any(x in w for x in ('TypeError','ReferenceError','Binding loop'))]
    finally:close_session(application,engine,root)

@pytest.mark.parametrize('mode',['missing','url','wrong-product','incomplete'])
def test_gog_exit_zero_without_local_proof_is_not_installed(tmp_path,mode):
    application=app();reg=InstallReceiptRegistry(tmp_path/'state/receipts.json')
    manager=GOGManager(mode);engine,model,root,warnings=session(application,reg,manager)
    try:
        view=engine.provider_jobs;view._root=str(tmp_path/'games');installed(model)
        assert engine.game_installs.install('gog-game') and wait(lambda:not view.busy)
        assert len(manager.executed)==1 and view.state['phase']=='completed'
        assert view.state['verification_reason'] and 'could not yet be verified' in view.state['message']
        assert model.rowCount()==0 and not reg.path.exists() and view.installReceipt=={}
        assert root.property('activePage')=='library'
    finally:close_session(application,engine,root)

def test_changed_destination_device_does_not_register_gog_proof(tmp_path):
    application=app();reg=InstallReceiptRegistry(tmp_path/'state/receipts.json')
    manager=GOGManager();engine,model,root,warnings=session(application,reg,manager)
    try:
        view=engine.provider_jobs;view._root=str(tmp_path/'games');installed(model)
        real_probe=view._storage_probe
        view._storage_probe=lambda p:dict(real_probe(p),filesystem_device=tmp_path.stat().st_dev+1)
        assert engine.game_installs.install('gog-game') and wait(lambda:not view.busy)
        assert view.state['verification_reason']=='destination_device_changed'
        assert not reg.path.exists() and model.rowCount()==0
    finally:close_session(application,engine,root)
