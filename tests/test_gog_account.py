import asyncio, json, os, stat, sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import pytest
from native_services.gog_account import GOGAccountService, GOGAccountError, CLIENT_ID

class Runner:
    def __init__(self): self.calls=[]; self.credentials={'access_token':'SECRET_ACCESS','refresh_token':'SECRET_REFRESH','user_id':'user-123','expires_in':3600}
    async def __call__(self, argv, *, env, timeout):
        self.calls.append((list(argv),dict(env),timeout))
        path=Path(argv[argv.index('--auth-config-path')+1])
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({CLIENT_ID:self.credentials}),encoding='utf-8')
        return type('Result',(),{'returncode':0,'stdout':json.dumps(self.credentials)})()

def test_gog_login_callback_state_and_credentials_are_private(tmp_path):
    runner=Runner();svc=GOGAccountService(Path(sys.executable),tmp_path/'waystone/gog',runner=runner,request_json=lambda *_: {})
    login=svc.begin_login();q=parse_qs(urlsplit(login).query)
    assert urlsplit(login).hostname=='auth.gog.com' and q['client_id']==[CLIENT_ID]
    state=q['state'][0]
    assert svc.consume_callback(f'https://embed.gog.com/on_login_success?origin=client&code=ok&state=wrong') is None
    assert svc.consume_callback(f'https://embed.gog.com.attacker.test/on_login_success?origin=client&code=ok&state={state}') is None
    assert svc.consume_callback(f'http://embed.gog.com/on_login_success?origin=client&code=ok&state={state}') is None
    assert svc.consume_callback(f'https://embed.gog.com/on_login_success?origin=client&code=ok&state={state}')=='ok'
    assert svc.consume_callback(f'https://embed.gog.com/on_login_success?origin=client&code=again&state={state}') is None
    account=asyncio.run(svc.authenticate('one-time-code'))
    assert account=={'provider':'gog','account_id':'user-123','display_name':'GOG account','status':'connected'}
    assert stat.S_IMODE(svc.auth_path.stat().st_mode)==0o600
    assert 'SECRET' not in repr(account)
    argv,env,_=runner.calls[-1]
    assert argv[-3:]==['auth','--code','one-time-code']
    assert env['GOGDL_CONFIG_PATH']==str(svc.config_root)

def test_refresh_library_paginates_filters_owned_and_resolves_actual_titles(tmp_path):
    runner=Runner();calls=[]
    def api(url,headers):
        calls.append((url,headers))
        if 'galaxy-library.gog.com' in url:
            if 'page_token=' not in url:return {'items':[
                {'platform_id':'gog','external_id':'42','owned':True},
                {'platform_id':'gog','external_id':'43','owned':False},
                {'platform_id':'steam','external_id':'44','owned':True}], 'next_page_token':'next'}
            return {'items':[{'platform_id':'gog','external_id':'45','owned':True}]}
        product=url.rsplit('/',1)[-1]
        return {'type':'game','game':{'visible_in_library':True,'title':{'en-US':'Title '+product}}}
    svc=GOGAccountService(Path(sys.executable),tmp_path/'waystone/gog',runner=runner,request_json=api)
    asyncio.run(svc.authenticate('code'))
    rows=asyncio.run(svc.refresh_library())
    assert rows==[{'product_id':'42','title':'Title 42'},{'product_id':'45','title':'Title 45'}]
    assert svc.load_cached_library()==rows
    assert stat.S_IMODE(svc.library_path.stat().st_mode)==0o600
    assert all(h['Authorization']=='Bearer SECRET_ACCESS' for _,h in calls)
    assert len([1 for u,_ in calls if 'gamesdb.gog.com' in u])==2
    assert all('SECRET' not in json.dumps(r) for r in rows)

def test_bad_page_and_untrusted_redirect_data_do_not_become_library(tmp_path):
    runner=Runner();svc=GOGAccountService(Path(sys.executable),tmp_path/'waystone/gog',runner=runner,request_json=lambda *_:{'items':'bad'})
    asyncio.run(svc.authenticate('code'))
    with pytest.raises(GOGAccountError):asyncio.run(svc.refresh_library())
    assert svc.load_cached_library()==[]

def test_auth_file_symlink_is_rejected(tmp_path):
    root=tmp_path/'waystone/gog';root.mkdir(parents=True)
    victim=tmp_path/'victim';victim.write_text('{}')
    (root/'auth.json').symlink_to(victim)
    with pytest.raises(GOGAccountError):GOGAccountService(Path(sys.executable),root)
