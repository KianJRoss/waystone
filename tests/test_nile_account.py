import json, asyncio
from pathlib import Path
import pytest
from native_services.nile_account import NileAccountService, callback_code, NileAccountError
from native_services.epic_account import _RunnerResult

class Runner:
    def __init__(self,root):self.calls=[];self.root=root
    async def __call__(self,argv,*,env,timeout):
        self.calls.append((argv,env,timeout))
        if argv[1:]==["auth","--login","--non-interactive"]:
            return _RunnerResult(0,json.dumps({"url":"https://www.amazon.com/ap/signin?opaque=1","client_id":"cid","code_verifier":"verifier","serial":"serial"}),"")
        if argv[1]=="register":
            d=self.root/"nile";d.mkdir(parents=True,exist_ok=True)
            (d/"current_user.json").write_text(json.dumps({"user_id":"account","name":"Kian"}))
            return _RunnerResult(0,"SECRET SHOULD NOT SURFACE","")
        if argv[1:]==["library","sync"]:
            d=self.root/"nile";d.mkdir(parents=True,exist_ok=True)
            (d/"library.json").write_text(json.dumps({"library":[
                {"id":"amazon-game-123","title":"Fixture Amazon Game"}]}))
            return _RunnerResult(0,"Library updated","")
        if argv[1:]==["auth","--logout"]:
            (self.root/"nile/current_user.json").unlink(missing_ok=True)
            return _RunnerResult(0,"","")
        raise AssertionError(argv)

def test_nile_waystone_owned_signin_and_logout(tmp_path):
    binary=tmp_path/"nile";binary.write_text("#!/bin/sh\n");binary.chmod(0o700)
    config=tmp_path/"waystone/nile"
    runner=Runner(config);service=NileAccountService(binary,config,runner)
    data=asyncio.run(service.begin_login())
    assert data=={"url":"https://www.amazon.com/ap/signin?opaque=1"}
    assert service.read_saved_account()["status"]=="disconnected"
    callback="https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=the-code"
    account=asyncio.run(service.finish_login(callback))
    assert account=={"provider":"amazon","account_id":"account","display_name":"Kian","status":"connected"}
    argv,env,_=runner.calls[1]
    assert argv==[str(binary),"register","--code","the-code","--code-verifier","verifier","--serial","serial","--client-id","cid"]
    assert env["NILE_CONFIG_PATH"]==str(config)
    assert "SECRET SHOULD NOT SURFACE" not in repr(account)
    assert asyncio.run(service.logout())["status"]=="disconnected"

def test_nile_callback_and_login_fields_fail_closed(tmp_path):
    assert callback_code("https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=x")=="x"
    for url in ("http://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=x",
                "https://evil.example/ap/maplanding?openid.oa2.authorization_code=x",
                "https://www.amazon.com.evil.example/ap/maplanding?openid.oa2.authorization_code=x",
                "https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=x&openid.oa2.authorization_code=y"):
        assert callback_code(url) is None
    binary=tmp_path/"nile";binary.write_text("#!/bin/sh\n");binary.chmod(0o700)
    class Bad(Runner):
        async def __call__(self,argv,*,env,timeout):
            return _RunnerResult(0,json.dumps({"url":"https://evil.example","client_id":"x","code_verifier":"y","serial":"z"}),"")
    service=NileAccountService(binary,tmp_path/"waystone/nile",Bad(tmp_path))
    with pytest.raises(NileAccountError):asyncio.run(service.begin_login())
    assert service._pending is None

def test_nile_account_reader_rejects_symlink(tmp_path):
    root=tmp_path/"waystone/nile";(root/"nile").mkdir(parents=True)
    target=tmp_path/"secret";target.write_text(json.dumps({"user_id":"x","name":"y"}))
    (root/"nile/current_user.json").symlink_to(target)
    binary=tmp_path/"nile-bin";binary.write_text("#!/bin/sh\n");binary.chmod(0o700)
    service=NileAccountService(binary,root,Runner(root))
    assert service.read_saved_account()["status"]=="disconnected"


def test_nile_library_refresh_syncs_and_reads_only_waystone_owned_cache(tmp_path):
    binary=tmp_path/"nile-bin";binary.write_text("#!/bin/sh\n");binary.chmod(0o700)
    config=tmp_path/"providers-v1/amazon"
    runner=Runner(config);service=NileAccountService(binary,config,runner)
    user=config/"nile";user.mkdir(parents=True)
    (user/"current_user.json").write_text(json.dumps({"user_id":"acct","name":"Kian"}))
    rows=asyncio.run(service.refresh_library())
    assert rows==[{"product_id":"amazon-game-123","title":"Fixture Amazon Game","is_installed":None}]
    argv,env,timeout=runner.calls[-1]
    assert argv==[str(binary),"library","sync"]
    assert env["NILE_CONFIG_PATH"]==str(config) and timeout==180
