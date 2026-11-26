"""Robots-aware public product page parser and narrowly scoped fetch primitive.

No source is enabled by default. Callers must review a retailer's terms and add its
exact host to an explicit allowlist before any page can be fetched.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import ipaddress
import json
import http.client
import ssl
import re
import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request
from urllib.robotparser import RobotFileParser

_MAX_HTML = 2 * 1024 * 1024
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class _JsonLdScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._chunks = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        kind = attr.get("type", "").split(";", 1)[0].strip().lower()
        if kind == "application/ld+json":
            self._capture = True
            self._chunks = []

    def handle_data(self, data):
        if self._capture:
            self._chunks.append(data)

    def handle_endtag(self, tag):
        if self._capture and tag.lower() == "script":
            self.documents.append("".join(self._chunks))
            self._capture = False
            self._chunks = []


def _objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, (dict, list)):
            yield from _objects(graph)
        yield value


def _types(node):
    value = node.get("@type")
    if isinstance(value, str):
        return {value.rsplit("/", 1)[-1].rsplit("#", 1)[-1]}
    if isinstance(value, list):
        return {str(item).rsplit("/", 1)[-1].rsplit("#", 1)[-1] for item in value}
    return set()


def _text(value):
    return value.strip()[:300] if isinstance(value, str) and value.strip() else None


def _public_http_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def _offers(value):
    if isinstance(value, list):
        for item in value:
            yield from _offers(item)
    elif isinstance(value, dict):
        if "AggregateOffer" in _types(value):
            return
        yield value


def parse_public_offer_snapshot(html: str, *, page_url: str, source_id: str, observed_at: str):
    """Extract literal public Product/Offer JSON-LD facts from caller-supplied HTML.

    This parser does not fetch URLs. It does not infer seller authorization, game
    edition equivalence, platform/region compatibility, or collection permission.
    """
    url = _public_http_url(page_url)
    if url is None:
        raise ValueError("A public HTTP(S) product-page URL is required")
    if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", source_id):
        raise ValueError("Invalid configured source ID")
    if not isinstance(observed_at, str) or len(observed_at) > 64 or not observed_at.endswith("Z"):
        raise ValueError("observed_at must be an explicit UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(observed_at[:-1] + "+00:00")
    except ValueError:
        raise ValueError("observed_at must be a valid UTC timestamp") from None
    if not isinstance(html, str) or len(html.encode("utf-8")) > _MAX_HTML:
        raise ValueError("Snapshot exceeds the safe size limit")

    parser = _JsonLdScripts()
    parser.feed(html)
    output = []
    for document in parser.documents:
        try:
            decoded = json.loads(document)
        except (ValueError, TypeError):
            continue
        for node in _objects(decoded):
            if "Product" not in _types(node):
                continue
            title = _text(node.get("name"))
            if not title:
                continue
            for offer in _offers(node.get("offers")):
                if not isinstance(offer, dict) or "Offer" not in _types(offer):
                    continue
                price = offer.get("price")
                currency = offer.get("priceCurrency")
                try:
                    amount = Decimal(str(price))
                except (InvalidOperation, ValueError):
                    continue
                if not amount.is_finite() or amount < 0 or not _text(currency) or not _CURRENCY.fullmatch(currency.strip()):
                    continue
                availability = _text(offer.get("availability"))
                seller = offer.get("seller")
                seller = seller.get("name") if isinstance(seller, dict) else seller
                output.append({
                    "source_id": source_id,
                    "title": title,
                    "seller": _text(seller),
                    "price": format(amount, "f"),
                    "currency": currency.strip(),
                    "availability": availability,
                    "product_page_url": url,
                    "observed_at": observed_at,
                    "evidence": "schema.org/Product/Offer JSON-LD",
                    "offer_class": "unknown",
                    "verification": "unverified",
                })
    return output


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to an already-validated IP while retaining TLS SNI."""
    def __init__(self, host, address, *, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self):
        raw = socket.create_connection((self._pinned_address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class PublicPageScraper:
    """Fetch one public product page from an explicitly reviewed HTTPS host.

    No cookies/authentication are sent; cross-host redirects are never followed.
    A source must be explicitly allowlisted after a terms and robots review.
    """
    def __init__(self, approved_hosts, *, opener=None, resolver=socket.getaddrinfo,
                 timeout=8.0, max_bytes=_MAX_HTML, min_interval=60.0,
                 clock=time.monotonic, user_agent="WaystonePriceProbe/0.1"):
        self._hosts = {str(h).lower().rstrip(".") for h in approved_hosts}
        # An injected opener exists only for deterministic offline tests. The
        # production transport connects to the validated address directly to
        # avoid a second DNS lookup (DNS-rebinding/SSRF).
        self._opener = opener
        self._resolver = resolver
        self._timeout = float(timeout)
        self._max_bytes = int(max_bytes)
        self._min_interval = float(min_interval)
        self._clock = clock
        self._ua = user_agent
        self._lock = threading.Lock()
        self._robots = {}
        self._last_fetch = {}
        if not 0.1 <= self._timeout <= 30 or not 1024 <= self._max_bytes <= _MAX_HTML:
            raise ValueError("Invalid public-page fetch bounds")
        if not 0 <= self._min_interval <= 86400:
            raise ValueError("Invalid rate limit")

    def _validate(self, url):
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError("Invalid product URL")
        p = urlsplit(url)
        host = (p.hostname or "").lower().rstrip(".")
        try:
            port = p.port
        except ValueError:
            raise ValueError("Invalid product URL") from None
        if p.scheme != "https" or not host or p.username or p.password or port not in (None, 443):
            raise ValueError("Only public HTTPS product pages are supported")
        if host not in self._hosts:
            raise ValueError("This retailer host has not been reviewed and enabled")
        if p.fragment:
            p = p._replace(fragment="")
        return urlunsplit(p), host

    def _public_dns(self, host):
        try:
            records = self._resolver(host, 443, type=socket.SOCK_STREAM)
            addresses = {ipaddress.ip_address(item[4][0]) for item in records}
        except Exception:
            raise ValueError("Retailer host could not be safely resolved") from None
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("Retailer host did not resolve exclusively to public addresses")
        return sorted(addresses, key=lambda address: (address.version, int(address)))[0].compressed

    def _read(self, url, *, limit):
        parsed = urlsplit(url)
        host = parsed.hostname
        address = self._public_dns(host)
        if self._opener is not None:
            req = Request(url, headers={
                "User-Agent": self._ua,
                "Accept": "text/html,application/xhtml+xml,text/plain",
            })
            try:
                with self._opener(req, timeout=self._timeout) as response:
                    if response.geturl() != url:
                        raise ValueError("Retailer redirected the request; refusing to follow it")
                    content_type = response.headers.get_content_type()
                    if content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
                        raise ValueError("Retailer did not return a public HTML page")
                    data = response.read(limit + 1)
            except ValueError:
                raise
            except Exception:
                raise ValueError("Public product page could not be fetched safely") from None
        else:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn = _PinnedHTTPSConnection(host, address, timeout=self._timeout)
            try:
                conn.request("GET", path, headers={
                    "User-Agent": self._ua,
                    "Accept": "text/html,application/xhtml+xml,text/plain",
                    "Connection": "close",
                })
                response = conn.getresponse()
                if 300 <= response.status < 400:
                    raise ValueError("Retailer redirected the request; refusing to follow it")
                if response.status != 200:
                    raise ValueError("Retailer did not return a public HTML page")
                content_type = response.headers.get_content_type()
                if content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
                    raise ValueError("Retailer did not return a public HTML page")
                data = response.read(limit + 1)
            except ValueError:
                raise
            except Exception:
                raise ValueError("Public product page could not be fetched safely") from None
            finally:
                conn.close()
        if len(data) > limit:
            raise ValueError("Public product page exceeded the safe size limit")
        return data.decode("utf-8", errors="replace")

    def _robots_for(self, host):
        now = self._clock()
        cached = self._robots.get(host)
        if cached and now - cached[0] < 3600:
            return cached[1]
        robots_url = "https://" + host + "/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        # Fail closed if robots policy cannot be read safely.
        text = self._read(robots_url, limit=128 * 1024)
        parser.parse(text.splitlines())
        self._robots[host] = (now, parser)
        return parser

    def fetch_html(self, url):
        clean_url, host = self._validate(url)
        self._public_dns(host)
        with self._lock:
            now = self._clock()
            previous = self._last_fetch.get(host)
            robots = self._robots_for(host)
            delay = robots.crawl_delay(self._ua) or 0
            required_interval = max(self._min_interval, float(delay))
            if previous is not None and now - previous < required_interval:
                raise ValueError("Retailer rate limit: wait before checking this store again")
            if not robots.can_fetch(self._ua, clean_url):
                raise ValueError("Retailer robots.txt disallows this page")
            html = self._read(clean_url, limit=self._max_bytes)
            self._last_fetch[host] = self._clock()
        return html

    def scrape(self, url, *, source_id, observed_at):
        html = self.fetch_html(url)
        return parse_public_offer_snapshot(
            html, page_url=url, source_id=source_id, observed_at=observed_at
        )
