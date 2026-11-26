import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

THIS_DIR = Path(__file__).resolve().parent
OWNED = THIS_DIR.parent
if str(OWNED) not in sys.path:
    sys.path.insert(0, str(OWNED))

from native_services.epic_account import EpicAccountService, LOGIN_URL, default_runner


class R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def write_user(root: Path, data, mode=0o600, symlink=False, mtime=None):
    p = root / "user.json"
    if symlink:
        target = root / "real.json"
        target.write_text(json.dumps(data))
        os.symlink(target, p)
        return p
    p.write_text(json.dumps(data))
    os.chmod(p, mode)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def valid_user():
    return {
        "account_id": "acct123",
        "displayName": "Player",
        "access_token": "tok",
        "refresh_token": "rtok",
    }


def test_constructor_inert(tmp_path):
    cfg = tmp_path / "cfg"
    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg))
    assert not cfg.exists()
    assert svc.login_url == LOGIN_URL == "https://legendary.gl/epiclogin"


def test_rejects_relative_and_bad_roots(tmp_path):
    with pytest.raises(ValueError):
        EpicAccountService("legendary", str(tmp_path / "cfg"))
    with pytest.raises(ValueError):
        EpicAccountService(str(tmp_path / "legendary"), "cfg")


def test_stdin_only_secret_and_no_secret_in_args_env(tmp_path):
    calls = []
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    secret = "SUPER_SECRET_CODE"

    async def runner(argv, *, input=None, env=None, timeout=0):
        calls.append((list(argv), input, dict(env or {}), timeout))
        if argv[1] == "auth" and "--disable-webview" in argv:
            write_user(cfg, valid_user())
            return R(0, "", "")
        if argv[1] == "list-games":
            return R(0, json.dumps([{"app_name": "Fortnite", "app_title": "Fortnite", "is_dlc": False}]), "")
        return R(1)

    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg), runner=runner)
    out = asyncio.run(svc.authenticate(secret))
    assert out == {"account_id": "acct123", "display_name": "Player", "status": "connected"}
    auth_call = next(c for c in calls if c[0][1] == "auth" and "--disable-webview" in c[0])
    assert secret not in " ".join(auth_call[0])
    assert secret not in json.dumps(auth_call[2])
    assert json.loads(auth_call[1]) == {"authorizationCode": secret}
    assert auth_call[2]["LEGENDARY_CONFIG_PATH"] == str(cfg)
    assert auth_call[2]["BROWSER"] == "/bin/true"


def test_auth_requires_zero_returncode_even_with_fresh_proof(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()

    async def runner(argv, *, input=None, env=None, timeout=0):
        if argv[1] == "auth":
            write_user(cfg, valid_user())
            return R(1, "", "")
        if argv[1] == "list-games":
            return R(0, "[]", "")
        return R(1)

    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg), runner=runner)
    out = asyncio.run(svc.authenticate("code"))
    assert out["status"] == "error"


def test_failed_cli_exit0_without_fresh_proof(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()

    async def runner(argv, *, input=None, env=None, timeout=0):
        if argv[1] == "auth":
            return R(0, "nope", "nope")
        if argv[1] == "list-games":
            return R(0, "[]", "")
        return R(1)

    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg), runner=runner)
    out = asyncio.run(svc.authenticate("code"))
    assert out["status"] == "error"


def test_strict_required_fields_and_control_account(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()

    cases = [
        {"account_id": "acct", "displayName": "P", "access_token": "a"},  # missing refresh
        {"account_id": "acct", "displayName": "P", "refresh_token": "r"},  # missing access
        {"account_id": "acct", "displayName": "P", "access_token": "", "refresh_token": "r"},
        {"account_id": "acct", "displayName": "P", "access_token": "a", "refresh_token": 123},
        {"account_id": "acct", "displayName": "P", "expires_in": 3600, "access_token": "", "refresh_token": ""},
        {"account_id": "acct", "displayName": "P", "expires_at": "123", "session_id": "s"},
        {"account_id": "bad\nid", "displayName": "P", "access_token": "a", "refresh_token": "r"},
        {"account_id": "acct", "displayName": "P\x00", "access_token": "a", "refresh_token": "r"},
    ]
    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg))
    for data in cases:
        write_user(cfg, data)
        assert svc.read_saved_account()["status"] == "disconnected", data


