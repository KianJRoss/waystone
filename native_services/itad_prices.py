"""Small personal-use ITAD API client. Secrets are injected, never persisted/logged here."""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

_BASE = "https://api.isthereanydeal.com"
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_MAX_RESPONSE = 2 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the ITAD API-key header to a redirect target.
        return None


_SAFE_OPEN = build_opener(_NoRedirect()).open


class ITADError(RuntimeError):
    """Safe-to-display provider error; never contains request headers or credentials."""


class ITADClient:
    def __init__(self, api_key: str, *, opener=None, timeout: float = 8.0):
        if (not isinstance(api_key, str) or not 8 <= len(api_key) <= 4096
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in api_key)):
            raise ValueError("Invalid ITAD API key")
        if not 0.1 <= float(timeout) <= 30:
            raise ValueError("Invalid timeout")
        self._key = api_key
        self._opener = opener or _SAFE_OPEN
        self._timeout = float(timeout)

    def _request(self, path: str, *, query=None, body=None):
        if path not in ("/games/search/v1", "/games/prices/v3", "/deals/v2"):
            raise ValueError("Unsupported ITAD endpoint")
        url = _BASE + path + (("?" + urlencode(query)) if query else "")
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = Request(url, data=payload, headers={
            "ITAD-API-Key": self._key,
            "Accept": "application/json",
            "User-Agent": "Waystone/0.1 (personal library)",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        }, method="POST" if payload is not None else "GET")
        try:
            with self._opener(req, timeout=self._timeout) as response:
                raw = response.read(_MAX_RESPONSE + 1)
            if len(raw) > _MAX_RESPONSE:
                raise ITADError("ITAD response exceeded the safe size limit.")
            return json.loads(raw.decode("utf-8"))
        except ITADError:
            raise
        except Exception as exc:
            # Do not chain urllib errors: they can include the full URL or request details.
            status = getattr(exc, "code", None)
            if status == 429:
                raise ITADError("ITAD rate limit reached; retry later.") from None
            raise ITADError("ITAD price lookup failed; check the connection and API key.") from None

    def search(self, title: str, *, results: int = 10):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise ValueError("Enter a game title to search")
        if type(results) is not int or not 1 <= results <= 30:
            raise ValueError("Invalid result limit")
        data = self._request("/games/search/v1", query={"title": title.strip(), "results": results})
        if not isinstance(data, list) or len(data) > results:
            raise ITADError("ITAD returned an unexpected search response.")
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("title"), str):
                raise ITADError("ITAD returned an unexpected search result.")
        return data

    def prices(self, game_ids, *, country="US", capacity=30, vouchers=None):
        if not isinstance(country, str) or not _COUNTRY.fullmatch(country):
            raise ValueError("Country must be a two-letter ISO code")
        if vouchers is not None and type(vouchers) is not bool:
            raise ValueError("Voucher inclusion must be a boolean")
        if (not isinstance(game_ids, (list, tuple)) or not 1 <= len(game_ids) <= 200
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", value) for value in game_ids)):
            raise ValueError("Supply 1 to 200 ITAD game IDs")
        if type(capacity) is not int or not 0 <= capacity <= 100:
            raise ValueError("Invalid offer count")
        query={"country":country,"capacity":capacity}
        if vouchers is not None:query["vouchers"]=str(vouchers).lower()
        data = self._request("/games/prices/v3", query=query, body=list(game_ids))
        if not isinstance(data, list) or len(data) > len(game_ids):
            raise ITADError("ITAD returned an unexpected price response.")
        return data

    def deals(self, *, country="US", offset=0, limit=120, sort="-trending", nondeals=True):
        if not isinstance(country, str) or not _COUNTRY.fullmatch(country):
            raise ValueError("Country must be a two-letter ISO code")
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid offset")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Invalid limit")
        query = {
            "country": country,
            "offset": offset,
            "limit": limit,
            "sort": sort,
            "nondeals": "true" if nondeals else "false",
        }
        data = self._request("/deals/v2", query=query)
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise ITADError("ITAD returned an unexpected catalog feed response.")
        return data
