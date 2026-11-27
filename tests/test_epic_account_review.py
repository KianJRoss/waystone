"""Coordinator independent boundaries; no accounts/model/network invocation."""
import asyncio,json,sys,os,stat
from pathlib import Path
from types import SimpleNamespace
import pytest
from native_services.epic_account import EpicAccountService,default_runner,_read_stream_limited

def result(value):return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')

def test_large_epic_tokens_are_not_mistaken_for_invalid_account(tmp_path):
    cfg=tmp_path/'waystone-account'
    async def runner(argv,*,input,env,timeout):
        if 'auth' in argv:
            cfg.mkdir(exist_ok=True)
            (cfg/'user.json').write_text(json.dumps({'account_id':'fixture-account','displayName':'Fixture','access_token':'fixture-'+'A'*2000,'refresh_token':'fixture-'+'B'*1000}))
            return result({})
        return result([])
    service=EpicAccountService(Path(sys.executable),cfg,runner=runner)
    assert asyncio.run(service.authenticate('fixture-code'))['status']=='connected'
    assert stat.S_IMODE(cfg.stat().st_mode)==0o700
    assert stat.S_IMODE((cfg/'user.json').stat().st_mode)==0o600

def test_large_library_with_metadata_and_malformed_ids(tmp_path):
    cfg=tmp_path/'own';cfg.mkdir()
    games=[{'app_name':'game'+str(i),'app_title':'Game '+str(i),'metadata':{'description':'fixture '*400}} for i in range(1000)]
    games += [{'app_name':'../../bad','app_title':'Bad'},{'app_name':'emptytitle','app_title':''},{'app_name':'control','app_title':'bad\ncontrol'}]
    async def runner(*args,**kwargs):return result(games)
    rows=asyncio.run(EpicAccountService(Path(sys.executable),cfg,runner=runner).refresh_library())
    assert len(rows)==1000 and rows[999]=={'app_name':'game999','app_title':'Game 999'}
    assert all(set(row)=={'app_name','app_title'} for row in rows)

def test_output_overflow_cannot_be_parsed_as_truncated_success():
    async def check():
        stream=asyncio.StreamReader();stream.feed_data(b'[]'+b' '*200);stream.feed_eof()
        try:await _read_stream_limited(stream,10)
        except ValueError:return
        raise AssertionError('overflow must reject, not silently truncate')
    asyncio.run(check())

# POSIX umask applies only to the POSIX child-process implementation. Windows
# uses the current user's app-data ACL instead; no Unix mode-bit claim is made.
@pytest.mark.linux_only
def test_child_private_umask_without_thread_unsafe_preexec(tmp_path):
    target=tmp_path/'child-created'
    code='import os,sys; f=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT,0o666);os.close(f)'
    value=asyncio.run(default_runner([sys.executable,'-c',code,str(target)],env=dict(os.environ),timeout=5))
    assert value.returncode==0 and stat.S_IMODE(target.stat().st_mode)==0o600