def test_rejects_oversized_or_control_auth_code_and_decodes_json_once(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    calls = []

    async def runner(argv, *, input=None, env=None, timeout=0):
        calls.append(input)
        if argv[1] == "auth":
            write_user(cfg, valid_user())
            return R(0)
        if argv[1] == "list-games":
            return R(0, "[]")
        return R(1)

    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg), runner=runner)
    assert asyncio.run(svc.authenticate("a" * 5000))["status"] == "error"
    assert asyncio.run(svc.authenticate("bad\ncode"))["status"] == "error"
    assert asyncio.run(svc.authenticate("\x01bad"))["status"] == "error"
    assert calls == []

    # JSON string/object decode once if supported
    out = asyncio.run(svc.authenticate(json.dumps({"authorizationCode": "REAL"})))
    assert out["status"] == "connected"
    auth_inputs = [c for c in calls if c is not None]
    assert json.loads(auth_inputs[-1]) == {"authorizationCode": "REAL"}

    cfg2 = tmp_path / "cfg2"
    cfg2.mkdir()
    calls2 = []
    async def runner2(argv, *, input=None, env=None, timeout=0):
        calls2.append(input)
        if argv[1] == "auth":
            write_user(cfg2, valid_user())
            return R(0)
        if argv[1] == "list-games":
            return R(0, "[]")
        return R(1)
    svc2 = EpicAccountService(str(tmp_path / "legendary"), str(cfg2), runner=runner2)
    out2 = asyncio.run(svc2.authenticate(json.dumps("REAL2")))
    assert out2["status"] == "connected"
    auth_inputs2 = [c for c in calls2 if c is not None]
    assert json.loads(auth_inputs2[-1]) == {"authorizationCode": "REAL2"}


def test_read_saved_account_pure_no_chmod(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    write_user(cfg, valid_user(), mode=0o644)
    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg))
    snap = svc.read_saved_account()
    assert snap == {"account_id": "acct123", "display_name": "Player", "status": "saved"}
    # must not chmod/write
    assert (stat.S_IMODE(os.stat(cfg / "user.json").st_mode) & 0o077) != 0


def test_auth_fails_if_cannot_secure_login_file(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()

    async def runner(argv, *, input=None, env=None, timeout=0):
        if argv[1] == "auth":
            write_user(cfg, valid_user(), mode=0o644)
            return R(0)
        if argv[1] == "list-games":
            return R(0, "[]")
        return R(1)

    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg), runner=runner)
    def bad_chmod(path, mode):
        raise PermissionError("nope")
    monkeypatch.setattr(os, "chmod", bad_chmod)
    out = asyncio.run(svc.authenticate("code"))
    assert out["status"] == "error"


def test_wrong_old_symlink_userdata_and_library(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()

    # old user.json (mtime before attempt) should not count as fresh proof
    old = valid_user()
    write_user(cfg, old, mtime=time.time() - 100)

    async def runner(argv, *, input=None, env=None, timeout=0):
        if argv[1] == "auth":
            # do not update
            return R(0)
        if argv[1] == "list-games":
            return R(0, "[]")
        return R(1)

    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg), runner=runner)
    out = asyncio.run(svc.authenticate("code"))
    assert out["status"] == "error"

    # symlink user.json rejected
    cfg2 = tmp_path / "cfg2"
    cfg2.mkdir()
    async def runner2(argv, *, input=None, env=None, timeout=0):
        if argv[1] == "auth":
            write_user(cfg2, valid_user(), symlink=True)
            return R(0)
        if argv[1] == "list-games":
            return R(0, "[]")
        return R(1)
    svc2 = EpicAccountService(str(tmp_path / "legendary"), str(cfg2), runner=runner2)
    assert asyncio.run(svc2.authenticate("code"))["status"] == "error"

    # library parse safe and sanitized exact fields, exclude DLC
    cfg3 = tmp_path / "cfg3"
    cfg3.mkdir()
    async def runner3(argv, *, input=None, env=None, timeout=0):
        if argv[1] == "auth":
            write_user(cfg3, valid_user())
            return R(0)
        if argv[1] == "list-games":
            payload = [
                {"app_name": "App", "app_title": "Title", "url": "javascript:bad()", "extra": 1},
                {"app_name": "DLC", "app_title": "D", "is_dlc": True},
                {"bad": 1},
                "x",
            ]
            return R(0, json.dumps(payload))
        return R(1)
    svc3 = EpicAccountService(str(tmp_path / "legendary"), str(cfg3), runner=runner3)
    assert asyncio.run(svc3.authenticate("code"))["status"] == "connected"
    games = asyncio.run(svc3.refresh_library())
    assert games == [{"app_name": "App", "app_title": "Title"}]


