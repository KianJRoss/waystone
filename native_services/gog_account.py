"""Waystone-owned GOG auth/library bridge using the bundled gogdl engine.
Tokens stay inside gogdl's private auth file or transient process memory.
"""
from __future__ import annotations
from .platform import set_private_file_mode
import asyncio, json, os, re, secrets, stat, tempfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .epic_account import default_runner, _reject_bad_config_root
from native_services import provider_engines

CLIENT_ID = "46899977096215655"
REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"
PRODUCT_ID = re.compile(r"^[1-9][0-9]{0,19}$")
MAX_BYTES = 2 * 1024 * 1024
MAX_LIBRARY = 5000
MAX_PAGES = 100

class GOGAccountError(ValueError): pass

def _bounded(value, limit=4096):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def _json_request(url, headers):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"galaxy-library.gog.com", "gamesdb.gog.com", "api.gog.com"} or parsed.username or parsed.password:
        raise GOGAccountError("GOG library request target was rejected")
    try:
        with build_opener(_NoRedirect).open(Request(url, headers=dict(headers), method="GET"), timeout=15) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname != parsed.hostname:
                raise GOGAccountError("GOG redirected the request to an untrusted host")
            body = response.read(MAX_BYTES + 1)
    except GOGAccountError: raise
    except Exception: raise GOGAccountError("GOG library request failed") from None
    if len(body) > MAX_BYTES: raise GOGAccountError("GOG returned an oversized response")
    try: return json.loads(body)
    except (ValueError, UnicodeDecodeError): raise GOGAccountError("GOG returned invalid JSON") from None

