"""Offline Xodus install contracts. No real Store, account or game calls."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from unittest.mock import patch
import pytest
from native_services.xodus_install import (XodusInstaller,XodusInstallError,XodusProgress,
    parse_packages,verify_installation,_process)

PRODUCT='9ABCDEF12345';CONTENT='11111111-2222-3333-4444-555555555555'
def package(**extra):
    return dict(productId=PRODUCT,contentId=CONTENT,title='Fixture game',supported=True,
        platform='Windows.Desktop',packageFormat='MSIXVC',kind='Game',downloadBytes=100,installBytes=200,**extra)
def response(row=None,**extra):
    return json.dumps(dict(schema=1,rootProductId=PRODUCT,market='US',status='ready',choices=[row or package()],**extra))
def frame(phase='downloading',sequence=1,**extra):
    return 'NEXUS_PROGRESS '+json.dumps(dict(schema=1,sequence=sequence,phase=phase,
        completedBytes=50,totalBytes=100,bytesPerSecond=5,paused=False,canPause=True,**extra))
def files(root):
    root.mkdir(parents=True,exist_ok=True)
    (root/'.xodus-streaming.msixvc').write_bytes(b'fixture container')
    (root/'Game.EXE').write_bytes(b'MZ fixture')
    (root/'MicrosoftGame.Config').write_text(f'<Game><StoreId>{PRODUCT}</StoreId><ExecutableList><Executable Name="game.exe" /></ExecutableList></Game>')
def installer(process,clock=None):return XodusInstaller(sys.executable,process=process,clock=clock)
def plan(service,tmp_path):return service.plan(PRODUCT,parse_packages(response(),PRODUCT)[0],str(tmp_path/'games'))


def test_metadata_exact_product_and_supported_base_game():
    assert parse_packages(response(),PRODUCT)[0]['content_id']==CONTENT
    raw=json.loads(response());raw['status']='unavailable';raw['choices'][0]['supported']=False
    assert parse_packages(json.dumps(raw),PRODUCT)==[]
    raw['status']='choice';raw['choices'][0]['supported']=True;raw['choices'][0]['kind']='GameAddOn'
    assert parse_packages(json.dumps(raw),PRODUCT)==[]

@pytest.mark.parametrize('mutation',[
    lambda d:d.update(rootProductId='9AAAAAAAAAAA'),lambda d:d.update(market='GB'),
    lambda d:d.update(schema=True),lambda d:d.update(status='choice'),
    lambda d:d['choices'].append(d['choices'][0]),
    lambda d:d['choices'][0].update(contentId='--help'),
    lambda d:d['choices'][0].update(platform='Xbox.One'),
    lambda d:d['choices'][0].update(installBytes=-1),
    lambda d:d['choices'][0].update(downloadBytes=True),
])
def test_invalid_package_responses_fail_closed(mutation):
    data=json.loads(response());mutation(data)
    with pytest.raises(XodusInstallError):parse_packages(json.dumps(data),PRODUCT)


def test_progress_uses_typed_records_not_log_percent_or_replayed_sequences():
    p=XodusProgress();assert p.consume('Bearer fixture-secret 100%') is None
    assert p.consume(frame()).percent==50
    assert p.consume(frame()) is None
    assert p.consume(frame('complete',2)).percent==50 and p.terminal=='complete'
    assert p.consume(frame('failed',3)) is None
    for bad in ('NEXUS_PROGRESS {}','NEXUS_PROGRESS null',frame(sequence=True)):
        assert XodusProgress().consume(bad) is None


def test_resolve_and_plan_do_not_download_or_make_directories(tmp_path):
    calls=[]
    async def process(argv,**kw):calls.append(argv);return 0,response()
    service=installer(process);choices=asyncio.run(service.resolve(PRODUCT))
    target=service.plan(PRODUCT,choices[0],str(tmp_path/'new'))
    assert calls==[(sys.executable,'resolve-package',PRODUCT,'--json','--market','US')]
    assert not (tmp_path/'new').exists()
    assert target.argv==(sys.executable,'streaming',PRODUCT,str(tmp_path/'new'/PRODUCT),
        '--market','US','--selected-content-id',CONTENT,'--progress-json')


@pytest.mark.windows_api_probe
def test_completed_stream_requires_files_and_writes_detached_receipt(tmp_path):
    calls=[];progress=[]
    async def process(argv,**kw):
        calls.append(argv);files(Path(argv[3]));kw['on_line'](frame('complete'))
        return 0,''
    service=installer(process);target=plan(service,tmp_path)
    result=asyncio.run(service.execute(target,progress.append))
    assert len(calls)==1 and result['state']=='files_verified'
    assert Path(result['executable']).name=='Game.EXE'
    assert json.loads((target.destination/'.waystone-install-receipt.json').read_text())==result
    assert progress[-1].phase=='running' # Never complete before independent file verification.


@pytest.mark.parametrize('exit_code,terminal',[(0,None),(1,'complete'),(0,'failed')])
def test_nonzero_or_unconfirmed_terminal_never_installed(tmp_path,exit_code,terminal):
    async def process(argv,**kw):
        if terminal:kw['on_line'](frame(terminal))
        return exit_code,''
    service=installer(process);target=plan(service,tmp_path)
    with pytest.raises(XodusInstallError):asyncio.run(service.execute(target,lambda p:None))
    assert (target.destination/'.waystone-xodus-install.json').is_file()
    assert not (target.destination/'.waystone-install-receipt.json').exists()


def test_empty_directory_is_not_installation_and_traversal_is_not_an_executable(tmp_path):
    service=installer(None);target=plan(service,tmp_path);target.destination.mkdir(parents=True)
    with pytest.raises(XodusInstallError):verify_installation(target)
    files(target.destination)
    (target.destination/'MicrosoftGame.Config').write_text('<Game><Executable Name="../escape.exe"/></Game>')
    with pytest.raises(XodusInstallError):verify_installation(target)
    files(target.destination);(target.destination/'.nexus-update-incomplete-v1').touch()
    with pytest.raises(XodusInstallError):verify_installation(target)


def test_different_store_and_xml_entities_rejected(tmp_path):
    service=installer(None);target=plan(service,tmp_path);files(target.destination)
    xml=target.destination/'MicrosoftGame.Config'
    xml.write_text('<Game><StoreId>9AAAAAAAAAAA</StoreId><Executable Name="game.exe"/></Game>')
    with pytest.raises(XodusInstallError):verify_installation(target)
    xml.write_text('<!DOCTYPE Game [<!ENTITY x "unsafe">]><Game><Executable Name="game.exe"/></Game>')
    with pytest.raises(XodusInstallError):verify_installation(target)


def test_existing_unowned_directory_is_never_overwritten(tmp_path):
    calls=[]
    async def process(argv,**kw):calls.append(argv);return 0,''
    service=installer(process);target=plan(service,tmp_path)
    target.destination.mkdir(parents=True);user_file=target.destination/'kept';user_file.write_text('user data')
    with pytest.raises(XodusInstallError):asyncio.run(service.execute(target,lambda p:None))
    assert not calls and user_file.read_text()=='user data'


def test_expired_plan_and_drive_change_do_not_start_process(tmp_path):
    now=[0];calls=[]
    async def process(argv,**kw):calls.append(argv);return 0,''
    service=installer(process,clock=lambda:now[0]);target=plan(service,tmp_path);now[0]=61
    with pytest.raises(XodusInstallError):asyncio.run(service.execute(target,lambda p:None))
    now[0]=0
    with patch('native_services.xodus_install.inspect_destination',return_value={'ready':False}):
        with pytest.raises(XodusInstallError):asyncio.run(service.execute(target,lambda p:None))
    with patch('native_services.xodus_install.inspect_destination',return_value={
            'ready':True,'free_bytes':100000,'filesystem_device':target.filesystem_device+1}):
        with pytest.raises(XodusInstallError):asyncio.run(service.execute(target,lambda p:None))
    assert not calls


@pytest.mark.windows_api_probe
def test_cancel_releases_the_owned_attempt_without_deleting_partial_data(tmp_path):
    async def scenario():
        active=asyncio.Event()
        async def process(argv,**kw):
            (Path(argv[3])/'partial').write_bytes(b'kept');active.set();await asyncio.sleep(999)
        service=installer(process);target=plan(service,tmp_path)
        task=asyncio.create_task(service.execute(target,lambda p:None))
        try:
            # If setup fails before the fake backend starts, fail with a useful
            # assertion instead of hanging until the outer CI watchdog kills pytest.
            await asyncio.wait_for(active.wait(),2)
            await service.cancel()
            assert task.cancelled() and service._active is None
            assert (target.destination/'partial').read_bytes()==b'kept'
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    asyncio.run(scenario())


def test_real_bounded_runner_suppresses_raw_errors_and_closes_readers():
    async def scenario():
        code,raw=await _process((sys.executable,'-c','import sys;print("{}");print("fixture-secret",file=sys.stderr)'))
        assert code==0 and raw.strip()=='{}'
        with pytest.raises(XodusInstallError):
            await _process((sys.executable,'-c','print("x"*300000)'),output_limit=20)
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(scenario())


def test_account_library_never_returns_email_credentials_or_inferred_ownership():
    async def process(argv,**kwargs):
        assert argv[1:] == ('library','--json','--market','US')
        return 0,json.dumps({'schema':1,'account':'private@example.test','market':'US',
            'entitlements':[{'productId':PRODUCT,'title':'Game','isTrial':True,'token':'FIXTURE_ONLY'},
                            {'productId':'--bad'}, {'productId':PRODUCT,'title':'duplicate'}]})
    rows=asyncio.run(installer(process).library())
    assert rows==[{'product_id':PRODUCT,'title':'Game'}]
    assert 'private' not in repr(rows) and 'FIXTURE' not in repr(rows) and 'owned' not in repr(rows)


def test_missing_library_titles_are_public_enriched_once_without_identity_merging():
    calls=[]
    async def process(argv,**kw):
        return 0,json.dumps({'schema':1,'market':'US','entitlements':[{'productId':PRODUCT}]})
    async def titles(ids,market):
        calls.append((ids,market));return {PRODUCT:'Public display title','9AAAAAAAAAAA':'Unrequested'}
    service=XodusInstaller(sys.executable,process=process,title_reader=titles)
    async def scenario():
        expected=[{'product_id':PRODUCT,'title':'Public display title'}]
        assert await service.library()==expected
        assert await service.library()==expected
    asyncio.run(scenario())
    assert calls==[([PRODUCT],'US')]


def test_public_title_lookup_uses_fixed_anonymous_endpoint_and_exact_ids():
    import httpx
    from native_services.xodus_install import public_product_titles
    requests=[];client=httpx.AsyncClient
    def serve(request):
        requests.append(request)
        assert str(request.url).startswith('https://displaycatalog.mp.microsoft.com/v7.0/products?')
        assert request.url.params['bigIds']==PRODUCT
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        return httpx.Response(200,json={'Products':[
            {'ProductId':PRODUCT,'LocalizedProperties':[{'ProductTitle':'Real title'}]},
            {'ProductId':'9AAAAAAAAAAA','LocalizedProperties':[{'ProductTitle':'Unrequested'}]}]})
    with patch('httpx.AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(serve),**kw)):
        assert asyncio.run(public_product_titles([PRODUCT],'US'))=={PRODUCT:'Real title'}
    assert len(requests)==1


def test_public_title_failure_keeps_unknown_library_records():
    async def process(argv,**kw):
        return 0,json.dumps({'schema':1,'market':'US','entitlements':[{'productId':PRODUCT}]})
    async def titles(ids,market):raise OSError('fixture failure, not exposed')
    service=XodusInstaller(sys.executable,process=process,title_reader=titles)
    assert asyncio.run(service.library())==[{'product_id':PRODUCT,'title':PRODUCT}]


def test_other_xodus_work_and_destination_symlinks_are_not_overwritten(tmp_path):
    calls=[]
    async def process(argv,**kw):calls.append(argv);return 0,''
    service=installer(process);target=plan(service,tmp_path)
    service.busy_check=lambda path:True
    with pytest.raises(XodusInstallError):asyncio.run(service.execute(target,lambda p:None))
    assert not calls
    service.busy_check=lambda path:False
    target.root.mkdir(parents=True);outside=tmp_path/'outside';outside.mkdir()
    target.destination.symlink_to(outside,target_is_directory=True)
    with pytest.raises(XodusInstallError):asyncio.run(service.execute(target,lambda p:None))
    assert not calls and list(outside.iterdir())==[]


def test_real_owned_runner_cancellation_reaps_its_child(tmp_path):
    import os
    import ctypes
    from ctypes import wintypes
    def pid_is_alive(pid):
        if os.name != 'nt':
            try:os.kill(pid,0);return True
            except ProcessLookupError:return False
            except PermissionError:return True
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return False
        try:
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return True
            return code.value==259 # STILL_ACTIVE
        finally:kernel.CloseHandle(handle)
    async def scenario():
        pidfile=tmp_path/'child-pid'
        code='import os,sys,time,signal;signal.signal(signal.SIGINT,signal.SIG_IGN);open(sys.argv[1],"w").write(str(os.getpid()));time.sleep(999)'
        task=asyncio.create_task(_process((sys.executable,'-c',code,str(pidfile)),timeout=30))
        for _ in range(200):
            if pidfile.exists():break
            await asyncio.sleep(.01)
        assert pidfile.exists();pid=int(pidfile.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not pid_is_alive(pid), 'the exact child owned by _process must be reaped'
    asyncio.run(scenario())


def test_windows_child_cleanup_uses_exact_process_terminate_then_kill():
    from native_services.xodus_install import _stop
    class Child:
        returncode=None
        def __init__(self):self.calls=[]
        def terminate(self):self.calls.append('terminate')
        def kill(self):self.calls.append('kill')
        async def wait(self):
            self.calls.append('wait')
            if self.calls.count('wait')==1:raise asyncio.TimeoutError()
            self.returncode=1
            return 1
    child=Child()
    asyncio.run(_stop(child,windows=True))
    assert child.calls==['terminate','wait','kill','wait']
