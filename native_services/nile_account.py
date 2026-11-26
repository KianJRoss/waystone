"""Waystone-owned, explicit Nile/Amazon device-login flow; no Heroic GUI/config use."""
from __future__ import annotations
import json, os, stat
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .epic_account import default_runner, _reject_bad_config_root
import asyncio
from native_services import provider_engines

MAX_OUTPUT=128*1024
MAX_FIELD=4096

class NileAccountError(ValueError):
    pass

def _clean(value, label):
    if not isinstance(value,str) or not 1 <= len(value) <= MAX_FIELD or any(ord(c)<32 or ord(c)==127 for c in value):
        raise NileAccountError(f"Invalid Nile {label}")
    return value

def callback_code(url):
    if not isinstance(url,str) or len(url)>8192:
        raise NileAccountError("Invalid Amazon sign-in callback")
    try:
        p=urlsplit(url)
        if p.scheme!="https" or p.hostname not in {"www.amazon.com","amazon.com"} or p.path not in {"/ap/maplanding","/ap/ext/oauth/2"}:
            raise ValueError()
        code=parse_qs(p.query,strict_parsing=True).get("openid.oa2.authorization_code",[])
        if len(code)!=1:return None
        return _clean(code[0],"authorization code")
    except (ValueError,TypeError,AttributeError):
        return None

class NileAccountService:
    def __init__(self,binary_path,config_root,runner=None):
        self.binary_path=Path(binary_path)
        self.config_root=_reject_bad_config_root(Path(config_root))
        self.runner=runner or default_runner
        self._pending=None
        self.config_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.config_root.is_symlink() or not self.config_root.is_dir():
            raise NileAccountError("Invalid Waystone Nile config directory")
        os.chmod(self.config_root,0o700)

    def _env(self):
        env=dict(os.environ)
        env["NILE_CONFIG_PATH"]=str(self.config_root)
        return env

    async def begin_login(self):
        try:  # W22: downloaded (checksum-verified) the first time it is needed
            await asyncio.to_thread(provider_engines.ensure, self.binary_path)
        except Exception as exc:
            raise NileAccountError(f"Could not download the Amazon helper: {exc}") from None
        if not self.binary_path.is_absolute() or not self.binary_path.is_file() or not os.access(self.binary_path,os.X_OK):
            raise NileAccountError("Waystone Nile engine is unavailable")
        result=await self.runner([str(self.binary_path),"auth","--login","--non-interactive"],env=self._env(),timeout=30)
        if result.returncode!=0 or len(result.stdout)>MAX_OUTPUT:
            raise NileAccountError("Amazon sign-in could not be started")
        try:
            data=json.loads(result.stdout)
            if not isinstance(data,dict) or set(data)!={"url","client_id","code_verifier","serial"}:
                raise ValueError()
            url=_clean(data["url"],"login URL")
            fields={k:_clean(data[k],k) for k in ("client_id","code_verifier","serial")}
            p=urlsplit(url)
            if p.scheme!="https" or p.hostname not in {"www.amazon.com","amazon.com"}:
                raise ValueError()
            self._pending={**fields}
            return {"url":url}
        except (ValueError,TypeError,KeyError,AttributeError):
            self._pending=None
            raise NileAccountError("Amazon returned an invalid sign-in response") from None

    async def finish_login(self,url):
        code=callback_code(url)
        if code is None or not self._pending:
            raise NileAccountError("Amazon sign-in did not return a valid authorization code")
        p=self._pending
        args=[str(self.binary_path),"register","--code",code,"--code-verifier",p["code_verifier"],
              "--serial",p["serial"],"--client-id",p["client_id"]]
        self._pending=None
        result=await self.runner(args,env=self._env(),timeout=30)
        if result.returncode!=0:
            raise NileAccountError("Amazon account could not be connected to Waystone")
        account=self.read_saved_account()
        if account.get("status")!="connected":
            raise NileAccountError("Nile registration returned without a readable account record")
        return account

    async def refresh_library(self):
        """Sync Nile's library into Waystone-owned cache, then parse its allowlisted export."""
        if self.read_saved_account().get("status") != "connected":
            raise NileAccountError("Connect an Amazon account before refreshing its library")
        result=await self.runner([str(self.binary_path),"library","sync"],env=self._env(),timeout=180)
        if result.returncode != 0:
            raise NileAccountError("Amazon library refresh failed")
        from .provider_entitlements import ProviderEntitlementsReader
        rows=ProviderEntitlementsReader().load_entitlements(
            "amazon", config_root=self.config_root.parent)
        return [{"product_id":row.product_id,"title":row.title,"is_installed":row.is_installed}
                for row in rows]

    def cancel_login(self):
        self._pending=None

    def read_saved_account(self):
        path=self.config_root/"nile"/"current_user.json"
        try:
            st=path.lstat()
            if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_size>32768:
                return {"provider":"amazon","account_id":"","display_name":"","status":"disconnected"}
            data=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data,dict) or set(data)-{"user_id","name"}:
                raise ValueError()
            aid=data.get("user_id");name=data.get("name")
            if not isinstance(aid,str) or not aid or len(aid)>256 or any(ord(c)<32 or ord(c)==127 for c in aid):raise ValueError()
            if not isinstance(name,str) or len(name)>256 or any(ord(c)<32 or ord(c)==127 for c in name):name=""
            return {"provider":"amazon","account_id":aid,"display_name":name,"status":"connected"}
        except (OSError,ValueError,TypeError,json.JSONDecodeError):
            return {"provider":"amazon","account_id":"","display_name":"","status":"disconnected"}

    async def logout(self):
        if not self.binary_path.is_file():raise NileAccountError("Waystone Nile engine is unavailable")
        result=await self.runner([str(self.binary_path),"auth","--logout"],env=self._env(),timeout=30)
        if result.returncode!=0:raise NileAccountError("Amazon sign-out failed")
        self._pending=None
        return self.read_saved_account()
