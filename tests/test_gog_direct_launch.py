import json
import stat
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from native_services.gog_direct_launch import GogDirectLaunchError, plan_gog_direct_launch

def _tool(root):
    p=root/'bin'/'gogdl'; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('#!/bin/sh\nexit 0\n'); p.chmod(p.stat().st_mode|stat.S_IXUSR); return p

def _config(root):
    p=root/'config'; p.mkdir(parents=True,exist_ok=True); return p

def _write_info(path,pid='12345',task_path='game.exe',tasks=None,root_id=None,game_id=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    data={'rootGameId':root_id if root_id is not None else pid,'gameId':game_id if game_id is not None else pid,
          'playTasks':tasks if tasks is not None else [{'isPrimary':True,'type':'FileTask','path':task_path,'workingDir':''}]}
    path.write_text(json.dumps(data))

def _install(root,platform='windows',pid='12345',path='game.exe',tasks=None):
    inst=root/'install'; inst.mkdir(parents=True,exist_ok=True)
    if platform=='osx':
        info=inst/'Contents'/'Resources'/f'goggame-{pid}.info'; exe=inst/path; exe.parent.mkdir(parents=True,exist_ok=True); exe.write_text('binary')
    elif platform=='linux':
        info=inst/'game'/f'goggame-{pid}.info'; exe=inst/'game'/path; exe.parent.mkdir(parents=True,exist_ok=True); exe.write_text('binary')
        start=inst/'start.sh'; start.write_text('#!/bin/sh\n'); start.chmod(0o755)
    else:
        info=inst/f'goggame-{pid}.info'; exe=inst/path; exe.parent.mkdir(parents=True,exist_ok=True); exe.write_text('binary')
    _write_info(info,pid,path,tasks); return inst

def _plan(tmp_path,platform='windows',pid='12345',**kwargs):
    args={}
    if platform=='windows':
        args={'wine_binary':str(_tool(tmp_path)), 'wine_prefix':str(tmp_path/'wineprefix')}
        (tmp_path/'wineprefix').mkdir(exist_ok=True)
    args.update(kwargs)
    return plan_gog_direct_launch(_config(tmp_path),pid,_install(tmp_path,platform,pid),platform=platform,gogdl_path=_tool(tmp_path),**args)

def test_windows_direct_plan_and_app_private_state(tmp_path):
    plan=_plan(tmp_path); intent=plan.launch_intent
    assert intent.argv[3:8]==['launch',str(Path(plan.install_path)),'12345','--platform','windows']
    assert intent.argv[-2:] == ['--wine-prefix',str((tmp_path/'wineprefix').resolve())]
    assert intent.verified_contract and intent.target_path == plan.install_path
    assert intent.env['GOGDL_CONFIG_PATH']==str((Path(plan.auth_config_path).parent/'gogdl-config').resolve())
    assert plan.info_path.endswith('goggame-12345.info')

def test_osx_metadata_path_matches_upstream_but_linux_planner_refuses_execution(tmp_path):
    from native_services.gog_direct_launch import _info_path_for
    install=_install(tmp_path,'osx')
    assert str(_info_path_for(install,'12345','osx')).endswith('Contents/Resources/goggame-12345.info')
    with pytest.raises(GogDirectLaunchError,match='macOS'):
        plan_gog_direct_launch(_config(tmp_path),'12345',install,platform='osx',gogdl_path=_tool(tmp_path))

def test_linux_requires_matching_local_metadata_and_start(tmp_path):
    plan=_plan(tmp_path,'linux')
    assert plan.info_path.endswith('game/goggame-12345.info')
    assert plan.launch_intent.argv[-2:]==['--platform','linux']
    assert 'cannot prove the script' in plan.limitations[0]

def test_linux_plain_start_without_receipt_rejected(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=tmp_path/'install'; inst.mkdir(); start=inst/'start.sh'; start.write_text('#!/bin/sh'); start.chmod(0o755)
    with pytest.raises(GogDirectLaunchError,match='goggame-12345.info'):
        plan_gog_direct_launch(cfg,'12345',inst,platform='linux',gogdl_path=tool)

def test_missing_identity_field_rejected(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path); info=inst/'goggame-12345.info'; data=json.loads(info.read_text()); data.pop('gameId'); info.write_text(json.dumps(data))
    with pytest.raises(GogDirectLaunchError,match='gameId'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool)

def test_boolean_identity_rejected(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path); info=inst/'goggame-12345.info'; data=json.loads(info.read_text()); data['gameId']=True; info.write_text(json.dumps(data))
    with pytest.raises(GogDirectLaunchError,match='gameId'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool)

def test_identity_mismatch_rejected(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path); info=inst/'goggame-12345.info'; data=json.loads(info.read_text()); data['rootGameId']='999'; info.write_text(json.dumps(data))
    with pytest.raises(GogDirectLaunchError,match='exact product_id'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool)

def test_missing_primary_task_rejected_without_fallback(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path,tasks=[{'type':'FileTask','path':'game.exe'}])
    with pytest.raises(GogDirectLaunchError,match='no primary'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool)

def test_preferred_task_bounds_and_bool(tmp_path):
    with pytest.raises(GogDirectLaunchError,match='outside playTasks'): _plan(tmp_path,prefer_task=-1)
    with pytest.raises(GogDirectLaunchError,match='outside playTasks'): _plan(tmp_path,prefer_task=True)

def test_non_file_task_rejected(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path,tasks=[{'isPrimary':True,'type':'URLTask','path':'game.exe'}])
    with pytest.raises(GogDirectLaunchError,match='not a FileTask'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool)

def test_task_symlink_escape_rejected(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path); ext=tmp_path/'outside.exe'; ext.write_text('x'); (inst/'game.exe').unlink(); (inst/'game.exe').symlink_to(ext)
    with pytest.raises(GogDirectLaunchError,match='escapes'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool)

def test_auth_config_must_be_contained_file_path(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path)
    with pytest.raises(GogDirectLaunchError,match='within config_root'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool,auth_config_path=cfg)
    d=cfg/'directory'; d.mkdir()
    with pytest.raises(GogDirectLaunchError,match='file, not a directory'): plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool,auth_config_path=d)

def test_reserved_environment_cannot_be_overridden(tmp_path):
    with pytest.raises(GogDirectLaunchError,match='reserved variable'): _plan(tmp_path,env={'GOGDL_CONFIG_PATH':'/tmp/heroic'})

def test_env_values_must_be_strings_and_wrapper_rejects_nul(tmp_path):
    with pytest.raises(GogDirectLaunchError,match='must be a string'): _plan(tmp_path,env={'SOME_VALUE':3})
    with pytest.raises(GogDirectLaunchError,match='NUL'): _plan(tmp_path,wrapper=['wine','bad\x00arg'])

def test_windows_launch_requires_explicit_wine_runtime(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path)
    with pytest.raises(GogDirectLaunchError,match='explicit Wine'):
        plan_gog_direct_launch(cfg,'12345',inst,platform='windows',gogdl_path=tool)


def test_invalid_product_id_rejected(tmp_path):
    cfg,tool=_config(tmp_path),_tool(tmp_path); inst=_install(tmp_path)
    with pytest.raises(GogDirectLaunchError,match='format'): plan_gog_direct_launch(cfg,'../123',inst,platform='windows',gogdl_path=tool)


def test_launch_intent_works_with_existing_waystone_process_guard(tmp_path):
    from native_services.launch_profiles import launch
    plan=_plan(tmp_path)
    calls=[]
    class Proc:
        pid=77
    def fake_spawn(argv, **kwargs):
        calls.append((argv,kwargs))
        return Proc()
    result=launch(plan.launch_intent,spawn=fake_spawn)
    assert result.status=='handed_off'
    assert result.pid==77
    assert calls[0][0][0]==plan.gogdl_path
    assert calls[0][1]['env']['GOGDL_CONFIG_PATH']==plan.launch_intent.env['GOGDL_CONFIG_PATH']