class GOGAccountService:
    def __init__(self, binary_path, config_root, runner=None, request_json=None):
        self.binary_path = Path(binary_path)
        raw_root = Path(config_root).expanduser()
        if raw_root.is_symlink(): raise GOGAccountError("Invalid Waystone GOG config directory")
        try: self.config_root = _reject_bad_config_root(raw_root)
        except ValueError: raise GOGAccountError("Invalid Waystone GOG config directory") from None
        self.runner = runner or default_runner
        self.request_json = request_json or _json_request
        self.auth_path = self.config_root / "auth.json"
        self.library_path = self.config_root / "library-cache.json"
        self._pending_state = None
        self.config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.config_root.is_symlink() or not self.config_root.is_dir(): raise GOGAccountError("Invalid Waystone GOG config directory")
        os.chmod(self.config_root, 0o700)
        self._validate_auth_path()

    def _validate_auth_path(self):
        try: info = self.auth_path.lstat()
        except FileNotFoundError: return
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > 32768:
            raise GOGAccountError("Waystone GOG credential file is unsafe")

    def _env(self):
        env = dict(os.environ); env["GOGDL_CONFIG_PATH"] = str(self.config_root); return env

    def begin_login(self):
        self._pending_state = secrets.token_urlsafe(32)
        return "https://auth.gog.com/auth?" + urlencode({"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
            "response_type": "code", "layout": "galaxy", "state": self._pending_state})

    def consume_callback(self, url):
        if not self._pending_state or not isinstance(url, str) or len(url) > 8192: return None
        try:
            p = urlsplit(url); q = parse_qs(p.query, strict_parsing=True)
            if (p.scheme != "https" or p.hostname != "embed.gog.com" or p.port not in (None, 443) or
                p.path != "/on_login_success" or p.username or p.password or q.get("origin") != ["client"] or
                q.get("state") != [self._pending_state]): return None
            codes = q.get("code", [])
            if len(codes) != 1 or not _bounded(codes[0]): return None
            self._pending_state = None; return codes[0]
        except (ValueError, TypeError, AttributeError): return None

    async def _run_auth(self, code=None):
        try:  # W22: downloaded (checksum-verified) the first time it is needed
            await asyncio.to_thread(provider_engines.ensure, self.binary_path)
        except Exception as exc:
            raise GOGAccountError(f"Could not download the GOG helper: {exc}") from None
        if not self.binary_path.is_absolute() or not self.binary_path.is_file() or not os.access(self.binary_path, os.X_OK):
            raise GOGAccountError("Waystone GOG engine is unavailable")
        argv = [str(self.binary_path), "--auth-config-path", str(self.auth_path), "auth"]
        if code is not None:
            if not _bounded(code): raise GOGAccountError("GOG returned an invalid sign-in code")
            argv += ["--code", code]
        try: result = await self.runner(argv, env=self._env(), timeout=30)
        except Exception: raise GOGAccountError("GOG account action failed") from None
        if result.returncode != 0 or len(result.stdout) > 32768: raise GOGAccountError("GOG account action failed")
        try: credentials = json.loads(result.stdout)
        except (ValueError, TypeError): raise GOGAccountError("GOG account action returned invalid credentials") from None
        if not isinstance(credentials, dict) or credentials.get("error") or not self._valid_credentials(credentials):
            raise GOGAccountError("GOG account action returned incomplete credentials")
        self._validate_auth_path(); os.chmod(self.auth_path, 0o600)
        return credentials

    @staticmethod
    def _valid_credentials(row):
        return all(_bounded(row.get(k), 8192) for k in ("access_token", "refresh_token", "user_id"))

    async def authenticate(self, code):
        await self._run_auth(code); return self.read_saved_account()

    def _read_auth(self):
        self._validate_auth_path()
        try:
            info = self.auth_path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > 32768: return None
            os.chmod(self.auth_path, 0o600)
            data = json.loads(self.auth_path.read_text(encoding="utf-8")); row = data.get(CLIENT_ID) if isinstance(data, dict) else None
            return row if isinstance(row, dict) and self._valid_credentials(row) else None
        except (OSError, ValueError, TypeError): return None

    def read_saved_account(self):
        row = self._read_auth(); user_id = row.get("user_id", "") if row else ""
        return {"provider": "gog", "account_id": user_id if _bounded(user_id, 256) else "",
                "display_name": "GOG account" if row else "", "status": "connected" if row else "disconnected"}

    def load_cached_library(self):
        try:
            info = self.library_path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > MAX_BYTES: return []
            rows = json.loads(self.library_path.read_text(encoding="utf-8"))
            if not isinstance(rows, list): return []
            return [r for r in rows[:MAX_LIBRARY] if isinstance(r, dict) and PRODUCT_ID.fullmatch(str(r.get("product_id", ""))) and _bounded(r.get("title"), 512)]
        except (OSError, ValueError, TypeError): return []

    async def refresh_library(self):
        if self.read_saved_account().get("status") != "connected": raise GOGAccountError("Connect GOG to Waystone before refreshing its library")
        credentials = await self._run_auth(); headers = {"Authorization": "Bearer " + credentials["access_token"]}
        from urllib.parse import urlencode
        products, cursor = set(), None
        for _ in range(MAX_PAGES):
            url = f"https://galaxy-library.gog.com/users/{quote(credentials['user_id'], safe='')}/releases"
            if cursor:
                if not _bounded(cursor): raise GOGAccountError("GOG returned an invalid page cursor")
                url += "?" + urlencode({"page_token": cursor})
            page = self.request_json(url, headers)
            if not isinstance(page, dict) or not isinstance(page.get("items", []), list): raise GOGAccountError("GOG returned an invalid library page")
            for item in page.get("items", []):
                if isinstance(item, dict) and item.get("platform_id") == "gog" and item.get("owned") is True:
                    product = str(item.get("external_id", ""))
                    if PRODUCT_ID.fullmatch(product): products.add(product)
            if len(products) >= MAX_LIBRARY: break
            cursor = page.get("next_page_token")
            if not cursor: break
        else: raise GOGAccountError("GOG library exceeded its pagination limit")
        games = []
        for product in sorted(products)[:MAX_LIBRARY]:
            try: data = self.request_json(f"https://gamesdb.gog.com/platforms/gog/external_releases/{product}", headers)
            except Exception: continue
            if not isinstance(data, dict) or data.get("type") not in ("game", "mod"): continue
            game = data.get("game")
            if not isinstance(game, dict) or game.get("visible_in_library") is not True: continue
            raw = game.get("title")
            title = (raw.get("en-US") or raw.get("*") or next(iter(raw.values()), "")) if isinstance(raw, dict) else raw
            if _bounded(title, 512): games.append({"product_id": product, "title": title.strip()})
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=".library-", suffix=".tmp", dir=self.config_root)
            set_private_file_mode(fd, 0o600)
            payload = json.dumps(games, ensure_ascii=False).encode("utf-8")
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp_name, self.library_path); tmp_name = None
        except OSError:
            raise GOGAccountError("Waystone could not save the GOG library cache") from None
        finally:
            if tmp_name:
                try: os.unlink(tmp_name)
                except OSError: pass
        return games

    def logout(self):
        try:
            self._validate_auth_path()
            if self.auth_path.exists(): self.auth_path.unlink()
            try: self.library_path.unlink()
            except FileNotFoundError: pass
        except OSError: raise GOGAccountError("Waystone could not remove its GOG account data") from None
        self._pending_state = None; return self.read_saved_account()

    def cancel_login(self): self._pending_state = None