def test_errors_and_saved_account_secret_safe(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    secret = "TOPSECRET"

    async def runner(argv, *, input=None, env=None, timeout=0):
        if "auth" in argv and "--disable-webview" in argv:
            write_user(cfg, valid_user())
            return R(0, "leaked?", "err")
        if "list-games" in argv:
            raise RuntimeError(f"boom {secret}")
        return R(1)

    svc = EpicAccountService(str(tmp_path / "legendary"), str(cfg), runner=runner)
    out = asyncio.run(svc.authenticate(secret))
    assert out == {"account_id": "", "display_name": "", "status": "error"}
    assert secret not in json.dumps(out)

    write_user(cfg, {"account_id": "id", "displayName": "Name", "access_token": "a", "refresh_token": "SECRET_TOKEN"})
    snap = svc.read_saved_account()
    assert snap == {"account_id": "id", "display_name": "Name", "status": "saved"}
    assert "SECRET_TOKEN" not in json.dumps(snap)


def test_logout_checks_exit_and_absence_and_touches_only_own_config(tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    write_user(foreign, valid_user())
    calls = []

    async def runner(argv, *, input=None, env=None, timeout=0):
        calls.append((list(argv), dict(env or {})))
        # simulate successful delete
        try:
            os.remove(own / "user.json")
        except FileNotFoundError:
            pass
        return R(0)

    write_user(own, valid_user())
    svc = EpicAccountService(str(tmp_path / "legendary"), str(own), runner=runner)
    out = asyncio.run(svc.logout())
    assert out == {"status": "disconnected"}
    assert calls and calls[0][0] == [str(tmp_path / "legendary"), "auth", "--delete"]
    assert calls[0][1].get("LEGENDARY_CONFIG_PATH") == str(own)
    assert json.loads((foreign / "user.json").read_text()) == valid_user()

    # nonzero exit -> error
    own2 = tmp_path / "own2"
    own2.mkdir()
    write_user(own2, valid_user())
    async def runner_fail(argv, *, input=None, env=None, timeout=0):
        return R(1)
    svc2 = EpicAccountService(str(tmp_path / "legendary"), str(own2), runner=runner_fail)
    assert asyncio.run(svc2.logout()) == {"status": "error"}
    assert (own2 / "user.json").exists()

    # exit 0 but file still present -> no false success
    own3 = tmp_path / "own3"
    own3.mkdir()
    write_user(own3, valid_user())
    async def runner_no_delete(argv, *, input=None, env=None, timeout=0):
        return R(0)
    svc3 = EpicAccountService(str(tmp_path / "legendary"), str(own3), runner=runner_no_delete)
    assert asyncio.run(svc3.logout()) == {"status": "error"}


def test_reject_symlink_or_root_or_heroic_config(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "linkcfg"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink not supported")
    svc = EpicAccountService(str(tmp_path / "legendary"), str(link))
    assert asyncio.run(svc.logout())["status"] in ("disconnected", "error")
    assert svc.read_saved_account()["status"] == "disconnected"

    heroic_like = tmp_path / "heroic" / "legendaryConfig"
    heroic_like.mkdir(parents=True)
    svc2 = EpicAccountService(str(tmp_path / "legendary"), str(heroic_like))
    assert svc2.read_saved_account()["status"] == "disconnected"


def test_cancellation_cleanup_own_synthetic_child(tmp_path):
    marker = tmp_path / "pid.txt"
    script = tmp_path / "sleeper.py"
    script.write_text(
        "import os,time,sys\n"
        "open(sys.argv[1],'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )

    async def run_case():
        task = asyncio.create_task(
            default_runner([sys.executable, str(script), str(marker)], input=None, env=dict(os.environ), timeout=30)
        )
        for _ in range(50):
            if marker.exists() and marker.read_text().strip():
                break
            await asyncio.sleep(0.05)
        assert marker.exists()
        pid = int(marker.read_text().strip())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.2)
        alive = True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        assert not alive, "synthetic child should be terminated on cancellation"

    asyncio.run(run_case())
