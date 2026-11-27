import asyncio
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from native_services import provider_engines
from native_services.platform import private_child_process_options

LOGIN_URL = "https://legendary.gl/epiclogin"
_SAFE_ERROR = "Epic account operation failed."
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_USER_BYTES = 256 * 1024
_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_DEFAULT_TIMEOUT = 30.0
_MAX_CODE_LEN = 4096
_MAX_FIELD_LEN = 256


class _RunnerResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def _read_stream_limited(stream: Optional[asyncio.StreamReader], limit: int) -> bytes:
    if stream is None:
        return b""
    chunks = []
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("Provider response exceeded the bounded limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    return data[:limit]


async def default_runner(argv, *, input: Optional[str] = None, env: dict, timeout: float = _DEFAULT_TIMEOUT):
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        **private_child_process_options(),
    )
    try:
        if input is not None:
            assert proc.stdin is not None
            proc.stdin.write(input.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                asyncio.gather(
                    _read_stream_limited(proc.stdout, _MAX_OUTPUT_BYTES),
                    _read_stream_limited(proc.stderr, _MAX_OUTPUT_BYTES),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return _RunnerResult(
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )
    except (asyncio.CancelledError, Exception):
        try:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    proc.kill()
                    await proc.wait()
        except Exception:
            pass
        raise


def _safe_account(account_id: Any, display_name: Any, status: str) -> dict:
    aid = account_id if _is_bounded_clean_str(account_id, _MAX_FIELD_LEN) else ""
    dn = display_name if _is_bounded_clean_str(display_name, _MAX_FIELD_LEN) else ""
    return {"account_id": aid, "display_name": dn, "status": status}


def _is_bounded_clean_str(value: Any, max_len: int) -> bool:
    if not isinstance(value, str):
        return False
    if value == "" or len(value) > max_len:
        return False
    # clean/bounded: no control chars
    for ch in value:
        o = ord(ch)
        if o < 0x20 or o == 0x7f:
            return False
    return True


def _reject_bad_config_root(config_root: Path):
    if config_root.is_symlink():
        raise ValueError("Invalid config root")
    try:
        resolved = config_root.resolve()
    except Exception:
        raise ValueError("Invalid config root")
    if resolved == Path(resolved.anchor):
        raise ValueError("Invalid config root")
    parts = [p.lower() for p in resolved.parts]
    if "heroic" in parts:
        raise ValueError("Invalid config root")
    return resolved


def _validate_and_normalize_auth_code(code: Any) -> Optional[str]:
    if not isinstance(code, str):
        return None
    if len(code) == 0 or len(code) > _MAX_CODE_LEN:
        return None
    # decode once if supported: JSON string/object carrying authorizationCode
    normalized = code
    stripped = code.strip()
    if stripped.startswith("{") or stripped.startswith('"'):
        try:
            decoded = json.loads(stripped)
        except Exception:
            decoded = None
        if isinstance(decoded, dict) and "authorizationCode" in decoded:
            val = decoded.get("authorizationCode")
            if isinstance(val, str):
                normalized = val
            else:
                return None
        elif isinstance(decoded, str):
            normalized = decoded
    if not isinstance(normalized, str):
        return None
    if normalized == "" or len(normalized) > _MAX_CODE_LEN:
        return None
    # reject control-bearing auth code
    for ch in normalized:
        o = ord(ch)
        if o < 0x20 or o == 0x7f:
            return None
    return normalized


class EpicAccountService:
    def __init__(self, binary_path, config_root, *, runner=None, now=None):
        self.binary_path = Path(binary_path)
        self.config_root = Path(config_root)
        self._runner = runner or default_runner
        self._now = now or time.time
        if not self.binary_path.is_absolute():
            raise ValueError("binary_path must be absolute")
        if not self.config_root.is_absolute():
            raise ValueError("config_root must be absolute")
        # inert: validate shape only; do not create/touch filesystem/network

    @property
    def login_url(self) -> str:
        return LOGIN_URL

    def _prepare_config_root(self) -> Path:
        root = _reject_bad_config_root(self.config_root)
        if root.exists() or root.is_symlink():
            if root.is_symlink() or not root.is_dir():
                raise ValueError("Invalid config root")
        else:
            root.mkdir(parents=True, exist_ok=False)
        os.chmod(root, 0o700)
        st = os.lstat(root)
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            raise ValueError("Invalid config root")
        return root

    def _user_json_path(self, root: Path) -> Path:
        return root / "user.json"

    def _read_user_json(self, root: Path):
        path = self._user_json_path(root)
        try:
            lst = os.lstat(path)
        except FileNotFoundError:
            return None, None
        except Exception:
            return None, None
        if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(lst.st_mode):
            return None, None
        if lst.st_size <= 0 or lst.st_size > _MAX_USER_BYTES:
            return None, None
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except Exception:
            return None, None
        try:
            with os.fdopen(fd, "rb") as f:
                data = f.read(_MAX_USER_BYTES + 1)
        except Exception:
            return None, None
        if len(data) > _MAX_USER_BYTES:
            return None, None
        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception:
            return None, None
        if not isinstance(obj, dict):
            return None, None
        return obj, lst

    def _has_required_fields(self, obj: dict) -> bool:
        if not _is_bounded_clean_str(obj.get("account_id"), _MAX_FIELD_LEN):
            return False
        if not _is_bounded_clean_str(obj.get("displayName"), _MAX_FIELD_LEN):
            return False
        if not _is_bounded_clean_str(obj.get("access_token"), 16384):
            return False
        if not _is_bounded_clean_str(obj.get("refresh_token"), 16384):
            return False
        return True

    def _secure_user_file(self, path: Path) -> bool:
        try:
            if path.is_symlink() or not path.exists() or not path.is_file():
                return False
            os.chmod(path, 0o600)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                return False
            if (stat.S_IMODE(st.st_mode) & 0o077) != 0:
                return False
            return True
        except Exception:
            return False

    def _base_env(self, root: Path) -> dict:
        env = dict(os.environ)
        env["LEGENDARY_CONFIG_PATH"] = str(root)
        env["BROWSER"] = "/bin/true"
        return env

    def read_saved_account(self) -> dict:
        try:
            root = _reject_bad_config_root(self.config_root)
            if not root.exists() or root.is_symlink() or not root.is_dir():
                return _safe_account("", "", "disconnected")
            obj, _ = self._read_user_json(root)
            if not obj or not self._has_required_fields(obj):
                return _safe_account("", "", "disconnected")
            # pure saved reader must not chmod/write
            return _safe_account(obj.get("account_id"), obj.get("displayName"), "saved")
        except Exception:
            return _safe_account("", "", "disconnected")

    async def _run(self, argv, *, input=None, env=None, timeout=_DEFAULT_TIMEOUT):
        # W22: the helper is downloaded (checksum-verified) the first time it is needed.
        await asyncio.to_thread(provider_engines.ensure, self.binary_path)
        return await self._runner(argv, input=input, env=env or {}, timeout=timeout)

    async def authenticate(self, code) -> dict:
        norm_code = _validate_and_normalize_auth_code(code)
        if norm_code is None:
            return _safe_account("", "", "error")
        try:
            root = self._prepare_config_root()
            before = self._user_json_path(root)
            before_mtime = None
            try:
                st = os.lstat(before)
                if not stat.S_ISLNK(st.st_mode):
                    before_mtime = st.st_mtime_ns
            except Exception:
                before_mtime = None
            payload = json.dumps({"authorizationCode": norm_code}, separators=(",", ":"))
            env = self._base_env(root)
            argv = [str(self.binary_path), "auth", "--disable-webview"]
            res = await self._run(argv, input=payload, env=env, timeout=_DEFAULT_TIMEOUT)
            # require zero returncode PLUS fresh user.json PLUS successful library
            if getattr(res, "returncode", 1) != 0:
                return _safe_account("", "", "error")
            obj, lst = self._read_user_json(root)
            if not obj or not lst or not self._has_required_fields(obj):
                return _safe_account("", "", "error")
            if before_mtime is not None and lst.st_mtime_ns <= before_mtime:
                return _safe_account("", "", "error")
            if not self._secure_user_file(self._user_json_path(root)):
                return _safe_account("", "", "error")
            games = await self.refresh_library(_trusted_root=root)
            if games is None:
                return _safe_account("", "", "error")
            return _safe_account(obj.get("account_id"), obj.get("displayName"), "connected")
        except Exception:
            return _safe_account("", "", "error")

    async def refresh_library(self, _trusted_root: Optional[Path] = None):
        try:
            root = _trusted_root or _reject_bad_config_root(self.config_root)
            if _trusted_root is None:
                if not root.exists() or root.is_symlink() or not root.is_dir():
                    return None
            env = self._base_env(root)
            argv = [str(self.binary_path), "list-games", "--json"]
            res = await self._run(argv, input=None, env=env, timeout=_DEFAULT_TIMEOUT)
            if getattr(res, "returncode", 1) != 0:
                return None
            stdout = getattr(res, "stdout", "")
            if not isinstance(stdout, str):
                return None
            if len(stdout.encode("utf-8", errors="ignore")) > _MAX_JSON_BYTES:
                return None
            data = json.loads(stdout) if stdout.strip() else []
            if not isinstance(data, list):
                return None
            out = []
            for item in data[:10000]:
                if not isinstance(item, dict):
                    continue
                if item.get("is_dlc") is True or item.get("dlc") is True:
                    continue
                app_name = item.get("app_name")
                app_title = item.get("app_title")
                if not _is_bounded_clean_str(app_name,128) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}",app_name) or ".." in app_name:
                    continue
                if not _is_bounded_clean_str(app_title,512):
                    continue
                out.append({"app_name": app_name, "app_title": app_title})
            return out
        except Exception:
            return None

    async def logout(self) -> dict:
        try:
            root = _reject_bad_config_root(self.config_root)
            if not root.exists() or root.is_symlink() or not root.is_dir():
                return {"status": "disconnected"}
            env = self._base_env(root)
            argv = [str(self.binary_path), "auth", "--delete"]
            res = await self._run(argv, input=None, env=env, timeout=_DEFAULT_TIMEOUT)
            if getattr(res, "returncode", 1) != 0:
                return {"status": "error"}
            user_path = self._user_json_path(root)
            # must check absence of own user.json before disconnected
            try:
                if user_path.exists() or user_path.is_symlink():
                    # if symlink or still present, do not claim disconnected success
                    if user_path.is_symlink() or os.path.lexists(user_path):
                        return {"status": "error"}
            except Exception:
                return {"status": "error"}
            return {"status": "disconnected"}
        except Exception:
            return {"status": "error"}
