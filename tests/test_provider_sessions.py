import json,subprocess
import pytest
from native_services.provider_sessions import ProviderSessionStore, SessionStoreError
OWNER='76561197968575517'

class Runner:
    def __init__(self):self.rows={};self.calls=[]
    def __call__(self,argv,**kw):
        self.calls.append((argv,kw));provider=argv[-1]
        assert kw['timeout']==6 and kw['capture_output'] and kw['check'] is False
        if argv[1]=='store':self.rows[provider]=kw['input'];return subprocess.CompletedProcess(argv,0,'','')
        if argv[1]=='clear':self.rows.pop(provider,None);return subprocess.CompletedProcess(argv,0,'','')
        return subprocess.CompletedProcess(argv,0 if provider in self.rows else 1,self.rows.get(provider,''),'')

def test_secure_roundtrip_is_inert_scoped_and_never_places_secret_in_argv():
    run=Runner();s=ProviderSessionStore(runner=run,executable='/usr/bin/secret-tool')
    assert not run.calls
    record={'kind':'steam_refresh','account_id':OWNER,'refresh_token':'fixture-private-refresh'}
    s.save('steam',record)
    assert 'fixture-private-refresh' not in repr(run.calls[0][0])
    assert s.load('steam')==record
    assert s.load('xbox') is None
    record['refresh_token']='changed';assert s.load('steam')['refresh_token']=='fixture-private-refresh'
    s.clear('steam');assert s.load('steam') is None
    assert all('io.waystone.Library' in argv for argv,_ in run.calls)

def test_xbox_remembers_selector_not_copied_credentials():
    run=Runner();s=ProviderSessionStore(runner=run,executable='secret-tool')
    record={'kind':'xbox_helper','account':'fixture-account'}
    s.save('xbox',record);assert s.load('xbox')==record
    with pytest.raises(ValueError):s.save('xbox',{**record,'token':'must-not-store'})

@pytest.mark.parametrize('result',[subprocess.CompletedProcess([],1,'','locked fixture-secret'),subprocess.CompletedProcess([],3,'fixture-secret','')])
def test_storage_errors_are_sanitized_without_plaintext_fallback(result,tmp_path):
    s=ProviderSessionStore(runner=lambda *a,**kw:result,executable='secret-tool')
    with pytest.raises(SessionStoreError) as exc:s.load('steam')
    assert 'fixture-secret' not in str(exc.value)
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('raw',['bad','{"version":true,"provider":"steam","session":{}}',json.dumps({'version':1,'provider':'xbox','session':{'kind':'xbox_helper','account':'someone'}})])
def test_malformed_or_cross_provider_record_is_not_accepted(raw):
    s=ProviderSessionStore(runner=lambda *a,**k:subprocess.CompletedProcess([],0,raw,''),executable='secret-tool')
    with pytest.raises(SessionStoreError):s.load('steam')

def test_exception_timeouts_are_sanitized():
    def fail(*a,**kw):raise subprocess.TimeoutExpired('fixture-private',6)
    s=ProviderSessionStore(runner=fail,executable='secret-tool')
    with pytest.raises(SessionStoreError) as exc:s.clear('steam')
    assert 'fixture-private' not in str(exc.value)

def test_invalid_provider_never_invokes_external_process():
    run=Runner();s=ProviderSessionStore(runner=run,executable='secret-tool')
    with pytest.raises(ValueError):s.load('other')
    with pytest.raises(ValueError):s.save('steam',{'kind':'xbox_helper','account':'x'})
    assert not run.calls

def test_itad_api_key_is_keyring_only_and_provider_scoped():
    run=Runner();s=ProviderSessionStore(runner=run,executable='secret-tool')
    record={'kind':'itad_api_key','api_key':'fixture-private-itad-key'}
    s.save('itad',record)
    argv,kwargs=run.calls[0]
    assert 'fixture-private-itad-key' not in repr(argv)
    assert kwargs['input'] and 'fixture-private-itad-key' in kwargs['input']
    assert s.load('itad')==record
    with pytest.raises(ValueError):s.save('steam',record)
    with pytest.raises(ValueError):s.save('itad',{**record,'unexpected':'value'})
    s.clear('itad');assert s.load('itad') is None

def test_steamgriddb_key_is_validated_and_keyring_only():
    run=Runner();s=ProviderSessionStore(runner=run,executable='secret-tool')
    record={'kind':'steamgriddb_api_key','api_key':'fixture-private-sgdb-key'}
    s.save('steamgriddb',record)
    argv,kwargs=run.calls[0]
    assert 'fixture-private-sgdb-key' not in repr(argv)
    assert 'fixture-private-sgdb-key' in kwargs['input']
    assert s.load('steamgriddb')==record
    with pytest.raises(ValueError):s.save('itad',record)
    with pytest.raises(ValueError):s.save('steamgriddb',{**record,'extra':'not allowed'})
    s.clear('steamgriddb');assert s.load('steamgriddb') is None
